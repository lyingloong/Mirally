"""Exercise the notebook owner over the real product database and RPC routes."""

from pathlib import Path
import tempfile
import unittest

from mirally.framework import FrameworkError, FrameworkService
from mirally.framework.service import SCHEMA_VERSION


class NotebookTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="mirally 记事本 ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.now = 1794700000000
        self.service = self.open_service()
        self.addCleanup(lambda: self.service.close())
        self.conversation = self.call("conversation.ensure")["conversation_id"]
        self.call("conversation.bind", conversation_id=self.conversation, session_id="notebook-session")
        self.turn_number = 0

    def open_service(self):
        return FrameworkService(self.root, self.root / "home", clock=lambda: self.now)

    def call(self, route, **parameters):
        return self.service.call(route, parameters)

    def source(self, utterance="请保存一条记事", channel="desktop"):
        self.turn_number += 1
        turn = f"notebook-turn-{self.turn_number}"
        self.call("command.admit", conversation_id=self.conversation, command_id=turn, text=utterance)
        return {"conversation_id": self.conversation, "turn_id": turn, "channel": channel, "utterance": utterance}

    def write(self, route, *, source=None, request_id="one", **parameters):
        return self.call("notebook." + route, source=source or self.source(), request_id=request_id, **parameters)

    def get(self, entry_id, **parameters):
        return self.call("notebook.entry.get", entry_id=entry_id, **parameters)

    def assert_error(self, code, function, *args, **kwargs):
        with self.assertRaises(FrameworkError) as caught:
            function(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)

    def test_database_upgrade_retains_existing_product_data(self):
        for table in ("notebook_collection_revisions", "notebook_revisions", "notebook_entries",
                      "notebook_collections", "notebook_requests"):
            self.service.db.execute("DROP TABLE " + table)
        self.service.db.execute("PRAGMA user_version=11")
        self.service.close()
        self.service = self.open_service()
        self.assertEqual(self.call("system.status")["schema_version"], SCHEMA_VERSION)
        entry = self.write("entry.add", body="升级后记录")
        self.service.close()
        self.service = self.open_service()
        self.assertEqual(self.get(entry["entry_id"])["entry"]["body"], "升级后记录")
        self.assertEqual(self.call("conversation.get", conversation_id=self.conversation)["session_id"], "notebook-session")

    def test_deterministic_query_pagination_and_evidence(self):
        first = self.write("entry.add", body="搬工位到了东面，完成 100%", title="老周",
                           tags=["Lab", "lab"], event_time="2026-09-01", props={"site": "梅坞"})
        self.now += 1
        second = self.write("entry.add", body="老周搬工位，等待确认", tags=["lab"],
                            event_time="2026-09-01T00:30:00+08:00")
        self.now += 1
        unknown = self.write("entry.add", body="老周搬工位，后来补记", tags=["lab"])
        query = {"query": "老周 搬工位", "tags": ["LAB"], "limit": 1}
        page = self.call("notebook.search", **query)
        self.assertEqual(page["total"], 3)
        self.assertEqual(page["evidence"][0]["entry_id"], unknown["entry_id"])
        self.assertEqual(self.call("notebook.search", offset=1, **query)["entries"][0]["entry_id"], second["entry_id"])
        self.assertEqual(self.call("notebook.search", offset=3, **query)["entries"], [])
        self.assertEqual(self.call("notebook.search", query="100% 梅坞")["total"], 1)
        self.assertEqual(self.call("notebook.search", query="100_")["total"], 0)
        timeline = self.call("notebook.timeline", from_time="2026-09-01", to_time="2026-09-01")
        self.assertEqual([row["entry_id"] for row in timeline["entries"]], [first["entry_id"], second["entry_id"]])
        instant = self.call("notebook.timeline", to_time="2026-08-31T16:30:00Z")
        self.assertEqual([row["entry_id"] for row in instant["entries"]], [second["entry_id"]])
        self.assertEqual(self.call("notebook.timeline")["entries"][-1]["time_quality"], "unknown")
        self.assertEqual(self.get(first["entry_id"])["entry"]["tags"], ["Lab"])
        self.assert_error("INVALID_PARAMS", self.write, "entry.add", body="坏时间", event_time="2026-11-12T10:00:00")
        self.assert_error("INVALID_PARAMS", self.call, "notebook.timeline", from_time="2026-11-13", to_time="2026-11-12")

    def test_command_receipts_and_semantic_idempotency(self):
        source = self.source("记下今天很开心" + "。" * 300)
        first = self.write("entry.add", body="今天很开心", source=source)
        replay = self.write("entry.add", body="今天很开心", source=source)
        self.assertFalse(first["replayed"])
        self.assertEqual(replay, {**first, "replayed": True})
        self.assert_error("IDEMPOTENCY_MISMATCH", self.write, "entry.add", body="不同正文", source=source)
        duplicate = self.write("entry.add", body="  今天很开心 ", source=source, request_id="two")
        self.assertTrue(duplicate["replayed"])
        self.assertEqual(duplicate["entry_id"], first["entry_id"])
        another = self.write("entry.add", body="今天很开心")
        self.assertNotEqual(first["entry_id"], another["entry_id"])
        details = self.get(first["entry_id"])
        self.assertEqual(len(details["entry"]["source"]["utterance"]), 200)
        self.assertEqual(details["entry"]["source"]["turn_id"], source["turn_id"])
        self.assertEqual(details["entry"]["created_by"], "user")
        self.assertEqual(self.get(another["entry_id"])["entry"]["created_by"], "model_inferred")
        self.assertEqual(details["revision_total"], 1)

    def test_directory_rename_move_archive_and_collision(self):
        entry = self.write("entry.add", body="旅途记录", collection_path="旅行/杭州")
        self.assertTrue(entry["collection_created"])
        self.assertEqual(entry["collection"]["kind"], "auto_created")
        roots = self.call("notebook.collection.list")["collections"]
        root = roots[0]
        self.assertEqual(root["path"], "旅行")
        child_id = entry["collection_id"]
        child = self.call("notebook.collection.list", collection_id=root["collection_id"])["collections"][0]
        self.assertEqual(child["collection_id"], child_id)
        competing = self.write("collection.create", path="项目")
        self.assertEqual(competing["kind"], "user_defined")
        self.assert_error("CONFLICT", self.write, "collection.rename", collection_id=root["collection_id"], title="项目")
        renamed = self.write("collection.rename", collection_id=root["collection_id"], title="出行", expected_revision=1)
        self.assertEqual(renamed["revision"], 2)
        self.assertEqual(self.get(entry["entry_id"])["entry"]["collection"]["path"], "出行/杭州")
        self.assertEqual(self.get(entry["entry_id"])["entry"]["collection"]["collection_id"], child_id)
        self.assertEqual(self.call("notebook.collection.list", collection_id=child_id)["entry_total"], 1)
        self.write("entry.move", entry_id=entry["entry_id"], expected_revision=1, collection_id=competing["collection_id"])
        self.assertEqual(self.call("notebook.collection.list", collection_id=child_id)["entry_total"], 0)
        self.write("collection.archive", collection_id=competing["collection_id"], expected_revision=1)
        self.assert_error("NOT_FOUND", self.call, "notebook.collection.list", collection_id=competing["collection_id"])
        details = self.get(entry["entry_id"])
        self.assertIsNone(details["entry"]["collection"])
        self.assertEqual([item["operation"] for item in details["revisions"]], ["archive_collection", "move", "add"])
        self.assertEqual(self.call("notebook.search", query="旅途")["total"], 1)

    def test_revision_conflicts_and_terminal_records(self):
        entry = self.write("entry.add", body="三花猫", event_time="2026-11-10")
        entry_id = entry["entry_id"]
        revised = self.write("entry.revise", entry_id=entry_id, expected_revision=1,
                             patch={"body": "橘猫", "event_time": None}, reason="原描述写错")
        self.assertEqual(revised["revision"], 2)
        details = self.get(entry_id)
        self.assertEqual(details["entry"]["time_quality"], "unknown")
        self.assertEqual([item["excerpt"] for item in details["revisions"]], ["橘猫", "三花猫"])
        self.assert_error("CONFLICT", self.write, "entry.revise", entry_id=entry_id,
                          expected_revision=1, patch={"body": "橘猫和三花猫"})
        reversed_entry = self.write("entry.reverse", entry_id=entry_id, expected_revision=2, reason="用户撤销")
        self.assertEqual(reversed_entry["status"], "reversed")
        self.assertEqual(self.call("notebook.search", query="橘猫")["total"], 0)
        deleted = self.write("entry.add", body="待删除")
        self.write("entry.delete", entry_id=deleted["entry_id"], expected_revision=1)
        self.assertEqual(self.get(deleted["entry_id"])["entry"]["status"], "deleted")
        self.assertEqual(self.call("notebook.timeline")["total"], 0)

    def test_private_content_and_audit_never_appear_in_default_reads(self):
        private = self.write("entry.add", body="样例证件号 110105199001011234", collection_path="个人/身份")
        entry_id = private["entry_id"]
        self.assertEqual(private["sensitivity"], "private")
        self.assertEqual(self.call("notebook.search", query="110105199001011234")["total"], 0)
        self.assertEqual(self.call("notebook.timeline")["total"], 0)
        self.assertEqual(self.call("notebook.collection.list")["collections"], [])
        self.assert_error("NOT_FOUND", self.get, entry_id)
        self.assert_error("NOT_FOUND", self.write, "entry.add", body="试探隐藏目录", collection_path="个人/其他")
        self.assert_error("NOT_FOUND", self.call, "notebook.collection.list", collection_id=private["collection_id"])
        self.write("entry.revise", entry_id=entry_id, expected_revision=1,
                   patch={"body": "删去编号", "sensitivity": "public"}, include_private=True)
        self.assert_error("NOT_FOUND", self.get, entry_id)
        self.assertEqual(self.call("notebook.collection.list", collection_id=private["collection_id"],
                                   include_private=True)["entry_total"], 1)
        details = self.get(entry_id, include_private=True)
        self.assertEqual(details["entry"]["body"], "删去编号")
        self.assertIn("110105199001011234", details["revisions"][1]["excerpt"])
        secret = self.write("entry.add", body="恢复码: NOTE-PVT-58371")
        self.assertTrue(secret["warnings"])
        self.assertNotIn("NOTE-PVT-58371", str(secret))
        card = self.write("entry.add", body="测试卡号: 4111 1111 1111 1111")
        self.assertEqual(card["sensitivity"], "private")
        manual = self.write("entry.add", body="需要私密的想法", sensitivity="private")
        self.assertEqual(self.call("notebook.search", query="想法", include_private=True)["entries"][0]["entry_id"],
                         manual["entry_id"])

    def test_full_body_only_for_direct_lookup(self):
        body = "观察记录" * 250
        entry = self.write("entry.add", body=body)
        self.assertEqual(self.get(entry["entry_id"])["entry"]["body"], body)
        page = self.call("notebook.search", query="观察")
        self.assertNotIn("body", page["entries"][0])
        self.assertNotIn("utterance", page["entries"][0]["source"])
        self.assertEqual(len(page["evidence"][0]["excerpt"]), 300)

    def test_desktop_cancellation_and_weixin_binding_fence_writes_and_reads(self):
        desktop = self.source()
        self.call("command.ack", conversation_id=self.conversation, command_id=desktop["turn_id"], state="cancelled")
        self.assert_error("CONFLICT", self.write, "entry.add", body="已取消", source=desktop)
        saved = self.call("channel.weixin.save", token="test-token", peer_id="peer", bot_id="bot",
                          base_url="https://ilinkai.weixin.qq.com")["state"]
        bound = self.call("channel.weixin.bind", expected_revision=saved["revision"],
                          conversation_id=self.conversation)["state"]
        source = self.source(channel="weixin")
        scope = {"installation_id": bound["installation_id"], "revision": bound["revision"], "command_id": source["turn_id"]}
        self.assert_error("CONFLICT", self.write, "entry.add", source=source, body="未授权")
        private = self.write("entry.add", source=source, weixin_scope=scope, body="跨端私密", sensitivity="private")
        self.assertEqual(self.call("notebook.search", source=source, weixin_scope=scope)["total"], 0)
        self.assertEqual(self.get(private["entry_id"], source=source, weixin_scope=scope,
                                  include_private=True)["entry"]["body"], "跨端私密")
        self.call("channel.weixin.enable", expected_revision=bound["revision"], enabled=False)
        with self.assertRaises(FrameworkError):
            self.get(private["entry_id"], source=source, weixin_scope=scope, include_private=True)
        with self.assertRaises(FrameworkError):
            self.write("entry.add", source=source, weixin_scope=scope, body="跨端私密", sensitivity="private")
        self.assertEqual(self.call("notebook.search", include_private=True)["total"], 1)
