"""Real temporary-store lifecycle with simulated model replies; no API or capture."""

import os
from pathlib import Path
import tempfile
import time
import unittest

import yaml

from mirally.core.activity import Observation, ObservationStore
from mirally.runtime.memory import MemoryService


class SemanticLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="mirally 生命周期 spaces ")
        self.root = Path(self.directory.name).resolve()
        self.now = [int(time.time() * 1000) + 5000]
        self.observed = (self.now[0] // 300000 - 2) * 300000 + 1000
        self.activity = self.root / "activity.yaml"
        self.raw_path = self.root / "原始 records.sqlite3"
        self.activity.write_text(yaml.safe_dump({"enabled": False, "storage": {
            "directory": str(self.root / "raw assets"), "database_path": str(self.raw_path),
            "knowledge_database_path": str(self.root / "public.sqlite3")}}), encoding="utf-8")
        self.settings = {"enabled": True, "activity_config_path": str(self.activity),
            "storage": {"database_path": str(self.root / "持久 memory.sqlite3"),
                        "projection_database_path": str(self.root / "private.sqlite3")},
            "raw_retention": {"enabled": True, "interval_seconds": 1},
            "semantics": {"enabled": True, "max_calls_per_hour": 1000,
                          "max_tokens_per_day": 10000000, "max_input_bytes": 4096}}
        self.config_path = self.root / "memory.yaml"
        self.write_config()
        self.source = ObservationStore(self.raw_path)
        self.service = MemoryService(self.root, config_path=self.config_path, clock=lambda: self.now[0])
        self.sequence = 0

    def tearDown(self):
        self.service.close()
        self.source.close()
        self.directory.cleanup()

    def write_config(self):
        self.config_path.write_text(yaml.safe_dump(self.settings), encoding="utf-8")

    def observe(self, text="Work on the migration document", *, observed=None, **kwargs):
        self.sequence += 1
        kind = kwargs.pop("kind", "content_snapshot")
        payload = {"text": text, "window_title": "Migration notes", **kwargs.pop("payload", {})}
        row = Observation.create(kind=kind, source_id="fixture", source_epoch="epoch",
            source_seq=self.sequence, boot_id="boot", observed_at_utc_ms=observed or self.observed,
            app_id="editor.exe", window_instance_id=kwargs.pop("window_instance_id", "editor"),
            payload=payload, **kwargs)
        return self.source.append(row)

    def claim(self):
        result = self.service.handle("claim_job")
        self.assertEqual(result["status"], "ready", result)
        return result["job"]

    def finish(self, job):
        return self.service.handle("complete_job", {"job_id": job["job_id"],
            "lease_token": job["lease_token"], "result": {
                "summary": "Observed editing migration notes. Completion remains unknown.",
                "source_observation_ids": job["payload"]["source_observation_ids"][:1],
                "facts": []}, "usage": {"totalTokens": 10}})

    def receipt(self, window):
        return self.service._writer.get_state("semantic_receipt:" + window["window_id"])

    def cleanup_cycle(self):
        self.now[0] += 1000
        for _ in range(15):
            result = self.service.process_once()
            if not self.service._writer.get_state("raw_retention", {}).get("cycle"):
                return result["raw_retention"]
        self.fail("retention cycle did not finish")

    def test_opt_in_reload_backfills_only_retained_recent_hour(self):
        self.settings["semantics"]["enabled"] = False
        self.write_config()
        self.service.handle("reload_settings")
        old = self.observe("Old retained notes", observed=self.observed - 7200000)
        recent = self.observe()
        self.service.process_once()
        before = self.service._writer.get_state("activity_checkpoint")
        self.assertEqual(self.service.handle("claim_job")["status"], "disabled")
        self.settings["semantics"]["enabled"] = True
        self.write_config()
        reloaded = self.service.handle("reload_settings")
        self.assertIn("health", reloaded)
        self.service.process_once()
        job = self.claim()
        self.assertIn(recent.observation_id, job["payload"]["source_observation_ids"])
        self.assertNotIn(old.observation_id, job["payload"]["source_observation_ids"])
        self.assertEqual(self.finish(job)["status"], "completed")
        self.assertEqual(self.service.handle("claim_job")["status"], "idle")
        self.assertEqual(before, self.service._writer.get_state("activity_checkpoint"))
        self.cleanup_cycle()
        self.assertIsNone(self.source.get(recent.observation_id))
        self.assertIsNotNone(self.source.get(old.observation_id))

    def test_disabling_stops_dispatch_but_inflight_receipt_can_purge(self):
        first = self.observe()
        second = self.observe("Another time bucket", observed=self.observed - 300000)
        self.service.process_once()
        job = self.claim()
        self.settings["semantics"]["enabled"] = False
        self.write_config()
        self.service.handle("reload_settings")
        self.assertEqual(self.service.handle("claim_job")["status"], "disabled")
        self.assertEqual(self.finish(job)["status"], "completed")
        self.cleanup_cycle()
        covered = set(job["payload"]["source_observation_ids"])
        for row in (first, second):
            self.assertEqual(self.source.get(row.observation_id) is None, row.observation_id in covered)

    def test_same_bucket_scopes_share_one_job_and_full_receipts(self):
        first = self.observe()
        second = self.observe("Monitor document", window_instance_id="monitor",
            payload={"scope": "monitor", "monitor_id": "fixture-display"})
        self.service.process_once()
        job = self.claim()
        self.assertEqual(len(job["payload"]["windows"]), 2)
        self.assertEqual(self.finish(job)["status"], "completed")
        for window in job["payload"]["windows"]:
            self.assertEqual(self.receipt(window)["source_observation_ids"], window["source_observation_ids"])
        self.assertEqual(self.service.handle("claim_job")["status"], "idle")
        self.cleanup_cycle()
        self.assertIsNone(self.source.get(first.observation_id))
        self.assertIsNone(self.source.get(second.observation_id))

    def test_long_evidence_requires_all_parts_and_merge_before_purge(self):
        row = self.observe("Complete document: " + "迁移证据0123456789" * 1400)
        self.service.process_once()
        job = self.claim()
        windows = job["payload"]["windows"]
        batch = self.service._writer.get_state(job["payload"]["batch_id"])
        self.assertGreater(len(batch["jobs"]), 1)
        self.assertEqual(self.finish(job)["status"], "partial_completed")
        self.assertTrue(all(self.receipt(window) is None for window in windows))
        self.cleanup_cycle()
        self.assertIsNotNone(self.source.get(row.observation_id))
        levels = {0}
        for _ in range(100):
            job = self.claim()
            levels.add(job["payload"]["level"])
            outcome = self.finish(job)
            if outcome["status"] == "completed":
                break
            self.assertTrue(all(self.receipt(window) is None for window in windows))
        else:
            self.fail("multipart synthesis did not complete")
        self.assertGreater(max(levels), 0)
        self.assertTrue(all(self.receipt(window) for window in windows))
        self.cleanup_cycle()
        self.assertIsNone(self.source.get(row.observation_id))

    def test_late_ocr_rejects_stale_output_and_retains_raw(self):
        original = self.observe("Original screen content")
        self.service.process_once()
        job = self.claim()
        late = self.observe("Late OCR adds important details", kind="ocr_result",
            observed=self.now[0], caused_by=(original.observation_id,))
        self.service.process_once()
        self.assertEqual(self.finish(job)["status"], "stale_source_revision")
        self.assertTrue(all(self.receipt(window) is None for window in job["payload"]["windows"]))
        self.cleanup_cycle()
        self.assertIsNotNone(self.source.get(original.observation_id))
        self.assertIsNotNone(self.source.get(late.observation_id))

    def test_insufficient_budget_protects_raw_in_unicode_directory_and_changed_cwd(self):
        self.settings["semantics"]["max_calls_per_hour"] = 0
        self.write_config()
        self.service.handle("reload_settings")
        row = self.observe()
        old_cwd = Path.cwd()
        try:
            os.chdir(self.root.parent)
            self.service.process_once()
            self.assertEqual(self.service.handle("claim_job")["status"], "budget_exhausted")
            self.cleanup_cycle()
        finally:
            os.chdir(old_cwd)
        self.assertIsNotNone(self.source.get(row.observation_id))
        self.assertEqual(len({self.raw_path, self.service.config.database_path,
            self.service.config.projection_database_path}), 3)
        self.assertGreater(self.service.handle("health")["semantic_maintenance"]["pending_windows"], 0)

    def test_reload_updates_queued_route_and_wakes_budget_deferred_job(self):
        self.settings["semantics"].update(model="old", max_output_tokens=1500,
                                          max_calls_per_hour=0)
        self.write_config()
        self.service.handle("reload_settings")
        row = self.observe()
        self.service.process_once()
        blocked = self.service.handle("claim_job")
        self.assertEqual(blocked["status"], "budget_exhausted")
        self.assertGreater(blocked["next_attempt_utc_ms"], self.now[0])
        pending = self.service._writer.get_state("semantic_pending_windows")
        window_id = next(iter(pending))
        record = self.service._writer.get_memory("episodic_" + window_id)
        # Inspect durable queue state without claiming or bypassing its budget.
        with self.service._writer._lock:
            job_id = self.service._writer._connection.execute(
                "SELECT job_id FROM memory_jobs ORDER BY job_id LIMIT 1").fetchone()[0]
        queued = self.service._writer.get_job(job_id)
        self.assertEqual(queued["payload"]["model"], "old")
        self.settings["semantics"].update(model="new", max_output_tokens=700,
                                          max_calls_per_hour=1000)
        self.write_config()
        before_time = self.now[0]
        self.service.handle("reload_settings")
        job = self.claim()
        self.assertEqual(self.now[0], before_time)
        self.assertEqual(job["job_id"], job_id)
        self.assertEqual((job["payload"]["model"], job["payload"]["max_tokens"]), ("new", 700))
        for key in ("prompt", "windows", "source_observation_ids", "input_refs", "input_hash"):
            self.assertEqual(job["payload"][key], queued["payload"][key])
        self.assertEqual(record["metadata"]["input_hash"], job["payload"]["windows"][0]["input_hash"])
        self.assertEqual(self.finish(job)["status"], "completed")
        self.cleanup_cycle()
        self.assertIsNone(self.source.get(row.observation_id))


if __name__ == "__main__":
    unittest.main()
