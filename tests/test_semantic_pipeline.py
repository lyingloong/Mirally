"""Complete-evidence batch coverage, restart and revision fences offline."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from mirally.core.memory import MemoryStore
from mirally.runtime.memory.semantic_pipeline import SemanticPipeline


class SemanticPipelineTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "memory.sqlite3"
        self.store = MemoryStore(self.path)
        self.addCleanup(lambda: self.store.close())
        semantics = SimpleNamespace(max_input_bytes=800, agent_id="main", model=None, max_output_tokens=300)
        self.service = SimpleNamespace(_writer=self.store,
            _organizer=SimpleNamespace(source=SimpleNamespace(generation=lambda: "g1")),
            config=SimpleNamespace(semantics=semantics), clock=lambda: 999999,
            semantic_system_prompt="Return summary, source_observation_ids and facts JSON; preserve uncertainty.")
        self.pipeline = SemanticPipeline(self.service)
        self.records, self.states = [], []
        for index in range(2):
            refs = [f"obs-{index}-{i}" for i in range(12)]
            record = self.store.put_memory({"id": f"parent{index}", "kind": "episodic", "status": "active",
                "scope": "user/local", "provenance": "observed", "title": "Observed", "content": "Complete local activity",
                "source_observation_ids": refs, "observed_start_utc_ms": 100, "observed_end_utc_ms": 200,
                "metadata": {"window_id": f"window{index}", "input_hash": f"hash{index}", "source_generation": "g1"}})
            self.records.append(record)
            self.states.append({"id": f"window{index}", "memory_id": record["id"], "generation": "g1"})

    def queue(self, rows):
        with patch("mirally.runtime.memory.semantic_pipeline.build_evidence", return_value=rows):
            self.pipeline.queue(self.records, self.states)

    def reply(self, job):
        refs = job["payload"]["source_observation_ids"]
        # Deliberately cite one source: receipt coverage must remain the full
        # deterministic manifest, independent of the model's supporting subset.
        result = {"summary": "Observed a document. Completion is unknown.", "source_observation_ids": refs[:1], "facts": []}
        return self.pipeline.complete(job["job_id"], job["lease_token"], job["payload"], result, {"requests": 1})

    def test_single_packet_covers_all_scopes_with_full_manifest_and_fenced_receipts(self):
        self.queue([{"text": "Complete evidence", "source_observation_ids": ["obs-0-0"]}])
        job = self.store.claim_job()
        self.assertEqual(job["payload"]["memory_id"], "parent0")
        self.assertEqual(len(job["payload"]["windows"]), 2)
        self.assertEqual(self.reply(job)["status"], "completed")
        for index, record in enumerate(self.records):
            receipt = self.store.get_state(f"semantic_receipt:window{index}")
            self.assertEqual(receipt["source_observation_ids"], record["source_observation_ids"])
            self.assertEqual(receipt["input_hash"], record["metadata"]["input_hash"])
            self.assertEqual(receipt["scope"], "user/local")
            self.assertIsNotNone(self.store.get_memory("synthesis_" + record["id"]))
        self.assertEqual(self.store.get_state("semantic_pending_windows"), {})
        self.assertEqual(self.reply(job)["status"], "stale_lease")

    def test_parts_never_receipt_until_restartable_layered_merge_covers_every_packet(self):
        rows = [{"text": "Evidence " + str(i) + ":" + "原文" * 75,
            "source_observation_ids": [f"obs-{i % 2}-{i % 12}"]} for i in range(18)]
        self.queue(rows)
        first = self.store.claim_job()
        initial = self.store.get_state(first["payload"]["batch_id"])
        self.assertGreater(len(initial["jobs"]), 1)
        self.assertEqual(self.reply(first)["status"], "partial_completed")
        self.assertIsNone(self.store.get_state("semantic_receipt:window0"))
        self.assertIsNone(self.store.get_memory("synthesis_parent0"))
        self.assertNotIn("prompt", self.store.get_job(first["job_id"])["payload"])
        self.assertIn("windows", self.store.get_job(first["job_id"])["payload"])
        self.store.close()
        self.store = MemoryStore(self.path)
        self.service._writer = self.store
        self.pipeline = SemanticPipeline(self.service)
        count, levels = 1, {0}
        while (job := self.store.claim_job()) is not None:
            self.assertLessEqual(len(job["payload"]["prompt"].encode("utf-8")), 800)
            levels.add(job["payload"]["level"])
            outcome = self.reply(job)
            count += 1
            self.assertLess(count, 100)
            if outcome["status"] == "completed":
                break
            self.assertIsNone(self.store.get_state("semantic_receipt:window0"))
        self.assertEqual(outcome["status"], "completed")
        self.assertGreater(max(levels), 1)
        self.assertEqual(self.store.get_state(initial["id"])["phase"], "complete")
        self.assertEqual(self.store.get_state(initial["id"])["results"], {})
        self.assertNotIn("prompt", self.store.get_job(job["job_id"])["payload"])
        self.assertEqual(len(self.store.get_state("semantic_receipt:window0")["source_observation_ids"]), 12)

    def test_revision_change_and_forgotten_window_never_receive_receipt(self):
        self.queue([{"text": "Full evidence", "source_observation_ids": ["obs-0-0"]}])
        job = self.store.claim_job()
        changed = dict(self.records[1])
        changed["metadata"] = {**changed["metadata"], "input_hash": "new-input"}
        self.store.put_memory(changed)
        self.assertEqual(self.reply(job)["status"], "stale_source_revision")
        self.assertIsNone(self.store.get_state("semantic_receipt:window0"))
        self.assertIsNone(self.store.get_memory("synthesis_parent0"))
        self.assertIn("window0", self.store.get_state("semantic_pending_windows"))

    def test_forgotten_source_discards_batch_and_keeps_no_receipt(self):
        self.queue([{"text": "Full evidence", "source_observation_ids": ["obs-0-0"]}])
        job = self.store.claim_job()
        self.store.forget(source_observation_ids=["obs-1-0"], scope="user/local")
        self.assertEqual(self.reply(job)["status"], "stale_lease")
        self.assertIsNone(self.store.get_state("semantic_receipt:window0"))
        self.assertIsNone(self.store.get_memory("synthesis_parent0"))

    def test_leaf_support_is_limited_to_its_packet_not_global_manifest(self):
        rows = [{"text": "x" * 600, "source_observation_ids": ["obs-0-0"]},
                {"text": "y" * 600, "source_observation_ids": ["obs-1-0"]}]
        self.queue(rows)
        job = self.store.claim_job()
        self.assertEqual(job["payload"]["source_observation_ids"], ["obs-0-0"])
        self.assertEqual(len(job["payload"]["windows"][1]["source_observation_ids"]), 12)

    def test_source_generation_change_discards_completion(self):
        self.queue([{"text": "Full evidence", "source_observation_ids": ["obs-0-0"]}])
        job = self.store.claim_job()
        self.service._organizer.source.generation = lambda: "replacement"
        self.assertEqual(self.reply(job)["status"], "stale_source_revision")
        self.assertIsNone(self.store.get_state("semantic_receipt:window0"))

    def test_failed_final_commit_rolls_back_summary_receipt_usage_and_completion(self):
        self.queue([{"text": "Full evidence", "source_observation_ids": ["obs-0-0"]}])
        job = self.store.claim_job()
        original = self.store.set_state
        def fail_receipt(key, value):
            if key == "semantic_receipt:window1":
                raise RuntimeError("commit interrupted")
            return original(key, value)
        with patch.object(self.store, "set_state", side_effect=fail_receipt):
            with self.assertRaisesRegex(RuntimeError, "interrupted"):
                self.reply(job)
        self.assertEqual(self.store.get_job(job["job_id"])["status"], "running")
        self.assertIsNone(self.store.get_memory("synthesis_parent0"))
        self.assertIsNone(self.store.get_state("semantic_receipt:window0"))
        self.assertEqual(self.store.usage_summary()["requests"], 0)
        self.assertEqual(self.reply(job)["status"], "completed")


if __name__ == "__main__":
    unittest.main()
