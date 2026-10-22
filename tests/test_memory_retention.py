"""Real temporary-store checks for scheduled, durable raw evidence retirement."""

from __future__ import annotations

import hashlib
import os
import sqlite3
import tempfile
import threading
import time
import unittest
from contextlib import closing, contextmanager
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from mirally.core.activity import Observation, ObservationStore
from mirally.core.activity.knowledge import KnowledgeStore
from mirally.core.activity.knowledge.models import Evidence
from mirally.core.memory import MemoryStore
from mirally.runtime.activity.config import load_activity_config
from mirally.runtime.activity.organizer_service import ActivityOrganizerService
from mirally.runtime.memory import MemoryConfig, MemoryReader, MemoryService, SemanticsConfig, load_memory_config
from mirally.runtime.memory.config import RawRetentionConfig


class MemoryRetentionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="mirally retention 中文 ")
        self.root = Path(self.temporary.name)
        self.source_path = self.root / "data raw" / "observations.sqlite3"
        self.activity_path = self.root / "profiles" / "activity.yaml"
        self.activity_path.parent.mkdir()
        self.public_path = self.root / "公共 knowledge.sqlite3"
        self.activity_path.write_text(
            "enabled: false\nstorage:\n  directory: data raw\n  database_path: data raw/observations.sqlite3\n"
            "  knowledge_database_path: 公共 knowledge.sqlite3\n", encoding="utf-8")
        self.config = MemoryConfig(True, self.root / "永久 memory.sqlite3", self.activity_path,
                                   self.root / "private projection.sqlite3", batch_size=500,
                                   raw_retention=RawRetentionConfig(interval_seconds=1))
        self.now = [1_800_000]
        self.source = ObservationStore(self.source_path, asset_directory=self.source_path.parent / "assets")
        self.services: list[MemoryService] = []
        self.sequence = 0
        self.fixture_semantic_receipts = True

    def tearDown(self) -> None:
        for service in reversed(self.services):
            service.close()
        self.source.close()
        self.temporary.cleanup()

    def observe(self, *, observed: int = 1000, title: str = "retained project.py", **kwargs) -> Observation:
        self.sequence += 1
        row = Observation.create(kind="window_context", source_id="test", source_epoch="epoch",
            source_seq=self.sequence, boot_id="boot", observed_at_utc_ms=observed,
            app_id="code.exe", window_instance_id="editor", payload={"window_title": title}, **kwargs)
        return self.source.append(row)

    def screenshot(self, *, observed: int = 1000) -> tuple[Observation, Path, int]:
        self.sequence += 1
        reservation = self.source.reserve_asset(mime_type="image/png", scope="foreground", policy_version=1)
        content = b"synthetic screenshot bytes\x00" * 1024
        row = Observation.create(kind="content_snapshot", source_id="test", source_epoch="epoch",
            source_seq=self.sequence, boot_id="boot", observed_at_utc_ms=observed,
            app_id="code.exe", window_instance_id="editor", asset_refs=(reservation.asset_id,),
            payload={"text": "saved screenshot context", "window_title": "screenshot project"})
        return self.source.commit_asset_bytes(reservation, content, row), reservation.final_path, len(content)

    def service(self, config: MemoryConfig | None = None) -> MemoryService:
        service = MemoryService(self.root, config=config or self.config, clock=lambda: self.now[0])
        step = service._retention.step
        def retire_acknowledged_fixture():
            # Most scenarios exercise cross-store cleanup recovery after a
            # successful semantic commit. Fixture receipts replace only the
            # provider call; the production deletion barrier remains real.
            if self.fixture_semantic_receipts:
                self.acknowledge_semantic(service)
            return step()
        service._retention.step = retire_acknowledged_fixture
        self.services.append(service)
        return service

    def acknowledge_semantic(self, service):
        for memory in service._writer.search(kinds=("episodic",), limit=100):
            if not memory["id"].startswith("episodic_"):
                continue
            metadata = memory.get("metadata") or {}
            if not metadata.get("input_hash"):
                metadata["input_hash"] = hashlib.sha256(memory["content"].encode()).hexdigest()
                memory["metadata"] = metadata
                service._writer.put_memory(memory)
            window_id = memory["id"].removeprefix("episodic_")
            service._writer.set_state("semantic_receipt:" + window_id, {
                "coverage_kind": "semantic", "source_generation": metadata["source_generation"],
                "input_hash": metadata["input_hash"], "scope": memory["scope"],
                "source_observation_ids": memory["source_observation_ids"],
                "completed_at_utc_ms": self.now[0],
            })

    def complete_cycle(self, service: MemoryService, *, passes: int = 10) -> dict:
        for _ in range(passes):
            result = service.process_once()
            state = service._writer.get_state("raw_retention", {})
            if not state.get("cycle"):
                return result["raw_retention"]
        self.fail("retention cycle did not finish")

    def test_default_hourly_schedule_and_config_validation(self) -> None:
        config = load_memory_config(self.root)
        self.assertTrue(config.raw_retention.enabled)
        self.assertEqual(config.raw_retention.interval_seconds, 3600)
        path = self.root / "memory.yaml"
        path.write_text('raw_retention:\n  enabled: "true"\n', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "boolean"):
            load_memory_config(self.root, path)
        path.write_text("raw_retention:\n  interval_seconds: 0\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "interval_seconds"):
            load_memory_config(self.root, path)
        service = self.service(replace(self.config, raw_retention=RawRetentionConfig()))
        row = self.observe()
        state = service.process_once()["raw_retention"]
        self.assertEqual(state["next_cleanup_utc_ms"], self.now[0] + 3_600_000)
        self.assertIsNotNone(self.source.get(row.observation_id))

    def test_unacknowledged_semantic_windows_keep_raw_with_models_disabled_or_pending(self):
        self.fixture_semantic_receipts = False
        row = self.observe()
        service = self.service()
        service.process_once()
        self.now[0] += 1000
        state = self.complete_cycle(service)
        self.assertEqual(state["deleted_observations"], 0)
        self.assertEqual(state["blocked_reason"], "semantic_memory_pending_observations_retained")
        self.assertIsNotNone(self.source.get(row.observation_id))
        self.assertGreater(service._organizer.knowledge.health().item_count, 0)

    def test_mismatched_or_incomplete_semantic_receipts_never_authorize_deletion(self):
        self.fixture_semantic_receipts = False
        row = self.observe()
        service = self.service()
        service.process_once()
        self.acknowledge_semantic(service)
        memory = service._writer.search(kinds=("episodic",))[0]
        key = "semantic_receipt:" + memory["id"].removeprefix("episodic_")
        valid = service._writer.get_state(key)
        for bad in ({"input_hash": "old revision"}, {"source_generation": "old store"},
                    {"scope": "other user"}, {"source_observation_ids": []},
                    {"completed_at_utc_ms": None}):
            with self.subTest(bad=bad):
                service._writer.set_state(key, {**valid, **bad})
                self.now[0] += 1000
                self.complete_cycle(service)
                self.assertIsNotNone(self.source.get(row.observation_id))
        service._writer.set_state(key, valid)
        self.now[0] += 1000
        self.complete_cycle(service)
        self.assertIsNone(self.source.get(row.observation_id))

    def test_semantic_receipt_revoked_after_journal_keeps_raw(self):
        self.fixture_semantic_receipts = False
        row = self.observe()
        service = self.service()
        service.process_once()
        self.acknowledge_semantic(service)
        memory = service._writer.search(kinds=("episodic",))[0]
        key = "semantic_receipt:" + memory["id"].removeprefix("episodic_")
        apply = service._retention._apply_batch
        def revoke_then_apply(state, cycle):
            service._writer.delete_state(key)
            return apply(state, cycle)
        self.now[0] += 1000
        with patch.object(service._retention, "_apply_batch", side_effect=revoke_then_apply):
            self.complete_cycle(service)
        self.assertIsNotNone(self.source.get(row.observation_id))
        self.assertGreater(service._organizer.knowledge.health().item_count, 0)

    def test_schedule_survives_restart_without_immediate_purge(self) -> None:
        row = self.observe()
        first = self.service()
        self.assertEqual(first.process_once()["raw_retention"]["state"], "scheduled")
        first.close()
        self.now[0] += 500
        resumed = self.service()
        self.assertEqual(resumed.process_once()["raw_retention"]["next_cleanup_utc_ms"], 1_801_000)
        self.assertIsNotNone(self.source.get(row.observation_id))
        self.now[0] += 500
        state = self.complete_cycle(resumed)
        self.assertEqual(state["deleted_observations"], 1)
        self.assertIsNone(self.source.get(row.observation_id))

    def test_stable_memory_survives_full_raw_and_both_projection_cleanup(self) -> None:
        row = self.observe(title="full evidence " + "secret paragraph " * 300)
        with ActivityOrganizerService(load_activity_config(self.root, self.activity_path)) as public:
            public.process_once()
            public.knowledge.set_checkpoint("behind", self.source.generation(), 0)
            public.knowledge.set_checkpoint("ahead", self.source.generation(), 999)
        service = self.service()
        service.process_once()
        store = service._writer
        before = store.search(kinds=("episodic",))[0]
        store.put_memory({"id": "user-fact", "kind": "semantic", "status": "active",
                          "provenance": "user_confirmed", "content": "keep explicit user memory"})
        self.now[0] += 1000
        state = self.complete_cycle(service)
        self.assertEqual(state["deleted_observations"], 1)
        self.assertEqual(service._organizer.knowledge.health().item_count, 0)
        with KnowledgeStore(self.public_path, read_only=True) as public:
            self.assertEqual(public.health().item_count, 0)
            self.assertEqual(public.checkpoint("behind")[1], row.ingest_seq)
            self.assertEqual(public.checkpoint("ahead")[1], 999)
        after = store.get_memory(before["id"])
        self.assertEqual((before["content"], before["source_observation_ids"]),
                         (after["content"], after["source_observation_ids"]))
        self.assertIsNotNone(store.get_memory("user-fact"))
        self.assertIsNone(store.get_state("observation_window:" + row.observation_id))
        window = store.get_state("window:" + before["id"].removeprefix("episodic_"))
        self.assertTrue(window["sealed"])
        self.assertEqual(window["item_ids"], [])
        reader = MemoryReader(self.root, config=self.config, clock=lambda: self.now[0])
        read = reader.handle("read", {"id": before["id"]})
        self.assertEqual(read["memory"]["metadata"]["source_retention_status"], "archived")
        self.assertTrue(read["memory"]["metadata"]["references_unavailable"])
        self.assertEqual(service.process_once()["processed"], 0)
        latest = self.observe(observed=self.now[0], title="new active tail")
        self.assertEqual(service.process_once()["processed"], 1)
        self.assertIsNotNone(self.source.get(latest.observation_id))

    def test_continuous_capture_does_not_block_stable_windows_and_keeps_tail(self) -> None:
        old = self.observe()
        current = self.observe(observed=self.now[0], title="unfinished current window")
        future = self.observe(observed=self.now[0] + 600_000, title="future timestamp")
        service = self.service()
        service.process_once()
        self.assertTrue(service._writer.get_state("dirty_windows"))
        self.now[0] += 1000
        self.complete_cycle(service)
        self.assertIsNone(self.source.get(old.observation_id))
        self.assertIsNotNone(self.source.get(current.observation_id))
        self.assertIsNotNone(self.source.get(future.observation_id))

    def test_unresolved_observation_is_retained_without_blocking_other_archive(self) -> None:
        saved = self.observe()
        missing = self.observe(title="projection unavailable")
        service = self.service()
        service._ensure_writer()
        service._ensure_source()
        projection = service._organizer.knowledge
        lookup = projection.get_item_by_observation_id
        def selected(observation_id):
            return None if observation_id == missing.observation_id else lookup(observation_id)
        with patch.object(projection, "get_item_by_observation_id", side_effect=selected):
            service.process_once()
            self.now[0] += 1000
            state = self.complete_cycle(service)
        self.assertEqual(state["state"], "partial")
        self.assertEqual(state["skipped_observations"], 1)
        self.assertIsNone(self.source.get(saved.observation_id))
        self.assertIsNotNone(self.source.get(missing.observation_id))

    def test_late_event_uses_new_window_and_cannot_replace_complete_archived_memory(self) -> None:
        self.observe(title="first complete context")
        service = self.service()
        service.process_once()
        original = service._writer.search(kinds=("episodic",))[0]
        self.now[0] += 1000
        self.complete_cycle(service)
        late = self.observe(observed=2000, title="late independent fragment")
        service.process_once()
        saved = service._writer.get_memory(original["id"])
        self.assertEqual(saved["content"], original["content"])
        self.assertEqual(saved["source_observation_ids"], original["source_observation_ids"])
        memories = service._writer.search(kinds=("episodic",))
        new = [row for row in memories if row["id"] != original["id"]]
        self.assertEqual(len(new), 1)
        self.assertIn(late.observation_id, new[0]["source_observation_ids"])
        self.assertIn("late independent fragment", new[0]["content"])

    def test_semantic_job_must_commit_before_raw_archive(self) -> None:
        self.fixture_semantic_receipts = False
        self.now[0] = int(time.time() * 1000)
        original = self.observe()
        service = self.service(replace(self.config, semantics=SemanticsConfig(enabled=True)))
        service.process_once()
        self.now[0] = int(time.time() * 1000) + 100
        job = service.handle("claim_job")["job"]
        self.now[0] += 1000
        self.complete_cycle(service)
        self.assertIsNotNone(self.source.get(original.observation_id))
        result = service.handle("complete_job", {"job_id": job["job_id"], "lease_token": job["lease_token"],
            "result": {"summary": "observed project context", "source_observation_ids": [original.observation_id],
                       "facts": []}, "usage": {"totalTokens": 20}})
        self.assertEqual(result["status"], "completed")
        # Simulate the final all-parts commit receipt without an external
        # provider, then exercise the real hourly retirement path.
        self.acknowledge_semantic(service)
        self.now[0] += 1000
        self.complete_cycle(service)
        self.assertIsNone(self.source.get(original.observation_id))
        read = service.handle("read", {"id": result["memory_ids"][0]})
        self.assertEqual(read["memory"]["metadata"]["source_retention_status"], "archived")
        self.assertEqual(service.handle("claim_job")["status"], "idle")

    def test_raw_failure_journal_survives_restart_and_exact_batch_retries(self) -> None:
        row = self.observe()
        service = self.service()
        service.process_once()
        original_memory = service._writer.search(kinds=("episodic",))[0]
        self.now[0] += 1000
        with patch.object(ObservationStore, "purge_memorized", side_effect=OSError("temporary raw failure")):
            result = service.process_once()
        self.assertEqual(result["raw_retention"]["state"], "degraded")
        pending = service._writer.get_state("raw_retention")["cycle"]["journal"]
        self.assertEqual(pending["observation_ids"], [row.observation_id])
        self.assertIsNotNone(self.source.get(row.observation_id))
        service.close()
        resumed = self.service()
        self.complete_cycle(resumed)
        self.assertIsNone(self.source.get(row.observation_id))
        self.assertEqual(resumed._writer.get_memory(original_memory["id"])["content"], original_memory["content"])

    def test_durable_memory_barrier_precedes_any_projection_or_raw_purge(self) -> None:
        row = self.observe()
        service = self.service()
        service.process_once()
        self.now[0] += 1000
        store, projection = service._writer, service._organizer.knowledge
        with patch.object(store, "durable_transaction", side_effect=OSError("durability barrier unavailable")), \
                patch.object(projection, "purge_memorized", wraps=projection.purge_memorized) as derived_purge, \
                patch.object(ObservationStore, "purge_memorized") as raw_purge:
            result = service.process_once()
        self.assertEqual(result["raw_retention"]["state"], "degraded")
        derived_purge.assert_not_called()
        raw_purge.assert_not_called()
        self.assertIsNotNone(self.source.get(row.observation_id))
        with patch.object(store, "durable_transaction", wraps=store.durable_transaction) as durable:
            self.complete_cycle(service)
        durable.assert_called_once()
        self.assertIsNone(self.source.get(row.observation_id))

    def test_forget_before_durable_candidate_check_keeps_raw(self) -> None:
        row = self.observe()
        service = self.service()
        service.process_once()
        memory_id = service._writer.search(kinds=("episodic",))[0]["id"]
        self.now[0] += 1000
        barrier = service._writer.durable_transaction
        @contextmanager
        def forget_before_barrier():
            service.handle("forget", {"id": memory_id})
            with barrier():
                yield
        with patch.object(service._writer, "durable_transaction", forget_before_barrier):
            result = service.process_once()
        self.assertEqual(result["raw_retention"]["deleted_observations"], 0)
        self.assertIsNotNone(self.source.get(row.observation_id))
        self.assertEqual(service._organizer.knowledge.health().item_count, 1)

    def test_forget_after_journal_before_apply_cancels_local_stale_batch(self) -> None:
        row = self.observe()
        service = self.service()
        service.process_once()
        memory_id = service._writer.search(kinds=("episodic",))[0]["id"]
        self.now[0] += 1000
        apply = service._retention._apply_batch
        def forget_before_apply(state, cycle):
            service.handle("forget", {"id": memory_id})
            return apply(state, cycle)
        with patch.object(service._retention, "_apply_batch", forget_before_apply):
            service.process_once()
        self.assertIsNotNone(self.source.get(row.observation_id))
        self.assertIsNone(service._writer.get_state("raw_retention"))
        self.assertEqual(service._organizer.knowledge.health().item_count, 1)

    def test_separate_cli_forget_waits_for_raw_commit_fence(self) -> None:
        row = self.observe()
        service = self.service()
        service.process_once()
        memory_id = service._writer.search(kinds=("episodic",))[0]["id"]
        self.now[0] += 1000
        attempted, completed = threading.Event(), threading.Event()
        results, errors = [], []
        threads: list[threading.Thread] = []
        purge = ObservationStore.purge_memorized
        with MemoryStore(self.config.database_path) as cli:
            def forget():
                attempted.set()
                try:
                    results.append(cli.forget(memory_id=memory_id))
                except Exception as error:
                    errors.append(error)
                finally:
                    completed.set()
            def during_purge(writer, **kwargs):
                thread = threading.Thread(target=forget)
                threads.append(thread)
                thread.start()
                self.assertTrue(attempted.wait(1))
                self.assertFalse(completed.wait(0.1))
                self.assertIsNotNone(service._writer.get_memory(memory_id))
                return purge(writer, **kwargs)
            try:
                with patch.object(ObservationStore, "purge_memorized", during_purge):
                    result = service.process_once()
            finally:
                for thread in threads:
                    thread.join(3)
        self.assertFalse(errors)
        self.assertTrue(completed.is_set())
        self.assertEqual(result["raw_retention"]["deleted_observations"], 1)
        self.assertIsNone(self.source.get(row.observation_id))
        self.assertFalse(results[0]["source_data_deleted"])
        self.assertIsNone(service._writer.get_memory(memory_id))

    def test_reentrant_service_forget_cannot_scrub_live_raw_commit(self) -> None:
        row = self.observe()
        service = self.service()
        service.process_once()
        memory_id = service._writer.search(kinds=("episodic",))[0]["id"]
        self.now[0] += 1000
        purge = ObservationStore.purge_memorized
        def reentrant(writer, **kwargs):
            service.handle("forget", {"id": memory_id})
            return purge(writer, **kwargs)
        with patch.object(ObservationStore, "purge_memorized", reentrant):
            result = service.process_once()
        self.assertEqual(result["raw_retention"]["state"], "degraded")
        self.assertIn("retry forget", result["raw_retention"]["error"])
        self.assertIsNone(service._retention.fence_owner_thread_id)
        self.assertIsNotNone(service._writer.get_memory(memory_id))
        self.assertIsNotNone(self.source.get(row.observation_id))
        self.assertFalse(service.handle("forget", {"id": memory_id})["source_data_deleted"])
        self.assertIsNotNone(self.source.get(row.observation_id))

    def test_more_than_500_sources_continues_past_shared_projection(self) -> None:
        rows = [self.observe(observed=1000 + index) for index in range(510)]
        ids = tuple(row.observation_id for row in rows)
        with ActivityOrganizerService(load_activity_config(self.root, self.activity_path)) as public:
            while public.process_once(limit=500)["lag"]:
                pass
            item = public.knowledge.get_item_by_observation_id(ids[0])
            public.knowledge.upsert_item(replace(item, item_id="shared-full-text", source_observation_ids=ids),
                Evidence("shared-evidence", ids, 1000, None, "text/plain", text="shared complete source text"))
        service = self.service()
        service.process_once()
        service.process_once()
        self.now[0] += 1000
        first = service.process_once()
        self.assertEqual(first["raw_retention"]["deleted_observations"], 500)
        self.assertEqual(self.source.observation_statistics()["observation_count"], 10)
        window_id = service._writer.search(kinds=("episodic",))[0]["id"].removeprefix("episodic_")
        self.assertTrue(service._writer.get_state("window:" + window_id)["item_ids"])
        state = self.complete_cycle(service)
        self.assertEqual(state["deleted_observations"], 510)
        self.assertEqual(state["retained_shared_items"], 0)
        with KnowledgeStore(self.public_path, read_only=True) as public:
            self.assertIsNone(public.get_item("shared-full-text"))

    def test_public_knowledge_alias_is_rejected_without_touching_permanent_memory(self) -> None:
        self.observe()
        service = self.service()
        service.process_once()
        self.activity_path.write_text("enabled: false\nstorage:\n  directory: data raw\n"
            "  database_path: data raw/observations.sqlite3\n  knowledge_database_path: 永久 memory.sqlite3\n", encoding="utf-8")
        self.now[0] += 1000
        result = service.process_once()
        self.assertEqual(result["raw_retention"]["state"], "degraded")
        self.assertIn("independent", result["raw_retention"]["error"])
        with closing(sqlite3.connect(self.config.database_path)) as connection:
            self.assertIsNone(connection.execute("SELECT name FROM sqlite_master WHERE name='knowledge_meta'").fetchone())
        self.assertEqual(self.source.observation_statistics()["observation_count"], 1)

    def test_unrelated_public_database_is_checked_before_migration(self) -> None:
        self.observe()
        with closing(sqlite3.connect(self.public_path)) as connection:
            connection.execute("CREATE TABLE unrelated(value TEXT)")
            connection.commit()
        before = hashlib.sha256(self.public_path.read_bytes()).hexdigest()
        service = self.service()
        service.process_once()
        self.now[0] += 1000
        result = service.process_once()
        self.assertEqual(result["raw_retention"]["state"], "degraded")
        self.assertEqual(hashlib.sha256(self.public_path.read_bytes()).hexdigest(), before)
        self.assertEqual(self.source.observation_statistics()["observation_count"], 1)

    def test_pending_source_generation_change_refuses_new_source(self) -> None:
        row = self.observe()
        service = self.service()
        service.process_once()
        self.now[0] += 1000
        with patch.object(ObservationStore, "purge_memorized", side_effect=OSError("pending")):
            service.process_once()
        previous_generation = self.source.generation()
        self.source.close()
        replacement_path = self.root / "replacement.sqlite3"
        with ObservationStore(replacement_path, asset_directory=self.source_path.parent / "assets") as replacement:
            fresh = replacement.append(Observation.create(kind="window_context", source_id="new-source",
                source_epoch="new-epoch", source_seq=1, boot_id="new-boot", observed_at_utc_ms=600_000,
                app_id="editor.exe", window_instance_id="new-window", payload={"window_title": "replacement source"}))
        replacement_path.resolve().relative_to(self.root.resolve())
        self.source_path.resolve().relative_to(self.root.resolve())
        os.replace(replacement_path, self.source_path)
        self.source = ObservationStore(self.source_path, asset_directory=self.source_path.parent / "assets")
        state = service._retention.step()
        self.assertEqual(state["blocked_reason"], "source_generation_changed")
        self.assertNotIn("cycle", service._writer.get_state("raw_retention"))
        abandoned = service._writer.get_state("raw_retention_abandoned")
        self.assertEqual(abandoned["generation"], previous_generation)
        self.assertEqual(abandoned["observation_ids"], [row.observation_id])
        self.assertIsNotNone(self.source.get(fresh.observation_id))
        service.process_once()
        self.now[0] += 1000
        calls: list[dict] = []
        purge = ObservationStore.purge_memorized
        def record_call(writer, **kwargs):
            calls.append(kwargs)
            return purge(writer, **kwargs)
        with patch.object(ObservationStore, "purge_memorized", record_call):
            self.complete_cycle(service)
        self.assertIsNone(self.source.get(fresh.observation_id))
        self.assertTrue(calls)
        self.assertTrue(all(call["store_generation"] == self.source.generation() for call in calls))
        self.assertTrue(all(row.observation_id not in call["observation_ids"] for call in calls))

    def test_replaced_source_rebuilds_private_projection_and_preserves_permanent_memory(self) -> None:
        old = self.observe(title="original source memory")
        service = self.service()
        service.process_once()
        original = service._writer.search(kinds=("episodic",))[0]
        old_generation = self.source.generation()
        with closing(sqlite3.connect(self.source_path)) as connection:
            connection.execute("UPDATE schema_meta SET value='replacement-generation' WHERE key='store_generation'")
            connection.commit()
        new = self.observe(observed=600_000, title="replacement source tail")
        first, second = service.process_once(), service.process_once()
        self.assertEqual(first["status"], "ready")
        self.assertEqual(second["processed"], 0)
        self.assertEqual(first["coverage"]["source_generation"], "replacement-generation")
        self.assertEqual(service._organizer.knowledge.checkpoint(service._organizer.consumer_id),
                         ("replacement-generation", new.ingest_seq))
        self.assertEqual(service._writer.get_memory(original["id"])["content"], original["content"])
        memories = service._writer.search(kinds=("episodic",))
        self.assertTrue(any(new.observation_id in row["source_observation_ids"] for row in memories))
        self.assertEqual(service._writer.get_memory(original["id"])["metadata"]["source_generation"], old_generation)
        self.assertTrue(old.observation_id)

    def test_pending_asset_retry_keeps_real_counts_and_reclaims_screenshots(self) -> None:
        first, failed_file, first_size = self.screenshot()
        second, removed_file, second_size = self.screenshot(observed=2000)
        service = self.service()
        service.process_once()
        self.now[0] += 1000
        original_unlink = Path.unlink
        def fail_one(path, *args, **kwargs):
            if path.resolve() == failed_file.resolve():
                raise PermissionError("temporary screenshot lock")
            return original_unlink(path, *args, **kwargs)
        with patch.object(Path, "unlink", fail_one):
            result = service.process_once()
        state = result["raw_retention"]
        self.assertEqual(state["blocked_reason"], "asset_cleanup_pending")
        self.assertEqual(state["deleted_observations"], 2)
        self.assertEqual(state["deleted_assets"], 1)
        self.assertGreaterEqual(state["reclaimed_bytes"], second_size)
        self.assertTrue(failed_file.is_file())
        self.assertFalse(removed_file.exists())
        self.assertIsNone(self.source.get(first.observation_id))
        self.assertIsNone(self.source.get(second.observation_id))
        service.close()
        resumed = self.service()
        state = self.complete_cycle(resumed)
        self.assertEqual(state["deleted_observations"], 2)
        self.assertEqual(state["deleted_assets"], 2)
        self.assertGreaterEqual(state["reclaimed_bytes"], first_size + second_size)
        self.assertFalse(failed_file.exists())

    def test_public_knowledge_hardlink_to_memory_is_rejected_before_writer_open(self) -> None:
        self.observe()
        service = self.service()
        service.process_once()
        os.link(self.config.database_path, self.public_path)
        self.now[0] += 1000
        result = service.process_once()
        self.assertEqual(result["raw_retention"]["state"], "degraded")
        self.assertIn("independent", result["raw_retention"]["error"])
        self.assertEqual(self.source.observation_statistics()["observation_count"], 1)
        with closing(sqlite3.connect(self.config.database_path)) as connection:
            self.assertIsNone(connection.execute("SELECT name FROM sqlite_master WHERE name='knowledge_meta'").fetchone())

    def test_concurrent_late_ingest_is_outside_frozen_cleanup_checkpoint(self) -> None:
        old = self.observe()
        service = self.service()
        service.process_once()
        self.now[0] += 1000
        purge = ObservationStore.purge_memorized
        inserted: list[Observation] = []
        def during_purge(writer, **kwargs):
            if not inserted:
                inserted.append(self.observe(observed=1500, title="concurrent late evidence"))
            return purge(writer, **kwargs)
        with patch.object(ObservationStore, "purge_memorized", during_purge):
            service.process_once()
        state = self.complete_cycle(service)
        self.assertEqual(state["deleted_observations"], 1)
        self.assertIsNone(self.source.get(old.observation_id))
        self.assertIsNotNone(self.source.get(inserted[0].observation_id))
        self.assertTrue(any(inserted[0].observation_id in row["source_observation_ids"]
                            for row in service._writer.search(kinds=("episodic",))))

    def test_raw_return_failure_preserves_exact_journal_after_actual_deletion(self) -> None:
        old = self.observe()
        service = self.service()
        service.process_once()
        memory = service._writer.search(kinds=("episodic",))[0]
        self.now[0] += 1000
        purge = ObservationStore.purge_memorized
        def fail_after_commit(writer, **kwargs):
            purge(writer, **kwargs)
            raise OSError("raw result delivery failed after commit")
        with patch.object(ObservationStore, "purge_memorized", fail_after_commit):
            state = service.process_once()["raw_retention"]
        self.assertEqual(state["state"], "degraded")
        self.assertIsNone(self.source.get(old.observation_id))
        read = service.handle("read", {"id": memory["id"]})
        self.assertEqual(read["memory"]["metadata"]["source_retention_status"], "archiving")
        self.assertTrue(read["memory"]["metadata"]["references_unavailable"])
        service.close()
        resumed = self.service()
        final = self.complete_cycle(resumed)
        self.assertEqual(final["deleted_observations"], 1)
        self.assertEqual(resumed.handle("read", {"id": memory["id"]})["memory"]["metadata"]["source_retention_status"], "archived")

    def test_disabled_retention_keeps_raw_and_read_only_health_never_initializes_schedule(self) -> None:
        row = self.observe()
        config = replace(self.config, raw_retention=RawRetentionConfig(enabled=False, interval_seconds=1))
        reader = MemoryReader(self.root, config=config)
        self.assertEqual(reader.handle("health")["raw_retention"]["state"], "disabled")
        self.assertFalse(config.database_path.exists())
        service = self.service(config)
        service.process_once()
        self.now[0] += 2_000_000
        self.assertEqual(service.process_once()["raw_retention"]["state"], "disabled")
        self.assertIsNotNone(self.source.get(row.observation_id))

    def test_compaction_deferred_is_retried_without_finishing_cycle(self) -> None:
        self.observe()
        service = self.service()
        service.process_once()
        self.now[0] += 1000
        service.process_once()
        projection = service._organizer.knowledge
        with patch.object(projection, "compact_memorized", return_value={
                "compaction_deferred": True, "reclaimed_bytes": 0}):
            result = service.process_once()
        self.assertEqual(result["raw_retention"]["blocked_reason"], "projection_compaction_pending")
        self.assertIn("cycle", service._writer.get_state("raw_retention"))
        self.assertNotIn("private", service._writer.get_state("raw_retention")["cycle"].get("compacted", []))
        self.assertEqual(self.complete_cycle(service)["state"], "scheduled")

    def test_custom_root_and_working_directory_keep_all_writes_in_selected_profile(self) -> None:
        self.observe()
        original_cwd = Path.cwd()
        service = self.service()
        try:
            os.chdir(self.root.parent)
            service.process_once()
            self.now[0] += 1000
            self.complete_cycle(service)
        finally:
            os.chdir(original_cwd)
        self.assertTrue(self.config.database_path.is_file())
        self.assertFalse((self.root / "config" / "mirally" / "activity.yaml").exists())
        self.assertEqual(self.source.observation_statistics()["observation_count"], 0)


if __name__ == "__main__":
    unittest.main()
