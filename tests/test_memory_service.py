"""Functional regressions for background maintenance and bounded recall."""

from __future__ import annotations

import hashlib
import json
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from mirally.core.activity import Observation, ObservationStore
from mirally.core.memory import MemoryStore
from mirally.runtime.memory import MemoryConfig, MemoryReader, MemoryService, SemanticsConfig, load_memory_config


class MemoryServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.source_path = self.root / "observations.sqlite3"
        self.activity_path = self.root / "activity.yaml"
        self.activity_path.write_text("enabled: false\nstorage:\n  database_path: " +
                                      self.source_path.as_posix() + "\n", encoding="utf-8")
        self.config = MemoryConfig(True, self.root / "memory.sqlite3", self.activity_path,
                                   self.root / "projection.sqlite3", poll_interval_seconds=0.05)
        self.source: ObservationStore | None = None
        self.services: list[MemoryService] = []
        self.sequence = 0

    def tearDown(self) -> None:
        for service in reversed(self.services):
            service.close()
        if self.source:
            self.source.close()
        self.directory.cleanup()

    def writer(self) -> ObservationStore:
        if self.source is None:
            self.source = ObservationStore(self.source_path)
        return self.source

    def observe(self, kind: str = "window_context", *, observed: int = 1000,
                app: str = "code.exe", window: str = "editor", payload: dict | None = None,
                **kwargs) -> Observation:
        self.sequence += 1
        row = Observation.create(kind=kind, source_id="test", source_epoch="epoch",
            source_seq=self.sequence, boot_id="boot", observed_at_utc_ms=observed,
            app_id=app, window_instance_id=window,
            payload=payload if payload is not None else {"window_title": "mirally memory.py"}, **kwargs)
        return self.writer().append(row)

    def service(self, config: MemoryConfig | None = None, **kwargs) -> MemoryService:
        service = MemoryService(self.root, config=config or self.config, **kwargs)
        self.services.append(service)
        return service

    def test_local_memory_defaults_on_but_model_calls_remain_opt_in(self) -> None:
        with patch("mirally.runtime.memory.config.default_data_directory",
                   return_value=self.root / "user/activity"):
            default = load_memory_config(self.root)
        self.assertTrue(default.enabled)
        self.assertFalse(default.semantics.enabled)
        self.assertTrue(default.raw_retention.enabled)
        self.assertEqual(default.raw_retention.interval_seconds, 3600)
        profile = self.root / "profile.yaml"
        profile.write_text("context_token_budget: 512\n", encoding="utf-8")
        self.assertTrue(load_memory_config(self.root, profile).enabled)
        profile.write_text("enabled: false\n", encoding="utf-8")
        self.assertFalse(load_memory_config(self.root, profile).enabled)
        # Configuration reads never start maintenance or create stores.
        self.assertFalse(default.database_path.exists())
        self.assertFalse(default.projection_database_path.exists())

    def test_config_boolean_strings_and_storage_aliases_rejected(self) -> None:
        bad = self.root / "bad.yaml"
        bad.write_text('enabled: "false"\n', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "boolean"):
            load_memory_config(self.root, bad)
        bad.write_text("enabled: true\nstorage:\n  database_path: same.sqlite3\n  projection_database_path: same.sqlite3\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "different databases"):
            load_memory_config(self.root, bad)

    def test_read_only_missing_store_creates_no_database_or_projection(self) -> None:
        reader = MemoryReader(self.root, config=self.config)
        self.assertEqual(reader.handle("health")["status"], "not_ready")
        self.assertEqual(reader.handle("context", {"query": "继续刚才的工作"})["memories"], [])
        self.assertFalse(self.config.database_path.exists())
        self.assertFalse(self.config.projection_database_path.exists())
        self.assertFalse(self.source_path.exists())

    def test_all_reader_operations_separate_durable_history_from_remaining_raw_source(self) -> None:
        with MemoryStore(self.config.database_path) as store:
            store.put_memory({"id": "old", "kind": "episodic", "title": "Old activity",
                "content": "Historical project", "observed_start_utc_ms": 100,
                "observed_end_utc_ms": 200})
            store.set_state("coverage", {"source_oldest_utc_ms": 800, "source_newest_utc_ms": 900})
            store.set_state("memory_archival:old", {"source_retention_status": "archived"})
        reader = MemoryReader(self.root, config=self.config, clock=lambda: 1000)
        for operation, args in [("health", {}), ("read", {"memory_id": "old"}),
                                ("search", {}), ("context", {})]:
            with self.subTest(operation=operation):
                result = reader.handle(operation, args)
                coverage = result["coverage"]
                self.assertEqual(coverage["source_oldest_utc_ms"], 800)
                self.assertEqual(coverage["durable_coverage"]["observed_start_utc_ms"], 100)
                self.assertEqual(coverage["durable_coverage"]["observed_end_utc_ms"], 200)
                self.assertIn("raw Activity only", coverage["source_coverage_note"])
                self.assertFalse(coverage["maintenance_on_query"])
                if operation == "health":
                    self.assertEqual(result["store"]["durable_coverage"], coverage["durable_coverage"])
        self.assertFalse(self.source_path.exists())
        self.assertFalse(self.config.projection_database_path.exists())

    def test_background_generates_meaningful_memory_without_dialogue_or_collector(self) -> None:
        first = self.observe(payload={"window_title": "mirally memory.py"})
        second = self.observe(observed=2000, app="msedge.exe", window="browser",
                              payload={"window_title": "SQLite indexing docs", "browser_url": "https://sqlite.org/fts5.html"})
        service = self.service()
        service.start()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if service.state == "ready":
                break
            time.sleep(0.02)
        with MemoryStore(self.config.database_path, read_only=True) as store:
            memories = store.search(kinds=("episodic",))
            self.assertEqual(len(memories), 1)
            content = memories[0]["content"]
            self.assertIn("memory.py", content)
            self.assertIn("SQLite indexing docs", content)
            self.assertIn("https://sqlite.org", content)
            self.assertIn("do not confirm", content)
            self.assertEqual(set(memories[0]["source_observation_ids"]), {first.observation_id, second.observation_id})
            self.assertEqual(memories[0]["scope"], "user/local")
            self.assertTrue(memories[0]["evidence_ids"])
        self.assertFalse(service.handle("health")["collector_started"])

    def test_restart_uses_ingest_checkpoint_and_preserves_source_hash(self) -> None:
        self.observe()
        service = self.service()
        self.assertEqual(service.process_once()["processed"], 1)
        memory = service.handle("search")["memories"][0]
        service.close()
        self.writer().close()
        self.source = None
        before = hashlib.sha256(self.source_path.read_bytes()).hexdigest()
        resumed = self.service()
        self.assertEqual(resumed.process_once()["processed"], 0)
        result = resumed.handle("search")["memories"][0]
        self.assertEqual((memory["id"], memory["revision"]), (result["id"], result["revision"]))
        resumed.close()
        self.assertEqual(hashlib.sha256(self.source_path.read_bytes()).hexdigest(), before)

    def test_late_ocr_updates_original_window_and_observed_ttl(self) -> None:
        original = self.observe("content_snapshot", observed=1000,
                                payload={"text": "original frame", "window_title": "oldpage"})
        service = self.service()
        service.process_once()
        first = service.handle("search")["memories"][0]
        self.observe("ocr_result", observed=900000, app="other.exe", window="unrelated",
                     payload={"text": "late indexing result"}, caused_by=(original.observation_id,))
        service.process_once()
        rows = service.handle("search")["memories"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], first["id"])
        self.assertIn("late indexing result", rows[0]["content"])
        self.assertEqual(rows[0]["observed_end_utc_ms"], 1000)
        self.assertGreater(rows[0]["revision"], first["revision"])
        self.assertEqual(service.handle("context", {"query": "继续刚才的工作"})["text"], "")

    def test_unrelated_context_is_empty_and_historical_working_ttl_is_not_refreshed(self) -> None:
        self.observe()
        service = self.service()
        service.process_once()
        self.assertEqual(service.handle("context", {"query": "一小时有多少分钟？"})["memories"], [])
        self.assertEqual(service.handle("context", {"query": "继续刚才的工作"})["memories"], [])
        related = service.handle("context", {"query": "memory.py", "token_budget": 512})
        self.assertTrue(related["memories"])
        self.assertIn("1970-01-01", related["text"])
        self.assertLessEqual(related["estimated_tokens_upper_bound"], 512)

    def test_missing_source_recovers_when_database_appears(self) -> None:
        service = self.service()
        self.assertEqual(service.process_once()["status"], "not_ready")
        self.assertFalse(self.source_path.exists())
        self.observe()
        self.assertEqual(service.process_once()["status"], "ready")
        self.assertTrue(service.handle("search")["memories"])

    def test_forget_tombstone_prevents_late_revision_and_restart_resurrection(self) -> None:
        row = self.observe()
        service = self.service()
        service.process_once()
        record = service.handle("search")["memories"][0]
        service.handle("forget", {"id": record["id"]})
        self.observe("input_text", observed=2000, payload={"inserted_text": "new content"})
        service.process_once()
        self.assertEqual(service.handle("search")["memories"], [])
        service.close()
        resumed = self.service()
        resumed.process_once()
        self.assertEqual(resumed.handle("search")["memories"], [])
        self.assertTrue(row.observation_id)

    def test_projection_rebuild_preserves_user_confirmed_memory(self) -> None:
        self.observe()
        service = self.service()
        service.process_once()
        assert service._writer is not None and service._organizer is not None
        service._writer.put_memory({"id": "confirmed", "kind": "semantic", "content": "Project release is Friday",
                                   "provenance": "user_confirmed", "status": "active"})
        service._organizer.rebuild()
        service.process_once()
        self.assertEqual(service.handle("read", {"id": "confirmed"})["text"], "Project release is Friday")

    def test_read_queries_do_not_wait_for_maintenance_operation_lock(self) -> None:
        self.observe()
        service = self.service()
        service.process_once()
        entered, release = threading.Event(), threading.Event()
        def slow_maintenance() -> None:
            with service._maintenance_lock:
                entered.set()
                release.wait(3)
        thread = threading.Thread(target=slow_maintenance)
        thread.start()
        entered.wait(1)
        try:
            before = time.monotonic()
            self.assertTrue(service.handle("search")["memories"])
            self.assertLess(time.monotonic() - before, 0.5)
        finally:
            release.set()
            thread.join()

    def test_only_one_writer_for_same_database(self) -> None:
        self.observe()
        first, second = self.service(), self.service()
        self.assertEqual(first.process_once()["status"], "ready")
        self.assertEqual(second.process_once()["status"], "other_writer")
        self.assertTrue(second.handle("search")["memories"])
        first.close()
        self.assertEqual(second.process_once()["status"], "ready")

    def test_semantics_requires_evidence_and_keeps_facts_as_candidates(self) -> None:
        original = self.observe()
        config = replace(self.config, semantics=SemanticsConfig(enabled=True))
        service = self.service(config)
        service.process_once()
        claimed = service.handle("claim_job")
        self.assertEqual(claimed["status"], "ready")
        job = claimed["job"]
        refs = [original.observation_id]
        result = {"summary": "An editor displayed mirally memory.py", "source_observation_ids": refs,
                  "facts": [{"statement": "Possible project memory module", "source_observation_ids": refs, "confidence": 0.6}]}
        invalid = {**result, "source_observation_ids": ["unseen-observation"]}
        with self.assertRaisesRegex(ValueError, "subset"):
            service.handle("complete_job", {"job_id": job["job_id"], "lease_token": job["lease_token"], "result": invalid})
        completed = service.handle("complete_job", {"job_id": job["job_id"], "lease_token": job["lease_token"],
            "result": {"text": json.dumps(result), "usage": {"input_tokens": 100, "output_tokens": 50},
                       "provider": "host", "model": "host-model"}})
        self.assertTrue(completed["ok"])
        with MemoryStore(config.database_path, read_only=True) as store:
            records = store.search(include_candidates=True)
            candidates = [record for record in records if record["status"] == "candidate"]
            self.assertEqual(len(candidates), 1)
            self.assertEqual(candidates[0]["provenance"], "model_inference")
            self.assertEqual(store.usage_summary()["tokens"], 150)
        self.assertFalse(any(record["status"] == "candidate" for record in service.handle("search")["memories"]))
        self.assertEqual(service.handle("claim_job")["status"], "idle")
        service.process_once()
        self.assertEqual(service.handle("claim_job")["status"], "idle")

    def test_semantic_budget_survives_restart(self) -> None:
        self.observe(observed=1000)
        self.observe(observed=600000, payload={"window_title": "second window"})
        config = replace(self.config, semantics=SemanticsConfig(enabled=True, max_calls_per_hour=1))
        service = self.service(config)
        service.process_once()
        job = service.handle("claim_job")["job"]
        service.handle("fail_job", {"job_id": job["job_id"], "lease_token": job["lease_token"], "error": "network failed"})
        service.close()
        resumed = self.service(config)
        self.assertEqual(resumed.handle("claim_job")["status"], "budget_exhausted")

    def test_legacy_queued_synthesis_clears_source_prompt_after_completion(self) -> None:
        original = self.observe()
        service = self.service()
        service.process_once()
        record = service._writer.search(kinds=("episodic",))[0]
        service.config = replace(self.config, semantics=SemanticsConfig(enabled=True))
        payload = {"system_prompt": "legacy instructions", "prompt": "legacy original text",
                   "memory_id": record["id"], "input_hash": record["metadata"]["input_hash"],
                   "source_observation_ids": [original.observation_id], "scope": record["scope"],
                   "source_generation": record["metadata"]["source_generation"],
                   "observed_start_utc_ms": record["observed_start_utc_ms"],
                   "observed_end_utc_ms": record["observed_end_utc_ms"], "max_tokens": 1500}
        service._writer.enqueue_job("legacy-job", payload)
        job = service.handle("claim_job")["job"]
        completed = service.handle("complete_job", {"job_id": job["job_id"], "lease_token": job["lease_token"],
            "result": {"summary": "Observed editor context", "source_observation_ids": [original.observation_id], "facts": []},
            "usage": {"tokens": 10}})
        self.assertEqual(completed["status"], "completed")
        stored = service._writer.get_job("legacy-job")["payload"]
        self.assertNotIn("prompt", stored)
        self.assertNotIn("system_prompt", stored)
        self.assertIsNone(service._writer.get_state("semantic_receipt:" + record["metadata"]["window_id"]))

    def test_upgrade_rebuilds_pending_window_before_full_evidence_synthesis(self) -> None:
        self.observe()
        service = self.service()
        service.process_once()
        record = service._writer.search(kinds=("episodic",))[0]
        window_id = record["metadata"]["window_id"]
        old_metadata = {key: value for key, value in record["metadata"].items() if key not in {"input_hash", "window_id"}}
        service._writer.put_memory({**record, "metadata": old_metadata})
        state = service._writer.get_state("window:" + window_id)
        service._writer.set_state("dirty_windows", [window_id])
        service._writer.set_state("dirty_window_priorities", {window_id: state["window"]})
        service.config = replace(self.config, semantics=SemanticsConfig(enabled=True))
        self.assertEqual(service.process_once()["status"], "ready")
        job = service.handle("claim_job")["job"]
        current = service._writer.get_memory(record["id"])
        self.assertEqual(job["payload"]["windows"][0]["input_hash"], current["metadata"]["input_hash"])
        self.assertEqual(current["metadata"]["window_id"], window_id)

    def test_expired_source_marks_references_unavailable_without_deleting_memory(self) -> None:
        self.observe()
        now = [int(time.time() * 1000)]
        service = self.service(clock=lambda: now[0])
        service.process_once()
        record = service.handle("search")["memories"][0]
        self.writer().cleanup(now_utc_ms=now[0], observation_retention_days=1)
        now[0] += 61_000
        service.process_once()
        updated = service.handle("read", {"id": record["id"]})["memory"]
        self.assertTrue(updated["metadata"]["references_unavailable"])

    def test_retention_advancing_ingest_floor_recovers_without_resetting_memory(self) -> None:
        for index in range(3):
            self.observe(observed=1000 + index)
        service = self.service(replace(self.config, batch_size=1))
        service.process_once()
        assert service._writer is not None
        service._writer.put_memory({"id": "confirmed", "kind": "semantic", "status": "active",
                                   "provenance": "user_confirmed", "content": "explicitly confirmed"})
        now = int(time.time() * 1000)
        latest = self.observe(observed=now, payload={"window_title": "current surviving evidence"})
        self.writer().cleanup(now_utc_ms=now, observation_retention_days=1)
        result = service.process_once()
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["coverage"]["memory_ingest_seq"], latest.ingest_seq)
        self.assertEqual(result["coverage"]["retention_gap"]["oldest_available_ingest_seq"], latest.ingest_seq)
        self.assertIsNotNone(service._writer.get_memory("confirmed"))
        self.assertEqual(service.process_once()["processed"], 0)

    def test_native_camel_case_usage_and_nonfinite_values(self) -> None:
        self.assertEqual(MemoryService._usage({"inputTokens": 100, "outputTokens": 50, "totalTokens": 150, "costUsd": 0.02}),
                         {"requests": 1, "input_tokens": 100, "output_tokens": 50, "tokens": 150, "cost_usd": 0.02})
        self.assertIsNone(MemoryService._usage({}))
        with self.assertRaisesRegex(ValueError, "finite"):
            MemoryService._usage({"totalTokens": float("nan")})

    def test_inflight_old_synthesis_cannot_overwrite_revised_evidence(self) -> None:
        original = self.observe()
        config = replace(self.config, semantics=SemanticsConfig(enabled=True))
        service = self.service(config)
        service.process_once()
        old_job = service.handle("claim_job")["job"]
        self.observe("input_text", observed=2000, payload={"inserted_text": "late changed evidence"})
        service.process_once()
        stale = {"summary": "old evidence summary", "source_observation_ids": [original.observation_id], "facts": []}
        result = service.handle("complete_job", {"job_id": old_job["job_id"], "lease_token": old_job["lease_token"],
                    "result": stale, "usage": {"totalTokens": 50}})
        self.assertEqual(result["status"], "stale_source_revision")
        self.assertFalse(any(row["content"] == stale["summary"] for row in service.handle("search")["memories"]))
        self.assertEqual(service.handle("claim_job")["status"], "ready")

    def test_new_source_revision_refreshes_summary_but_user_supersession_stays_effective(self) -> None:
        self.observe()
        config = replace(self.config, semantics=SemanticsConfig(enabled=True))
        service = self.service(config)
        service.process_once()

        def complete(job, text):
            return service.handle("complete_job", {"job_id": job["job_id"], "lease_token": job["lease_token"],
                "result": {"summary": text, "source_observation_ids": job["payload"]["source_observation_ids"][:1], "facts": []},
                "usage": {"tokens": 10}})

        first = service.handle("claim_job")["job"]
        self.assertTrue(complete(first, "first semantic summary")["ok"])
        identifier = "synthesis_" + first["payload"]["memory_id"]
        assert service._writer is not None
        self.observe("input_text", observed=2000, payload={"inserted_text": "changed source"})
        service.process_once()
        self.assertEqual(service._writer.get_memory(identifier, include_inactive=True)["status"], "superseded")
        current = service.handle("claim_job")["job"]
        self.assertTrue(complete(current, "updated semantic summary")["ok"])
        refreshed = service._writer.get_memory(identifier)
        self.assertIsNotNone(refreshed)
        self.assertEqual(refreshed["content"], "updated semantic summary")
        self.assertTrue(service.handle("search", {"query": "updated semantic summary"})["memories"])
        self.assertEqual(complete(first, "late old summary")["status"], "stale_lease")
        service._writer.put_memory({"id": "user-correction", "kind": "semantic", "content": "User rejected the inference",
            "provenance": "user_confirmed", "supersedes": [identifier]})
        self.observe("input_text", observed=3000, payload={"inserted_text": "another changed source"})
        service.process_once()
        newer = service.handle("claim_job")["job"]
        self.assertTrue(complete(newer, "inference after explicit rejection")["ok"])
        rejected = service._writer.get_memory(identifier, include_inactive=True)
        self.assertEqual(rejected["status"], "superseded")
        self.assertEqual(rejected["superseded_by"], "user-correction")
        self.assertNotEqual(rejected["content"], "inference after explicit rejection")

    def test_failed_result_validation_settles_reported_usage_without_freeing_unknown_output(self) -> None:
        self.observe()
        config = replace(self.config, semantics=SemanticsConfig(enabled=True))
        service = self.service(config)
        service.process_once()
        first = service.handle("claim_job")["job"]
        assert service._writer is not None
        reserved = service._writer.usage_summary()["tokens"]
        with self.assertRaises(ValueError):
            service.handle("complete_job", {"job_id": first["job_id"], "lease_token": first["lease_token"],
                "result": {"wrong": "schema"}})
        service.handle("fail_job", {"job_id": first["job_id"], "lease_token": first["lease_token"],
            "error": "invalid result", "usage": {"inputTokens": 30}})
        self.assertEqual(service._writer.usage_summary()["tokens"], reserved)
        self.assertEqual(service._writer.usage_summary()["input_tokens"], 30)
        self.assertEqual(MemoryService._usage({"inputTokens": 30}), {"requests": 1, "input_tokens": 30})

    def test_oldest_window_settles_before_more_than_100_historical_windows(self) -> None:
        for index in range(100):
            self.observe(observed=index * 300_000 + 1000)
        recent = int(time.time() * 1000) - 600_000
        self.observe(observed=recent, payload={"window_title": "today's project"})
        config = replace(self.config, semantics=SemanticsConfig(enabled=True, max_calls_per_hour=1))
        service = self.service(config)
        service.process_once()
        job = service.handle("claim_job")["job"]
        self.assertEqual(job["payload"]["observed_end_utc_ms"], 1000)
        assert service._writer is not None
        # Exactly 100 pending windows settle; the recent remainder stays durable.
        self.assertEqual(len(service._writer.get_state("dirty_windows")), 1)
        self.assertEqual(len(service._writer.get_state("dirty_window_priorities")), 1)
        service.close()
        resumed = self.service(config)
        resumed.process_once()
        self.assertEqual(resumed._writer.get_state("dirty_windows"), [])
        self.assertEqual(resumed._writer.get_state("dirty_window_priorities"), {})

    def test_context_source_headers_and_explicit_search_budget(self) -> None:
        original = self.observe()
        service = self.service()
        service.process_once()
        context = service.handle("context", {"query": "memory.py", "token_budget": 512})
        self.assertIn(original.observation_id, context["text"])
        response = service.handle("search", {"token_budget": 1500})
        self.assertLessEqual(sum(len(json.dumps(row, ensure_ascii=False).encode("utf-8")) for row in response["memories"]), 1500)
        assert service._writer is not None
        service._writer.put_memory({"id": "possible", "kind": "semantic", "status": "candidate", "content": "possible fact"})
        self.assertFalse(any(row["id"] == "possible" for row in service.handle("search")["memories"]))
        self.assertTrue(any(row["id"] == "possible" for row in service.handle("search", {"include_candidates": True})["memories"]))

    def test_large_recall_preserves_results_under_escaped_json_and_reference_budgets(self) -> None:
        self.observe()
        service = self.service()
        service.process_once()
        assert service._writer is not None
        service._writer.put_memory({"id": "large", "kind": "episodic", "provenance": "observed",
            "title": "large recall", "content": ('line with \"quotes\"\n' * 1000),
            "source_observation_ids": ["observation-" + str(index).zfill(36) for index in range(200)],
            "evidence_ids": ["evidence-" + str(index).zfill(36) for index in range(200)],
            "metadata": {"tags": ["very long tag " * 100] * 100, "evidence_samples": ["large private payload"] * 1000}})
        for budget in (1500, 2000, 6000):
            result = service.handle("search", {"query": "large recall", "token_budget": budget})
            self.assertEqual(result["status"], "ready")
            self.assertEqual(result["memories"][0]["id"], "large")
            record = result["memories"][0]
            self.assertTrue(record["content"])
            self.assertTrue(record["source_observation_ids"])
            self.assertLessEqual(len(json.dumps(record, ensure_ascii=False).encode("utf-8")), budget)
            self.assertNotIn("evidence_samples", record["metadata"])

    def test_unknown_observation_time_is_not_presented_as_epoch(self) -> None:
        self.observe()
        service = self.service()
        service.process_once()
        assert service._writer is not None
        service._writer.put_memory({"id": "undated", "kind": "semantic", "status": "active",
                                   "provenance": "user_confirmed", "content": "specific undated fact"})
        result = service.handle("context", {"query": "specific undated fact"})
        self.assertIn("time unknown", result["text"])
        self.assertNotIn("1970", result["text"])

    def test_storage_alias_is_rejected_before_source_is_modified(self) -> None:
        self.observe()
        before = hashlib.sha256(self.source_path.read_bytes()).hexdigest()
        service = self.service(replace(self.config, database_path=self.source_path))
        with self.assertRaisesRegex(ValueError, "independent"):
            service.process_once()
        self.assertEqual(hashlib.sha256(self.source_path.read_bytes()).hexdigest(), before)
        self.assertFalse(self.config.projection_database_path.exists())

    def test_more_than_500_unresolved_observations_remain_in_durable_pages(self) -> None:
        for index in range(510):
            self.observe(observed=1000 + index)
        service = self.service(replace(self.config, batch_size=500))
        service._ensure_writer()
        service._ensure_source()
        assert service._organizer is not None and service._writer is not None
        original_lookup = service._organizer.knowledge.get_item_by_observation_id
        with patch.object(service._organizer.knowledge, "get_item_by_observation_id", return_value=None):
            service.process_once()
            service.process_once()
        queue = service._writer.get_state("unresolved_queue")
        saved = [value for page in range(queue["head"], queue["tail"])
                 for value in service._writer.get_state("unresolved_page:" + str(page), [])]
        self.assertEqual(len(set(saved)), 510)
        self.assertEqual(service._organizer.knowledge.get_item_by_observation_id, original_lookup)
        service.process_once()
        service.process_once()
        self.assertEqual(service._writer.get_state("unresolved_queue"), {"head": 0, "tail": 0})
        self.assertTrue(service.handle("search")["memories"])


if __name__ == "__main__":
    unittest.main()
