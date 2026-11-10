"""Offline product invariants, with real SQLite and existing read-only owners."""

from __future__ import annotations

import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
import threading
import unittest
from unittest.mock import patch

from mirally.framework import FrameworkError, FrameworkService
from mirally.framework import delivery_health
from mirally.framework.service import SCHEMA_VERSION


class DeliveryHealthSnapshotTests(unittest.TestCase):
    """Read-only delivery snapshot (SPEC-1): counts only, no content, no writes."""

    MARKER = "MARKER_DELIVERY_BODY"
    CRASH_WRITER = textwrap.dedent(
        """
        import os
        import sqlite3
        import sys

        connection = sqlite3.connect(sys.argv[1])
        connection.execute(
            "INSERT INTO deliveries (id, kind, reference_id, channel, status,"
            " claim_token, receipt_json, sources_json, created_at, updated_at)"
            " VALUES (?,?,?,?,?,NULL,NULL,'[]',?,?)",
            ("crash-writer-1", "commitment", "crash-writer-ref", "weixin",
             "pending", 1000000000000, 1000000000000))
        connection.commit()
        # No close and no checkpoint: the committed row lives only in -wal.
        os._exit(0)
        """).strip()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name) / "中文 root with spaces"
        self.base.mkdir()
        self.home = self.base / "个人 数据"
        self.scratch = Path(self.temp.name) / "scratch"
        self.scratch.mkdir()
        self.now = 1_000_000_000_000
        self.database = self.home / "framework" / "product.sqlite3"
        self.services = []
        # Connections used by the checkpoint tests; SQLite is thread-bound, so
        # the checkpoint thread owns and closes its own, and tearDown joins it
        # before closing anything else.
        self.side_connections: list[sqlite3.Connection] = []
        self.thread_connections: list[sqlite3.Connection] = []
        self.side_stop = threading.Event()
        self.side_writer: threading.Thread | None = None

    def tearDown(self):
        self.stop_side_work()
        for service in self.services:
            service.close()
        self.services.clear()
        self.temp.cleanup()

    def side_connection(self, **kwargs) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.database), **kwargs)
        self.side_connections.append(connection)
        return connection

    def checkpoint_connection(self) -> sqlite3.Connection:
        """A connection owned by whoever thread runs the checkpoint loop."""
        connection = sqlite3.connect(str(self.database), check_same_thread=False, timeout=10)
        self.thread_connections.append(connection)
        return connection

    def stop_side_work(self) -> None:
        self.side_stop.set()
        worker = self.side_writer
        if worker is not None:
            worker.join(timeout=30)
            self.assertFalse(worker.is_alive(), "the checkpoint thread did not stop")
        self.side_writer = None
        for connection in (*self.side_connections, *self.thread_connections):
            try:
                connection.close()
            except sqlite3.Error:
                pass
        self.side_connections.clear()
        self.thread_connections.clear()

    def service(self):
        service = FrameworkService(self.base, self.home, clock=lambda: self.now)
        # Closed in tearDown *before* the temp dir is removed: Windows keeps
        # product.sqlite3 locked while this connection is open.
        self.services.append(service)
        return service

    def close_services(self):
        """Quiesce the writer so the snapshot sees checkpointed rows."""
        for service in self.services:
            service.close()
        self.services.clear()

    def report(self, home=None):
        return delivery_health.build(home or self.home, work=self.scratch, now_ms=lambda: self.now)

    def pending_delivery_in_wal(self) -> None:
        """One checkpointed row plus a pending row that lives only in ``-wal``."""
        seeded = self.service()
        seeded.commitment_create(title="checked in", due_at_utc_ms=self.now, request_id="seed")
        seeded.delivery_tick()              # an inbox reminder, delivered immediately
        seeded.close()                      # schema and that row are checkpointed
        self.services.clear()
        journal = Path(str(self.database) + "-wal")
        self.assertFalse(journal.exists() and journal.stat().st_size)
        # Keep the live writer alive for the whole test: it owns the row.
        live = self.service()
        self.now += 100_000
        live.commitment_create(title="still in wal", due_at_utc_ms=self.now,
                               request_id="wal", channel="weixin")
        live.delivery_tick()                # weixin is never sent offline: stays pending
        self.assertGreater(journal.stat().st_size, 0)

    def checkpoint_truncate(self) -> list[tuple[int, int, int]]:
        connection = self.side_connection(timeout=10)
        try:
            return connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
        finally:
            connection.close()

    def crash_writer_commit(self) -> None:
        """A real second process commits a pending delivery and dies via ``os._exit``.

        Closing a connection normally checkpoints ``-wal`` into the main file;
        dying without closing leaves the committed row only in the journal. That
        is the state the snapshot must never delete from stale metadata.
        """
        result = subprocess.run(
            [sys.executable, "-c", self.CRASH_WRITER, str(self.database)],
            capture_output=True, text=True)
        if result.returncode != 0:
            self.fail(f"crash writer failed ({result.returncode}): {result.stderr}")

    def test_empty_home_reports_zeros_without_errors(self):
        report = self.report()
        self.assertEqual(report["schema"], "mirally.delivery.health.v1")
        self.assertEqual(report["database"], "absent")
        self.assertEqual(report["errors"], [])
        self.assertEqual(report["counts"], dict.fromkeys(delivery_health.DELIVERY_STATUSES, 0))
        self.assertEqual(report["stale"],
                         {"pending_older_than_1h": 0, "sending_older_than_10m": 0, "unknown_any": 0})
        self.assertEqual(report["commitments"],
                         {"pending": 0, "queued": 0, "due_overdue": 0, "expired": 0})
        self.assertEqual(report["initiatives"],
                         {"pending": 0, "queued": 0, "expired": 0, "dismissed": 0})
        self.assertEqual(report["inbox"], {"unread": 0})
        self.assertFalse(self.database.exists(), "the snapshot must not create the product database")
        self.assertEqual(delivery_health.main(
            ["--home", str(self.home), "--json", "--scratch", str(self.scratch)]), 0)

    def test_stale_pending_sending_and_unknown_are_counted(self):
        service = self.service()
        # pending, older than the 1h threshold, on a channel the tick never delivers.
        service.commitment_create(title="waiting", due_at_utc_ms=self.now, request_id="p1",
                                  channel="test-channel")
        service.delivery_tick()
        # sending, older than the 10m threshold: claimed, sender "died".
        service.commitment_create(title="sending", due_at_utc_ms=self.now, request_id="s2",
                                  channel="test-channel")
        sending_id = service.delivery_tick()["delivery_ids"][0]
        token = service.delivery_claim(sending_id)["claim_token"]
        self.now += delivery_health.PENDING_STALE_MS + 1
        # A live writer is fine now: the -wal is part of the snapshot, so the
        # stale pending row is counted even before the writer checkpoints.
        live = self.report()
        self.assertEqual(live["database"], "present")
        self.assertEqual(live["stale"]["pending_older_than_1h"], 1)
        service.delivery_ack(sending_id, token, state="unknown", receipt={"provider_id": "lost"})
        self.close_services()
        report = self.report()
        self.assertEqual(report["stale"]["pending_older_than_1h"], 1)
        self.assertEqual(report["stale"]["unknown_any"], 1)
        self.assertEqual(report["counts"]["pending"], 1)
        self.assertEqual(report["counts"]["unknown"], 1)
        # The commitment already counts as overdue while its delivery is stuck.
        self.assertEqual(report["commitments"]["due_overdue"], 2)

    def test_overdue_commitment_and_expired_initiative_are_visible(self):
        service = self.service()
        service.commitment_create(title="overdue", due_at_utc_ms=self.now + 10, request_id="o1",
                                  channel="test-channel")
        service.initiative_offer(title="topic", body="body", reason="relevant", dedupe_key="d1",
                                 expires_at_utc_ms=self.now + 10)
        self.now += 100
        service.delivery_tick()  # expires the initiative; the test channel never delivers
        self.close_services()
        report = self.report()
        self.assertEqual(report["commitments"]["due_overdue"], 1)
        self.assertEqual(report["initiatives"]["expired"], 1)
        self.assertEqual(report["initiatives"]["pending"], 0)
        self.assertEqual(report["by_kind"]["pending"]["commitment"], 1)
        # An inbox reminder is delivered immediately and becomes `fulfilled`,
        # so it stops counting as overdue.
        service = self.service()
        service.commitment_create(title="now", due_at_utc_ms=self.now, request_id="o2")
        service.delivery_tick()
        self.close_services()
        report = self.report()
        self.assertEqual(report["counts"]["delivered"], 1)
        self.assertEqual(report["by_channel"]["inbox"], 1)
        self.assertEqual(report["inbox"]["unread"], 1)
        self.assertEqual(report["commitments"]["due_overdue"], 1)

    def test_sensitive_content_never_reaches_the_snapshot(self):
        service = self.service()
        service.initiative_offer(title=self.MARKER, body=self.MARKER, reason=self.MARKER,
                                 dedupe_key=self.MARKER, expires_at_utc_ms=self.now + 10)
        service.commitment_create(title=self.MARKER, body=self.MARKER,
                                  due_at_utc_ms=self.now, request_id=self.MARKER,
                                  channel="test-channel")
        identity = service.delivery_tick()["delivery_ids"][0]
        token = service.delivery_claim(identity)["claim_token"]
        service.delivery_ack(identity, token, state="failed",
                             receipt={"provider_id": self.MARKER, "detail": self.MARKER})
        self.close_services()
        report = self.report()
        rendered = json.dumps(report, ensure_ascii=False)
        self.assertNotIn(self.MARKER, rendered)
        for leaked in ("provider_id", "reference_id", "title", "body", "receipt",
                       "sources", "claim_token"):
            self.assertNotIn(leaked, rendered)
        # Neither the home path nor its non-ASCII directory names may leak.
        for fragment in (str(self.home), str(self.home).replace("\\", "/"), "个人 数据",
                         self.base.name):
            self.assertNotIn(fragment, rendered)
        self.assertGreaterEqual(report["counts"]["failed"], 1)

    def test_snapshot_is_read_only_for_the_product_database(self):
        service = self.service()
        service.commitment_create(title="audit me", due_at_utc_ms=self.now, request_id="ro1")
        service.delivery_tick()
        service.close()
        # Compare bytes rather than reading through a handle: on Windows an
        # open SQLite connection keeps the file locked for the whole test.
        before = self.database.read_bytes()
        mtime = self.database.stat().st_mtime_ns
        entries_before = sorted(path.name for path in self.database.parent.iterdir())
        self.report()
        self.assertEqual(self.database.read_bytes(), before)
        self.assertEqual(self.database.stat().st_mtime_ns, mtime)
        # A read-only WAL open leaves SQLite's own empty -wal/-shm beside a
        # static store. The snapshot must not remove them from a size it read
        # earlier: a writer can commit inside that window and its committed
        # rows live in the journal. Only those two SQLite side files may
        # appear; the main database and the caller's work dir stay untouched.
        entries_after = sorted(path.name for path in self.database.parent.iterdir())
        self.assertLessEqual(set(entries_after) - set(entries_before),
                             {"product.sqlite3-wal", "product.sqlite3-shm"})
        self.assertEqual(self.database.read_bytes(), before)
        self.assertEqual(sorted(self.scratch.iterdir()), [])
        self.assertEqual(self.report(), self.report())

    def test_writer_commit_and_crash_never_loses_the_journal(self):
        """P1: side files are never deleted from a size read taken earlier.

        Codex repro: the snapshot inspects an empty ``-wal``, a real writer then
        commits and calls ``os._exit(0)`` (no close, no checkpoint), and the
        cleanup unlinks the now non-empty journal. Reopening silently loses the
        committed row. The fixed snapshot only reads and never unlinks the
        product's ``-wal``/``-shm``, so the crashed writer's commit survives for
        the next open.
        """
        service = self.service()
        service.commitment_create(title="before", due_at_utc_ms=self.now,
                                  request_id="crash-before", channel="weixin")
        service.delivery_tick()
        service.close()
        self.services.clear()
        journal = Path(str(self.database) + "-wal")
        shared_memory = Path(str(self.database) + "-shm")
        self.assertFalse(journal.exists(), "a quiesced writer leaves no journal")
        crash = {"ran": False}

        real_stat = Path.stat

        def crash_after_the_size_check(path, *args, **kwargs):
            """Inject the writer in the exact window the old cleanup unlinked.

            The removed implementation read ``st_size`` and then unlinked the
            journal. A real commit that lands between those steps used to be
            deleted with it; the fixed snapshot never inspects the side files,
            so this hook only fires on the old code.
            """
            result = real_stat(path, *args, **kwargs)
            if path == journal and not crash["ran"]:
                crash["ran"] = True
                self.crash_writer_commit()
            return result

        with patch.object(Path, "stat", crash_after_the_size_check):
            report = self.report()
        if not crash["ran"]:
            # No source side file is inspected/unlinked any more: sequence the
            # same crash commit immediately after the snapshot's read.
            self.crash_writer_commit()
        self.assertEqual(report["database"], "present")
        self.assertFalse(crash["ran"], "the snapshot must not inspect product side files")
        self.assertTrue(journal.is_file(), "the committed -wal must not be deleted")
        self.assertGreater(journal.stat().st_size, 0)
        self.assertTrue(shared_memory.is_file(), "the writer's -shm must not be deleted")
        # The snapshot itself still sees the WAL-carried commit.
        self.assertEqual(self.report()["counts"]["pending"], 2)
        # Reopening the product database must see the committed row.
        connection = sqlite3.connect(str(self.database))
        try:
            committed = connection.execute(
                "SELECT COUNT(*) FROM deliveries WHERE reference_id='crash-writer-ref'"
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(committed, 1, "reopening the product database must see the commit")

    def test_unreadable_database_discloses_errors_instead_of_zeros(self):
        self.database.parent.mkdir(parents=True)
        self.database.write_bytes(b"this is not a sqlite database")
        report = self.report()
        self.assertNotEqual(report["errors"], [])
        self.assertEqual(report["database"], "unreadable")
        # Missing measurements must never be reported as a clean zero.
        self.assertIsNone(report["counts"]["pending"])
        self.assertIsNone(report["stale"]["unknown_any"])
        self.assertEqual(delivery_health.main(
            ["--home", str(self.home), "--json", "--scratch", str(self.scratch)]), 1)

    def test_snapshot_sees_rows_a_live_writer_has_not_checkpointed(self):
        """P1: immutable=1 alone dropped committed rows still sitting in -wal."""
        seeded = self.service()
        seeded.commitment_create(title="checked in", due_at_utc_ms=self.now, request_id="w1",
                                 channel="test-channel")
        seeded.delivery_tick()
        seeded.close()                      # schema and the first row are checkpointed
        self.services.clear()
        # A live writer commits a second pending row; it lives only in -wal.
        live = self.service()
        self.now += 100_000
        live.commitment_create(title="still in wal", due_at_utc_ms=self.now, request_id="w2",
                               channel="test-channel")
        live.delivery_tick()
        self.assertTrue(Path(str(self.database) + "-wal").is_file())
        before = sorted(path.name for path in self.database.parent.iterdir())
        report = self.report()
        self.assertEqual(report["database"], "present")
        # Both rows: the checkpointed one and the one only present in -wal.
        self.assertEqual(report["counts"]["pending"], 2)
        self.assertNotIn("deliveries", [error["section"] for error in report["errors"]])
        # The source is left exactly as the writer had it.
        self.assertEqual(before, sorted(path.name for path in self.database.parent.iterdir()))

    def test_concurrent_checkpoint_cannot_hide_wal_rows(self):
        """P1: a live writer's TRUNCATE checkpoint must not empty the snapshot.

        The removed implementation copied the main file and then ``-wal`` as
        two steps; a checkpoint between them emptied the journal first and the
        stale copy silently reported pending=0 with no error at all.
        """
        self.pending_delivery_in_wal()

        def checkpoint_loop():
            while not self.side_stop.is_set():
                connection = self.checkpoint_connection()
                try:
                    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
                finally:
                    connection.close()

        self.side_writer = threading.Thread(target=checkpoint_loop, daemon=True)
        self.side_writer.start()
        try:
            reports = [self.report() for _ in range(10)]
        finally:
            self.stop_side_work()
        for report in reports:
            self.assertEqual(report["database"], "present")
            self.assertEqual(report["counts"]["pending"], 1, report["counts"])
            self.assertEqual(report["commitments"]["queued"], 1)
            self.assertEqual(report["errors"], [])

    def test_checkpoint_between_two_file_copies_cannot_hide_wal_rows(self):
        """P1, deterministically: a checkpoint right after the main-file copy.

        Every separate copy of the main file is followed by a writer
        ``wal_checkpoint(TRUNCATE)`` before a second file copy could happen.
        A two-step copy reads pending=0 here; one SQLite backup read cannot be
        split that way.
        """
        self.pending_delivery_in_wal()
        real_copy2 = shutil.copy2

        def copy_then_checkpoint(source, destination, *args, **kwargs):
            result = real_copy2(source, destination, *args, **kwargs)
            if Path(source) == self.database:
                self.assertEqual(self.checkpoint_truncate()[0][0], 0)
            return result

        with patch.object(delivery_health.shutil, "copy2", copy_then_checkpoint):
            report = self.report()
        self.assertEqual(report["database"], "present")
        self.assertEqual(report["counts"]["pending"], 1, report["counts"])
        self.assertEqual(report["commitments"]["queued"], 1)
        self.assertEqual(report["errors"], [])

    def test_checkpoint_after_the_snapshot_is_neither_blocked_nor_lost(self):
        """The snapshot releases the store before a checkpoint is attempted.

        A writer that truncates the journal while the report is being read
        from the scratch copy cannot change what the snapshot already read,
        and the audit must not hold a read transaction that blocks it.
        """
        self.pending_delivery_in_wal()
        real_snapshot = delivery_health._snapshot_copy
        checkpoints = []

        def snapshot_then_checkpoint(database, work):
            target = real_snapshot(database, work)
            if not checkpoints:
                checkpoints.append(self.checkpoint_truncate())
            return target

        with patch.object(delivery_health, "_snapshot_copy", snapshot_then_checkpoint):
            report = self.report()
        self.assertEqual(len(checkpoints), 1)
        self.assertEqual(checkpoints[0][0][0], 0, "the checkpoint must not be blocked")
        self.assertEqual(report["counts"]["pending"], 1, report["counts"])
        self.assertEqual(report["commitments"]["queued"], 1)

    def test_hot_wal_without_shm_is_read_and_left_alone(self):
        """A ``-wal`` left by a crashed writer (no ``-shm``) is still read.

        This is the home SQLite would find after a crash: the journal holds
        committed rows but no shared-memory file exists. The snapshot must
        recover those rows and must never delete the journal; the read-only
        open may add its own ``-shm``, which SQLite manages from then on.
        """
        self.pending_delivery_in_wal()
        framework = self.base / "crashed home" / "framework"
        framework.mkdir(parents=True)
        shutil.copy2(self.database, framework / "product.sqlite3")
        shutil.copy2(Path(str(self.database) + "-wal"), framework / "product.sqlite3-wal")
        self.close_services()               # the writer is gone; the pair is all that is left
        before = sorted(path.name for path in framework.iterdir())
        self.assertEqual(before, ["product.sqlite3", "product.sqlite3-wal"])
        journal = framework / "product.sqlite3-wal"
        journal_size = journal.stat().st_size
        self.assertGreater(journal_size, 0)
        report = self.report(self.base / "crashed home")
        self.assertEqual(report["database"], "present")
        self.assertEqual(report["counts"]["pending"], 1, report["counts"])
        self.assertEqual(report["errors"], [])
        # The crashed writer's journal keeps the committed rows; only SQLite's
        # own shared-memory file may be added, and no source file is removed.
        after = sorted(path.name for path in framework.iterdir())
        self.assertLessEqual(set(after), {"product.sqlite3", "product.sqlite3-wal",
                                          "product.sqlite3-shm"})
        self.assertIn("product.sqlite3-wal", after)
        self.assertEqual(journal.stat().st_size, journal_size)

    def test_scratch_parent_files_are_never_used_or_removed(self):
        """P1: a caller's own product.sqlite3 inside --scratch must survive."""
        service = self.service()
        service.commitment_create(title="audit me", due_at_utc_ms=self.now, request_id="s1")
        service.delivery_tick()
        service.close()
        self.services.clear()
        caller_copy = self.scratch / "product.sqlite3"
        caller_copy.write_bytes(b"caller owned bytes")
        sibling = self.scratch / "notes.txt"
        sibling.write_text("keep me", encoding="utf-8")
        report = self.report()
        self.assertEqual(report["counts"]["delivered"], 1)
        # The caller's files are untouched, and no work directory is left behind.
        self.assertEqual(caller_copy.read_bytes(), b"caller owned bytes")
        self.assertEqual(sibling.read_text(encoding="utf-8"), "keep me")
        self.assertEqual(sorted(path.name for path in self.scratch.iterdir()),
                         ["notes.txt", "product.sqlite3"])

    def test_scratch_subdirectory_is_exclusive_and_cleaned_up(self):
        """The copy lives in a private mkdtemp subdirectory, removed afterwards."""
        service = self.service()
        service.commitment_create(title="audit me", due_at_utc_ms=self.now, request_id="s2")
        service.delivery_tick()
        service.close()
        self.services.clear()
        created = []
        real_mkdtemp = tempfile.mkdtemp

        def recording_mkdtemp(*args, **kwargs):
            path = real_mkdtemp(*args, **kwargs)
            created.append(Path(path))
            return path

        with patch.object(delivery_health.tempfile, "mkdtemp", recording_mkdtemp):
            report = self.report()
        self.assertEqual(report["counts"]["delivered"], 1)
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0].parent, self.scratch)
        self.assertFalse(created[0].exists(), "the private directory must be removed")
        self.assertEqual(sorted(path.name for path in self.scratch.iterdir()), [])

    def test_overlapping_scratch_and_home_is_rejected_without_damage(self):
        """A ``--scratch`` inside the product data must not touch the source."""
        service = self.service()
        service.commitment_create(title="audit me", due_at_utc_ms=self.now, request_id="s3")
        service.delivery_tick()
        service.close()
        self.services.clear()
        before = self.database.read_bytes()
        code = delivery_health.main(["--home", str(self.home), "--json",
                                     "--scratch", str(self.database.parent)])
        self.assertIn(code, (0, 1))
        self.assertEqual(self.database.read_bytes(), before)
        # SQLite's own side files may remain after the read-only open; the
        # product database itself and any work subdirectory are not left over.
        entries = sorted(path.name for path in self.database.parent.iterdir())
        self.assertIn("product.sqlite3", entries)
        self.assertLessEqual(set(entries) - {"product.sqlite3"},
                             {"product.sqlite3-wal", "product.sqlite3-shm"})
        self.assertFalse([name for name in entries if name.startswith("delivery-health-")],
                         "the private work subdirectory must be removed")


class FrameworkProductTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name) / "中文 root with spaces"
        self.base.mkdir()
        self.home = self.base / "个人 数据"
        self.now = 1000000
        self.service = FrameworkService(self.base, self.home, clock=lambda: self.now)

    def tearDown(self):
        self.service.close()
        self.temp.cleanup()

    def call(self, method, **params):
        return self.service.call(method, params)

    def conversation(self, identity="chat"):
        self.call("conversation.ensure", conversation_id=identity)
        return self.call("conversation.bind", conversation_id=identity, session_id="native-" + identity)

    def restart(self):
        self.service.close()
        self.service = FrameworkService(self.base, self.home, clock=lambda: self.now)

    def test_safe_defaults_and_missing_sources_create_no_owner_databases(self):
        settings = self.call("settings.get")
        for name in ("activity_enabled", "background_enabled", "general_initiative_enabled"):
            self.assertFalse(settings[name])
        context = self.call("context.read")
        self.assertEqual(context["memory"]["status"], "not_ready")
        self.assertEqual(context["activity"]["status"], "disabled")
        self.assertFalse((self.home / "memory" / "memory.sqlite3").exists())
        self.assertFalse((self.home / "data" / "activity").exists())
        self.assertEqual(self.call("system.status")["transcript_owner"], "dsh")

    def test_optional_contact_is_grounded_opt_in_and_cooldown_survives_restart(self):
        self.assertEqual(self.call("initiative.acquire")["status"], "disabled")
        self.call("settings.update", patch={"background_enabled": True, "general_initiative_enabled": True})
        self.assertEqual(self.call("initiative.acquire")["status"], "no_evidence")
        self.call("preference.set", key="tone", value="brief")
        lease = self.call("initiative.acquire")
        self.assertEqual(lease["status"], "leased")
        self.assertEqual(self.call("initiative.complete", job_id=lease["job_id"], lease_token=lease["lease_token"], proposal={"contact": False})["status"], "quiet")
        self.restart()
        self.assertEqual(self.call("initiative.acquire")["status"], "cooldown")
        self.now += 4 * 3600000 + 1
        lease = self.call("initiative.acquire")
        self.call("settings.update", patch={"do_not_disturb": True})
        self.assertEqual(self.call("initiative.complete", job_id=lease["job_id"], lease_token=lease["lease_token"], proposal={"contact": True})["status"], "discarded")

    def test_contact_drops_withdrawn_evidence_and_expired_lease(self):
        self.call("settings.update", patch={"background_enabled": True, "general_initiative_enabled": True})
        self.call("preference.set", key="tone", value="brief")
        lease = self.call("initiative.acquire")
        self.call("preference.set", key="tone", value="detailed")
        self.assertEqual(self.call("initiative.complete", job_id=lease["job_id"], lease_token=lease["lease_token"], proposal={"contact": True})["status"], "discarded")
        self.now += 4 * 3600000 + 1
        lease = self.call("initiative.acquire")
        self.now += 120001
        self.assertEqual(self.call("initiative.complete", job_id=lease["job_id"], lease_token=lease["lease_token"], proposal={"contact": True})["status"], "discarded")

    def test_first_schema_upgrades_without_replacing_product_state(self):
        self.call("preference.set", key="keep", value="original")
        self.service.db.execute("DROP TABLE initiative_runs")
        for column in ("pinned", "archived", "metadata_version", "metadata_updated_at", "pinned_at", "archived_at", "title_mode"):
            self.service.db.execute("ALTER TABLE conversations DROP COLUMN " + column)
        self.service.db.execute("PRAGMA user_version=1")
        self.restart()
        self.assertEqual(self.call("preference.list")["preferences"][0]["value"], "original")
        self.assertEqual(self.service.db.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)

    def test_withdrawn_preferences_are_excluded_from_direct_and_context_reads(self):
        self.call("preference.set", key="topic", value="private")
        self.call("source.set", source_id="preference:user/local:topic", enabled=False)
        self.assertEqual(self.call("preference.list")["preferences"], [])
        self.assertEqual(self.call("context.read")["preferences"], [])

    def test_continuation_tracks_prior_sources_and_omits_withdrawn_generations(self):
        self.call('preference.set', key='tone', value='brief')
        self.conversation()
        self.call('context.read', conversation_id='chat', generation=1, query='tone')
        self.call('interaction.stop', conversation_id='chat')
        self.call('conversation.rotate', conversation_id='chat', reason='explicit_user_input')
        result = self.call('internal.conversation.continuation', conversation_id='chat', generation=2)
        self.assertEqual(result['generations'], [1])
        self.call('source.set', source_id='preference:user/local:tone', enabled=False)
        self.assertTrue(self.call('conversation.get', conversation_id='chat')['frozen'])
        self.call('conversation.rotate', conversation_id='chat', reason='explicit_user_input')
        result = self.call('internal.conversation.continuation', conversation_id='chat', generation=3)
        self.assertNotIn(1, result['generations'])
        self.assertTrue(result['omitted'])

    def test_memory_ancestry_and_full_owner_sources_fence_reads_and_old_sessions(self):
        from mirally.core.memory.store import MemoryStore
        parent = self.call("memory.correct", content="Parent original fact")["memory"]
        with MemoryStore(self.home / "memory/memory.sqlite3") as store:
            child = store.put_memory({"id": "derived", "kind": "episodic", "scope": "user/local", "status": "active", "provenance": "model_inference",
                "title": "Derived fact", "content": "Distinct derived fact", "source_observation_ids": ["a", "b", "c", "d"], "metadata": {"derived_from_memory_id": parent["id"]}})
        self.conversation()
        context = self.call("context.read", conversation_id="chat", query="Distinct")
        self.assertTrue(any(item["id"] == "derived" for item in context["memory"]["memories"]))
        self.assertTrue(any(ref["source_id"] == "observation:d" for ref in context["sources"]))
        self.call("source.set", source_id="memory:" + parent["id"], enabled=False)
        self.assertTrue(self.call("conversation.ensure", conversation_id="chat")["frozen"])
        self.assertEqual(self.call("memory.search", query="Distinct")["memories"], [])
        with self.assertRaises(FrameworkError): self.call("memory.read", memory_id="derived")

    def test_legacy_root_config_is_not_implicitly_consumed(self):
        config_dir = self.base / "config" / "mirally"
        config_dir.mkdir(parents=True)
        (config_dir / "memory.yaml").write_text("enabled: true\nstorage:\n  database_path: private.sqlite3\n", encoding="utf-8")
        with patch("mirally.runtime.memory.config.load_memory_config", side_effect=AssertionError("legacy access")):
            self.assertEqual(self.call("context.read")["memory"]["status"], "not_ready")

    def test_secret_configuration_survives_restart_without_public_echo(self):
        updated = self.call("settings.update", patch={"model": {"api_key": "DO-NOT-ECHO", "model_id": "my-model"},
            "extensions": [{"id": "calendar", "type": "mcp", "config": {"env": {"PRIVATE_KEY": "env-secret"},
            "headers": {"Authorization": "header-secret"}, "nested": {"apiKey": "nested-secret"}}}]})
        self.assertNotIn("DO-NOT-ECHO", json.dumps(updated))
        self.assertNotIn("env-secret", json.dumps(updated))
        self.assertNotIn("header-secret", json.dumps(updated))
        self.assertNotIn("nested-secret", json.dumps(updated))
        self.assertTrue(updated["model"]["api_key_configured"])
        self.restart()
        private = self.call("internal.settings.get")
        self.assertEqual(private["model"]["api_key"], "DO-NOT-ECHO")
        self.assertEqual(private["extensions"][0]["config"]["env"]["PRIVATE_KEY"], "env-secret")
        self.assertNotIn("api_key", self.call("settings.get")["model"])

    def test_invalid_settings_are_atomic(self):
        for patch_value in ({"unknown": True}, {"activity_enabled": 1}, {"model": {"temperature": float("nan")}},
                            {"model": {"base_url": "https://user:password@example.com"}}, {"extensions": ["bad"]}):
            with self.subTest(patch=patch_value), self.assertRaises(FrameworkError):
                self.call("settings.update", patch=patch_value)
        self.assertFalse(self.call("settings.get")["activity_enabled"])

    def test_action_approval_preferences_default_validate_and_survive_restart(self):
        initial = self.call("settings.get")
        self.assertEqual(initial["action_approval_mode"], "ask")
        self.assertFalse(initial["weixin_auto_approve_enabled"])
        for patch_value in ({"action_approval_mode": "never"}, {"action_approval_mode": True},
                            {"weixin_auto_approve_enabled": 1}):
            with self.subTest(patch=patch_value), self.assertRaises(FrameworkError):
                self.call("settings.update", patch=patch_value)
        self.call("settings.update", patch={"action_approval_mode": "trusted", "weixin_auto_approve_enabled": True})
        self.restart()
        saved = self.call("settings.get")
        self.assertEqual(saved["action_approval_mode"], "trusted")
        self.assertTrue(saved["weixin_auto_approve_enabled"])
        self.call("settings.update", patch={"action_approval_mode": "ask", "weixin_auto_approve_enabled": False})

    def test_reasoning_effort_and_context_budget_are_explicit_and_durable(self):
        self.assertEqual(self.call("settings.get")["model"]["reasoning_effort"], "")
        self.call("settings.update", patch={"model": {"context_window": 1000000, "reasoning_effort": "default"}})
        self.restart()
        self.assertEqual(self.call("settings.get")["model"]["context_window"], 1000000)
        self.assertEqual(self.call("settings.get")["model"]["reasoning_effort"], "default")
        for effort in ("off", "low", "medium", "high", "max", ""):
            self.call("settings.update", patch={"model": {"reasoning_effort": effort}})
        for model in ({"reasoning_effort": "ultra"}, {"reasoning_effort": False},
                      {"context_window": True}, {"context_window": 0}, {"context_window": 10000001}):
            with self.subTest(model=model), self.assertRaises(FrameworkError):
                self.call("settings.update", patch={"model": model})

    def test_concurrent_command_identity_is_admitted_once(self):
        self.conversation()
        def admit(_):
            return self.call("command.admit", conversation_id="chat", command_id="same", text="同一句输入")
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(admit, range(24)))
        self.assertEqual(sum(result["should_dispatch"] for result in results), 1)
        self.assertEqual(len(self.call("command.list", conversation_id="chat")["commands"]), 1)
        with self.assertRaises(FrameworkError):
            self.call("command.admit", conversation_id="chat", command_id="same", text="different")

    def test_conversation_times_and_recency_follow_real_command_receipts(self):
        first = self.conversation("first")
        self.now += 10
        self.conversation("second")
        self.assertEqual(self.call("conversation.list")["conversations"][0]["conversation_id"], "second")
        self.now += 10
        self.call("command.admit", conversation_id="first", command_id="later", text="continue")
        recent = self.call("conversation.list")["conversations"][0]
        self.assertEqual(recent["conversation_id"], "first")
        self.assertEqual(recent["created_at"], first["created_at"])
        self.assertEqual(recent["updated_at"], self.now)
        self.restart()
        self.assertEqual(self.call("conversation.list")["conversations"][0]["updated_at"], self.now)

    def test_command_identity_lookup_is_exact_beyond_the_recent_list(self):
        self.conversation()
        for index in range(205):
            self.now += 1
            self.call("command.admit", conversation_id="chat", command_id=f"command-{index}", text=str(index))
        self.assertNotIn("command-0", {item["command_id"] for item in self.call("command.list", conversation_id="chat")["commands"]})
        exact = self.call("command.list", conversation_id="chat", command_id="command-0")["commands"]
        self.assertEqual([item["command_id"] for item in exact], ["command-0"])
        self.assertEqual(self.call("command.list", conversation_id="chat", command_id="missing")["commands"], [])
        page = self.call("command.list", conversation_id="chat", limit=10, offset=200)["commands"]
        self.assertEqual([item["command_id"] for item in page], [f"command-{index}" for index in range(4, -1, -1)])
        for options in ({"offset": -1}, {"offset": True}, {"limit": 0}, {"limit": 1001}):
            with self.subTest(options=options), self.assertRaises(FrameworkError):
                self.call("command.list", conversation_id="chat", **options)
        self.assertNotIn("同一句输入", self.service.path.read_bytes().decode("utf-8", errors="ignore"))

    def test_command_replay_never_rebinds_new_generation(self):
        self.conversation()
        first = self.call("command.admit", conversation_id="chat", command_id="one", text="hello")
        self.call("conversation.rotate", conversation_id="chat", session_id="native-two", reason="source correction")
        replay = self.call("command.admit", conversation_id="chat", command_id="one", text="hello")
        self.assertEqual(replay["generation"], first["generation"])
        self.assertEqual(replay["session_id"], "native-chat")
        self.assertFalse(replay["should_dispatch"])
        self.assertEqual(replay["state"], "cancelled")
        refs = self.call("conversation.history_refs", conversation_id="chat")["generations"]
        self.assertEqual([ref["session_id"] for ref in refs], ["native-chat", "native-two"])
        self.assertTrue(refs[0]["frozen"])

    def test_native_session_cannot_be_shared_or_replaced_silently(self):
        self.conversation()
        with self.assertRaises(FrameworkError):
            self.call("conversation.bind", conversation_id="chat", session_id="different")
        self.call("conversation.ensure", conversation_id="another")
        with self.assertRaises(FrameworkError):
            self.call("conversation.bind", conversation_id="another", session_id="native-chat")

    def test_restart_preserves_applied_and_marks_submitted_unknown(self):
        self.conversation()
        for identity in ("submitted", "applied"):
            self.call("command.admit", conversation_id="chat", command_id=identity, text=identity)
            self.call("command.ack", conversation_id="chat", command_id=identity, state=identity, runtime_request_id="request-" + identity)
        self.restart()
        commands = {item["command_id"]: item for item in self.call("command.list", conversation_id="chat")["commands"]}
        self.assertEqual(commands["submitted"]["state"], "unknown")
        self.assertTrue(commands["submitted"]["needs_reconciliation"])
        self.assertEqual(commands["applied"]["state"], "applied")
        self.assertFalse(commands["submitted"]["should_dispatch"])

    def test_stop_wins_over_late_ack_and_does_not_cancel_unrelated_reminder(self):
        self.conversation()
        reminder = self.call("commitment.create", title="站起来", due_at_utc_ms=self.now + 100, conversation_id="chat")
        self.call("command.admit", conversation_id="chat", command_id="one", text="hello")
        stopped = self.call("interaction.stop", conversation_id="chat")
        self.assertEqual(stopped["cancelled_command_ids"], ["one"])
        late = self.call("command.ack", conversation_id="chat", command_id="one", state="applied")
        self.assertEqual(late["state"], "cancelled")
        self.assertFalse(self.call("guard.check", conversation_id="chat", command_id="one", effect="desktop", confirmed=True)["allow"])
        self.assertEqual(self.call("commitment.list")["commitments"][0]["id"], reminder["id"])
        self.assertEqual(self.call("commitment.list")["commitments"][0]["status"], "pending")

    def test_guard_requires_live_scope_and_explicit_confirmation(self):
        self.conversation()
        self.call("command.admit", conversation_id="chat", command_id="one", text="hello")
        self.assertTrue(self.call("guard.check", conversation_id="chat", command_id="one", effect="query")["allow"])
        self.assertFalse(self.call("guard.check", conversation_id="chat", command_id="one", effect="script")["allow"])
        self.assertTrue(self.call("guard.check", conversation_id="chat", command_id="one", effect="script", confirmed=True)["allow"])
        self.assertFalse(self.call("guard.check", effect="desktop", confirmed=True)["allow"])
        self.assertFalse(self.call("guard.check", effect="unknown")["allow"])

    def test_source_retraction_fences_old_session_and_pending_contact(self):
        self.conversation()
        refs = [{"source_id": "memory:item", "version": 1}]
        self.call("command.admit", conversation_id="chat", command_id="one", text="hello", sources=refs)
        candidate = self.call("initiative.offer", title="topic", body="hello", reason="relevant", dedupe_key="once", expires_at_utc_ms=self.now + 500, sources=refs)
        disabled = self.call("source.set", source_id="memory:item", enabled=False)
        self.assertEqual(disabled["affected"][0]["session_id"], "native-chat")
        self.assertTrue(self.call("conversation.ensure", conversation_id="chat")["frozen"])
        self.assertFalse(self.call("guard.check", conversation_id="chat", generation=1)["allow"])
        self.assertEqual(self.call("initiative.list")["initiatives"][0]["status"], "cancelled")
        again = self.call("source.set", source_id="memory:item", enabled=False)
        self.assertEqual(again["version"], disabled["version"])
        self.call("conversation.rotate", conversation_id="chat", session_id="clean", reason="source removed")
        self.assertTrue(self.call("guard.check", conversation_id="chat", generation=2)["allow"])

    def test_preference_correction_only_freezes_sessions_that_used_it(self):
        self.conversation()
        self.conversation("unused")
        self.call("preference.set", key="name", value="old")
        self.call("context.read", conversation_id="chat")
        corrected = self.call("preference.set", key="name", value="new")
        self.assertEqual(corrected["revision"], 2)
        self.assertEqual(len(corrected["affected"]), 1)
        self.assertFalse(self.call("conversation.ensure", conversation_id="unused")["frozen"])
        self.assertEqual(self.call("context.read")["preferences"][0]["value"], "new")

    def test_explicit_reminder_is_durable_once_without_background_or_models(self):
        reminder = self.call("commitment.create", title="喝水", due_at_utc_ms=self.now + 100, request_id="click-one")
        replay = self.call("commitment.create", title="喝水", due_at_utc_ms=self.now + 100, request_id="click-one")
        self.assertEqual(reminder["id"], replay["id"])
        self.assertEqual(self.call("delivery.tick")["delivery_ids"], [])
        self.restart()
        self.now += 101
        delivered = self.call("delivery.tick")["delivery_ids"]
        self.assertEqual(len(delivered), 1)
        self.assertEqual(self.call("delivery.tick")["delivery_ids"], [])
        self.restart()
        inbox = self.call("inbox.list")["items"]
        self.assertEqual(len(inbox), 1)
        self.assertEqual(inbox[0]["body"], "喝水")
        self.call("inbox.ack", delivery_id=delivered[0], state="replied")
        self.assertEqual(self.call("inbox.ack", delivery_id=delivered[0], state="read")["state"], "replied")

    def test_reminder_request_id_conflict_and_cancel_before_due(self):
        first = self.call("commitment.create", title="one", due_at_utc_ms=self.now, request_id="once")
        with self.assertRaises(FrameworkError):
            self.call("commitment.create", title="different", due_at_utc_ms=self.now, request_id="once")
        self.call("commitment.cancel", commitment_id=first["id"])
        self.assertEqual(self.call("delivery.tick")["delivery_ids"], [])

    def test_expired_reminder_is_not_backfilled_after_restart(self):
        self.call("commitment.create", title="time sensitive", due_at_utc_ms=self.now, expires_at_utc_ms=self.now + 100)
        self.now += 101
        self.restart()
        self.assertEqual(self.call("inbox.list")["items"], [])
        self.assertEqual(self.call("commitment.list")["commitments"][0]["status"], "expired")

    def test_general_initiative_is_off_and_expires_without_backlog(self):
        self.call("initiative.offer", title="topic", body="hello", reason="relevant", dedupe_key="once", expires_at_utc_ms=self.now + 100)
        self.assertEqual(self.call("delivery.tick")["delivery_ids"], [])
        self.now += 101
        self.call("settings.update", patch={"general_initiative_enabled": True})
        self.assertEqual(self.call("delivery.tick")["delivery_ids"], [])
        self.assertEqual(self.call("initiative.list")["initiatives"][0]["status"], "expired")

    def test_dnd_delays_general_contact_but_not_explicit_reminder(self):
        self.call("settings.update", patch={"general_initiative_enabled": True, "do_not_disturb": True})
        self.call("initiative.offer", title="topic", body="hello", reason="relevant", dedupe_key="once", expires_at_utc_ms=self.now + 100)
        self.call("commitment.create", title="explicit", due_at_utc_ms=self.now)
        self.assertEqual(len(self.call("delivery.tick")["delivery_ids"]), 1)
        self.call("settings.update", patch={"do_not_disturb": False})
        self.assertEqual(len(self.call("delivery.tick")["delivery_ids"]), 1)
        self.assertEqual(len(self.call("inbox.list")["items"]), 2)

    def test_external_unknown_delivery_never_retries_automatically(self):
        self.call("commitment.create", title="external", due_at_utc_ms=self.now, channel="test-channel")
        identity = self.call("delivery.tick")["delivery_ids"][0]
        claimed = self.call("delivery.claim", delivery_id=identity)
        self.assertTrue(claimed["should_send"])
        self.assertFalse(self.call("delivery.claim", delivery_id=identity)["should_send"])
        self.restart()
        replay = self.call("delivery.claim", delivery_id=identity)
        self.assertEqual(replay["status"], "unknown")
        self.assertFalse(replay["should_send"])
        self.assertEqual(self.call("delivery.tick")["delivery_ids"], [])
        reconciled = self.call("delivery.ack", delivery_id=identity, claim_token=claimed["claim_token"], state="delivered", receipt={"provider_id": "known"})
        self.assertEqual(reconciled["status"], "delivered")

    def test_late_delivery_receipt_is_fact_without_resurrecting_cancelled_plan(self):
        reminder = self.call("commitment.create", title="external", due_at_utc_ms=self.now, channel="test-channel")
        identity = self.call("delivery.tick")["delivery_ids"][0]
        claimed = self.call("delivery.claim", delivery_id=identity)
        self.call("commitment.cancel", commitment_id=reminder["id"])
        result = self.call("delivery.ack", delivery_id=identity, claim_token=claimed["claim_token"], state="delivered", receipt={"provider_id": "already sent"})
        self.assertEqual(result["status"], "cancelled")
        self.assertTrue(result["receipt"]["late"])
        self.assertEqual(self.call("commitment.list")["commitments"][0]["status"], "cancelled")

    def test_manual_memory_uses_owner_and_correction_invalidates_stale_session(self):
        self.conversation()
        first = self.call("memory.correct", content="我喜欢绿茶", title="口味")["memory"]
        self.assertEqual(first["provenance"], "user_confirmed")
        context = self.call("context.read", conversation_id="chat", query="绿茶")
        self.assertEqual(context["memory"]["memories"][0]["id"], first["id"])
        corrected = self.call("memory.correct", memory_id=first["id"], content="我现在喜欢红茶")
        self.assertEqual(corrected["memory"]["revision"], 2)
        self.assertEqual(len(corrected["affected"]), 1)
        self.assertTrue(self.call("conversation.ensure", conversation_id="chat")["frozen"])
        read = self.call("memory.read", memory_id=first["id"])
        self.assertEqual(read["text"], "我现在喜欢红茶")
        tables = {row[0] for row in self.service.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertNotIn("memories", tables)
        self.assertNotIn("messages", tables)

    def test_context_queries_leave_memory_and_activity_databases_unchanged(self):
        from mirally.core.activity.knowledge.repository import KnowledgeStore
        from mirally.core.activity.knowledge.models import ActivityItem, Evidence
        memory = self.call("memory.correct", content="reference memory")["memory"]
        memory_path = self.home / "memory" / "memory.sqlite3"
        activity_path = self.home / "data" / "activity" / "knowledge.sqlite3"
        item = ActivityItem(item_id="item-one", kind="document", start_utc_ms=1, end_utc_ms=2,
                            title="a document", text_summary="reference activity", source_observation_ids=("observation-one",))
        evidence = Evidence("evidence-one", ("observation-one",), 1, 1, "text/plain", text="reference activity")
        with KnowledgeStore(activity_path) as owner:
            owner.upsert_item(item, evidence)
        hashes = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in (memory_path, activity_path)}
        self.call("settings.update", patch={"activity_enabled": True})
        result = self.call("context.read", query="reference")
        self.assertEqual(result["memory"]["memories"][0]["id"], memory["id"])
        self.assertEqual(result["activity"]["items"][0]["item_id"], "item-one")
        self.assertFalse(result["activity"]["collector_started"])
        for path, previous in hashes.items():
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), previous)
        self.assertFalse((activity_path.parent / "observations.sqlite3").exists())

    def test_failed_memory_correction_keeps_durable_source_fence(self):
        self.conversation()
        identity = self.call("memory.correct", content="old")["memory"]["id"]
        self.call("context.read", conversation_id="chat")
        with patch("mirally.core.memory.store.MemoryStore.put_memory", side_effect=OSError("disk unavailable")):
            with self.assertRaises(OSError):
                self.call("memory.correct", memory_id=identity, content="new")
        self.restart()
        source = next(source for source in self.call("source.list")["sources"] if source["source_id"] == "memory:" + identity)
        self.assertFalse(source["enabled"])
        self.assertTrue(self.call("conversation.ensure", conversation_id="chat")["frozen"])
        self.assertEqual(self.call("context.read")["memory"]["memories"], [])
        self.call("memory.correct", memory_id=identity, content="new")
        self.assertEqual(self.call("memory.read", memory_id=identity)["text"], "new")

    def test_controller_receives_private_settings_but_status_contains_no_key(self):
        class Controller:
            def __init__(self):
                self.updates = []
                self.closed = False
            def update(self, settings):
                self.updates.append(settings)
            def status(self):
                return {"activity": "stopped"}
            def close(self):
                self.closed = True
        controller = Controller()
        self.service.close()
        self.service = FrameworkService(self.base, self.home, controller=controller)
        result = self.call("settings.update", patch={"model": {"api_key": "private-value"}, "activity_enabled": True})
        self.assertEqual(controller.updates[-1]["model"]["api_key"], "private-value")
        self.assertTrue(controller.updates[-1]["activity_enabled"])
        self.assertNotIn("private-value", json.dumps(result))
        self.assertNotIn("private-value", json.dumps(self.call("system.status")))

    def test_future_schema_is_rejected_without_modification(self):
        future = self.base / "future" / "framework"
        future.mkdir(parents=True)
        path = future / "product.sqlite3"
        with sqlite3.connect(path) as db:
            db.execute("PRAGMA user_version=999")
        with self.assertRaises(FrameworkError):
            FrameworkService(self.base, future.parent)
        with sqlite3.connect(path) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 999)


class FrameworkStdioTests(unittest.TestCase):
    def test_real_jsonl_process_unicode_paths_restart_and_structured_errors(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / "新 home with spaces"
            requests = [
                {"id": "one", "method": "conversation.ensure", "params": {"conversation_id": "中文对话", "title": "生活"}},
                {"id": 2, "method": "settings.update", "params": {"patch": {"model": {"api_key": "stdio-secret"}}}},
                {"id": 3, "method": "unknown"},
                {"id": 4, "method": "settings.update", "params": {"patch": {"memory_enabled": "bad"}}},
                {"id": 5, "method": "settings.get"},
            ]
            env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
            command = [sys.executable, "-m", "mirally.framework", "--root", str(root), "--home", str(home)]
            payload = "\n".join(json.dumps(request, ensure_ascii=False) for request in requests) + "\n"
            run = subprocess.run(command, cwd=root, input=payload, capture_output=True, text=True, encoding="utf-8", env=env, timeout=20)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertNotIn("stdio-secret", run.stdout)
            results = [json.loads(line) for line in run.stdout.splitlines()]
            self.assertEqual(results[0]["result"]["conversation_id"], "中文对话")
            self.assertEqual(results[2]["error"]["code"], "METHOD_NOT_FOUND")
            self.assertEqual(results[3]["error"]["code"], "INVALID_PARAMS")
            self.assertTrue(results[4]["result"]["model"]["api_key_configured"])
            replay = subprocess.run(command, cwd=root, input='{"id":1,"method":"conversation.list"}\n', capture_output=True,
                                    text=True, encoding="utf-8", env=env, timeout=20)
            self.assertEqual(json.loads(replay.stdout)["result"]["conversations"][0]["title"], "生活")


if __name__ == "__main__":
    unittest.main()
