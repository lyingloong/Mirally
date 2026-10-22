"""Runtime lifecycle for the activity knowledge organizer."""

from __future__ import annotations

import threading
import time
from typing import Any, Optional

from mirally.core.activity.knowledge import ActivityOrganizer, ContextQuery, KnowledgeStore
from mirally.core.activity.store import ObservationStore

from .config import ActivityConfig


_THREAD_JOIN_TIMEOUT_SECONDS = 5.0


class ActivityOrganizerService:
    """Run the knowledge projection beside the collector.

    The service owns a read-only consumer of the raw store and a separate
    derived KnowledgeStore.  A collector can continue writing while this
    service is stopped or rebuilding.
    """

    def __init__(self, config: ActivityConfig, *, consumer_id: str = "activity-knowledge") -> None:
        knowledge_path = config.knowledge_database_path or config.database_path.with_name("activity-knowledge.sqlite3")
        self.config = config
        self.consumer_id = consumer_id
        self.source = ObservationStore(config.database_path, asset_directory=config.asset_directory, read_only=True)
        self.knowledge = KnowledgeStore(knowledge_path)
        self.organizer = ActivityOrganizer(
            self.source,
            self.knowledge,
            consumer_id=consumer_id,
            idle_timeout_ms=int(config.knowledge_idle_timeout_seconds * 1000),
            allowed_lateness_ms=int(config.knowledge_allowed_lateness_seconds * 1000),
        )
        self._operation_lock = threading.RLock()
        self.stop_event = threading.Event()
        self.thread: Optional[threading.Thread] = None
        self.last_error: Optional[str] = None
        self._close_lock = threading.RLock()
        self._closed = False

    def process_once(self, *, limit: int = 200) -> dict[str, Any]:
        with self._operation_lock:
            try:
                stats = self.organizer.process_once(limit=limit)
                self.last_error = None
                return stats.__dict__
            except Exception as error:
                self.last_error = f"{type(error).__name__}: {error}"
                raise

    def rebuild(self, *, limit: int = 200) -> dict[str, Any]:
        with self._operation_lock:
            stats = self.organizer.rebuild(batch_size=limit)
            return stats.__dict__

    def context(self, query: ContextQuery):
        with self._operation_lock:
            return self.organizer.context(query)

    def health(self) -> dict[str, Any]:
        with self._operation_lock:
            value = self.organizer.health()
            value["last_error"] = self.last_error
            return value

    def _run(self, interval_seconds: float, batch_size: int) -> None:
        while not self.stop_event.is_set():
            try:
                self.process_once(limit=batch_size)
            except Exception:
                # The cursor remains at the last committed batch.  The next
                # pass can retry after a transient SQLite/source failure.
                pass
            self.stop_event.wait(max(0.1, interval_seconds))

    def start(self, *, interval_seconds: Optional[float] = None, batch_size: Optional[int] = None) -> None:
        with self._close_lock:
            if self._closed:
                raise RuntimeError("activity organizer service is closed")
            if self.thread is not None and self.thread.is_alive():
                return
            self.stop_event.clear()
            interval_seconds = self.config.knowledge_poll_interval_seconds if interval_seconds is None else interval_seconds
            batch_size = self.config.knowledge_batch_size if batch_size is None else batch_size
            self.thread = threading.Thread(target=self._run, args=(interval_seconds, batch_size), name="mirally-activity-organizer", daemon=True)
            self.thread.start()

    def close(self) -> None:
        with self._close_lock:
            if self._closed:
                return
            self.stop_event.set()
            worker = self.thread
            if worker is not None and worker.is_alive() and worker is not threading.current_thread():
                worker.join(timeout=_THREAD_JOIN_TIMEOUT_SECONDS)
            if worker is not None and worker.is_alive():
                # Keep both connections open so a late organizer iteration
                # cannot use a closed SQLite handle.  A later close() can
                # retry after the worker has stopped.
                raise RuntimeError("organizer thread did not stop; activity stores remain open")

            # A caller may have invoked process_once() directly while the
            # background thread was stopping.  Serialize final connection
            # teardown with those operations as well.
            with self._operation_lock:
                self.thread = None
                self.knowledge.close()
                self.source.close()
                self._closed = True

    def __enter__(self) -> "ActivityOrganizerService":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()
