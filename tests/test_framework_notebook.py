"""Exercise the notebook owner over the real product database and RPC routes."""

from pathlib import Path
import tempfile
import unicodedata
import unittest

from mirally.framework import FrameworkError, FrameworkService
from mirally.framework.service import SCHEMA_VERSION

JOINER = "\u200d"
FAMILY = f"\U0001F468{JOINER}\U0001F469{JOINER}\U0001F467"
FAMILY_WITH_SON = f"{FAMILY}{JOINER}\U0001F466"
DEVELOPER = f"\U0001F469{JOINER}\U0001F4BB"
DEVELOPER_WITH_SPACE = "\U0001F469 \U0001F4BB"
RAINBOW_FLAG = f"\U0001F3F3\ufe0f{JOINER}\U0001F308"
ZERO_WIDTH_SPACE = "\u200b"
WORD_JOINER = "\u2060"
BYTE_ORDER_MARK = "\ufeff"
RIGHT_TO_LEFT_OVERRIDE = "\u202e"
TAG_LATIN_A = "\U000e0041"


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

    def test_explicit_null_collection_filters_unclassified_records(self):
        unclassified = self.write("entry.add", body="未分类记录")
        collection = self.write("collection.create", path="工作")
        filed = self.write("entry.add", body="目录记录", collection_id=collection["collection_id"])
        self.assertEqual(self.call("notebook.search")["total"], 2)
        self.assertEqual(self.call("notebook.search", collection_id=None)["total"], 1)
        self.assertEqual(self.call("notebook.search", collection_id=None)["entries"][0]["entry_id"],
                         unclassified["entry_id"])
        self.assertEqual(self.call("notebook.search", collection_id=collection["collection_id"])["entries"][0]["entry_id"],
                         filed["entry_id"])
        self.assertEqual(self.call("notebook.timeline")["total"], 2)
        self.assertEqual(self.call("notebook.timeline", collection_id=None)["total"], 1)
        self.assertEqual(self.call("notebook.timeline", collection_id=collection["collection_id"])["total"], 1)

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

    def test_approval_preflight_replays_only_matching_receipts_from_the_active_turn(self):
        entry = self.write("entry.add", body="待修订")
        collection = self.write("collection.create", path="整理")
        entry_id, collection_id = entry["entry_id"], collection["collection_id"]
        cases = (
            ("add", "entry.add", {"body": "当天记录", "collection_path": "个人/整理", "sensitivity": "private"}),
            ("revise", "entry.revise", {"entry_id": entry_id, "expected_revision": 1,
                                       "patch": {"body": "已修订"}, "reason": " 校正 "}),
            ("move", "entry.move", {"entry_id": entry_id, "expected_revision": 2, "collection_id": collection_id}),
            ("delete", "entry.delete", {"entry_id": entry_id, "expected_revision": 3}),
            ("create_collection", "collection.create", {"path": " 日程 /地点 "}),
            ("rename_collection", "collection.rename", {"collection_id": collection_id,
                                                         "expected_revision": 1, "title": " 资料 "}),
            ("archive_collection", "collection.archive", {"collection_id": collection_id, "expected_revision": 2}),
        )
        for operation, route, params in cases:
            with self.subTest(operation=operation):
                source = self.source()
                preview = {"operation": operation, "params": params, "source": source, "request_id": "one"}
                self.assertIn("detail", self.call("notebook.approval.preview", **preview))
                first = self.write(route, source=source, **params)
                self.assertEqual(self.call("notebook.approval.preview", **preview),
                                 {"receipt": {**first, "replayed": True}})
                other_operation = "add" if operation != "add" else "delete"
                self.assert_error("IDEMPOTENCY_MISMATCH", self.call, "notebook.approval.preview",
                                  **{**preview, "operation": other_operation})
                self.call("command.ack", conversation_id=self.conversation, command_id=source["turn_id"],
                          state="completed")
                self.assert_error("CONFLICT", self.call, "notebook.approval.preview", **preview)
        self.assertEqual(self.get(entry_id)["entry"]["status"], "deleted")

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

    def test_history_can_page_and_read_full_content_of_an_old_revision(self):
        prefix = "历史记录" * 110
        original = prefix + " OLD-FLOOR-3"
        entry = self.write("entry.add", body=original)
        entry_id = entry["entry_id"]
        current = prefix + " NEW-FLOOR-9"
        self.write("entry.revise", entry_id=entry_id, expected_revision=1, patch={"body": current})
        for expected in range(2, 12):
            current = prefix + f" REVISION-{expected + 1}"
            self.write("entry.revise", entry_id=entry_id, expected_revision=expected, patch={"body": current})

        first_page = self.call("notebook.entry.revisions.list", entry_id=entry_id, limit=5)
        self.assertEqual(first_page["revision_total"], 12)
        self.assertEqual([item["revision"] for item in first_page["revisions"]], [12, 11, 10, 9, 8])
        last_page = self.call("notebook.entry.revisions.list", entry_id=entry_id, limit=5, offset=10)
        self.assertEqual([item["revision"] for item in last_page["revisions"]], [2, 1])
        self.assertNotIn("OLD-FLOOR-3", last_page["revisions"][1]["excerpt"])
        old_revision = self.call("notebook.entry.revision.get", entry_id=entry_id, revision=1)
        self.assertTrue(old_revision["entry"]["body"].endswith("OLD-FLOOR-3"))
        self.assertEqual(old_revision["entry"]["revision"], 1)
        self.assert_error("NOT_FOUND", self.call, "notebook.entry.revision.get", entry_id=entry_id, revision=13)

    def test_private_content_and_audit_never_appear_in_default_reads(self):
        private = self.write("entry.add", body="样例证件号 110105199001011234", collection_path="个人/身份")
        entry_id = private["entry_id"]
        self.assertEqual(private["sensitivity"], "private")
        self.assertEqual(self.call("notebook.search", query="110105199001011234")["total"], 0)
        self.assertEqual(self.call("notebook.timeline")["total"], 0)
        self.assert_error("NOT_FOUND", self.write, "entry.add", body="私密目录追加失败", collection_id=private["collection_id"])
        appended = self.write("entry.add", body="私密目录追加成功", collection_id=private["collection_id"],
                              sensitivity="private", include_private=True)
        self.assertEqual(appended["collection_id"], private["collection_id"])
        self.assertEqual(appended["sensitivity"], "private")
        self.assertEqual(self.call("notebook.search", query="私密目录追加成功")["total"], 0)
        self.assertEqual(self.call("notebook.collection.list")["collections"], [])
        self.assert_error("NOT_FOUND", self.get, entry_id)
        self.assert_error("NOT_FOUND", self.write, "entry.add", body="试探隐藏目录", collection_path="个人/其他")
        self.assert_error("NOT_FOUND", self.call, "notebook.collection.list", collection_id=private["collection_id"])
        self.write("entry.revise", entry_id=entry_id, expected_revision=1,
                   patch={"body": "删去编号", "sensitivity": "public"}, include_private=True)
        self.assert_error("NOT_FOUND", self.get, entry_id)
        self.assertEqual(self.call("notebook.collection.list", collection_id=private["collection_id"],
                                   include_private=True)["entry_total"], 2)
        details = self.get(entry_id, include_private=True)
        self.assertEqual(details["entry"]["body"], "删去编号")
        self.assertIn("110105199001011234", details["revisions"][1]["excerpt"])
        self.assert_error("CREDENTIAL_NOT_SUPPORTED", self.write, "entry.add", body="恢复码: NOTE-PVT-58371")
        self.assertEqual(self.call("notebook.search", query="NOTE-PVT-58371", include_private=True)["total"], 0)
        consent = self.source("按普通私密记事保存：我的恢复码是 NOTE-PVT-58371")
        consent["utterance"] = "按普通私密记事保存：凭据内容已隐藏"
        secret = self.write("entry.add", source=consent, body="恢复码: NOTE-PVT-58371", credential_as_note=True)
        self.assertTrue(secret["warnings"])
        self.assertNotIn("NOTE-PVT-58371", str(secret))
        self.assertEqual(secret["sensitivity"], "private")
        card = self.write("entry.add", body="测试卡号: 4111 1111 1111 1111")
        self.assertEqual(card["sensitivity"], "private")
        manual = self.write("entry.add", body="需要私密的想法", sensitivity="private")
        self.assertEqual(self.call("notebook.search", query="想法", include_private=True)["entries"][0]["entry_id"],
                         manual["entry_id"])

    def test_approval_preview_shows_public_content_and_redacts_private_content(self):
        public_source = self.source("记一下：周五交付搜索页，标签为产品")
        public = self.call("notebook.approval.preview", operation="add", source=public_source, params={
            "body": "周五交付搜索页", "title": "迭代计划", "tags": ["产品"],
            "event_time": "2026-11-20", "time_quality": "day", "type": "task",
        })["detail"]
        self.assertIn("周五交付搜索页", public)
        self.assertIn("标题：迭代计划", public)
        self.assertIn("标签：[\"产品\"]", public)
        self.assertIn("时间：2026-11-20", public)
        self.assertEqual(self.call("notebook.search", query="周五交付")["total"], 0)

        secret = "110105199001011234"
        private_source = self.source(f"记一下：证件号 {secret}")
        private = self.call("notebook.approval.preview", operation="add", source=private_source, params={
            "body": f"证件号 {secret}", "title": "个人资料", "tags": ["身份"],
        })["detail"]
        self.assertIn("私密记事（内容已隐藏）", private)
        self.assertNotIn(secret, private)
        self.assertNotIn("个人资料", private)

        credential = "NOTE-PVT-58371"
        self.assert_error("CREDENTIAL_NOT_SUPPORTED", self.call, "notebook.approval.preview",
                          operation="add", source=self.source(f"记一下：恢复码 {credential}"),
                          params={"body": f"恢复码: {credential}"})
        safe_consent = self.source(f"按普通私密记事保存：恢复码 {credential}")
        safe_consent["utterance"] = "按普通私密记事保存：凭据内容已隐藏"
        redacted = self.call("notebook.approval.preview", operation="add", source=safe_consent,
                             params={"body": f"恢复码: {credential}"}, credential_as_note=True)["detail"]
        self.assertIn("私密记事（内容已隐藏）", redacted)
        self.assertNotIn(credential, redacted)

    def test_new_private_folder_names_use_the_same_visibility_at_preview_and_write(self):
        parent = self.write("collection.create", path="已有公开目录")
        source = self.source("保存一条私密记事")
        new_path = "已有公开目录/合成主题/归档资料"
        params = {"body": "模拟私密正文", "collection_path": new_path, "sensitivity": "private"}
        preview = self.call("notebook.approval.preview", source=source, operation="add", params=params)["detail"]
        self.assertIn("私密目录（名称已隐藏）", preview)
        self.assertNotIn("合成主题", preview)
        self.assertNotIn("归档资料", preview)
        self.assertNotIn("模拟私密正文", preview)
        saved = self.write("entry.add", source=source, **params)
        self.assertEqual(saved["collection"]["sensitivity"], "private")
        self.assertEqual(saved["collection"]["path"], new_path)
        self.assertEqual(self.call("notebook.collection.list", collection_id=parent["collection_id"])["total"], 0)
        existing_public = self.call("notebook.approval.preview", source=source, operation="add", params={
            **params, "collection_path": "已有公开目录",
        })["detail"]
        self.assertIn("目录：已有公开目录", existing_public)
        self.assertIn("私密记事（内容已隐藏）", existing_public)

    def test_approval_preview_identifies_revision_and_collection_effects(self):
        entry = self.write("entry.add", body="周会整理完成", title="原定事项")
        source = self.source("更正这条周会记事：标题改成最终事项")
        revised = self.call("notebook.approval.preview", operation="revise", source=source, params={
            "entry_id": entry["entry_id"], "expected_revision": 1,
            "patch": {"title": "最终事项"},
        })["detail"]
        self.assertIn(entry["entry_id"], revised)
        self.assertIn("正文：周会整理完成", revised)
        self.assertIn("标题：原定事项", revised)
        self.assertIn("标题：最终事项", revised)
        self.assertEqual(self.get(entry["entry_id"])["entry"]["title"], "原定事项")

        collection = self.write("collection.create", path="项目/准备")
        self.write("entry.add", body="准备清单", collection_id=collection["collection_id"])
        inactive = self.write("entry.add", body="已撤销的准备清单", collection_id=collection["collection_id"])
        self.write("entry.reverse", entry_id=inactive["entry_id"], expected_revision=1)
        archive = self.call("notebook.approval.preview", operation="archive_collection",
                            source=self.source("归档这个记事本目录项目/准备。"), params={
                                "collection_id": collection["collection_id"], "expected_revision": 1,
                            })["detail"]
        self.assertIn("项目/准备", archive)
        self.assertIn("影响：0 个子目录，2 条记事（其中 1 条有效）", archive)
        self.assertEqual(self.call("notebook.collection.list", collection_id=collection["collection_id"])["entry_total"], 1)

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

    def test_connected_emoji_sequences_work_as_directory_names(self):
        for path in (FAMILY, DEVELOPER, RAINBOW_FLAG):
            created = self.write("collection.create", path=path)
            self.assertTrue(created["collection_created"])
            self.assertEqual((created["title"], created["path"]), (path, path))
            stored = self.service.db.execute("SELECT * FROM notebook_collections WHERE id=?",
                                             (created["collection_id"],)).fetchone()
            self.assertEqual(stored["path"], path)
            self.assertEqual(stored["path_key"], unicodedata.normalize("NFKC", path).casefold())
        roots = self.call("notebook.collection.list")
        self.assertEqual(roots["total"], 3)
        self.assertEqual(sorted(item["path"] for item in roots["collections"]),
                         sorted((FAMILY, DEVELOPER, RAINBOW_FLAG)))

        nested = self.write("collection.create", path=f"家庭/{FAMILY}")
        self.assertEqual(nested["path"], f"家庭/{FAMILY}")
        parent = next(item for item in self.call("notebook.collection.list")["collections"] if item["path"] == "家庭")
        self.assertEqual(parent["kind"], "auto_created")
        children = self.call("notebook.collection.list", collection_id=parent["collection_id"])["collections"]
        self.assertEqual([item["path"] for item in children], [f"家庭/{FAMILY}"])

        entry = self.write("entry.add", body="周末全家一起看了电影", collection_path=f"家庭/{FAMILY}")
        self.assertEqual(entry["collection"]["path"], f"家庭/{FAMILY}")
        self.assertEqual(self.call("notebook.collection.list", collection_id=entry["collection_id"])["entry_total"], 1)

        renamed = self.write("collection.rename", collection_id=entry["collection_id"], title=FAMILY_WITH_SON,
                             expected_revision=1)
        self.assertEqual((renamed["title"], renamed["path"]), (FAMILY_WITH_SON, f"家庭/{FAMILY_WITH_SON}"))
        self.assertEqual(self.get(entry["entry_id"])["entry"]["collection"]["path"], f"家庭/{FAMILY_WITH_SON}")

    def test_approval_preview_matches_the_write_for_connected_emoji_paths(self):
        path = f"家庭/{FAMILY}"
        created = self.call("notebook.approval.preview", source=self.source("新建家庭目录"),
                            operation="create_collection", params={"path": path})["detail"]
        self.assertIn("目录：家庭/", created)

        source = self.source("把这条记事放进家庭目录")
        params = {"body": "周末全家一起看了电影", "collection_path": path}
        detail = self.call("notebook.approval.preview", source=source, operation="add", params=params)["detail"]
        self.assertIn("目录：家庭/", detail)
        saved = self.write("entry.add", source=source, **params)
        self.assertEqual(saved["collection"]["path"], path)
        self.assertEqual(saved["collection"]["sensitivity"], "public")

        renamed = self.call("notebook.approval.preview", source=self.source("把家庭目录改成彩虹旗"),
                            operation="rename_collection", params={
                                "collection_id": saved["collection_id"], "expected_revision": 1,
                                "title": RAINBOW_FLAG})["detail"]
        # The approval shows the name as written, joiners included, so the flag keeps its whole shape.
        self.assertIn(f"新目录：家庭/{RAINBOW_FLAG}", renamed)

        private_source = self.source("保存一条只有我能看的记事")
        private_params = {"body": "只有我能看的旅行记事", "collection_path": f"旅行/{RAINBOW_FLAG}",
                          "sensitivity": "private"}
        hidden = self.call("notebook.approval.preview", source=private_source, operation="add",
                           params=private_params)["detail"]
        self.assertIn("私密目录（名称已隐藏）", hidden)
        self.assertNotIn("\U0001F3F3", hidden)
        private = self.write("entry.add", source=private_source, **private_params)
        self.assertEqual(private["collection"]["path"], f"旅行/{RAINBOW_FLAG}")
        self.assertEqual(private["collection"]["sensitivity"], "private")

    def test_directory_names_still_reject_control_bidi_and_invisible_characters(self):
        rejected = (
            "工作\t计划", "工作\n计划", "工作\x00计划", JOINER, f"{JOINER}工作", f"工作{JOINER}",
            f"👨{JOINER}{JOINER}👩", ZERO_WIDTH_SPACE, f"工作{ZERO_WIDTH_SPACE}计划", f"工作{WORD_JOINER}计划",
            f"{BYTE_ORDER_MARK}工作", f"{RIGHT_TO_LEFT_OVERRIDE}工作", f"工作{RIGHT_TO_LEFT_OVERRIDE}",
            f"{TAG_LATIN_A}工作", ".", "..", "工作／计划", "标签" * 13, "项目/准备/清单/备份/更多", "标签" * 51,
        )
        for path in rejected:
            self.assert_error("INVALID_PARAMS", self.write, "collection.create", path=path)
        self.assertEqual(self.call("notebook.collection.list")["total"], 0)
        self.assert_error("INVALID_PARAMS", self.write, "entry.add", body="目录名无效", collection_path=f"工作{JOINER}")
        working = self.write("collection.create", path="工作")
        self.assert_error("INVALID_PARAMS", self.write, "collection.rename",
                          collection_id=working["collection_id"], title=f"{JOINER}工作")

    def test_directory_deduplication_still_folds_case_and_compatibility_forms(self):
        created = self.write("collection.create", path="Lab")
        self.assertTrue(created["collection_created"])
        for alias in ("lab", "LAB", "Ｌａｂ"):
            again = self.write("collection.create", path=alias)
            self.assertFalse(again["collection_created"])
            self.assertEqual(again["collection_id"], created["collection_id"])
        roots = self.call("notebook.collection.list")
        self.assertEqual(roots["total"], 1)
        self.assertEqual(roots["collections"][0]["path"], "Lab")

        # Joiners stay in the name: the same faces without a joiner are a different directory.
        unjoined = self.write("collection.create", path=FAMILY.replace(JOINER, ""))
        self.assertTrue(unjoined["collection_created"])
        self.assertNotEqual(unjoined["collection_id"], self.write("collection.create", path=FAMILY)["collection_id"])
        self.assertEqual(self.call("notebook.collection.list")["total"], 3)

    def test_approval_preview_shows_directory_names_exactly_as_written(self):
        joined = self.write("collection.create", path=DEVELOPER)
        spaced = self.write("collection.create", path=DEVELOPER_WITH_SPACE)
        self.assertNotEqual(joined["collection_id"], spaced["collection_id"])

        # Two directories that only differ by the joiner must not share one approval text,
        # otherwise the user cannot tell where the note is going.
        body = "今天把新版本发上去了"
        previews = {}
        for path in (DEVELOPER, DEVELOPER_WITH_SPACE):
            previews[path] = self.call("notebook.approval.preview", source=self.source("存进表情目录"),
                                       operation="add", params={"body": body, "collection_path": path})["detail"]
            self.assertIn(f"目录：{path}", previews[path])
        self.assertNotEqual(previews[DEVELOPER], previews[DEVELOPER_WITH_SPACE])

        by_id = self.call("notebook.approval.preview", source=self.source("存进已有目录"), operation="add",
                          params={"body": body, "collection_id": joined["collection_id"]})["detail"]
        self.assertIn(f"目录：{DEVELOPER}", by_id)

        created = self.call("notebook.approval.preview", source=self.source("新建子目录"),
                            operation="create_collection", params={"path": f"工作/{DEVELOPER}"})["detail"]
        self.assertIn(f"目录：工作/{DEVELOPER}", created)

        entry = self.write("entry.add", body=body, collection_path=DEVELOPER)
        self.assertEqual(entry["collection"]["path"], DEVELOPER)
        moved = self.call("notebook.approval.preview", source=self.source("换个目录"), operation="move",
                          params={"entry_id": entry["entry_id"], "expected_revision": 1,
                                  "collection_id": spaced["collection_id"]})["detail"]
        self.assertIn(f"目标目录：{DEVELOPER_WITH_SPACE}", moved)

        archived = self.call("notebook.approval.preview", source=self.source("归档目录"),
                             operation="archive_collection",
                             params={"collection_id": joined["collection_id"], "expected_revision": 1})["detail"]
        self.assertIn(f"目录：{DEVELOPER}", archived)
        renamed = self.call("notebook.approval.preview", source=self.source("改名"), operation="rename_collection",
                            params={"collection_id": joined["collection_id"], "expected_revision": 1,
                                    "title": RAINBOW_FLAG})["detail"]
        self.assertIn(f"新目录：{RAINBOW_FLAG}", renamed)

        # A public directory keeps its visible name; a new private one still hides it, joiners and all.
        public_target = self.call("notebook.approval.preview", source=self.source("只给自己看"), operation="add",
                                  params={"body": body, "collection_path": DEVELOPER,
                                          "sensitivity": "private"})["detail"]
        self.assertIn("内容：私密记事（内容已隐藏）", public_target)
        self.assertIn(f"目录：{DEVELOPER}", public_target)

        private_new = self.call("notebook.approval.preview", source=self.source("新建私密目录"), operation="add",
                                params={"body": body, "collection_path": f"私密/{DEVELOPER}",
                                        "sensitivity": "private"})["detail"]
        self.assertIn("私密目录（名称已隐藏）", private_new)
        self.assertNotIn(DEVELOPER, private_new)

    def test_approval_preview_still_hides_undisplayable_characters_in_directory_names(self):
        # Directory names reaching the approval are already validated, so only a legacy or hand-written row
        # can carry a control or format character; it must never be rendered raw into the approval text.
        row = self.write("collection.create", path="工作")
        legacy = "工作\t计划\x00"
        self.service.db.execute("UPDATE notebook_collections SET path=?,path_key=? WHERE id=?",
                                (legacy, unicodedata.normalize("NFKC", legacy).casefold(), row["collection_id"]))
        detail = self.call("notebook.approval.preview", source=self.source("归档目录"),
                           operation="archive_collection",
                           params={"collection_id": row["collection_id"], "expected_revision": 1})["detail"]
        self.assertIn("目录：工作 计划", detail)
        self.assertNotIn("\t", detail)
        self.assertNotIn("\x00", detail)

