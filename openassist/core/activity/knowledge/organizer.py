"""Deterministic Observation -> activity knowledge projection."""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, replace
from typing import Any, Iterable, Mapping, Optional

from ..models import Observation, ObservationQuality
from ..store import CursorExpiredError, ObservationStore
from .models import ActivityEpisode, ActivityItem, ContextBundle, ContextQuery, Evidence, MemoryCandidate, MemoryFact
from .repository import KnowledgeStore, deterministic_id
from .semantic import ParserRegistry
from .projection import build_projection
from .events import build_event_fragments
from .coverage import collection_coverage


PROJECTION_VERSION = "activity.v2"


@dataclass(frozen=True)
class OrganizerStats:
    processed: int = 0
    projected: int = 0
    skipped: int = 0
    errors: int = 0
    last_cursor: int = 0
    lag: int = 0
    error_messages: tuple[str, ...] = ()


class ActivityOrganizer:
    """Incrementally materialize activity knowledge from raw observations."""

    def __init__(
        self,
        source: ObservationStore,
        knowledge: KnowledgeStore,
        *,
        consumer_id: str = "activity-knowledge",
        idle_timeout_ms: int = 5 * 60 * 1000,
        allowed_lateness_ms: int = 60 * 1000,
        parser_registry: Optional[ParserRegistry] = None,
    ) -> None:
        self.source = source
        self.knowledge = knowledge
        self.consumer_id = consumer_id
        self.idle_timeout_ms = max(1_000, int(idle_timeout_ms))
        self.allowed_lateness_ms = max(0, int(allowed_lateness_ms))
        self.parser_registry = parser_registry or ParserRegistry()
        self._last_stats = OrganizerStats()

    def project(self, observation: Observation) -> tuple[ActivityItem, Evidence]:
        return build_projection(observation, PROJECTION_VERSION)

    def _episode_for(self, item: ActivityItem, *, related_episode: Optional[ActivityEpisode] = None, detached: bool = False) -> ActivityEpisode:
        existing = related_episode or (None if detached else self.knowledge.find_open_episode(
            app_id=item.app_id, window_id=item.window_instance_id,
            start_utc_ms=item.start_utc_ms,
            # Keep the late-arrival grace window attached to the deterministic
            # episode rule.  A result arriving just after the idle boundary
            # can revise the prior episode; later data starts a new one.
            idle_timeout_ms=self.idle_timeout_ms + self.allowed_lateness_ms,
        ))
        if existing is not None and related_episode is None:
            # A closed visit is not a new visit to the same window. Late
            # evidence is allowed only within its previously observed range.
            if existing.finalized and existing.end_reason not in {None, "idle"} and item.start_utc_ms >= existing.end_utc_ms:
                existing = None
            elif existing.item_ids:
                prior = self.knowledge.get_item(existing.item_ids[-1])
                old_epoch = (prior.structured.get("source") or {}).get("source_epoch") if prior else None
                new_epoch = (item.structured.get("source") or {}).get("source_epoch")
                if old_epoch and new_epoch and old_epoch != new_epoch:
                    self.knowledge.upsert_episode(replace(existing, finalized=True, end_reason="collector_restart", revision=existing.revision + 1))
                    existing = None
        if existing is None or item.kind == "system" and item.quality == "gap":
            episode_id = deterministic_id("episode", item.item_id, PROJECTION_VERSION)
            return ActivityEpisode(
                episode_id=episode_id, start_utc_ms=item.start_utc_ms, end_utc_ms=item.end_utc_ms,
                title=item.title or item.url or item.kind, primary_app=item.app_id,
                primary_window_id=item.window_instance_id, item_ids=(item.item_id,),
                confidence=item.confidence, data_gaps=tuple(item.missing_reasons),
                projection_version=PROJECTION_VERSION,
                summary=self._episode_summary(item.app_id, 1, item.missing_reasons),
            )
        previous_item = self.knowledge.latest_item_in_episode(existing.episode_id)
        previous_url = previous_item.url if previous_item is not None else None
        if related_episode is None and item.url and previous_url and item.url != previous_url and item.start_utc_ms >= existing.end_utc_ms:
            self.knowledge.upsert_episode(replace(existing, finalized=True, end_reason="navigation", revision=existing.revision + 1))
            return ActivityEpisode(
                episode_id=deterministic_id("episode", item.item_id, PROJECTION_VERSION),
                start_utc_ms=item.start_utc_ms, end_utc_ms=item.end_utc_ms,
                title=item.title or item.url, primary_app=item.app_id,
                primary_window_id=item.window_instance_id, item_ids=(item.item_id,),
                confidence=item.confidence, data_gaps=item.missing_reasons,
                summary=self._episode_summary(item.app_id, 1, item.missing_reasons),
                projection_version=PROJECTION_VERSION,
            )
        item_ids = tuple(dict.fromkeys(existing.item_ids + (item.item_id,)))
        title = existing.title or item.title or item.url or item.kind
        gaps = tuple(dict.fromkeys(existing.data_gaps + tuple(item.missing_reasons)))
        confidence = min(existing.confidence, item.confidence)
        return ActivityEpisode(
            episode_id=existing.episode_id,
            start_utc_ms=min(existing.start_utc_ms, item.start_utc_ms),
            end_utc_ms=max(existing.end_utc_ms, item.end_utc_ms), title=title,
            primary_app=existing.primary_app or item.app_id,
            primary_window_id=existing.primary_window_id or item.window_instance_id,
            item_ids=item_ids, summary=self._episode_summary(item.app_id, len(item_ids), gaps), confidence=confidence,
            finalized=existing.finalized, end_reason=existing.end_reason,
            revision=existing.revision + 1, data_gaps=gaps,
            projection_version=PROJECTION_VERSION,
        )

    @staticmethod
    def _episode_summary(app: Optional[str], count: int, gaps: Iterable[str]) -> str:
        suffix = "；存在采集缺口" if tuple(gaps) else ""
        return f"在 {app or '未知应用'} 中记录了 {count} 条活动观察{suffix}。此摘要由观察计数生成，不推断用户意图。"

    def _inherit_context(self, item: ActivityItem, evidence: Evidence, context: Optional[ActivityItem]) -> tuple[ActivityItem, Evidence]:
        if context is None:
            return item, evidence
        blocked = set((item.structured.get("source") or {}).get("resource_unknown_fields") or ())
        inherited = [field for field in ("title", "url", "document_ref") if field not in blocked and not getattr(item, field) and getattr(context, field)]
        if not inherited:
            return item, evidence
        values = {field: getattr(context, field) if field in inherited else getattr(item, field)
                  for field in ("title", "url", "document_ref")}
        title, url, document = values["title"], values["url"], values["document_ref"]
        structured = dict(item.structured)
        field_sources = dict(structured.get("context_field_sources") or {})
        for field in inherited:
            field_sources[field] = list((context.structured.get("context_field_sources") or {}).get(field,
                                        context.structured.get("context_observation_ids", context.source_observation_ids)))
        context_ids = tuple(dict.fromkeys(value for references in field_sources.values() for value in references))
        structured["context_observation_ids"] = list(context_ids)
        structured["context_field_sources"] = field_sources
        activity = dict(structured.get("activity") or {})
        target = dict(activity.get("object") or {})
        target.update(title=title, url=url, document_ref=document)
        activity["object"] = target
        activity["context_observation_ids"] = list(context_ids)
        activity["unknowns"] = list(dict.fromkeys(list(activity.get("unknowns") or ()) +
                                    [f"{field}_unknown" for field in context.structured.get("context_unknown_fields", ())
                                     if not values.get(field)]))
        structured["activity"] = activity
        return replace(item, title=title, url=url, document_ref=document, structured=structured), replace(evidence, structured=structured)

    def _episode_description(self, episode: ActivityEpisode, current: ActivityItem) -> str:
        members = [self.knowledge.get_item(value) for value in episode.item_ids if value != current.item_id]
        members.append(current)
        labels: list[str] = []
        for value in members:
            if value is None:
                continue
            activity = value.structured.get("activity") or {}
            label = str(activity.get("summary") or "")
            if label and label not in labels:
                labels.append(label)
        scope = (current.structured.get("source") or {}).get("scope")
        subject = "显示器画面" if scope in {"monitor", "monitors"} or current.actor == "BACKGROUND" else episode.primary_app or "未知应用"
        facts = "；".join(labels[:4]) or "记录了活动观察"
        gap = "；有采集或内容缺口" if episode.data_gaps else ""
        return f"{subject}：{facts}；共 {len(episode.item_ids)} 条证据{gap}。操作结果与任务目的仅在有对应证据时确认。"[:600]

    def _process_one(self, observation: Observation) -> bool:
        with self.knowledge.transaction():
            if self.knowledge.get_item_by_observation_id(observation.observation_id) is not None:
                self.knowledge.clear_projection_error(observation.observation_id, consumer_id=self.consumer_id)
                return False
            item, evidence = self.project(observation)
            related_episode = None
            detached = False
            if observation.kind == "ocr_result":
                originals = [self.knowledge.get_item_by_observation_id(value) for value in observation.caused_by]
                original = next((value for value in originals if value is not None and
                                 (value.structured.get("source") or {}).get("kind") == "content_snapshot"), None)
                if original is not None:
                    related_episode = self.knowledge.get_episode(original.episode_id) if original.episode_id else None
                    # OCR describes the original frame even if the result
                    # carries context sampled after another page became active.
                    structured = dict(item.structured)
                    field_sources = {
                        field: list((original.structured.get("context_field_sources") or {}).get(
                            field, original.source_observation_ids))
                        for field in ("title", "url", "document_ref") if getattr(original, field)
                    }
                    context_ids = list(dict.fromkeys(value for references in field_sources.values() for value in references))
                    structured["context_field_sources"] = field_sources
                    structured["context_observation_ids"] = context_ids
                    for key in ("title", "url"):
                        value = getattr(original, key)
                        if value:
                            structured[key] = value
                        else:
                            structured.pop(key, None)
                    source = dict(item.structured.get("source") or {})
                    source["linked_observation_ids"] = list(original.source_observation_ids)
                    source["linkage_status"] = "linked"
                    source["content_observed_at_utc_ms"] = original.start_utc_ms
                    source["result_observed_at_utc_ms"] = item.start_utc_ms
                    original_source = original.structured.get("source") or {}
                    for key in ("scope", "monitor_id", "monitor_bounds", "bounds", "capture_bounds"):
                        if key in original_source:
                            source[key] = original_source[key]
                        else:
                            source.pop(key, None)
                    activity = dict(item.structured.get("activity") or {})
                    target = dict(activity.get("object") or {})
                    target.update(app_id=original.app_id, process_instance_id=original.process_instance_id,
                                  window_instance_id=original.window_instance_id,
                                  title=original.title, url=original.url, document_ref=original.document_ref,
                                  type="monitor" if source.get("scope") in {"monitor", "monitors"} else "window")
                    if source.get("monitor_id"):
                        target["monitor_id"] = source["monitor_id"]
                    else:
                        target.pop("monitor_id", None)
                    if source.get("scope"):
                        target["scope"] = source["scope"]
                    else:
                        target.pop("scope", None)
                    if source.get("monitor_bounds") is not None:
                        target["monitor_bounds"] = source["monitor_bounds"]
                    else:
                        target.pop("monitor_bounds", None)
                    activity["object"] = target
                    activity["context_observation_ids"] = context_ids
                    structured.update(source=source, activity=activity)
                    item = replace(item, app_id=original.app_id, process_instance_id=original.process_instance_id,
                                   window_instance_id=original.window_instance_id, start_utc_ms=original.start_utc_ms,
                                   end_utc_ms=original.end_utc_ms, title=original.title, url=original.url,
                                   document_ref=original.document_ref, structured=structured)
                    evidence = replace(evidence, structured=structured)
                else:
                    # Unknown causal scope is kept separate rather than being
                    # assigned to the page that happens to be open on arrival.
                    detached = True
                    source = {**(item.structured.get("source") or {}), "linkage_status": "unresolved"}
                    gaps = tuple(dict.fromkeys(item.missing_reasons + ("causal_evidence_missing",)))
                    activity = dict(item.structured.get("activity") or {})
                    activity["unknowns"] = list(dict.fromkeys(list(activity.get("unknowns") or ()) + ["causal_origin_unknown"]))
                    structured = {**item.structured, "source": source, "activity": activity, "missing_reasons": list(gaps)}
                    item = replace(item, structured=structured, missing_reasons=gaps,
                                   quality="partial" if item.quality == "complete" else item.quality,
                                   confidence=min(item.confidence, 0.45))
                    evidence = replace(evidence, structured=structured,
                                       quality="partial" if evidence.quality == "complete" else evidence.quality,
                                       confidence=min(evidence.confidence, 0.45))
            elif item.window_instance_id and item.actor != "BACKGROUND" and (item.structured.get("source") or {}).get("scope") not in {"monitor", "monitors"}:
                context = self.knowledge.latest_window_context(
                    window_id=item.window_instance_id, at_utc_ms=item.start_utc_ms, app_id=item.app_id,
                    session_instance_id=observation.session_instance_id,
                    source_epoch=observation.source_epoch,
                )
                item, evidence = self._inherit_context(item, evidence, context)
            self.knowledge.finalize_episodes(item.start_utc_ms - self.idle_timeout_ms - self.allowed_lateness_ms)
            if observation.kind == "collection_gap":
                reasons = tuple(dict.fromkeys(item.missing_reasons + ("collection_gap",)))
                self.knowledge.finalize_for_gap(item.start_utc_ms, reasons, session_instance_id=observation.session_instance_id)
                item = replace(item, missing_reasons=reasons)
                detached = True
            elif observation.kind in {"foreground_changed", "window_context"} and item.window_instance_id:
                self.knowledge.finalize_episodes(item.start_utc_ms, reason="window_changed",
                                                except_window=item.window_instance_id, foreground_only=True)
            episode = self._episode_for(item, related_episode=related_episode, detached=detached)
            if observation.kind == "collection_gap":
                episode = replace(episode, finalized=True, end_reason="collection_gap")
            episode = replace(episode, summary=self._episode_description(episode, item))
            item = replace(item, episode_id=episode.episode_id)
            self.knowledge.upsert_item(item, evidence)
            self.knowledge.upsert_episode(episode)
            self.knowledge.attach_item_episode(item.item_id, episode.episode_id)
            annotation = self.parser_registry.parse(item)
            if annotation is not None:
                self.knowledge.upsert_annotation(item.item_id, annotation)
            self.knowledge.clear_projection_error(observation.observation_id, consumer_id=self.consumer_id)
            return True

    def _check_projection_version(self) -> None:
        versions = self.knowledge.projection_versions()
        if any(value != PROJECTION_VERSION for value in versions):
            raise ValueError("activity projection version changed; run knowledge-process --rebuild before processing or querying")

    def process_once(self, *, limit: int = 200) -> OrganizerStats:
        self._check_projection_version()
        generation, cursor = self.knowledge.checkpoint(self.consumer_id)
        source_generation = self.source.generation()
        if generation and generation != source_generation:
            raise CursorExpiredError("activity source generation changed; rebuild knowledge projection")
        rows, next_cursor, has_more = self.source.read_since(cursor, limit=max(1, min(500, int(limit))))
        processed = projected = skipped = errors = 0
        messages: list[str] = []
        retry_rows = []
        for failure in self.knowledge.list_projection_errors(consumer_id=self.consumer_id, limit=10, retryable_only=True):
            row = self.source.get(failure["observation_id"])
            if row is None:
                # Retention can expire a failed source. Keep the failure visible
                # while ensuring it eventually leaves the retry queue.
                self.knowledge.record_projection_error(failure["observation_id"], failure["ingest_seq"],
                                                       LookupError("source_observation_unavailable"), consumer_id=self.consumer_id)
            else:
                retry_rows.append(row)
        pending_ids = {row.observation_id for row in rows}
        retry_rows = [row for row in retry_rows if row is not None and row.observation_id not in pending_ids]
        for row in retry_rows + rows:
            processed += 1
            try:
                if self._process_one(row):
                    projected += 1
                else:
                    skipped += 1
            except Exception as error:
                errors += 1
                messages.append(f"{row.observation_id}: {type(error).__name__}: {error}")
                self.knowledge.record_projection_error(row.observation_id, row.ingest_seq, error, consumer_id=self.consumer_id)
        # A bad row is acknowledged after being reported so one malformed
        # payload cannot stall the entire local activity stream.
        if rows:
            self.knowledge.set_checkpoint(self.consumer_id, source_generation, next_cursor)
        if not has_more:
            self.knowledge.finalize_episodes(int(time.time() * 1000) - self.idle_timeout_ms - self.allowed_lateness_ms)
        newest = self.source.health().newest_ingest_seq or next_cursor
        stats = OrganizerStats(processed, projected, skipped, errors, next_cursor, max(0, int(newest or 0) - next_cursor), tuple(messages[-10:]))
        self._last_stats = stats
        return stats

    def catch_up(self, *, batch_size: int = 200, max_batches: int = 100) -> OrganizerStats:
        latest = self._last_stats
        totals = OrganizerStats()
        for _ in range(max(1, int(max_batches))):
            current = self.process_once(limit=batch_size)
            totals = OrganizerStats(
                processed=totals.processed + current.processed,
                projected=totals.projected + current.projected,
                skipped=totals.skipped + current.skipped,
                errors=totals.errors + current.errors,
                last_cursor=current.last_cursor,
                lag=current.lag,
                error_messages=totals.error_messages + current.error_messages,
            )
            latest = current
            if current.processed == 0 or current.lag == 0:
                break
        self._last_stats = totals if totals.processed else latest
        return self._last_stats

    def context(self, query: ContextQuery) -> ContextBundle:
        query.validate()
        self._check_projection_version()
        # Fetch one extra row so reaching max_items is visible to the caller.
        episodes, items, evidence = self.knowledge.context(replace(query, max_items=min(500, query.max_items + 1)))
        budget = max(128, query.token_budget) * 3
        selected_items: list[ActivityItem] = []
        selected_episodes: list[ActivityEpisode] = []
        selected_evidence: list[Evidence] = []
        truncated = len(items) > query.max_items
        bundle = ContextBundle(
            query_id=f"ctx_{uuid.uuid4().hex}", generated_at_utc_ms=int(time.time() * 1000),
            index_watermark=self.knowledge.health(self.consumer_id).processed_ingest_seq,
        )
        projection_gaps = ("projection_incomplete",) if self.knowledge.health(self.consumer_id).projection_error_count else ()
        for item in items[:query.max_items]:
            if query.mode == "outline":
                visible = replace(item, text_summary=item.text_summary[:600], structured={}, asset_refs=())
            elif query.mode == "structured":
                structured = dict(item.structured)
                if not query.include_uia:
                    structured.pop("uia_nodes", None)
                    structured.pop("uia_text", None)
                    structured.pop("focused_control", None)
                    structured.pop("uia_metadata", None)
                if not query.include_ocr:
                    structured.pop("ocr_text", None)
                    structured.pop("ocr_lines", None)
                    structured.pop("ocr_metadata", None)
                visible = replace(item, structured=structured, asset_refs=item.asset_refs if query.include_assets else ())
            else:
                visible = replace(item, asset_refs=item.asset_refs if query.include_assets else ())
            proposed = selected_items + [visible]
            selected_ids = {value.item_id for value in proposed}
            # Return only the member ids present in this bounded context.
            projected_episodes = [replace(
                episode, item_ids=tuple(value for value in episode.item_ids if value in selected_ids),
                summary=(episode.summary or "")[:600] or None,
            ) for episode in episodes if selected_ids.intersection(episode.item_ids)]
            observation_ids = {oid for value in proposed for oid in value.source_observation_ids}
            projected_evidence = [replace(value, asset_refs=value.asset_refs if query.include_assets else ())
                                  for value in evidence if observation_ids.intersection(value.source_observation_ids)] if query.mode == "evidence" else []
            gaps = tuple(dict.fromkeys(projection_gaps + tuple(gap for value in proposed for gap in value.missing_reasons)))
            candidate = replace(bundle, items=tuple(proposed), episodes=tuple(projected_episodes),
                                evidence=tuple(projected_evidence), gaps=gaps, truncated=True,
                                next_cursor=visible.item_id)
            encoded = json.dumps(candidate.to_dict(), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            if len(encoded) > budget:
                truncated = True
                break
            selected_items = proposed
            selected_episodes = projected_episodes
            selected_evidence = projected_evidence
        gaps = tuple(dict.fromkeys(projection_gaps + tuple(gap for item in selected_items for gap in item.missing_reasons)))
        next_cursor = selected_items[-1].item_id if truncated and selected_items else None
        return replace(bundle,
            episodes=tuple(selected_episodes), items=tuple(selected_items), evidence=tuple(selected_evidence),
            gaps=gaps, truncated=truncated, next_cursor=next_cursor,
        )

    def propose_memory(self, statement: str, *, fact_key: Optional[str] = None, source_episode_ids: Iterable[str] = (), source_observation_ids: Iterable[str] = (), confidence: float = 0.5, extraction_method: str = "manual") -> MemoryCandidate:
        if not str(statement).strip():
            raise ValueError("memory statement cannot be empty")
        episode_refs = tuple(dict.fromkeys(str(value) for value in source_episode_ids))
        observation_refs = tuple(dict.fromkeys(str(value) for value in source_observation_ids))
        candidate = MemoryCandidate(
            candidate_id=deterministic_id("memory", fact_key or statement, episode_refs, observation_refs),
            statement=str(statement).strip()[:4000], fact_key=fact_key,
            source_episode_ids=episode_refs,
            source_observation_ids=observation_refs,
            confidence=max(0.0, min(1.0, float(confidence))), extraction_method=extraction_method,
        )
        self.knowledge.upsert_memory_candidate(candidate)
        return candidate

    def accept_memory(self, candidate_id: str, *, supersedes: Iterable[str] = (), conflicts_with: Iterable[str] = ()) -> Optional[MemoryFact]:
        """Promote a candidate without deleting its original review record."""

        candidate = self.knowledge.get_memory_candidate(candidate_id)
        if candidate is None:
            return None
        fact = MemoryFact(
            fact_id=deterministic_id("fact", candidate.candidate_id),
            statement=candidate.statement,
            fact_key=candidate.fact_key,
            subject=candidate.subject,
            predicate=candidate.predicate,
            object_value=candidate.object_value,
            valid_from_utc_ms=candidate.valid_from_utc_ms,
            valid_to_utc_ms=candidate.valid_to_utc_ms,
            source_episode_ids=candidate.source_episode_ids,
            source_observation_ids=candidate.source_observation_ids,
            confidence=candidate.confidence,
            extraction_method=candidate.extraction_method,
            extraction_version=candidate.extraction_version,
            supersedes=tuple(dict.fromkeys(str(value) for value in supersedes)),
            conflicts_with=tuple(dict.fromkeys(str(value) for value in conflicts_with)),
        )
        self.knowledge.upsert_memory_fact(fact)
        self.knowledge.set_memory_status(candidate_id, "accepted", conflicts_with=fact.conflicts_with)
        for old_fact_id in fact.supersedes:
            self.knowledge.set_memory_fact_status(old_fact_id, "superseded")
        return fact

    def update_memory(self, candidate_id: str, status: str, *, conflicts_with: Iterable[str] = ()) -> bool:
        return self.knowledge.set_memory_status(candidate_id, status, conflicts_with=tuple(conflicts_with))

    def update_fact(self, fact_id: str, status: str) -> bool:
        return self.knowledge.set_memory_fact_status(fact_id, status)

    def recent_activity(self, *, seconds: int = 300, mode: str = "outline", token_budget: int = 4000) -> ContextBundle:
        now = int(time.time() * 1000)
        return self.context(ContextQuery(
            start_utc_ms=now - max(1, int(seconds)) * 1000,
            end_utc_ms=now + 1,
            mode=mode,
            token_budget=token_budget,
        ))

    def search_activity(self, query: str, *, mode: str = "outline", token_budget: int = 4000, max_items: int = 30) -> ContextBundle:
        return self.context(ContextQuery(query=str(query), mode=mode, token_budget=token_budget, max_items=max_items))

    def activity_around(self, at_utc_ms: int, *, window_seconds: int = 60, mode: str = "outline", token_budget: int = 4000) -> ContextBundle:
        center = int(at_utc_ms)
        radius = max(1, int(window_seconds)) * 1000
        return self.context(ContextQuery(
            start_utc_ms=center - radius,
            end_utc_ms=center + radius,
            mode=mode,
            token_budget=token_budget,
        ))

    def get_episode(self, episode_id: str) -> Optional[ActivityEpisode]:
        self._check_projection_version()
        return self.knowledge.get_episode(str(episode_id))

    def get_evidence(self, evidence_ids: Iterable[str], *, include_assets: bool = False) -> tuple[Evidence, ...]:
        self._check_projection_version()
        values = self.knowledge.get_evidence(tuple(evidence_ids))
        if include_assets:
            return tuple(values)
        return tuple(replace(value, asset_refs=()) for value in values)

    def activity_events(self, query: ContextQuery) -> dict[str, Any]:
        """Return related actions, window states and text as activity fragments.

        Item cursors bound the view; an episode id stays stable across pages,
        and partial_event distinguishes a fragment from a complete episode.
        """
        query.validate()
        self._check_projection_version()
        items = self.knowledge.query_items(
            query=query.query, start_utc_ms=query.start_utc_ms, end_utc_ms=query.end_utc_ms,
            apps=query.apps, windows=query.windows, kinds=query.kinds, episode_id=query.episode_id,
            after_item_id=query.after_item_id, limit=min(500, query.max_items + 1),
        )
        health = self.health()
        result = {
            "schema_version": "activity.event.v1", "projection_version": PROJECTION_VERSION,
            "data_status": "projection_incomplete" if health["knowledge"].get("projection_error_count", 0)
                           else "ok" if items else "index_lagging" if health["organizer_lag"] else "empty",
            "index_watermark": health["organizer_processed_seq"],
            "source_newest_ingest_seq": health["source_newest_ingest_seq"],
            "projection_error_count": health["knowledge"].get("projection_error_count", 0),
            "semantics": "observed_window_activity; intent_and_action_outcomes_unconfirmed",
            "order": "chronological_within_page; item_cursor_pages_newest_first",
            "content_channels": {"uia": query.include_uia, "ocr": query.include_ocr, "assets": query.include_assets},
            "collection_coverage": collection_coverage(self.source.get_state("collector_diagnostics"), query),
            "events": [], "background_evidence": [], "unattributed_evidence": [], "collection_status": [],
            "truncated": len(items) > query.max_items, "next_cursor": None,
        }
        episodes = {episode_id: self.knowledge.get_episode(episode_id)
                    for episode_id in {item.episode_id for item in items if item.episode_id}}
        episodes = {key: value for key, value in episodes.items() if value is not None}
        selected = []
        text_cache = {}
        budget = query.token_budget * 3
        for item in items[:query.max_items]:
            fragments = build_event_fragments(selected + [item], episodes,
                                             include_uia=query.include_uia, include_ocr=query.include_ocr,
                                             include_assets=query.include_assets, text_cache=text_cache)
            candidate = {**result, **fragments, "truncated": True, "next_cursor": item.item_id}
            if len(fragments["events"]) > query.max_episodes or len(json.dumps(
                    candidate, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > budget:
                result["truncated"] = True
                break
            selected.append(item)
            result.update(fragments)
        # The repository caps a fetch at 500. Probe that boundary separately
        # so max_items=500 does not silently lose a continuation.
        if len(selected) == 500 and len(items) == 500:
            result["truncated"] = bool(self.knowledge.query_items(
                query=query.query, start_utc_ms=query.start_utc_ms, end_utc_ms=query.end_utc_ms,
                apps=query.apps, windows=query.windows, kinds=query.kinds, episode_id=query.episode_id,
                after_item_id=selected[-1].item_id, limit=1,
            ))
        result["next_cursor"] = selected[-1].item_id if selected and result["truncated"] else None
        if items and not selected:
            result["data_status"] = "budget_exceeded"
        return result

    def activity_timeline(self, query: ContextQuery) -> dict[str, Any]:
        """Return compact observed facts without requiring a model to narrate them."""
        query.validate()
        self._check_projection_version()
        _, items, _ = self.knowledge.context(replace(query, mode="structured", max_items=min(500, query.max_items + 1)))
        rows: list[dict[str, Any]] = []
        truncated = len(items) > query.max_items
        health = self.health()
        result: dict[str, Any] = {
            "data_status": "projection_incomplete" if health["knowledge"].get("projection_error_count", 0) else
                           "ok" if items else "index_lagging" if health["organizer_lag"] else "empty",
            "projection_version": PROJECTION_VERSION, "timeline": rows,
            "truncated": truncated, "next_cursor": None,
            "source_newest_ingest_seq": health["source_newest_ingest_seq"],
            "organizer_processed_seq": health["organizer_processed_seq"],
            "projection_error_count": health["knowledge"].get("projection_error_count", 0),
            "semantics": "observed_facts; intent_and_unobserved_results_are_unknown",
        }
        budget = max(128, query.token_budget) * 3
        for item in items[:query.max_items]:
            activity = dict(item.structured.get("activity") or {})
            change = dict(activity.get("change") or {})
            for key, value in tuple(change.items()):
                if isinstance(value, str) and len(value) > 500:
                    change[key] = value[:500]
                    change["content_truncated"] = True
            row = {
                "item_id": item.item_id, "episode_id": item.episode_id,
                "start_utc_ms": item.start_utc_ms, "end_utc_ms": item.end_utc_ms,
                "app_id": item.app_id, "window_instance_id": item.window_instance_id,
                "title": (item.title or "")[:512] or None, "url": (item.url or "")[:1024] or None,
                "actor": item.actor, "actor_basis": item.actor_basis,
                "action": activity.get("action", item.kind), "summary": str(activity.get("summary") or item.kind)[:600],
                "change": change, "result": activity.get("result", "unconfirmed"),
                "unknowns": list(activity.get("unknowns") or ()),
                "source_observation_ids": list(item.source_observation_ids),
                "context_observation_ids": list(item.structured.get("context_observation_ids") or ()),
                "evidence_ids": list(activity.get("evidence_ids") or ()),
                "quality": item.quality, "missing_reasons": list(item.missing_reasons),
                "projection": dict(item.structured.get("projection") or {}),
                "capture_scope": dict((item.structured.get("source") or {}).get("capture_scope") or {}),
            }
            target = dict(activity.get("object") or {})
            target_truncated = []
            for key, value in tuple(target.items()):
                if isinstance(value, str) and len(value) > 1024:
                    target[key] = value[:1024]
                    target_truncated.append(key)
            row["object"] = target
            row["object_truncated_fields"] = target_truncated
            source_kind = (item.structured.get("source") or {}).get("kind")
            if not ((source_kind == "context_snapshot" and not query.include_uia) or
                    (source_kind in {"content_snapshot", "ocr_result"} and not query.include_ocr)):
                row["content_excerpt"] = item.text_summary[:500]
                row["content_excerpt_truncated"] = len(item.text_summary) > 500
            candidate = {**result, "timeline": rows + [row], "truncated": True, "next_cursor": item.item_id}
            if len(json.dumps(candidate, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > budget:
                truncated = True
                break
            rows.append(row)
        result["truncated"] = truncated
        result["next_cursor"] = rows[-1]["item_id"] if rows and truncated else None
        if items and not rows:
            result["data_status"] = "budget_exceeded"
        return result

    def activity_summary(self, query: ContextQuery) -> dict[str, Any]:
        """Return a bounded timeline summary with explicit data status."""
        query.validate()
        context = self.context(replace(query, mode="outline", max_items=min(query.max_items, 200)))
        episodes, items = context.episodes, context.items
        apps: dict[str, dict[str, Any]] = {}
        for episode in episodes:
            app = episode.primary_app or "unknown"
            start = max(episode.start_utc_ms, query.start_utc_ms if query.start_utc_ms is not None else episode.start_utc_ms)
            end = min(episode.end_utc_ms, query.end_utc_ms if query.end_utc_ms is not None else episode.end_utc_ms)
            duration = max(0, end - start)
            bucket = apps.setdefault(app, {"app_id": app, "active_ms": 0, "episode_count": 0, "item_count": 0})
            bucket["active_ms"] += duration
            bucket["episode_count"] += 1
            bucket["item_count"] += len(episode.item_ids)
        snippets = []
        for item in items[:10]:
            text = item.text_summary or item.title or item.url or ""
            if text:
                snippets.append({"item_id": item.item_id, "at_utc_ms": item.start_utc_ms, "app_id": item.app_id, "text": text[:500]})
        health = self.health()
        if health["knowledge"].get("projection_error_count", 0):
            data_status = "projection_incomplete"
        elif not items and health["organizer_lag"] > 0:
            data_status = "index_lagging"
        elif not items:
            data_status = "empty"
        else:
            data_status = "ok"
        return {
            "data_status": data_status,
            "query_status": "matched" if items else "no_query_matches" if query.query else "not_requested",
            "time_range": {"start_utc_ms": query.start_utc_ms, "end_utc_ms": query.end_utc_ms},
            "total_active_ms": sum(value["active_ms"] for value in apps.values()),
            "apps": sorted(apps.values(), key=lambda value: value["active_ms"], reverse=True),
            "episode_count": len(episodes),
            "item_count": len(items),
            "truncated": context.truncated,
            "next_cursor": context.next_cursor,
            "coverage": "bounded_query_sample",
            "duration_basis": "observed_episode_span_not_user_active_time",
            "snippets": snippets,
            "organizer": health,
        }

    def health(self) -> dict[str, Any]:
        source = self.source.health()
        knowledge = self.knowledge.health(self.consumer_id)
        return {
            "source_newest_ingest_seq": source.newest_ingest_seq or 0,
            "organizer_processed_seq": knowledge.processed_ingest_seq,
            "organizer_lag": max(0, int(source.newest_ingest_seq or 0) - knowledge.processed_ingest_seq),
            "knowledge": knowledge.__dict__,
            "last_run": self._last_stats.__dict__,
            "quality": self.knowledge.quality_report(),
            "projection_version": PROJECTION_VERSION,
            "projection_rebuild_required": any(value != PROJECTION_VERSION for value in self.knowledge.projection_versions()),
        }

    def rebuild(self, *, batch_size: int = 200) -> OrganizerStats:
        self.knowledge.reset()
        self._last_stats = OrganizerStats()
        return self.catch_up(batch_size=batch_size)
