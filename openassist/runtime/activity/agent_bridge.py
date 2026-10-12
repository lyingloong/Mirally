"""Small JSONL bridge for on-demand activity knowledge queries.

The OpenClaw adapter talks to this module over stdin/stdout.  The bridge is
deliberately query-only: it never starts the activity collector and it does
not read or create an activity database until a caller has requested a
knowledge operation while activity collection is enabled.  That keeps
ordinary agent turns free of activity lookups and makes the same interface
available to every OpenClaw harness.

Protocol::

    {"id": "1", "command": "recent", "args": {}, "sync": true}
    {"id": "1", "ok": true, "result": {...}}

One response is written for every non-empty input line.  Diagnostics are sent
to stderr so stdout remains safe for a parent process to parse as JSONL.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

from openassist.core.activity.knowledge import ContextQuery
from openassist.core.activity.knowledge.events import _text_excerpt

from .config import ActivityConfig, load_activity_config
from .context_source import ActivityContextSource
from .organizer_service import ActivityOrganizerService


_MAX_SECONDS = 24 * 60 * 60
_MAX_WINDOW_SECONDS = 24 * 60 * 60
_MAX_QUERY_CHARS = 2_000
_MAX_IDS = 100
_MAX_ID_CHARS = 256
_MAX_ITEMS = 200
_MAX_EPISODES = 50
_MAX_TOKEN_BUDGET = 16_000
_MAX_SYNC_BATCH = 200
_MAX_SYNC_BATCHES = 10
_COMMANDS = frozenset({"health", "query", "events", "recent", "search", "around", "summary", "episode", "evidence"})


def _as_mapping(value: Any, *, name: str = "args") -> Mapping[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    return value


def _bounded_int(value: Any, *, name: str, minimum: int, maximum: int, default: int) -> int:
    if value is None:
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be an integer") from error
    return max(minimum, min(maximum, parsed))


def _bounded_text(value: Any, *, name: str, maximum: int, required: bool = False) -> Optional[str]:
    if value is None:
        if required:
            raise ValueError(f"{name} is required")
        return None
    text = str(value).strip()
    if required and not text:
        raise ValueError(f"{name} is required")
    if not text and not required:
        return None
    return text[:maximum]


def _string_tuple(value: Any, *, name: str, maximum: int = _MAX_IDS) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        values: Iterable[Any] = (value,)
    elif isinstance(value, (list, tuple, set)):
        values = value
    else:
        raise ValueError(f"{name} must be an array of strings")
    result: list[str] = []
    for item in list(values)[:maximum]:
        text = str(item).strip()
        if text:
            result.append(text[:_MAX_ID_CHARS])
    return tuple(dict.fromkeys(result))


def _bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    return bool(value)


class ActivityAgentBridge:
    """Handle bounded activity knowledge requests in one long-lived process."""

    def __init__(self, root: Path, config_path: Optional[Path] = None) -> None:
        self.root = root.resolve()
        self.config_path = config_path.resolve() if config_path is not None else None
        self.config: ActivityConfig = load_activity_config(self.root, self.config_path)
        self._service: Optional[ActivityOrganizerService] = None
        self._source = ActivityContextSource
        self._lock = threading.RLock()

    @property
    def knowledge_path(self) -> Path:
        return self.config.knowledge_database_path or self.config.database_path.with_name("activity-knowledge.sqlite3")

    def _base_status(self) -> dict[str, Any]:
        return {
            "enabled": bool(self.config.enabled),
            "database": str(self.config.database_path),
            "knowledge_database": str(self.knowledge_path),
        }

    def _storage_ready(self) -> bool:
        # Do not let an agent's health check create SQLite files.  The sensor
        # owns creation of these files; this bridge only observes an existing
        # store.
        return self.config.database_path.is_file() and self.config.database_path.stat().st_size > 0

    def _not_available(self, status: str, reason: str) -> dict[str, Any]:
        payload = {**self._base_status(), "status": status, "data_status": status, "reason": reason}
        # Keep ``result`` present on every successful response.  This lets
        # OpenClaw use one decoder for health, empty and populated queries.
        return {**payload, "result": payload}

    def _service_or_status(self) -> tuple[Optional[ActivityOrganizerService], Optional[dict[str, Any]]]:
        if not self.config.enabled:
            return None, self._not_available("disabled", "activity_collection_disabled")
        if not self._storage_ready():
            return None, self._not_available("not_ready", "activity_store_not_found")
        if self._service is None:
            self._service = ActivityOrganizerService(self.config)
        return self._service, None

    def _sync(self, service: ActivityOrganizerService, request: Mapping[str, Any], args: Mapping[str, Any]) -> Optional[dict[str, Any]]:
        if not _bool(request.get("sync"), _bool(args.get("sync"), True)):
            return None
        batch_size = _bounded_int(args.get("sync_batch_size"), name="sync_batch_size", minimum=1, maximum=_MAX_SYNC_BATCH, default=self.config.knowledge_batch_size)
        batches = _bounded_int(args.get("sync_batches"), name="sync_batches", minimum=1, maximum=_MAX_SYNC_BATCHES, default=_MAX_SYNC_BATCHES)
        stats = service.organizer.catch_up(batch_size=batch_size, max_batches=batches)
        return stats.__dict__

    def _query(self, args: Mapping[str, Any], *, include_uia_default: bool = False) -> ContextQuery:
        mode = str(args.get("mode", "outline"))
        purpose = str(args.get("purpose", "agent_context"))
        token_budget = _bounded_int(args.get("token_budget"), name="token_budget", minimum=128, maximum=_MAX_TOKEN_BUDGET, default=4_000)
        return ContextQuery(
            query=_bounded_text(args.get("query"), name="query", maximum=_MAX_QUERY_CHARS),
            start_utc_ms=_optional_int(args.get("start_utc_ms"), "start_utc_ms"),
            end_utc_ms=_optional_int(args.get("end_utc_ms"), "end_utc_ms"),
            apps=_string_tuple(args.get("apps"), name="apps"),
            windows=_string_tuple(args.get("windows"), name="windows"),
            kinds=_string_tuple(args.get("kinds"), name="kinds"),
            episode_id=_bounded_text(args.get("episode_id"), name="episode_id", maximum=_MAX_ID_CHARS),
            mode=mode,
            purpose=purpose,
            max_items=_bounded_int(args.get("max_items"), name="max_items", minimum=1, maximum=_MAX_ITEMS, default=30),
            max_episodes=_bounded_int(args.get("max_episodes"), name="max_episodes", minimum=1, maximum=_MAX_EPISODES, default=10),
            token_budget=token_budget,
            include_assets=_bool(args.get("include_assets"), False),
            include_uia=_bool(args.get("include_uia"), include_uia_default),
            include_ocr=_bool(args.get("include_ocr"), True),
            after_item_id=_bounded_text(args.get("after_item_id"), name="after_item_id", maximum=_MAX_ID_CHARS),
        )

    def handle(self, request: Mapping[str, Any]) -> dict[str, Any]:
        # ``command`` is the Python-facing spelling; ``op`` keeps the wire
        # contract terse for OpenClaw tool calls.  Accepting both also makes
        # the bridge straightforward to exercise outside the plugin.
        command = str(request.get("command", request.get("op", ""))).strip().lower()
        args = _as_mapping(request.get("args"))
        with self._lock:
            if command not in _COMMANDS:
                raise ValueError(f"unsupported command: {command or '<empty>'}")
            if command == "health":
                if not self.config.enabled:
                    return self._not_available("disabled", "activity_collection_disabled")
                if not self._storage_ready():
                    return self._not_available("not_ready", "activity_store_not_found")
                service, _ = self._service_or_status()
                assert service is not None
                health = service.health()
                return {
                    **self._base_status(),
                    "status": "ready",
                    "data_status": "index_lagging" if health["organizer_lag"] else "ready",
                    "health": health,
                    "result": health,
                }

            service, unavailable = self._service_or_status()
            if unavailable is not None:
                return unavailable
            assert service is not None
            sync_stats = self._sync(service, request, args)
            source = self._source(service)

            if command == "query":
                result: Any = source.query(self._query(args))
            elif command == "events":
                # Event fragments are the agent-facing deterministic action
                # view.  Keep UIA enabled by default here because the event
                # summary needs observed text to explain changes; callers can
                # explicitly disable it to reduce the response.
                query = self._query(args, include_uia_default=True)
                if query.start_utc_ms is None and query.end_utc_ms is None:
                    lookback = _bounded_int(args.get("seconds"), name="seconds", minimum=1, maximum=_MAX_SECONDS, default=300)
                    now = int(time.time() * 1000)
                    query = ContextQuery(**{**query.__dict__, "start_utc_ms": now - lookback * 1000, "end_utc_ms": now + 1})
                result = source.events(query)
            elif command == "recent":
                seconds = _bounded_int(args.get("seconds"), name="seconds", minimum=1, maximum=_MAX_SECONDS, default=300)
                mode = str(args.get("mode", "outline"))
                token = _bounded_int(args.get("token_budget"), name="token_budget", minimum=128, maximum=_MAX_TOKEN_BUDGET, default=4_000)
                result = source.recent(seconds=seconds, mode=mode, token_budget=token)
            elif command == "search":
                query = _bounded_text(args.get("query"), name="query", maximum=_MAX_QUERY_CHARS, required=True)
                mode = str(args.get("mode", "outline"))
                token = _bounded_int(args.get("token_budget"), name="token_budget", minimum=128, maximum=_MAX_TOKEN_BUDGET, default=4_000)
                max_items = _bounded_int(args.get("max_items"), name="max_items", minimum=1, maximum=_MAX_ITEMS, default=30)
                seconds = args.get("seconds")
                if seconds is None:
                    result = source.search_activity(query, mode=mode, token_budget=token, max_items=max_items)
                else:
                    lookback = _bounded_int(seconds, name="seconds", minimum=1, maximum=_MAX_SECONDS, default=300)
                    now = int(time.time() * 1000)
                    result = source.query(ContextQuery(
                        query=query,
                        start_utc_ms=now - lookback * 1000,
                        end_utc_ms=now + 1,
                        mode=mode,
                        max_items=max_items,
                        token_budget=token,
                    ))
            elif command == "around":
                at = _required_int(args.get("at_utc_ms"), "at_utc_ms")
                window = _bounded_int(args.get("window_seconds"), name="window_seconds", minimum=1, maximum=_MAX_WINDOW_SECONDS, default=60)
                mode = str(args.get("mode", "outline"))
                token = _bounded_int(args.get("token_budget"), name="token_budget", minimum=128, maximum=_MAX_TOKEN_BUDGET, default=4_000)
                result = source.get_activity_around(at, window_seconds=window, mode=mode, token_budget=token)
            elif command == "summary":
                query = self._query(args)
                if query.start_utc_ms is None and query.end_utc_ms is None:
                    lookback = _bounded_int(args.get("seconds"), name="seconds", minimum=1, maximum=_MAX_SECONDS, default=300)
                    now = int(time.time() * 1000)
                    query = ContextQuery(**{**query.__dict__, "start_utc_ms": now - lookback * 1000, "end_utc_ms": now + 1})
                result = source.summary(query)
            elif command == "episode":
                episode_id = _bounded_text(args.get("episode_id"), name="episode_id", maximum=_MAX_ID_CHARS, required=True)
                result = source.get_episode(episode_id)
                if result is not None:
                    budget = _bounded_int(args.get("token_budget"), name="token_budget", minimum=128, maximum=_MAX_TOKEN_BUDGET, default=4000) * 3
                    result["total_item_count"] = len(result["item_ids"])
                    result["truncated"] = False
                    while result["item_ids"] and len(json.dumps(result, ensure_ascii=False).encode("utf-8")) > budget:
                        result["item_ids"].pop()
                        result["truncated"] = True
            elif command == "evidence":
                ids = _string_tuple(args.get("evidence_ids", args.get("ids")), name="evidence_ids")
                if not ids:
                    raise ValueError("evidence_ids is required")
                result = source.get_evidence(list(ids), include_assets=_bool(args.get("include_assets"), False))
                budget = _bounded_int(args.get("token_budget"), name="token_budget", minimum=128, maximum=_MAX_TOKEN_BUDGET, default=4000) * 3
                selected = []
                for value in result:
                    value = dict(value)
                    value["text_truncated"] = False
                    if len(json.dumps(value, ensure_ascii=False).encode("utf-8")) > budget // 2:
                        text_limit = max(1, budget // 6)
                        if value.get("content_type") == "ocr":
                            value["text"], _, _ = _text_excerpt(
                                str(value.get("text", "")), "ocr_result", limit_override=text_limit
                            )
                        else:
                            value["text"] = value.get("text", "")[:min(600, text_limit)]
                        value["structured"] = {}
                        value["text_truncated"] = True
                    if len(json.dumps(selected + [value], ensure_ascii=False).encode("utf-8")) > budget:
                        break
                    selected.append(value)
                omitted = len(result) - len(selected)
                result = {"evidence": selected, "truncated": omitted > 0 or any(value["text_truncated"] for value in selected),
                          "omitted_count": omitted}
            # A caller can intentionally skip catch-up. Availability must still
            # describe the real index, rather than treating missing sync stats
            # as proof that every observation has been projected.
            lag = service.health()["organizer_lag"]
            data_status = "index_lagging" if lag else "ok"
            if result is None or isinstance(result, list) and not result or isinstance(result, dict) and "items" in result and not result["items"]:
                data_status = "index_lagging" if data_status == "index_lagging" else "empty"
            if isinstance(result, dict) and result.get("data_status") in {"empty", "projection_incomplete", "budget_exceeded"}:
                data_status = str(result["data_status"])
            if isinstance(result, dict) and result.get("truncated") and (
                result.get("items") == []
                or command == "evidence" and result.get("evidence") == []
                or command == "events" and result.get("events") == []
            ):
                data_status = "budget_exceeded"
                result["retry_hint"] = "Increase token_budget or use outline mode; matching activity was not empty."
            # Episode metadata and summary diagnostics can exceed the budget
            # even after their variable-length member lists have been cut.
            budget = _bounded_int(args.get("token_budget"), name="token_budget", minimum=128, maximum=_MAX_TOKEN_BUDGET, default=4000) * 3
            if len(json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > budget:
                data_status = "budget_exceeded"
                result = {"truncated": True, "retry_hint": "Increase token_budget or use outline mode."}
            if sync_stats is not None:
                return {"status": "ready", "data_status": data_status, "result": result, "sync": sync_stats}
            return {"status": "ready", "data_status": data_status, "result": result}

    def close(self) -> None:
        with self._lock:
            if self._service is not None:
                self._service.close()
                self._service = None


def _optional_int(value: Any, name: str) -> Optional[int]:
    if value is None or value == "":
        return None
    return _required_int(value, name)


def _required_int(value: Any, name: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be an integer") from error


def _error_payload(error: Exception) -> dict[str, Any]:
    return {"type": type(error).__name__, "message": str(error)[:2_000]}


def run_jsonl(bridge: ActivityAgentBridge, input_stream: Any = None, output_stream: Any = None) -> int:
    """Serve JSONL requests until EOF.  Returns a process-style status code."""

    input_stream = sys.stdin if input_stream is None else input_stream
    output_stream = sys.stdout if output_stream is None else output_stream
    try:
        for raw_line in input_stream:
            line = raw_line.strip()
            if not line:
                continue
            request_id: Any = None
            try:
                request = json.loads(line)
                if not isinstance(request, Mapping):
                    raise ValueError("request must be a JSON object")
                request_id = request.get("id")
                result = bridge.handle(request)
                response = {"id": request_id, "ok": True, **result}
            except Exception as error:
                response = {"id": request_id, "ok": False, "error": _error_payload(error)}
            output_stream.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n")
            output_stream.flush()
    finally:
        bridge.close()
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Mirally on-demand activity knowledge JSONL bridge")
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="product repository root")
    parser.add_argument("--config", type=Path, default=None, help="activity.yaml path")
    args = parser.parse_args(argv)
    return run_jsonl(ActivityAgentBridge(args.root, args.config))


if __name__ == "__main__":  # pragma: no cover - exercised by subprocess tests
    raise SystemExit(main())

