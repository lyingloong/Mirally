"""Memory-confirmed raw retention uses only isolated stores and fixture files."""

from __future__ import annotations

import hashlib
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from unittest.mock import patch

from mirally.core.activity.models import Observation
from mirally.core.activity.store import CursorExpiredError, ObservationStore


class MemorizedCleanupTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="mirally 清理 ")
        self.root = Path(self.directory.name)
        self.path = self.root / "活动.sqlite3"
        self.assets = self.root / "图片 assets"
        self.store = ObservationStore(self.path, asset_directory=self.assets)
        self.sequence = 0

    def tearDown(self):
        self.store.close()
        self.directory.cleanup()

    def observation(self, *, at=100, refs=(), body="fixture body"):
        self.sequence += 1
        return Observation.create(
            kind="context_snapshot", source_id="fixture", source_epoch="one",
            source_seq=self.sequence, boot_id="fixture-boot", observed_at_utc_ms=at,
            asset_refs=refs, payload={"raw_body": body},
            uia_summary={"body": body}, ocr_summary={"text": body},
        )

    def append(self, **kwargs):
        return self.store.append(self.observation(**kwargs))

    def image(self, *, at=100, body=b"fixture screenshot"):
        reservation = self.store.reserve_asset(mime_type="image/png", scope="activity-content", policy_version=1)
        observation = self.store.commit_asset_bytes(reservation, body,
            self.observation(at=at, refs=(reservation.asset_id,)))
        return reservation, observation

    def purge(self, *, through=100, before=200, ids=None):
        return self.store.purge_memorized(store_generation=self.store.generation(),
            through_ingest_seq=through, before_utc_ms=before, observation_ids=ids)

    def sql(self, statement, args=()):
        connection = self.store._connect()
        try:
            return connection.execute(statement, args).fetchall()
        finally:
            connection.close()

    def test_collector_age_expiry_and_quota_cleanup_preserve_semantic_pending_evidence(self):
        image, old = self.image(at=1)
        text = self.append(at=1, body="unprocessed OCR and input")
        self.store._submit("fail_asset", image.asset_id, "expired")
        # Restore ready with an already expired deadline, without invoking the
        # semantic-authorized purge path.
        connection = self.store._connect()
        try:
            connection.execute("UPDATE assets SET status='ready',expires_at_utc_ms=1 WHERE asset_id=?", (image.asset_id,))
            connection.commit()
        finally:
            connection.close()
        receipt = self.store.cleanup(now_utc_ms=2_000_000_000, observation_retention_days=1,
                                     max_asset_bytes=0, preserve_observations=True)
        self.assertEqual(receipt["observations_deleted"], 0)
        self.assertEqual(receipt["assets_finalized"], 0)
        self.assertIsNotNone(self.store.get(old.observation_id))
        self.assertIsNotNone(self.store.get(text.observation_id))
        self.assertTrue(image.final_path.exists())
        self.assertEqual(self.store.asset(image.asset_id)["status"], "ready")

    def test_three_boundaries_preserve_cutoff_and_new_ingest_tail(self):
        image, old = self.image(at=100)
        image.temp_path.write_bytes(b"abandoned temp for confirmed image")
        cutoff = self.append(at=200)
        new_tail = self.append(at=50)
        generation = self.store.generation()
        result = self.purge(through=cutoff.ingest_seq, before=200)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["observations_deleted"], 1)
        self.assertEqual(result["assets_deleted"], 1)
        self.assertEqual(result["files_deleted"], 2)
        self.assertIsNone(self.store.get(old.observation_id))
        self.assertIsNotNone(self.store.get(cutoff.observation_id))
        self.assertIsNotNone(self.store.get(new_tail.observation_id))
        self.assertFalse(image.final_path.exists())
        self.assertFalse(image.temp_path.exists())
        self.assertIsNone(self.store.asset(image.asset_id))
        self.assertEqual(self.store.generation(), generation)
        self.assertEqual(self.store.get_state("memorized_retention")["retention_floor"], old.ingest_seq)

    def test_generation_mismatch_and_invalid_arguments_do_not_delete(self):
        old = self.append()
        with self.assertRaisesRegex(ValueError, "generation changed"):
            self.store.purge_memorized(store_generation="other-store", through_ingest_seq=100, before_utc_ms=200)
        self.assertIsNotNone(self.store.get(old.observation_id))
        self.assertIsNone(self.store.get_state("memorized_retention"))
        for arguments in ({"through_ingest_seq": True}, {"through_ingest_seq": -1},
                          {"before_utc_ms": True}, {"before_utc_ms": -1},
                          {"store_generation": ""}, {"observation_ids": "one"},
                          {"observation_ids": [""]}, {"observation_ids": ["x"] * 501}):
            actual = {"store_generation": self.store.generation(), "through_ingest_seq": 100, "before_utc_ms": 200}
            actual.update(arguments)
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                self.store.purge_memorized(**actual)
        self.assertIsNotNone(self.store.get(old.observation_id))

    def test_exact_batch_subset_never_deletes_other_confirmable_rows(self):
        first = self.append(at=1)
        second = self.append(at=1)
        cutoff = self.append(at=200)
        result = self.purge(through=second.ingest_seq, ids=[first.observation_id, cutoff.observation_id])
        self.assertEqual(result["observations_deleted"], 1)
        self.assertEqual(result["retention_floor"], first.ingest_seq)
        self.assertIsNotNone(self.store.get(second.observation_id))
        self.assertIsNotNone(self.store.get(cutoff.observation_id))
        self.assertEqual(set(result["observation_ids"]), {first.observation_id, cutoff.observation_id})
        again = self.purge(through=second.ingest_seq, ids=[first.observation_id, cutoff.observation_id])
        self.assertEqual(again["observations_deleted"], 1)
        self.assertEqual(again["files_deleted"], 0)
        self.assertIsNotNone(self.store.get(second.observation_id))
        self.assertEqual(self.purge(through=second.ingest_seq, ids=[])["observations_deleted"], 0)

    def test_shared_asset_stays_available_and_owner_is_detached(self):
        image, owner = self.image()
        retained = self.append(at=500, refs=(image.asset_id,))
        result = self.purge(through=retained.ingest_seq)
        self.assertEqual(result["assets_considered"], 0)
        self.assertIsNone(self.store.get(owner.observation_id))
        self.assertIsNotNone(self.store.get(retained.observation_id))
        asset = self.store.asset(image.asset_id)
        self.assertEqual(asset["status"], "ready")
        self.assertIsNone(asset["observation_id"])
        self.assertEqual(image.final_path.read_bytes(), b"fixture screenshot")
        self.assertEqual(self.store.resolve_asset(image.asset_id)["status"], "available")
        self.purge(through=retained.ingest_seq, before=600)
        self.assertFalse(image.final_path.exists())
        self.assertIsNone(self.store.asset(image.asset_id))

    def test_retained_owner_protects_asset_even_without_explicit_tail_ref(self):
        image, owner = self.image(at=500)
        selected = self.append(at=1, refs=(image.asset_id,))
        self.purge(through=selected.ingest_seq)
        self.assertIsNone(self.store.get(selected.observation_id))
        self.assertIsNotNone(self.store.get(owner.observation_id))
        self.assertEqual(self.store.asset(image.asset_id)["observation_id"], owner.observation_id)
        self.assertTrue(image.final_path.exists())

    def test_owned_pending_assets_are_removed_but_unattributed_reservations_stay(self):
        owned = self.store.reserve_asset(mime_type="image/png", scope="fixture", policy_version=1)
        orphan = self.store.reserve_asset(mime_type="image/png", scope="fixture", policy_version=1)
        owned.temp_path.write_bytes(b"owned pending")
        owned.final_path.write_bytes(b"uncommitted final")
        orphan.temp_path.write_bytes(b"not proven memorized")
        old = self.append(refs=(owned.asset_id,))
        result = self.purge(through=old.ingest_seq)
        self.assertEqual(result["assets_deleted"], 1)
        self.assertFalse(owned.temp_path.exists())
        self.assertFalse(owned.final_path.exists())
        self.assertTrue(orphan.temp_path.exists())
        self.assertEqual(self.store.asset(orphan.asset_id)["status"], "pending")

    def test_file_failure_journal_survives_restart_and_can_retry_without_raw_bodies(self):
        image, old = self.image()
        image.temp_path.write_bytes(b"locked temporary fixture")
        unlink = Path.unlink

        def fail_temp(path, *args, **kwargs):
            if path == image.temp_path:
                raise PermissionError("fixture file locked")
            return unlink(path, *args, **kwargs)

        with patch.object(Path, "unlink", fail_temp):
            result = self.purge(through=old.ingest_seq)
        self.assertEqual(result["status"], "pending")
        self.assertFalse(result["logical_complete"])
        self.assertEqual(result["assets_pending"], 1)
        self.assertEqual(result["reclamation"]["file_reclaimed_bytes"], len(b"fixture screenshot"))
        self.assertIsNone(self.store.get(old.observation_id))
        self.assertFalse(image.final_path.exists())
        self.assertTrue(image.temp_path.exists())
        self.assertEqual(self.store.asset(image.asset_id)["status"], "purging")
        state = self.store.get_state("memorized_retention")
        self.assertIn("fixture file locked", state["errors"][0]["error"])
        self.assertNotIn("fixture body", str(state))
        self.assertEqual(len(self.sql("SELECT * FROM memorized_purge_assets")), 1)
        with self.assertRaisesRegex(ValueError, "reserved"):
            self.append(refs=(image.asset_id,))
        generation = self.store.generation()
        self.store.close()
        self.store = ObservationStore(self.path, asset_directory=self.assets)
        self.assertEqual(self.store.generation(), generation)
        completed = self.purge(through=old.ingest_seq)
        self.assertEqual(completed["status"], "complete")
        self.assertEqual(completed["observations_deleted"], 1)
        self.assertEqual(completed["reclamation"]["file_reclaimed_bytes"], len(b"locked temporary fixture"))
        self.assertFalse(image.temp_path.exists())
        self.assertIsNone(self.store.asset(image.asset_id))
        self.assertEqual(self.sql("SELECT * FROM memorized_purge_assets"), [])
        self.assertEqual(self.purge(through=old.ingest_seq)["reclamation"]["file_reclaimed_bytes"], 0)

    def test_unsafe_asset_path_is_rejected_and_outside_files_survive(self):
        image, old = self.image()
        outside = self.root / "outside.bin"
        outside.write_bytes(b"outside asset directory")
        outside.with_name(outside.name + ".pending").write_bytes(b"outside pending")
        self.sql("UPDATE assets SET relative_path='../outside.bin' WHERE asset_id=?", (image.asset_id,))
        result = self.purge(through=old.ingest_seq)
        self.assertEqual(result["status"], "pending")
        self.assertEqual(result["assets_pending"], 1)
        self.assertEqual(outside.read_bytes(), b"outside asset directory")
        self.assertTrue(outside.with_name(outside.name + ".pending").exists())
        self.assertTrue(image.final_path.exists())
        self.assertIn("ValueError", result["errors"][0]["error"])

    def test_interruption_between_database_and_file_phases_can_recover(self):
        image, old = self.image()
        unlink = Path.unlink

        def interrupt(path, *args, **kwargs):
            if path == image.final_path:
                raise KeyboardInterrupt("fixture process interrupted")
            return unlink(path, *args, **kwargs)

        with patch.object(Path, "unlink", interrupt), self.assertRaises(KeyboardInterrupt):
            self.purge(through=old.ingest_seq)
        self.assertIsNone(self.store.get(old.observation_id))
        self.assertTrue(image.final_path.exists())
        self.assertEqual(self.store.get_state("memorized_retention")["status"], "pending")
        self.store.close()
        self.store = ObservationStore(self.path, asset_directory=self.assets)
        result = self.purge(through=old.ingest_seq)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["observations_deleted"], 1)
        self.assertFalse(image.final_path.exists())

    def test_new_observation_can_be_appended_during_file_phase_and_survives(self):
        image, old = self.image()
        unlink = Path.unlink
        tail = []

        def append_during_delete(path, *args, **kwargs):
            if path == image.final_path and not tail:
                tail.append(self.append(at=1, body="new concurrent capture"))
            return unlink(path, *args, **kwargs)

        with patch.object(Path, "unlink", append_during_delete):
            result = self.purge(through=old.ingest_seq)
        self.assertEqual(result["observations_deleted"], 1)
        self.assertEqual(len(tail), 1)
        self.assertGreater(tail[0].ingest_seq, old.ingest_seq)
        self.assertEqual(self.store.get(tail[0].observation_id).payload["raw_body"], "new concurrent capture")

    def test_persistent_floor_and_sequence_survive_empty_store_restart(self):
        first = self.append()
        generation = self.store.generation()
        result = self.purge(through=first.ingest_seq)
        self.assertEqual(result["retention_floor"], first.ingest_seq)
        self.assertEqual(self.store.read_since(first.ingest_seq)[0], [])
        with self.assertRaises(CursorExpiredError):
            self.store.read_since(0)
        with self.assertRaises(CursorExpiredError):
            self.store.ack("late", "fixture", 0)
        self.store.close()
        self.store = ObservationStore(self.path, asset_directory=self.assets)
        self.assertEqual(self.store.generation(), generation)
        with self.assertRaises(CursorExpiredError):
            self.store.read_since(0)
        later = self.append()
        self.assertGreater(later.ingest_seq, first.ingest_seq)
        self.assertEqual(self.store.read_since(first.ingest_seq)[0][0].observation_id, later.observation_id)

    def test_sqlite_and_wal_reclaim_body_storage_without_content_tombstones(self):
        marker = "MEMORIZED_RAW_BODY_" + hashlib.sha256(b"fixture marker").hexdigest()
        old = self.append(body=marker * 16000)
        before = self.store.health().db_bytes + self.store.health().wal_bytes
        result = self.purge(through=old.ingest_seq)
        self.assertEqual(result["status"], "complete")
        self.assertTrue(result["reclamation"]["vacuumed"])
        after = self.store.health().db_bytes + self.store.health().wal_bytes
        self.assertLess(after, before // 2)
        for path in (self.path, self.path.with_name(self.path.name + "-wal")):
            if path.exists():
                self.assertNotIn(marker.encode(), path.read_bytes())
        self.assertEqual(self.sql("SELECT COUNT(*) FROM observations")[0][0], 0)
        self.assertNotIn(marker, str(self.store.get_state("memorized_retention")))

    def test_reclaimed_bytes_include_only_successful_files_and_actual_database_delta(self):
        screenshot = b"fixture screenshot pixels" * 2000
        temporary = b"fixture pending pixels" * 1000
        image, old = self.image(body=screenshot)
        image.temp_path.write_bytes(temporary)
        raw = self.append(body="fixture raw UIA text" * 50000)
        before = self.store.health()
        before_database_bytes = before.db_bytes + before.wal_bytes
        result = self.purge(through=raw.ingest_seq)
        after = self.store.health()
        database_delta = max(0, before_database_bytes - after.db_bytes - after.wal_bytes)
        reclamation = result["reclamation"]
        self.assertEqual(reclamation["file_reclaimed_bytes"], len(screenshot) + len(temporary))
        self.assertEqual(reclamation["database_reclaimed_bytes"], database_delta)
        self.assertEqual(reclamation["reclaimed_bytes"], len(screenshot) + len(temporary) + database_delta)
        self.assertGreater(database_delta, 100000)
        self.assertGreater(self.store.get_state("memorized_retention")["reclamation"]["reclaimed_bytes"], 0)

    def test_older_filesystem_batch_cannot_regress_newer_continuous_floor(self):
        image, first = self.image(at=1)
        second = self.append(at=2)
        waiting, release = Event(), Event()
        unlink = Path.unlink

        def block_first_file(path, *args, **kwargs):
            if path == image.final_path:
                waiting.set()
                if not release.wait(15):
                    raise TimeoutError("fixture did not release the older purge")
            return unlink(path, *args, **kwargs)

        with patch.object(Path, "unlink", block_first_file), ThreadPoolExecutor(max_workers=1) as pool:
            older = pool.submit(self.purge, through=second.ingest_seq, ids=[first.observation_id])
            try:
                self.assertTrue(waiting.wait(15))
                newer = self.purge(through=second.ingest_seq, ids=[second.observation_id])
                self.assertEqual(newer["retention_floor"], second.ingest_seq)
            finally:
                release.set()
            completed = older.result(timeout=15)
        receipt = self.store.get_state("memorized_retention")
        self.assertEqual(completed["retention_floor"], second.ingest_seq)
        self.assertEqual(receipt["retention_floor"], second.ingest_seq)
        self.assertEqual(receipt["retention_ingest_seq"], second.ingest_seq)
        self.assertEqual(receipt["watermark"]["retention_floor"], second.ingest_seq)
        self.assertEqual(self.store.read_since(receipt["retention_floor"]), ([], second.ingest_seq, False))
        tail = self.append(at=500)
        self.assertEqual(self.store.read_since(receipt["retention_floor"])[0], [tail])

    def test_generation_change_before_finish_preserves_replacement_receipt(self):
        old = self.append()
        original_submit = self.store._submit
        replacement = {"store_generation": "replacement", "retention_floor": 0, "fixture": "new source"}
        tail = self.observation(at=500)

        def replace_before_finish(operation, *args, **kwargs):
            if operation == "finish_memorized_purge":
                connection = self.store._connect()
                try:
                    connection.execute("UPDATE schema_meta SET value='replacement' WHERE key='store_generation'")
                    connection.execute("DELETE FROM schema_meta WHERE key='memorized_retention_ingest_seq'")
                finally:
                    connection.close()
                original_submit("set_state", "memorized_retention", replacement)
                original_submit("append", tail)
            return original_submit(operation, *args, **kwargs)

        with patch.object(self.store, "_submit", side_effect=replace_before_finish):
            with self.assertRaisesRegex(ValueError, "generation changed"):
                self.purge(through=old.ingest_seq)
        self.assertEqual(self.store.get_state("memorized_retention"), replacement)
        self.assertIsNotNone(self.store.get(tail.observation_id))

    def test_busy_reader_defers_reclamation_truthfully_and_retry_truncates_wal(self):
        marker = "BUSY_READER_RAW_FIXTURE_" * 1000
        old = self.append(body=marker)
        reader = sqlite3.connect(self.path, isolation_level=None)
        try:
            reader.execute("BEGIN")
            reader.execute("SELECT payload_json FROM observations").fetchall()
            result = self.purge(through=old.ingest_seq)
            self.assertEqual(result["status"], "pending")
            self.assertTrue(result["logical_complete"])
            self.assertTrue(result["reclamation"]["checkpoint_busy"])
            self.assertFalse(result["reclamation"]["vacuumed"])
            self.assertTrue(self.store.get_state("memorized_retention")["reclamation"]["errors"])
        finally:
            reader.rollback()
            reader.close()
        result = self.purge(through=old.ingest_seq)
        self.assertEqual(result["status"], "complete")
        self.assertTrue(result["reclamation"]["vacuumed"])
        for path in (self.path, self.path.with_name(self.path.name + "-wal")):
            if path.exists():
                self.assertNotIn(marker.encode(), path.read_bytes())

    def test_read_only_consumer_cannot_purge(self):
        old = self.append()
        with ObservationStore(self.path, asset_directory=self.assets, read_only=True) as reader:
            with self.assertRaises(PermissionError):
                reader.purge_memorized(store_generation=reader.generation(),
                    through_ingest_seq=old.ingest_seq, before_utc_ms=200)
        self.assertIsNotNone(self.store.get(old.observation_id))


if __name__ == "__main__":
    unittest.main()
