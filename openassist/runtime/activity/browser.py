"""Read-only local browser for previously collected Activity data."""

from __future__ import annotations

import secrets
import threading
import time
import urllib.parse
import webbrowser
from dataclasses import replace
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping, Optional

from openassist.core.activity.knowledge import ActivityOrganizer, KnowledgeStore
from openassist.core.activity.store import CursorExpiredError, ObservationStore

from .config import ActivityConfig
from .monitor import ActivityMonitorRequestHandler, _safe_observation


class _ReadOnlySensor:
    """Small compatibility surface used by the shared monitor request handler."""

    def metrics_snapshot(self) -> dict[str, Any]:
        return {"read_only": True, "paused": False}


class ActivityBrowserRuntime:
    """Serve the developer browser without starting a collector or writer."""

    browser_only = True

    def __init__(self, config: ActivityConfig, *, root: Optional[Path] = None, sync: bool = True) -> None:
        self.config = config
        self.root = Path(root or Path(__file__).resolve().parents[3])
        self.store = ObservationStore(config.database_path, asset_directory=config.asset_directory, read_only=True)
        knowledge_path = config.knowledge_database_path or config.database_path.with_name("activity-knowledge.sqlite3")
        # A normal browser launch may catch up the rebuildable projection.  A
        # no-sync launch is a genuinely read-only historical view of both
        # databases and fails clearly when the projection is absent.
        self.knowledge_store = KnowledgeStore(knowledge_path, read_only=not sync)
        self.organizer = ActivityOrganizer(
            self.store,
            self.knowledge_store,
            idle_timeout_ms=int(config.knowledge_idle_timeout_seconds * 1000),
            allowed_lateness_ms=int(config.knowledge_allowed_lateness_seconds * 1000),
        )
        self.sensor = _ReadOnlySensor()
        self.token = secrets.token_urlsafe(24)
        self.httpd: Optional[ThreadingHTTPServer] = None
        self.http_thread: Optional[threading.Thread] = None
        self.sync = bool(sync)
        self._closed = False

    @property
    def url(self) -> str:
        if self.httpd is None:
            return ""
        port = int(self.httpd.server_address[1])
        return f"http://127.0.0.1:{port}/?token={urllib.parse.quote(self.token)}"

    def _handler_factory(self):
        runtime = self

        class Handler(ActivityMonitorRequestHandler):
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                super().__init__(runtime, *args, **kwargs)

        return Handler

    def start(self, *, port: int = 0) -> str:
        # The derived store is rebuildable. Catch up here so an existing raw
        # database is immediately browsable, while the source remains read-only.
        if self.sync:
            self.organizer.catch_up(batch_size=self.config.knowledge_batch_size, max_batches=100)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", int(port)), self._handler_factory())
        self.httpd.daemon_threads = True
        self.http_thread = threading.Thread(
            target=self.httpd.serve_forever,
            kwargs={"poll_interval": 0.1},
            name="mirally-activity-browser-http",
            daemon=True,
        )
        self.http_thread.start()
        return self.url

    def status(self) -> dict[str, Any]:
        health = self.store.health()
        knowledge = self.organizer.health()
        return {
            "browser": {"read_only": True, "source_read_only": True, "knowledge_read_only": self.knowledge_store.read_only},
            "enabled": self.config.enabled,
            "database": str(self.config.database_path),
            "asset_directory": str(self.config.asset_directory),
            "knowledge_database": str(self.knowledge_store.path),
            "policy_version": self.config.policy.policy_version,
            "collection_level": self.config.policy.default_level,
            "collector": self.store.get_state("collector", {}),
            "collector_diagnostics": self.store.get_state("collector_diagnostics", {}),
            "health": health.__dict__,
            "knowledge": knowledge,
            "metrics": self.sensor.metrics_snapshot(),
            "sources": {},
            "coverage": {"note": "历史只读浏览，不启动采集器"},
        }

    def browser_observations(self, params: Mapping[str, list[str]]) -> dict[str, Any]:
        """Return the raw source newest-first with a stable before cursor."""
        limit = max(1, min(200, int((params.get("limit") or ["100"])[0])))
        before_value = (params.get("before") or [None])[0]
        before = None if before_value in (None, "") else int(before_value)
        start_value = (params.get("start") or [None])[0]
        end_value = (params.get("end") or [None])[0]
        rows = self.store.query(
            start_utc_ms=None if start_value in (None, "") else int(start_value),
            end_utc_ms=None if end_value in (None, "") else int(end_value),
            app_ids=[v for v in params.get("app", ()) if v],
            kinds=[v for v in params.get("kind", ()) if v],
            query=(params.get("q") or [None])[0] or None,
            before_ingest_seq=before,
            descending=True,
            limit=limit + 1,
        )
        has_more = len(rows) > limit
        selected = rows[:limit]
        cursor = selected[-1].ingest_seq if selected and has_more else None
        include_content = (params.get("include_content") or [""])[0].lower() in {"1", "true", "yes"}
        return {
            "data_status": "ok" if selected else "empty",
            "observations": [_safe_observation(row, include_content=include_content) for row in selected],
            "cursor": cursor,
            "has_more": has_more,
        }

    def events(self, params: Mapping[str, list[str]]) -> dict[str, Any]:
        after = max(0, int((params.get("after") or ["0"])[0]))
        limit = max(1, min(200, int((params.get("limit") or ["100"])[0])))
        include_content = (params.get("include_content") or [""])[0].lower() in {"1", "true", "yes"}
        kinds = [value for value in params.get("kind", []) if value]
        apps = [value for value in params.get("app", []) if value]
        try:
            if kinds or apps:
                rows = self.store.query(
                    after_ingest_seq=after, kinds=kinds or None, app_ids=apps or None, limit=limit + 1
                )
                has_more = len(rows) > limit
                selected = rows[:limit]
                cursor = selected[-1].ingest_seq if selected and selected[-1].ingest_seq is not None else after
            else:
                rows, cursor, has_more = self.store.read_since(after, limit=limit)
        except CursorExpiredError as error:
            return {"events": [], "cursor": after, "has_more": False, "cursor_expired": True, "error": str(error)}
        selected = rows[:limit] if (kinds or apps) else rows
        return {"events": [_safe_observation(row, include_content=include_content) for row in selected], "cursor": cursor, "has_more": has_more}

    @staticmethod
    def _query(params: Mapping[str, list[str]], *, mode: Optional[str] = None) -> Any:
        return ActivityMonitorRequestHandler._context_query(params, mode=mode, purpose="diagnostics")

    def raw(self, params: Mapping[str, list[str]]) -> dict[str, Any]:
        # Direct developer browsing defaults to full local source content.
        # HTTP responses still honor the page's explicit reveal toggle.
        expanded = dict(params)
        expanded.setdefault("include_content", ["1"])
        result = self.events(expanded)
        return {
            "data_status": "ok" if result["events"] else "empty",
            "observations": result["events"],
            "cursor": result["cursor"],
            "has_more": result["has_more"],
            "cursor_expired": result.get("cursor_expired", False),
        }

    def items(self, params: Mapping[str, list[str]]) -> dict[str, Any]:
        query = self._query(params, mode="structured")
        limit = max(1, min(500, query.max_items))
        values = self.knowledge_store.query_items(
            query=query.query, start_utc_ms=query.start_utc_ms, end_utc_ms=query.end_utc_ms,
            apps=query.apps, windows=query.windows, kinds=query.kinds, episode_id=query.episode_id,
            after_item_id=query.after_item_id, limit=min(500, limit + 1),
        )
        selected = values[:limit]
        return {
            "data_status": "ok" if selected else "empty",
            "items": [value.to_dict() for value in selected],
            "truncated": len(values) > limit,
            "next_cursor": selected[-1].item_id if len(values) > limit else None,
        }

    def item(self, item_id: str) -> Optional[dict[str, Any]]:
        value = self.knowledge_store.get_item(str(item_id))
        return None if value is None else value.to_dict()

    def episodes(self, params: Mapping[str, list[str]]) -> dict[str, Any]:
        start = (params.get("start") or [None])[0]
        end = (params.get("end") or [None])[0]
        limit = max(1, min(100, int((params.get("limit") or ["50"])[0])))
        values = self.knowledge_store.query_episodes(
            start_utc_ms=None if start in (None, "") else int(start),
            end_utc_ms=None if end in (None, "") else int(end),
            query=(params.get("q") or [None])[0] or None,
            apps=tuple(params.get("app", ())),
            windows=tuple(params.get("window", ())),
            kinds=tuple(params.get("kind", ())),
            after_episode_id=(params.get("after_episode") or [None])[0] or None,
            limit=limit + 1,
        )
        truncated = len(values) > limit
        selected = values[:limit]
        return {
            "data_status": "ok" if selected else "empty", "episodes": [value.to_dict() for value in selected],
            "truncated": truncated, "next_cursor": selected[-1].episode_id if truncated else None,
        }

    def episode(self, episode_id: str) -> Optional[dict[str, Any]]:
        value = self.organizer.get_episode(str(episode_id))
        return None if value is None else value.to_dict()

    def evidence(self, evidence_ids: list[str], *, include_assets: bool = False) -> dict[str, Any]:
        return {"evidence": [value.to_dict() for value in self.organizer.get_evidence(evidence_ids, include_assets=include_assets)]}

    def evidence_query(self, params: Mapping[str, list[str]], *, include_assets: bool = False) -> dict[str, Any]:
        """Page complete Evidence rows through matching projected items."""
        query = self._query(params, mode="evidence")
        limit = max(1, min(500, query.max_items))
        items = self.knowledge_store.query_items(
            query=query.query, start_utc_ms=query.start_utc_ms, end_utc_ms=query.end_utc_ms,
            apps=query.apps, windows=query.windows, kinds=query.kinds, episode_id=query.episode_id,
            after_item_id=query.after_item_id, limit=limit + 1,
        )
        selected = items[:limit]
        evidence = self.knowledge_store.get_evidence_for_items(item.item_id for item in selected)
        if not include_assets:
            evidence = [replace(value, asset_refs=()) for value in evidence]
        return {
            "data_status": "ok" if evidence else "empty", "evidence": [value.to_dict() for value in evidence],
            "truncated": len(items) > limit, "next_cursor": selected[-1].item_id if len(items) > limit else None,
        }

    def asset(self, asset_id: str) -> dict[str, Any]:
        record = self.store.resolve_asset(str(asset_id), scope="activity-content")
        return {key: value for key, value in record.items() if key != "path"}

    def events_view(self, params: Mapping[str, list[str]]) -> dict[str, Any]:
        return self.organizer.activity_events(self._query(params, mode="evidence"))

    def timeline(self, params: Mapping[str, list[str]]) -> dict[str, Any]:
        return self.organizer.activity_timeline(self._query(params, mode="structured"))

    def context(self, params: Mapping[str, list[str]]) -> dict[str, Any]:
        return self.organizer.context(self._query(params)).to_dict()

    def summary(self, params: Mapping[str, list[str]]) -> dict[str, Any]:
        return self.organizer.activity_summary(self._query(params, mode="outline"))

    def candidates(self, params: Mapping[str, list[str]]) -> dict[str, Any]:
        limit = max(1, min(500, int((params.get("limit") or ["100"])[0])))
        status = (params.get("status") or [None])[0]
        return {"candidates": [value.to_dict() for value in self.knowledge_store.list_memory_candidates(status=status, limit=limit)]}

    def facts(self, params: Mapping[str, list[str]]) -> dict[str, Any]:
        limit = max(1, min(500, int((params.get("limit") or ["100"])[0])))
        status = (params.get("status") or [None])[0]
        return {"facts": [value.to_dict() for value in self.knowledge_store.list_memory_facts(status=status, limit=limit)]}

    def diagnostics(self) -> dict[str, Any]:
        return self.status()

    def request_stop(self) -> None:
        return

    def close(self) -> None:
        if self._closed:
            return
        if self.httpd is not None:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None
        if self.http_thread is not None and self.http_thread.is_alive() and self.http_thread is not threading.current_thread():
            self.http_thread.join(timeout=3.0)
        self.http_thread = None
        self.knowledge_store.close()
        self.store.close()
        self._closed = True

    def serve(self, *, port: int = 0, open_browser: bool = True, duration: Optional[float] = None) -> int:
        url = self.start(port=port)
        print(f"Activity Browser: {url}")
        print(f"Database: {self.config.database_path}")
        print("Read-only source; press Ctrl+C to stop the browser.")
        if open_browser:
            webbrowser.open(url)
        deadline = time.monotonic() + duration if duration else None
        try:
            while self.httpd is not None and (deadline is None or time.monotonic() < deadline):
                time.sleep(0.2)
        except KeyboardInterrupt:
            pass
        finally:
            self.close()
        return 0


__all__ = ["ActivityBrowserRuntime"]
