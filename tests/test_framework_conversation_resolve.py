"""Phone route resolution uses one bounded SQLite query, independent of list order."""
from pathlib import Path
import tempfile
import unittest

from mirally.framework import FrameworkError, FrameworkService


class ConversationResolveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mirally 会话 resolve ")
        self.addCleanup(self.temp.cleanup)
        self.service = FrameworkService(Path(self.temp.name), Path(self.temp.name) / "home")
        self.addCleanup(self.service.close)

    def create(self, identity, title):
        return self.service.call("conversation.ensure", {"conversation_id": identity, "title": title})

    def resolve(self, selector):
        return self.service.call("internal.conversation.resolve", {"selector": selector})["conversations"]

    def test_complete_ids_take_priority_including_short_legacy_ids(self):
        self.create("main", "主要会话")
        self.create("conversation_other", "main")
        self.assertEqual(self.resolve("main"), [{"conversation_id": "main", "title": "主要会话"}])
        self.assertEqual(self.resolve("mai"), [])
        self.create("conversation_complete", "完整身份")
        self.create("conversation_other2", "conversation_complete")
        self.assertEqual(self.resolve("conversation_complete"), [{"conversation_id": "conversation_complete", "title": "完整身份"}])

    def test_exact_title_and_short_id_share_one_ambiguity_check(self):
        self.create("conversation_0123456789abcdef", "编号目标")
        self.create("conversation_title", "0123456789ab")
        self.assertEqual({row["conversation_id"] for row in self.resolve("0123456789ab")},
                         {"conversation_0123456789abcdef", "conversation_title"})
        self.assertEqual(self.resolve("0123456789abcdef"), [{"conversation_id": "conversation_0123456789abcdef", "title": "编号目标"}])

    def test_old_duplicates_are_included_without_paging_or_recent_order(self):
        with self.service._transaction():
            self.create("conversation_old", "同名实验")
            for index in range(210):
                self.create("conversation_record_" + str(index), "其他记录")
            self.create("conversation_new", "同名实验")
        statements = []
        self.service.db.set_trace_callback(statements.append)
        try:
            found = self.resolve("同名实验")
        finally:
            self.service.db.set_trace_callback(None)
        self.assertEqual({row["conversation_id"] for row in found}, {"conversation_old", "conversation_new"})
        self.assertEqual(len(statements), 1, "candidate uniqueness must use one SQLite snapshot")
        self.assertNotIn("OFFSET", statements[0])

    def test_percent_and_underscore_are_literals_and_prefixes_are_case_sensitive(self):
        self.create("conversation_ab_cd_123tail", "字面下划线")
        self.create("conversation_abXcdY123tail", "不可误匹配")
        self.create("conversation_ab_cd_%tail", "百分号与下划线_%")
        self.assertEqual([row["conversation_id"] for row in self.resolve("ab_cd_123")], ["conversation_ab_cd_123tail"])
        self.assertEqual([row["conversation_id"] for row in self.resolve("百分号与下划线_%")], ["conversation_ab_cd_%tail"])
        self.assertEqual(self.resolve("ab_cd_%t"), [])
        self.assertEqual(self.resolve("AB_CD_123"), [])

    def test_archived_conversations_are_not_candidates_even_for_an_exact_id(self):
        self.create("conversation_archived", "相同标题")
        self.create("conversation_active", "相同标题")
        self.create("conversation_confusing_title", "conversation_archived")
        self.service.call("conversation.patch", {"conversation_id": "conversation_archived", "expected_version": 1, "patch": {"archived": True}})
        self.assertEqual(self.resolve("conversation_archived"), [])
        self.assertEqual(self.resolve("archived"), [])
        self.assertEqual(self.resolve("相同标题"), [{"conversation_id": "conversation_active", "title": "相同标题"}])

    def test_results_and_input_are_bounded(self):
        for index in range(12):
            self.create("conversation_many_" + str(index), "重复名称")
        self.assertEqual(len(self.resolve("重复名称")), 9)
        for value in [None, "", "中" * 513, "one\ntwo", "one\rtwo"]:
            with self.subTest(value=value), self.assertRaises(FrameworkError):
                self.resolve(value)


if __name__ == "__main__":
    unittest.main()
