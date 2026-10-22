"""Persistent-memory correctness independent of models and desktop capture."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from mirally.core.memory import MemoryStore


class MemoryStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "memory.sqlite3"
        self.store = MemoryStore(self.path)

    def tearDown(self) -> None:
        self.store.close()
        self.directory.cleanup()

    def memory(self, identifier: str = "m1", **kwargs: object) -> dict:
        return {"id": identifier, "kind": "episodic", "title": "Activity memory", "content": "Worked on a project", "source_observation_ids": ["obs1"], "observed_start_utc_ms": 100, "observed_end_utc_ms": 200, **kwargs}

    def test_durable_coverage_excludes_unrecallable_records_and_survives_raw_archival(self) -> None:
        self.store.put_memory(self.memory("old", observed_start_utc_ms=100, observed_end_utc_ms=200))
        self.store.put_memory(self.memory("new", observed_start_utc_ms=800, observed_end_utc_ms=900, expires_at_utc_ms=2000))
        for identifier, fields in [("candidate", {"status": "candidate"}),
                                   ("retracted", {"status": "retracted"}),
                                   ("superseded", {"status": "superseded"}),
                                   ("expired", {"expires_at_utc_ms": 1000})]:
            self.store.put_memory(self.memory(identifier, observed_start_utc_ms=1, observed_end_utc_ms=9999, **fields))
        self.store.set_state("memory_archival:old", {"source_retention_status": "archived"})
        with MemoryStore(self.path, read_only=True) as reader:
            health = reader.health(now_utc_ms=1000)
            coverage = health["durable_coverage"]
            self.assertEqual(health["active_count"], 2)
            self.assertEqual(coverage["active_count"], 2)
            self.assertEqual((coverage["observed_start_utc_ms"], coverage["observed_end_utc_ms"]), (100, 900))
            for field, value in [("observed_start_local", 100), ("observed_end_local", 900)]:
                date = datetime.fromisoformat(coverage[field])
                self.assertIsNotNone(date.utcoffset())
                self.assertAlmostEqual(date.timestamp() * 1000, value)
            self.assertIn("separate", coverage["note"])

    def test_durable_coverage_empty_and_undated_records_keep_unknown_bounds(self) -> None:
        coverage = self.store.durable_coverage(now_utc_ms=1000)
        self.assertEqual(coverage["active_count"], 0)
        self.assertIsNone(coverage["observed_start_local"])
        self.store.put_memory(self.memory("undated", observed_start_utc_ms=None, observed_end_utc_ms=None))
        coverage = self.store.durable_coverage(now_utc_ms=1000)
        self.assertEqual(coverage["active_count"], 1)
        self.assertIsNone(coverage["observed_start_utc_ms"])
        self.assertIsNone(coverage["observed_end_utc_ms"])

    def test_noop_versions_and_restart_preserve_memories_and_checkpoint(self) -> None:
        first = self.store.put_memory(self.memory())
        self.assertEqual(self.store.put_memory(self.memory()), first)
        with self.store.transaction():
            second = self.store.put_memory(self.memory(content="Changed project"))
            self.store.set_state("checkpoint", {"ingest_seq": 17})
        self.assertEqual(second["revision"], 2)
        self.store.close()
        self.store = MemoryStore(self.path)
        self.assertEqual(self.store.get_state("checkpoint"), {"ingest_seq": 17})
        self.assertEqual(self.store.get_memory("m1")["content"], "Changed project")
        self.assertEqual([item["revision"] for item in self.store.revisions("m1")], [2, 1])

    def test_public_transaction_rolls_back_checkpoint_and_records(self) -> None:
        with self.assertRaises(ValueError):
            with self.store.transaction():
                self.store.put_memory(self.memory())
                self.store.set_state("checkpoint", 12)
                self.store.put_memory(self.memory("invalid", confidence=2))
        self.assertIsNone(self.store.get_memory("m1"))
        self.assertIsNone(self.store.get_state("checkpoint"))

    def test_cleanup_journal_commits_with_full_sync_and_restores_normal_mode(self) -> None:
        self.store.put_memory(self.memory())
        trace = []
        self.store._connection.set_trace_callback(trace.append)
        try:
            with self.store.durable_transaction():
                self.assertEqual(self.store._connection.execute("PRAGMA synchronous").fetchone()[0], 2)
                self.store.set_state("cleanup_journal", {"sources": ["obs1"]})
        finally:
            self.store._connection.set_trace_callback(None)
        self.assertLess(trace.index("PRAGMA synchronous=FULL"), trace.index("COMMIT"))
        self.assertLess(trace.index("COMMIT"), trace.index("PRAGMA synchronous=1"))
        self.assertEqual(self.store._connection.execute("PRAGMA synchronous").fetchone()[0], 1)
        self.store.close()
        self.store = MemoryStore(self.path)
        self.assertEqual(self.store.get_state("cleanup_journal"), {"sources": ["obs1"]})
        self.assertIsNotNone(self.store.get_memory("m1"))

    def test_failed_durable_journal_rolls_back_and_cannot_be_nested(self) -> None:
        with self.assertRaisesRegex(ValueError, "barrier failed"):
            with self.store.durable_transaction():
                self.store.set_state("cleanup_journal", {"sources": ["obs1"]})
                raise ValueError("barrier failed")
        self.assertIsNone(self.store.get_state("cleanup_journal"))
        self.assertEqual(self.store._connection.execute("PRAGMA synchronous").fetchone()[0], 1)
        with self.store.transaction():
            with self.assertRaisesRegex(RuntimeError, "outermost"):
                with self.store.durable_transaction():
                    pass

    def test_read_only_reader_never_creates_database_and_sees_wal_commits(self) -> None:
        missing = self.path.parent / "absent" / "missing.sqlite3"
        with self.assertRaises(FileNotFoundError):
            MemoryStore(missing, read_only=True)
        self.assertFalse(missing.parent.exists())
        with MemoryStore(self.path, read_only=True) as reader:
            self.store.put_memory(self.memory())
            self.assertEqual(reader.get_memory("m1")["id"], "m1")
            with self.assertRaises(PermissionError):
                reader.set_state("checkpoint", 8)
            with self.assertRaises(PermissionError):
                reader.forget("m1")

    def test_future_schema_is_rejected_without_downgrade(self) -> None:
        self.store.close()
        connection = sqlite3.connect(self.path)
        connection.execute("PRAGMA user_version=999")
        connection.close()
        with self.assertRaisesRegex(RuntimeError, "newer"):
            MemoryStore(self.path)
        connection = sqlite3.connect(self.path)
        self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 999)
        connection.close()

    def test_schema_migration_preserves_confirmed_records_state_and_usage(self) -> None:
        self.store.put_memory(self.memory(provenance="user_confirmed"))
        self.store.set_state("checkpoint", {"ingest_seq": 44})
        self.store.enqueue_job("j1", {})
        job = self.store.claim_job()
        self.store.reserve_usage("j1", job["lease_token"], {"requests": 1, "tokens": 20}, {"tokens_daily": 100})
        self.store.close()
        connection = sqlite3.connect(self.path)
        connection.execute("DROP TABLE memory_claims")
        connection.execute("DROP INDEX memory_job_priority")
        connection.execute("DROP INDEX memory_job_source")
        connection.execute("ALTER TABLE memory_jobs DROP COLUMN priority")
        connection.execute("ALTER TABLE memory_jobs DROP COLUMN source_memory_id")
        connection.execute("PRAGMA user_version=1")
        connection.close()
        self.store = MemoryStore(self.path)
        self.assertEqual(self.store.health()["schema_version"], 3)
        self.assertEqual(self.store.get_memory("m1")["provenance"], "user_confirmed")
        self.assertEqual(self.store.get_state("checkpoint"), {"ingest_seq": 44})
        self.assertEqual(self.store.usage_summary()["tokens"], 20)

    def test_literal_chinese_search_fts_syntax_and_scope_filters(self) -> None:
        self.store.put_memory(self.memory(content="今天修改持久记忆系统，完成中文检索。"))
        self.store.put_memory(self.memory("private", scope="other/user", content="持久记忆系统"))
        self.assertEqual([item["id"] for item in self.store.search("记忆系统", scope="user/local")], ["m1"])
        self.assertEqual(len(self.store.search("记忆")), 2)
        self.assertEqual(len(self.store.search('" OR *')), 0)
        self.assertEqual(len(self.store.search("project", start_utc_ms=201)), 0)

    def test_provenance_filter_precedes_sql_pagination_and_combines_with_existing_filters(self) -> None:
        for index in range(25):
            self.store.put_memory(self.memory(f"observed-{index}", title="shared term",
                observed_start_utc_ms=1000 + index, observed_end_utc_ms=1001 + index))
        for index in range(3):
            self.store.put_memory(self.memory(f"model-{index}", title="shared term", provenance="model_inference",
                observed_start_utc_ms=100 + index * 100, observed_end_utc_ms=150 + index * 100))
        self.store.put_memory(self.memory("confirmed", title="shared term", provenance="user_confirmed"))
        self.store.put_memory(self.memory("other-scope", title="shared term", provenance="model_inference",
            scope="other", observed_start_utc_ms=500, observed_end_utc_ms=600))
        for query in ("", "shared term"):
            first = self.store.search_page(query, provenances=("model_inference",), scope="user/local", limit=2)
            self.assertEqual([row["id"] for row in first["items"]], ["model-2", "model-1"])
            second = self.store.search_page(query, provenances=("model_inference",), scope="user/local",
                                            limit=2, offset=first["next_offset"])
            self.assertEqual([row["id"] for row in second["items"]], ["model-0"])
            self.assertIsNone(second["next_offset"])
        rows = self.store.search(provenances=("model_inference",), kinds=("episodic",), scope="user/local",
                                 start_utc_ms=200, end_utc_ms=400)
        self.assertEqual([row["id"] for row in rows], ["model-2", "model-1"])
        self.assertTrue(any(row["provenance"] == "observed" for row in self.store.search(limit=100)))
        self.assertEqual([row["id"] for row in self.store.search(provenances=("user_confirmed",))], ["confirmed"])

    def test_provenance_filter_rejects_unknown_types_and_sql_injection(self) -> None:
        self.store.put_memory(self.memory())
        for values in (None, "observed", [True], ["unknown"], ["observed') OR 1=1--"],
                       ["observed"] * 4, [[]]):
            with self.subTest(values=values), self.assertRaises(ValueError):
                self.store.search(provenances=values)
        self.assertEqual(len(self.store.search()), 1)
        self.assertTrue(self.store.health()["fts_available"])

    def test_candidates_expiry_and_superseded_are_excluded(self) -> None:
        self.store.put_memory(self.memory("candidate", status="candidate"))
        self.store.put_memory(self.memory("expired", expires_at_utc_ms=1000))
        self.store.put_memory(self.memory("superseded", status="superseded"))
        self.store.put_memory(self.memory("active", expires_at_utc_ms=2000))
        self.assertEqual([item["id"] for item in self.store.search(now_utc_ms=1000)], ["active"])
        self.assertEqual({item["id"] for item in self.store.search(now_utc_ms=1000, include_candidates=True)}, {"active", "candidate"})
        self.assertIsNone(self.store.get_memory("expired", now_utc_ms=1000))
        self.assertIsNotNone(self.store.get_memory("expired", include_inactive=True))

    def test_query_budget_and_pagination_are_explicit(self) -> None:
        for index in range(5):
            self.store.put_memory(self.memory(f"m{index}", title="Title", content="0123456789"))
        first = self.store.search_page(limit=2, max_chars=100)
        self.assertEqual(len(first["items"]), 2)
        self.assertEqual(first["next_offset"], 2)
        self.assertFalse({item["id"] for item in first["items"]} & {item["id"] for item in self.store.search(offset=2, limit=2)})
        shortened = self.store.search(max_chars=7)
        self.assertEqual(sum(len(item["title"]) + len(item["content"]) for item in shortened), 7)
        self.assertTrue(shortened[0]["truncated"])
        self.assertEqual(len(self.store.get_memory(shortened[0]["id"])["content"]), 10)

    def test_user_confirmation_and_supersession_resist_background_overwrite(self) -> None:
        original = self.store.put_memory(self.memory(provenance="user_confirmed", metadata={"decision": "explicit"}))
        denied = self.store.put_memory(self.memory(content="Model guessed otherwise", provenance="model_inference"))
        self.assertEqual(denied["content"], original["content"])
        self.assertTrue(denied["protected_user_confirmed"])
        self.store.put_memory(self.memory("old", status="superseded"))
        self.assertEqual(self.store.put_memory(self.memory("old"))["status"], "superseded")

    def test_internal_source_revision_supersession_cannot_be_revived_by_plain_upsert(self) -> None:
        self.store.put_memory(self.memory("synthesis", provenance="model_inference", status="superseded",
            metadata={"derived_from_memory_id": "parent", "superseded_reason": "source_revision_changed"}))
        denied = self.store.put_memory(self.memory("synthesis", provenance="model_inference", status="active",
            content="new inference", metadata={"derived_from_memory_id": "parent", "job_id": "fabricated"}))
        self.assertEqual(denied["status"], "superseded")
        self.assertNotEqual(denied["content"], "new inference")

    def test_conflicting_claim_requires_explicit_user_resolution(self) -> None:
        old = self.store.put_memory(self.memory("old", kind="semantic", content="Database is SQLite", provenance="user_confirmed", metadata={"conflict_key": "project/database", "claim_value": "sqlite"}))
        guessed = self.store.put_memory(self.memory("guess", kind="semantic", content="Database may be PostgreSQL", provenance="model_inference", metadata={"conflict_key": "project/database", "claim_value": "postgres"}))
        self.assertEqual(guessed["status"], "candidate")
        self.assertEqual(guessed["conflict_with"], [old["id"]])
        self.assertEqual(self.store.get_memory("old")["status"], "active")
        correction = self.store.put_memory({**guessed, "status": "active", "provenance": "user_confirmed", "supersedes": ["old"]})
        self.assertEqual(correction["status"], "active")
        self.assertIsNone(self.store.get_memory("old"))
        self.assertEqual(self.store.get_memory("old", include_inactive=True)["status"], "superseded")
        self.assertEqual(self.store.put_memory({**old, "provenance": "model_inference"})["status"], "superseded")
        self.assertEqual(self.store.put_memory({**guessed, "status": "active", "provenance": "user_confirmed", "supersedes": ["old"]})["revision"], correction["revision"])

    def test_scan_keyset_pagination_retains_expired_candidates_for_maintenance(self) -> None:
        self.store.put_memory(self.memory("m1", status="candidate", expires_at_utc_ms=1))
        self.store.put_memory(self.memory("m2", expires_at_utc_ms=1))
        self.store.put_memory(self.memory("m3", status="retracted"))
        self.assertEqual([record["id"] for record in self.store.scan_memories(limit=1)], ["m1"])
        self.assertEqual([record["id"] for record in self.store.scan_memories(after_id="m1")], ["m2"])
        self.assertEqual([record["id"] for record in self.store.scan_memories(after_id="m2", include_inactive=True)], ["m3"])

    def test_forget_scrubs_revisions_fts_jobs_state_and_blocks_new_ids(self) -> None:
        self.store.put_memory(self.memory(content="secret正文"))
        self.store.put_memory(self.memory(content="secret正文 revised"))
        self.store.enqueue_job("semantic:obs1", {"source_observation_ids": ["obs1"], "text": "secret正文"})
        self.store.set_state("working-window", {"source_observation_ids": ["obs1"], "text": "secret正文"})
        self.store.set_state("checkpoint", 20)
        lease = self.store.claim_job()
        result = self.store.forget("m1")
        self.assertEqual(result["forgotten_count"], 1)
        self.assertEqual(result["cancelled_jobs"], 1)
        self.assertFalse(result["source_data_deleted"])
        self.assertIsNone(self.store.get_memory("m1"))
        self.assertEqual(self.store.revisions("m1"), [])
        self.assertEqual(self.store.search("secret"), [])
        self.assertEqual(self.store.get_job("semantic:obs1")["payload"], {})
        self.assertIsNone(self.store.get_state("working-window"))
        self.assertEqual(self.store.get_state("checkpoint"), 20)
        self.assertFalse(self.store.finish_job("semantic:obs1", lease["lease_token"], {}, [self.memory("new")]))
        self.assertTrue(self.store.put_memory(self.memory("new"))["blocked_by_tombstone"])
        self.assertFalse(self.store.enqueue_job("new:obs1", {"source_observation_ids": ["obs1"], "text": "secret正文"}))
        self.store.close()
        self.store = MemoryStore(self.path)
        self.assertTrue(self.store.put_memory(self.memory("after-restart"))["blocked_by_tombstone"])

    def test_forget_source_before_memory_exists_and_scope_isolation(self) -> None:
        self.store.forget(source_observation_ids=["obs1"], scope="user/local")
        self.assertTrue(self.store.put_memory(self.memory())["blocked_by_tombstone"])
        self.assertEqual(self.store.put_memory(self.memory("other", scope="other/user"))["status"], "active")
        self.store.put_memory(self.memory("new", source_observation_ids=["obs-new"]))
        self.store.forget(scope="user/local")
        self.assertIsNone(self.store.get_memory("new"))
        self.assertIsNotNone(self.store.get_memory("other"))
        self.assertEqual(self.store.put_memory(self.memory("future", source_observation_ids=["future-source"]))["status"], "active")

    def test_forget_retracts_existing_direct_derivatives_without_transitive_overforgetting(self) -> None:
        self.store.put_memory(self.memory("original", source_observation_ids=["a"], content="private A"))
        self.store.put_memory(self.memory("working", kind="working", source_observation_ids=["a"], content="private A"))
        self.store.put_memory(self.memory("summary", source_observation_ids=["a", "b"], content="private A plus public B"))
        self.store.put_memory(self.memory("independent-b", source_observation_ids=["b"], content="public B"))
        self.store.put_memory(self.memory("other-scope", scope="another/user", source_observation_ids=["a"], content="private A"))
        result = self.store.forget(memory_id="original")
        self.assertEqual(set(result["memory_ids"]), {"original", "working", "summary"})
        self.assertEqual([record["id"] for record in self.store.search("private", scope="user/local")], [])
        self.assertIsNotNone(self.store.get_memory("independent-b"))
        self.assertIsNotNone(self.store.get_memory("other-scope"))
        self.assertTrue(self.store.put_memory(self.memory("new-a", source_observation_ids=["a"]))["blocked_by_tombstone"])
        self.assertEqual(self.store.put_memory(self.memory("new-b", source_observation_ids=["b"]))["status"], "active")
        for identifier in result["memory_ids"]:
            self.assertEqual(self.store.revisions(identifier), [])

    def test_forget_explicit_source_does_not_suppress_other_sources_of_summary(self) -> None:
        self.store.put_memory(self.memory("summary", source_observation_ids=["a", "b"]))
        self.store.put_memory(self.memory("independent-b", source_observation_ids=["b"]))
        self.store.forget(source_observation_ids=["a"], scope="user/local")
        self.assertIsNone(self.store.get_memory("summary"))
        self.assertIsNotNone(self.store.get_memory("independent-b"))
        self.assertEqual(self.store.put_memory(self.memory("new-b", source_observation_ids=["b"]))["status"], "active")

    def test_job_deduplication_fenced_leases_and_atomic_completion(self) -> None:
        with patch("mirally.core.memory.store._now", return_value=1000):
            self.assertTrue(self.store.enqueue_job("j1", {"input": "first"}))
            self.assertFalse(self.store.enqueue_job("j1", {"input": "changed"}))
            first = self.store.claim_job(lease_ms=10)
        with patch("mirally.core.memory.store._now", return_value=1011):
            second = self.store.claim_job(lease_ms=100)
            self.assertNotEqual(first["lease_token"], second["lease_token"])
            self.assertEqual(second["attempt"], 2)
            self.assertFalse(self.store.complete_job("j1", first["lease_token"], {}))
            self.assertTrue(self.store.finish_job("j1", second["lease_token"], {"ok": True}, [self.memory()]))
            self.assertFalse(self.store.complete_job("j1", second["lease_token"], {}))
        self.assertEqual(self.store.get_job("j1")["status"], "complete")
        self.assertIsNotNone(self.store.get_memory("m1"))

    def test_completion_failure_rolls_back_all_memories_and_job(self) -> None:
        self.store.enqueue_job("j1", {})
        job = self.store.claim_job()
        with self.assertRaises(ValueError):
            self.store.finish_job("j1", job["lease_token"], {}, [self.memory(), self.memory("bad", status="invalid")])
        self.assertIsNone(self.store.get_memory("m1"))
        self.assertEqual(self.store.get_job("j1")["status"], "running")
        self.assertTrue(self.store.complete_job("j1", job["lease_token"], {}))

    def test_retries_fail_persistently_and_expired_last_attempt_is_terminal(self) -> None:
        with patch("mirally.core.memory.store._now", return_value=1000):
            self.store.enqueue_job("j1", {}, max_attempts=2)
            first = self.store.claim_job(10)
            self.assertTrue(self.store.fail_job("j1", first["lease_token"], "timeout", retry_delay_ms=5))
            self.assertIsNone(self.store.claim_job())
        self.store.close()
        self.store = MemoryStore(self.path)
        with patch("mirally.core.memory.store._now", return_value=1006):
            second = self.store.claim_job(10)
            self.assertEqual(second["attempt"], 2)
        with patch("mirally.core.memory.store._now", return_value=1017):
            self.assertIsNone(self.store.claim_job())
            self.assertEqual(self.store.get_job("j1")["status"], "failed")
            self.assertFalse(self.store.finish_job("j1", second["lease_token"], {}))

    def test_usage_reservations_enforce_atomic_hour_and_day_and_survive_restart(self) -> None:
        self.store.enqueue_job("j1", {})
        job = self.store.claim_job()
        limits = {"requests_hourly": 1, "tokens_daily": 100}
        self.assertTrue(self.store.reserve_usage("j1", job["lease_token"], {"requests": 1, "tokens": 80}, limits))
        self.assertTrue(self.store.reserve_usage("j1", job["lease_token"], {"requests": 1, "tokens": 80}, limits))
        self.assertEqual(self.store.usage_summary()["tokens"], 80)
        self.assertTrue(self.store.finish_job("j1", job["lease_token"], {}, usage={"input_tokens": 20, "output_tokens": 10}))
        self.assertEqual(self.store.usage_summary()["tokens"], 30)
        self.store.close()
        self.store = MemoryStore(self.path)
        self.assertEqual(self.store.usage_summary()["requests"], 1)
        self.store.enqueue_job("j2", {})
        second = self.store.claim_job()
        self.assertFalse(self.store.reserve_usage("j2", second["lease_token"], {"requests": 1, "tokens": 1}, limits))
        self.assertFalse(self.store.reserve_usage("j2", second["lease_token"], {"requests": 1, "tokens": 71}, {"tokens_daily": 100}))
        self.assertEqual(self.store.usage_summary()["tokens"], 30)

    def test_failed_call_with_unknown_usage_retains_reservation(self) -> None:
        self.store.enqueue_job("j1", {})
        job = self.store.claim_job()
        self.assertTrue(self.store.reserve_usage("j1", job["lease_token"], {"requests": 1, "tokens": 90}, {"tokens_daily": 100}))
        self.assertTrue(self.store.fail_job("j1", job["lease_token"], "response lost", retry_delay_ms=0))
        retried = self.store.claim_job()
        self.assertFalse(self.store.reserve_usage("j1", retried["lease_token"], {"requests": 1, "tokens": 90}, {"tokens_daily": 100}))
        self.assertEqual(self.store.usage_summary()["tokens"], 90)

    def test_budget_deferral_does_not_exhaust_model_retries(self) -> None:
        with patch("mirally.core.memory.store._now", return_value=1000):
            self.store.enqueue_job("j1", {}, max_attempts=1)
            job = self.store.claim_job()
            self.assertFalse(self.store.reserve_usage("j1", job["lease_token"], {"tokens": 20}, {"tokens_daily": 10}))
            self.assertTrue(self.store.defer_job("j1", job["lease_token"], delay_ms=10))
            self.assertEqual(self.store.get_job("j1")["attempt"], 0)
            self.assertIsNone(self.store.claim_job())
        with patch("mirally.core.memory.store._now", return_value=1011):
            reclaimed = self.store.claim_job()
            self.assertEqual(reclaimed["attempt"], 1)
            self.assertTrue(self.store.reserve_usage("j1", reclaimed["lease_token"], {"tokens": 20}, {"tokens_daily": 100}))
            self.assertFalse(self.store.defer_job("j1", reclaimed["lease_token"], delay_ms=1))

    def test_model_inferred_semantic_fact_cannot_bypass_candidate_state(self) -> None:
        stored = self.store.put_memory(self.memory(kind="semantic", provenance="model_inference", status="active"))
        self.assertEqual(stored["status"], "candidate")
        self.assertEqual(self.store.search(), [])

    def test_two_connections_cannot_claim_same_job(self) -> None:
        self.store.enqueue_job("j1", {})
        with MemoryStore(self.path) as other:
            with ThreadPoolExecutor(max_workers=2) as pool:
                leases = list(pool.map(lambda store: store.claim_job(), [self.store, other]))
            self.assertEqual(sum(lease is not None for lease in leases), 1)

    def test_oldest_jobs_precede_recent_work_and_replace_pending_revisions(self) -> None:
        self.store.enqueue_job("historical", {"memory_id": "old", "observed_end_utc_ms": 100})
        self.store.enqueue_job("recent-old-revision", {"memory_id": "current", "observed_end_utc_ms": 500})
        self.store.enqueue_job("recent-new-revision", {"memory_id": "current", "observed_end_utc_ms": 501})
        self.assertEqual(self.store.get_job("recent-old-revision")["status"], "cancelled")
        historical = self.store.claim_job()
        self.assertEqual(historical["job_id"], "historical")
        self.assertTrue(self.store.complete_job(historical["job_id"], historical["lease_token"], {}))
        recent = self.store.claim_job()
        self.assertEqual(recent["job_id"], "recent-new-revision")
        self.store.enqueue_job("recent-next-revision", {"memory_id": "current", "observed_end_utc_ms": 502})
        self.assertEqual(self.store.get_job("recent-new-revision")["status"], "running")
        self.assertTrue(self.store.complete_job(recent["job_id"], recent["lease_token"], {}))
        self.assertEqual(self.store.claim_job()["job_id"], "recent-next-revision")

    def test_completion_receipt_is_atomic_and_survives_restart(self) -> None:
        self.store.enqueue_job("j", {})
        job = self.store.claim_job()
        receipt = {"job_id": "j", "memory_ids": ["m1"], "source_observation_ids": ["obs1"]}
        with self.store.durable_transaction():
            self.assertTrue(self.store.finish_job("j", job["lease_token"], {}, [self.memory()], states={"semantic_receipt:j": receipt}))
        self.store.close()
        self.store = MemoryStore(self.path)
        self.assertEqual(self.store.get_state("semantic_receipt:j"), receipt)
        self.assertIsNotNone(self.store.get_memory("m1"))
        self.assertEqual(self.store.get_job("j")["status"], "complete")
        self.assertFalse(self.store.finish_job("j", job["lease_token"], {}, states={"late": True}))
        self.assertIsNone(self.store.get_state("late"))

    def test_completion_without_memories_can_commit_receipt(self) -> None:
        self.store.enqueue_job("empty", {})
        job = self.store.claim_job()
        self.assertTrue(self.store.finish_job("empty", job["lease_token"], {}, states={"receipt:empty": {"empty": True}}))
        self.assertEqual(self.store.get_state("receipt:empty"), {"empty": True})

    def test_protected_memory_rejection_does_not_commit_success_receipt(self) -> None:
        self.store.put_memory(self.memory(provenance="user_confirmed", content="User fact"))
        self.store.enqueue_job("j", {})
        job = self.store.claim_job()
        self.assertTrue(self.store.finish_job("j", job["lease_token"], {}, [self.memory(content="Model replacement")], states={"receipt:j": {"ok": True}}))
        self.assertEqual(self.store.get_memory("m1")["content"], "User fact")
        self.assertIsNone(self.store.get_state("receipt:j"))

    def test_invalid_completion_state_rolls_back_memories_usage_and_earlier_states(self) -> None:
        self.store.enqueue_job("j", {})
        job = self.store.claim_job()
        self.store.reserve_usage("j", job["lease_token"], {"tokens": 80}, {})
        with self.assertRaises(ValueError):
            self.store.finish_job("j", job["lease_token"], {}, [self.memory()], usage={"tokens": 10}, states={"receipt": {"ok": True}, "bad": float("nan")})
        self.assertIsNone(self.store.get_memory("m1"))
        self.assertIsNone(self.store.get_state("receipt"))
        self.assertEqual(self.store.usage_summary()["tokens"], 80)
        self.assertEqual(self.store.get_job("j")["status"], "running")

    def test_budget_deferral_targets_the_latest_exhausted_utc_boundary(self) -> None:
        hour, day = 3_600_000, 86_400_000
        with patch("mirally.core.memory.store._now", return_value=hour + 1000):
            self.store.enqueue_job("spent", {})
            spent = self.store.claim_job()
            self.store.reserve_usage("spent", spent["lease_token"], {"requests": 1, "tokens": 80}, {})
            self.store.complete_job("spent", spent["lease_token"], {})
            reservation = {"requests": 1, "tokens": 30}
            self.assertEqual(self.store.next_budget_at(reservation, {"requests_hourly": 1}), 2 * hour)
            self.assertEqual(self.store.next_budget_at(reservation, {"requests_hourly": 1, "tokens_daily": 100}), day)
            self.assertEqual(self.store.next_budget_at(reservation, {}), hour + 1000)
            with self.assertRaises(ValueError):
                self.store.next_budget_at(reservation, {"tokens_daily": 20})
            self.store.enqueue_job("waiting", {})
            job = self.store.claim_job()
            self.assertTrue(self.store.defer_job("waiting", job["lease_token"], available_at_utc_ms=day))
            self.assertEqual(self.store.get_job("waiting")["attempt"], 0)
        with patch("mirally.core.memory.store._now", return_value=day - 1):
            self.assertIsNone(self.store.claim_job())
        with patch("mirally.core.memory.store._now", return_value=day):
            job = self.store.claim_job()
            self.assertTrue(self.store.reserve_usage("waiting", job["lease_token"], reservation, {"requests_hourly": 1, "tokens_daily": 100}))

    def test_multiwindow_merge_can_refresh_only_matching_parent_revision(self) -> None:
        parent = self.store.put_memory(self.memory("parent", metadata={"input_hash": "current-hash", "source_generation": "g"}))
        previous = self.memory("synthesis_parent", status="superseded", provenance="model_inference",
            metadata={"derived_from_memory_id": "parent", "source_generation": "g", "superseded_reason": "source_revision_changed"})
        self.store.put_memory(previous)
        manifest = {"memory_id": "parent", "input_hash": "current-hash", "source_generation": "g"}
        self.store.enqueue_job("merge", {"memory_id": "unique-merge-part", "windows": [manifest]})
        job = self.store.claim_job()
        replacement = self.memory("synthesis_parent", status="active", provenance="model_inference", content="New summary",
            metadata={"derived_from_memory_id": "parent", "source_generation": "g", "job_id": "merge"})
        self.assertTrue(self.store.finish_job("merge", job["lease_token"], {}, [replacement], states={"receipt": {"ok": True}}))
        self.assertEqual(self.store.get_memory("synthesis_parent")["content"], "New summary")
        self.assertEqual(self.store.get_state("receipt"), {"ok": True})

    def test_completed_request_scrub_keeps_manifest_and_retry_evidence(self) -> None:
        payload = {"prompt": "raw captured content", "system_prompt": "instructions", "windows": [{"memory_id": "m1"}]}
        self.store.enqueue_job("j", payload)
        job = self.store.claim_job()
        self.assertFalse(self.store.scrub_completed_job("j"))
        self.assertEqual(self.store.get_job("j")["payload"]["prompt"], payload["prompt"])
        self.store.complete_job("j", job["lease_token"], {"summary": "durable result"})
        self.assertTrue(self.store.scrub_completed_job("j"))
        self.assertEqual(self.store.get_job("j")["payload"], {"windows": payload["windows"]})
        self.assertEqual(self.store.get_job("j")["result"], {"summary": "durable result"})

    def test_explicit_retry_preserves_failed_call_charges_and_other_lifecycles(self) -> None:
        self.store.enqueue_job("failed", {}, max_attempts=1)
        job = self.store.claim_job()
        self.store.reserve_usage("failed", job["lease_token"], {"requests": 1, "tokens": 80}, {})
        self.store.fail_job("failed", job["lease_token"], "bad model configuration")
        self.store.health()
        self.assertEqual(self.store.get_job("failed")["status"], "failed")
        self.store.enqueue_job("running", {})
        running = self.store.claim_job()
        self.store.enqueue_job("complete", {})
        complete = self.store.claim_job()
        self.store.complete_job("complete", complete["lease_token"], {})
        self.assertEqual(self.store.retry_failed_jobs(), 1)
        retried = self.store.get_job("failed")
        self.assertEqual(retried["status"], "pending")
        self.assertEqual(retried["attempt"], 0)
        self.assertIsNone(retried["error"])
        self.assertEqual(self.store.get_job("running")["lease_token"], running["lease_token"])
        self.assertEqual(self.store.get_job("complete")["status"], "complete")
        self.assertEqual(self.store.usage_summary()["tokens"], 80)
        self.assertEqual(self.store.usage_summary()["requests"], 1)
        reservations = self.store._connection.execute("SELECT count(*) FROM memory_reservations WHERE job_id='failed'").fetchone()[0]
        self.assertEqual(reservations, 1)
        self.assertEqual(self.store.retry_failed_jobs(), 0)

    def test_limit_change_wakes_only_budget_deferrals(self) -> None:
        for name, reason in (("budget", "budget_exhausted"), ("provider", "model timeout")):
            self.store.enqueue_job(name, {})
            job = self.store.claim_job()
            if name == "budget":
                self.store.defer_job(name, job["lease_token"], delay_ms=100000, reason=reason)
            else:
                self.store.fail_job(name, job["lease_token"], reason, retry_delay_ms=100000)
        self.assertEqual(self.store.wake_deferred_jobs(), 1)
        self.assertEqual(self.store.claim_job()["job_id"], "budget")
        self.assertEqual(self.store.get_job("provider")["status"], "pending")

    def test_provider_retry_reserves_another_request_for_its_new_lease(self) -> None:
        self.store.enqueue_job("j1", {})
        first = self.store.claim_job()
        self.assertTrue(self.store.reserve_usage("j1", first["lease_token"], {"requests": 1, "tokens": 20}, {"tokens_daily": 100}))
        self.store.fail_job("j1", first["lease_token"], "unknown usage", retry_delay_ms=0)
        self.store.close()
        self.store = MemoryStore(self.path)
        second = self.store.claim_job()
        self.assertTrue(self.store.reserve_usage("j1", second["lease_token"], {"requests": 1, "tokens": 20}, {"tokens_daily": 100}))
        self.assertEqual(self.store.usage_summary()["requests"], 2)
        self.assertEqual(self.store.usage_summary()["tokens"], 40)

    def test_request_only_usage_preserves_unreported_token_and_cost_reservations(self) -> None:
        self.store.enqueue_job("j", {})
        job = self.store.claim_job()
        self.store.reserve_usage("j", job["lease_token"], {"requests": 1, "tokens": 90, "cost_usd": 0.4}, {})
        self.store.finish_job("j", job["lease_token"], {}, usage={"requests": 1})
        self.assertEqual(self.store.usage_summary()["tokens"], 90)
        self.assertEqual(self.store.usage_summary()["cost_usd"], 0.4)
        self.store.close()
        self.store = MemoryStore(self.path)
        self.assertEqual(self.store.usage_summary()["tokens"], 90)

    def test_partial_usage_changes_only_reported_measurements(self) -> None:
        self.store.enqueue_job("j", {})
        job = self.store.claim_job()
        self.store.reserve_usage("j", job["lease_token"], {"requests": 1, "tokens": 90, "cost_usd": 0.4}, {})
        self.store.finish_job("j", job["lease_token"], {}, usage={"input_tokens": 15, "cost_usd": 0.02})
        usage = self.store.usage_summary()
        self.assertEqual(usage["requests"], 1)
        self.assertEqual(usage["tokens"], 90)
        self.assertEqual(usage["input_tokens"], 15)
        self.assertAlmostEqual(usage["cost_usd"], 0.02)

    def test_complete_token_counts_do_not_clear_unreported_cost(self) -> None:
        self.store.enqueue_job("j", {})
        job = self.store.claim_job()
        self.store.reserve_usage("j", job["lease_token"], {"requests": 1, "tokens": 90, "cost_usd": 0.4}, {})
        self.store.finish_job("j", job["lease_token"], {}, usage={"input_tokens": 15, "output_tokens": 5})
        self.assertEqual(self.store.usage_summary()["tokens"], 20)
        self.assertEqual(self.store.usage_summary()["cost_usd"], 0.4)


if __name__ == "__main__":
    unittest.main()
