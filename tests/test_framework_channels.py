"""Offline channel receipts, credentials and binding ownership with real SQLite."""
from __future__ import annotations

import concurrent.futures
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from mirally.framework import FrameworkError, FrameworkService
from mirally.framework.channels import CredentialProtector, WeixinChannels
from mirally.framework.service import SCHEMA_VERSION


class TestProtector:
    def protect(self, value):
        return "test:" + value[::-1]

    def unprotect(self, value):
        assert value.startswith("test:")
        return value[5:][::-1]


class FrameworkChannelsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mirally 微信 channel ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / "product home"
        self.now = 123456789
        self.service = FrameworkService(self.root, self.home, clock=lambda: self.now)
        self.addCleanup(lambda: self.service.close())

    def call(self, name, **params):
        return self.service.call("channel.weixin." + name, params)

    def state(self):
        return self.call("get")["state"]

    def save(self):
        return self.call("save", token="secret-wechat-token", peer_id="peer", bot_id="bot", base_url="https://ilinkai.weixin.qq.com/")["state"]

    def bound(self):
        saved = self.save()
        return self.call("bind", expected_revision=saved["revision"])["state"]

    def event(self, state=None, identity="event-one", text="实验进度如何？", **extra):
        state = state or self.state()
        return self.call("admit", installation_id=state["installation_id"], revision=state["revision"],
                         event_id=identity, text=text, **extra)["event"]

    def delivery(self, state=None, key="reply", **extra):
        state = state or self.state()
        return self.call("enqueue", installation_id=state["installation_id"], revision=state["revision"], key=key,
                         text="实验仍在运行", **extra)["delivery"]

    def restart(self):
        self.service.close()
        self.service = FrameworkService(self.root, self.home, clock=lambda: self.now)

    def test_credentials_require_local_binding_and_never_enter_public_settings(self):
        initial = self.state()
        self.assertIsNone(initial["installation_id"])
        self.assertFalse(initial["bound"])
        self.assertFalse(initial["enabled"])
        self.assertEqual(initial["revision"], 0)
        saved = self.save()
        self.assertEqual(saved["revision"], 1)
        self.assertEqual(saved["base_url"], "https://ilinkai.weixin.qq.com")
        self.assertFalse(saved["bound"])
        self.assertFalse(saved["enabled"])
        with self.assertRaises(FrameworkError):
            self.event(saved)
        with self.assertRaises(FrameworkError):
            self.call("enable", expected_revision=saved["revision"], enabled=True)
        with self.assertRaises(FrameworkError):
            self.call("bind", expected_revision=0)
        bound = self.call("bind", expected_revision=1)["state"]
        self.assertTrue(bound["enabled"])
        self.assertTrue(bound["bound"])
        self.assertEqual(bound["revision"], 2)
        self.assertTrue(self.service.call("conversation.get", {"conversation_id": bound["conversation_id"]})["title"].startswith("新对话-"))
        for method in ("settings.get", "internal.settings.get", "system.status"):
            wire = json.dumps(self.service.call(method))
            self.assertNotIn("secret-wechat-token", wire)
            self.assertNotIn("channel_weixin", wire)
        stored = self.service.db.execute("SELECT token FROM channel_weixin").fetchone()[0]
        self.assertNotIn("secret-wechat-token", stored)
        self.restart()
        self.assertEqual(self.state(), bound)

    def test_save_installs_new_identity_and_cancels_previous_unsent_replies(self):
        previous = self.bound()
        delivery = self.delivery()
        new = self.save()
        self.assertNotEqual(new["installation_id"], previous["installation_id"])
        self.assertEqual(new["revision"], previous["revision"] + 1)
        self.assertFalse(new["bound"])
        self.assertIsNone(new["conversation_id"])
        self.assertEqual(self.call("claim", delivery_id=delivery["delivery_id"])["delivery"]["status"], "cancelled")
        with self.assertRaises(FrameworkError):
            self.event(previous)

    def test_remote_control_is_opt_in_persistent_and_bound_to_installation(self):
        get = lambda: self.service.call('settings.get')['weixin_remote_control_enabled']
        update = lambda value: self.service.call('settings.update', {'patch': {'weixin_remote_control_enabled': value}})
        self.assertFalse(get())
        with self.assertRaises(FrameworkError):
            update(True)
        state = self.bound()
        with self.assertRaises(FrameworkError):
            update('true')
        update(True)
        self.service.call('settings.update', {'patch': {'weixin_auto_approve_enabled': True}})
        self.restart()
        self.assertTrue(get())
        self.assertTrue(self.service.call('settings.get')['weixin_auto_approve_enabled'])
        self.call('unbind', expected_revision=state['revision'])
        self.assertFalse(get())
        self.assertFalse(self.service.call('settings.get')['weixin_auto_approve_enabled'])
        state = self.bound()
        update(True)
        self.service.call('settings.update', {'patch': {'weixin_auto_approve_enabled': True}})
        self.save()
        self.assertFalse(get())
        self.assertFalse(self.service.call('settings.get')['weixin_auto_approve_enabled'])

    def test_assistant_mode_preserves_direct_route_and_fences_delegations(self):
        direct = self.bound()
        target = direct['conversation_id']
        assistant = self.call('mode', expected_revision=direct['revision'], mode='assistant')['state']
        source = assistant['conversation_id']
        self.assertNotEqual(source, target)
        self.assertEqual(assistant['direct_conversation_id'], target)
        self.service.call('conversation.bind', {'conversation_id':source, 'session_id':'assistant-native'})
        self.service.call('command.admit', {'conversation_id':source, 'command_id':'source-command', 'text':'继续实验'})
        args = dict(installation_id=assistant['installation_id'],revision=assistant['revision'],source_conversation_id=source,
                    source_command_id='source-command',target_conversation_id=target,target_generation=1,
                    target_command_id='target-command',content_hash='a'*64)
        with self.assertRaises(FrameworkError):
            self.call('delegate', **args)
        self.service.call('settings.update', {'patch':{'weixin_remote_control_enabled':True}})
        self.call('delegate', **args)
        check = {key:value for key,value in args.items() if key not in ('target_generation','content_hash')}
        self.call('delegation_check', **check)
        with self.assertRaises(FrameworkError):
            self.call('delegate', **{**args,'content_hash':'b'*64})
        self.service.call('settings.update', {'patch':{'weixin_remote_control_enabled':False}})
        with self.assertRaises(FrameworkError):
            self.call('delegation_check', **check)
        restored = self.call('mode', expected_revision=assistant['revision'], mode='direct')['state']
        self.assertEqual(restored['conversation_id'],target)
        self.assertEqual(restored['assistant_conversation_id'],source)
        with self.assertRaises(FrameworkError):
            self.call('delegation_check', **check)

    def test_schema_nine_adds_delegation_storage_without_replacing_binding(self):
        state = self.bound()
        self.service.db.execute('DROP TABLE channel_weixin_delegations')
        self.service.db.execute('PRAGMA user_version=9')
        self.restart()
        self.assertEqual(self.state()['installation_id'], state['installation_id'])
        self.assertEqual(self.state()['conversation_id'], state['conversation_id'])
        self.assertEqual(self.service.db.execute('PRAGMA user_version').fetchone()[0], SCHEMA_VERSION)
        self.assertIsNotNone(self.service.db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='channel_weixin_delegations'").fetchone())

    def test_history_evidence_only_joins_real_matching_weixin_event_and_command(self):
        state = self.bound()
        cid = state["conversation_id"]
        self.service.call("conversation.bind", {"conversation_id": cid, "session_id": "native"})
        receipt = self.event(state)
        command = receipt["command_id"]
        self.service.call("command.admit", {"conversation_id": cid, "command_id": command, "text": "实验进度如何？"})
        self.service.call("command.ack", {"conversation_id": cid, "command_id": command, "state": "applied", "runtime_request_id": "native-message"})
        evidence = self.service.call("internal.conversation.weixin_receipts", {"conversation_id": cid})["receipts"]
        self.assertEqual(len(evidence), 1)
        self.assertEqual(evidence[0]["runtime_request_id"], "native-message")
        self.assertEqual(evidence[0]["session_id"], "native")
        self.assertEqual(set(evidence[0]), {"command_id", "runtime_request_id", "session_id", "content_hash"})
        self.service.db.execute("UPDATE channel_weixin_events SET content_hash='not-matching'")
        self.assertEqual(self.service.call("internal.conversation.weixin_receipts", {"conversation_id": cid})["receipts"], [])

    def test_peer_confirmation_routes_only_to_existing_unarchived_or_new_conversation(self):
        saved = self.save()
        with self.assertRaises(FrameworkError):
            self.call("bind", expected_revision=saved["revision"], conversation_id="nonexistent")
        self.assertEqual(self.state(), saved)
        self.service.call("conversation.ensure", {"conversation_id": "existing"})
        self.service.call("conversation.patch", {"conversation_id": "existing", "expected_version": 1, "patch": {"archived": True}})
        with self.assertRaises(FrameworkError):
            self.call("bind", expected_revision=saved["revision"], conversation_id="existing")
        self.service.call("conversation.patch", {"conversation_id": "existing", "expected_version": 2, "patch": {"archived": False}})
        linked = self.call("bind", expected_revision=saved["revision"], conversation_id="existing")["state"]
        first = self.event(linked)
        old_delivery = self.delivery(linked)
        switched = self.call("select", expected_revision=linked["revision"])["state"]
        self.assertNotEqual(switched["conversation_id"], "existing")
        self.assertEqual(first["conversation_id"], "existing")
        self.assertEqual(self.call("claim", delivery_id=old_delivery["delivery_id"])["delivery"]["status"], "cancelled")
        with self.assertRaises(FrameworkError):
            self.event(linked, "stale")

    def test_admission_receipts_deduplicate_pin_route_and_reject_content_changes(self):
        bound = self.bound()
        event = self.event(context_token="sensitive-context", created_at=123)
        self.assertEqual(event["created_at"], 123)
        self.assertEqual(event["state"], "queued")
        self.assertEqual(event["context_token"], "sensitive-context")
        self.assertFalse(event["duplicate"])
        duplicate = self.event(context_token="new-context")
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(duplicate["command_id"], event["command_id"])
        self.assertEqual(duplicate["context_token"], "sensitive-context")
        with self.assertRaises(FrameworkError):
            self.event(text="same identity, changed text")
        self.call("cursor", installation_id=bound["installation_id"], revision=bound["revision"], cursor="next-batch")
        switched = self.call("select", expected_revision=bound["revision"])["state"]
        again = self.event(switched)
        self.assertTrue(again["duplicate"])
        self.assertEqual(again["conversation_id"], bound["conversation_id"])
        self.assertEqual(self.state()["cursor"], "next-batch")
        raw = self.service.db.execute("SELECT context_token FROM channel_weixin_events").fetchone()[0]
        self.assertNotIn("sensitive-context", raw)
        self.assertEqual(self.service.call("command.list", {"conversation_id": bound["conversation_id"]})["commands"], [])

    def test_restart_never_replays_inflight_commands_or_uncertain_delivery(self):
        bound = self.bound()
        self.event(identity="still-queued")
        inflight = self.event(identity="inflight")
        self.call("event", installation_id=bound["installation_id"], event_id=inflight["event_id"], state="submitted")
        pending = self.delivery(key="pending")
        sending = self.delivery(key="sending")
        claimed = self.call("claim", delivery_id=sending["delivery_id"])["delivery"]
        self.assertEqual(claimed["state"]["token"], "secret-wechat-token")
        self.assertEqual(claimed["status"], "sending")
        self.assertNotIn("state", self.call("claim", delivery_id=sending["delivery_id"])["delivery"])
        self.restart()
        events = self.call("events", installation_id=bound["installation_id"])["events"]
        self.assertEqual({item["event_id"]: item["state"] for item in events}, {"still-queued": "queued", "inflight": "unknown"})
        self.assertEqual(self.call("claim", delivery_id=sending["delivery_id"])["delivery"]["status"], "unknown")
        self.assertEqual([item["delivery_id"] for item in self.call("outbox", installation_id=bound["installation_id"])["deliveries"]], [pending["delivery_id"]])
        self.assertEqual(self.state()["outbox_counts"], {"pending": 1, "unknown": 1, "failed": 0})

    def test_terminal_event_and_outbox_receipts_are_immutable_and_idempotent(self):
        bound = self.bound()
        event = self.event()
        params = dict(installation_id=bound["installation_id"], event_id=event["event_id"], state="completed", result_ref={"session_id": "native", "message_id": "m"})
        completed = self.call("event", **params)
        self.assertEqual(self.call("event", **params), completed)
        for replacement in (dict(state="queued"), dict(result_ref="other"), dict(state="rejected")):
            with self.assertRaises(FrameworkError):
                self.call("event", **{**params, **replacement})
        delivery = self.delivery(command_id=event["command_id"], conversation_id=event["conversation_id"])
        self.assertEqual(self.delivery(command_id=event["command_id"], conversation_id=event["conversation_id"]), delivery)
        with self.assertRaises(FrameworkError):
            self.call("enqueue", installation_id=bound["installation_id"], revision=bound["revision"], key="reply", text="different")
        with self.assertRaises(FrameworkError):
            self.call("ack", delivery_id=delivery["delivery_id"], status="sent")
        self.call("claim", delivery_id=delivery["delivery_id"])
        sent = self.call("ack", delivery_id=delivery["delivery_id"], status="sent", receipt={"message_id": "remote-id"})
        self.assertEqual(self.call("ack", delivery_id=delivery["delivery_id"], status="sent", receipt={"message_id": "remote-id"}), sent)
        with self.assertRaises(FrameworkError):
            self.call("ack", delivery_id=delivery["delivery_id"], status="failed")
        self.assertEqual(self.call("outbox", installation_id=bound["installation_id"])["deliveries"], [])

    def test_disconnect_and_unbind_revoke_new_input_preserve_conversations_and_known_receipts(self):
        bound = self.bound()
        event = self.event(context_token="context")
        delivery = self.delivery()
        disconnected = self.call("enable", expected_revision=bound["revision"], enabled=False)["state"]
        self.assertTrue(disconnected["bound"])
        self.assertFalse(disconnected["enabled"])
        with self.assertRaises(FrameworkError):
            self.event(disconnected)
        with self.assertRaises(FrameworkError):
            self.call("select", expected_revision=disconnected["revision"])
        restored = self.call("enable", expected_revision=disconnected["revision"], enabled=True)["state"]
        self.assertEqual(restored["conversation_id"], bound["conversation_id"])
        unbound = self.call("unbind", expected_revision=restored["revision"])["state"]
        for field in ("token", "peer_id", "bot_id", "conversation_id", "cursor", "context_token", "installation_id"):
            self.assertIsNone(unbound[field])
        self.assertEqual(unbound["revision"], restored["revision"] + 1)
        self.assertEqual(self.call("claim", delivery_id=delivery["delivery_id"])["delivery"]["status"], "cancelled")
        self.assertEqual(self.call("events", installation_id=bound["installation_id"])["events"][0]["event_id"], event["event_id"])
        self.assertIsNone(self.call("events", installation_id=bound["installation_id"])["events"][0]["context_token"])
        self.assertIsNotNone(self.service.call("conversation.get", {"conversation_id": bound["conversation_id"]}))

    def test_atomic_claim_has_one_winner_and_compare_revision_is_transactional(self):
        self.bound()
        delivery = self.delivery()
        def claim(_):
            return self.call("claim", delivery_id=delivery["delivery_id"])["delivery"]
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(claim, range(4)))
        self.assertEqual(sum("state" in item for item in results), 1)
        bound = self.state()
        def switch(_):
            try:
                return self.call("select", expected_revision=bound["revision"])
            except FrameworkError as error:
                return error.code
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(switch, range(4)))
        self.assertEqual(results.count("CONFLICT"), 3)

    def test_archive_after_binding_blocks_new_admission_and_enable(self):
        bound = self.bound()
        self.service.call("conversation.patch", {"conversation_id": bound["conversation_id"], "expected_version": 1, "patch": {"archived": True}})
        with self.assertRaises(FrameworkError):
            self.event()
        disconnected = self.call("enable", expected_revision=bound["revision"], enabled=False)["state"]
        with self.assertRaises(FrameworkError):
            self.call("enable", expected_revision=disconnected["revision"], enabled=True)

    def test_runtime_validation_bounds_and_bounded_pending_queues(self):
        bound = self.bound()
        for values in ({"text": "x" * 100001}, {"identity": "x" * 201}, {"created_at": True}, {"context_token": "x" * 16001}):
            with self.subTest(values=list(values)), self.assertRaises(FrameworkError):
                self.event(**values)
        for method, params in (("select", {"expected_revision": True}), ("enable", {"expected_revision": bound["revision"], "enabled": 1}),
                               ("events", {"installation_id": bound["installation_id"], "states": ["invalid"]}),
                               ("outbox", {"installation_id": bound["installation_id"], "limit": False})):
            with self.subTest(method=method), self.assertRaises(FrameworkError):
                self.call(method, **params)
        self.service.channels.MAX_PENDING = 1
        self.event()
        with self.assertRaises(FrameworkError):
            self.event(identity="second")
        self.delivery()
        with self.assertRaises(FrameworkError):
            self.delivery(key="second")
        # A legal large response is validated independently from small receipt JSON.
        self.service.channels.MAX_PENDING = 2
        large = self.call("enqueue", installation_id=bound["installation_id"], revision=bound["revision"], key="large", text="中" * 100000)
        self.assertEqual(len(large["delivery"]["text"]), 100000)

    def test_invalid_credentials_and_service_url_do_not_replace_existing_binding(self):
        saved = self.bound()
        for replacement in ({"token": ""}, {"peer_id": 12}, {"bot_id": "x" * 513}, {"base_url": "http://example.com"},
                            {"base_url": "https://user:password@example.com"}, {"base_url": "https://example.com/?token=secret"}):
            params = {"token": "secret-wechat-token", "peer_id": "peer", "bot_id": "bot", "base_url": "https://ilinkai.weixin.qq.com"}
            with self.subTest(replacement=replacement), self.assertRaises(FrameworkError):
                self.call("save", **{**params, **replacement})
            self.assertEqual(self.state(), saved)

    def test_loopback_origin_requires_explicit_test_mode_and_exact_fixture_origin(self):
        params = {"token": "fixture", "peer_id": "peer", "bot_id": "bot", "base_url": "http://127.0.0.1:45678"}
        for env in ({}, {"MIRALLY_TEST_MODE": "1"}, {"MIRALLY_TEST_WEIXIN_URL": params["base_url"]},
                    {"MIRALLY_TEST_MODE": "1", "MIRALLY_TEST_WEIXIN_URL": "http://127.0.0.1:45679"}):
            with self.subTest(env=env), patch.dict(os.environ, env, clear=True), self.assertRaises(FrameworkError):
                self.call("save", **params)
        with patch.dict(os.environ, {"MIRALLY_TEST_MODE": "1", "MIRALLY_TEST_WEIXIN_URL": params["base_url"]}):
            self.assertEqual(self.call("save", **params)["state"]["base_url"], params["base_url"])
            for other in ("http://127.1:45678", "http://localhost:45678", "http://127.0.0.1:65536", "http://example.com:45678"):
                with self.subTest(origin=other), patch.dict(os.environ, {"MIRALLY_TEST_WEIXIN_URL": other}), self.assertRaises(FrameworkError):
                    self.call("save", **{**params, "base_url": other})

    def test_outbox_keeps_insertion_order_even_at_the_same_timestamp(self):
        bound = self.bound()
        expected = [self.delivery(key="reply-" + str(index))["delivery_id"] for index in range(8)]
        actual = self.call("outbox", installation_id=bound["installation_id"])["deliveries"]
        self.assertEqual([item["delivery_id"] for item in actual], expected)

    def test_schema_three_upgrade_preserves_settings_and_future_schema_is_rejected(self):
        self.service.call("settings.update", {"patch": {"do_not_disturb": True}})
        self.service.db.execute("DROP TABLE channel_weixin_events")
        self.service.db.execute("DROP TABLE channel_weixin_outbox")
        self.service.db.execute("DROP TABLE channel_weixin")
        self.service.db.execute("ALTER TABLE conversations DROP COLUMN title_mode")
        self.service.db.execute("PRAGMA user_version=3")
        self.restart()
        self.assertEqual(self.service.db.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)
        self.assertTrue(self.service.call("settings.get")["do_not_disturb"])
        self.assertIsNone(self.state()["installation_id"])
        self.service.db.execute("PRAGMA user_version=99")
        self.service.close()
        with self.assertRaises(FrameworkError):
            FrameworkService(self.root, self.home)
        # Reopen for the regular cleanup.
        connection = sqlite3.connect(self.service.path)
        connection.execute("PRAGMA user_version=6")
        connection.close()
        self.service = FrameworkService(self.root, self.home)

    def test_injectable_protection_and_platform_default_roundtrip(self):
        injected = WeixinChannels(self.service, protector=TestProtector())
        saved = injected.save("known-secret", "peer", "bot", "https://ilinkai.weixin.qq.com")["state"]
        self.assertEqual(saved["token"], "known-secret")
        raw = self.service.db.execute("SELECT token FROM channel_weixin").fetchone()[0]
        self.assertEqual(raw, "test:terces-nwonk")
        protector = CredentialProtector()
        self.assertEqual(protector.unprotect(protector.protect("微信 credential")), "微信 credential")
        # Restore standard protection so normal store operations still read this DB.
        injected.unbind(saved["revision"])
        if os.name != "nt":
            self.assertEqual(self.service.path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(self.service.path.parent.stat().st_mode & 0o777, 0o700)

    def test_unreadable_credentials_do_not_block_startup_or_destroy_binding_metadata(self):
        bound = self.bound()
        self.event(context_token="valid-context")
        self.service.db.execute("UPDATE channel_weixin SET token='dpapi:invalid@@' WHERE singleton=1")
        self.restart()
        state = self.state()
        self.assertTrue(state["credential_error"])
        self.assertIsNone(state["token"])
        self.assertEqual(state["context_token"], "valid-context")
        self.assertEqual(state["conversation_id"], bound["conversation_id"])
        self.assertEqual(state["revision"], bound["revision"])
        self.assertEqual(state["peer_id"], bound["peer_id"])
        self.assertTrue(state["enabled"])
        self.assertTrue(state["bound"])
        self.assertEqual(self.service.db.execute("SELECT token FROM channel_weixin").fetchone()[0], "dpapi:invalid@@")
        self.assertNotIn("secret-wechat-token", json.dumps(state))
        self.assertTrue(self.service.call("system.status")["ready"])
        for method, params in (("bind", {}), ("enable", {"enabled": True}), ("enable", {"enabled": False})):
            with self.subTest(method=method, params=params), self.assertRaises(FrameworkError):
                self.call(method, expected_revision=state["revision"], **params)
        cleared = self.call("unbind", expected_revision=state["revision"])["state"]
        self.assertNotIn("credential_error", cleared)
        self.assertIsNone(cleared["token"])
        self.assertFalse(cleared["bound"])
        self.assertEqual(self.save()["token"], "secret-wechat-token")

    def test_unreadable_context_is_independent_and_rescan_restores_readable_state(self):
        bound = self.bound()
        self.service.db.execute("UPDATE channel_weixin SET context_token='dpapi:invalid@@' WHERE singleton=1")
        self.restart()
        state = self.state()
        self.assertTrue(state["credential_error"])
        self.assertEqual(state["token"], "secret-wechat-token")
        self.assertIsNone(state["context_token"])
        self.assertEqual(state["conversation_id"], bound["conversation_id"])
        self.assertEqual(self.service.db.execute("SELECT context_token FROM channel_weixin").fetchone()[0], "dpapi:invalid@@")
        self.service.db.execute("UPDATE channel_weixin SET token='dpapi:also-invalid@@' WHERE singleton=1")
        both = self.state()
        self.assertTrue(both["credential_error"])
        self.assertIsNone(both["token"])
        self.assertIsNone(both["context_token"])
        rescanned = self.save()
        self.assertNotIn("credential_error", rescanned)
        self.assertIsNone(rescanned["context_token"])
        self.assertEqual(rescanned["token"], "secret-wechat-token")
        self.assertFalse(rescanned["bound"])
        confirmed = self.call("bind", expected_revision=rescanned["revision"])["state"]
        self.assertTrue(confirmed["enabled"])


if __name__ == "__main__":
    unittest.main()
