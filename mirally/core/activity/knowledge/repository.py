"""SQLite repository for rebuildable activity projections."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional, Sequence

from .models import ActivityEpisode, ActivityItem, Evidence, MemoryCandidate, MemoryFact


SCHEMA_VERSION = 3


class ArchivedObservationError(ValueError):
    """A memorized source cannot be written back into a raw projection."""


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _from_json(value: Optional[str], default: Any) -> Any:
    try:
        return default if not value else json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return default


def deterministic_id(prefix: str, *values: Any) -> str:
    encoded = "\x1f".join(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str) for value in values)
    return f"{prefix}_{hashlib.sha256(encoded.encode('utf-8')).hexdigest()[:32]}"


@dataclass(frozen=True)
class KnowledgeHealth:
    available: bool
    schema_version: int
    processed_ingest_seq: int
    item_count: int
    episode_count: int
    candidate_count: int
    fts_available: bool
    db_bytes: int
    last_error: Optional[str] = None
    fact_count: int = 0
    projection_error_count: int = 0


class KnowledgeStore:
    """One-writer SQLite store for derived activity knowledge.

    The organizer owns this connection and is the only writer.  Reads use the
    same short critical sections, which keeps the implementation deterministic
    and avoids a second write queue competing with the raw observation store.
    """

    def __init__(self, path: Path, *, read_only: bool = False) -> None:
        self.path = Path(path)
        self.read_only = bool(read_only)
        if not self.read_only:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        elif not self.path.is_file():
            raise FileNotFoundError(
                f"knowledge database not found: {self.path}; run knowledge-process first"
            )
        self._lock = threading.RLock()
        self._closed = False
        self._last_error: Optional[str] = None
        self._fts_available = True
        self._archived_sources_available = False
        self._transaction_depth = 0
        self._savepoint_seq = 0
        if self.read_only:
            self._connection = sqlite3.connect(
                self.path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5.0, check_same_thread=False
            )
        else:
            self._connection = sqlite3.connect(str(self.path), timeout=5.0, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        if not self.read_only:
            self._connection.execute("PRAGMA journal_mode=WAL")
            self._connection.execute("PRAGMA synchronous=NORMAL")
        self._connection.execute("PRAGMA busy_timeout=2000")
        try:
            if self.read_only:
                self._check_read_only_schema()
            else:
                self._initialize()
        except BaseException:
            self._connection.close()
            raise

    def _check_read_only_schema(self) -> None:
        """Validate an existing projection without creating or migrating it."""

        try:
            meta = self._connection.execute(
                "SELECT value FROM knowledge_meta WHERE key='schema_version'"
            ).fetchone()
        except sqlite3.OperationalError as error:
            raise RuntimeError(
                "knowledge database is not initialized; run knowledge-process first"
            ) from error
        if meta is None:
            raise RuntimeError(
                "knowledge database has no schema version; run knowledge-process first"
            )
        try:
            version = int(meta[0])
        except (TypeError, ValueError) as error:
            raise RuntimeError(
                "knowledge database schema version is invalid; run knowledge-process first"
            ) from error
        if version != SCHEMA_VERSION:
            raise RuntimeError(
                f"unsupported knowledge schema {version}, expected {SCHEMA_VERSION}; run knowledge-process first"
            )
        self._fts_available = bool(self._connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='activity_fts'"
        ).fetchone())
        self._archived_sources_available = bool(self._connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='archived_sources'"
        ).fetchone())

    def _initialize(self) -> None:
        with self._lock:
            needs_source_backfill = self._connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='activity_item_sources'"
            ).fetchone() is None
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS knowledge_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS organizer_checkpoints (
                    consumer_id TEXT PRIMARY KEY,
                    source_generation TEXT NOT NULL DEFAULT '',
                    processed_ingest_seq INTEGER NOT NULL DEFAULT 0,
                    updated_at_utc_ms INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS archived_sources (
                    observation_id TEXT PRIMARY KEY,
                    source_generation TEXT NOT NULL DEFAULT '',
                    archived_at_utc_ms INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS activity_items (
                    item_id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    start_utc_ms INTEGER NOT NULL,
                    end_utc_ms INTEGER NOT NULL,
                    app_id TEXT,
                    process_instance_id TEXT,
                    window_instance_id TEXT,
                    title TEXT,
                    url TEXT,
                    document_ref TEXT,
                    text_summary TEXT NOT NULL DEFAULT '',
                    structured_json TEXT NOT NULL,
                    asset_refs_json TEXT NOT NULL,
                    source_observation_ids_json TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    actor_basis TEXT NOT NULL,
                    quality TEXT NOT NULL,
                    missing_reasons_json TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    importance REAL NOT NULL,
                    sensitivity TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    projection_version TEXT NOT NULL,
                    episode_id TEXT,
                    created_at_utc_ms INTEGER NOT NULL,
                    updated_at_utc_ms INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_activity_items_time
                    ON activity_items(start_utc_ms, end_utc_ms, item_id);
                CREATE INDEX IF NOT EXISTS idx_activity_items_app
                    ON activity_items(app_id, start_utc_ms);
                CREATE INDEX IF NOT EXISTS idx_activity_items_window
                    ON activity_items(window_instance_id, start_utc_ms);
                CREATE INDEX IF NOT EXISTS idx_activity_items_episode
                    ON activity_items(episode_id, start_utc_ms);
                CREATE TABLE IF NOT EXISTS activity_item_sources (
                    item_id TEXT NOT NULL,
                    observation_id TEXT NOT NULL,
                    PRIMARY KEY(item_id, observation_id),
                    FOREIGN KEY(item_id) REFERENCES activity_items(item_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_activity_item_sources_observation
                    ON activity_item_sources(observation_id, item_id);
                CREATE TABLE IF NOT EXISTS activity_episodes (
                    episode_id TEXT PRIMARY KEY,
                    start_utc_ms INTEGER NOT NULL,
                    end_utc_ms INTEGER NOT NULL,
                    title TEXT NOT NULL DEFAULT '',
                    primary_app TEXT,
                    primary_window_id TEXT,
                    item_ids_json TEXT NOT NULL,
                    summary TEXT,
                    confidence REAL NOT NULL,
                    finalized INTEGER NOT NULL DEFAULT 0,
                    end_reason TEXT,
                    revision INTEGER NOT NULL DEFAULT 1,
                    data_gaps_json TEXT NOT NULL,
                    projection_version TEXT NOT NULL,
                    created_at_utc_ms INTEGER NOT NULL,
                    updated_at_utc_ms INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_activity_episodes_time
                    ON activity_episodes(start_utc_ms, end_utc_ms);
                CREATE TABLE IF NOT EXISTS activity_evidence (
                    evidence_id TEXT PRIMARY KEY,
                    item_id TEXT NOT NULL,
                    source_observation_ids_json TEXT NOT NULL,
                    observed_at_utc_ms INTEGER NOT NULL,
                    received_at_utc_ms INTEGER,
                    content_type TEXT NOT NULL,
                    text TEXT NOT NULL DEFAULT '',
                    structured_json TEXT NOT NULL,
                    asset_refs_json TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    quality TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    extractor_version TEXT NOT NULL,
                    FOREIGN KEY(item_id) REFERENCES activity_items(item_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_activity_evidence_item
                    ON activity_evidence(item_id, observed_at_utc_ms);
                CREATE TABLE IF NOT EXISTS semantic_annotations (
                    annotation_id TEXT PRIMARY KEY,
                    item_id TEXT NOT NULL,
                    extractor_name TEXT NOT NULL,
                    extractor_version TEXT NOT NULL,
                    model_name TEXT,
                    value_json TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    status TEXT NOT NULL,
                    error TEXT,
                    created_at_utc_ms INTEGER NOT NULL,
                    UNIQUE(item_id, extractor_name, extractor_version)
                );
                CREATE TABLE IF NOT EXISTS memory_candidates (
                    candidate_id TEXT PRIMARY KEY,
                    statement TEXT NOT NULL,
                    fact_key TEXT,
                    subject TEXT,
                    predicate TEXT,
                    object_value TEXT,
                    valid_from_utc_ms INTEGER,
                    valid_to_utc_ms INTEGER,
                    source_episode_ids_json TEXT NOT NULL,
                    source_observation_ids_json TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    extraction_method TEXT NOT NULL,
                    extraction_version TEXT NOT NULL,
                    status TEXT NOT NULL,
                    conflicts_with_json TEXT NOT NULL,
                    created_at_utc_ms INTEGER NOT NULL,
                    updated_at_utc_ms INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_memory_candidates_status
                    ON memory_candidates(status, confidence DESC, updated_at_utc_ms DESC);
                CREATE TABLE IF NOT EXISTS memory_facts (
                    fact_id TEXT PRIMARY KEY,
                    statement TEXT NOT NULL,
                    fact_key TEXT,
                    subject TEXT,
                    predicate TEXT,
                    object_value TEXT,
                    valid_from_utc_ms INTEGER,
                    valid_to_utc_ms INTEGER,
                    source_episode_ids_json TEXT NOT NULL,
                    source_observation_ids_json TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    extraction_method TEXT NOT NULL,
                    extraction_version TEXT NOT NULL,
                    status TEXT NOT NULL,
                    supersedes_json TEXT NOT NULL,
                    conflicts_with_json TEXT NOT NULL,
                    created_at_utc_ms INTEGER NOT NULL,
                    updated_at_utc_ms INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_memory_facts_status
                    ON memory_facts(status, confidence DESC, updated_at_utc_ms DESC);
                CREATE TABLE IF NOT EXISTS projection_errors (
                    consumer_id TEXT NOT NULL,
                    observation_id TEXT NOT NULL,
                    ingest_seq INTEGER,
                    error_type TEXT NOT NULL,
                    error TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 1,
                    first_failed_at_utc_ms INTEGER NOT NULL,
                    last_failed_at_utc_ms INTEGER NOT NULL,
                    PRIMARY KEY(consumer_id, observation_id)
                );
                CREATE INDEX IF NOT EXISTS idx_projection_errors_retry
                    ON projection_errors(consumer_id, attempts, ingest_seq);
                CREATE TABLE IF NOT EXISTS activity_collection_gaps (
                    gap_id TEXT PRIMARY KEY,
                    at_utc_ms INTEGER NOT NULL,
                    session_instance_id TEXT,
                    missing_reasons_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_activity_collection_gaps_time
                    ON activity_collection_gaps(at_utc_ms, session_instance_id);
                """
            )
            # Older stores already retain their source ids in JSON. Backfill
            # the lookup index without changing those items or their versions.
            if needs_source_backfill:
                self._connection.execute(
                    """INSERT OR IGNORE INTO activity_item_sources(item_id, observation_id)
                       SELECT i.item_id, source.value FROM activity_items i,
                       json_each(i.source_observation_ids_json) source"""
                )
            try:
                self._connection.execute(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS activity_fts USING fts5(item_id UNINDEXED, title, url, text_summary, uia_text, ocr_text, clipboard_text, document_ref, entity_names)"
                )
            except sqlite3.OperationalError:
                self._fts_available = False
            self._connection.execute(
                "INSERT INTO knowledge_meta(key,value) VALUES('schema_version',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(SCHEMA_VERSION),),
            )
            self._connection.commit()
            self._archived_sources_available = True

    @property
    def fts_available(self) -> bool:
        return self._fts_available

    def _execute(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Cursor:
        return self._connection.execute(sql, tuple(params))

    def _require_writable(self) -> None:
        if self.read_only:
            raise PermissionError("knowledge store is read-only")

    def _commit(self) -> None:
        if not self._transaction_depth:
            self._connection.commit()

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Make a projection atomic, including its evidence, FTS and episode.

        Savepoints also let a nested projection roll back independently. The
        lock covers the whole scope so another thread cannot commit its writes.
        """
        self._require_writable()
        with self._lock:
            self._savepoint_seq += 1
            name = f"knowledge_transaction_{self._savepoint_seq}"
            self._execute(f"SAVEPOINT {name}")
            self._transaction_depth += 1
            try:
                yield
            except BaseException:
                self._execute(f"ROLLBACK TO SAVEPOINT {name}")
                self._execute(f"RELEASE SAVEPOINT {name}")
                raise
            else:
                self._execute(f"RELEASE SAVEPOINT {name}")
            finally:
                self._transaction_depth -= 1

    def checkpoint(self, consumer_id: str) -> tuple[str, int]:
        with self._lock:
            row = self._execute(
                "SELECT source_generation, processed_ingest_seq FROM organizer_checkpoints WHERE consumer_id=?",
                (consumer_id,),
            ).fetchone()
            return ("", 0) if row is None else (str(row[0]), int(row[1]))

    def set_checkpoint(self, consumer_id: str, generation: str, ingest_seq: int) -> None:
        self._require_writable()
        with self._lock:
            # A batch read before cleanup can finish afterwards. It must not
            # put this consumer back behind a prefix that no longer exists.
            self._execute(
                """INSERT INTO organizer_checkpoints(consumer_id,source_generation,processed_ingest_seq,updated_at_utc_ms)
                   VALUES(?,?,MAX(?,COALESCE(CAST((SELECT value FROM knowledge_meta WHERE key=?) AS INTEGER),0)),?)
                   ON CONFLICT(consumer_id) DO UPDATE SET source_generation=excluded.source_generation,
                   processed_ingest_seq=MAX(excluded.processed_ingest_seq,
                       CASE WHEN organizer_checkpoints.source_generation=excluded.source_generation
                       THEN organizer_checkpoints.processed_ingest_seq ELSE 0 END),
                   updated_at_utc_ms=excluded.updated_at_utc_ms""",
                (consumer_id, generation, int(ingest_seq), "retention_floor:" + generation, int(time.time() * 1000)),
            )
            self._commit()

    def is_archived_observation(self, observation_id: str) -> bool:
        """Check a content-free receipt, including before an item was projected."""
        with self._lock:
            return self._archived_sources_available and self._execute(
                "SELECT 1 FROM archived_sources WHERE observation_id=?", (observation_id,)
            ).fetchone() is not None

    def _reject_archived_sources(self, observation_ids: Iterable[str]) -> None:
        for observation_id in dict.fromkeys(observation_ids):
            if self.is_archived_observation(observation_id):
                raise ArchivedObservationError(f"observation already memorized: {observation_id}")

    def advance_retention_floor(self, source_generation: str, retention_floor: int) -> dict[str, Any]:
        """Acknowledge only the raw store's actually removed continuous prefix.

        The coordinator calls this after raw deletion. Timestamp cutoffs and
        the highest archived ingest sequence are not continuous raw floors.
        """
        self._require_writable()
        if not source_generation or int(retention_floor) < 0:
            raise ValueError("a source generation and non-negative retention floor are required")
        now = int(time.time() * 1000)
        advanced = []
        with self.transaction():
            key = "retention_floor:" + source_generation
            old = self._execute("SELECT value FROM knowledge_meta WHERE key=?", (key,)).fetchone()
            floor = max(int(retention_floor), int(old[0]) if old else 0)
            rows = self._execute(
                "SELECT consumer_id,processed_ingest_seq FROM organizer_checkpoints "
                "WHERE source_generation IN ('',?) AND processed_ingest_seq<?",
                (source_generation, floor),
            ).fetchall()
            for row in rows:
                self._execute(
                    "UPDATE organizer_checkpoints SET source_generation=?,processed_ingest_seq=?,"
                    "updated_at_utc_ms=? WHERE consumer_id=?",
                    (source_generation, floor, now, row["consumer_id"]),
                )
                advanced.append({"consumer_id": row["consumer_id"], "from_ingest_seq": int(row["processed_ingest_seq"])})
            self._execute("INSERT INTO knowledge_meta(key,value) VALUES(?,?) "
                          "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(floor)))
            if advanced:
                self._execute("INSERT INTO knowledge_meta(key,value) VALUES('retention_gap',?) "
                              "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (_json({
                                  "source_generation": source_generation, "retention_floor": floor,
                                  "advanced_consumers": advanced, "at_utc_ms": now,
                              }),))
        return {"source_generation": source_generation, "retention_floor": floor,
                "advanced_checkpoints": len(advanced)}

    def purge_memorized(
        self, observation_ids: Sequence[str], *, source_generation: Optional[str] = None,
        through_ingest_seq: Optional[int] = None, before_utc_ms: Optional[int] = None,
        consumer_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Remove exact sources only after permanent memory has committed them.

        Receipts are bounded, idempotent and contain no captured content. A
        multi-source item stays until all its sources have committed receipts.
        Checkpoints are deliberately unaffected until raw deletion confirms a
        continuous floor with :meth:`advance_retention_floor`.
        """
        self._require_writable()
        if isinstance(observation_ids, (str, bytes)) or len(observation_ids) > 500:
            raise ValueError("memorized receipt must contain at most 500 observation IDs")
        if any(not isinstance(value, str) or not value for value in observation_ids):
            raise ValueError("memorized receipt observation IDs must be non-empty strings")
        if through_ingest_seq is not None and int(through_ingest_seq) < 0:
            raise ValueError("through_ingest_seq must be non-negative")
        if before_utc_ms is not None and int(before_utc_ms) < 0:
            raise ValueError("before_utc_ms must be non-negative")
        ids = tuple(dict.fromkeys(observation_ids))
        result: dict[str, Any] = {"observation_count": len(ids), "deleted_items": 0,
            "deleted_evidence": 0, "deleted_annotations": 0, "deleted_episodes": 0,
            "pruned_episodes": 0, "retained_open_episodes": 0, "retained_shared_items": 0,
            "deleted_candidates": 0, "deleted_facts": 0, "deleted_projection_errors": 0}
        if not ids:
            return result
        placeholders = ",".join("?" for _ in ids)
        now = int(time.time() * 1000)
        with self.transaction():
            checkpoints = self._execute(
                "SELECT source_generation FROM organizer_checkpoints" +
                (" WHERE consumer_id=?" if consumer_id else ""), (consumer_id,) if consumer_id else (),
            ).fetchall()
            if source_generation is not None and any(
                row[0] and str(row[0]) != source_generation for row in checkpoints
            ):
                raise ValueError("knowledge source generation does not match memorized receipt")
            for observation_id in ids:
                self._execute("INSERT OR IGNORE INTO archived_sources VALUES(?,?,?)",
                              (observation_id, source_generation or "", now))
            rows = self._execute(
                f"SELECT DISTINCT i.item_id,i.episode_id FROM activity_items i "
                f"JOIN activity_item_sources s ON s.item_id=i.item_id WHERE s.observation_id IN ({placeholders})",
                ids,
            ).fetchall()
            touched_episodes: set[str] = set()
            for row in rows:
                item_id = str(row["item_id"])
                unarchived = self._execute(
                    "SELECT 1 FROM activity_item_sources s WHERE s.item_id=? AND NOT EXISTS "
                    "(SELECT 1 FROM archived_sources a WHERE a.observation_id=s.observation_id) LIMIT 1",
                    (item_id,),
                ).fetchone()
                if unarchived:
                    result["retained_shared_items"] += 1
                    continue
                if row["episode_id"]:
                    touched_episodes.add(str(row["episode_id"]))
                for table, counter in (("activity_evidence", "deleted_evidence"),
                                       ("semantic_annotations", "deleted_annotations")):
                    result[counter] += self._execute(f"DELETE FROM {table} WHERE item_id=?", (item_id,)).rowcount
                if self._fts_available:
                    self._execute("DELETE FROM activity_fts WHERE item_id=?", (item_id,))
                self._execute("DELETE FROM activity_item_sources WHERE item_id=?", (item_id,))
                result["deleted_items"] += self._execute("DELETE FROM activity_items WHERE item_id=?", (item_id,)).rowcount
            empty_episodes: set[str] = set()
            for episode_id in touched_episodes:
                episode = self._execute("SELECT * FROM activity_episodes WHERE episode_id=?", (episode_id,)).fetchone()
                if episode is None:
                    continue
                members = self._execute(
                    "SELECT item_id,title FROM activity_items WHERE episode_id=? ORDER BY start_utc_ms,item_id",
                    (episode_id,),
                ).fetchall()
                if not members:
                    empty_episodes.add(episode_id)
                if not members and episode["finalized"]:
                    result["deleted_episodes"] += self._execute(
                        "DELETE FROM activity_episodes WHERE episode_id=?", (episode_id,)
                    ).rowcount
                    continue
                # Clear old descriptions directly: upsert_episode intentionally
                # preserves an existing summary when a new summary is None.
                title = next((str(value["title"]) for value in reversed(members) if value["title"]), "")
                summary = f"保留 {len(members)} 条尚未归档的活动观察。" if members else None
                self._execute(
                    "UPDATE activity_episodes SET item_ids_json=?,title=?,summary=?,revision=revision+1,"
                    "updated_at_utc_ms=? WHERE episode_id=?",
                    (_json([str(value["item_id"]) for value in members]), title, summary, now, episode_id),
                )
                result["pruned_episodes"] += 1
                result["retained_open_episodes"] += int(not members)
            # These are rebuildable activity candidates/facts, not the permanent
            # MemoryStore. Discard statements touching any archived evidence.
            for table, id_column, counter in (("memory_candidates", "candidate_id", "deleted_candidates"),
                                               ("memory_facts", "fact_id", "deleted_facts")):
                rows = self._execute(
                    f"SELECT {id_column},source_episode_ids_json FROM {table} WHERE EXISTS "
                    f"(SELECT 1 FROM json_each(source_observation_ids_json) s "
                    f"JOIN archived_sources a ON a.observation_id=s.value)" +
                    (f" OR EXISTS (SELECT 1 FROM json_each(source_episode_ids_json) WHERE value IN "
                     f"({','.join('?' for _ in empty_episodes)}))" if empty_episodes else ""),
                    tuple(empty_episodes),
                ).fetchall()
                for row in rows:
                    result[counter] += self._execute(f"DELETE FROM {table} WHERE {id_column}=?", (row[0],)).rowcount
            result["deleted_projection_errors"] = self._execute(
                f"DELETE FROM projection_errors WHERE observation_id IN ({placeholders})", ids,
            ).rowcount
            self._execute("INSERT INTO knowledge_meta(key,value) VALUES('memorized_retention',?) "
                          "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (_json({
                              "source_generation": source_generation or "", "through_ingest_seq": through_ingest_seq,
                              "before_utc_ms": before_utc_ms, "at_utc_ms": now, **result,
                          }),))
        return result

    def compact_memorized(self) -> dict[str, Any]:
        """Reclaim deleted projection pages once a maintenance batch finishes."""
        self._require_writable()
        with self._lock:
            if self._transaction_depth:
                raise RuntimeError("knowledge compaction cannot run inside a transaction")
            wal_path = Path(str(self.path) + "-wal")
            before = self.path.stat().st_size + (wal_path.stat().st_size if wal_path.exists() else 0)
            checkpoint = self._execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            deferred = bool(checkpoint[0])
            error = None
            if not deferred:
                # VACUUM rewrites every live page. Repeating it while an older
                # reader pins the WAL adds another full database copy each time.
                # Wait for that snapshot to finish before doing any compaction.
                deadline = time.monotonic() + 3.0
                self._connection.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
                try:
                    if self._fts_available:
                        self._execute("INSERT INTO activity_fts(activity_fts) VALUES('optimize')")
                        self._connection.commit()
                    self._execute("VACUUM")
                except sqlite3.OperationalError as failure:
                    if (getattr(failure, "sqlite_errorcode", None) != getattr(sqlite3, "SQLITE_INTERRUPT", 9) and
                            str(failure).lower() != "interrupted"):
                        raise
                    self._connection.rollback()
                    deferred = True
                    error = "SQLite space reclamation exceeded its time limit; retry pending"
                finally:
                    self._connection.set_progress_handler(None, 0)
                if not deferred:
                    checkpoint = self._execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
                    deferred = bool(checkpoint[0])
            wal_bytes = wal_path.stat().st_size if wal_path.exists() else 0
            after = self.path.stat().st_size + wal_bytes
            retained_shared_items = int(self._execute(
                "SELECT COUNT(DISTINCT s.item_id) FROM activity_item_sources s "
                "JOIN archived_sources a ON a.observation_id=s.observation_id",
            ).fetchone()[0])
            return {"db_bytes_before": before, "db_bytes_after": after, "reclaimed_bytes": max(0, before - after),
                    "retained_shared_items": retained_shared_items, "wal_bytes_after": wal_bytes,
                    "compaction_deferred": deferred, "error": error}

    def upsert_item(self, item: ActivityItem, evidence: Evidence) -> None:
        now = int(time.time() * 1000)
        with self.transaction():
            self._reject_archived_sources((*item.source_observation_ids, *evidence.source_observation_ids))
            self._execute(
                """INSERT INTO activity_items(
                    item_id,kind,start_utc_ms,end_utc_ms,app_id,process_instance_id,window_instance_id,
                    title,url,document_ref,text_summary,structured_json,asset_refs_json,source_observation_ids_json,
                    actor,actor_basis,quality,missing_reasons_json,confidence,importance,sensitivity,content_hash,
                    projection_version,episode_id,created_at_utc_ms,updated_at_utc_ms)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(item_id) DO UPDATE SET
                    end_utc_ms=excluded.end_utc_ms,title=excluded.title,url=excluded.url,document_ref=excluded.document_ref,
                    text_summary=excluded.text_summary,structured_json=excluded.structured_json,
                    asset_refs_json=excluded.asset_refs_json,quality=excluded.quality,
                    missing_reasons_json=excluded.missing_reasons_json,confidence=excluded.confidence,
                    importance=excluded.importance,content_hash=excluded.content_hash,
                    source_observation_ids_json=excluded.source_observation_ids_json,
                    episode_id=COALESCE(excluded.episode_id,activity_items.episode_id),
                    updated_at_utc_ms=excluded.updated_at_utc_ms""",
                (
                    item.item_id, item.kind, item.start_utc_ms, item.end_utc_ms, item.app_id,
                    item.process_instance_id, item.window_instance_id, item.title, item.url,
                    item.document_ref, item.text_summary, _json(dict(item.structured)), _json(list(item.asset_refs)),
                    _json(list(item.source_observation_ids)), item.actor, item.actor_basis, item.quality,
                    _json(list(item.missing_reasons)), item.confidence, item.importance, item.sensitivity,
                    item.content_hash, item.projection_version, item.episode_id, now, now,
                ),
            )
            self._execute("DELETE FROM activity_item_sources WHERE item_id=?", (item.item_id,))
            for observation_id in dict.fromkeys(item.source_observation_ids):
                self._execute(
                    "INSERT INTO activity_item_sources(item_id,observation_id) VALUES(?,?)",
                    (item.item_id, observation_id),
                )
            self._execute(
                """INSERT INTO activity_evidence(
                    evidence_id,item_id,source_observation_ids_json,observed_at_utc_ms,received_at_utc_ms,
                    content_type,text,structured_json,asset_refs_json,content_hash,quality,confidence,extractor_version)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(evidence_id) DO UPDATE SET text=excluded.text, structured_json=excluded.structured_json,
                    asset_refs_json=excluded.asset_refs_json, quality=excluded.quality, confidence=excluded.confidence,
                    source_observation_ids_json=excluded.source_observation_ids_json,content_hash=excluded.content_hash""",
                (
                    evidence.evidence_id, item.item_id, _json(list(evidence.source_observation_ids)),
                    evidence.observed_at_utc_ms, evidence.received_at_utc_ms, evidence.content_type,
                    evidence.text, _json(dict(evidence.structured)), _json(list(evidence.asset_refs)),
                    evidence.content_hash, evidence.quality, evidence.confidence, evidence.extractor_version,
                ),
            )
            if self._fts_available:
                self._execute("DELETE FROM activity_fts WHERE item_id=?", (item.item_id,))
                structured = dict(item.structured)
                # The search corpus includes complete evidence. Keep the
                # bounded item summary for readers and generated descriptions.
                search_text = "\n".join(value for value in (item.text_summary, evidence.text) if value)
                self._execute(
                    "INSERT INTO activity_fts(item_id,title,url,text_summary,uia_text,ocr_text,clipboard_text,document_ref,entity_names) VALUES(?,?,?,?,?,?,?,?,?)",
                    (
                        item.item_id, item.title or "", item.url or "", search_text,
                        str(structured.get("uia_text", "")), str(structured.get("ocr_text", "")),
                        str(structured.get("clipboard_text", "")), item.document_ref or "",
                        " ".join(str(value) for value in structured.get("entities", ()) if value),
                    ),
                )
            self._commit()

    def attach_item_episode(self, item_id: str, episode_id: str) -> None:
        self._require_writable()
        with self._lock:
            self._execute("UPDATE activity_items SET episode_id=?,updated_at_utc_ms=? WHERE item_id=?", (episode_id, int(time.time() * 1000), item_id))
            self._commit()

    def upsert_annotation(self, item_id: str, annotation: Any) -> None:
        self._require_writable()
        now = int(time.time() * 1000)
        with self._lock:
            self._execute(
                """INSERT INTO semantic_annotations(annotation_id,item_id,extractor_name,extractor_version,model_name,value_json,confidence,status,error,created_at_utc_ms)
                   VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(item_id,extractor_name,extractor_version) DO UPDATE SET
                   model_name=excluded.model_name,value_json=excluded.value_json,confidence=excluded.confidence,
                   status=excluded.status,error=excluded.error,created_at_utc_ms=excluded.created_at_utc_ms""",
                (deterministic_id("annotation", item_id, annotation.extractor_name, annotation.extractor_version), item_id,
                 annotation.extractor_name, annotation.extractor_version, annotation.model_name,
                 _json(dict(annotation.value)), annotation.confidence, annotation.status, annotation.error, now),
            )
            self._commit()

    def get_item(self, item_id: str) -> Optional[ActivityItem]:
        with self._lock:
            row = self._execute("SELECT * FROM activity_items WHERE item_id=?", (item_id,)).fetchone()
            return None if row is None else self._item(row)

    def get_item_by_observation_id(self, observation_id: str) -> Optional[ActivityItem]:
        with self._lock:
            row = self._execute(
                """SELECT i.* FROM activity_items i JOIN activity_item_sources s ON s.item_id=i.item_id
                   WHERE s.observation_id=? ORDER BY i.start_utc_ms DESC,i.item_id DESC LIMIT 1""",
                (observation_id,),
            ).fetchone()
            return None if row is None else self._item(row)

    def get_episode_by_observation_id(self, observation_id: str) -> Optional[ActivityEpisode]:
        with self._lock:
            item = self.get_item_by_observation_id(observation_id)
            return self.get_episode(item.episode_id) if item and item.episode_id else None

    @staticmethod
    def _session_id(item: ActivityItem) -> Optional[str]:
        for key in ("source", "provenance"):
            source = item.structured.get(key)
            if isinstance(source, dict) and source.get("session_instance_id"):
                return str(source["session_instance_id"])
        value = item.structured.get("session_instance_id")
        return str(value) if value else None

    def latest_window_context(self, *, window_id: str, at_utc_ms: int,
                              app_id: Optional[str] = None,
                              session_instance_id: Optional[str] = None,
                              source_epoch: Optional[str] = None) -> Optional[ActivityItem]:
        """Resolve each resource field from observations already seen by this time.

        The returned item is a context view, not a newly persisted observation.
        ``context_observation_ids`` identifies all sources used for its fields.
        """
        clauses = ["window_instance_id=?", "start_utc_ms<=?"]
        params: list[Any] = [window_id, int(at_utc_ms)]
        if app_id is not None:
            clauses.append("app_id=?")
            params.append(app_id)
        if session_instance_id is not None:
            clauses.append("""COALESCE(NULLIF(json_extract(structured_json,'$.source.session_instance_id'),''),
                NULLIF(json_extract(structured_json,'$.provenance.session_instance_id'),''),
                json_extract(structured_json,'$.session_instance_id'))=?""")
            params.append(session_instance_id)
        if source_epoch is not None:
            clauses.append("json_extract(structured_json,'$.source.source_epoch')=?")
            params.append(source_epoch)
        fields: dict[str, Optional[str]] = {}
        sources: list[str] = []
        selected: list[ActivityItem] = []
        field_sources: dict[str, tuple[str, ...]] = {}
        unknown_fields: list[str] = []
        with self._lock:
            gap_params: list[Any] = [int(at_utc_ms)]
            gap_scope = ""
            if session_instance_id is not None:
                gap_scope = " AND (session_instance_id IS NULL OR session_instance_id=?)"
                gap_params.append(session_instance_id)
            gap = self._execute(
                f"SELECT MAX(at_utc_ms) FROM activity_collection_gaps WHERE at_utc_ms<=?{gap_scope}",
                gap_params,
            ).fetchone()[0]
            if gap is not None:
                clauses.append("start_utc_ms>?")
                params.append(int(gap))
            for field in ("title", "url", "document_ref"):
                # Items produced from an earlier context carry their resolved
                # resource fields for query convenience.  They are not new
                # observations of that resource, so a late direct context
                # must win over a newer item that merely inherited the old
                # value.  Keep this predicate field-specific because an item
                # may observe one resource directly while inheriting another.
                direct_field = f"json_type(structured_json,'$.context_field_sources.{field}') IS NULL"
                field_clauses_base = list(clauses) + [direct_field]
                barrier = self._execute(
                    f"""SELECT start_utc_ms,COALESCE(json_extract(structured_json,'$.source.ingest_seq'),0)
                        FROM activity_items WHERE {' AND '.join(field_clauses_base)} AND EXISTS(
                            SELECT 1 FROM json_each(COALESCE(json_extract(structured_json,
                                '$.source.resource_unknown_fields'),'[]')) WHERE value=?)
                        ORDER BY start_utc_ms DESC,COALESCE(json_extract(structured_json,'$.source.ingest_seq'),0) DESC
                        LIMIT 1""", (*params, field),
                ).fetchone()
                field_clauses, field_params = field_clauses_base, list(params)
                if barrier is not None:
                    field_clauses.append("(start_utc_ms>? OR (start_utc_ms=? AND COALESCE(json_extract(structured_json,'$.source.ingest_seq'),0)>?))")
                    field_params.extend((int(barrier[0]), int(barrier[0]), int(barrier[1])))
                row = self._execute(
                    f"""SELECT * FROM activity_items WHERE {' AND '.join(field_clauses)}
                        AND {field} IS NOT NULL AND TRIM({field})<>''
                        ORDER BY start_utc_ms DESC,COALESCE(json_extract(structured_json,'$.source.ingest_seq'),0) DESC,item_id DESC LIMIT 1""",
                    field_params,
                ).fetchone()
                item = self._item(row) if row is not None else None
                fields[field] = getattr(item, field) if item else None
                if barrier is not None and item is None:
                    unknown_fields.append(field)
                if item:
                    selected.append(item)
                    references = (item.structured.get("context_field_sources") or {}).get(field)
                    if references is None:
                        references = item.source_observation_ids
                    field_sources[field] = tuple(references)
                    sources.extend(references)
            if not selected:
                return None
            latest = max(selected, key=lambda item: (item.start_utc_ms, item.item_id))
            return replace(latest, **fields, structured={
                **dict(latest.structured), "context_observation_ids": tuple(dict.fromkeys(sources)),
                "context_field_sources": field_sources,
                "context_unknown_fields": unknown_fields,
            })

    def projection_versions(self) -> tuple[str, ...]:
        with self._lock:
            rows = self._execute("""SELECT projection_version FROM activity_items
                UNION SELECT projection_version FROM activity_episodes ORDER BY projection_version""").fetchall()
            return tuple(str(row[0]) for row in rows)

    def record_projection_error(self, observation_id: str, ingest_seq: Optional[int], error: Any,
                                *, consumer_id: str = "activity-knowledge") -> None:
        self._require_writable()
        now = int(time.time() * 1000)
        with self._lock:
            if self.is_archived_observation(observation_id):
                return
            self._execute(
                """INSERT INTO projection_errors(consumer_id,observation_id,ingest_seq,error_type,error,attempts,
                   first_failed_at_utc_ms,last_failed_at_utc_ms) VALUES(?,?,?,?,?,1,?,?)
                   ON CONFLICT(consumer_id,observation_id) DO UPDATE SET ingest_seq=excluded.ingest_seq,
                   error_type=excluded.error_type,error=excluded.error,attempts=projection_errors.attempts+1,
                   last_failed_at_utc_ms=excluded.last_failed_at_utc_ms""",
                (consumer_id, observation_id, ingest_seq, type(error).__name__, str(error)[:2000], now, now),
            )
            self._commit()

    def clear_projection_error(self, observation_id: str, *, consumer_id: str = "activity-knowledge") -> None:
        self._require_writable()
        with self._lock:
            self._execute("DELETE FROM projection_errors WHERE consumer_id=? AND observation_id=?",
                          (consumer_id, observation_id))
            self._commit()

    def list_projection_errors(self, *, consumer_id: str = "activity-knowledge", limit: int = 100,
                               retryable_only: bool = False) -> list[dict[str, Any]]:
        with self._lock:
            retry_clause = " AND attempts<3" if retryable_only else ""
            rows = self._execute(
                f"SELECT * FROM projection_errors WHERE consumer_id=?{retry_clause} ORDER BY ingest_seq,observation_id LIMIT ?",
                (consumer_id, max(1, min(500, int(limit)))),
            ).fetchall()
            return [dict(row) for row in rows]

    def _item(self, row: sqlite3.Row) -> ActivityItem:
        return ActivityItem(
            item_id=str(row["item_id"]), kind=str(row["kind"]), start_utc_ms=int(row["start_utc_ms"]),
            end_utc_ms=int(row["end_utc_ms"]), app_id=row["app_id"], process_instance_id=row["process_instance_id"],
            window_instance_id=row["window_instance_id"], title=row["title"], url=row["url"], document_ref=row["document_ref"],
            text_summary=str(row["text_summary"] or ""), structured=_from_json(row["structured_json"], {}),
            asset_refs=tuple(_from_json(row["asset_refs_json"], [])), source_observation_ids=tuple(_from_json(row["source_observation_ids_json"], [])),
            actor=str(row["actor"]), actor_basis=str(row["actor_basis"]), quality=str(row["quality"]),
            missing_reasons=tuple(_from_json(row["missing_reasons_json"], [])), confidence=float(row["confidence"]),
            importance=float(row["importance"]), sensitivity=str(row["sensitivity"]), content_hash=str(row["content_hash"]),
            projection_version=str(row["projection_version"]), episode_id=row["episode_id"],
        )

    def _episode(self, row: sqlite3.Row) -> ActivityEpisode:
        item_ids = tuple(_from_json(row["item_ids_json"], []))
        gaps = tuple(_from_json(row["data_gaps_json"], []))
        summary = row["summary"]
        if not summary:
            app = row["primary_app"] or "未知应用"
            gap_text = "，包含采集缺口" if gaps else ""
            summary = f"在 {app} 中记录了 {len(item_ids)} 条活动观察{gap_text}；这是确定性统计，不代表任务语义总结。"
        return ActivityEpisode(
            episode_id=str(row["episode_id"]), start_utc_ms=int(row["start_utc_ms"]), end_utc_ms=int(row["end_utc_ms"]),
            title=str(row["title"] or ""), primary_app=row["primary_app"], primary_window_id=row["primary_window_id"],
            item_ids=item_ids, summary=summary, confidence=float(row["confidence"]),
            finalized=bool(row["finalized"]), end_reason=row["end_reason"], revision=int(row["revision"]),
            data_gaps=gaps, projection_version=str(row["projection_version"]),
        )

    def latest_item_in_episode(self, episode_id: str) -> Optional[ActivityItem]:
        with self._lock:
            row = self._execute(
                "SELECT * FROM activity_items WHERE episode_id=? AND url IS NOT NULL AND TRIM(url)<>'' ORDER BY start_utc_ms DESC,item_id DESC LIMIT 1",
                (episode_id,),
            ).fetchone()
            return None if row is None else self._item(row)

    def find_open_episode(self, *, app_id: Optional[str], window_id: Optional[str], start_utc_ms: int, idle_timeout_ms: int) -> Optional[ActivityEpisode]:
        with self._lock:
            app_clause = "primary_app IS NULL" if app_id is None else "primary_app=?"
            window_clause = "primary_window_id IS NULL" if window_id is None else "primary_window_id=?"
            params: list[Any] = []
            if app_id is not None:
                params.append(app_id)
            if window_id is not None:
                params.append(window_id)
            row = self._execute(
                f"""SELECT * FROM activity_episodes WHERE {app_clause}
                   AND {window_clause} AND start_utc_ms<=? AND end_utc_ms>=?
                    AND (finalized=0 OR end_reason='idle' OR end_utc_ms>=?)
                   ORDER BY start_utc_ms DESC LIMIT 1""",
                (*params, start_utc_ms, start_utc_ms - idle_timeout_ms, start_utc_ms),
            ).fetchone()
            if row is None:
                return None
            episode = self._episode(row)
            if start_utc_ms - episode.end_utc_ms > idle_timeout_ms:
                return None
            return episode

    def finalize_episodes(self, before_utc_ms: int, *, reason: str = "idle", except_window: Optional[str] = None,
                          foreground_only: bool = False) -> None:
        self._require_writable()
        with self._lock:
            clause = "finalized=0 AND end_utc_ms<?"
            params: list[Any] = [reason, int(time.time() * 1000), before_utc_ms]
            if except_window is not None:
                clause += " AND primary_window_id IS NOT ?"
                params.append(except_window)
            if foreground_only:
                clause += """ AND COALESCE(primary_window_id,'') NOT LIKE 'monitor%'
                    AND NOT EXISTS (SELECT 1 FROM activity_items i WHERE i.episode_id=activity_episodes.episode_id
                        AND (i.actor='BACKGROUND' OR i.actor_basis='monitor_observation'
                            OR json_extract(i.structured_json,'$.source.scope')='monitor'
                            OR json_extract(i.structured_json,'$.provenance.scope')='monitor'))"""
            self._execute(
                f"UPDATE activity_episodes SET finalized=1,end_reason=?,revision=revision+1,updated_at_utc_ms=? WHERE {clause}",
                params,
            )
            self._commit()

    def finalize_for_gap(self, at_utc_ms: int, missing_reasons: Iterable[str], *,
                         session_instance_id: Optional[str] = None) -> int:
        """Seal and mark open activity affected by an explicit collection gap.

        A missing session id denotes a stream-wide gap. A known session id only
        affects episodes containing evidence from that session, including its
        background monitor observations. No observed span is extended.
        """
        reasons = tuple(dict.fromkeys(("collection_gap", *(str(value) for value in missing_reasons))))
        count = 0
        with self.transaction():
            self._execute(
                """INSERT INTO activity_collection_gaps(gap_id,at_utc_ms,session_instance_id,missing_reasons_json)
                   VALUES(?,?,?,?) ON CONFLICT(gap_id) DO UPDATE SET missing_reasons_json=excluded.missing_reasons_json""",
                (deterministic_id("gap", session_instance_id, int(at_utc_ms)), int(at_utc_ms),
                 session_instance_id, _json(list(reasons))),
            )
            rows = self._execute("SELECT * FROM activity_episodes WHERE finalized=0 AND start_utc_ms<=?",
                                 (int(at_utc_ms),)).fetchall()
            for row in rows:
                episode = self._episode(row)
                if session_instance_id is not None:
                    items = self._items_for_ids(episode.item_ids)
                    if not any(self._session_id(item) == session_instance_id for item in items):
                        continue
                self.upsert_episode(replace(
                    episode, finalized=True, end_reason="collection_gap", revision=episode.revision + 1,
                    data_gaps=tuple(dict.fromkeys((*episode.data_gaps, *reasons))),
                    confidence=min(episode.confidence, 0.25),
                ))
                count += 1
        return count

    def upsert_episode(self, episode: ActivityEpisode) -> None:
        self._require_writable()
        now = int(time.time() * 1000)
        with self._lock:
            self._execute(
                """INSERT INTO activity_episodes(
                    episode_id,start_utc_ms,end_utc_ms,title,primary_app,primary_window_id,item_ids_json,summary,
                    confidence,finalized,end_reason,revision,data_gaps_json,projection_version,created_at_utc_ms,updated_at_utc_ms)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(episode_id) DO UPDATE SET
                    start_utc_ms=excluded.start_utc_ms,end_utc_ms=excluded.end_utc_ms,title=excluded.title,
                    primary_app=excluded.primary_app,primary_window_id=excluded.primary_window_id,item_ids_json=excluded.item_ids_json,
                    summary=COALESCE(excluded.summary,activity_episodes.summary),confidence=excluded.confidence,
                    finalized=excluded.finalized,end_reason=excluded.end_reason,revision=excluded.revision,
                    data_gaps_json=excluded.data_gaps_json,updated_at_utc_ms=excluded.updated_at_utc_ms""",
                (
                    episode.episode_id, episode.start_utc_ms, episode.end_utc_ms, episode.title, episode.primary_app,
                    episode.primary_window_id, _json(list(episode.item_ids)), episode.summary, episode.confidence,
                    int(episode.finalized), episode.end_reason, episode.revision, _json(list(episode.data_gaps)),
                    episode.projection_version, now, now,
                ),
            )
            self._commit()

    def get_episode(self, episode_id: str) -> Optional[ActivityEpisode]:
        with self._lock:
            row = self._execute("SELECT * FROM activity_episodes WHERE episode_id=?", (episode_id,)).fetchone()
            return None if row is None else self._episode(row)

    def _items_for_ids(self, ids: Iterable[str]) -> list[ActivityItem]:
        result: list[ActivityItem] = []
        for item_id in ids:
            item = self.get_item(str(item_id))
            if item is not None:
                result.append(item)
        return result

    def _evidence_for_items(self, item_ids: Iterable[str]) -> list[Evidence]:
        values = tuple(str(value) for value in item_ids)
        if not values:
            return []
        with self._lock:
            placeholders = ",".join("?" for _ in values)
            rows = self._execute(f"SELECT * FROM activity_evidence WHERE item_id IN ({placeholders}) ORDER BY observed_at_utc_ms", values).fetchall()
            return [Evidence(
                evidence_id=str(row["evidence_id"]), source_observation_ids=tuple(_from_json(row["source_observation_ids_json"], [])),
                observed_at_utc_ms=int(row["observed_at_utc_ms"]), received_at_utc_ms=row["received_at_utc_ms"],
                content_type=str(row["content_type"]), text=str(row["text"] or ""), structured=_from_json(row["structured_json"], {}),
                asset_refs=tuple(_from_json(row["asset_refs_json"], [])), content_hash=str(row["content_hash"]),
                quality=str(row["quality"]), confidence=float(row["confidence"]), extractor_version=str(row["extractor_version"]),
            ) for row in rows]

    def evidence_ids_for_items(self, item_ids: Iterable[str]) -> dict[str, tuple[str, ...]]:
        """Resolve item evidence references without loading captured content."""

        values = tuple(dict.fromkeys(str(value) for value in item_ids if value))
        if not values:
            return {}
        with self._lock:
            placeholders = ",".join("?" for _ in values)
            rows = self._execute(
                f"SELECT item_id,evidence_id FROM activity_evidence WHERE item_id IN ({placeholders}) "
                "ORDER BY observed_at_utc_ms,evidence_id",
                values,
            ).fetchall()
        references: dict[str, list[str]] = {}
        for row in rows:
            references.setdefault(str(row["item_id"]), []).append(str(row["evidence_id"]))
        return {item_id: tuple(ids) for item_id, ids in references.items()}

    def get_evidence(self, evidence_ids: Sequence[str]) -> list[Evidence]:
        """Return only explicitly requested evidence references."""

        values = tuple(str(value) for value in evidence_ids if value)
        if not values:
            return []
        with self._lock:
            placeholders = ",".join("?" for _ in values)
            rows = self._execute(
                f"SELECT * FROM activity_evidence WHERE evidence_id IN ({placeholders}) ORDER BY observed_at_utc_ms",
                values,
            ).fetchall()
            return [
                Evidence(
                    evidence_id=str(row["evidence_id"]),
                    source_observation_ids=tuple(_from_json(row["source_observation_ids_json"], [])),
                    observed_at_utc_ms=int(row["observed_at_utc_ms"]),
                    received_at_utc_ms=row["received_at_utc_ms"],
                    content_type=str(row["content_type"]),
                    text=str(row["text"] or ""),
                    structured=_from_json(row["structured_json"], {}),
                    asset_refs=tuple(_from_json(row["asset_refs_json"], [])),
                    content_hash=str(row["content_hash"]),
                    quality=str(row["quality"]),
                    confidence=float(row["confidence"]),
                    extractor_version=str(row["extractor_version"]),
                )
                for row in rows
            ]

    def query_items(self, *, query: Optional[str] = None, start_utc_ms: Optional[int] = None, end_utc_ms: Optional[int] = None,
                    apps: Sequence[str] = (), windows: Sequence[str] = (), kinds: Sequence[str] = (), episode_id: Optional[str] = None,
                    limit: int = 30, after_item_id: Optional[str] = None) -> list[ActivityItem]:
        limit = max(1, min(500, int(limit)))
        clauses: list[str] = []
        params: list[Any] = []
        order = "i.start_utc_ms DESC, i.item_id DESC"
        if query:
            # unicode61 does not segment Chinese. Combine full-text and
            # literal recall on every page, including when FTS has some hits;
            # an empty-page fallback loses embedded phrases and makes cursor
            # pagination change the search semantics halfway through.
            literal_terms = [f"i.{column} LIKE ? ESCAPE '\\'" for column in ("title", "url", "text_summary", "app_id")]
            # Evidence is also searched when an older FTS row contains only
            # the summary or unicode61 cannot match an embedded Chinese phrase.
            literal_terms.append("""EXISTS (SELECT 1 FROM activity_evidence e
                WHERE e.item_id=i.item_id AND e.text LIKE ? ESCAPE '\\')""")
            literal = " OR ".join(literal_terms)
            pattern = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            if self._fts_available:
                clauses.append(f"(i.item_id IN (SELECT item_id FROM activity_fts WHERE activity_fts MATCH ?) OR {literal})")
                params.append(query)
            else:
                clauses.append(f"({literal})")
            params.extend((pattern,) * len(literal_terms))
        if start_utc_ms is not None:
            clauses.append("i.end_utc_ms >= ?"); params.append(int(start_utc_ms))
        if end_utc_ms is not None:
            clauses.append("i.start_utc_ms < ?"); params.append(int(end_utc_ms))
        if episode_id:
            clauses.append("i.episode_id=?"); params.append(episode_id)
        for column, values in (("i.app_id", apps), ("i.window_instance_id", windows), ("i.kind", kinds)):
            if values:
                clauses.append(f"{column} IN ({','.join('?' for _ in values)})")
                params.extend(values)
        if after_item_id:
            with self._lock:
                row = self._execute("SELECT start_utc_ms,item_id FROM activity_items WHERE item_id=?", (after_item_id,)).fetchone()
            if row is None:
                raise ValueError("activity continuation cursor no longer exists; restart the query")
            clauses.append("(i.start_utc_ms < ? OR (i.start_utc_ms=? AND i.item_id < ?))")
            params.extend((int(row[0]), int(row[0]), after_item_id))
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self._lock:
            try:
                rows = self._execute(f"SELECT i.* FROM activity_items i{where} ORDER BY {order} LIMIT ?", (*params, limit)).fetchall()
            except sqlite3.OperationalError:
                if query and self._fts_available:
                    # FTS syntax is user-facing input; retry as a phrase.
                    safe = '"' + query.replace('"', '""') + '"'
                    params = [safe] + params[1:]
                    rows = self._execute(f"SELECT i.* FROM activity_items i{where} ORDER BY {order} LIMIT ?", (*params, limit)).fetchall()
                else:
                    raise
            return [self._item(row) for row in rows]

    def query_episodes(
        self,
        *,
        start_utc_ms: Optional[int] = None,
        end_utc_ms: Optional[int] = None,
        query: Optional[str] = None,
        apps: Sequence[str] = (),
        windows: Sequence[str] = (),
        kinds: Sequence[str] = (),
        after_episode_id: Optional[str] = None,
        limit: int = 10,
    ) -> list[ActivityEpisode]:
        clauses: list[str] = []
        params: list[Any] = []
        if start_utc_ms is not None:
            clauses.append("end_utc_ms >= ?"); params.append(int(start_utc_ms))
        if end_utc_ms is not None:
            clauses.append("start_utc_ms < ?"); params.append(int(end_utc_ms))
        if apps:
            clauses.append(f"primary_app IN ({','.join('?' for _ in apps)})")
            params.extend(str(value) for value in apps)
        if windows:
            clauses.append(f"primary_window_id IN ({','.join('?' for _ in windows)})")
            params.extend(str(value) for value in windows)
        if query:
            escaped_query = str(query).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            pattern = f"%{escaped_query}%"
            clauses.append(
                "EXISTS (SELECT 1 FROM activity_items i LEFT JOIN activity_evidence e ON e.item_id=i.item_id "
                "WHERE i.episode_id=activity_episodes.episode_id AND "
                "(i.title LIKE ? ESCAPE '\\' OR i.url LIKE ? ESCAPE '\\' OR "
                "i.text_summary LIKE ? ESCAPE '\\' OR i.app_id LIKE ? ESCAPE '\\' OR "
                "i.structured_json LIKE ? ESCAPE '\\' OR e.text LIKE ? ESCAPE '\\'))"
            )
            params.extend((pattern,) * 6)
        if kinds:
            placeholders = ",".join("?" for _ in kinds)
            clauses.append(
                f"EXISTS (SELECT 1 FROM activity_items k WHERE k.episode_id=activity_episodes.episode_id "
                f"AND (k.kind IN ({placeholders}) OR json_extract(k.structured_json, '$.source.kind') IN ({placeholders})))"
            )
            params.extend(str(value) for value in kinds)
            params.extend(str(value) for value in kinds)
        if after_episode_id:
            with self._lock:
                cursor = self._execute(
                    "SELECT start_utc_ms,episode_id FROM activity_episodes WHERE episode_id=?",
                    (after_episode_id,),
                ).fetchone()
            if cursor is None:
                raise ValueError("activity episode continuation cursor no longer exists; restart the query")
            clauses.append("(start_utc_ms < ? OR (start_utc_ms=? AND episode_id < ?))")
            params.extend((int(cursor[0]), int(cursor[0]), str(after_episode_id)))
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self._lock:
            rows = self._execute(
                f"SELECT * FROM activity_episodes{where} ORDER BY start_utc_ms DESC, episode_id DESC LIMIT ?",
                (*params, max(1, min(101, int(limit)))),
            ).fetchall()
            return [self._episode(row) for row in rows]

    def get_evidence_for_items(self, item_ids: Iterable[str]) -> list[Evidence]:
        """Return complete evidence rows associated with projected items."""

        return self._evidence_for_items(item_ids)

    def context(self, query: "ContextQuery") -> tuple[list[ActivityEpisode], list[ActivityItem], list[Evidence]]:
        items = self.query_items(query=query.query, start_utc_ms=query.start_utc_ms, end_utc_ms=query.end_utc_ms, apps=query.apps, windows=query.windows, kinds=query.kinds, episode_id=query.episode_id, limit=query.max_items, after_item_id=query.after_item_id)
        episode_ids = tuple(item.episode_id for item in items if item.episode_id)
        episodes = []
        for episode_id in episode_ids:
            episode = self.get_episode(str(episode_id))
            if episode is not None and episode.episode_id not in {value.episode_id for value in episodes}:
                episodes.append(episode)
        episodes = episodes[:query.max_episodes]
        evidence = self._evidence_for_items(item.item_id for item in items) if query.mode == "evidence" else []
        return episodes, items, evidence

    def upsert_memory_candidate(self, candidate: MemoryCandidate) -> None:
        self._require_writable()
        now = int(time.time() * 1000)
        with self._lock:
            self._reject_archived_sources(candidate.source_observation_ids)
            self._execute(
                """INSERT INTO memory_candidates(candidate_id,statement,fact_key,subject,predicate,object_value,
                   valid_from_utc_ms,valid_to_utc_ms,source_episode_ids_json,source_observation_ids_json,confidence,
                   extraction_method,extraction_version,status,conflicts_with_json,created_at_utc_ms,updated_at_utc_ms)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(candidate_id) DO UPDATE SET
                   statement=excluded.statement,confidence=excluded.confidence,status=excluded.status,
                   source_episode_ids_json=excluded.source_episode_ids_json,source_observation_ids_json=excluded.source_observation_ids_json,
                   conflicts_with_json=excluded.conflicts_with_json,updated_at_utc_ms=excluded.updated_at_utc_ms""",
                (candidate.candidate_id, candidate.statement, candidate.fact_key, candidate.subject, candidate.predicate,
                 candidate.object_value, candidate.valid_from_utc_ms, candidate.valid_to_utc_ms, _json(list(candidate.source_episode_ids)),
                 _json(list(candidate.source_observation_ids)), candidate.confidence, candidate.extraction_method,
                 candidate.extraction_version, candidate.status, _json(list(candidate.conflicts_with)), now, now),
            )
            self._commit()

    def list_memory_candidates(self, *, status: Optional[str] = None, limit: int = 100) -> list[MemoryCandidate]:
        with self._lock:
            if status:
                rows = self._execute("SELECT * FROM memory_candidates WHERE status=? ORDER BY confidence DESC,updated_at_utc_ms DESC LIMIT ?", (status, max(1, min(500, int(limit))))).fetchall()
            else:
                rows = self._execute("SELECT * FROM memory_candidates ORDER BY confidence DESC,updated_at_utc_ms DESC LIMIT ?", (max(1, min(500, int(limit))),)).fetchall()
            return [MemoryCandidate(
                candidate_id=str(row["candidate_id"]), statement=str(row["statement"]), fact_key=row["fact_key"], subject=row["subject"],
                predicate=row["predicate"], object_value=row["object_value"], valid_from_utc_ms=row["valid_from_utc_ms"],
                valid_to_utc_ms=row["valid_to_utc_ms"], source_episode_ids=tuple(_from_json(row["source_episode_ids_json"], [])),
                source_observation_ids=tuple(_from_json(row["source_observation_ids_json"], [])), confidence=float(row["confidence"]),
                extraction_method=str(row["extraction_method"]), extraction_version=str(row["extraction_version"]),
                status=str(row["status"]), conflicts_with=tuple(_from_json(row["conflicts_with_json"], [])),
            ) for row in rows]

    def get_memory_candidate(self, candidate_id: str) -> Optional[MemoryCandidate]:
        with self._lock:
            row = self._execute("SELECT * FROM memory_candidates WHERE candidate_id=?", (candidate_id,)).fetchone()
            if row is None:
                return None
            return MemoryCandidate(
                candidate_id=str(row["candidate_id"]), statement=str(row["statement"]), fact_key=row["fact_key"],
                subject=row["subject"], predicate=row["predicate"], object_value=row["object_value"],
                valid_from_utc_ms=row["valid_from_utc_ms"], valid_to_utc_ms=row["valid_to_utc_ms"],
                source_episode_ids=tuple(_from_json(row["source_episode_ids_json"], [])),
                source_observation_ids=tuple(_from_json(row["source_observation_ids_json"], [])),
                confidence=float(row["confidence"]), extraction_method=str(row["extraction_method"]),
                extraction_version=str(row["extraction_version"]), status=str(row["status"]),
                conflicts_with=tuple(_from_json(row["conflicts_with_json"], [])),
            )

    def set_memory_status(self, candidate_id: str, status: str, *, conflicts_with: Sequence[str] = ()) -> bool:
        self._require_writable()
        if status not in {"candidate", "accepted", "superseded", "rejected", "retracted"}:
            raise ValueError("invalid memory candidate status")
        with self._lock:
            cursor = self._execute(
                "UPDATE memory_candidates SET status=?,conflicts_with_json=?,updated_at_utc_ms=? WHERE candidate_id=?",
                (status, _json(list(conflicts_with)), int(time.time() * 1000), candidate_id),
            )
            self._commit()
            return cursor.rowcount > 0

    def upsert_memory_fact(self, fact: MemoryFact) -> None:
        self._require_writable()
        if fact.status not in {"active", "superseded", "retracted"}:
            raise ValueError("invalid memory fact status")
        now = int(time.time() * 1000)
        with self._lock:
            self._reject_archived_sources(fact.source_observation_ids)
            self._execute(
                """INSERT INTO memory_facts(fact_id,statement,fact_key,subject,predicate,object_value,
                   valid_from_utc_ms,valid_to_utc_ms,source_episode_ids_json,source_observation_ids_json,confidence,
                   extraction_method,extraction_version,status,supersedes_json,conflicts_with_json,created_at_utc_ms,updated_at_utc_ms)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(fact_id) DO UPDATE SET
                   statement=excluded.statement,confidence=excluded.confidence,status=excluded.status,
                   valid_from_utc_ms=excluded.valid_from_utc_ms,valid_to_utc_ms=excluded.valid_to_utc_ms,
                   source_episode_ids_json=excluded.source_episode_ids_json,source_observation_ids_json=excluded.source_observation_ids_json,
                   supersedes_json=excluded.supersedes_json,conflicts_with_json=excluded.conflicts_with_json,
                   updated_at_utc_ms=excluded.updated_at_utc_ms""",
                (
                    fact.fact_id, fact.statement, fact.fact_key, fact.subject, fact.predicate, fact.object_value,
                    fact.valid_from_utc_ms, fact.valid_to_utc_ms, _json(list(fact.source_episode_ids)),
                    _json(list(fact.source_observation_ids)), fact.confidence, fact.extraction_method,
                    fact.extraction_version, fact.status, _json(list(fact.supersedes)), _json(list(fact.conflicts_with)),
                    now, now,
                ),
            )
            self._commit()

    def list_memory_facts(self, *, status: Optional[str] = None, limit: int = 100) -> list[MemoryFact]:
        with self._lock:
            bounded = max(1, min(500, int(limit)))
            if status:
                rows = self._execute(
                    "SELECT * FROM memory_facts WHERE status=? ORDER BY confidence DESC,updated_at_utc_ms DESC LIMIT ?",
                    (status, bounded),
                ).fetchall()
            else:
                rows = self._execute(
                    "SELECT * FROM memory_facts ORDER BY confidence DESC,updated_at_utc_ms DESC LIMIT ?",
                    (bounded,),
                ).fetchall()
            return [
                MemoryFact(
                    fact_id=str(row["fact_id"]), statement=str(row["statement"]), fact_key=row["fact_key"],
                    subject=row["subject"], predicate=row["predicate"], object_value=row["object_value"],
                    valid_from_utc_ms=row["valid_from_utc_ms"], valid_to_utc_ms=row["valid_to_utc_ms"],
                    source_episode_ids=tuple(_from_json(row["source_episode_ids_json"], [])),
                    source_observation_ids=tuple(_from_json(row["source_observation_ids_json"], [])),
                    confidence=float(row["confidence"]), extraction_method=str(row["extraction_method"]),
                    extraction_version=str(row["extraction_version"]), status=str(row["status"]),
                    supersedes=tuple(_from_json(row["supersedes_json"], [])),
                    conflicts_with=tuple(_from_json(row["conflicts_with_json"], [])),
                )
                for row in rows
            ]

    def set_memory_fact_status(self, fact_id: str, status: str) -> bool:
        self._require_writable()
        if status not in {"active", "superseded", "retracted"}:
            raise ValueError("invalid memory fact status")
        with self._lock:
            cursor = self._execute(
                "UPDATE memory_facts SET status=?,updated_at_utc_ms=? WHERE fact_id=?",
                (status, int(time.time() * 1000), fact_id),
            )
            self._commit()
            return cursor.rowcount > 0

    def reset(self) -> None:
        self._require_writable()
        with self.transaction():
            for table in ("activity_evidence", "activity_item_sources", "activity_items", "activity_episodes",
                          "semantic_annotations", "memory_candidates", "memory_facts", "organizer_checkpoints",
                          "projection_errors", "activity_collection_gaps"):
                self._execute(f"DELETE FROM {table}")
            if self._fts_available:
                self._execute("DELETE FROM activity_fts")
            self._commit()

    def health(self, consumer_id: str = "default") -> KnowledgeHealth:
        with self._lock:
            try:
                _, checkpoint = self.checkpoint(consumer_id)
                item_count = int(self._execute("SELECT COUNT(*) FROM activity_items").fetchone()[0])
                episode_count = int(self._execute("SELECT COUNT(*) FROM activity_episodes").fetchone()[0])
                candidate_count = int(self._execute("SELECT COUNT(*) FROM memory_candidates").fetchone()[0])
                fact_count = int(self._execute("SELECT COUNT(*) FROM memory_facts").fetchone()[0])
                error_count = int(self._execute("SELECT COUNT(*) FROM projection_errors WHERE consumer_id=?",
                                               (consumer_id,)).fetchone()[0])
                return KnowledgeHealth(True, SCHEMA_VERSION, checkpoint, item_count, episode_count, candidate_count, self._fts_available, self.path.stat().st_size if self.path.exists() else 0, self._last_error, fact_count, error_count)
            except Exception as error:
                self._last_error = f"{type(error).__name__}: {error}"
                return KnowledgeHealth(False, SCHEMA_VERSION, 0, 0, 0, 0, self._fts_available, 0, self._last_error, 0)

    def quality_report(self) -> dict[str, Any]:
        with self._lock:
            qualities = dict(self._execute("SELECT quality,COUNT(*) FROM activity_items GROUP BY quality").fetchall())
            counts = self._execute("""SELECT COUNT(*),MIN(start_utc_ms),MAX(end_utc_ms),
                SUM(CASE WHEN text_summary<>'' THEN 1 ELSE 0 END),
                SUM(CASE WHEN title IS NOT NULL OR url IS NOT NULL THEN 1 ELSE 0 END)
                FROM activity_items""").fetchone()
            finalized = int(self._execute("SELECT COUNT(*) FROM activity_episodes WHERE finalized=1").fetchone()[0])
            projection_errors = int(self._execute("SELECT COUNT(*) FROM projection_errors").fetchone()[0])
            return {"item_count": counts[0], "quality_counts": qualities,
                    "oldest_observed_utc_ms": counts[1], "newest_observed_utc_ms": counts[2],
                    "items_with_text": counts[3] or 0, "items_with_title_or_url": counts[4] or 0,
                    "finalized_episode_count": finalized,
                    "projection_error_count": projection_errors,
                    "quality_basis": "capture_completeness_not_semantic_accuracy",
                    "episode_summary_basis": "observed_actions_and_changes_no_intent_inference"}

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self._connection.close()
                self._closed = True

    def __enter__(self) -> "KnowledgeStore":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()
