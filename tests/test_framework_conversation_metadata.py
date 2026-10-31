"""Conversation organization uses its real SQLite owner, not UI-only state."""
from pathlib import Path
import concurrent.futures
import tempfile
import unittest
from datetime import datetime

from mirally.framework import FrameworkError, FrameworkService


class ConversationMetadataTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mirally 会话 metadata ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / "product home"
        self.now = 1000
        self.service = FrameworkService(self.root, self.home, clock=lambda: self.now)
        self.addCleanup(lambda: self.service.close())

    def create(self, identity="one"):
        self.service.call("conversation.ensure", {"conversation_id": identity, "title": "原来的对话"})
        return self.service.call("conversation.bind", {"conversation_id": identity, "session_id": "native-" + identity})

    def patch(self, patch, version=1, identity="one"):
        return self.service.call("conversation.patch", {"conversation_id": identity, "expected_version": version, "patch": patch})

    def test_conversation_organization_does_not_require_work_tables(self):
        tables = {row[0] for row in self.service.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertNotIn('works', tables)
        self.assertNotIn('work_runs', tables)
        self.create()
        self.assertEqual(self.patch({'title': '新的标题'})['title'], '新的标题')

    def test_internal_activity_batch_is_bounded_and_returns_only_current_owner_metadata(self):
        self.create("one")
        self.create("two")
        self.patch({"archived": True}, identity="two")
        self.service.call("conversation.rotate", {"conversation_id": "one", "reason": "test", "session_id": "new-native"})
        result = self.service.call("internal.conversation.activity_batch", {"conversation_ids": ["one", "two", "missing", "one"]})
        by_id = {item["conversation_id"]: item for item in result["conversations"]}
        self.assertEqual(set(by_id), {"one", "two"})
        self.assertEqual(by_id["one"], {"conversation_id": "one", "session_id": "new-native", "frozen": False, "archived": False})
        self.assertTrue(by_id["two"]["archived"])
        self.assertEqual(self.service.call("internal.conversation.activity_batch", {"conversation_ids": []}), {"conversations": []})
        with self.assertRaises(FrameworkError):
            self.service.call("internal.conversation.activity_batch", {"conversation_ids": ["one"] * 201})
        with self.assertRaises(FrameworkError):
            self.service.call("internal.conversation.activity_batch", {"conversation_ids": [None]})

    def test_title_pin_archive_restore_survive_restart_without_changing_native_ownership(self):
        original = self.create()
        self.now += 100
        changed = self.patch({"title": "计划与生活", "pinned": True, "archived": True})
        self.assertEqual(changed["metadata_version"], 2)
        self.assertEqual(changed["session_id"], original["session_id"])
        self.assertEqual(changed["generation"], original["generation"])
        self.service.close()
        self.service = FrameworkService(self.root, self.home, clock=lambda: self.now)
        persisted = self.service.call("conversation.get", {"conversation_id": "one"})
        self.assertEqual(persisted, changed)
        restored = self.patch({"archived": False}, 2)
        self.assertFalse(restored["archived"])
        self.assertTrue(restored["pinned"])
        self.assertIsNone(restored["archived_at"])
        self.assertEqual(restored["title"], "计划与生活")
        self.assertEqual(self.service.call("conversation.history_refs", {"conversation_id": "one"})["generations"][0]["session_id"], "native-one")

    def test_concurrent_metadata_writes_compare_the_observed_version(self):
        self.create()
        def edit(title):
            service = FrameworkService(self.root, self.home)
            try:
                return service.call("conversation.patch", {"conversation_id": "one", "expected_version": 1, "patch": {"title": title}})
            except FrameworkError as error:
                return error.code
            finally:
                service.close()
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(edit, ("first", "second")))
        self.assertEqual(sum(isinstance(item, dict) for item in outcomes), 1)
        self.assertEqual(outcomes.count("CONFLICT"), 1)
        current = self.service.call("conversation.get", {"conversation_id": "one"})
        self.assertEqual(current["metadata_version"], 2)

    def test_archive_blocks_new_commands_and_actions_until_explicit_restore(self):
        self.create()
        self.patch({"archived": True})
        with self.assertRaisesRegex(FrameworkError, "restore"):
            self.service.call("command.admit", {"conversation_id": "one", "command_id": "new", "text": "误发"})
        guard = self.service.call("guard.check", {"conversation_id": "one"})
        self.assertFalse(guard["allow"])
        self.assertIn("conversation_archived", guard["reasons"])
        self.patch({"archived": False}, 2)
        admitted = self.service.call("command.admit", {"conversation_id": "one", "command_id": "new", "text": "恢复后的需求"})
        self.assertTrue(admitted["should_dispatch"])
        with self.assertRaisesRegex(FrameworkError, "stop the active"):
            self.patch({"archived": True}, 3)
        self.assertFalse(self.service.call("conversation.get", {"conversation_id": "one"})["archived"])

    def test_filters_pins_pagination_and_point_reads_include_old_conversations(self):
        for number in range(205):
            self.now += 1
            self.create(str(number))
        self.patch({"pinned": True}, identity="0")
        self.patch({"archived": True}, identity="1")
        active = self.service.call("conversation.list", {"archived_filter": "active", "limit": 1000})["conversations"]
        self.assertEqual(active[0]["conversation_id"], "0")
        self.assertEqual(len(active), 204)
        self.assertEqual(self.service.call("conversation.get", {"conversation_id": "2"})["session_id"], "native-2")
        archived = self.service.call("conversation.list", {"archived_filter": "archived"})["conversations"]
        self.assertEqual([item["conversation_id"] for item in archived], ["1"])
        self.assertEqual(len(self.service.call("conversation.list", {"offset": 200})["conversations"]), 5)

    def test_recency_uses_conversation_activity_within_pinned_groups(self):
        self.create('first'); self.now += 10
        self.create('second'); self.now += 10
        self.create('third'); self.now += 10
        self.service.call('command.admit', {'conversation_id':'first','command_id':'first-msg','text':'hello'})
        self.now += 10
        self.patch({'title':'new title'}, identity='second')
        self.assertEqual([c['conversation_id'] for c in self.service.conversation_list('active')['conversations']], ['first','third','second'])
        self.now += 10
        self.patch({'pinned':True}, identity='first')
        self.now += 10
        self.patch({'pinned':True}, version=2, identity='second')
        self.assertEqual([c['conversation_id'] for c in self.service.conversation_list('active')['conversations']], ['first','second','third'])

    def test_metadata_rejects_unknown_fields_types_stale_versions_and_keeps_noops_stable(self):
        self.create()
        for patch in ({}, {"permissionMode": "full"}, {"archived": 1}, {"pinned": "yes"}, {"title": " "}, {"title": "x" * 513}):
            with self.subTest(patch=patch), self.assertRaises(FrameworkError):
                self.patch(patch)
        with self.assertRaises(FrameworkError):
            self.patch({"title": "new"}, True)
        self.assertEqual(self.patch({"title": "原来的对话"})["metadata_version"], 1)
        self.patch({"title": "new"})
        with self.assertRaisesRegex(FrameworkError, "refresh"):
            self.patch({"pinned": True})

    def test_schema_two_upgrade_preserves_commands_title_and_references(self):
        self.create()
        self.service.call("command.admit", {"conversation_id": "one", "command_id": "before", "text": "原始消息"})
        self.service.call("command.ack", {"conversation_id": "one", "command_id": "before", "state": "completed"})
        for column in ("pinned", "archived", "metadata_version", "metadata_updated_at", "pinned_at", "archived_at", "title_mode"):
            self.service.db.execute("ALTER TABLE conversations DROP COLUMN " + column)
        self.service.db.execute("PRAGMA user_version=2")
        self.service.close()
        self.service = FrameworkService(self.root, self.home)
        current = self.service.call("conversation.get", {"conversation_id": "one"})
        self.assertEqual(current["title"], "原来的对话")
        self.assertFalse(current["pinned"])
        self.assertFalse(current["archived"])
        self.assertEqual(current["metadata_version"], 1)
        self.assertEqual(self.service.call("command.list", {"conversation_id": "one"})["commands"][0]["command_id"], "before")

    def test_automatic_title_requires_first_durable_input_and_survives_restart(self):
        created = self.service.call("conversation.ensure", {"conversation_id": "auto"})
        self.assertEqual(created["title"], "新对话-" + datetime.fromtimestamp(self.now / 1000).strftime("%Y-%m-%d"))
        self.service.call("conversation.bind", {"conversation_id": "auto", "session_id": "native-auto"})
        self.service.call("command.admit", {"conversation_id": "auto", "command_id": "first", "text": "第一行\n第二行"})
        params = {"conversation_id": "auto", "command_id": "first", "text": "第一行\n第二行"}
        self.assertFalse(self.service.call("internal.conversation.autotitle", params)["changed"])
        self.service.call("command.ack", {"conversation_id": "auto", "command_id": "first", "state": "applied", "runtime_request_id": "message"})
        named = self.service.call("internal.conversation.autotitle", params)
        self.assertTrue(named["changed"])
        self.assertEqual(named["conversation"]["title"], "第一行 第二行")
        self.assertFalse(self.service.call("internal.conversation.autotitle", params)["changed"])
        self.service.close()
        self.service = FrameworkService(self.root, self.home, clock=lambda: self.now)
        self.assertEqual(self.service.call("conversation.get", {"conversation_id": "auto"})["title"], "第一行 第二行")

    def test_manual_or_explicit_name_wins_even_if_equal_to_placeholder(self):
        created = self.service.call("conversation.ensure", {"conversation_id": "auto"})
        self.service.call("conversation.bind", {"conversation_id": "auto", "session_id": "native-auto"})
        self.service.call("command.admit", {"conversation_id": "auto", "command_id": "first", "text": "内容"})
        self.service.call("command.ack", {"conversation_id": "auto", "command_id": "first", "state": "applied", "runtime_request_id": "message"})
        self.patch({"title": created["title"]}, identity="auto")
        self.assertFalse(self.service.call("internal.conversation.autotitle", {"conversation_id": "auto", "command_id": "first", "text": "内容"})["changed"])
        self.assertEqual(self.service.call("conversation.get", {"conversation_id": "auto"})["title"], created["title"])

    def test_schema_five_existing_title_is_never_reinterpreted_as_automatic(self):
        self.create()
        self.service.db.execute("ALTER TABLE conversations DROP COLUMN title_mode")
        self.service.db.execute("PRAGMA user_version=5")
        self.service.close()
        self.service = FrameworkService(self.root, self.home, clock=lambda: self.now)
        self.service.call("command.admit", {"conversation_id": "one", "command_id": "old", "text": "内容"})
        self.service.call("command.ack", {"conversation_id": "one", "command_id": "old", "state": "applied", "runtime_request_id": "message"})
        result = self.service.call("internal.conversation.autotitle", {"conversation_id": "one", "command_id": "old", "text": "内容"})
        self.assertFalse(result["changed"])
        self.assertEqual(result["conversation"]["title"], "原来的对话")

    def test_manual_rename_and_auto_title_race_preserves_manual_intent(self):
        self.service.call("conversation.ensure", {"conversation_id": "race"})
        self.service.call("conversation.bind", {"conversation_id": "race", "session_id": "native-race"})
        self.service.call("command.admit", {"conversation_id": "race", "command_id": "first", "text": "输入"})
        self.service.call("command.ack", {"conversation_id": "race", "command_id": "first", "state": "applied", "runtime_request_id": "message"})
        def automatic():
            return self.service.call("internal.conversation.autotitle", {"conversation_id": "race", "command_id": "first", "text": "输入"})
        def manual():
            while True:
                version = self.service.call("conversation.get", {"conversation_id": "race"})["metadata_version"]
                try:
                    return self.patch({"title": "用户名称"}, version=version, identity="race")
                except FrameworkError as error:
                    if error.code != "CONFLICT": raise
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda task: task(), (automatic, manual)))
        self.assertEqual(self.service.call("conversation.get", {"conversation_id": "race"})["title"], "用户名称")

    def test_failed_admission_does_not_block_first_recorded_message(self):
        self.service.call("conversation.ensure", {"conversation_id": "auto"})
        self.service.call("conversation.bind", {"conversation_id": "auto", "session_id": "native-auto"})
        self.service.call("command.admit", {"conversation_id": "auto", "command_id": "failed", "text": "失败"})
        self.service.call("command.ack", {"conversation_id": "auto", "command_id": "failed", "state": "rejected"})
        self.service.call("command.admit", {"conversation_id": "auto", "command_id": "first", "text": "成功"})
        self.service.call("command.ack", {"conversation_id": "auto", "command_id": "first", "state": "applied", "runtime_request_id": "message"})
        result = self.service.call("internal.conversation.autotitle", {"conversation_id": "auto", "command_id": "first", "text": "成功"})
        self.assertTrue(result["changed"])
        self.assertEqual(result["conversation"]["title"], "成功")

    def test_uncertain_earlier_admission_blocks_later_title(self):
        self.service.call("conversation.ensure", {"conversation_id": "auto"})
        self.service.call("conversation.bind", {"conversation_id": "auto", "session_id": "native-auto"})
        self.service.call("command.admit", {"conversation_id": "auto", "command_id": "unknown", "text": "未知"})
        self.service.call("command.ack", {"conversation_id": "auto", "command_id": "unknown", "state": "unknown"})
        self.service.call("command.admit", {"conversation_id": "auto", "command_id": "later", "text": "后续"})
        self.service.call("command.ack", {"conversation_id": "auto", "command_id": "later", "state": "applied", "runtime_request_id": "later-message"})
        result = self.service.call("internal.conversation.autotitle", {"conversation_id": "auto", "command_id": "later", "text": "后续"})
        self.assertFalse(result["changed"])


if __name__ == "__main__":
    unittest.main()
