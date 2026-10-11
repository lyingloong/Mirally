from __future__ import annotations

import hashlib
import sqlite3
import tempfile
import unittest
from pathlib import Path

from openassist.core.activity import Observation, ObservationStore
from openassist.core.activity.knowledge import ActivityOrganizer, KnowledgeStore


class ActivityBrowserQueryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        self.raw_path = root / "observations.sqlite3"
        self.knowledge_path = root / "activity-knowledge.sqlite3"
        self.source = ObservationStore(self.raw_path)
        self.sequence = 0

    def tearDown(self) -> None:
        self.source.close()
        self.directory.cleanup()

    def append(self, kind: str, at: int, *, app: str = "fixture.exe", window: str = "window-a", payload=None):
        value = Observation.create(
            kind=kind,
            source_id="fixture",
            source_epoch="epoch",
            source_seq=self.sequence,
            boot_id="boot",
            observed_at_utc_ms=at,
            app_id=app,
            window_instance_id=window,
            payload=payload or {},
        )
        self.sequence += 1
        return self.source.append(value)

    @staticmethod
    def digest(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def test_raw_query_supports_literal_text_filters_and_stable_direction(self) -> None:
        first = self.append("window_context", 1000, payload={"window_title": "100% ready"})
        second = self.append("window_context", 1001, app="editor.exe", payload={"window_title": "Draft _one_"})
        third = self.append("ocr_result", 1002, payload={"ocr_text": "noise"})

        self.assertEqual([row.observation_id for row in self.source.query(query="100%")], [first.observation_id])
        self.assertEqual([row.observation_id for row in self.source.query(app_ids=["editor.exe"])], [second.observation_id])
        self.assertEqual(
            [row.observation_id for row in self.source.query(before_ingest_seq=third.ingest_seq, descending=True)],
            [second.observation_id, first.observation_id],
        )
        self.assertEqual(
            [row.observation_id for row in self.source.query(after_ingest_seq=first.ingest_seq)],
            [second.observation_id, third.observation_id],
        )

    def test_episode_query_filters_items_and_paginates_newest_first(self) -> None:
        self.append("window_context", 1000, payload={"window_title": "Alpha"})
        self.append("foreground_changed", 1001, window="window-b", app="editor.exe", payload={"window_title": "Beta"})
        self.append("window_context", 1002, window="window-c", app="browser.exe", payload={"window_title": "Gamma"})
        self.source.close()
        with ObservationStore(self.raw_path, read_only=True) as source, KnowledgeStore(self.knowledge_path) as knowledge:
            organizer = ActivityOrganizer(source, knowledge)
            organizer.catch_up()
            all_episodes = knowledge.query_episodes(limit=100)
            self.assertGreaterEqual(len(all_episodes), 3)
            self.assertEqual(
                [episode.start_utc_ms for episode in all_episodes],
                sorted((episode.start_utc_ms for episode in all_episodes), reverse=True),
            )
            editor = knowledge.query_episodes(apps=("editor.exe",), limit=100)
            self.assertEqual(len(editor), 1)
            self.assertEqual(editor[0].primary_app, "editor.exe")
            by_kind = knowledge.query_episodes(kinds=("foreground_changed",), limit=100)
            self.assertEqual(len(by_kind), 1)
            by_text = knowledge.query_episodes(query="Gamma", windows=("window-c",), limit=100)
            self.assertEqual(len(by_text), 1)
            first_page = knowledge.query_episodes(limit=1)
            second_page = knowledge.query_episodes(limit=1, after_episode_id=first_page[0].episode_id)
            self.assertEqual(len(second_page), 1)
            self.assertNotEqual(first_page[0].episode_id, second_page[0].episode_id)
        # Reopen the source writer for tearDown's idempotent close path.
        self.source = ObservationStore(self.raw_path)

    def test_knowledge_read_only_does_not_initialize_or_mutate_database(self) -> None:
        self.append("window_context", 1000, payload={"window_title": "Stable"})
        self.source.close()
        with KnowledgeStore(self.knowledge_path) as knowledge:
            with ObservationStore(self.raw_path, read_only=True) as source:
                ActivityOrganizer(source, knowledge).catch_up()
        before = self.digest(self.knowledge_path)
        with KnowledgeStore(self.knowledge_path, read_only=True) as reader:
            self.assertTrue(reader.read_only)
            self.assertEqual(reader.health().schema_version, 3)
            self.assertTrue(reader.query_items(limit=10))
            self.assertEqual(reader.fts_available, bool(reader._connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='activity_fts'"
            ).fetchone()))
        self.assertEqual(self.digest(self.knowledge_path), before)
        self.source = ObservationStore(self.raw_path)

    def test_knowledge_read_only_rejects_projection_mutations_before_sqlite(self) -> None:
        self.append("window_context", 1000, payload={"window_title": "Stable"})
        self.source.close()
        with KnowledgeStore(self.knowledge_path) as knowledge:
            with ObservationStore(self.raw_path, read_only=True) as source:
                ActivityOrganizer(source, knowledge).catch_up()
        with KnowledgeStore(self.knowledge_path, read_only=True) as reader:
            mutations = (
                lambda: reader.set_checkpoint("fixture", "generation", 1),
                lambda: reader.upsert_item(None, None),
                lambda: reader.attach_item_episode("item", "episode"),
                lambda: reader.upsert_annotation("item", None),
                lambda: reader.record_projection_error("observation", 1, RuntimeError("fixture")),
                lambda: reader.clear_projection_error("observation"),
                lambda: reader.finalize_episodes(1000),
                lambda: reader.finalize_for_gap(1000, ("restart",)),
                lambda: reader.upsert_episode(None),
                lambda: reader.upsert_memory_candidate(None),
                lambda: reader.set_memory_status("candidate", "accepted"),
                lambda: reader.upsert_memory_fact(None),
                lambda: reader.set_memory_fact_status("fact", "active"),
                lambda: reader.reset(),
            )
            for mutate in mutations:
                with self.subTest(mutate=mutate):
                    with self.assertRaisesRegex(PermissionError, "read-only"):
                        mutate()
        self.source = ObservationStore(self.raw_path)

    def test_read_only_missing_database_has_actionable_error(self) -> None:
        self.source.close()
        with self.assertRaisesRegex(FileNotFoundError, "knowledge-process"):
            KnowledgeStore(self.knowledge_path, read_only=True)
        self.source = ObservationStore(self.raw_path)

    def test_read_only_old_schema_does_not_attempt_migration(self) -> None:
        self.source.close()
        connection = sqlite3.connect(str(self.knowledge_path))
        try:
            connection.execute("CREATE TABLE knowledge_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            connection.execute("INSERT INTO knowledge_meta VALUES('schema_version', '1')")
            connection.commit()
        finally:
            connection.close()
        before = self.digest(self.knowledge_path)
        with self.assertRaisesRegex(RuntimeError, "unsupported knowledge schema 1.*knowledge-process"):
            KnowledgeStore(self.knowledge_path, read_only=True)
        self.assertEqual(self.digest(self.knowledge_path), before)
        self.source = ObservationStore(self.raw_path)


if __name__ == "__main__":
    unittest.main()
