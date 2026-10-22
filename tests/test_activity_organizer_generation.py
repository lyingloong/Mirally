"""Source replacement checks use isolated, real SQLite projections only."""

from __future__ import annotations

import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from mirally.core.activity import Observation, ObservationStore
from mirally.core.activity.knowledge import ActivityOrganizer, KnowledgeStore
from mirally.core.activity.store import CursorExpiredError


class OrganizerGenerationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="mirally source 中文 ")
        self.root = Path(self.temporary.name)
        self.source = ObservationStore(self.root / "raw.sqlite3")
        self.knowledge = KnowledgeStore(self.root / "knowledge.sqlite3")
        self.organizer = ActivityOrganizer(self.source, self.knowledge)
        self.sequence = 0

    def tearDown(self):
        self.knowledge.close()
        self.source.close()
        self.temporary.cleanup()

    def append(self):
        self.sequence += 1
        return self.source.append(Observation.create(kind="window_context", source_id="fixture",
            source_epoch="one", source_seq=self.sequence, boot_id="fixture-boot",
            observed_at_utc_ms=self.sequence, window_instance_id="fixture-window",
            payload={"window_title": "synthetic replacement fixture"}))

    def replace_generation(self):
        with closing(self.source._connect()) as connection:
            connection.execute("UPDATE schema_meta SET value='replacement' WHERE key='store_generation'")

    def assert_empty_projection(self):
        self.assertEqual(self.knowledge.checkpoint(self.organizer.consumer_id), ("", 0))
        for table in ("activity_items", "activity_evidence", "activity_episodes", "projection_errors"):
            self.assertEqual(self.knowledge._execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0)
        if self.knowledge.fts_available:
            self.assertEqual(self.knowledge._execute("SELECT COUNT(*) FROM activity_fts").fetchone()[0], 0)

    def test_replacement_during_source_read_commits_no_projection_or_checkpoint(self):
        self.append()
        read = self.source.read_since

        def replace_during_read(*args, **kwargs):
            self.replace_generation()
            return read(*args, **kwargs)

        with patch.object(self.source, "read_since", side_effect=replace_during_read):
            with self.assertRaisesRegex(CursorExpiredError, "generation changed"):
                self.organizer.process_once()
        self.assert_empty_projection()
        self.assertEqual(self.organizer.process_once().projected, 1)
        self.assertEqual(self.knowledge.checkpoint(self.organizer.consumer_id), ("replacement", 1))

    def test_replacement_during_retry_read_preserves_original_failure_and_checkpoint(self):
        row = self.append()
        generation = self.source.generation()
        self.knowledge.set_checkpoint(self.organizer.consumer_id, generation, row.ingest_seq)
        self.knowledge.record_projection_error(row.observation_id, row.ingest_seq, ValueError("fixture failure"),
                                              consumer_id=self.organizer.consumer_id)
        get = self.source.get

        def replace_during_retry(*args, **kwargs):
            self.replace_generation()
            return get(*args, **kwargs)

        with patch.object(self.source, "get", side_effect=replace_during_retry):
            with self.assertRaisesRegex(CursorExpiredError, "generation changed"):
                self.organizer.process_once()
        self.assertEqual(self.knowledge.health().item_count, 0)
        self.assertEqual(self.knowledge.checkpoint(self.organizer.consumer_id), (generation, row.ingest_seq))
        failures = self.knowledge.list_projection_errors(consumer_id=self.organizer.consumer_id)
        self.assertEqual(failures[0]["attempts"], 1)

    def test_replacement_after_first_projection_rolls_back_whole_batch(self):
        first, second = self.append(), self.append()
        project = self.organizer.project

        def replace_after_first(row):
            if row.observation_id == second.observation_id:
                self.replace_generation()
            return project(row)

        with patch.object(self.organizer, "project", side_effect=replace_after_first):
            with self.assertRaisesRegex(CursorExpiredError, "generation changed"):
                self.organizer.process_once()
        self.assert_empty_projection()
        self.assertIsNotNone(self.source.get(first.observation_id))
        self.assertIsNotNone(self.source.get(second.observation_id))

    def test_replacement_before_final_commit_rolls_back_checkpoint_and_floor(self):
        row = self.append()
        generation = self.source.generation()
        self.source.purge_memorized(store_generation=generation, through_ingest_seq=row.ingest_seq,
                                   before_utc_ms=10, observation_ids=[row.observation_id])
        self.append()
        checkpoint = self.knowledge.set_checkpoint
        calls = []

        def replace_after_checkpoint(*args, **kwargs):
            checkpoint(*args, **kwargs)
            calls.append(args)
            if len(calls) == 2:
                self.replace_generation()

        with patch.object(self.knowledge, "set_checkpoint", side_effect=replace_after_checkpoint):
            with self.assertRaisesRegex(CursorExpiredError, "generation changed"):
                self.organizer.process_once()
        self.assert_empty_projection()
        self.assertIsNone(self.knowledge._execute(
            "SELECT value FROM knowledge_meta WHERE key=?", ("retention_floor:" + generation,)).fetchone())


if __name__ == "__main__":
    unittest.main()
