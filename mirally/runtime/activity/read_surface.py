"""The single read-only query surface for Activity diagnostics.

The monitor and the read-only browser serve the same recorded data: the monitor
also hosts a collector process, while the browser only reads what was already
written.  Every read-only rule lives here — raw paging, content redaction,
``ContextQuery`` parsing, the projected-knowledge and memory queries, and the
collector status derivation — so a paging, redaction, limit or status rule can
only be expressed once for both diagnostics faces.

Writing, control and authorization stay outside this module; it never mutates a
store and never starts collection.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Mapping, Optional

from mirally.core.activity.knowledge import ActivityOrganizer, ContextQuery, KnowledgeStore
from mirally.core.activity.store import CursorExpiredError, ObservationStore

_PAGE_MAX_OBSERVATIONS = 200
_ITEMS_MAX = 500
_EPISODES_MAX = 100
# A pause marker only qualifies a status that was actually recorded for this
# collector; "nothing was recorded" must never be displayed as "paused".  A status
# that already ended the collector is exempt too: a paused collector can be resumed,
# a stopped or crashed one cannot.
_PAUSE_EXEMPT_STATUSES = frozenset({"stopped", "disabled", "unknown", "error"})
# Recorded facts that end one collector generation.  A stop written into the
# collector row is the end of collection, not another status to average with.
_TERMINAL_LIFECYCLE_STATUSES = frozenset({"stopped", "error"})


def _query_one(params: Mapping[str, list[str]], name: str) -> Optional[str]:
    values = params.get(name) or []
    return values[0] if values else None


def parse_flag(params: Mapping[str, list[str]], name: str) -> bool:
    return (_query_one(params, name) or "").lower() in {"1", "true", "yes"}


def _not_flag(params: Mapping[str, list[str]], name: str) -> bool:
    """An opt-out flag: the value has to ask out explicitly to disable it."""
    return (_query_one(params, name) or "").lower() not in {"0", "false", "no"}


def _integer(params: Mapping[str, list[str]], name: str) -> Optional[int]:
    value = _query_one(params, name)
    return None if value in (None, "") else int(value)


def _paging_int(params: Mapping[str, list[str]], name: str, default: int) -> int:
    """A supplied-but-empty paging value is a client error, not a silent default."""
    values = params.get(name)
    return default if values is None else int(values[0])


def _bounded(value: int, maximum: int) -> int:
    return max(1, min(maximum, value))


def _redact_content(mapping: Any) -> dict[str, Any]:
    clean = dict(mapping or {})
    for key in ("name", "value", "text", "inserted_text", "automation_id", "class_name"):
        if key in clean:
            text = str(clean.pop(key) or "")
            clean[key + "_char_count"] = len(text)
    return clean


def safe_observation(value: Any, *, include_content: bool = False) -> dict[str, Any]:
    """Return one observation, with content only for an explicit reveal request."""
    result = value.to_dict()
    if include_content:
        return result
    ocr = dict(result.get("ocr_summary") or {})
    if "text" in ocr:
        text = str(ocr.pop("text") or "")
        ocr["text_available"] = bool(text)
        ocr["text_char_count"] = len(text)
    lines = ocr.get("lines")
    if isinstance(lines, list):
        ocr["line_count"] = len(lines)
        ocr.pop("lines", None)
    result["ocr_summary"] = ocr
    visual = dict(result.get("visual_summary") or {})
    visual.pop("path", None)
    result["visual_summary"] = visual
    uia = dict(result.get("uia_summary") or {})
    uia["nodes"] = [_redact_content(node) for node in uia.get("nodes", ())]
    uia["focused_control"] = _redact_content(uia.get("focused_control"))
    result["uia_summary"] = uia
    if result.get("kind") in {"input_text", "clipboard_content"}:
        result["payload"] = _redact_content(result.get("payload"))
    return result


def parse_context_query(params: Mapping[str, list[str]], *, mode: Optional[str] = None,
                        purpose: Optional[str] = None,
                        include_uia: Optional[bool] = None) -> ContextQuery:
    """Parse request parameters into one ``ContextQuery`` for both faces."""
    selected_mode = mode or _query_one(params, "mode") or "outline"
    selected_purpose = purpose or _query_one(params, "purpose") or "agent_context"
    uia = include_uia if include_uia is not None else parse_flag(params, "include_uia")
    return ContextQuery(
        query=_query_one(params, "q"), start_utc_ms=_integer(params, "start"), end_utc_ms=_integer(params, "end"),
        apps=tuple(params.get("app", ())), windows=tuple(params.get("window", ())),
        kinds=tuple(params.get("kind", ())), mode=selected_mode, purpose=selected_purpose,
        max_items=int(_query_one(params, "limit") or 30),
        max_episodes=int(_query_one(params, "episodes") or 10),
        token_budget=int(_query_one(params, "tokens") or 4000),
        episode_id=_query_one(params, "episode"),
        include_assets=parse_flag(params, "include_assets"),
        include_uia=uia, include_ocr=_not_flag(params, "include_ocr"),
        after_item_id=_query_one(params, "after_item"),
    )


def page_observations(store: ObservationStore, params: Mapping[str, list[str]]) -> dict[str, Any]:
    """Return the raw source newest-first with a stable ``before`` cursor."""
    limit = _bounded(_paging_int(params, "limit", 100), _PAGE_MAX_OBSERVATIONS)
    rows = store.query(
        start_utc_ms=_integer(params, "start"),
        end_utc_ms=_integer(params, "end"),
        app_ids=[value for value in params.get("app", ()) if value],
        kinds=[value for value in params.get("kind", ()) if value],
        query=_query_one(params, "q") or None,
        before_ingest_seq=_integer(params, "before"),
        descending=True,
        # Fetch one extra row so a page that happens to contain exactly ``limit``
        # records is not reported as having a next page.
        limit=limit + 1,
    )
    has_more = len(rows) > limit
    selected = rows[:limit]
    return {
        "data_status": "ok" if selected else "empty",
        "observations": [safe_observation(row, include_content=parse_flag(params, "include_content")) for row in selected],
        "cursor": selected[-1].ingest_seq if selected and has_more else None,
        "has_more": has_more,
    }


def page_events(store: ObservationStore, params: Mapping[str, list[str]]) -> dict[str, Any]:
    """Return raw events after a cursor, filtered or from the retained stream."""
    after = max(0, _paging_int(params, "after", 0))
    limit = _bounded(_paging_int(params, "limit", 100), _PAGE_MAX_OBSERVATIONS)
    include_content = parse_flag(params, "include_content")
    kinds = [value for value in params.get("kind", []) if value]
    apps = [value for value in params.get("app", []) if value]
    try:
        if kinds or apps:
            rows = store.query(after_ingest_seq=after, kinds=kinds or None, app_ids=apps or None, limit=limit + 1)
            has_more = len(rows) > limit
            selected = rows[:limit]
            cursor = selected[-1].ingest_seq if selected and selected[-1].ingest_seq is not None else after
        else:
            selected, cursor, has_more = store.read_since(after, limit=limit)
    except CursorExpiredError as error:
        return {"events": [], "cursor": after, "has_more": False, "cursor_expired": True, "error": str(error)}
    return {
        "events": [safe_observation(row, include_content=include_content) for row in selected],
        "cursor": cursor,
        "has_more": has_more,
    }


def _recorded_ms(record: Mapping[str, Any], *keys: str) -> Optional[int]:
    """The first usable recorded timestamp on a state row, in UTC milliseconds."""
    for key in keys:
        value = record.get(key)
        if isinstance(value, bool) or value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


def _terminal_record_ends_the_collection(collector: Mapping[str, Any], diagnostics: Mapping[str, Any]) -> bool:
    """Whether a recorded stop or crash outranks the last diagnostics snapshot.

    ``ActivitySensor.stop()`` writes the diagnostics snapshot first and the terminal
    lifecycle row after it, so an interrupted or failed write can leave an older
    ``running``/``paused`` snapshot beside a collector that already recorded
    ``stopped``.  Each row names the collection generation that wrote it, so rows of
    two generations are ordered by their recorded times: the later fact describes the
    current collection.  Within one generation the terminal row ends it, because the
    monitor keeps writing diagnostics from control and sampling code after a collector
    thread has died, and that must not read as a collector that still runs.
    """
    if collector.get("status") not in _TERMINAL_LIFECYCLE_STATUSES:
        return False
    if not diagnostics.get("status"):
        return False  # Nothing to outrank; the plain recorded order already answers.
    lifecycle_epoch = collector.get("source_epoch")
    diagnostics_epoch = diagnostics.get("source_epoch")
    if not lifecycle_epoch or not diagnostics_epoch or lifecycle_epoch == diagnostics_epoch:
        return True
    lifecycle_ms = _recorded_ms(collector, "at", "started_at_utc_ms")
    diagnostics_ms = _recorded_ms(diagnostics, "updated_at_utc_ms", "stopped_at_utc_ms")
    if lifecycle_ms is None or diagnostics_ms is None:
        return False  # Two generations without a recorded time stay unordered.
    return lifecycle_ms >= diagnostics_ms


def derive_collector_status(*, collector: Mapping[str, Any], diagnostics: Mapping[str, Any],
                            pause: Mapping[str, Any]) -> tuple[str, str]:
    """Derive the one collector status both faces show, plus its recorded basis.

    A recorded stop or crash ends its own collection generation, so it is shown even
    when the last diagnostics snapshot is older or from an earlier generation.  Every
    other status follows the fixed order: the last recorded collector diagnostics,
    then the last recorded collector status, then ``unknown``.  A pause marker only
    overrides a status that was actually recorded, so the absence of records stays
    ``unknown`` instead of being shown as paused.  The returned basis tells a page
    which recorded fact produced the status; a live owner replaces it with its own.
    """
    diagnostics_status = diagnostics.get("status")
    collector_status = collector.get("status")
    if collector_status and (not diagnostics_status
                             or _terminal_record_ends_the_collection(collector, diagnostics)):
        derived, derived_basis = str(collector_status), "collector"
    elif diagnostics_status:
        derived, derived_basis = str(diagnostics_status), "diagnostics"
    elif collector_status:
        derived, derived_basis = str(collector_status), "collector"
    else:
        derived, derived_basis = "unknown", "none"
    if pause.get("paused") and derived not in _PAUSE_EXEMPT_STATUSES:
        return "paused", "pause"
    return derived, derived_basis


def collector_view(*, collector: Mapping[str, Any], diagnostics: Mapping[str, Any],
                   pause: Mapping[str, Any], read_only: bool = False) -> dict[str, Any]:
    """Build the ``collector`` payload shared by both faces from recorded state."""
    status, basis = derive_collector_status(collector=collector, diagnostics=diagnostics, pause=pause)
    view: dict[str, Any] = {**collector, "status": status, "status_basis": basis}
    if read_only:
        # The page has to tell "this is when the shown record was written" apart from
        # a live update, so the timestamp belongs to the record that decided.  A pause
        # or an absent record carries no status time of its own, so the snapshot row
        # keeps the recorded diagnostics time.
        deciding = collector if basis == "collector" else diagnostics
        view["snapshot"] = True
        view["updated_at_utc_ms"] = (
            _recorded_ms(deciding, "updated_at_utc_ms", "stopped_at_utc_ms", "at", "started_at_utc_ms")
            or _recorded_ms(diagnostics, "updated_at_utc_ms", "stopped_at_utc_ms")
            or _recorded_ms(collector, "at", "started_at_utc_ms")
        )
    return view


class ActivityReadSurface:
    """Read-only queries over one observation store and its knowledge projection."""

    def __init__(self, store: ObservationStore, knowledge_store: Optional[KnowledgeStore] = None,
                 organizer: Optional[ActivityOrganizer] = None) -> None:
        self.store = store
        self.knowledge_store = knowledge_store
        self.organizer = organizer

    # Raw source

    def observations(self, params: Mapping[str, list[str]]) -> dict[str, Any]:
        return page_observations(self.store, params)

    def events(self, params: Mapping[str, list[str]]) -> dict[str, Any]:
        return page_events(self.store, params)

    def observation(self, observation_id: str, *, include_content: bool = False) -> Optional[dict[str, Any]]:
        value = self.store.get(str(observation_id))
        return None if value is None else safe_observation(value, include_content=include_content)

    def asset_record(self, asset_id: str) -> Mapping[str, Any]:
        return self.store.resolve_asset(str(asset_id), scope="activity-content")

    def asset(self, asset_id: str) -> dict[str, Any]:
        return {key: value for key, value in self.asset_record(asset_id).items() if key != "path"}

    # Projected knowledge

    def _require_organizer(self) -> ActivityOrganizer:
        if self.organizer is None:
            raise RuntimeError("活动知识索引尚未准备；原始记录和诊断仍可查看。打开此页不会创建或重建索引。")
        return self.organizer

    def _require_knowledge(self) -> KnowledgeStore:
        if self.knowledge_store is None:
            raise RuntimeError("活动知识索引尚未准备；原始记录和诊断仍可查看。打开此页不会创建或重建索引。")
        return self.knowledge_store

    def health(self) -> dict[str, Any]:
        return self._require_organizer().health()

    def context(self, params: Mapping[str, list[str]]) -> dict[str, Any]:
        return self._require_organizer().context(parse_context_query(params)).to_dict()

    def events_view(self, params: Mapping[str, list[str]]) -> dict[str, Any]:
        return self._require_organizer().activity_events(
            parse_context_query(params, mode="evidence", purpose="diagnostics", include_uia=True))

    def timeline(self, params: Mapping[str, list[str]]) -> dict[str, Any]:
        return self._require_organizer().activity_timeline(
            parse_context_query(params, mode="structured", purpose="diagnostics", include_uia=True))

    def summary(self, params: Mapping[str, list[str]]) -> dict[str, Any]:
        return self._require_organizer().activity_summary(
            parse_context_query(params, mode="outline", purpose="diagnostics"))

    def items(self, params: Mapping[str, list[str]]) -> dict[str, Any]:
        query = parse_context_query(params, mode="structured", purpose="diagnostics", include_uia=True)
        limit = max(1, min(_ITEMS_MAX, query.max_items))
        values = self._require_knowledge().query_items(
            query=query.query, start_utc_ms=query.start_utc_ms, end_utc_ms=query.end_utc_ms,
            apps=query.apps, windows=query.windows, kinds=query.kinds, episode_id=query.episode_id,
            after_item_id=query.after_item_id, limit=min(_ITEMS_MAX, limit + 1),
        )
        truncated = len(values) > limit
        selected = values[:limit]
        return {
            "data_status": "ok" if selected else "empty",
            "items": [value.to_dict() for value in selected],
            "truncated": truncated,
            "next_cursor": selected[-1].item_id if truncated and selected else None,
        }

    def item(self, item_id: str) -> Optional[dict[str, Any]]:
        value = self._require_knowledge().get_item(str(item_id))
        return None if value is None else value.to_dict()

    def episodes(self, params: Mapping[str, list[str]]) -> dict[str, Any]:
        limit = _bounded(int(_query_one(params, "limit") or 50), _EPISODES_MAX)
        values = self._require_knowledge().query_episodes(
            start_utc_ms=_integer(params, "start"), end_utc_ms=_integer(params, "end"),
            query=_query_one(params, "q") or None,
            apps=tuple(params.get("app", ())), windows=tuple(params.get("window", ())),
            kinds=tuple(params.get("kind", ())),
            after_episode_id=_query_one(params, "after_episode") or None,
            limit=limit + 1,
        )
        truncated = len(values) > limit
        selected = values[:limit]
        return {
            "data_status": "ok" if selected else "empty", "episodes": [value.to_dict() for value in selected],
            "truncated": truncated, "next_cursor": selected[-1].episode_id if truncated else None,
        }

    def episode(self, episode_id: str) -> Optional[dict[str, Any]]:
        value = self._require_organizer().get_episode(str(episode_id))
        return None if value is None else value.to_dict()

    def evidence(self, evidence_ids: list[str], *, include_assets: bool = False) -> dict[str, Any]:
        values = self._require_organizer().get_evidence(evidence_ids, include_assets=include_assets)
        return {"evidence": [value.to_dict() for value in values]}

    def evidence_query(self, params: Mapping[str, list[str]], *, include_assets: bool = False) -> dict[str, Any]:
        """Page complete Evidence rows through the matching projected items.

        An evidence row carries captured text, so the list is one projection read
        rather than a table dump: it goes through the same context path as the agent
        context route, which selects the content channels the caller asked for (UIA,
        OCR, assets), reports what the projection could not establish, and stops when
        the output budget is spent.  Both diagnostics faces therefore show one
        evidence list, with one channel, validity and budget rule.
        """
        query = parse_context_query(params, mode="evidence", purpose="diagnostics")
        if include_assets:
            query = replace(query, include_assets=True)
        bundle = self._require_organizer().context(query).to_dict()
        evidence = list(bundle.get("evidence") or ())
        return {
            "data_status": "ok" if evidence else "empty", "evidence": evidence,
            "truncated": bool(bundle.get("truncated")), "next_cursor": bundle.get("next_cursor"),
            "gaps": list(bundle.get("gaps") or ()),
        }

    def candidates(self, params: Mapping[str, list[str]]) -> dict[str, Any]:
        values = self._require_knowledge().list_memory_candidates(
            status=_query_one(params, "status"), limit=_bounded(int(_query_one(params, "limit") or 100), _ITEMS_MAX))
        return {"candidates": [value.to_dict() for value in values]}

    def facts(self, params: Mapping[str, list[str]]) -> dict[str, Any]:
        values = self._require_knowledge().list_memory_facts(
            status=_query_one(params, "status"), limit=_bounded(int(_query_one(params, "limit") or 100), _ITEMS_MAX))
        return {"facts": [value.to_dict() for value in values]}


__all__ = [
    "ActivityReadSurface",
    "collector_view",
    "parse_flag",
    "derive_collector_status",
    "page_events",
    "page_observations",
    "parse_context_query",
    "safe_observation",
]
