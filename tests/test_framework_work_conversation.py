"""Work follows a real conversation and preserves old work records."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import unittest

from mirally.framework import FrameworkError, FrameworkService


class WorkConversationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mirally 工作会话 ")
        self.addCleanup(self.temp.cleanup)
        self.service = FrameworkService(Path(self.temp.name), Path(self.temp.name) / "home")
        self.addCleanup(self.service.close)
        self.conversation = self.service.call("conversation.ensure", {"title": "实验讨论"})["conversation_id"]

    def ensure(self, title="GPU 监测", goal="持续查看空闲 GPU"):
        return self.service.call("internal.work.ensure_conversation", {"conversation_id": self.conversation, "title": title, "goal": goal})

    def test_ensure_is_atomic_and_preserves_original_work_on_retry(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda index: self.ensure("名称 " + str(index), "目标 " + str(index)), range(8)))
        self.assertEqual(sum(result["created"] for result in results), 1)
        self.assertEqual(len({result["work"]["id"] for result in results}), 1)
        work = next(result["work"] for result in results if result["created"])
        self.assertTrue(all(result["work"]["title"] == work["title"] for result in results))
        self.assertEqual(self.service.call("work.by_conversation", {"conversation_id": self.conversation})["works"][0]["id"], work["id"])

    def test_legacy_multiple_works_are_not_merged_or_silently_selected(self):
        for title in ("历史 A", "历史 B"):
            self.service.call("work.create", {"title": title, "conversation_id": self.conversation})
        with self.assertRaisesRegex(FrameworkError, "多项"):
            self.ensure()
        self.assertEqual(len(self.service.call("work.by_conversation", {"conversation_id": self.conversation})["works"]), 2)

    def test_archived_work_requires_explicit_restore(self):
        work = self.ensure()["work"]
        self.service.call("work.update", {"work_id": work["id"], "expected_revision": work["revision"], "patch": {"intent": "archived"}})
        with self.assertRaisesRegex(FrameworkError, "归档"):
            self.ensure()
        self.assertEqual(self.service.call("work.by_conversation", {"conversation_id": self.conversation})["works"], [])
        self.assertEqual(len(self.service.call("work.by_conversation", {"conversation_id": self.conversation, "include_archived": True})["works"]), 1)

    def test_model_update_is_confined_to_the_current_conversation_and_description(self):
        work = self.ensure()["work"]
        result = self.service.call("internal.work.update_conversation", {"conversation_id": self.conversation,
            "work_id": work["id"], "patch": {"title": "GPU 训练监测", "goal": "追踪训练 GPU 占用"}})
        self.assertEqual(result["work"]["title"], "GPU 训练监测")
        self.assertEqual(result["work"]["conversation_id"], self.conversation)
        other = self.service.call("conversation.ensure", {"title": "其他会话"})["conversation_id"]
        with self.assertRaises(FrameworkError):
            self.service.call("internal.work.update_conversation", {"conversation_id": other, "work_id": work["id"], "patch": {"title": "越权"}})
        with self.assertRaises(FrameworkError):
            self.service.call("internal.work.update_conversation", {"conversation_id": self.conversation, "work_id": work["id"], "patch": {"intent": "ended"}})
        self.assertEqual(self.service.call("work.get", {"work_id": work["id"]})["work"]["title"], "GPU 训练监测")

    def test_archived_conversation_cannot_create_work(self):
        meta = self.service.call("conversation.get", {"conversation_id": self.conversation})
        self.service.call("conversation.patch", {"conversation_id": self.conversation, "expected_version": meta["metadata_version"], "patch": {"archived": True}})
        with self.assertRaises(FrameworkError):
            self.ensure()

    def test_generated_title_renames_auto_conversation_but_preserves_explicit_title(self):
        explicit = self.ensure()
        self.assertEqual(explicit["work"]["title"], "实验讨论")
        self.assertEqual(self.service.call("conversation.get", {"conversation_id": self.conversation})["title"], "实验讨论")
        auto = self.service.call("conversation.ensure", {})["conversation_id"]
        created = self.service.call("internal.work.ensure_conversation", {"conversation_id": auto, "title": "GPU 监测", "goal": "监测显存"})
        self.assertTrue(created["conversation_title_changed"])
        self.assertEqual(created["work"]["title"], "GPU 监测")
        self.assertEqual(self.service.call("conversation.get", {"conversation_id": auto})["title"], "GPU 监测")
        self.assertEqual(self.service.db.execute("SELECT title_mode FROM conversations WHERE id=?", (auto,)).fetchone()[0], "auto")

    def test_renames_are_atomic_and_stale_work_revision_cannot_overwrite(self):
        work = self.ensure()["work"]
        updated = self.service.call("internal.work.update_conversation", {"conversation_id": self.conversation,
            "work_id": work["id"], "patch": {"title": "模型命名"}})
        self.assertEqual(updated["work"]["revision"], work["revision"] + 1)
        self.assertEqual(self.service.call("conversation.get", {"conversation_id": self.conversation})["title"], "模型命名")
        meta = self.service.call("conversation.get", {"conversation_id": self.conversation})
        self.service.call("conversation.patch", {"conversation_id": self.conversation, "expected_version": meta["metadata_version"], "patch": {"title": "用户命名"}})
        latest = self.service.call("work.get", {"work_id": work["id"]})["work"]
        self.assertEqual(latest["title"], "用户命名")
        self.assertEqual(latest["revision"], updated["work"]["revision"] + 1)
        with self.assertRaises(FrameworkError):
            self.service.call("work.update", {"work_id": work["id"], "expected_revision": work["revision"], "patch": {"title": "旧版本"}})
        self.assertEqual(self.service.call("conversation.get", {"conversation_id": self.conversation})["title"], "用户命名")

    def test_legacy_multiple_work_titles_are_not_renamed_with_the_conversation(self):
        works = [self.service.call("work.create", {"conversation_id": self.conversation, "title": title})["work"] for title in ("既有一", "既有二")]
        meta = self.service.call("conversation.get", {"conversation_id": self.conversation})
        self.service.call("conversation.patch", {"conversation_id": self.conversation, "expected_version": meta["metadata_version"], "patch": {"title": "新会话名称"}})
        self.assertEqual([self.service.call("work.get", {"work_id": item["id"]})["work"]["title"] for item in works], ["既有一", "既有二"])
        self.service.call("internal.work.update_conversation", {"conversation_id": self.conversation, "work_id": works[0]["id"], "patch": {"title": "修改第一项"}})
        self.assertEqual(self.service.call("conversation.get", {"conversation_id": self.conversation})["title"], "新会话名称")
        self.assertEqual(self.service.call("work.get", {"work_id": works[1]["id"]})["work"]["title"], "既有二")

    def test_conversation_rename_invalidates_pending_work_approval(self):
        work = self.ensure()["work"]
        self.service.call("capabilities.configure", {"expected_revision": 1, "patch": {"workspace_write_enabled": True}})
        workspace = self.service.home / "workspace"
        workspace.mkdir(exist_ok=True)
        (workspace / "fixture.py").write_text("print('fixture')", encoding="utf-8")
        approval = self.service.call("work.run.prepare", {"work_id": work["id"], "script": "fixture.py", "request_id": "pending"})["approval"]
        meta = self.service.call("conversation.get", {"conversation_id": self.conversation})
        self.service.call("conversation.patch", {"conversation_id": self.conversation, "expected_version": meta["metadata_version"], "patch": {"title": "修改后的名称"}})
        with self.assertRaises(FrameworkError):
            self.service.call("work.run.approve", {"approval_id": approval["id"], "expected_digest": approval["digest"], "actor": "desktop"})

    def test_remote_work_mutations_recheck_binding_setting_and_command_in_owner_transaction(self):
        saved = self.service.call("channel.weixin.save", {"token":"fixture", "peer_id":"peer", "bot_id":"bot",
            "base_url":"https://ilinkai.weixin.qq.com"})["state"]
        bound = self.service.call("channel.weixin.bind", {"expected_revision":saved["revision"],
            "conversation_id":self.conversation})["state"]
        self.service.call("settings.update", {"patch":{"weixin_remote_control_enabled":True}})
        command = "weixin-fixture-command"
        self.service.call("conversation.bind", {"conversation_id":self.conversation, "session_id":"native-fixture"})
        self.service.call("command.admit", {"conversation_id":self.conversation, "command_id":command, "text":"创建工作"})
        scope = {"installation_id":bound["installation_id"], "revision":bound["revision"], "command_id":command}
        work = self.service.call("internal.work.ensure_conversation", {"conversation_id":self.conversation,
            "title":"GPU", "weixin_scope":scope})["work"]
        self.service.call("settings.update", {"patch":{"weixin_remote_control_enabled":False}})
        with self.assertRaises(FrameworkError):
            self.service.call("internal.work.update_conversation", {"conversation_id":self.conversation,
                "work_id":work["id"], "patch":{"goal":"未授权的修改"}, "weixin_scope":scope})
        self.assertNotEqual(self.service.call("work.get", {"work_id":work["id"]})["work"]["goal"], "未授权的修改")
        self.service.call("settings.update", {"patch":{"weixin_remote_control_enabled":True}})
        self.service.call("command.ack", {"conversation_id":self.conversation, "command_id":command, "state":"cancelled"})
        with self.assertRaises(FrameworkError):
            self.service.call("internal.work.update_conversation", {"conversation_id":self.conversation,
                "work_id":work["id"], "patch":{"goal":"已取消的修改"}, "weixin_scope":scope})
        other = self.service.call("conversation.ensure", {"title":"新路由"})["conversation_id"]
        self.service.call("channel.weixin.select", {"expected_revision":bound["revision"], "conversation_id":other})
        with self.assertRaises(FrameworkError):
            self.service.call("internal.work.ensure_conversation", {"conversation_id":self.conversation,
                "title":"失效路由", "weixin_scope":scope})


if __name__ == "__main__":
    unittest.main()
