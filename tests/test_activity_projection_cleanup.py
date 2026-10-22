"""Committed-memory retention removes derived raw copies without losing tails."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mirally.core.activity import Observation, ObservationStore
from mirally.core.activity.knowledge import (
    ActivityEpisode, ActivityItem, ActivityOrganizer, Evidence, KnowledgeStore, MemoryCandidate, MemoryFact,
)
from mirally.core.activity.knowledge.repository import ArchivedObservationError
from mirally.core.activity.knowledge.semantic import SemanticAnnotation


class ActivityProjectionCleanupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "活动 projection.sqlite3"
        self.store = KnowledgeStore(self.path)
        self.store.set_checkpoint("activity", "generation-a", 50)

    def tearDown(self) -> None:
        self.store.close()
        self.directory.cleanup()

    def put(self, item_id: str, *, episode: str | None = None, at: int = 100,
            text: str = "archivedsecret正文", sources: tuple[str, ...] | None = None,
            title: str | None = None) -> tuple[ActivityItem, Evidence]:
        ids = sources or ("source-" + item_id,)
        item = ActivityItem(
            item_id=item_id, kind="content", start_utc_ms=at, end_utc_ms=at,
            app_id="fixture.exe", window_instance_id="window", title=title or text,
            text_summary=text, structured={"uia_text": text, "source": {"ingest_seq": at}},
            asset_refs=("asset-" + item_id,), source_observation_ids=ids, episode_id=episode,
        )
        evidence = Evidence(
            evidence_id="evidence-" + item_id, source_observation_ids=ids,
            observed_at_utc_ms=at, received_at_utc_ms=at + 10,
            content_type="fixture", text=text, structured={"body": text}, asset_refs=item.asset_refs,
        )
        self.store.upsert_item(item, evidence)
        self.store.upsert_annotation(item_id, SemanticAnnotation(
            extractor_name="fixture", extractor_version="1", value={"body": text},
        ))
        return item, evidence

    def episode(self, episode_id: str, ids: tuple[str, ...], *, finalized: bool = False,
                text: str = "archivedsecret正文") -> None:
        self.store.upsert_episode(ActivityEpisode(
            episode_id=episode_id, start_utc_ms=100, end_utc_ms=300,
            primary_app="fixture.exe", primary_window_id="window", item_ids=ids,
            title=text, summary=text + " summary",
            finalized=finalized, projection_version="activity.v1",
        ))

    def purge(self, *ids: str) -> dict:
        return self.store.purge_memorized(
            ids, source_generation="generation-a", through_ingest_seq=400, before_utc_ms=400,
        )

    def count(self, table: str) -> int:
        return int(self.store._connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])

    def test_full_copy_cleanup_preserves_checkpoint_version_and_unrelated_tail(self) -> None:
        old, _ = self.put("old", episode="finished")
        tail, _ = self.put("tail", at=500, text="tailonly", episode="tail-episode")
        self.episode("finished", (old.item_id,), finalized=True)
        self.episode("tail-episode", (tail.item_id,), text="tailonly")
        self.store.record_projection_error(old.source_observation_ids[0], 1, ValueError("archivedsecret正文"))
        self.store.upsert_memory_candidate(MemoryCandidate(
            candidate_id="candidate-old", statement="archivedsecret正文",
            source_observation_ids=old.source_observation_ids,
        ))
        self.store.upsert_memory_fact(MemoryFact(
            fact_id="fact-old", statement="archivedsecret正文", source_episode_ids=("finished",),
        ))
        self.store.upsert_memory_candidate(MemoryCandidate(
            candidate_id="candidate-tail", statement="tailonly", source_observation_ids=tail.source_observation_ids,
        ))
        result = self.purge(*old.source_observation_ids)
        self.assertEqual((result["deleted_items"], result["deleted_evidence"],
                          result["deleted_annotations"], result["deleted_episodes"]), (1, 1, 1, 1))
        self.assertEqual((result["deleted_candidates"], result["deleted_facts"],
                          result["deleted_projection_errors"]), (1, 1, 1))
        self.assertEqual(self.store.query_items(query="archivedsecret"), [])
        self.assertEqual(self.store.get_evidence(("evidence-old",)), [])
        self.assertIsNone(self.store.get_episode("finished"))
        self.assertEqual(self.store.get_item_by_observation_id(tail.source_observation_ids[0]), tail)
        self.assertEqual(self.store.checkpoint("activity"), ("generation-a", 50))
        self.assertEqual(self.store.projection_versions(), ("activity.v1",))
        self.assertEqual([value.candidate_id for value in self.store.list_memory_candidates()], ["candidate-tail"])
        for table in ("activity_items", "activity_evidence", "semantic_annotations", "activity_episodes",
                      "memory_candidates", "memory_facts", "projection_errors"):
            rows = self.store._connection.execute(f"SELECT * FROM {table}").fetchall()
            self.assertNotIn("archivedsecret", repr([tuple(row) for row in rows]), table)

    def test_pruned_episode_keeps_tail_window_and_clears_old_description(self) -> None:
        old, _ = self.put("old", episode="open")
        tail, _ = self.put("tail", at=500, episode="open", text="tailonly", title="tail window")
        self.episode("open", (old.item_id, tail.item_id))
        result = self.purge(*old.source_observation_ids)
        episode = self.store.get_episode("open")
        self.assertEqual(episode.item_ids, (tail.item_id,))
        self.assertEqual((episode.primary_app, episode.primary_window_id, episode.finalized),
                         ("fixture.exe", "window", False))
        self.assertEqual(episode.title, "tail window")
        self.assertNotIn("archivedsecret", episode.summary)
        self.assertEqual(result["pruned_episodes"], 1)
        self.assertEqual(self.store.latest_window_context(window_id="window", at_utc_ms=600).item_id, tail.item_id)

    def test_empty_open_episode_retains_only_continuity_shell(self) -> None:
        old, _ = self.put("old", episode="open")
        self.episode("open", (old.item_id,))
        result = self.purge(*old.source_observation_ids)
        episode = self.store.find_open_episode(
            app_id="fixture.exe", window_id="window", start_utc_ms=350, idle_timeout_ms=1000,
        )
        self.assertEqual(episode.episode_id, "open")
        self.assertEqual((episode.item_ids, episode.title, episode.finalized), ((), "", False))
        stored_summary = self.store._connection.execute(
            "SELECT summary FROM activity_episodes WHERE episode_id='open'",
        ).fetchone()[0]
        self.assertIsNone(stored_summary)
        self.assertEqual(result["retained_open_episodes"], 1)

    def test_shared_item_survives_until_all_sources_commit_across_batches(self) -> None:
        item, _ = self.put("shared", sources=("source-old", "source-tail"))
        first = self.purge("source-old")
        self.assertEqual((first["deleted_items"], first["retained_shared_items"]), (0, 1))
        self.assertIsNotNone(self.store.get_item(item.item_id))
        self.assertEqual(self.store.compact_memorized()["retained_shared_items"], 1)
        second = self.purge("source-tail")
        self.assertEqual(second["deleted_items"], 1)
        self.assertIsNone(self.store.get_item(item.item_id))
        self.assertEqual(self.count("activity_item_sources"), 0)
        self.assertEqual(self.store.compact_memorized()["retained_shared_items"], 0)

    def test_stale_writer_cannot_restore_body_or_episode_and_error_retry(self) -> None:
        stale, evidence = self.put("old", episode="finished")
        self.episode("finished", (stale.item_id,), finalized=True)
        with KnowledgeStore(self.path) as other:
            self.purge(*stale.source_observation_ids)
            with self.assertRaises(ArchivedObservationError):
                with other.transaction():
                    other.upsert_episode(ActivityEpisode(
                        episode_id="finished", start_utc_ms=100, end_utc_ms=100,
                        item_ids=(stale.item_id,), summary="archivedsecret正文",
                    ))
                    other.upsert_item(stale, evidence)
            other.record_projection_error(stale.source_observation_ids[0], 100, RuntimeError("stale body"))
        self.assertIsNone(self.store.get_episode("finished"))
        self.assertEqual(self.count("activity_items"), 0)
        self.assertEqual(self.count("projection_errors"), 0)

    def test_unprojected_receipt_prevents_late_projection_and_fact_reinsertion(self) -> None:
        self.purge("source-never-projected")
        self.assertTrue(self.store.is_archived_observation("source-never-projected"))
        with self.assertRaises(ArchivedObservationError):
            self.put("late", sources=("source-never-projected",))
        with self.assertRaises(ArchivedObservationError):
            self.store.upsert_memory_candidate(MemoryCandidate(
                candidate_id="late", statement="old body", source_observation_ids=("source-never-projected",),
            ))
        with self.assertRaises(ArchivedObservationError):
            self.store.upsert_memory_fact(MemoryFact(
                fact_id="late", statement="old body", source_observation_ids=("source-never-projected",),
            ))
        self.assertEqual(self.count("activity_items"), 0)

    def test_cleanup_is_idempotent_and_rolls_back_with_outer_transaction(self) -> None:
        old, _ = self.put("old")
        with self.assertRaisesRegex(RuntimeError, "rollback"):
            with self.store.transaction():
                self.purge(*old.source_observation_ids)
                raise RuntimeError("rollback")
        self.assertIsNotNone(self.store.get_item(old.item_id))
        self.assertFalse(self.store.is_archived_observation(old.source_observation_ids[0]))
        self.assertEqual(self.purge(*old.source_observation_ids)["deleted_items"], 1)
        self.assertEqual(self.purge(*old.source_observation_ids)["deleted_items"], 0)
        self.assertEqual(self.store.checkpoint("activity"), ("generation-a", 50))

    def test_invalid_generation_and_receipts_leave_live_data_unchanged(self) -> None:
        old, _ = self.put("old")
        with self.assertRaisesRegex(ValueError, "generation"):
            self.store.purge_memorized(old.source_observation_ids, source_generation="generation-b")
        for ids in ("source-old", ("",), tuple(str(index) for index in range(501))):
            with self.assertRaises(ValueError):
                self.store.purge_memorized(ids)
        self.assertIsNotNone(self.store.get_item(old.item_id))
        self.assertFalse(self.store.is_archived_observation(old.source_observation_ids[0]))

    def test_confirmed_continuous_floor_advances_only_lagging_matching_consumers(self) -> None:
        self.store.set_checkpoint("lagging", "generation-a", 5)
        self.store.set_checkpoint("new", "", 0)
        self.store.set_checkpoint("other", "generation-b", 2)
        result = self.store.advance_retention_floor("generation-a", 20)
        self.assertEqual(result["advanced_checkpoints"], 2)
        self.assertEqual(self.store.checkpoint("lagging"), ("generation-a", 20))
        self.assertEqual(self.store.checkpoint("new"), ("generation-a", 20))
        self.assertEqual(self.store.checkpoint("activity"), ("generation-a", 50))
        self.assertEqual(self.store.checkpoint("other"), ("generation-b", 2))
        # A previously read batch completing after retention cannot regress cp.
        self.store.set_checkpoint("lagging", "generation-a", 7)
        self.store.set_checkpoint("activity", "generation-a", 25)
        self.assertEqual(self.store.checkpoint("lagging"), ("generation-a", 20))
        self.assertEqual(self.store.checkpoint("activity"), ("generation-a", 50))
        self.assertEqual(self.store.advance_retention_floor("generation-a", 10)["retention_floor"], 20)
        gap = json.loads(self.store._connection.execute(
            "SELECT value FROM knowledge_meta WHERE key='retention_gap'",
        ).fetchone()[0])
        self.assertEqual(gap["retention_floor"], 20)

    def test_read_only_cleanup_rejects_all_mutations(self) -> None:
        with KnowledgeStore(self.path, read_only=True) as reader:
            for action in (lambda: reader.purge_memorized(()),
                           lambda: reader.advance_retention_floor("generation-a", 1),
                           reader.compact_memorized):
                with self.assertRaises(PermissionError):
                    action()

    def test_real_organizer_skips_stale_raw_snapshot_and_rebuild_then_projects_tail(self) -> None:
        directory = Path(self.directory.name)
        with ObservationStore(directory / "raw.sqlite3") as source, KnowledgeStore(directory / "live.sqlite3") as live:
            organizer = ActivityOrganizer(source, live)
            old = source.append(Observation.create(
                kind="input_text", source_id="fixture", source_epoch="epoch", source_seq=1,
                boot_id="boot", window_instance_id="window", app_id="fixture.exe",
                observed_at_utc_ms=100, payload={"inserted_text": "archivedsecret正文", "operation": "insert"},
            ))
            stale_snapshot = source.read_since(0)[0][0]
            organizer.catch_up()
            live.purge_memorized((old.observation_id,), source_generation=source.generation())
            self.assertFalse(organizer._process_one(stale_snapshot))
            self.assertEqual(live.health(organizer.consumer_id).item_count, 0)
            # A deliberate projection rebuild cannot restore journaled sources
            # while the raw deletion phase is still pending.
            live.reset()
            tail = source.append(Observation.create(
                kind="input_text", source_id="fixture", source_epoch="epoch", source_seq=2,
                boot_id="boot", window_instance_id="window", app_id="fixture.exe",
                observed_at_utc_ms=500, payload={"inserted_text": "tailonly", "operation": "insert"},
            ))
            stats = organizer.catch_up()
            self.assertEqual((stats.projected, stats.skipped, stats.errors), (1, 1, 0))
            self.assertIsNone(live.get_item_by_observation_id(old.observation_id))
            self.assertIsNotNone(live.get_item_by_observation_id(tail.observation_id))
            self.assertEqual(live.checkpoint(organizer.consumer_id), (source.generation(), tail.ingest_seq))
            self.assertEqual(live.query_items(query="archivedsecret"), [])

    def test_new_organizer_resumes_actual_raw_floor_without_resetting_tail(self) -> None:
        directory = Path(self.directory.name)
        with ObservationStore(directory / "raw-floor.sqlite3") as source, KnowledgeStore(directory / "floor.sqlite3") as live:
            old = source.append(Observation.create(
                kind="window_context", source_id="fixture", source_epoch="epoch", source_seq=1,
                boot_id="boot", observed_at_utc_ms=100, payload={"window_title": "archivedsecret正文"},
            ))
            tail = source.append(Observation.create(
                kind="window_context", source_id="fixture", source_epoch="epoch", source_seq=2,
                boot_id="boot", observed_at_utc_ms=500, payload={"window_title": "tailonly"},
            ))
            live.purge_memorized((old.observation_id,), source_generation=source.generation())
            receipt = source.purge_memorized(
                store_generation=source.generation(), through_ingest_seq=old.ingest_seq,
                before_utc_ms=400, observation_ids=(old.observation_id,),
            )
            self.assertEqual(receipt["retention_floor"], old.ingest_seq)
            organizer = ActivityOrganizer(source, live, consumer_id="new-consumer")
            stats = organizer.catch_up()
            self.assertEqual((stats.projected, stats.errors), (1, 0))
            self.assertEqual(live.checkpoint("new-consumer"), (source.generation(), tail.ingest_seq))
            self.assertEqual(live.health("new-consumer").item_count, 1)
            self.assertIsNotNone(live.get_item_by_observation_id(tail.observation_id))

    def test_compaction_reclaims_large_body_and_fts_pages(self) -> None:
        marker = "unique_archived_body_长正文_"
        old, _ = self.put("huge", text=marker * 30_000)
        self.put("tail", at=500, text="tailonly")
        self.store.compact_memorized()
        before = self.path.stat().st_size
        self.purge(*old.source_observation_ids)
        result = self.store.compact_memorized()
        self.assertGreater(result["reclaimed_bytes"], before // 2)
        self.assertLess(self.path.stat().st_size, before // 2)
        self.assertNotIn(marker.encode("utf-8"), self.path.read_bytes())
        self.assertEqual([item.item_id for item in self.store.query_items(query="tailonly")], ["tail"])
        self.assertEqual(self.store.query_items(query="unique_archived_body"), [])
        with self.store.transaction():
            with self.assertRaisesRegex(RuntimeError, "inside a transaction"):
                self.store.compact_memorized()

    def test_compaction_reports_busy_snapshot_and_retries_after_reader_closes(self) -> None:
        old, _ = self.put("old", text="retainedsnapshot正文" * 10_000)
        self.put("tail", at=500, text="tailonly")
        self.store.compact_memorized()
        reader = sqlite3.connect(str(self.path))
        try:
            reader.execute("BEGIN")
            reader.execute("SELECT * FROM activity_items").fetchall()
            self.purge(*old.source_observation_ids)
            self.store._connection.execute("PRAGMA busy_timeout=1")
            wal_path = Path(str(self.path) + "-wal")
            pinned_wal_bytes = wal_path.stat().st_size
            for _ in range(4):
                deferred = self.store.compact_memorized()
                self.assertTrue(deferred["compaction_deferred"])
                self.assertGreater(deferred["wal_bytes_after"], 0)
                self.assertLessEqual(deferred["wal_bytes_after"], pinned_wal_bytes)
            self.assertEqual([value.item_id for value in self.store.query_items()], ["tail"])
        finally:
            reader.rollback()
            reader.close()
        completed = self.store.compact_memorized()
        self.assertFalse(completed["compaction_deferred"])
        self.assertEqual(completed["wal_bytes_after"], 0)
        self.assertNotIn("retainedsnapshot正文".encode("utf-8"), self.path.read_bytes())

    def test_interrupted_compaction_removes_progress_handler_and_can_retry(self) -> None:
        self.put("tail", text="tailonly")
        execute = self.store._execute

        def interrupt_vacuum(sql, params=()):
            if sql == "VACUUM":
                raise sqlite3.OperationalError("interrupted")
            return execute(sql, params)

        with patch.object(self.store, "_execute", side_effect=interrupt_vacuum):
            receipt = self.store.compact_memorized()
        self.assertTrue(receipt["compaction_deferred"])
        self.assertIn("time limit", receipt["error"])
        # A leaked deadline callback would now interrupt an ordinary query.
        with patch("mirally.core.activity.knowledge.repository.time.monotonic", return_value=float("inf")):
            result = self.store._connection.execute(
                "WITH RECURSIVE n(x) AS (SELECT 0 UNION ALL SELECT x+1 FROM n WHERE x<5000) SELECT SUM(x) FROM n",
            ).fetchone()[0]
        self.assertEqual(result, 12_502_500)
        self.assertFalse(self.store.compact_memorized()["compaction_deferred"])
        self.assertEqual([value.item_id for value in self.store.query_items(query="tailonly")], ["tail"])


if __name__ == "__main__":
    unittest.main()
