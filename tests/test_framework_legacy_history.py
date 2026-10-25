import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from mirally.framework.legacy_history import archive, entries, read, record_view
from mirally.framework.service import FrameworkService
from mirally.framework.migration import MigrationPlan


class LegacyHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mirally-legacy-")
        self.root = Path(self.temp.name)
        self.home = self.root / "新的 home"
        (self.home / "framework").mkdir(parents=True)
        self.original = self.root / "原始 会话.jsonl"
        self.original.write_text("\n".join(json.dumps({"type": "message", "message": {"role": "assistant", "content": [{"type": "text", "text": f"record {n} <script>literal</script>"}]}}, ensure_ascii=False) for n in range(35)), encoding="utf-8")
        entry = {"session_id": "old-session", "path": str(self.original), "readonly": True,
                 "bytes": self.original.stat().st_size, "sha256": hashlib.sha256(self.original.read_bytes()).hexdigest()}
        self.index = self.home / "framework/legacy-history.json"
        self.index.write_text(json.dumps([entry]), encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def test_pagination_and_independent_archive_after_original_retired(self):
        identity = entries(self.home)[0]["id"]
        result = read(self.home, identity, limit=30)
        self.assertEqual(result["next_cursor"], 30)
        self.assertIn("<script>literal</script>", result["messages"][0]["text"])
        self.assertEqual(archive(self.home)["archived"], 1)
        self.original.unlink()
        self.assertTrue(entries(self.home)[0]["archived"])
        self.assertEqual(entries(self.home)[0]["id"], identity)
        tail = read(self.home, identity, cursor=30)
        self.assertEqual(len(tail["messages"]), 5)
        self.assertIsNone(tail["next_cursor"])
        self.assertFalse(tail["transcript_conversion"])
        self.assertEqual(archive(self.home)["archived"], 1)

    def test_modified_old_source_is_not_consumed_or_archived(self):
        self.original.write_text('{"message":{"role":"user","content":"replacement"}}', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "指纹"):
            read(self.home, entries(self.home)[0]["id"])
        with self.assertRaisesRegex(ValueError, "指纹"):
            archive(self.home)

    def test_content_block_limit_reports_partial_view(self):
        value=record_view({"content":[{"type":"text","text":"short"}]*101})
        self.assertTrue(value["truncated"])

    def test_only_known_ids_and_owned_archive_paths(self):
        with self.assertRaises(ValueError):
            read(self.home, "../secret")
        value=json.loads(self.index.read_text())
        value[0]["archive_path"]="../../原始 会话.jsonl"
        self.index.write_text(json.dumps(value),encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "越界"):
            read(self.home, entries(self.home)[0]["id"])

    def test_owner_routes_are_readonly_and_archive_reopens_without_old_runtime(self):
        with FrameworkService(self.root,self.home) as service:
            item=service.call("legacy.history")["sessions"][0]
            service.call("legacy.archive")
            self.original.unlink()
            result=service.call("legacy.history.read",{"record_id":item["id"],"limit":2})
            self.assertEqual(len(result["messages"]),2)
            self.assertTrue(result["readonly"])
            self.assertEqual(service.call("conversation.list")["conversations"],[])

    def test_full_migration_publishes_independent_archive_and_preserves_source(self):
        destination=self.root / "迁入 新目录"
        before=self.original.read_bytes()
        plan=MigrationPlan(self.root,destination,legacy_history=self.root)
        self.assertFalse(plan.preview()["writes"])
        result=plan.apply(confirm_stopped=True)
        self.assertTrue(result["legacy_sessions_archived"])
        self.assertEqual(self.original.read_bytes(),before)
        # The old runtime and its whole directory can retire independently.
        self.original.unlink()
        item=next(item for item in entries(destination) if item["session_id"]==self.original.stem)
        self.assertTrue(item["archived"])
        self.assertIn("record 0",read(destination,item["id"],limit=1)["messages"][0]["text"])


if __name__ == "__main__":
    unittest.main()
