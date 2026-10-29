"""Offline product invariants, with real SQLite and existing read-only owners."""

from __future__ import annotations

import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from mirally.framework import FrameworkError, FrameworkService


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
        self.assertEqual(self.service.db.execute("PRAGMA user_version").fetchone()[0], 9)

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
