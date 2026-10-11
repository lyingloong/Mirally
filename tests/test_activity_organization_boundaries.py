"""Resource boundaries, retry progress and truthful query states."""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from openassist.core.activity import Observation, ObservationStore
from openassist.core.activity.knowledge import ActivityOrganizer, ContextQuery, KnowledgeStore


class ActivityOrganizationBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        self.source = ObservationStore(root / "raw.sqlite3")
        self.knowledge = KnowledgeStore(root / "derived.sqlite3")
        self.organizer = ActivityOrganizer(self.source, self.knowledge)
        self.seq = 0

    def tearDown(self):
        self.knowledge.close()
        self.source.close()
        self.directory.cleanup()

    def append(self, kind, at, *, epoch="epoch", **kwargs):
        row = Observation.create(kind=kind, source_id="fixture", source_epoch=epoch,
                                 source_seq=self.seq, boot_id="boot", session_instance_id="session",
                                 window_instance_id="window", app_id="browser", observed_at_utc_ms=at, **kwargs)
        self.seq += 1
        return self.source.append(row)

    def item(self, row):
        return self.knowledge.get_item_by_observation_id(row.observation_id)

    def test_recursive_context_retains_original_field_sources(self):
        original = self.append("window_context", 1000, payload={"window_title": "A", "browser_url": "https://example.test/a"})
        self.append("context_snapshot", 1001, uia_summary={"nodes": [{"name": "Body"}]})
        edit = self.append("input_text", 1002, payload={"operation": "insert", "inserted_text": "draft"})
        self.organizer.catch_up()
        self.assertEqual(self.item(edit).structured["context_field_sources"]["url"], [original.observation_id])

    def test_explicit_url_failure_blocks_old_page_even_at_same_timestamp(self):
        self.append("window_context", 1000, payload={"window_title": "A", "browser_url": "https://example.test/a"})
        failure = self.append("window_context", 1000, payload={"window_title": "B", "browser_url": None, "browser_url_status": "timeout"})
        edit = self.append("input_text", 1001, payload={"operation": "insert", "inserted_text": "draft"})
        restored = self.append("window_context", 1002, payload={"browser_url": "https://example.test/b"})
        later = self.append("input_text", 1003, payload={"operation": "delete", "removed_char_count": 2})
        self.organizer.catch_up()
        self.assertIsNone(self.item(failure).url)
        self.assertIsNone(self.item(edit).url)
        self.assertEqual(self.item(edit).title, "B")
        self.assertIn("url_unknown", self.item(edit).structured["activity"]["unknowns"])
        self.assertEqual(self.item(later).url, self.item(restored).url)

    def test_late_direct_url_supersedes_newer_inherited_context(self):
        self.append("window_context", 1000, payload={
            "window_title": "A", "browser_url": "https://example.test/a",
        })
        inherited = self.append("input_text", 1200,
                                payload={"operation": "insert", "inserted_text": "before"})
        late = self.append("window_context", 1100, payload={
            "window_title": "B", "browser_url": "https://example.test/b",
        })
        after = self.append("input_text", 1300,
                            payload={"operation": "insert", "inserted_text": "after"})
        self.organizer.catch_up()
        self.assertEqual(self.item(inherited).url, "https://example.test/a")
        self.assertEqual(self.item(late).url, "https://example.test/b")
        self.assertEqual(self.item(after).url, "https://example.test/b")
        self.assertEqual(self.item(after).structured["context_field_sources"]["url"],
                         [late.observation_id])

    def test_restart_does_not_inherit_previous_epoch_resource(self):
        self.append("window_context", 1000, payload={"browser_url": "https://example.test/old"})
        new = self.append("input_text", 1001, epoch="new-epoch", payload={"operation": "snapshot", "text": "draft"})
        self.organizer.catch_up()
        self.assertIsNone(self.item(new).url)

    def test_expired_failures_leave_queue_and_valid_retry_can_recover(self):
        valid = self.append("window_context", 1000, payload={"window_title": "Fixture"})
        for index in range(10):
            self.knowledge.record_projection_error(f"expired-{index}", index, "failed")
        self.knowledge.record_projection_error(valid.observation_id, valid.ingest_seq, "failed")
        self.knowledge.set_checkpoint(self.organizer.consumer_id, self.source.generation(), valid.ingest_seq)
        for _ in range(4):
            self.organizer.process_once()
        self.assertIsNotNone(self.item(valid))
        self.assertEqual(self.knowledge.list_projection_errors(retryable_only=True), [])
        self.assertEqual(len(self.knowledge.list_projection_errors()), 10)

    def test_failed_projection_is_visible_in_all_contexts(self):
        class FailingParser:
            def parse(self, item):
                raise ValueError("fixture failure")
        self.organizer.parser_registry = FailingParser()
        self.append("window_context", 1000, payload={"window_title": "Fixture"})
        self.organizer.catch_up()
        self.assertEqual(self.organizer.activity_summary(ContextQuery())["data_status"], "projection_incomplete")
        self.assertIn("projection_incomplete", self.organizer.context(ContextQuery()).gaps)
        self.assertEqual(self.knowledge.list_projection_errors()[0]["error_type"], "ValueError")

    def test_evidence_and_episode_query_reject_old_projection(self):
        row = self.append("window_context", 1000, payload={"window_title": "Fixture"})
        self.organizer.catch_up()
        item = self.item(row)
        evidence = self.knowledge.get_evidence(item.structured["activity"]["evidence_ids"])[0]
        self.knowledge.upsert_item(replace(item, item_id="old-projection", projection_version="activity.v1"),
                                   replace(evidence, evidence_id="old-evidence"))
        with self.assertRaisesRegex(ValueError, "rebuild"):
            self.organizer.get_evidence((evidence.evidence_id,))
        with self.assertRaisesRegex(ValueError, "rebuild"):
            self.organizer.get_episode(item.episode_id)


if __name__ == "__main__":
    unittest.main()
