"""Read-only local browser for previously collected Activity data."""

from __future__ import annotations

import secrets
import threading
import time
import urllib.parse
import webbrowser
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional

from mirally.core.activity.knowledge import ActivityOrganizer, KnowledgeStore
from mirally.core.activity.store import ObservationStore

from .config import ActivityConfig
from .monitor import ActivityMonitorRequestHandler
from .read_surface import ActivityReadSurface, collector_view


class ActivityBrowserRuntime:
    """Serve the developer browser without starting a collector or writer.

    Reads go through the same ``ActivityReadSurface`` as the monitor, so both
    diagnostics faces answer a paging, redaction or status question the same way.
    """

    browser_only = True

    def __init__(self, config: ActivityConfig, *, root: Optional[Path] = None, sync: bool = True,
                 require_knowledge: bool = True) -> None:
        self.config = config
        self.root = Path(root or Path(__file__).resolve().parents[3])
        self.store = ObservationStore(config.database_path, asset_directory=config.asset_directory, read_only=True)
        knowledge_path = config.knowledge_database_path or config.database_path.with_name("activity-knowledge.sqlite3")
        # A normal browser launch may catch up the rebuildable projection.  A
        # no-sync launch is a genuinely read-only historical view of both
        # databases and fails clearly when the projection is absent.
        self.knowledge_path = knowledge_path
        self.knowledge_error = None
        try:
            self.knowledge_store = KnowledgeStore(knowledge_path, read_only=not sync)
        except (FileNotFoundError, RuntimeError) as error:
            if sync or require_knowledge:
                self.store.close()
                raise
            self.knowledge_store = None
            self.knowledge_error = str(error)
        except BaseException:
            self.store.close()
            raise
        self.organizer = (ActivityOrganizer(
            self.store, self.knowledge_store,
            idle_timeout_ms=int(config.knowledge_idle_timeout_seconds * 1000),
            allowed_lateness_ms=int(config.knowledge_allowed_lateness_seconds * 1000),
        ) if self.knowledge_store is not None else None)
        self.read_surface = ActivityReadSurface(self.store, self.knowledge_store, self.organizer)
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

    def metrics(self) -> dict[str, Any]:
        """Recorded collector metrics; this face has no collector process of its own."""
        diagnostics = self.store.get_state("collector_diagnostics", {})
        return {**diagnostics.get("metrics", {}), "scheduler": diagnostics.get("scheduler"),
                "read_only": True, "snapshot": True, "paused": bool(diagnostics.get("paused")),
                "updated_at_utc_ms": diagnostics.get("updated_at_utc_ms")}

    def status(self) -> dict[str, Any]:
        health = self.store.health()
        knowledge = self.organizer.health() if self.organizer else {
            "knowledge": {"available": False}, "status": "not_ready", "error": self.knowledge_error}
        diagnostics = self.store.get_state("collector_diagnostics", {})
        pause = self.store.get_state("collector_pause", {})
        collector = collector_view(collector=self.store.get_state("collector", {}),
                                   diagnostics=diagnostics, pause=pause, read_only=True)
        return {
            "browser": {"read_only": True, "source_read_only": True, "knowledge_read_only": True
                if self.knowledge_store is None else self.knowledge_store.read_only},
            "enabled": self.config.enabled,
            "content_enabled": self.config.policy.default_level == "content" and bool(
                self.config.visual_enabled or self.config.uia_content_enabled
                or self.config.input_text_enabled or self.config.clipboard_enabled),
            "database": str(self.config.database_path),
            "asset_directory": str(self.config.asset_directory),
            "knowledge_database": str(self.knowledge_path),
            "policy_version": self.config.policy.policy_version,
            "collection_level": self.config.policy.default_level,
            "collector": collector,
            "collector_diagnostics": diagnostics,
            "pause": pause,
            "health": health.__dict__,
            "knowledge": knowledge,
            "metrics": self.metrics(),
            "sources": diagnostics.get("sources", {}),
            "coverage": {"note": "只读查看已保存的诊断，不启动采集器"},
        }

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
        if self.knowledge_store is not None:
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
