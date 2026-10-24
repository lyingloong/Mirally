"""Manual-memory creation retries use the durable Memory owner, not a second ledger."""
from __future__ import annotations

import hashlib
from pathlib import Path
import tempfile
import unittest

from mirally.core.memory.store import MemoryStore
from mirally.framework import FrameworkError, FrameworkService


class FrameworkMemoryReplayTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="Mirally 记忆 replay ")
        self.root = Path(self.temporary.name)
        self.home = self.root / "personal data"
        self.product = FrameworkService(self.root, self.home)
        self.memory_path = self.home / "memory" / "memory.sqlite3"

    def tearDown(self):
        self.product.close()
        self.temporary.cleanup()

    def rpc(self, method, **params):
        return self.product.call(method, params)

    def restart(self):
        self.product.close()
        self.product = FrameworkService(self.root, self.home)

    def memory_snapshot(self, memory_id):
        with MemoryStore(self.memory_path, read_only=True) as store:
            return store.get_memory(memory_id, include_inactive=True), store.revisions(memory_id)

    def test_lost_create_response_replays_after_restart_without_rewriting_owner(self):
        params = {"content": "我喜欢绿茶\n不加糖", "title": "口味", "request_id": "lost-response-one"}
        first = self.rpc("memory.correct", **params)
        before = self.memory_snapshot(first["memory"]["id"])
        digest = hashlib.sha256(self.memory_path.read_bytes()).hexdigest()
        self.restart()
        replay = self.rpc("memory.correct", **params)
        self.assertEqual(replay, first)
        self.assertEqual(self.memory_snapshot(first["memory"]["id"]), before)
        self.assertEqual(hashlib.sha256(self.memory_path.read_bytes()).hexdigest(), digest)
        self.assertEqual([item["id"] for item in self.rpc("memory.search", query="绿茶")["memories"]], [first["memory"]["id"]])

    def test_reusing_create_identity_with_changed_content_or_title_conflicts(self):
        params = {"content": "原始内容", "title": "原始标题", "request_id": "fixed-create"}
        first = self.rpc("memory.correct", **params)
        before = self.memory_snapshot(first["memory"]["id"])
        for patch in ({"content": "新内容"}, {"title": "新标题"}, {"title": None}):
            with self.subTest(patch=patch):
                with self.assertRaises(FrameworkError) as raised:
                    self.rpc("memory.correct", **{**params, **patch})
                self.assertEqual(raised.exception.code, "CONFLICT")
                self.assertEqual(self.memory_snapshot(first["memory"]["id"]), before)

    def test_replay_does_not_restore_a_withdrawn_memory_source(self):
        params = {"content": "撤回后不要再次使用", "request_id": "withdrawn-create"}
        first = self.rpc("memory.correct", **params)
        source = self.rpc("source.set", source_id="memory:" + first["memory"]["id"], enabled=False)
        before = self.memory_snapshot(first["memory"]["id"])
        self.restart()
        replay = self.rpc("memory.correct", **params)
        self.assertFalse(replay["source"]["enabled"])
        self.assertEqual(replay["source"]["version"], source["version"])
        self.assertEqual(replay["affected"], [])
        self.assertEqual(self.memory_snapshot(first["memory"]["id"]), before)
        self.assertEqual(self.rpc("memory.search", query="撤回")["memories"], [])

    def test_old_create_retry_cannot_overwrite_a_later_explicit_correction(self):
        params = {"content": "原来的偏好", "request_id": "then-corrected"}
        first = self.rpc("memory.correct", **params)
        corrected = self.rpc("memory.correct", memory_id=first["memory"]["id"], content="现在的偏好")
        self.restart()
        with self.assertRaises(FrameworkError) as raised:
            self.rpc("memory.correct", **params)
        self.assertEqual(raised.exception.code, "CONFLICT")
        self.assertEqual(self.memory_snapshot(first["memory"]["id"])[0], corrected["memory"])

    def test_distinct_requests_can_create_distinct_memories_with_identical_text(self):
        first = self.rpc("memory.correct", content="same text", request_id="intent-one")["memory"]
        second = self.rpc("memory.correct", content="same text", request_id="intent-two")["memory"]
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(len(self.rpc("memory.search", query="same text")["memories"]), 2)

    def test_invalid_request_identity_does_not_create_a_memory_database(self):
        for request_id in ("", "  ", "x" * 513, 7):
            with self.subTest(request_id=request_id):
                with self.assertRaises(FrameworkError) as raised:
                    self.rpc("memory.correct", content="not saved", request_id=request_id)
                self.assertEqual(raised.exception.code, "INVALID_PARAMS")
                self.assertFalse(self.memory_path.exists())


if __name__ == "__main__":
    unittest.main()
