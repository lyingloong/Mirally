"""Bounded, restartable retirement of evidence already saved as memory.

Memory, raw Activity and its projections live in separate databases. A durable
exact-ID journal bridges their commits; no source checkpoint or memory record
is reset to make cleanup succeed.
"""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, TYPE_CHECKING

from mirally.core.activity.knowledge import KnowledgeStore
from mirally.core.activity.knowledge.repository import deterministic_id
from mirally.core.activity.store import ObservationStore
from mirally.runtime.activity.config import load_activity_config

if TYPE_CHECKING:
    from mirally.core.memory import MemoryStore
    from .config import MemoryConfig
    from .service import MemoryService


_STATUS_FIELDS = ("state", "last_cleanup_utc_ms", "next_cleanup_utc_ms",
                  "deleted_observations", "deleted_assets", "reclaimed_bytes",
                  "error", "blocked_reason", "retained_shared_items",
                  "reclamation_pending", "skipped_observations")


def retention_status(config: MemoryConfig, store: MemoryStore | None) -> dict[str, Any]:
    saved = store.get_state("raw_retention", {}) if store else {}
    enabled = config.enabled and config.raw_retention.enabled
    return {"enabled": enabled, "interval_seconds": config.raw_retention.interval_seconds,
            "state": "not_ready" if enabled else "disabled",
            "last_cleanup_utc_ms": None, "next_cleanup_utc_ms": None,
            "deleted_observations": 0, "deleted_assets": 0, "reclaimed_bytes": 0,
            "error": None, "blocked_reason": None,
            **{key: saved[key] for key in _STATUS_FIELDS if key in saved},
            **({"state": "disabled"} if not enabled else {})}


class RawRetentionMaintenance:
    def __init__(self, service: MemoryService) -> None:
        self.service = service
        self.fence_owner_thread_id: int | None = None

    def _semantic_receipt_matches(self, window_id: str, memory: dict[str, Any], generation: str) -> bool:
        store = self.service._writer
        assert store is not None
        receipt = store.get_state("semantic_receipt:" + window_id, {})
        expected_hash = (memory.get("metadata") or {}).get("input_hash")
        completed = receipt.get("completed_at_utc_ms")
        sources = receipt.get("source_observation_ids")
        return bool(isinstance(expected_hash, str) and expected_hash and receipt.get("input_hash") == expected_hash
                    and receipt.get("source_generation") == generation
                    and receipt.get("scope") == memory.get("scope")
                    and isinstance(completed, int) and not isinstance(completed, bool) and completed > 0
                    and isinstance(sources, list) and all(isinstance(value, str) for value in sources)
                    and sources and set(memory.get("source_observation_ids") or ()) == set(sources))

    @contextmanager
    def _fence(self) -> Iterator[None]:
        store = self.service._writer
        assert store is not None
        with store.transaction():
            previous = self.fence_owner_thread_id
            self.fence_owner_thread_id = threading.get_ident()
            try:
                yield
            finally:
                self.fence_owner_thread_id = previous

    def step(self) -> dict[str, Any]:
        service, now = self.service, self.service.clock()
        store = service._writer
        assert store is not None and service._organizer is not None
        if not service.config.raw_retention.enabled:
            return retention_status(service.config, store)
        state = store.get_state("raw_retention", {})
        interval = int(service.config.raw_retention.interval_seconds * 1000)
        if not state:
            state = {"state": "scheduled", "next_cleanup_utc_ms": now + interval,
                     "last_cleanup_utc_ms": None, "deleted_observations": 0,
                     "deleted_assets": 0, "reclaimed_bytes": 0, "error": None}
            store.set_state("raw_retention", state)
            return retention_status(service.config, store)
        if not state.get("cycle") and now < int(state.get("next_cleanup_utc_ms") or 0):
            return retention_status(service.config, store)
        try:
            source = service._organizer.source
            generation = source.generation()
            cycle = state.get("cycle")
            if cycle and cycle["generation"] != generation:
                # An unfinished batch belongs to a replaced source. It must
                # never be replayed against the replacement, even on matching IDs.
                journal = cycle.get("journal", {})
                abandoned = {"generation": cycle["generation"], "replacement_generation": generation,
                             "through_ingest_seq": cycle["through_ingest_seq"],
                             "before_utc_ms": cycle["before_utc_ms"],
                             "observation_ids": journal.get("observation_ids", []),
                             "window_ids": journal.get("window_ids", []),
                             "at_utc_ms": now, "reason": "source_generation_changed"}
                state.pop("cycle", None)
                state.update(state="blocked", error=None, blocked_reason="source_generation_changed",
                             next_cleanup_utc_ms=now + interval)
                with store.transaction():
                    store.set_state("raw_retention_abandoned", abandoned)
                    store.set_state("raw_retention", state)
                return retention_status(service.config, store)
            if not cycle:
                checkpoint = store.get_state("activity_checkpoint", {})
                if checkpoint.get("generation") != generation:
                    state.update(state="blocked", blocked_reason="memory_checkpoint_unavailable")
                    store.set_state("raw_retention", state)
                    return retention_status(service.config, store)
                window_ms = service.config.window_seconds * 1000
                cycle = {"generation": generation, "through_ingest_seq": int(checkpoint["ingest_seq"]),
                         "before_utc_ms": max(0, (now - 60_000) // window_ms * window_ms),
                         "scan_cursor": 0, "started_utc_ms": now,
                         "skipped_observations": 0, "retained_shared_items": 0}
                state.update(cycle=cycle, state="cleaning", error=None, blocked_reason=None,
                             deleted_observations=0, deleted_assets=0, reclaimed_bytes=0,
                             reclamation_pending=False, retained_shared_items=0, skipped_observations=0)
                store.set_state("raw_retention", state)
            if cycle.get("journal"):
                self._apply_batch(state, cycle)
            else:
                self._prepare_batch(state, cycle)
        except Exception as error:
            # The last committed journal is authoritative after any cross-store
            # failure; never replace it with an in-memory, partially updated copy.
            state = store.get_state("raw_retention", state)
            state.update(state="degraded", error=f"{type(error).__name__}: {error}"[:700])
            store.set_state("raw_retention", state)
        return retention_status(service.config, store)

    def _connection(self) -> sqlite3.Connection:
        assert self.service._organizer is not None
        path = self.service._organizer.source.path
        connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
        connection.execute("PRAGMA query_only=ON")
        return connection

    def _prepare_batch(self, state: dict[str, Any], cycle: dict[str, Any]) -> None:
        service, store = self.service, self.service._writer
        assert store is not None and service._organizer is not None
        connection = self._connection()
        try:
            rows = connection.execute(
                "SELECT observation_id,ingest_seq FROM observations WHERE ingest_seq>? "
                "AND ingest_seq<=? AND observed_at_utc_ms<? ORDER BY ingest_seq LIMIT 500",
                (cycle["scan_cursor"], cycle["through_ingest_seq"], cycle["before_utc_ms"]),
            ).fetchall()
        finally:
            connection.close()
        if not rows:
            self._finish_cycle(state, cycle)
            return
        with store.durable_transaction():
            latest = store.get_state("raw_retention", {})
            if latest.get("cycle") != cycle:
                return
            # Check active records inside the writer transaction. An explicit
            # forget between page selection and this barrier must win safely.
            ids, windows = [], set()
            for observation_id, _ in rows:
                window_id = store.get_state("observation_window:" + observation_id)
                if window_id is None:
                    # Existing installations predate the direct source map.
                    item = service._organizer.knowledge.get_item_by_observation_id(observation_id)
                    if item is None:
                        continue
                    window = item.start_utc_ms // (service.config.window_seconds * 1000)
                    window_id = deterministic_id("memory_window", cycle["generation"], service._scope(item), window)
                window_state = store.get_state("window:" + window_id, {})
                memory = store.get_memory("episodic_" + window_id)
                if (not memory or window_state.get("generation") != cycle["generation"] or
                        observation_id not in memory.get("source_observation_ids", ()) or
                        not service._stable(memory, window_state) or
                        not self._semantic_receipt_matches(window_id, memory, cycle["generation"])):
                    continue
                ids.append(observation_id)
                windows.add(window_id)
            cycle["skipped_observations"] += len(rows) - len(ids)
            if not ids:
                cycle["scan_cursor"] = rows[-1][1]
                state.update(cycle=cycle, state="cleaning")
                store.set_state("raw_retention", state)
                return
            cycle["journal"] = {"observation_ids": ids, "window_ids": sorted(windows),
                                "next_scan_cursor": rows[-1][1], "projections": {}}
            # Freeze archived memories before any derived text is removed.
            # Late events use a new epoch, so a partial projection cannot replace
            # the previously complete permanent episodic record.
            for window_id in windows:
                window_state = store.get_state("window:" + window_id, {})
                if not window_state.get("sealed"):
                    window_state["sealed"] = True
                    store.set_state("window:" + window_id, window_state)
                    base_id = window_state.get("base_window_id", window_id)
                    epoch = store.get_state("window_epoch:" + base_id, {"epoch": 0, "id": base_id})
                    if epoch["id"] == window_id:
                        epoch = {"epoch": int(epoch["epoch"]) + 1}
                        epoch["id"] = deterministic_id("memory_late_window", base_id, epoch["epoch"])
                        store.set_state("window_epoch:" + base_id, epoch)
                annotation_key = "memory_archival:episodic_" + window_id
                if not store.get_state(annotation_key):
                    annotation = {"source_retention_status": "archiving", "references_unavailable": True}
                    store.set_state(annotation_key, annotation)
                    store.set_state("memory_archival:working_" + window_id, annotation)
            state.update(cycle=cycle, state="cleaning", error=None, blocked_reason=None)
            store.set_state("raw_retention", state)
        self._apply_batch(state, cycle)

    def _activity_knowledge_path(self) -> Path:
        activity = load_activity_config(self.service.product_root, self.service.config.activity_config_path)
        return activity.knowledge_database_path or activity.database_path.with_name("activity-knowledge.sqlite3")

    def _external_knowledge(self) -> KnowledgeStore | None:
        path = self._activity_knowledge_path().resolve()
        service = self.service
        assert service._organizer is not None
        private = service._organizer.knowledge.path.resolve()
        if path == private or path.is_file() and private.is_file() and path.samefile(private):
            return None
        for reserved in (service.config.database_path, service._organizer.source.path):
            if path == reserved.resolve() or path.is_file() and reserved.is_file() and path.samefile(reserved):
                raise ValueError("Activity knowledge must be independent of permanent memory and raw storage")
        if not path.is_file():
            return None
        # A configured path can point at an unrelated existing SQLite file.
        # Validate without migrations before opening its maintenance writer.
        with KnowledgeStore(path, read_only=True):
            pass
        return KnowledgeStore(path)

    def _apply_batch(self, state: dict[str, Any], cycle: dict[str, Any]) -> None:
        store = self.service._writer
        assert store is not None
        # BEGIN IMMEDIATE fences another process's Memory CLI, and the store's
        # writer lock fences this process's forget, through the raw commit and
        # journal acknowledgement. Read-only memory queries remain independent.
        with self._fence():
            latest = store.get_state("raw_retention", {})
            live = latest.get("cycle") or {}
            journal = live.get("journal")
            if not journal or (live.get("generation"), live.get("through_ingest_seq"), live.get("before_utc_ms")) != (
                    cycle.get("generation"), cycle.get("through_ingest_seq"), cycle.get("before_utc_ms")):
                return
            valid_ids, valid_windows = set(), []
            for window_id in journal["window_ids"]:
                memory = store.get_memory("episodic_" + window_id)
                window = store.get_state("window:" + window_id, {})
                if (memory and window.get("generation") == live["generation"] and self.service._stable(memory, window)
                        and self._semantic_receipt_matches(window_id, memory, live["generation"])):
                    valid_ids.update(memory.get("source_observation_ids") or ())
                    valid_windows.append(window_id)
            selected = [value for value in journal["observation_ids"] if value in valid_ids]
            if len(selected) != len(journal["observation_ids"]):
                live["skipped_observations"] += len(journal["observation_ids"]) - len(selected)
                if not selected:
                    live["scan_cursor"] = journal["next_scan_cursor"]
                    live.pop("journal")
                    latest.update(state="cleaning", cycle=live, blocked_reason=None, error=None)
                    store.set_state("raw_retention", latest)
                    return
                journal.update(observation_ids=selected, window_ids=valid_windows, projections={})
                journal.pop("raw_receipt", None)
                journal.pop("accounted_observations", None)
                store.set_state("raw_retention", latest)
            self._apply_batch_locked(latest, live)

    def _apply_batch_locked(self, state: dict[str, Any], cycle: dict[str, Any]) -> None:
        service, store = self.service, self.service._writer
        assert store is not None and service._organizer is not None
        source, projection = service._organizer.source, service._organizer.knowledge
        if source.generation() != cycle["generation"]:
            raise ValueError("source generation changed during retention")
        journal = cycle["journal"]
        bounds = {"source_generation": cycle["generation"],
                  "through_ingest_seq": cycle["through_ingest_seq"], "before_utc_ms": cycle["before_utc_ms"]}
        if "private" not in journal["projections"]:
            journal["projections"]["private"] = projection.purge_memorized(journal["observation_ids"], **bounds)
            store.set_state("raw_retention", state)
        external = self._external_knowledge()
        try:
            if external and "activity" not in journal["projections"]:
                journal["projections"]["activity"] = external.purge_memorized(journal["observation_ids"], **bounds)
                store.set_state("raw_retention", state)
            with ObservationStore(source.path, asset_directory=source.asset_directory) as writer:
                receipt = writer.purge_memorized(
                    store_generation=cycle["generation"], through_ingest_seq=cycle["through_ingest_seq"],
                    before_utc_ms=cycle["before_utc_ms"], observation_ids=journal["observation_ids"])
            journal["raw_receipt"] = receipt
            reclamation = receipt.get("reclamation") or {}
            reclaimed = int(reclamation.get("reclaimed_bytes", max(0,
                sum(int(value) for value in (reclamation.get("before_bytes") or {}).values()) -
                sum(int(value) for value in (reclamation.get("after_bytes") or {}).values()))))
            observations = int(receipt.get("observations_deleted", 0))
            accounted = int(journal.get("accounted_observations", 0))
            state.update(deleted_observations=int(state.get("deleted_observations", 0)) + max(0, observations - accounted),
                         deleted_assets=int(state.get("deleted_assets", 0)) + int(receipt.get("assets_deleted", 0)),
                         reclaimed_bytes=int(state.get("reclaimed_bytes", 0)) + reclaimed,
                         reclamation_pending=bool(state.get("reclamation_pending") or receipt.get("status") != "complete"))
            journal["accounted_observations"] = max(accounted, observations)
            store.set_state("raw_retention", state)
            floor = int(receipt.get("retention_floor", 0))
            projection.advance_retention_floor(cycle["generation"], floor)
            if external:
                external.advance_retention_floor(cycle["generation"], floor)
            self._annotate(journal["window_ids"])
            if not receipt.get("logical_complete", receipt.get("status") == "complete"):
                state.update(state="degraded", error="raw assets await cleanup",
                             blocked_reason="asset_cleanup_pending")
                store.set_state("raw_retention", state)
                return
            # Shared projections may need a later page to retire all members.
            # Keep scanning; retrying this page forever would deadlock >500 IDs.
            cycle["retained_shared_items"] += sum(int(value.get("retained_shared_items", 0))
                                                  for value in journal["projections"].values())
            cycle["scan_cursor"] = journal["next_scan_cursor"]
            cycle.pop("journal")
            state.update(cycle=cycle, state="cleaning", error=None, blocked_reason=None)
            with store.transaction():
                for observation_id in journal["observation_ids"]:
                    store.delete_state("observation_window:" + observation_id)
                for window_id in journal["window_ids"]:
                    window = store.get_state("window:" + window_id, {})
                    annotation = store.get_state("memory_archival:episodic_" + window_id, {})
                    if annotation.get("source_retention_status") == "archived":
                        window["item_ids"] = []
                        store.set_state("window:" + window_id, window)
                store.set_state("raw_retention", state)
            service._source_statistics = None
        finally:
            if external:
                external.close()

    def _annotate(self, window_ids: list[str]) -> None:
        store = self.service._writer
        assert store is not None
        connection = self._connection()
        try:
            for window_id in window_ids:
                memory = store.get_memory("episodic_" + window_id)
                if not memory:
                    continue
                refs = list(memory.get("source_observation_ids") or ())
                remaining = 0
                for index in range(0, len(refs), 500):
                    page = refs[index:index + 500]
                    remaining += int(connection.execute(
                        "SELECT COUNT(*) FROM observations WHERE observation_id IN (" +
                        ",".join("?" for _ in page) + ")", page).fetchone()[0])
                if remaining == len(refs):
                    continue
                annotation = {"source_retention_status": "archived" if remaining == 0 else "partially_archived",
                              "references_unavailable": True, "archived_at_utc_ms": self.service.clock(),
                              "archived_observation_count": len(refs) - remaining}
                with store.transaction():
                    store.set_state("memory_archival:episodic_" + window_id, annotation)
                    store.set_state("memory_archival:working_" + window_id, annotation)
        finally:
            connection.close()

    def _finish_cycle(self, state: dict[str, Any], cycle: dict[str, Any]) -> None:
        service, store = self.service, self.service._writer
        assert store is not None and service._organizer is not None
        reclaimed = 0
        retained_shared = int(cycle.get("retained_shared_final", 0))
        projection = service._organizer.knowledge
        if state.get("reclamation_pending"):
            source = service._organizer.source
            with ObservationStore(source.path, asset_directory=source.asset_directory) as writer:
                receipt = writer.purge_memorized(
                    store_generation=cycle["generation"], through_ingest_seq=cycle["through_ingest_seq"],
                    before_utc_ms=cycle["before_utc_ms"], observation_ids=[])
            state["reclaimed_bytes"] = int(state.get("reclaimed_bytes", 0)) + int(
                (receipt.get("reclamation") or {}).get("reclaimed_bytes", 0))
            state["reclamation_pending"] = receipt.get("status") != "complete"
            if state["reclamation_pending"]:
                state.update(state="degraded", error="raw database space reclamation awaits retry",
                             blocked_reason="raw_compaction_pending")
                store.set_state("raw_retention", state)
                return
        # Persist compaction progress too: a blocked external reader should not
        # force repeated expensive VACUUM of a projection already compacted.
        for name in ("private", "activity"):
            if name in cycle.get("compacted", []):
                continue
            external = self._external_knowledge() if name == "activity" else None
            try:
                target = external if name == "activity" else projection
                if target:
                    receipt = target.compact_memorized()
                    reclaimed += int(receipt.get("reclaimed_bytes", 0))
                    if receipt.get("compaction_deferred") or receipt.get("status") == "pending":
                        state.update(state="degraded", error="projection space reclamation awaits retry",
                                     blocked_reason="projection_compaction_pending",
                                     reclaimed_bytes=int(state.get("reclaimed_bytes", 0)) + reclaimed)
                        store.set_state("raw_retention", state)
                        return
                    retained_shared += int(receipt.get("retained_shared_items", 0))
            finally:
                if external:
                    external.close()
            cycle.setdefault("compacted", []).append(name)
            cycle["retained_shared_final"] = retained_shared
            state["reclaimed_bytes"] = int(state.get("reclaimed_bytes", 0)) + reclaimed
            reclaimed = 0
            store.set_state("raw_retention", state)
        state.pop("cycle", None)
        skipped = int(cycle.get("skipped_observations", 0))
        state.update(state="partial" if skipped or retained_shared or state.get("reclamation_pending") else "scheduled",
                     last_cleanup_utc_ms=service.clock(),
                     next_cleanup_utc_ms=service.clock() + int(service.config.raw_retention.interval_seconds * 1000),
                     skipped_observations=skipped, retained_shared_items=retained_shared,
                     error=None, blocked_reason="semantic_memory_pending_observations_retained" if skipped else None)
        store.set_state("raw_retention", state)
