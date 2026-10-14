"""Real MCP memory reads use already produced memories, not model calls."""

import asyncio
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest

from fastmcp import Client
from fastmcp.client.transports import StdioTransport

from openassist.runtime.memory.mcp_server import create_server


ROOT = Path(__file__).resolve().parents[1]


class MemoryMcpTests(unittest.TestCase):
    def test_discovery_is_lazy_and_disabled_profile_creates_no_store(self):
        async def run():
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                config = root / "memory.yaml"
                config.write_text("invalid: [\n", encoding="utf-8")
                async with Client(create_server(root, config)) as client:
                    tools = await client.list_tools()
                    self.assertEqual(len(tools), 5)
                    read_tools = {tool.name for tool in tools if tool.annotations.readOnlyHint}
                    self.assertEqual(read_tools, {"memory_health", "memory_recall", "memory_read"})
                config.write_text("enabled: false\n", encoding="utf-8")
                async with Client(create_server(root, config)) as client:
                    result = (await client.call_tool("memory_health", {})).data
                    self.assertEqual(result["status"], "disabled")
                self.assertEqual(list(root.iterdir()), [config])
        asyncio.run(run())

    def test_stdio_reads_independent_memory_without_processing_or_mutating_source(self):
        from openassist.core.memory import MemoryStore

        async def run():
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                database = root / "memory.sqlite3"
                config = root / "memory.yaml"
                config.write_text(f"enabled: true\nstorage:\n  database_path: {database.as_posix()}\n",
                                  encoding="utf-8")
                with MemoryStore(database) as store:
                    store.put_memory({"id": "meeting", "kind": "episodic", "status": "active",
                                      "title": "项目会议", "content": "观察到打开项目会议文档。",
                                      "provenance": "observed", "source_observation_ids": ["observation-1"],
                                      "observed_start_utc_ms": 1000, "observed_end_utc_ms": 2000})
                before = hashlib.sha256(database.read_bytes()).hexdigest()
                transport = StdioTransport(command=sys.executable,
                    args=["-m", "openassist.runtime.memory.mcp_server", "--root", str(root), "--config", str(config)],
                    cwd=str(ROOT), env={"PYTHONUTF8": "1"}, keep_alive=False)
                async with Client(transport) as client:
                    recalled = (await client.call_tool("memory_recall", {"query": "项目会议"})).data
                    self.assertEqual(recalled["status"], "ready")
                    self.assertEqual(recalled["memories"][0]["id"], "meeting")
                    read = (await client.call_tool("memory_read", {"memory_id": "meeting"})).data
                    self.assertIn("observation-1", str(read))
                self.assertEqual(hashlib.sha256(database.read_bytes()).hexdigest(), before)
                self.assertFalse((root / "knowledge.sqlite3").exists())
        asyncio.run(run())

    def test_forget_suppresses_regeneration_and_proposals_need_review(self):
        from openassist.core.memory import MemoryStore

        async def run():
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                database = root / "memory.sqlite3"
                config = root / "memory.yaml"
                config.write_text(f"enabled: true\nstorage:\n  database_path: {database.as_posix()}\n", encoding="utf-8")
                record = {"id": "seen", "kind": "episodic", "content": "观察到页面",
                          "source_observation_ids": ["obs"], "provenance": "observed"}
                with MemoryStore(database) as store:
                    store.put_memory(record)
                async with Client(create_server(root, config)) as client:
                    await client.call_tool("memory_forget", {"memory_id": "seen"})
                    candidate = (await client.call_tool("memory_propose", {"statement": "项目使用某数据库"})).data
                    self.assertEqual(candidate["memory"]["status"], "candidate")
                    self.assertEqual((await client.call_tool("memory_recall", {"query": "数据库"})).data["memories"], [])
                with MemoryStore(database) as store:
                    result = store.put_memory({**record, "id": "different-derived-id"})
                    self.assertTrue(result["blocked_by_tombstone"])
        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
