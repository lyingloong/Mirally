"""Offline file-watch settings regressions; model replies are simulated only."""

import copy
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import yaml

from mirally.core.activity import Observation, ObservationStore
from mirally.runtime.memory import MemoryService, load_memory_config


class MemoryConfigReloadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mirally 配置 watch ")
        self.root = Path(self.temp.name).resolve()
        self.now = int(time.time() * 1000) + 5000
        self.observed = (self.now // 300000 - 2) * 300000 + 1000
        self.activity = self.root / "activity.yaml"
        self.raw_path = self.root / "raw.sqlite3"
        self.activity.write_text(yaml.safe_dump({"enabled": False, "storage": {
            "directory": str(self.root / "assets"), "database_path": str(self.raw_path),
            "knowledge_database_path": str(self.root / "public.sqlite3")}}), encoding="utf-8")
        self.settings = {"enabled": True, "activity_config_path": str(self.activity),
            "storage": {"database_path": str(self.root / "memory.sqlite3"),
                        "projection_database_path": str(self.root / "projection.sqlite3")},
            "raw_retention": {"enabled": False}, "semantics": {"enabled": False,
                "model": "old", "max_calls_per_hour": 1000, "max_tokens_per_day": 10000000}}
        self.path = self.root / "memory.yaml"
        self.write()
        self.source = ObservationStore(self.raw_path)
        self.services = []
        self.sequence = 0

    def tearDown(self):
        for service in reversed(self.services):
            service.close()
        self.source.close()
        self.temp.cleanup()

    def service(self, **kwargs):
        service = MemoryService(self.root, config_path=self.path, clock=lambda: self.now, **kwargs)
        self.services.append(service)
        return service

    def write(self, *, atomic=False):
        text = yaml.safe_dump(self.settings)
        if atomic:
            replacement = self.path.with_suffix(".new")
            replacement.write_text(text, encoding="utf-8")
            os.replace(replacement, self.path)
        else:
            self.path.write_text(text, encoding="utf-8")

    def sample(self, service):
        service.handle("health")
        time.sleep(0.3)
        return service.handle("health")

    def observe(self, *, observed=None):
        self.sequence += 1
        return self.source.append(Observation.create(kind="content_snapshot", source_id="fixture",
            source_epoch="epoch", source_seq=self.sequence, boot_id="boot",
            observed_at_utc_ms=observed or self.observed, app_id="editor.exe",
            window_instance_id="editor", payload={"text": "Migration document evidence",
                                                      "window_title": "Migration notes"}))

    def finish(self, service, job):
        return service.handle("complete_job", {"job_id": job["job_id"], "lease_token": job["lease_token"],
            "result": {"summary": "Observed migration document editing; completion unknown.",
                "source_observation_ids": job["payload"]["source_observation_ids"][:1], "facts": []},
            "usage": {"totalTokens": 10}})

    def test_file_opt_in_backfills_recent_hour_without_explicit_reload(self):
        service = self.service()
        old = self.observe(observed=self.observed - 7200000)
        recent = self.observe()
        service.process_once()
        checkpoint = service._writer.get_state("activity_checkpoint")
        self.settings["semantics"]["enabled"] = True
        self.write()
        self.assertEqual(self.sample(service)["config_reload"]["state"], "ready")
        service.process_once()
        job = service.handle("claim_job")["job"]
        self.assertIsNotNone(job)
        self.assertIn(recent.observation_id, job["payload"]["source_observation_ids"])
        self.assertNotIn(old.observation_id, job["payload"]["source_observation_ids"])
        self.assertEqual(checkpoint, service._writer.get_state("activity_checkpoint"))

    def test_restarted_worker_backfills_when_file_enabled_while_stopped(self):
        service = self.service()
        old = self.observe(observed=self.observed - 7200000)
        recent = self.observe()
        service.process_once()
        checkpoint = service._writer.get_state("activity_checkpoint")
        self.assertEqual(service.handle("claim_job")["status"], "disabled")
        service.close()
        self.settings["semantics"]["enabled"] = True
        self.write(atomic=True)
        resumed = self.service()
        resumed.process_once()
        claimed = resumed.handle("claim_job")
        self.assertEqual(claimed["status"], "ready")
        job = claimed["job"]
        self.assertIn(recent.observation_id, job["payload"]["source_observation_ids"])
        self.assertNotIn(old.observation_id, job["payload"]["source_observation_ids"])
        self.assertEqual(resumed._writer.get_state("activity_checkpoint"), checkpoint)
        self.assertEqual(self.finish(resumed, job)["status"], "completed")
        self.assertEqual(resumed.handle("claim_job")["status"], "idle")

    def test_backfill_pages_identifiers_without_decoding_raw_payloads(self):
        service = self.service()
        rows = [self.observe(observed=self.observed + index) for index in range(200)]
        rows.extend(self.observe(observed=self.observed - 300000 + index) for index in range(5))
        for _ in range(3):
            service.process_once()
        checkpoint = service._writer.get_state("activity_checkpoint")
        expected = {service._writer.get_state("observation_window:" + row.observation_id) for row in rows}
        self.assertEqual(len(expected), 2)
        self.assertNotIn(None, expected)
        self.assertEqual(service._writer.get_state("dirty_windows"), [])
        self.settings["semantics"]["enabled"] = True
        self.write()
        self.sample(service)
        raw_source = service._organizer.source
        with patch.object(raw_source, "query", side_effect=AssertionError("backfill decoded raw query")), \
                patch.object(raw_source, "get", side_effect=AssertionError("backfill decoded raw get")):
            self.assertFalse(service._backfill_recent())
            self.assertEqual(service._backfill_cursor, rows[199].ingest_seq)
            self.assertEqual(len(service._writer.get_state("dirty_windows")), 1)
            self.assertTrue(service._backfill_recent())
            self.assertEqual(service._backfill_cursor, rows[-1].ingest_seq)
            self.assertTrue(service._backfill_recent())
        dirty = service._writer.get_state("dirty_windows")
        self.assertEqual(set(dirty), expected)
        self.assertEqual(len(dirty), len(expected))
        self.assertEqual(checkpoint, service._writer.get_state("activity_checkpoint"))
        service.process_once()
        covered, jobs = set(), set()
        for _ in range(100):
            claimed = service.handle("claim_job")
            if claimed["status"] == "idle":
                break
            self.assertEqual(claimed["status"], "ready")
            job = claimed["job"]
            self.assertNotIn(job["job_id"], jobs)
            jobs.add(job["job_id"])
            covered.update(job["payload"]["source_observation_ids"])
            self.finish(service, job)
        else:
            self.fail("backfill jobs did not finish")
        self.assertEqual(covered, {row.observation_id for row in rows})
        self.assertTrue(jobs)

    def test_queued_route_output_and_budget_follow_atomic_file_update(self):
        self.settings["semantics"].update(enabled=True, max_calls_per_hour=0)
        self.write()
        service = self.service()
        self.observe()
        service.process_once()
        self.assertEqual(service.handle("claim_job")["status"], "budget_exhausted")
        self.settings["semantics"].update(model="new", agent_id="other-agent",
            max_output_tokens=700, max_calls_per_hour=1000)
        self.write(atomic=True)
        health = self.sample(service)
        self.assertEqual(health["config_reload"]["state"], "ready")
        job = service.handle("claim_job")["job"]
        self.assertIsNotNone(job)
        self.assertEqual((job["payload"]["model"], job["payload"]["agent_id"],
                          job["payload"]["max_tokens"]), ("new", "other-agent", 700))
        self.assertEqual(self.finish(service, job)["status"], "completed")

    def test_transient_missing_empty_and_invalid_keep_last_valid_then_recover(self):
        service = self.service()
        original = service.config
        for content in (None, "", "semantics: [broken", "semantics:\n  enabled: perhaps\n"):
            with self.subTest(content=content):
                if content is None:
                    self.path.unlink()
                else:
                    self.path.write_text(content, encoding="utf-8")
                health = self.sample(service)
                self.assertEqual(service.config, original)
                self.assertEqual(health["config_reload"]["state"], "invalid")
                self.assertTrue(health["config_reload"].get("error"))
                self.write(atomic=True)
                self.assertEqual(self.sample(service)["config_reload"]["state"], "ready")
        self.settings["semantics"]["model"] = "repaired"
        self.write()
        self.sample(service)
        self.assertEqual(service.config.semantics.model, "repaired")

    def test_immutable_storage_or_window_change_rejects_entire_update(self):
        service = self.service()
        original = copy.deepcopy(self.settings)
        active = service.config
        for change in ("storage", "window_seconds"):
            with self.subTest(change=change):
                self.settings = copy.deepcopy(original)
                if change == "storage":
                    self.settings["storage"]["database_path"] = str(self.root / "different.sqlite3")
                else:
                    self.settings["window_seconds"] = 600
                self.settings["semantics"]["model"] = "must-not-apply"
                self.write()
                health = self.sample(service)
                self.assertEqual(health["config_reload"]["state"], "restart_required")
                self.assertEqual(service.config, active)
        self.settings = original
        self.write()
        self.assertEqual(self.sample(service)["config_reload"]["state"], "ready")

    def test_format_only_change_does_not_backfill_reset_failures_or_usage(self):
        self.settings["semantics"]["enabled"] = True
        self.write()
        service = self.service()
        self.observe()
        service.process_once()
        job = service.handle("claim_job")["job"]
        self.finish(service, job)
        failure = {"job_id": "fixture-failed", "error": "simulated failure"}
        service._writer.set_state("semantic_last_failure", failure)
        usage = service._writer.usage_summary()
        with patch.object(service, "_backfill_recent", wraps=service._backfill_recent) as backfill:
            self.path.write_text("# formatting only\n\n" + yaml.safe_dump(self.settings, sort_keys=False), encoding="utf-8")
            health = self.sample(service)
            backfill.assert_not_called()
        self.assertEqual(health["config_reload"]["state"], "ready")
        self.assertEqual(service._writer.get_state("semantic_last_failure"), failure)
        self.assertEqual(service._writer.usage_summary(), usage)

    def test_disabled_start_does_not_create_memory_db_and_file_can_enable(self):
        self.settings["enabled"] = False
        self.settings["poll_interval_seconds"] = 300
        self.write()
        service = self.service()
        service.start()
        service.process_once()
        self.assertFalse((self.root / "memory.sqlite3").exists())
        self.assertFalse((self.root / "projection.sqlite3").exists())
        self.observe()
        self.settings["enabled"] = True
        self.write()
        deadline = time.monotonic() + 4
        while not service.config.enabled and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertTrue(service.config.enabled, "disabled worker must continue watching config")
        service.process_once()
        self.assertTrue((self.root / "memory.sqlite3").exists())

    def test_global_disable_stops_claim_but_accepts_inflight_completion(self):
        self.settings["semantics"]["enabled"] = True
        self.write()
        service = self.service()
        self.observe()
        service.process_once()
        job = service.handle("claim_job")["job"]
        self.settings["enabled"] = False
        self.write()
        self.sample(service)
        self.assertFalse(service.config.enabled)
        self.assertEqual(service.handle("claim_job")["status"], "disabled")
        self.assertEqual(self.finish(service, job)["status"], "completed")

    def test_unrelated_edit_does_not_wake_budget_deferral(self):
        self.settings["semantics"].update(enabled=True, max_calls_per_hour=0)
        self.write()
        service = self.service()
        self.observe()
        service.process_once()
        self.assertEqual(service.handle("claim_job")["status"], "budget_exhausted")
        wait = service._writer.get_state("semantic_budget_wait")
        with service._writer._lock:
            job_id = service._writer._connection.execute(
                "SELECT job_id FROM memory_jobs LIMIT 1").fetchone()[0]
        before = service._writer.get_job(job_id)
        self.settings["context_token_budget"] = 1200
        self.write()
        self.sample(service)
        self.assertEqual(service.config.context_token_budget, 1200)
        self.assertEqual(service.handle("claim_job")["status"], "idle")
        self.assertEqual(service._writer.get_state("semantic_budget_wait"), wait)
        after = service._writer.get_job(job_id)
        for key in ("status", "attempt", "available_at_utc_ms", "error"):
            self.assertEqual(after[key], before[key])

    def test_unrelated_edit_preserves_real_failed_call_retry_and_usage(self):
        self.settings["semantics"]["enabled"] = True
        self.write()
        service = self.service()
        self.observe()
        service.process_once()
        job = service.handle("claim_job")["job"]
        result = service.handle("fail_job", {"job_id": job["job_id"],
            "lease_token": job["lease_token"], "error": "simulated provider failure",
            "usage": {"totalTokens": 10}})
        self.assertEqual(result["status"], "retry_pending")
        before = service._writer.get_job(job["job_id"])
        self.assertGreater(before["available_at_utc_ms"], self.now)
        usage = service._writer.usage_summary()
        self.settings["context_token_budget"] = 1200
        self.write(atomic=True)
        self.sample(service)
        service.process_once()
        self.assertEqual(service.handle("claim_job")["status"], "idle")
        after = service._writer.get_job(job["job_id"])
        for key in ("status", "attempt", "available_at_utc_ms", "error"):
            self.assertEqual(after[key], before[key])
        self.assertEqual(service._writer.usage_summary(), usage)

    def test_injected_config_does_not_watch_or_apply_file_changes(self):
        injected = load_memory_config(self.root, self.path)
        service = self.service(config=injected)
        self.settings["semantics"].update(enabled=True, model="must-not-apply")
        self.write()
        self.sample(service)
        service.process_once()
        self.assertEqual(service.config, injected)

    def test_debounce_requires_stable_signature_and_cancelled_edit_does_not_apply(self):
        service = self.service()
        original_text = self.path.read_text(encoding="utf-8")
        revision = service.handle("health")["config_reload"]["revision"]
        with patch("mirally.runtime.memory.service.time.monotonic") as monotonic:
            monotonic.return_value = 100.0
            self.settings["semantics"]["model"] = "first-incomplete-save"
            self.write()
            service.handle("health")
            self.assertEqual(service.config.semantics.model, "old")
            monotonic.return_value = 100.2
            self.settings["semantics"]["model"] = "second-complete-save"
            self.write(atomic=True)
            service.handle("health")
            monotonic.return_value = 100.3
            service.handle("settings")
            self.assertEqual(service.config.semantics.model, "old")
            monotonic.return_value = 100.46
            self.assertEqual(service.handle("health")["settings"]["semantics"]["model"], "second-complete-save")
            self.path.write_text(original_text, encoding="utf-8")
            monotonic.return_value = 101.0
            service.handle("health")
            # Cancel this pending restoration by returning to active content.
            self.write()
            monotonic.return_value = 101.3
            health = service.handle("health")
            self.assertEqual(health["config_reload"]["state"], "ready")
            self.assertEqual(service.config.semantics.model, "second-complete-save")
            self.assertNotEqual(health["config_reload"]["revision"], revision)

    def test_unrelated_edit_does_not_revive_exhausted_failed_job(self):
        self.settings["semantics"]["enabled"] = True
        self.write()
        service = self.service()
        self.observe()
        service.process_once()
        for _ in range(3):
            job = service.handle("claim_job")["job"]
            self.assertIsNotNone(job)
            result = service.handle("fail_job", {"job_id": job["job_id"],
                "lease_token": job["lease_token"], "error": "simulated persistent failure",
                "usage": {"totalTokens": 10}})
            self.now += 61000
        self.assertEqual(result["status"], "failed")
        before = service._writer.get_job(job["job_id"])
        failure = service._writer.get_state("semantic_last_failure")
        usage = service._writer.usage_summary()
        self.settings["context_token_budget"] = 1200
        self.write()
        self.sample(service)
        service.process_once()
        self.assertEqual(service.handle("claim_job")["status"], "idle")
        after = service._writer.get_job(job["job_id"])
        self.assertEqual((after["status"], after["attempt"]), ("failed", before["attempt"]))
        self.assertEqual(service._writer.get_state("semantic_last_failure"), failure)
        self.assertEqual(service._writer.usage_summary(), usage)

    def test_same_size_and_timestamp_content_edit_is_detected(self):
        service = self.service()
        original = self.path.stat()
        self.settings["semantics"]["model"] = "new"
        self.write()
        self.assertEqual(self.path.stat().st_size, original.st_size)
        os.utime(self.path, ns=(original.st_atime_ns, original.st_mtime_ns))
        self.assertEqual(self.path.stat().st_mtime_ns, original.st_mtime_ns)
        self.sample(service)
        self.assertEqual(service.config.semantics.model, "new")

    def test_retention_interval_edit_reschedules_persisted_deadline(self):
        self.settings["raw_retention"] = {"enabled": True, "interval_seconds": 3600}
        self.write()
        service = self.service()
        self.observe()
        service.process_once()
        before = service._writer.get_state("raw_retention")
        self.assertEqual(before["next_cleanup_utc_ms"], self.now + 3600000)
        self.settings["raw_retention"]["interval_seconds"] = 120
        self.write()
        self.sample(service)
        after = service._writer.get_state("raw_retention")
        self.assertEqual(after["next_cleanup_utc_ms"], self.now + 120000)
        self.assertFalse(after.get("cycle"))

    def assert_status_responds_while_locked(self, service, lock):
        held, release = threading.Event(), threading.Event()
        def hold():
            with lock:
                held.set()
                release.wait(4)
        holder = threading.Thread(target=hold, daemon=True)
        holder.start()
        self.assertTrue(held.wait(1), "fixture lock was not acquired")
        results, completed, threads = {}, {}, []
        for operation in ("health", "settings"):
            completed[operation] = threading.Event()
            def query(op=operation):
                try:
                    results[op] = service.handle(op)
                except Exception as error:
                    results[op] = error
                finally:
                    completed[op].set()
            thread = threading.Thread(target=query, daemon=True)
            threads.append(thread)
            thread.start()
        try:
            responsive = {op: event.wait(0.75) for op, event in completed.items()}
        finally:
            release.set()
            holder.join(2)
            for thread in threads:
                thread.join(2)
        self.assertEqual(responsive, {"health": True, "settings": True})
        self.assertTrue(all(isinstance(result, dict) for result in results.values()), results)

    def test_health_and_settings_do_not_wait_for_maintenance_lock(self):
        service = self.service()
        self.observe()
        service.process_once()
        self.assert_status_responds_while_locked(service, service._maintenance_lock)

    def test_health_and_settings_do_not_wait_for_busy_writer_transaction(self):
        service = self.service()
        self.observe()
        service.process_once()
        self.assert_status_responds_while_locked(service, service._writer._lock)

    def test_claim_returns_busy_without_leasing_or_spending_during_maintenance(self):
        self.settings["semantics"]["enabled"] = True
        self.write()
        service = self.service()
        self.observe()
        service.process_once()
        usage = service._writer.usage_summary()
        with service._writer._lock:
            job_id = service._writer._connection.execute(
                "SELECT job_id FROM memory_jobs LIMIT 1").fetchone()[0]
        before = service._writer.get_job(job_id)
        held, release, completed = threading.Event(), threading.Event(), threading.Event()
        result = {}
        def hold():
            with service._maintenance_lock:
                held.set()
                release.wait(4)
        def claim():
            try:
                result.update(service.handle("claim_job"))
            finally:
                completed.set()
        holder = threading.Thread(target=hold, daemon=True)
        requester = threading.Thread(target=claim, daemon=True)
        holder.start()
        self.assertTrue(held.wait(1))
        requester.start()
        try:
            responsive = completed.wait(0.75)
        finally:
            release.set()
            holder.join(2)
            requester.join(2)
        self.assertTrue(responsive, "claim must return while maintenance remains busy")
        self.assertEqual(result["status"], "maintenance_busy")
        self.assertIsNone(result["job"])
        after = service._writer.get_job(job_id)
        for key in ("status", "attempt", "lease_token", "lease_expires_utc_ms"):
            self.assertEqual(after[key], before[key])
        self.assertEqual(service._writer.usage_summary(), usage)
        claimed = service.handle("claim_job")
        self.assertEqual(claimed["status"], "ready")
        self.assertEqual(claimed["job"]["job_id"], job_id)


if __name__ == "__main__":
    unittest.main()
