"""Persistent-memory correctness independent of models and desktop capture."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from openassist.core.memory import MemoryStore


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
        with patch("openassist.core.memory.store._now", return_value=1000):
            self.assertTrue(self.store.enqueue_job("j1", {"input": "first"}))
            self.assertFalse(self.store.enqueue_job("j1", {"input": "changed"}))
            first = self.store.claim_job(lease_ms=10)
        with patch("openassist.core.memory.store._now", return_value=1011):
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
        with patch("openassist.core.memory.store._now", return_value=1000):
            self.store.enqueue_job("j1", {}, max_attempts=2)
            first = self.store.claim_job(10)
            self.assertTrue(self.store.fail_job("j1", first["lease_token"], "timeout", retry_delay_ms=5))
            self.assertIsNone(self.store.claim_job())
        self.store.close()
        self.store = MemoryStore(self.path)
        with patch("openassist.core.memory.store._now", return_value=1006):
            second = self.store.claim_job(10)
            self.assertEqual(second["attempt"], 2)
        with patch("openassist.core.memory.store._now", return_value=1017):
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
        with patch("openassist.core.memory.store._now", return_value=1000):
            self.store.enqueue_job("j1", {}, max_attempts=1)
            job = self.store.claim_job()
            self.assertFalse(self.store.reserve_usage("j1", job["lease_token"], {"tokens": 20}, {"tokens_daily": 10}))
            self.assertTrue(self.store.defer_job("j1", job["lease_token"], delay_ms=10))
            self.assertEqual(self.store.get_job("j1")["attempt"], 0)
            self.assertIsNone(self.store.claim_job())
        with patch("openassist.core.memory.store._now", return_value=1011):
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

    def test_recent_jobs_precede_historical_backlog_and_replace_pending_revisions(self) -> None:
        self.store.enqueue_job("historical", {"memory_id": "old", "observed_end_utc_ms": 100})
        self.store.enqueue_job("recent-old-revision", {"memory_id": "current", "observed_end_utc_ms": 500})
        self.store.enqueue_job("recent-new-revision", {"memory_id": "current", "observed_end_utc_ms": 501})
        self.assertEqual(self.store.get_job("recent-old-revision")["status"], "cancelled")
        recent = self.store.claim_job()
        self.assertEqual(recent["job_id"], "recent-new-revision")
        self.store.enqueue_job("recent-next-revision", {"memory_id": "current", "observed_end_utc_ms": 502})
        self.assertEqual(self.store.get_job("recent-new-revision")["status"], "running")
        self.assertTrue(self.store.complete_job(recent["job_id"], recent["lease_token"], {}))
        self.assertEqual(self.store.claim_job()["job_id"], "recent-next-revision")

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
