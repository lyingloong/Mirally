"""Protocol tests for the product-owned Activity MCP adapter."""

from __future__ import annotations

import asyncio
import hashlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastmcp import Client
from fastmcp.client.transports import StdioTransport
from fastmcp.exceptions import ToolError

from mirally.core.activity import Observation, ObservationStore
from mirally.runtime.activity.mcp_server import create_server


ROOT = Path(__file__).resolve().parents[1]


class ActivityMcpServerTests(unittest.TestCase):
    def test_exposes_bounded_read_only_activity_surface(self) -> None:
        async def run() -> None:
            with tempfile.TemporaryDirectory() as directory, patch(
                    "mirally.runtime.activity.config.default_data_directory") as data_directory:
                root = Path(directory)
                data_directory.return_value = root / "data"
                async with Client(create_server(root)) as client:
                    tools = await client.list_tools()
                    names = [tool.name for tool in tools]
                    self.assertEqual(
                        names,
                        [
                            "activity_health", "activity_recent", "activity_search",
                            "activity_around", "activity_summary", "activity_query",
                            "activity_events", "activity_episode", "activity_evidence",
                        ],
                    )
                    events_tool = next(tool for tool in tools if tool.name == "activity_events")
                    self.assertEqual(
                        events_tool.inputSchema["properties"]["mode"]["default"],
                        "evidence",
                    )
                    self.assertTrue(all(tool.annotations.readOnlyHint for tool in tools))
                    health = await client.call_tool("activity_health", {})
                    self.assertTrue(health.data["enabled"])
                    self.assertFalse(health.data["bridge_starts_collector"])
                    self.assertEqual(health.data["data_status"], "not_ready")
                    self.assertEqual(list(root.iterdir()), [])

        asyncio.run(run())

    def test_servers_keep_separate_profiles_and_close_their_bridges(self) -> None:
        async def run() -> None:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                config_a = root / "disabled.yaml"
                config_b = root / "enabled.yaml"
                config_a.write_text("enabled: false\n", encoding="utf-8")
                config_b.write_text(
                    f"enabled: true\nstorage:\n  database_path: {(root / 'missing.sqlite3').as_posix()}\n",
                    encoding="utf-8",
                )
                server_a = create_server(root, config_a)
                server_b = create_server(root, config_b)
                async with Client(server_a) as a, Client(server_b) as b:
                    self.assertEqual((await a.call_tool("activity_health", {})).data["status"], "disabled")
                    self.assertEqual((await b.call_tool("activity_health", {})).data["status"], "not_ready")
                    self.assertEqual((await a.call_tool("activity_health", {})).data["status"], "disabled")
                # A new session must reopen the profile after lifecycle cleanup.
                config_a.write_text(config_b.read_text(encoding="utf-8"), encoding="utf-8")
                async with Client(server_a) as a:
                    self.assertEqual((await a.call_tool("activity_health", {})).data["status"], "not_ready")
                self.assertFalse((root / "missing.sqlite3").exists())

        asyncio.run(run())

    def test_discovery_does_not_load_activity_configuration(self) -> None:
        async def run() -> None:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                config = root / "activity.yaml"
                config.write_text("invalid: [\n", encoding="utf-8")
                async with Client(create_server(root, config)) as client:
                    self.assertEqual(len(await client.list_tools()), 9)
                self.assertEqual(list(root.iterdir()), [config])

        asyncio.run(run())

    def test_stdio_queries_real_history_without_modifying_source(self) -> None:
        async def run() -> None:
            with tempfile.TemporaryDirectory(prefix="mirally-mcp-") as directory:
                root = Path(directory)
                database = root / "observations.sqlite3"
                knowledge = root / "knowledge.sqlite3"
                config = root / "历史.yaml"
                config.write_text(
                    f"enabled: true\nstorage:\n  database_path: {database.as_posix()}\n"
                    f"  knowledge_database_path: {knowledge.as_posix()}\n",
                    encoding="utf-8",
                )
                with ObservationStore(database) as store:
                    observation = store.append(Observation.create(
                        kind="window_context", source_id="fixture", source_epoch="epoch",
                        source_seq=1, boot_id="boot", observed_at_utc_ms=1000,
                        app_id="msedge.exe", window_instance_id="window-1",
                        payload={"window_title": "会议页面", "browser_url": "https://example.com/meeting"},
                    ))
                before = hashlib.sha256(database.read_bytes()).hexdigest()
                transport = StdioTransport(
                    command=sys.executable,
                    args=["-m", "mirally.runtime.activity.mcp_server", "--root", str(root), "--config", str(config)],
                    cwd=str(ROOT),
                    env={"PYTHONUTF8": "1"},
                    keep_alive=False,
                )
                async with Client(transport, timeout=30) as client:
                    self.assertEqual(len(await client.list_tools()), 9)
                    self.assertFalse(knowledge.exists())
                    result = (await client.call_tool("activity_query", {})).data
                    self.assertEqual(result["data_status"], "ok")
                    item = result["result"]["items"][0]
                    self.assertEqual(item["title"], "会议页面")
                    self.assertIn(observation.observation_id, item["source_observation_ids"])
                    summary = (await client.call_tool("activity_summary", {
                        "start_utc_ms": 1000, "end_utc_ms": 2000,
                    })).data
                    self.assertEqual(summary["data_status"], "ok")
                    self.assertEqual(summary["result"]["item_count"], 1)
                    self.assertIn(observation.observation_id, summary["result"]["source_observation_ids"])
                    with self.assertRaises(ToolError):
                        await client.call_tool("activity_query", {"start_utc_ms": 2000, "end_utc_ms": 1000})
                    self.assertEqual((await client.call_tool("activity_health", {})).data["status"], "ready")
                self.assertTrue(knowledge.exists())
                self.assertEqual(hashlib.sha256(database.read_bytes()).hexdigest(), before)

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
