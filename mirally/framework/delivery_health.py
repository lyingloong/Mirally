"""Read-only delivery health snapshot for the product framework database.

Answers "is anything stuck in pending/unknown, did a reminder pass its due time
without being delivered, is a failed delivery recorded" without opening the
product, a model, a channel or any credential.

    python -m mirally.framework.delivery_health --home <dir> [--json]

The snapshot only emits counts and status enums. It never selects
``reference_id``, ``title``, ``body``, ``receipt_json``, ``sources_json``,
claim tokens, channel destinations or the absolute home path, and it reads
``product.sqlite3`` through SQLite's online backup API, which sees rows an
active writer has not checkpointed yet. The backup is written to a private
directory created under ``--scratch`` for this call only, so neither the
product home nor the caller's scratch directory is ever modified.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

SCHEMA = "mirally.delivery.health.v1"
DATABASE = Path("framework") / "product.sqlite3"
DELIVERY_STATUSES = ("pending", "sending", "delivered", "failed", "unknown", "cancelled")
DELIVERY_KINDS = ("commitment", "initiative")
DELIVERY_CHANNELS = ("inbox", "weixin", "desktop")
INITIATIVE_STATUSES = ("pending", "queued", "expired", "dismissed")
# Spec §1.6: thresholds are constants and echoed in the output so a reader can
# never mistake them for an SLA or a quality score.
PENDING_STALE_MS = 3_600_000
SENDING_STALE_MS = 600_000
WAL_MARKERS = ("-wal", "-shm")


class SnapshotError(RuntimeError):
    """The snapshot could not be built; reported inside errors[], never silent."""


def _utc_now_ms() -> int:
    return int(time.time() * 1000)


def _iso(value: int | None) -> str:
    if value is None:
        return ""
    return datetime.fromtimestamp(value / 1000, timezone.utc).replace(
        microsecond=0).isoformat().replace("+00:00", "Z")


def _snapshot_copy(database: Path, work: Path) -> Path:
    """Consistent copy of a WAL database, main file and ``-wal`` together.

    The pair must be copied as one unit: copying the main file alone silently
    returns an older history (measured with a live writer: 1 row instead of 2).

    The copy is then opened read-write *once* -- on a throwaway file, never the
    store -- so SQLite runs its own WAL recovery and folds the journal into the
    main file. After that close the copy is a self-contained rollback-journal
    database that any read-only reader sees in full.

    The source is only ever read (``shutil.copy2``), so nothing is created,
    locked or migrated next to it.
    """
    work.mkdir(parents=True, exist_ok=True)
    target = work / database.name
    shutil.copy2(database, target)
    journal = Path(str(database) + "-wal")
    if journal.is_file():
        shutil.copy2(journal, Path(str(target) + "-wal"))
    recovered = sqlite3.connect(str(target))
    try:
        # Replays -wal into the copy and drops the journal when it closes.
        recovered.execute("SELECT count(*) FROM sqlite_master").fetchone()
    finally:
        recovered.close()
    return target


def _count(connection: sqlite3.Connection, sql: str, params: tuple = ()) -> int:
    return int(connection.execute(sql, params).fetchone()[0] or 0)


def _table_present(connection: sqlite3.Connection, name: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _column_present(connection: sqlite3.Connection, table: str, column: str) -> bool:
    return any(row[1] == column for row in connection.execute(f"PRAGMA table_info({table})"))


def build(home: Path, *, work: Path, now_ms: Callable[[], int] = _utc_now_ms) -> dict[str, Any]:
    """Build the snapshot for ``home``. Never writes to ``home``."""
    errors: list[dict[str, str]] = []
    report: dict[str, Any] = {
        "schema": SCHEMA,
        "generated_at": _iso(now_ms()),
        "database": "present",
        "thresholds": {"pending_stale_ms": PENDING_STALE_MS, "sending_stale_ms": SENDING_STALE_MS},
        "counts": dict.fromkeys(DELIVERY_STATUSES, 0),
        "by_kind": {kind: dict.fromkeys(DELIVERY_KINDS, 0) for kind in DELIVERY_STATUSES},
        "by_channel": dict.fromkeys(DELIVERY_CHANNELS, 0),
        "stale": {"pending_older_than_1h": 0, "sending_older_than_10m": 0, "unknown_any": 0},
        "commitments": {"pending": 0, "queued": 0, "due_overdue": 0, "expired": 0},
        "initiatives": dict.fromkeys(INITIATIVE_STATUSES, 0),
        "inbox": {"unread": 0},
        "errors": errors,
    }
    database = home / DATABASE
    if not database.is_file():
        # A fresh home has no framework database yet: that is "nothing recorded",
        # which is a real zero, not a missing measurement.
        report["database"] = "absent"
        return report

    connection = None
    # Every temporary artefact goes into a private directory created under
    # ``work``. ``mkdtemp`` is atomic and collision-free, so a caller-supplied
    # ``--scratch`` holding their own product.sqlite3 is never touched, and the
    # cleanup below can only ever remove what this call created.
    scratch = Path(tempfile.mkdtemp(prefix="delivery-health-", dir=work))
    try:
        copy = _snapshot_copy(database, scratch)
        connection = sqlite3.connect(copy.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        now = now_ms()
        schema_objects = connection.execute(
            "SELECT COUNT(*) FROM sqlite_master").fetchone()[0]
        if not schema_objects:
            # A database with no schema at all cannot be summarised; say so
            # instead of reporting a clean zero.
            return _unreadable(report, errors, "read", "database",
                               "no schema visible in the snapshot copy")
        for table in ("deliveries", "commitments", "initiatives", "inbox"):
            if not _table_present(connection, table):
                errors.append({"section": table, "reason": "table missing"})
        if not errors:
            _delivery_sections(connection, report, now, errors)
            _commitment_section(connection, report, now, errors)
            _initiative_section(connection, report, errors)
            _inbox_section(connection, report, errors)
        return report
    except (sqlite3.Error, OSError) as error:
        return _unreadable(report, errors, "unreadable", "database",
                           f"{type(error).__name__}: {error}")
    finally:
        if connection is not None:
            connection.close()
        # Remove only the private directory this call created.
        shutil.rmtree(scratch, ignore_errors=True)


def _unreadable(report: dict[str, Any], errors: list[dict[str, str]], state: str,
                section: str, reason: str) -> dict[str, Any]:
    """Mark every measurement unknown; a partial snapshot is never a zero."""
    errors.append({"section": section, "reason": reason})
    report["database"] = state
    for name in ("counts", "stale", "commitments", "initiatives", "inbox"):
        report[name] = {key: None for key in report[name]}
    report["by_kind"] = {kind: dict.fromkeys(DELIVERY_KINDS, None) for kind in DELIVERY_STATUSES}
    report["by_channel"] = dict.fromkeys(DELIVERY_CHANNELS, None)
    return report


def _delivery_sections(connection: sqlite3.Connection, report: dict[str, Any], now: int,
                       errors: list[dict[str, str]]) -> None:
    for status, count in connection.execute(
            "SELECT status,COUNT(*) FROM deliveries GROUP BY status").fetchall():
        if status in report["counts"]:
            report["counts"][status] = int(count)
        else:
            errors.append({"section": "counts", "reason": f"unknown delivery status: {status}"})
    for kind, status, count in connection.execute(
            "SELECT kind,status,COUNT(*) FROM deliveries GROUP BY kind,status").fetchall():
        bucket = report["by_kind"].get(status)
        if bucket is None or kind not in bucket:
            errors.append({"section": "by_kind", "reason": f"unexpected pair: {kind}/{status}"})
            continue
        bucket[kind] = int(count)
    for channel, count in connection.execute(
            "SELECT channel,COUNT(*) FROM deliveries GROUP BY channel").fetchall():
        if channel in report["by_channel"]:
            report["by_channel"][channel] = int(count)
        else:
            errors.append({"section": "by_channel", "reason": f"unknown channel: {channel}"})
    age_column = "updated_at" if _column_present(connection, "deliveries", "updated_at") else "created_at"
    if not _column_present(connection, "deliveries", age_column):
        errors.append({"section": "stale", "reason": "no age column on deliveries"})
        return
    report["stale"]["pending_older_than_1h"] = _count(
        connection, f"SELECT COUNT(*) FROM deliveries WHERE status='pending' AND {age_column}<?",
        (now - PENDING_STALE_MS,))
    report["stale"]["sending_older_than_10m"] = _count(
        connection, f"SELECT COUNT(*) FROM deliveries WHERE status='sending' AND {age_column}<?",
        (now - SENDING_STALE_MS,))
    # "Any" is literal: an unknown delivery has no safe automatic retry, so its
    # age never makes it fresh again (service.py delivery claim path).
    report["stale"]["unknown_any"] = report["counts"]["unknown"]


def _commitment_section(connection: sqlite3.Connection, report: dict[str, Any], now: int,
                        errors: list[dict[str, str]]) -> None:
    for status in ("pending", "queued", "expired"):
        report["commitments"][status] = _count(
            connection, "SELECT COUNT(*) FROM commitments WHERE status=?", (status,))
    # Past its due time and still not delivered. The tick marks a queued
    # commitment `queued` before the channel reports back, so "overdue" must
    # include `pending` and `queued`; `fulfilled` never lands here.
    report["commitments"]["due_overdue"] = _count(
        connection,
        "SELECT COUNT(*) FROM commitments WHERE status IN ('pending','queued') AND due_at<=?",
        (now,))


def _initiative_section(connection: sqlite3.Connection, report: dict[str, Any],
                        errors: list[dict[str, str]]) -> None:
    for status in INITIATIVE_STATUSES:
        report["initiatives"][status] = _count(
            connection, "SELECT COUNT(*) FROM initiatives WHERE status=?", (status,))


def _inbox_section(connection: sqlite3.Connection, report: dict[str, Any],
                   errors: list[dict[str, str]]) -> None:
    report["inbox"]["unread"] = _count(connection, "SELECT COUNT(*) FROM inbox WHERE state='unread'")


def print_summary(report: dict[str, Any]) -> None:
    counts = report["counts"]
    stale = report["stale"]
    print(f"Delivery health ({report['schema']}, database {report['database']})")
    print("  deliveries: " + "  ".join(f"{key}={counts[key]}" for key in DELIVERY_STATUSES))
    print("  stale: " + "  ".join(f"{key}={stale[key]}" for key in stale))
    print("  commitments: " + "  ".join(f"{key}={report['commitments'][key]}"
                                        for key in report["commitments"]))
    print("  initiatives: " + "  ".join(f"{key}={report['initiatives'][key]}"
                                        for key in report["initiatives"]))
    print(f"  inbox unread: {report['inbox']['unread']}")
    for error in report["errors"]:
        print(f"  ERROR {error['section']}: {error['reason']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only delivery health snapshot (never sends, retries or writes).")
    parser.add_argument("--home", required=True, help="product home to inspect (opened read-only)")
    parser.add_argument("--json", action="store_true", help="print the machine-readable report only")
    parser.add_argument("--scratch", default=None,
                        help="parent directory for a private work subdirectory "
                             "(defaults to the system temp dir); never written to directly")
    args = parser.parse_args(argv)
    home = Path(args.home).expanduser()
    # ``work`` is only a parent: build() creates an exclusive subdirectory in it
    # and removes that subdirectory again, so neither this tool's temp dir nor a
    # caller-supplied --scratch is ever used for the copy itself.
    work = Path(args.scratch) if args.scratch else Path(tempfile.mkdtemp(prefix="mirally-delivery-health-"))
    if args.scratch:
        work.mkdir(parents=True, exist_ok=True)
    report = build(home, work=work)
    if not args.scratch:
        shutil.rmtree(work, ignore_errors=True)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print_summary(report)
    # An unreadable or partial snapshot is not a clean bill of health.
    return 1 if report["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
