"""Durable observation store with one coordinated SQLite writer.

The store is intentionally independent from OpenClaw's session database and
from UFO.  A bounded command queue feeds one writer connection; reads use
short-lived read connections.  Observations are append-only and consumers use
``ingest_seq`` cursors rather than wall-clock timestamps.
"""

from __future__ import annotations

import hashlib
import json
import os
import queue
import secrets
import sqlite3
import threading
import time
import uuid
from concurrent.futures import Future
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .models import Observation, ObservationInput, opaque_id, utc_ms


SCHEMA_VERSION = 2
MAX_QUERY_LIMIT = 500


class StoreClosedError(RuntimeError):
    pass


class StoreQueueFullError(RuntimeError):
    pass


class CursorExpiredError(RuntimeError):
    pass


@dataclass(frozen=True)
class AssetReservation:
    asset_id: str
    temp_path: Path
    final_path: Path
    mime_type: str
    scope: str
    policy_version: int
    expires_at_utc_ms: Optional[int]


@dataclass(frozen=True)
class StoreHealth:
    available: bool
    schema_version: int
    store_generation: str
    queue_size: int
    oldest_ingest_seq: Optional[int]
    newest_ingest_seq: Optional[int]
    pending_assets: int
    db_bytes: int
    wal_bytes: int
    error: Optional[str] = None


@dataclass
class _Command:
    operation: str
    args: Tuple[Any, ...]
    kwargs: Dict[str, Any]
    future: Future


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _from_json(value: Optional[str], default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


class ObservationStore:
    """Append-only observation and asset store.

    ``append`` and all mutating methods synchronously wait for the coordinated
    writer.  This keeps the public API simple for current callers while still
    guaranteeing that SQLite mutations, migrations, cursor acknowledgements
    and retention share one writer connection.
    """

    def __init__(
        self,
        path: Path,
        *,
        asset_directory: Optional[Path] = None,
        queue_size: int = 4096,
        command_timeout: float = 5.0,
        read_only: bool = False,
    ) -> None:
        self.path = Path(path)
        self._read_only = read_only
        if not read_only:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.asset_directory = Path(asset_directory or self.path.parent / "assets")
        if not read_only:
            self.asset_directory.mkdir(parents=True, exist_ok=True)
        self._queue: "queue.Queue[Optional[_Command]]" = queue.Queue(maxsize=queue_size)
        self._command_timeout = command_timeout
        self._closed = threading.Event()
        self._ready = threading.Event()
        self._startup_error: Optional[BaseException] = None
        self._last_error: Optional[str] = None
        if read_only:
            self._writer = None
            # Opening a consumer must never create or migrate the source DB.
            connection = self._connect()
            try:
                connection.execute("SELECT value FROM schema_meta WHERE key='store_generation'").fetchone()
            finally:
                connection.close()
            self._ready.set()
            return
        self._writer = threading.Thread(
            target=self._writer_main,
            name="mirally-activity-db-writer",
            daemon=True,
        )
        self._writer.start()
        if not self._ready.wait(command_timeout):
            self.close()
            raise TimeoutError("activity store writer did not initialize")
        if self._startup_error is not None:
            error = self._startup_error
            self.close()
            raise RuntimeError("activity store initialization failed") from error

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.path.resolve().as_uri() + "?mode=ro" if self._read_only else str(self.path),
            uri=self._read_only,
            timeout=max(0.25, self._command_timeout),
            isolation_level=None,
            check_same_thread=True,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 1000")
        return connection

    def _configure_writer(self, connection: sqlite3.Connection) -> None:
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = FULL")
        connection.execute("PRAGMA wal_autocheckpoint = 1000")

    def _initialize_schema(self, connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS schema_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS observations (
                ingest_seq INTEGER PRIMARY KEY AUTOINCREMENT,
                observation_id TEXT NOT NULL UNIQUE,
                schema_version INTEGER NOT NULL,
                kind TEXT NOT NULL,
                source_id TEXT NOT NULL,
                source_epoch TEXT NOT NULL,
                source_seq INTEGER NOT NULL,
                observed_at_utc_ms INTEGER NOT NULL,
                received_at_utc_ms INTEGER NOT NULL,
                monotonic_ns INTEGER NOT NULL,
                boot_id TEXT NOT NULL,
                clock_epoch INTEGER NOT NULL,
                time_quality TEXT NOT NULL,
                session_instance_id TEXT,
                process_instance_id TEXT,
                window_instance_id TEXT,
                app_id TEXT,
                actor TEXT NOT NULL,
                actor_basis TEXT NOT NULL,
                control_owner TEXT NOT NULL,
                policy_version INTEGER NOT NULL,
                collection_level TEXT NOT NULL,
                privacy_status TEXT NOT NULL,
                caused_by_json TEXT NOT NULL,
                capture_group_id TEXT,
                capture_started_at INTEGER,
                capture_finished_at INTEGER,
                payload_json TEXT NOT NULL,
                uia_summary_json TEXT NOT NULL,
                visual_summary_json TEXT NOT NULL,
                ocr_summary_json TEXT NOT NULL DEFAULT '{}',
                asset_refs_json TEXT NOT NULL,
                quality TEXT NOT NULL,
                missing_reasons_json TEXT NOT NULL,
                truncated INTEGER NOT NULL,
                identity_confidence TEXT NOT NULL,
                created_at_utc_ms INTEGER NOT NULL,
                UNIQUE(source_id, source_epoch, source_seq)
            );
            CREATE INDEX IF NOT EXISTS idx_observations_observed
                ON observations(observed_at_utc_ms, ingest_seq);
            CREATE INDEX IF NOT EXISTS idx_observations_app_time
                ON observations(app_id, observed_at_utc_ms, ingest_seq);
            CREATE INDEX IF NOT EXISTS idx_observations_actor_time
                ON observations(actor, observed_at_utc_ms, ingest_seq);
            CREATE INDEX IF NOT EXISTS idx_observations_kind_time
                ON observations(kind, observed_at_utc_ms, ingest_seq);
            CREATE TABLE IF NOT EXISTS assets (
                asset_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                relative_path TEXT NOT NULL UNIQUE,
                mime_type TEXT NOT NULL,
                byte_size INTEGER NOT NULL DEFAULT 0,
                sha256 TEXT,
                scope TEXT NOT NULL,
                policy_version INTEGER NOT NULL,
                created_at_utc_ms INTEGER NOT NULL,
                expires_at_utc_ms INTEGER,
                committed_at_utc_ms INTEGER,
                observation_id TEXT,
                FOREIGN KEY(observation_id) REFERENCES observations(observation_id)
            );
            CREATE INDEX IF NOT EXISTS idx_assets_expiry ON assets(status, expires_at_utc_ms);
            CREATE TABLE IF NOT EXISTS memorized_purge_assets (
                asset_id TEXT PRIMARY KEY,
                purge_key TEXT NOT NULL,
                relative_path TEXT NOT NULL,
                store_generation TEXT NOT NULL,
                through_ingest_seq INTEGER NOT NULL,
                before_utc_ms INTEGER NOT NULL,
                last_error TEXT,
                reserved_at_utc_ms INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_memorized_purge_batch
                ON memorized_purge_assets(purge_key);
            CREATE TABLE IF NOT EXISTS process_instances (
                process_instance_id TEXT PRIMARY KEY,
                boot_id TEXT NOT NULL,
                pid INTEGER,
                created_at_utc_ms INTEGER,
                app_id TEXT,
                confidence TEXT NOT NULL,
                first_seen_utc_ms INTEGER NOT NULL,
                last_seen_utc_ms INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS window_instances (
                window_instance_id TEXT PRIMARY KEY,
                process_instance_id TEXT,
                hwnd INTEGER,
                generation INTEGER NOT NULL,
                first_seen_utc_ms INTEGER NOT NULL,
                last_seen_utc_ms INTEGER NOT NULL,
                confidence TEXT NOT NULL,
                FOREIGN KEY(process_instance_id) REFERENCES process_instances(process_instance_id)
            );
            CREATE TABLE IF NOT EXISTS collector_state (
                key TEXT PRIMARY KEY,
                value_json TEXT NOT NULL,
                updated_at_utc_ms INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS source_cursors (
                consumer_id TEXT NOT NULL,
                scope_hash TEXT NOT NULL,
                store_generation TEXT NOT NULL,
                acked_ingest_seq INTEGER NOT NULL,
                updated_at_utc_ms INTEGER NOT NULL,
                PRIMARY KEY(consumer_id, scope_hash)
            );
            """
        )
        current = connection.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()
        if current is None:
            connection.execute(
                "INSERT INTO schema_meta(key, value) VALUES('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )
        else:
            current_version = int(current[0])
            if current_version == 1:
                # OCR is an additive enrichment.  Existing observations keep
                # an empty summary and remain readable after the migration.
                columns = {
                    str(row[1])
                    for row in connection.execute("PRAGMA table_info(observations)").fetchall()
                }
                if "ocr_summary_json" not in columns:
                    connection.execute(
                        "ALTER TABLE observations ADD COLUMN ocr_summary_json TEXT NOT NULL DEFAULT '{}'"
                    )
                connection.execute(
                    "UPDATE schema_meta SET value = ? WHERE key = 'schema_version'",
                    (str(SCHEMA_VERSION),),
                )
            elif current_version != SCHEMA_VERSION:
                raise RuntimeError(
                    f"Unsupported activity schema {current_version}, expected {SCHEMA_VERSION}"
                )
        generation = connection.execute(
            "SELECT value FROM schema_meta WHERE key = 'store_generation'"
        ).fetchone()
        if generation is None:
            connection.execute(
                "INSERT INTO schema_meta(key, value) VALUES('store_generation', ?)",
                (opaque_id("store"),),
            )

    def _writer_main(self) -> None:
        connection: Optional[sqlite3.Connection] = None
        try:
            connection = self._connect()
            self._configure_writer(connection)
            self._initialize_schema(connection)
        except BaseException as error:  # surfaced to the constructor
            self._startup_error = error
            self._ready.set()
            if connection is not None:
                connection.close()
            return
        self._ready.set()
        while True:
            command = self._queue.get()
            if command is None:
                break
            try:
                result = self._dispatch(connection, command.operation, *command.args, **command.kwargs)
            except BaseException as error:
                self._last_error = f"{type(error).__name__}: {error}"
                command.future.set_exception(error)
            else:
                command.future.set_result(result)
        connection.close()
        self._closed.set()

    def _dispatch(self, connection: sqlite3.Connection, operation: str, *args: Any, **kwargs: Any) -> Any:
        method = getattr(self, f"_write_{operation}")
        return method(connection, *args, **kwargs)

    def _submit(self, operation: str, *args: Any, **kwargs: Any) -> Any:
        if self._read_only:
            raise PermissionError("activity observation consumer is read-only")
        if self._closed.is_set() or not self._writer.is_alive():
            raise StoreClosedError("activity store is closed")
        future: Future = Future()
        command = _Command(operation, args, kwargs, future)
        try:
            self._queue.put(command, timeout=self._command_timeout)
        except queue.Full as error:
            raise StoreQueueFullError("activity store writer queue is full") from error
        return future.result(timeout=self._command_timeout)

    @staticmethod
    def _observation_params(observation: ObservationInput, received: int) -> Tuple[Any, ...]:
        return (
            observation.observation_id if isinstance(observation, Observation) else opaque_id("obs"),
            SCHEMA_VERSION,
            observation.kind,
            observation.source_id,
            observation.source_epoch,
            observation.source_seq,
            observation.observed_at_utc_ms,
            received,
            observation.monotonic_ns,
            observation.boot_id,
            observation.clock_epoch,
            observation.time_quality,
            observation.session_instance_id,
            observation.process_instance_id,
            observation.window_instance_id,
            observation.app_id,
            observation.actor,
            observation.actor_basis,
            observation.control_owner,
            observation.policy_version,
            observation.collection_level,
            observation.privacy_status,
            _json(list(observation.caused_by)),
            observation.capture_group_id,
            observation.capture_started_at,
            observation.capture_finished_at,
            _json(observation.payload),
            _json(observation.uia_summary or {}),
            _json(observation.visual_summary or {}),
            _json(observation.ocr_summary or {}),
            _json(list(observation.asset_refs)),
            observation.quality,
            _json(list(observation.missing_reasons)),
            int(observation.truncated),
            observation.identity_confidence,
            received,
        )

    def _write_append(self, connection: sqlite3.Connection, observation: Observation) -> Observation:
        observation.validate()
        received = observation.received_at_utc_ms or utc_ms()
        existing = connection.execute(
            "SELECT * FROM observations WHERE source_id = ? AND source_epoch = ? AND source_seq = ?",
            (observation.source_id, observation.source_epoch, observation.source_seq),
        ).fetchone()
        if existing is not None:
            return self._row_to_observation(existing)
        for asset_id in observation.asset_refs:
            if connection.execute(
                "SELECT 1 FROM memorized_purge_assets WHERE asset_id=?", (asset_id,)
            ).fetchone() is not None:
                raise ValueError("observation asset is reserved for memorized retention deletion")
        params = self._observation_params(observation, received)
        connection.execute(
            """INSERT INTO observations(
                observation_id, schema_version, kind, source_id, source_epoch, source_seq,
                observed_at_utc_ms, received_at_utc_ms, monotonic_ns, boot_id, clock_epoch,
                time_quality, session_instance_id, process_instance_id, window_instance_id,
                app_id, actor, actor_basis, control_owner, policy_version, collection_level,
                privacy_status, caused_by_json, capture_group_id, capture_started_at,
                capture_finished_at, payload_json, uia_summary_json, visual_summary_json,
                ocr_summary_json, asset_refs_json, quality, missing_reasons_json, truncated,
                identity_confidence, created_at_utc_ms
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            params,
        )
        row = connection.execute(
            "SELECT * FROM observations WHERE observation_id = ?", (params[0],)
        ).fetchone()
        assert row is not None
        return self._row_to_observation(row)

    def _write_reserve_asset(
        self,
        connection: sqlite3.Connection,
        asset_id: str,
        relative_path: str,
        mime_type: str,
        scope: str,
        policy_version: int,
        expires_at_utc_ms: Optional[int],
    ) -> AssetReservation:
        created = utc_ms()
        connection.execute(
            """INSERT INTO assets(asset_id,status,relative_path,mime_type,scope,policy_version,
               created_at_utc_ms,expires_at_utc_ms) VALUES(?,?,?,?,?,?,?,?)""",
            (asset_id, "pending", relative_path, mime_type, scope, policy_version, created, expires_at_utc_ms),
        )
        return AssetReservation(
            asset_id,
            self.asset_directory / (relative_path + ".pending"),
            self.asset_directory / relative_path,
            mime_type,
            scope,
            policy_version,
            expires_at_utc_ms,
        )

    def _write_commit_asset(
        self,
        connection: sqlite3.Connection,
        reservation: AssetReservation,
        byte_size: int,
        sha256: str,
        observation: Observation,
    ) -> Observation:
        if not reservation.final_path.is_file():
            raise FileNotFoundError(f"asset is not ready: {reservation.asset_id}")
        connection.execute("BEGIN IMMEDIATE")
        try:
            asset = connection.execute(
                "SELECT status FROM assets WHERE asset_id = ?", (reservation.asset_id,)
            ).fetchone()
            if asset is None or asset[0] != "pending":
                raise ValueError(f"asset is not pending: {reservation.asset_id}")
            stored = self._write_append(connection, observation)
            connection.execute(
                """UPDATE assets SET status='ready', byte_size=?, sha256=?,
                   committed_at_utc_ms=?, observation_id=? WHERE asset_id=?""",
                (byte_size, sha256, utc_ms(), stored.observation_id, reservation.asset_id),
            )
            connection.commit()
            return stored
        except BaseException:
            connection.rollback()
            raise

    def _write_fail_asset(self, connection: sqlite3.Connection, asset_id: str, status: str) -> None:
        if status not in {"missing", "expired"}:
            raise ValueError("invalid asset failure status")
        connection.execute("UPDATE assets SET status = ? WHERE asset_id = ?", (status, asset_id))

    def _write_prepare_cleanup(
        self, connection: sqlite3.Connection, now_utc_ms: int, observation_cutoff_utc_ms: int,
        max_asset_bytes: Optional[int] = None,
        preserve_observations: bool = False,
    ) -> Tuple[Tuple[str, str], int]:
        """Reserve deletions in SQLite without doing filesystem IO in the writer."""

        if preserve_observations:
            # The semantic-memory retirement journal owns raw deletion. Age,
            # image expiry and quota must not remove evidence behind its back.
            return (), 0

        rows = connection.execute(
            "SELECT asset_id, relative_path FROM assets WHERE status='ready' AND expires_at_utc_ms IS NOT NULL AND expires_at_utc_ms <= ?",
            (now_utc_ms,),
        ).fetchall()
        expired = {str(row[0]): str(row[1]) for row in rows}
        if max_asset_bytes is not None:
            ready = connection.execute("SELECT asset_id,relative_path,byte_size FROM assets WHERE status='ready' ORDER BY created_at_utc_ms,rowid").fetchall()
            total = sum(int(row[2]) for row in ready if str(row[0]) not in expired)
            for asset_id, relative_path, byte_size in ready:
                if total <= max(0, max_asset_bytes):
                    break
                if str(asset_id) not in expired:
                    expired[str(asset_id)] = str(relative_path)
                    total -= int(byte_size)
        asset_ids = tuple(expired.items())
        if asset_ids:
            connection.executemany(
                "UPDATE assets SET status='deleting' WHERE asset_id=? AND status='ready'",
                ((asset_id,) for asset_id, _ in asset_ids),
            )
        # Keep observations referenced by a deleting asset.  Once the asset is
        # finalized, the next pass can remove those old rows safely.
        deleted = connection.execute(
            """DELETE FROM observations WHERE observed_at_utc_ms < ?
               AND NOT EXISTS (SELECT 1 FROM assets WHERE assets.observation_id=observations.observation_id
                               AND assets.status IN ('ready','deleting'))""",
            (observation_cutoff_utc_ms,),
        ).rowcount
        return asset_ids, int(deleted)

    def _write_finalize_cleanup(
        self, connection: sqlite3.Connection, asset_statuses: Sequence[Tuple[str, str]]
    ) -> None:
        for asset_id, status in asset_statuses:
            if status not in {"expired", "missing"}:
                raise ValueError("invalid cleanup status")
            connection.execute(
                "UPDATE assets SET status=?, observation_id=NULL WHERE asset_id=? AND status='deleting'",
                (status, asset_id),
            )

    def _write_prepare_memorized_purge(
        self, connection: sqlite3.Connection, generation: str, through: int, before: int,
        observation_ids: Optional[Tuple[str, ...]], purge_key: str,
    ) -> Dict[str, Any]:
        connection.execute("PRAGMA secure_delete=ON")
        connection.execute("BEGIN IMMEDIATE")
        try:
            if self._write_generation(connection) != generation:
                raise ValueError("activity store generation changed; memorized purge refused")
            connection.execute("CREATE TEMP TABLE IF NOT EXISTS memorized_purge_targets (observation_id TEXT PRIMARY KEY)")
            connection.execute("DELETE FROM memorized_purge_targets")
            if observation_ids is None:
                connection.execute(
                    """INSERT INTO memorized_purge_targets SELECT observation_id FROM observations
                       WHERE ingest_seq<=? AND observed_at_utc_ms<?""", (through, before))
            else:
                connection.executemany(
                    """INSERT OR IGNORE INTO memorized_purge_targets SELECT observation_id FROM observations
                       WHERE observation_id=? AND ingest_seq<=? AND observed_at_utc_ms<?""",
                    ((value, through, before) for value in observation_ids))
            # An owner and an explicit asset_refs entry both count as a
            # reference. Assets shared with any retained row stay available.
            for name in ("memorized_target_asset_refs", "memorized_retained_asset_refs"):
                connection.execute(f"CREATE TEMP TABLE IF NOT EXISTS {name} (asset_id TEXT PRIMARY KEY)")
                connection.execute(f"DELETE FROM {name}")
            connection.execute("""INSERT OR IGNORE INTO memorized_target_asset_refs
                SELECT ref.value FROM observations o JOIN memorized_purge_targets t
                    ON t.observation_id=o.observation_id, json_each(o.asset_refs_json) ref
                WHERE ref.value IS NOT NULL""")
            connection.execute("""INSERT OR IGNORE INTO memorized_retained_asset_refs
                SELECT ref.value FROM observations o, json_each(o.asset_refs_json) ref
                WHERE ref.value IS NOT NULL
                    AND o.observation_id NOT IN (SELECT observation_id FROM memorized_purge_targets)""")
            candidates = connection.execute("""
                SELECT a.asset_id,a.relative_path FROM assets a
                WHERE (a.observation_id IN (SELECT observation_id FROM memorized_purge_targets)
                    OR a.asset_id IN (SELECT asset_id FROM memorized_target_asset_refs))
                AND (a.observation_id IS NULL
                     OR a.observation_id IN (SELECT observation_id FROM memorized_purge_targets))
                AND a.asset_id NOT IN (SELECT asset_id FROM memorized_retained_asset_refs)
            """).fetchall()
            connection.executemany(
                """INSERT OR IGNORE INTO memorized_purge_assets
                   (asset_id,purge_key,relative_path,store_generation,through_ingest_seq,before_utc_ms,reserved_at_utc_ms)
                   VALUES(?,?,?,?,?,?,?)""",
                ((row[0], purge_key, row[1], generation, through, before, utc_ms()) for row in candidates))
            connection.execute("""UPDATE assets SET status='purging',observation_id=NULL
                WHERE asset_id IN (SELECT asset_id FROM memorized_purge_assets WHERE purge_key=?)""", (purge_key,))
            connection.execute("""UPDATE assets SET observation_id=NULL
                WHERE observation_id IN (SELECT observation_id FROM memorized_purge_targets)""")
            deleted = connection.execute("""DELETE FROM observations
                WHERE observation_id IN (SELECT observation_id FROM memorized_purge_targets)""").rowcount
            oldest = connection.execute("SELECT MIN(ingest_seq) FROM observations").fetchone()[0]
            latest = connection.execute("SELECT seq FROM sqlite_sequence WHERE name='observations'").fetchone()
            prefix = int(oldest) - 1 if oldest is not None else int(latest[0] if latest else 0)
            previous = connection.execute(
                "SELECT value FROM schema_meta WHERE key='memorized_retention_ingest_seq'").fetchone()
            prefix = max(prefix, int(previous[0]) if previous else 0)
            connection.execute("""INSERT INTO schema_meta(key,value) VALUES('memorized_retention_ingest_seq',?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value""", (str(prefix),))
            state_row = connection.execute(
                "SELECT value_json FROM collector_state WHERE key='memorized_retention'").fetchone()
            previous_state = _from_json(state_row[0], {}) if state_row else {}
            same_batch = previous_state.get("purge_key") == purge_key
            receipt: Dict[str, Any] = {
                "purge_key": purge_key, "store_generation": generation,
                "through_ingest_seq": through, "before_utc_ms": before,
                "retention_ingest_seq": prefix, "retention_floor": prefix,
                "observations_deleted": int(deleted) + (int(previous_state.get("observations_deleted", 0)) if same_batch else 0),
                "watermark": {
                    "retention_floor": prefix,
                    "through_ingest_seq": through,
                    "before_utc_ms": before,
                },
                "status": "pending", "updated_at_utc_ms": utc_ms(),
            }
            if observation_ids is not None:
                receipt["observation_ids"] = list(observation_ids)
            self._write_set_state(connection, "memorized_retention", receipt)
            connection.commit()
            receipt["assets"] = tuple((str(row[0]), str(row[1])) for row in connection.execute(
                "SELECT asset_id,relative_path FROM memorized_purge_assets WHERE purge_key=?", (purge_key,)))
            return receipt
        except BaseException:
            connection.rollback()
            raise

    def _write_finalize_memorized_purge(
        self, connection: sqlite3.Connection, generation: str, purge_key: str,
        outcomes: Sequence[Tuple[str, Optional[str]]],
    ) -> Tuple[int, int]:
        connection.execute("BEGIN IMMEDIATE")
        try:
            if self._write_generation(connection) != generation:
                raise ValueError("activity store generation changed; memorized purge refused")
            deleted = 0
            for asset_id, error in outcomes:
                if error is not None:
                    connection.execute("""UPDATE memorized_purge_assets SET last_error=?
                        WHERE asset_id=? AND purge_key=?""", (error[:500], asset_id, purge_key))
                    continue
                row = connection.execute("SELECT 1 FROM memorized_purge_assets WHERE asset_id=? AND purge_key=?",
                                         (asset_id, purge_key)).fetchone()
                if row is None:
                    continue
                deleted += connection.execute("DELETE FROM assets WHERE asset_id=?", (asset_id,)).rowcount
                connection.execute("DELETE FROM memorized_purge_assets WHERE asset_id=? AND purge_key=?", (asset_id, purge_key))
            pending = connection.execute("SELECT COUNT(*) FROM memorized_purge_assets WHERE purge_key=?", (purge_key,)).fetchone()[0]
            connection.commit()
            return int(deleted), int(pending)
        except BaseException:
            connection.rollback()
            raise

    def _write_finish_memorized_purge(self, connection: sqlite3.Connection, receipt: Dict[str, Any]) -> Dict[str, Any]:
        """Reclaim deleted SQLite pages without claiming success while busy."""
        if self._write_generation(connection) != receipt["store_generation"]:
            raise ValueError("activity store generation changed; memorized purge refused")
        errors: List[str] = []
        vacuumed = False
        checkpoint_busy = False
        before_sizes = receipt["database_bytes_before"]
        try:
            busy, _, _ = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            checkpoint_busy = bool(busy)
            if busy:
                errors.append("SQLite checkpoint is busy; old WAL pages may remain")
            else:
                deadline = time.monotonic() + max(0.1, min(3.0, self._command_timeout / 2))
                connection.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
                try:
                    connection.execute("VACUUM")
                    vacuumed = True
                finally:
                    connection.set_progress_handler(None, 0)
        except sqlite3.Error as error:
            errors.append(f"SQLite space reclamation incomplete: {error}")
        result = dict(receipt)
        result["reclamation"] = {"vacuumed": vacuumed, "checkpoint_busy": checkpoint_busy,
                                 "errors": errors, "before_bytes": before_sizes}
        result["status"] = "complete" if result["assets_pending"] == 0 and not errors else "pending"
        result["logical_complete"] = result["assets_pending"] == 0
        result["updated_at_utc_ms"] = utc_ms()
        self._write_memorized_receipt(connection, result)
        try:
            busy, _, _ = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            if busy and not checkpoint_busy:
                result["reclamation"]["checkpoint_busy"] = True
                result["reclamation"]["errors"].append("SQLite final checkpoint is busy; WAL truncation deferred")
                result["status"] = "pending"
                self._write_memorized_receipt(connection, result)
        except sqlite3.Error as error:
            result["reclamation"]["errors"].append(f"SQLite final checkpoint incomplete: {error}")
            result["status"] = "pending"
            self._write_memorized_receipt(connection, result)
        result["reclamation"]["after_bytes"] = self._retention_file_sizes()
        database_reclaimed = max(0, sum(before_sizes.values()) - sum(result["reclamation"]["after_bytes"].values()))
        result["reclamation"].update(
            file_reclaimed_bytes=result["file_reclaimed_bytes"],
            database_reclaimed_bytes=database_reclaimed,
            reclaimed_bytes=result["file_reclaimed_bytes"] + database_reclaimed,
        )
        # Keep the measured result available after a crash. This tiny receipt
        # can add metadata WAL pages, so truncate once more before reporting
        # the final on-disk measurement below.
        self._write_memorized_receipt(connection, result)
        try:
            busy, _, _ = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            if busy and not result["reclamation"]["checkpoint_busy"]:
                result["reclamation"]["checkpoint_busy"] = True
                result["reclamation"]["errors"].append("SQLite receipt checkpoint is busy; WAL truncation deferred")
                result["status"] = "pending"
                self._write_memorized_receipt(connection, result)
        except sqlite3.Error as error:
            result["reclamation"]["errors"].append(f"SQLite receipt checkpoint incomplete: {error}")
            result["status"] = "pending"
            self._write_memorized_receipt(connection, result)
        result["reclamation"]["after_bytes"] = self._retention_file_sizes()
        database_reclaimed = max(0, sum(before_sizes.values()) - sum(result["reclamation"]["after_bytes"].values()))
        result["reclamation"].update(database_reclaimed_bytes=database_reclaimed,
                                     reclaimed_bytes=result["file_reclaimed_bytes"] + database_reclaimed)
        return result

    def _write_memorized_receipt(self, connection: sqlite3.Connection, receipt: Dict[str, Any]) -> None:
        """Publish a completed batch without regressing another writer's floor."""
        connection.execute("BEGIN IMMEDIATE")
        try:
            if self._write_generation(connection) != receipt["store_generation"]:
                raise ValueError("activity store generation changed; memorized purge refused")
            retained = connection.execute(
                "SELECT value FROM schema_meta WHERE key='memorized_retention_ingest_seq'"
            ).fetchone()
            floor = max(int(receipt.get("retention_floor", 0)), int(retained[0]) if retained else 0)
            receipt["retention_floor"] = receipt["retention_ingest_seq"] = floor
            receipt["watermark"] = {**receipt.get("watermark", {}), "retention_floor": floor}
            self._write_set_state(connection, "memorized_retention", receipt)
            connection.commit()
        except BaseException:
            connection.rollback()
            raise

    def _retention_file_sizes(self) -> Dict[str, int]:
        result = {}
        for name, path in (("database", self.path), ("wal", self.path.with_name(self.path.name + "-wal"))):
            try:
                result[name] = path.stat().st_size
            except FileNotFoundError:
                result[name] = 0
        return result

    def _write_set_state(self, connection: sqlite3.Connection, key: str, value: Any) -> None:
        connection.execute(
            """INSERT INTO collector_state(key,value_json,updated_at_utc_ms) VALUES(?,?,?)
               ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,
               updated_at_utc_ms=excluded.updated_at_utc_ms""",
            (key, _json(value), utc_ms()),
        )

    def _write_ack(self, connection: sqlite3.Connection, consumer_id: str, scope_hash: str, cursor: int) -> str:
        generation = self._write_generation(connection)
        retained = connection.execute("SELECT value FROM schema_meta WHERE key='memorized_retention_ingest_seq'").fetchone()
        if retained is not None and cursor < int(retained[0]):
            raise CursorExpiredError(f"cursor {cursor} is older than memorized retention watermark {retained[0]}")
        oldest = connection.execute("SELECT MIN(ingest_seq) FROM observations").fetchone()[0]
        if oldest is not None and cursor < oldest - 1:
            raise CursorExpiredError(f"cursor {cursor} is older than store watermark {oldest}")
        connection.execute(
            """INSERT INTO source_cursors(consumer_id,scope_hash,store_generation,acked_ingest_seq,updated_at_utc_ms)
               VALUES(?,?,?,?,?) ON CONFLICT(consumer_id,scope_hash) DO UPDATE SET
               store_generation=excluded.store_generation, acked_ingest_seq=excluded.acked_ingest_seq,
               updated_at_utc_ms=excluded.updated_at_utc_ms""",
            (consumer_id, scope_hash, generation, cursor, utc_ms()),
        )
        return generation

    @staticmethod
    def _write_generation(connection: sqlite3.Connection) -> str:
        row = connection.execute("SELECT value FROM schema_meta WHERE key='store_generation'").fetchone()
        return str(row[0])

    def _row_to_observation(self, row: sqlite3.Row) -> Observation:
        return Observation(
            observation_id=row["observation_id"],
            kind=row["kind"],
            source_id=row["source_id"],
            source_epoch=row["source_epoch"],
            source_seq=row["source_seq"],
            ingest_seq=row["ingest_seq"],
            observed_at_utc_ms=row["observed_at_utc_ms"],
            received_at_utc_ms=row["received_at_utc_ms"],
            monotonic_ns=row["monotonic_ns"],
            boot_id=row["boot_id"],
            clock_epoch=row["clock_epoch"],
            time_quality=row["time_quality"],
            session_instance_id=row["session_instance_id"],
            process_instance_id=row["process_instance_id"],
            window_instance_id=row["window_instance_id"],
            app_id=row["app_id"],
            actor=row["actor"],
            actor_basis=row["actor_basis"],
            control_owner=row["control_owner"],
            policy_version=row["policy_version"],
            collection_level=row["collection_level"],
            privacy_status=row["privacy_status"],
            caused_by=tuple(_from_json(row["caused_by_json"], [])),
            capture_group_id=row["capture_group_id"],
            capture_started_at=row["capture_started_at"],
            capture_finished_at=row["capture_finished_at"],
            payload=_from_json(row["payload_json"], {}),
            uia_summary=_from_json(row["uia_summary_json"], {}),
            visual_summary=_from_json(row["visual_summary_json"], {}),
            ocr_summary=_from_json(row["ocr_summary_json"], {}),
            asset_refs=tuple(_from_json(row["asset_refs_json"], [])),
            quality=row["quality"],
            missing_reasons=tuple(_from_json(row["missing_reasons_json"], [])),
            truncated=bool(row["truncated"]),
            identity_confidence=row["identity_confidence"],
        )

    def append(self, observation: ObservationInput) -> Observation:
        if not isinstance(observation, Observation):
            observation = Observation.create(**observation.__dict__)
        return self._submit("append", observation)

    def reserve_asset(
        self,
        *,
        mime_type: str,
        scope: str,
        policy_version: int,
        expires_at_utc_ms: Optional[int] = None,
    ) -> AssetReservation:
        asset_id = opaque_id("asset")
        relative = f"{asset_id}.{mime_type.rsplit('/', 1)[-1] or 'bin'}"
        return self._submit(
            "reserve_asset", asset_id, relative, mime_type, scope, policy_version, expires_at_utc_ms
        )

    def commit_asset_bytes(
        self,
        reservation: AssetReservation,
        content: bytes,
        observation: Observation,
    ) -> Observation:
        """Atomically publish a file and its observation after policy checks.

        The bytes are written outside SQLite, but the final rename and the
        asset/observation commit are ordered so recovery can distinguish a
        pending file from a published asset.
        """
        if self._read_only:
            raise PermissionError("activity observation consumer is read-only")

        reservation.temp_path.parent.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256(content).hexdigest()
        try:
            with reservation.temp_path.open("xb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(reservation.temp_path, reservation.final_path)
            return self._submit(
                "commit_asset", reservation, len(content), digest, observation
            )
        except BaseException:
            for path in (reservation.temp_path, reservation.final_path):
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
            try:
                self._submit("fail_asset", reservation.asset_id, "missing")
            except Exception:
                pass
            raise

    def fail_asset(self, asset_id: str, status: str = "missing") -> None:
        self._submit("fail_asset", asset_id, status)

    def set_state(self, key: str, value: Any) -> None:
        self._submit("set_state", key, value)

    def get_state(self, key: str, default: Any = None) -> Any:
        connection = self._connect()
        try:
            row = connection.execute("SELECT value_json FROM collector_state WHERE key=?", (key,)).fetchone()
            return default if row is None else _from_json(row[0], default)
        finally:
            connection.close()

    def observation_statistics(self) -> Dict[str, Optional[int]]:
        """Return retained history bounds without reading observation content.

        The timestamp boundary lookups use the observation time index. Keep
        them in the same SQLite snapshot as the count so concurrent collection
        cannot produce a range from a different retained history.
        """
        connection = self._connect()
        try:
            row = connection.execute("""SELECT COUNT(*),
                (SELECT observed_at_utc_ms FROM observations
                 ORDER BY observed_at_utc_ms ASC LIMIT 1),
                (SELECT observed_at_utc_ms FROM observations
                 ORDER BY observed_at_utc_ms DESC LIMIT 1)
                FROM observations""").fetchone()
            return {"observation_count": int(row[0]),
                    "oldest_observed_utc_ms": row[1],
                    "newest_observed_utc_ms": row[2]}
        finally:
            connection.close()

    def ack(self, consumer_id: str, scope_hash: str, cursor: int) -> str:
        return self._submit("ack", consumer_id, scope_hash, int(cursor))

    def cursor(self, consumer_id: str, scope_hash: str) -> Optional[Tuple[str, int]]:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT store_generation, acked_ingest_seq FROM source_cursors WHERE consumer_id=? AND scope_hash=?",
                (consumer_id, scope_hash),
            ).fetchone()
            return None if row is None else (str(row[0]), int(row[1]))
        finally:
            connection.close()

    def generation(self) -> str:
        connection = self._connect()
        try:
            row = connection.execute("SELECT value FROM schema_meta WHERE key='store_generation'").fetchone()
            return str(row[0])
        finally:
            connection.close()

    def read_since(self, after_ingest_seq: int = 0, *, limit: int = 100) -> Tuple[List[Observation], int, bool]:
        limit = max(1, min(MAX_QUERY_LIMIT, int(limit)))
        connection = self._connect()
        try:
            retained = connection.execute("SELECT value FROM schema_meta WHERE key='memorized_retention_ingest_seq'").fetchone()
            if retained is not None and after_ingest_seq < int(retained[0]):
                raise CursorExpiredError(f"cursor {after_ingest_seq} is older than memorized retention watermark {retained[0]}")
            oldest = connection.execute("SELECT MIN(ingest_seq) FROM observations").fetchone()[0]
            if oldest is not None and after_ingest_seq < oldest - 1:
                raise CursorExpiredError(f"cursor {after_ingest_seq} is older than watermark {oldest}")
            rows = connection.execute(
                "SELECT * FROM observations WHERE ingest_seq > ? ORDER BY ingest_seq LIMIT ?",
                (after_ingest_seq, limit + 1),
            ).fetchall()
            has_more = len(rows) > limit
            rows = rows[:limit]
            records = [self._row_to_observation(row) for row in rows]
            next_cursor = records[-1].ingest_seq if records else after_ingest_seq
            return records, int(next_cursor or after_ingest_seq), has_more
        finally:
            connection.close()

    def query(
        self,
        *,
        start_utc_ms: Optional[int] = None,
        end_utc_ms: Optional[int] = None,
        app_ids: Optional[Sequence[str]] = None,
        actors: Optional[Sequence[str]] = None,
        kinds: Optional[Sequence[str]] = None,
        after_ingest_seq: Optional[int] = None,
        before_ingest_seq: Optional[int] = None,
        query: Optional[str] = None,
        descending: bool = False,
        limit: int = 100,
    ) -> List[Observation]:
        limit = max(1, min(MAX_QUERY_LIMIT, int(limit)))
        clauses: List[str] = []
        params: List[Any] = []
        if start_utc_ms is not None:
            clauses.append("observed_at_utc_ms >= ?")
            params.append(int(start_utc_ms))
        if end_utc_ms is not None:
            clauses.append("observed_at_utc_ms < ?")
            params.append(int(end_utc_ms))
        if after_ingest_seq is not None:
            clauses.append("ingest_seq > ?")
            params.append(int(after_ingest_seq))
        if before_ingest_seq is not None:
            clauses.append("ingest_seq < ?")
            params.append(int(before_ingest_seq))
        for field_name, values in (("app_id", app_ids), ("actor", actors), ("kind", kinds)):
            if values:
                placeholders = ",".join("?" for _ in values)
                clauses.append(f"{field_name} IN ({placeholders})")
                params.extend(values)
        if query:
            # Search only persisted JSON/text columns. Escape LIKE metacharacters
            # so developer queries remain literal even when they contain `%`,
            # `_` or a backslash.
            escaped = str(query).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            pattern = f"%{escaped}%"
            clauses.append(
                "(app_id LIKE ? ESCAPE '\\' OR payload_json LIKE ? ESCAPE '\\' "
                "OR uia_summary_json LIKE ? ESCAPE '\\' OR ocr_summary_json LIKE ? ESCAPE '\\')"
            )
            params.extend((pattern, pattern, pattern, pattern))
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        order = "DESC" if descending else "ASC"
        connection = self._connect()
        try:
            rows = connection.execute(
                f"SELECT * FROM observations{where} ORDER BY ingest_seq {order} LIMIT ?",
                (*params, limit),
            ).fetchall()
            return [self._row_to_observation(row) for row in rows]
        finally:
            connection.close()

    def get(self, observation_id: str) -> Optional[Observation]:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM observations WHERE observation_id = ?", (observation_id,)
            ).fetchone()
            return None if row is None else self._row_to_observation(row)
        finally:
            connection.close()

    def get_recent(self, window_seconds: int = 300, *, limit: int = 100) -> List[Observation]:
        now = utc_ms()
        return self.query(
            start_utc_ms=now - max(1, int(window_seconds)) * 1000,
            end_utc_ms=now + 1,
            limit=limit,
        )

    def asset(self, asset_id: str) -> Optional[Mapping[str, Any]]:
        connection = self._connect()
        try:
            row = connection.execute("SELECT * FROM assets WHERE asset_id=?", (asset_id,)).fetchone()
            if row is None:
                return None
            return dict(row)
        finally:
            connection.close()

    def resolve_asset(self, asset_id: str, *, scope: Optional[str] = None) -> Mapping[str, Any]:
        """Resolve an asset without exposing arbitrary filesystem paths."""

        record = self.asset(asset_id)
        if record is None:
            return {"asset_id": asset_id, "status": "missing"}
        if scope is not None and str(record["scope"]) != scope:
            return {"asset_id": asset_id, "status": "denied"}
        status = str(record["status"])
        response: Dict[str, Any] = {
            "asset_id": asset_id,
            "status": "available" if status == "ready" else status,
            "mime_type": record["mime_type"],
            "byte_size": int(record["byte_size"]),
            "sha256": record["sha256"],
            "expires_at_utc_ms": record["expires_at_utc_ms"],
        }
        if status == "ready":
            path = (self.asset_directory / str(record["relative_path"])).resolve()
            try:
                path.relative_to(self.asset_directory.resolve())
            except ValueError:
                return {"asset_id": asset_id, "status": "missing"}
            response["path"] = str(path)
        return response

    def cleanup(self, *, now_utc_ms: Optional[int] = None, observation_retention_days: int = 14,
                max_asset_bytes: Optional[int] = None,
                preserve_observations: bool = False) -> Mapping[str, int]:
        """Delete expired asset files and then finalize their tombstones.

        Files are never deleted while the SQLite writer transaction is open.
        A crash between the two phases leaves ``deleting`` rows for the next
        recovery pass, rather than silently claiming that a file was removed.
        """

        now = int(now_utc_ms or utc_ms())
        cutoff = now - max(1, int(observation_retention_days)) * 24 * 60 * 60 * 1000
        candidates, observations = self._submit("prepare_cleanup", now, cutoff, max_asset_bytes,
                                                preserve_observations)
        finalized: List[Tuple[str, str]] = []
        for asset_id, relative_path in candidates:
            path = (self.asset_directory / relative_path).resolve()
            path.relative_to(self.asset_directory.resolve())
            try:
                path.unlink()
            except FileNotFoundError:
                finalized.append((asset_id, "missing"))
            except OSError:
                # Leave it deleting so doctor/retry can report the real failure.
                continue
            else:
                finalized.append((asset_id, "expired"))
        if finalized:
            self._submit("finalize_cleanup", finalized)
        return {
            "assets_considered": len(candidates),
            "assets_finalized": len(finalized),
            "observations_deleted": observations,
        }

    def purge_memorized(
        self, *, store_generation: str, through_ingest_seq: int, before_utc_ms: int,
        observation_ids: Optional[Sequence[str]] = None,
    ) -> Dict[str, Any]:
        """Purge a confirmed memory batch, never records beyond its boundaries.

        Only the caller can establish that memory was durably written. The
        generation, inclusive ingest boundary and exclusive observed-time
        cutoff protect against a reset source and a concurrent collection tail.
        File failures leave a small durable journal, without observation bodies,
        that an identical call can retry. Unowned asset reservations are kept:
        their contents cannot be attributed to a confirmed observation batch.
        """
        if not isinstance(store_generation, str) or not store_generation or len(store_generation) > 256:
            raise ValueError("store_generation must be a nonempty store identity")
        if type(through_ingest_seq) is not int or through_ingest_seq < 0:
            raise ValueError("through_ingest_seq must be a nonnegative integer")
        if type(before_utc_ms) is not int or before_utc_ms < 0:
            raise ValueError("before_utc_ms must be a nonnegative integer")
        ids = None
        if observation_ids is not None:
            if isinstance(observation_ids, (str, bytes)) or len(observation_ids) > 500:
                raise ValueError("observation_ids must contain at most 500 observation identities")
            if any(not isinstance(value, str) or not value or len(value) > 256 for value in observation_ids):
                raise ValueError("observation_ids must contain nonempty observation identities")
            ids = tuple(sorted(set(observation_ids)))
        key = hashlib.sha256(_json([store_generation, through_ingest_seq, before_utc_ms, ids]).encode("utf-8")).hexdigest()
        database_bytes_before = self._retention_file_sizes()
        receipt = self._submit("prepare_memorized_purge", store_generation, through_ingest_seq, before_utc_ms, ids, key)
        candidates = receipt.pop("assets")
        outcomes: List[Tuple[str, Optional[str]]] = []
        errors: List[Dict[str, str]] = []
        files_deleted = 0
        file_reclaimed_bytes = 0
        directory = self.asset_directory.resolve()
        for asset_id, relative in candidates:
            error = None
            try:
                paths = [self.asset_directory / relative, self.asset_directory / (relative + ".pending")]
                for path in paths:
                    path.resolve().relative_to(directory)
                for path in paths:
                    try:
                        file_bytes = path.lstat().st_size
                        path.unlink()
                        files_deleted += 1
                        file_reclaimed_bytes += file_bytes
                    except FileNotFoundError:
                        pass
            except (OSError, ValueError) as failure:
                error = f"{type(failure).__name__}: {failure}"[:500]
                errors.append({"asset_id": asset_id, "error": error})
            outcomes.append((asset_id, error))
        deleted, pending = self._submit("finalize_memorized_purge", store_generation, key, outcomes)
        receipt.update(assets_considered=len(candidates), assets_deleted=deleted, assets_pending=pending,
                       files_deleted=files_deleted, errors=errors, file_reclaimed_bytes=file_reclaimed_bytes,
                       database_bytes_before=database_bytes_before)
        return self._submit("finish_memorized_purge", receipt)

    def asset_bytes(self) -> int:
        connection = self._connect()
        try:
            return int(connection.execute("SELECT COALESCE(SUM(byte_size),0) FROM assets WHERE status IN ('ready','deleting')").fetchone()[0])
        finally:
            connection.close()

    def health(self) -> StoreHealth:
        try:
            connection = self._connect()
            try:
                generation = str(connection.execute("SELECT value FROM schema_meta WHERE key='store_generation'").fetchone()[0])
                oldest, newest = connection.execute("SELECT MIN(ingest_seq), MAX(ingest_seq) FROM observations").fetchone()
                pending = connection.execute("SELECT COUNT(*) FROM assets WHERE status='pending'").fetchone()[0]
            finally:
                connection.close()
            return StoreHealth(
                True,
                SCHEMA_VERSION,
                generation,
                self._queue.qsize(),
                oldest,
                newest,
                int(pending),
                self.path.stat().st_size if self.path.exists() else 0,
                self.path.with_name(self.path.name + "-wal").stat().st_size
                if self.path.with_name(self.path.name + "-wal").exists()
                else 0,
                self._last_error,
            )
        except Exception as error:
            return StoreHealth(False, SCHEMA_VERSION, "", self._queue.qsize(), None, None, 0, 0, 0, str(error))

    def recover_assets(self, *, orphan_grace_seconds: int = 300) -> Mapping[str, int]:
        """Reconcile files left by a crash without publishing uncommitted data."""

        if self._read_only:
            raise PermissionError("activity observation consumer is read-only")
        now = time.time()
        pending_removed = 0
        orphan_removed = 0
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT asset_id, status, relative_path, committed_at_utc_ms FROM assets"
            ).fetchall()
            known = {str(row[2]) for row in rows}
            for row in rows:
                final_path = self.asset_directory / str(row[2])
                pending_path = final_path.with_name(final_path.name + ".pending")
                if row[1] == "deleting":
                    try:
                        final_path.unlink()
                    except FileNotFoundError:
                        pass
                    except OSError:
                        continue
                    self.fail_asset(str(row[0]), "expired")
                    continue
                if row[1] == "pending" and (now - final_path.stat().st_mtime if final_path.exists() else now) > orphan_grace_seconds:
                    for candidate in (final_path, pending_path):
                        try:
                            candidate.unlink()
                        except FileNotFoundError:
                            pass
                    pending_removed += 1
                if row[1] == "ready" and not final_path.is_file():
                    self.fail_asset(str(row[0]), "missing")
            for candidate in self.asset_directory.iterdir():
                if not candidate.is_file() or candidate.name.endswith(".pending"):
                    continue
                if candidate.name not in known:
                    try:
                        if now - candidate.stat().st_mtime > orphan_grace_seconds:
                            candidate.unlink()
                            orphan_removed += 1
                    except FileNotFoundError:
                        pass
        finally:
            connection.close()
        return {"pending_removed": pending_removed, "orphan_removed": orphan_removed}

    def close(self) -> None:
        if self._closed.is_set():
            return
        if self._writer is not None and self._writer.is_alive():
            try:
                self._queue.put(None, timeout=self._command_timeout)
            except queue.Full:
                return
            self._writer.join(timeout=self._command_timeout)
        self._closed.set()

    def __enter__(self) -> "ObservationStore":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()
