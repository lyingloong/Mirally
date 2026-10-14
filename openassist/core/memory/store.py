"""Durable memories and bounded background work, without model dependencies.

Activity projections can be rebuilt; this database deliberately cannot. Writers
use short SQLite transactions, while independent read-only connections can serve
recall without starting a worker or creating a database. A job lease fences late
model responses and persistent reservations bound spend across worker restarts.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence


SCHEMA_VERSION = 3
KINDS = frozenset({"working", "episodic", "task", "semantic"})
STATUSES = frozenset({"active", "candidate", "retracted", "superseded"})
PROVENANCES = frozenset({"observed", "model_inference", "user_confirmed"})
SOURCE_FIELDS = ("source_observation_ids", "source_item_ids", "source_episode_ids", "evidence_ids")
USAGE_FIELDS = ("requests", "tokens", "input_tokens", "output_tokens", "cost_usd")


def _now() -> int:
    return time.time_ns() // 1_000_000


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _usage(value: Mapping[str, Any] | None) -> dict[str, float | int]:
    result: dict[str, float | int] = {}
    for key in USAGE_FIELDS:
        number = (value or {}).get(key, 0)
        if isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number) or number < 0:
            raise ValueError(f"usage.{key} must be a finite nonnegative number")
        if key != "cost_usd" and number != int(number):
            raise ValueError(f"usage.{key} must be an integer")
        result[key] = float(number) if key == "cost_usd" else int(number)
    if value and "tokens" not in value:
        result["tokens"] = result["input_tokens"] + result["output_tokens"]
    return result


def _periods(now: int) -> tuple[str, str]:
    stamp = datetime.fromtimestamp(now / 1000, timezone.utc)
    return stamp.strftime("day:%Y-%m-%d"), stamp.strftime("hour:%Y-%m-%dT%H")


class MemoryStore:
    """Thread-safe SQLite store, with SQLite fencing across worker processes.

    ``put_memory`` returns the canonical record. Identical input is a no-op;
    revisions are assigned by the store. A forgotten source returns a minimal
    retracted record with ``blocked_by_tombstone=True`` and saves no new content.
    No method resets confirmed memories when an Activity index is rebuilt.
    """

    def __init__(self, path: Path | str, read_only: bool = False) -> None:
        self.path = Path(path)
        self.read_only = bool(read_only)
        self._lock = threading.RLock()
        self._closed = False
        self._savepoint_sequence = 0
        if self.read_only and not self.path.is_file():
            raise FileNotFoundError(f"memory database not found: {self.path}")
        if not self.read_only:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        target = self.path.resolve().as_uri() + "?mode=ro" if self.read_only else str(self.path)
        self._connection = sqlite3.connect(target, uri=self.read_only, timeout=5, isolation_level=None, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA busy_timeout=5000")
        self._connection.execute("PRAGMA foreign_keys=ON")
        try:
            if self.read_only:
                self._check_schema()
                self._connection.execute("PRAGMA query_only=ON")
            else:
                self._connection.execute("PRAGMA journal_mode=WAL")
                self._connection.execute("PRAGMA synchronous=NORMAL")
                self._initialize()
            self._trigram = self._connection.execute(
                "SELECT 1 FROM sqlite_master WHERE name='memory_literal_fts'"
            ).fetchone() is not None
        except BaseException:
            self._connection.close()
            raise

    def _check_schema(self) -> None:
        version = self._connection.execute("PRAGMA user_version").fetchone()[0]
        if version != SCHEMA_VERSION:
            raise RuntimeError(f"unsupported memory schema {version}; expected {SCHEMA_VERSION}")
        if not self._connection.execute("SELECT 1 FROM sqlite_master WHERE name='memories'").fetchone():
            raise RuntimeError("memory database is not initialized")

    def _initialize(self) -> None:
        version = self._connection.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            raise RuntimeError(f"memory schema {version} is newer than supported {SCHEMA_VERSION}")
        if version == SCHEMA_VERSION:
            self._check_schema()
            return
        if version == 1:
            with self._transaction():
                self._connection.execute("""CREATE TABLE IF NOT EXISTS memory_claims
                    (memory_id TEXT PRIMARY KEY REFERENCES memories(id) ON DELETE CASCADE,
                    scope TEXT NOT NULL,claim_key TEXT NOT NULL,value_hash TEXT NOT NULL)""")
                self._connection.execute("CREATE INDEX IF NOT EXISTS memory_claim_lookup ON memory_claims(scope,claim_key,value_hash)")
                for row in self._connection.execute("SELECT record_json FROM memories WHERE status IN ('active','candidate')"):
                    record = json.loads(row[0])
                    claim_key = record.get("conflict_key") or record.get("metadata", {}).get("conflict_key")
                    if claim_key:
                        value = record.get("metadata", {}).get("claim_value", record.get("content", ""))
                        self._connection.execute("INSERT OR REPLACE INTO memory_claims VALUES(?,?,?,?)", (record["id"], record["scope"], claim_key, _digest(_json(value))))
                self._connection.execute("PRAGMA user_version=2")
            version = 2
        if version == 2:
            with self._transaction():
                self._connection.execute("ALTER TABLE memory_jobs ADD COLUMN priority INTEGER NOT NULL DEFAULT 0")
                self._connection.execute("ALTER TABLE memory_jobs ADD COLUMN source_memory_id TEXT")
                self._connection.execute("""UPDATE memory_jobs SET priority=COALESCE(CAST(json_extract(payload_json,'$.observed_end_utc_ms') AS INTEGER),0),
                    source_memory_id=json_extract(payload_json,'$.memory_id')""")
                self._connection.execute("CREATE INDEX memory_job_priority ON memory_jobs(status,priority DESC,created_at_utc_ms,job_id)")
                self._connection.execute("CREATE INDEX memory_job_source ON memory_jobs(source_memory_id,status)")
                self._connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            return
        # Each schema step is transactional; a failed migration never advertises
        # the new version. Fresh databases are schema version zero.
        with self._transaction():
            statements = (
                "CREATE TABLE memory_state (key TEXT PRIMARY KEY,value_json TEXT NOT NULL,updated_at_utc_ms INTEGER NOT NULL)",
                """CREATE TABLE memories (id TEXT PRIMARY KEY,kind TEXT NOT NULL,scope TEXT NOT NULL,status TEXT NOT NULL,
                    provenance TEXT NOT NULL,title TEXT NOT NULL,content TEXT NOT NULL,
                    observed_start_utc_ms INTEGER,observed_end_utc_ms INTEGER,expires_at_utc_ms INTEGER,
                    revision INTEGER NOT NULL,content_hash TEXT NOT NULL,record_json TEXT NOT NULL,
                    created_at_utc_ms INTEGER NOT NULL,updated_at_utc_ms INTEGER NOT NULL)""",
                "CREATE INDEX memory_recall ON memories(scope,status,kind,observed_end_utc_ms DESC)",
                "CREATE INDEX memory_expiry ON memories(expires_at_utc_ms) WHERE expires_at_utc_ms IS NOT NULL",
                """CREATE TABLE memory_sources (memory_id TEXT NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
                    kind TEXT NOT NULL,source_id TEXT NOT NULL,PRIMARY KEY(memory_id,kind,source_id))""",
                "CREATE INDEX memory_source_lookup ON memory_sources(kind,source_id,memory_id)",
                """CREATE TABLE memory_claims (memory_id TEXT PRIMARY KEY REFERENCES memories(id) ON DELETE CASCADE,
                    scope TEXT NOT NULL,claim_key TEXT NOT NULL,value_hash TEXT NOT NULL)""",
                "CREATE INDEX memory_claim_lookup ON memory_claims(scope,claim_key,value_hash)",
                """CREATE TABLE memory_revisions (memory_id TEXT NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
                    revision INTEGER NOT NULL,record_json TEXT NOT NULL,PRIMARY KEY(memory_id,revision))""",
                """CREATE TABLE memory_tombstones (kind TEXT NOT NULL,target_hash TEXT NOT NULL,scope TEXT NOT NULL,
                    forgotten_at_utc_ms INTEGER NOT NULL,PRIMARY KEY(kind,target_hash,scope))""",
                """CREATE TABLE memory_jobs (job_id TEXT PRIMARY KEY,payload_json TEXT NOT NULL,status TEXT NOT NULL,
                    attempt INTEGER NOT NULL DEFAULT 0,max_attempts INTEGER NOT NULL,available_at_utc_ms INTEGER NOT NULL,
                    lease_token TEXT,lease_expires_utc_ms INTEGER,result_json TEXT,error TEXT,
                    created_at_utc_ms INTEGER NOT NULL,updated_at_utc_ms INTEGER NOT NULL,
                    priority INTEGER NOT NULL DEFAULT 0,source_memory_id TEXT)""",
                "CREATE INDEX memory_job_claim ON memory_jobs(status,available_at_utc_ms,created_at_utc_ms)",
                "CREATE INDEX memory_job_priority ON memory_jobs(status,priority DESC,created_at_utc_ms,job_id)",
                "CREATE INDEX memory_job_source ON memory_jobs(source_memory_id,status)",
                "CREATE TABLE memory_usage (period TEXT PRIMARY KEY,usage_json TEXT NOT NULL)",
                """CREATE TABLE memory_reservations (job_id TEXT NOT NULL,lease_token TEXT NOT NULL,
                    periods_json TEXT NOT NULL,usage_json TEXT NOT NULL,settled INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(job_id,lease_token))""",
                "CREATE VIRTUAL TABLE memory_fts USING fts5(memory_id UNINDEXED,title,content,tokenize='unicode61')",
            )
            for statement in statements:
                self._connection.execute(statement)
            # Trigram FTS gives Chinese substring search an index. Some older
            # SQLite builds lack it; SQL literal matching remains correct there.
            try:
                self._connection.execute("CREATE VIRTUAL TABLE memory_literal_fts USING fts5(memory_id UNINDEXED,title,content,tokenize='trigram')")
            except sqlite3.OperationalError as error:
                if "tokenizer" not in str(error).lower():
                    raise
            self._connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        if self.read_only:
            raise PermissionError("memory store is read-only")
        with self._lock:
            nested = self._connection.in_transaction
            self._savepoint_sequence += 1
            name = f"memory_savepoint_{self._savepoint_sequence}"
            self._connection.execute(f"SAVEPOINT {name}" if nested else "BEGIN IMMEDIATE")
            try:
                yield
                self._connection.execute(f"RELEASE SAVEPOINT {name}" if nested else "COMMIT")
            except BaseException:
                self._connection.execute(f"ROLLBACK TO SAVEPOINT {name}" if nested else "ROLLBACK")
                if nested:
                    self._connection.execute(f"RELEASE SAVEPOINT {name}")
                raise

    def transaction(self) -> Any:
        """Group memories and their durable processing checkpoint atomically."""
        return self._transaction()

    def get_state(self, key: str, default: Any = None) -> Any:
        with self._lock:
            row = self._connection.execute("SELECT value_json FROM memory_state WHERE key=?", (key,)).fetchone()
            return json.loads(row[0]) if row else default

    def set_state(self, key: str, value: Any) -> None:
        encoded = _json(value)
        with self._transaction():
            self._connection.execute("""INSERT INTO memory_state VALUES(?,?,?) ON CONFLICT(key)
                DO UPDATE SET value_json=excluded.value_json,updated_at_utc_ms=excluded.updated_at_utc_ms""", (key, encoded, _now()))

    def delete_state(self, key: str) -> bool:
        with self._transaction():
            return self._connection.execute("DELETE FROM memory_state WHERE key=?", (key,)).rowcount > 0

    def _normalize(self, record: Mapping[str, Any]) -> dict[str, Any]:
        result = json.loads(_json(dict(record)))
        aliases = {"memory_id": "id", "text": "content", "start_utc_ms": "observed_start_utc_ms", "end_utc_ms": "observed_end_utc_ms"}
        for alias, canonical in aliases.items():
            if canonical not in result and alias in result:
                result[canonical] = result[alias]
            result.pop(alias, None)
        result.setdefault("kind", "episodic")
        result.setdefault("scope", "user/local")
        result.setdefault("provenance", "observed")
        result.setdefault("status", "candidate" if result["kind"] == "semantic" and result["provenance"] == "model_inference" else "active")
        result.setdefault("title", "")
        result.setdefault("content", "")
        result.setdefault("confidence", 1.0)
        result.setdefault("metadata", {})
        for field in SOURCE_FIELDS:
            sources = result.get(field, [])
            if not isinstance(sources, (list, tuple)) or len(sources) > 10000 or any(not isinstance(item, str) or not item or len(item) > 512 for item in sources):
                raise ValueError(f"{field} must contain at most 10000 nonempty source IDs")
            result[field] = sorted(set(sources))
        if not result.get("id"):
            result["id"] = "memory_" + _digest(_json([result["scope"], result["kind"], result["title"], *(result[field] for field in SOURCE_FIELDS)]))[:32]
        for field in ("id", "scope", "title", "content"):
            if not isinstance(result[field], str):
                raise ValueError(f"{field} must be text")
        if not result["id"] or len(result["id"]) > 512 or not result["scope"] or len(result["scope"]) > 512:
            raise ValueError("memory id and scope must be nonempty and bounded")
        if result["kind"] not in KINDS or result["status"] not in STATUSES or result["provenance"] not in PROVENANCES:
            raise ValueError("invalid memory kind, status or provenance")
        if result["kind"] == "semantic" and result["provenance"] == "model_inference" and result["status"] == "active":
            result["status"] = "candidate"
        confidence = result["confidence"]
        if isinstance(confidence, bool) or not isinstance(confidence, (float, int)) or not math.isfinite(confidence) or not 0 <= confidence <= 1:
            raise ValueError("confidence must be between zero and one")
        if not isinstance(result["metadata"], dict):
            raise ValueError("metadata must be an object")
        for field in ("observed_start_utc_ms", "observed_end_utc_ms", "expires_at_utc_ms"):
            value = result.setdefault(field, None)
            if value is not None and (isinstance(value, bool) or not isinstance(value, int) or not -(2**63) <= value < 2**63):
                raise ValueError(f"{field} must be an SQLite integer or null")
        start, end = result["observed_start_utc_ms"], result["observed_end_utc_ms"]
        if start is not None and end is not None and start > end:
            raise ValueError("memory observation range is reversed")
        for field in ("revision", "created_at_utc_ms", "updated_at_utc_ms", "score", "truncated", "protected_user_confirmed", "blocked_by_tombstone"):
            result.pop(field, None)
        if len(_json(result).encode("utf-8")) > 2_000_000:
            raise ValueError("memory record exceeds the 2 MB storage limit")
        return result

    def _blocked(self, record: Mapping[str, Any]) -> bool:
        targets = [("memory", _digest(record["id"]))]
        targets.extend((kind, _digest(source)) for kind in SOURCE_FIELDS for source in record[kind])
        # Chunked indexed checks avoid SQLite variable limits for long histories.
        for index in range(0, len(targets), 100):
            chunk = targets[index:index + 100]
            predicates = " OR ".join("(kind=? AND target_hash=?)" for _ in chunk)
            params = [item for pair in chunk for item in pair] + [record["scope"]]
            if self._connection.execute(f"SELECT 1 FROM memory_tombstones WHERE ({predicates}) AND scope IN ('*',?) LIMIT 1", params).fetchone():
                return True
        return False

    def _can_refresh_source_revision(self, record: Mapping[str, Any], old: Mapping[str, Any], job: Mapping[str, Any] | None) -> bool:
        """Allow only a fenced host job to replace its internally stale summary."""
        if not job or old.get("status") != "superseded" or old.get("provenance") != "model_inference":
            return False
        metadata, previous = record.get("metadata", {}), old.get("metadata", {})
        payload = job["payload"]
        parent_id = payload.get("memory_id")
        if (record.get("kind") != "episodic" or record.get("provenance") != "model_inference"
                or record.get("status") != "active" or record.get("scope") != old.get("scope")
                or previous.get("superseded_reason") != "source_revision_changed"
                or old.get("superseded_by") or previous.get("superseded_by")
                or metadata.get("derived_from_memory_id") != parent_id
                or previous.get("derived_from_memory_id") != parent_id
                or metadata.get("job_id") != job["job_id"]):
            return False
        parent = self.get_memory(str(parent_id)) if parent_id else None
        if parent is None or parent.get("scope") != record["scope"]:
            return False
        current_hash = _digest(_json({key: parent.get(key) for key in (
            "content", "source_observation_ids", "observed_start_utc_ms", "observed_end_utc_ms")}))
        generation = (parent.get("metadata") or {}).get("source_generation")
        return (payload.get("input_hash") == current_hash
                and payload.get("source_generation") == generation
                and metadata.get("source_generation") == generation)

    def _put(self, record: Mapping[str, Any], *, source_revision_job: Mapping[str, Any] | None = None) -> dict[str, Any]:
        value = self._normalize(record)
        if self._blocked(value):
            return {"id": value["id"], "status": "retracted", "blocked_by_tombstone": True}
        old = self._connection.execute("SELECT * FROM memories WHERE id=?", (value["id"],)).fetchone()
        if old and old["provenance"] == "user_confirmed" and value["provenance"] != "user_confirmed":
            return {**json.loads(old["record_json"]), "protected_user_confirmed": True}
        if old and old["status"] in {"retracted", "superseded"} and value["status"] == "active" and value["provenance"] != "user_confirmed":
            previous = json.loads(old["record_json"])
            if not self._can_refresh_source_revision(value, previous, source_revision_job):
                return previous
        claim_key = value.get("conflict_key") or value["metadata"].get("conflict_key")
        claim_hash = _digest(_json(value["metadata"].get("claim_value", value["content"])))
        if claim_key:
            if not isinstance(claim_key, str) or len(claim_key) > 512:
                raise ValueError("conflict_key must contain at most 512 characters")
            conflicts = [row[0] for row in self._connection.execute("""SELECT c.memory_id FROM memory_claims c
                JOIN memories m ON m.id=c.memory_id WHERE c.scope=? AND c.claim_key=? AND c.value_hash<>?
                AND c.memory_id<>? AND m.status='active' ORDER BY c.memory_id""", (value["scope"], claim_key, claim_hash, value["id"]))]
            value["conflict_with"] = conflicts
            supersedes = value.get("supersedes", [])
            if not isinstance(supersedes, list) or any(not isinstance(item, str) for item in supersedes):
                raise ValueError("supersedes must contain memory IDs")
            if value["provenance"] == "user_confirmed" and supersedes:
                value["conflict_with"] = sorted(set(conflicts) - set(supersedes))
                value["resolved_conflicts"] = sorted(set(supersedes))
            if conflicts and value["status"] in {"active", "candidate"} and not (value["provenance"] == "user_confirmed" and set(conflicts) <= set(supersedes)):
                value["status"] = "candidate"
        fingerprint = _digest(_json(value))
        if old and old["content_hash"] == fingerprint:
            return json.loads(old["record_json"])
        now = _now()
        value.update(revision=(old["revision"] + 1 if old else 1), created_at_utc_ms=(old["created_at_utc_ms"] if old else now), updated_at_utc_ms=now)
        encoded = _json(value)
        self._connection.execute("""INSERT INTO memories VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
            kind=excluded.kind,scope=excluded.scope,status=excluded.status,provenance=excluded.provenance,title=excluded.title,
            content=excluded.content,observed_start_utc_ms=excluded.observed_start_utc_ms,observed_end_utc_ms=excluded.observed_end_utc_ms,
            expires_at_utc_ms=excluded.expires_at_utc_ms,revision=excluded.revision,content_hash=excluded.content_hash,
            record_json=excluded.record_json,updated_at_utc_ms=excluded.updated_at_utc_ms""", (
                value["id"], value["kind"], value["scope"], value["status"], value["provenance"], value["title"], value["content"],
                value["observed_start_utc_ms"], value["observed_end_utc_ms"], value["expires_at_utc_ms"], value["revision"], fingerprint, encoded,
                value["created_at_utc_ms"], value["updated_at_utc_ms"],
            ))
        self._connection.execute("INSERT INTO memory_revisions VALUES(?,?,?)", (value["id"], value["revision"], encoded))
        self._connection.execute("DELETE FROM memory_sources WHERE memory_id=?", (value["id"],))
        self._connection.executemany("INSERT INTO memory_sources VALUES(?,?,?)", [(value["id"], kind, source) for kind in SOURCE_FIELDS for source in value[kind]])
        self._connection.execute("DELETE FROM memory_claims WHERE memory_id=?", (value["id"],))
        if claim_key:
            self._connection.execute("INSERT INTO memory_claims VALUES(?,?,?,?)", (value["id"], value["scope"], claim_key, claim_hash))
        for table in ("memory_fts", "memory_literal_fts") if self._trigram else ("memory_fts",):
            self._connection.execute(f"DELETE FROM {table} WHERE memory_id=?", (value["id"],))
            if value["status"] in {"active", "candidate"}:
                self._connection.execute(f"INSERT INTO {table}(memory_id,title,content) VALUES(?,?,?)", (value["id"], value["title"], value["content"]))
        if value["provenance"] == "user_confirmed" and value["status"] == "active":
            for identifier in value.get("supersedes", []):
                other = self._connection.execute("SELECT record_json FROM memories WHERE id=? AND scope=? AND status IN ('active','candidate')", (identifier, value["scope"])).fetchone()
                if other and identifier != value["id"]:
                    previous = json.loads(other[0])
                    previous.update(status="superseded", superseded_by=value["id"])
                    self._put(previous)
        return value

    def put_memory(self, record: Mapping[str, Any]) -> dict[str, Any]:
        with self._transaction():
            return self._put(record)

    def get_memory(self, memory_id: str, include_inactive: bool = False, *, now_utc_ms: int | None = None) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute("SELECT record_json,status,expires_at_utc_ms FROM memories WHERE id=?", (memory_id,)).fetchone()
            if row is None:
                return None
            if not include_inactive and (row["status"] != "active" or (row["expires_at_utc_ms"] is not None and row["expires_at_utc_ms"] <= (_now() if now_utc_ms is None else now_utc_ms))):
                return None
            return json.loads(row["record_json"])

    def search(self, query: str = "", limit: int = 10, kinds: Sequence[str] = (), scope: str | None = None,
               start_utc_ms: int | None = None, end_utc_ms: int | None = None, include_candidates: bool = False,
               now_utc_ms: int | None = None, *, offset: int = 0, max_chars: int = 12000) -> list[dict[str, Any]]:
        """Return relevant active records with a total title/content character budget.

        Word FTS and literal substring matches are combined. SQL applies scope,
        status, time and expiry before LIMIT; sources/metadata are retained, and
        shortened output explicitly carries ``truncated=True``.
        """
        if not isinstance(query, str) or len(query) > 512:
            raise ValueError("query must contain at most 512 characters")
        limit, offset, max_chars = int(limit), int(offset), int(max_chars)
        if not 1 <= limit <= 100 or not 0 <= offset <= 100000 or not 0 <= max_chars <= 1000000:
            raise ValueError("search pagination or character budget is out of range")
        if any(kind not in KINDS for kind in kinds):
            raise ValueError("unknown memory kind")
        now = _now() if now_utc_ms is None else now_utc_ms
        conditions, params = ["m.status IN ('active','candidate')" if include_candidates else "m.status='active'", "(m.expires_at_utc_ms IS NULL OR m.expires_at_utc_ms>?)"], [now]
        if kinds:
            conditions.append("m.kind IN (" + ",".join("?" for _ in kinds) + ")")
            params.extend(kinds)
        if scope is not None:
            conditions.append("m.scope=?")
            params.append(scope)
        if start_utc_ms is not None:
            conditions.append("m.observed_end_utc_ms>=?")
            params.append(start_utc_ms)
        if end_utc_ms is not None:
            conditions.append("m.observed_start_utc_ms<=?")
            params.append(end_utc_ms)
        text = query.strip()
        score = "0.0"
        joins = ""
        leading: list[Any] = []
        if text:
            tokens = re.findall(r"\w+", text, re.UNICODE)
            fts_query = " OR ".join('"' + token.replace('"', '""') + '"' for token in tokens)
            sets: list[str] = []
            if fts_query:
                sets.append("SELECT memory_id,-bm25(memory_fts) AS relevance FROM memory_fts WHERE memory_fts MATCH ?")
                leading.append(fts_query)
            if self._trigram and len(text) >= 3:
                sets.append("SELECT memory_id,1.0 AS relevance FROM memory_literal_fts WHERE memory_literal_fts MATCH ?")
                leading.append('"' + text.replace('"', '""') + '"')
            else:
                sets.append("SELECT id AS memory_id,1.0 AS relevance FROM memories WHERE instr(lower(title),lower(?))>0 OR instr(lower(content),lower(?))>0")
                leading.extend([text, text])
            joins = " JOIN (SELECT memory_id,MAX(relevance) AS relevance FROM (" + " UNION ALL ".join(sets) + ") GROUP BY memory_id) r ON r.memory_id=m.id"
            score = "r.relevance"
        sql = f"SELECT m.record_json,{score} AS score FROM memories m{joins} WHERE " + " AND ".join(conditions) + f" ORDER BY {score} DESC,COALESCE(m.observed_end_utc_ms,m.updated_at_utc_ms) DESC,m.id LIMIT ? OFFSET ?"
        with self._lock:
            rows = self._connection.execute(sql, [*leading, *params, limit, offset]).fetchall()
        result: list[dict[str, Any]] = []
        remaining = max_chars
        for row in rows:
            if remaining <= 0:
                break
            record = json.loads(row["record_json"])
            record["score"] = row["score"]
            original = len(record["title"]) + len(record["content"])
            record["title"] = record["title"][:remaining]
            remaining -= len(record["title"])
            record["content"] = record["content"][:remaining]
            remaining -= len(record["content"])
            record["truncated"] = original > len(record["title"]) + len(record["content"])
            result.append(record)
        return result

    def search_page(self, query: str = "", *, offset: int = 0, limit: int = 10, **kwargs: Any) -> dict[str, Any]:
        items = self.search(query, offset=offset, limit=limit, **kwargs)
        next_offset = offset + len(items)
        # Probe without returning another record body; a budget exhausted midway
        # through a record still advances over that explicitly truncated record.
        more = bool(items) and bool(self.search(query, offset=next_offset, limit=1, **kwargs))
        return {"items": items, "next_offset": next_offset if more else None, "truncated": any(item["truncated"] for item in items)}

    def revisions(self, memory_id: str, *, limit: int = 20) -> list[dict[str, Any]]:
        if not 1 <= limit <= 100:
            raise ValueError("revision limit must be between 1 and 100")
        with self._lock:
            return [json.loads(row[0]) for row in self._connection.execute("SELECT record_json FROM memory_revisions WHERE memory_id=? ORDER BY revision DESC LIMIT ?", (memory_id, limit))]

    def scan_memories(self, *, after_id: str | None = None, limit: int = 100, include_inactive: bool = False) -> list[dict[str, Any]]:
        """Keyset scan for bounded background maintenance, independent of recall.

        A maintenance worker should use this instead of increasing OFFSET over a
        large store. Expired records remain visible here so their evidence status
        can be maintained without making them eligible for conversational recall.
        """
        if not 1 <= limit <= 1000:
            raise ValueError("scan limit must be between 1 and 1000")
        clauses = ["id>?"]
        if not include_inactive:
            clauses.append("status IN ('active','candidate')")
        with self._lock:
            return [json.loads(row[0]) for row in self._connection.execute("SELECT record_json FROM memories WHERE " + " AND ".join(clauses) + " ORDER BY id LIMIT ?", (after_id or "", limit))]

    def forget(self, memory_id: str | None = None, source_observation_ids: Sequence[str] = (), scope: str | None = None) -> dict[str, Any]:
        """Remove recall content and persist suppression of every derived source.

        This is explicit user forgetting, not raw-evidence retention expiry. It
        does not delete the Activity source database. Revisions and queued model
        inputs containing the forgotten sources are scrubbed in this transaction.
        """
        if not memory_id and not source_observation_ids and scope is None:
            raise ValueError("forget requires a memory id, observation sources or scope")
        with self._transaction():
            clauses, params = [], []
            if memory_id:
                clauses.append("id=?")
                params.append(memory_id)
            if source_observation_ids:
                # The public input is bounded to avoid an accidental enormous IN.
                if len(source_observation_ids) > 10000:
                    raise ValueError("too many forgotten sources")
                clauses.append("id IN (SELECT memory_id FROM memory_sources WHERE kind='source_observation_ids' AND source_id IN (SELECT value FROM json_each(?)))")
                params.append(_json(list(source_observation_ids)))
            condition = "(" + " OR ".join(clauses) + ")" if clauses else "1=1"
            if scope is not None:
                condition += " AND scope=?"
                params.append(scope)
            selected = {record["id"]: record for record in (
                json.loads(row[0]) for row in self._connection.execute("SELECT record_json FROM memories WHERE " + condition, params)
            )}
            now, forgotten, source_targets = _now(), [], set()
            # Expand only from the user's original target sources. A dependent
            # summary can also mention unrelated observations; adding those as
            # new seeds would erase the entire connected history transitively.
            seeds: dict[tuple[str, str], set[str]] = {}
            for source in source_observation_ids:
                seeds.setdefault(("source_observation_ids", scope or "*"), set()).add(source)
            for record in selected.values():
                if record["id"] == memory_id or (not memory_id and not source_observation_ids):
                    for kind in SOURCE_FIELDS:
                        seeds.setdefault((kind, record["scope"]), set()).update(record[kind])
            for (kind, source_scope), sources in seeds.items():
                if not sources:
                    continue
                query = """SELECT DISTINCT m.record_json FROM memories m JOIN memory_sources s ON s.memory_id=m.id
                    WHERE s.kind=? AND s.source_id IN (SELECT value FROM json_each(?))"""
                values = [kind, _json(sorted(sources))]
                if source_scope != "*":
                    query += " AND m.scope=?"
                    values.append(source_scope)
                for row in self._connection.execute(query, values):
                    record = json.loads(row[0])
                    selected[record["id"]] = record
                for source in sources:
                    source_targets.add(source)
                    self._connection.execute("INSERT OR IGNORE INTO memory_tombstones VALUES(?,?,?,?)", (kind, _digest(source), source_scope, now))
            if memory_id:
                self._connection.execute("INSERT OR IGNORE INTO memory_tombstones VALUES('memory',?,'*',?)", (_digest(memory_id), now))
            for record in selected.values():
                identifier = record["id"]
                forgotten.append(identifier)
                self._connection.execute("INSERT OR IGNORE INTO memory_tombstones VALUES('memory',?,'*',?)", (_digest(identifier), now))
                minimal = {"id": identifier, "kind": record["kind"], "scope": record["scope"], "status": "retracted", "provenance": record["provenance"], "revision": record["revision"] + 1, "title": "", "content": "", "forgotten_at_utc_ms": now, **{field: [] for field in SOURCE_FIELDS}}
                self._connection.execute("""UPDATE memories SET status='retracted',title='',content='',revision=?,record_json=?,
                    content_hash='',observed_start_utc_ms=NULL,observed_end_utc_ms=NULL,expires_at_utc_ms=NULL,updated_at_utc_ms=? WHERE id=?""", (minimal["revision"], _json(minimal), now, identifier))
                self._connection.execute("DELETE FROM memory_revisions WHERE memory_id=?", (identifier,))
                self._connection.execute("DELETE FROM memory_sources WHERE memory_id=?", (identifier,))
                self._connection.execute("DELETE FROM memory_claims WHERE memory_id=?", (identifier,))
                for table in ("memory_fts", "memory_literal_fts") if self._trigram else ("memory_fts",):
                    self._connection.execute(f"DELETE FROM {table} WHERE memory_id=?", (identifier,))
            # Jobs are bounded batches, but can contain nested source references.
            # Exact JSON string matching avoids substring collisions (o1 vs o10).
            needles = [_json(item) for item in source_targets | set(forgotten) | ({memory_id} if memory_id else set())]
            cancelled = 0
            for job in self._connection.execute("SELECT job_id,payload_json,result_json FROM memory_jobs"):
                if any(needle in job["payload_json"] or needle in (job["result_json"] or "") for needle in needles):
                    self._connection.execute("""UPDATE memory_jobs SET payload_json='{}',result_json=NULL,error='forgotten source',
                        status='cancelled',lease_token=NULL,lease_expires_utc_ms=NULL,updated_at_utc_ms=? WHERE job_id=?""", (now, job["job_id"]))
                    cancelled += 1
            for state in self._connection.execute("SELECT key,value_json FROM memory_state"):
                if any(needle in state["value_json"] for needle in needles):
                    self._connection.execute("DELETE FROM memory_state WHERE key=?", (state["key"],))
            return {"forgotten_count": len(forgotten), "memory_ids": forgotten, "cancelled_jobs": cancelled, "source_data_deleted": False}

    def enqueue_job(self, job_id: str, payload: Mapping[str, Any], *, max_attempts: int = 3, available_at_utc_ms: int | None = None, priority: int | None = None) -> bool:
        if not job_id or not 1 <= max_attempts <= 100:
            raise ValueError("job id and retry count are required")
        encoded = _json(dict(payload))
        if len(encoded.encode("utf-8")) > 2_000_000:
            raise ValueError("job payload exceeds 2 MB")
        priority = payload.get("observed_end_utc_ms", 0) or 0 if priority is None else priority
        if isinstance(priority, bool) or not isinstance(priority, int) or not -(2**63) <= priority < 2**63:
            raise ValueError("job priority must be an SQLite integer")
        source_memory_id = payload.get("memory_id")
        if source_memory_id is not None and (not isinstance(source_memory_id, str) or len(source_memory_id) > 512):
            raise ValueError("job source memory id must be bounded text")
        with self._transaction():
            now = _now()
            if self._payload_blocked(payload):
                return False
            cursor = self._connection.execute("""INSERT OR IGNORE INTO memory_jobs(job_id,payload_json,status,max_attempts,
                available_at_utc_ms,created_at_utc_ms,updated_at_utc_ms,priority,source_memory_id)
                VALUES(?,?,'pending',?,?,?,?,?,?)""", (job_id, encoded, max_attempts, now if available_at_utc_ms is None else available_at_utc_ms, now, now, priority, source_memory_id))
            if cursor.rowcount == 1 and source_memory_id:
                # Running calls retain their fenced lease and usage; their result
                # must be checked against the current source revision by the host.
                self._connection.execute("""UPDATE memory_jobs SET status='cancelled',error='superseded input revision',updated_at_utc_ms=?
                    WHERE source_memory_id=? AND job_id<>? AND status='pending'""", (now, source_memory_id, job_id))
            return cursor.rowcount == 1

    def _payload_blocked(self, payload: Any, scope: str = "user/local") -> bool:
        if isinstance(payload, dict):
            selected_scope = payload.get("scope", scope)
            sources = {field: payload.get(field, []) for field in SOURCE_FIELDS}
            if any(sources.values()) or payload.get("memory_id"):
                check = {"id": str(payload.get("memory_id", "")), "scope": selected_scope, **sources}
                if self._blocked(check):
                    return True
            return any(self._payload_blocked(value, selected_scope) for value in payload.values() if isinstance(value, (dict, list)))
        if isinstance(payload, list):
            return any(self._payload_blocked(value, scope) for value in payload if isinstance(value, (dict, list)))
        return False

    def claim_job(self, lease_ms: int = 120000, *, now_utc_ms: int | None = None) -> dict[str, Any] | None:
        if not 1 <= lease_ms <= 3_600_000:
            raise ValueError("lease duration must be between 1 ms and one hour")
        with self._transaction():
            now = _now() if now_utc_ms is None else now_utc_ms
            self._connection.execute("""UPDATE memory_jobs SET status='failed',error='lease expired after final attempt',
                lease_token=NULL,lease_expires_utc_ms=NULL,updated_at_utc_ms=?
                WHERE status='running' AND lease_expires_utc_ms<=? AND attempt>=max_attempts""", (now, now))
            row = self._connection.execute("""SELECT * FROM (
                SELECT * FROM memory_jobs INDEXED BY memory_job_priority WHERE attempt<max_attempts
                    AND status='pending' AND available_at_utc_ms<=? ORDER BY priority DESC,created_at_utc_ms,job_id LIMIT 1
                ) UNION ALL SELECT * FROM (
                SELECT * FROM memory_jobs INDEXED BY memory_job_priority WHERE attempt<max_attempts
                    AND status='running' AND lease_expires_utc_ms<=? ORDER BY priority DESC,created_at_utc_ms,job_id LIMIT 1
                ) ORDER BY priority DESC,created_at_utc_ms,job_id LIMIT 1""", (now, now)).fetchone()
            if row is None:
                return None
            token = uuid.uuid4().hex
            self._connection.execute("UPDATE memory_jobs SET status='running',attempt=attempt+1,lease_token=?,lease_expires_utc_ms=?,updated_at_utc_ms=? WHERE job_id=?", (token, now + lease_ms, now, row["job_id"]))
            return {"job_id": row["job_id"], "id": row["job_id"], "payload": json.loads(row["payload_json"]), "attempt": row["attempt"] + 1, "lease_token": token, "lease_expires_utc_ms": now + lease_ms}

    def renew_job(self, job_id: str, lease_token: str, lease_ms: int = 120000) -> bool:
        if not 1 <= lease_ms <= 3_600_000:
            raise ValueError("invalid lease duration")
        with self._transaction():
            now = _now()
            return self._connection.execute("""UPDATE memory_jobs SET lease_expires_utc_ms=?,updated_at_utc_ms=?
                WHERE job_id=? AND status='running' AND lease_token=? AND lease_expires_utc_ms>?""", (now + lease_ms, now, job_id, lease_token, now)).rowcount == 1

    def _leased(self, job_id: str, token: str) -> sqlite3.Row | None:
        return self._connection.execute("SELECT * FROM memory_jobs WHERE job_id=? AND status='running' AND lease_token=? AND lease_expires_utc_ms>?", (job_id, token, _now())).fetchone()

    def reserve_usage(self, job_id: str, lease_token: str, reservation: Mapping[str, Any], limits: Mapping[str, Any], *, period: str | None = None) -> bool:
        """Atomically reserve UTC day/hour usage before calling a model.

        ``requests_hourly`` and ``tokens_daily`` limits may be combined with
        generic daily ``requests``, ``tokens`` and ``cost_usd``. Expired or failed
        calls retain their reservation unless actual usage is known; restarting
        never makes uncertain spend available again.
        """
        reserved = _usage(reservation)
        with self._transaction():
            if self._leased(job_id, lease_token) is None:
                return False
            existing = self._connection.execute("SELECT usage_json FROM memory_reservations WHERE job_id=? AND lease_token=?", (job_id, lease_token)).fetchone()
            if existing:
                return json.loads(existing[0]) == reserved
            day, hour = _periods(_now())
            periods = [period] if period else [day, hour]
            for selected in periods:
                current = self._read_usage(selected)
                for key in USAGE_FIELDS:
                    bound = limits.get(key)
                    specific = limits.get(key + ("_hourly" if selected.startswith("hour:") else "_daily"))
                    if selected.startswith("hour:"):
                        bound = specific
                    elif specific is not None:
                        bound = min(bound, specific) if bound is not None else specific
                    if bound is not None:
                        if isinstance(bound, bool) or not isinstance(bound, (int, float)) or not math.isfinite(bound) or bound < 0:
                            raise ValueError(f"invalid budget for {key}")
                        if current[key] + reserved[key] > bound:
                            return False
            for selected in periods:
                self._add_usage(selected, reserved)
            self._connection.execute("INSERT INTO memory_reservations VALUES(?,?,?,?,0)", (job_id, lease_token, _json(periods), _json(reserved)))
            return True

    def _read_usage(self, period: str) -> dict[str, float | int]:
        row = self._connection.execute("SELECT usage_json FROM memory_usage WHERE period=?", (period,)).fetchone()
        return _usage(json.loads(row[0]) if row else {})

    def _add_usage(self, period: str, usage: Mapping[str, Any]) -> None:
        current = self._read_usage(period)
        updated = {key: max(0, current[key] + usage.get(key, 0)) for key in USAGE_FIELDS}
        self._connection.execute("INSERT INTO memory_usage VALUES(?,?) ON CONFLICT(period) DO UPDATE SET usage_json=excluded.usage_json", (period, _json(updated)))

    def _settle(self, job_id: str, token: str, usage: Mapping[str, Any] | None) -> None:
        row = self._connection.execute("SELECT * FROM memory_reservations WHERE job_id=? AND lease_token=?", (job_id, token)).fetchone()
        if row and not row["settled"]:
            if usage:
                reserved, reported = json.loads(row["usage_json"]), _usage(usage)
                actual = dict(reserved)
                # A missing field is unknown, not a reported zero. In particular,
                # request-only usage must not release token or cost reservations.
                for key in ("requests", "input_tokens", "output_tokens", "cost_usd"):
                    if key in usage:
                        actual[key] = reported[key]
                if "tokens" in usage:
                    actual["tokens"] = reported["tokens"]
                elif "input_tokens" in usage and "output_tokens" in usage:
                    actual["tokens"] = reported["input_tokens"] + reported["output_tokens"]
                else:
                    # Partial counts cannot make unreported output free. A known
                    # lower bound above the reservation still increases spend.
                    actual["tokens"] = max(reserved["tokens"], reported["input_tokens"] + reported["output_tokens"])
                delta = {key: actual[key] - reserved[key] for key in USAGE_FIELDS}
                for period in json.loads(row["periods_json"]):
                    self._add_usage(period, delta)
                self._connection.execute("UPDATE memory_reservations SET usage_json=? WHERE job_id=? AND lease_token=?", (_json(actual), job_id, token))
            self._connection.execute("UPDATE memory_reservations SET settled=1 WHERE job_id=? AND lease_token=?", (job_id, token))
        elif not row and usage:
            actual = _usage(usage)
            actual["requests"] = actual["requests"] or 1
            for period in _periods(_now()):
                self._add_usage(period, actual)

    def usage_summary(self, period: str | None = None) -> dict[str, Any]:
        selected = period or _periods(_now())[0]
        with self._lock:
            return {"period": selected, **self._read_usage(selected)}

    def finish_job(self, job_id: str, lease_token: str, result: Mapping[str, Any], memories: Sequence[Mapping[str, Any]] = (), usage: Mapping[str, Any] | None = None) -> bool:
        """Commit all generated memories, usage and completion as one transaction."""
        encoded = _json(dict(result))
        with self._transaction():
            job = self._leased(job_id, lease_token)
            if job is None:
                return False
            revision_job = {"job_id": job_id, "payload": json.loads(job["payload_json"])}
            written = [self._put(record, source_revision_job=revision_job) for record in memories]
            if any(record.get("blocked_by_tombstone") for record in written):
                # Never persist returned model text after its evidence was forgotten.
                encoded = _json({"suppressed_forgotten_memories": True})
            self._settle(job_id, lease_token, usage)
            self._connection.execute("""UPDATE memory_jobs SET status='complete',result_json=?,error=NULL,
                lease_token=NULL,lease_expires_utc_ms=NULL,updated_at_utc_ms=? WHERE job_id=?""", (encoded, _now(), job_id))
            return True

    def complete_job(self, job_id: str, lease_token: str, result: Mapping[str, Any], usage: Mapping[str, Any] | None = None) -> bool:
        return self.finish_job(job_id, lease_token, result, usage=usage)

    def fail_job(self, job_id: str, lease_token: str, error: str, retry_delay_ms: int = 60000, *, max_attempts: int | None = None, usage: Mapping[str, Any] | None = None) -> bool:
        if retry_delay_ms < 0 or (max_attempts is not None and not 1 <= max_attempts <= 100):
            raise ValueError("invalid retry policy")
        with self._transaction():
            row = self._leased(job_id, lease_token)
            if row is None:
                return False
            ceiling = row["max_attempts"] if max_attempts is None else min(row["max_attempts"], max_attempts)
            self._settle(job_id, lease_token, usage)
            now = _now()
            self._connection.execute("""UPDATE memory_jobs SET status=?,max_attempts=?,available_at_utc_ms=?,
                lease_token=NULL,lease_expires_utc_ms=NULL,error=?,updated_at_utc_ms=? WHERE job_id=?""", ("failed" if row["attempt"] >= ceiling else "pending", ceiling, now + retry_delay_ms, str(error)[:2000], now, job_id))
            return True

    def defer_job(self, job_id: str, lease_token: str, delay_ms: int = 60000, reason: str = "budget unavailable") -> bool:
        """Return an unstarted claim without spending its model retry allowance.

        A budget rejection is not a model failure. Only claims without a usage
        reservation may be deferred; uncertain already-issued calls must use
        ``fail_job`` and keep their charged reservation instead.
        """
        if delay_ms < 0:
            raise ValueError("job delay must be nonnegative")
        with self._transaction():
            if self._leased(job_id, lease_token) is None:
                return False
            if self._connection.execute("SELECT 1 FROM memory_reservations WHERE job_id=? AND lease_token=?", (job_id, lease_token)).fetchone():
                return False
            now = _now()
            self._connection.execute("""UPDATE memory_jobs SET status='pending',attempt=attempt-1,
                available_at_utc_ms=?,lease_token=NULL,lease_expires_utc_ms=NULL,error=?,updated_at_utc_ms=?
                WHERE job_id=?""", (now + delay_ms, str(reason)[:2000], now, job_id))
            return True

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute("SELECT * FROM memory_jobs WHERE job_id=?", (job_id,)).fetchone()
            if row is None:
                return None
            value = dict(row)
            value["payload"] = json.loads(value.pop("payload_json"))
            encoded_result = value.pop("result_json")
            value["result"] = json.loads(encoded_result) if encoded_result is not None else None
            return value

    def health(self) -> dict[str, Any]:
        with self._lock:
            statuses = {row[0]: row[1] for row in self._connection.execute("SELECT status,count(*) FROM memories GROUP BY status")}
            jobs = {row[0]: row[1] for row in self._connection.execute("SELECT status,count(*) FROM memory_jobs GROUP BY status")}
            now = _now()
            active = self._connection.execute("SELECT count(*) FROM memories WHERE status='active' AND (expires_at_utc_ms IS NULL OR expires_at_utc_ms>?)", (now,)).fetchone()[0]
            return {"available": True, "schema_version": SCHEMA_VERSION, "read_only": self.read_only, "memory_count": sum(statuses.values()), "active_count": active, "statuses": statuses, "jobs": jobs, "fts_available": True, "literal_fts_available": self._trigram, "tombstone_count": self._connection.execute("SELECT count(*) FROM memory_tombstones").fetchone()[0], "usage": self.usage_summary(), "db_bytes": self.path.stat().st_size}

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self._connection.close()
                self._closed = True

    def __enter__(self) -> "MemoryStore":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()
