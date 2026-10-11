"""Tests for the product-owned JSONL activity knowledge bridge."""

from __future__ import annotations

import io
import json
import tempfile
import time
import unittest
from pathlib import Path

from openassist.core.activity import Observation, ObservationStore
from openassist.runtime.activity.agent_bridge import ActivityAgentBridge, run_jsonl


class ActivityAgentBridgeTests(unittest.TestCase):
    def _history_bridge(self, root: Path, *, observed: int, text: str = "会议纪要", count: int = 1) -> ActivityAgentBridge:
        config = root / "activity.yaml"
        database = root / "observations.sqlite3"
        config.write_text(
            f"enabled: true\nstorage:\n  database_path: {database.as_posix()}\n"
            f"  knowledge_database_path: {(root / 'knowledge.sqlite3').as_posix()}\n",
            encoding="utf-8",
        )
        with ObservationStore(database) as store:
            for seq in range(count):
                store.append(Observation.create(kind="input_text", source_id="fixture", source_epoch="epoch",
                                                source_seq=seq, boot_id="boot", observed_at_utc_ms=observed + seq,
                                                payload={"inserted_text": text}))
        return ActivityAgentBridge(root, config)

    def test_unsynchronized_empty_index_reports_lag_until_caught_up(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bridge = self._history_bridge(Path(directory), observed=int(time.time() * 1000))
            try:
                self.assertEqual(bridge.handle({"command": "health"})["data_status"], "index_lagging")
                response = bridge.handle({"command": "query", "sync": False})
                self.assertEqual(response["data_status"], "index_lagging")
                self.assertEqual(response["result"]["items"], [])
                response = bridge.handle({"command": "query"})
                self.assertEqual(response["data_status"], "ok")
                self.assertEqual(len(response["result"]["items"]), 1)
                self.assertEqual(bridge.handle({"command": "health"})["data_status"], "ready")
            finally:
                bridge.close()

    def test_summary_defaults_to_recent_period_and_reports_empty_history_window(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bridge = self._history_bridge(Path(directory), observed=int(time.time() * 1000) - 600_000)
            try:
                response = bridge.handle({"command": "summary"})
                self.assertEqual(response["data_status"], "empty")
                self.assertEqual(response["result"]["item_count"], 0)
                span = response["result"]["time_range"]
                self.assertEqual(span["end_utc_ms"] - span["start_utc_ms"], 300_001)
                response = bridge.handle({"command": "summary", "args": {"seconds": 900}})
                self.assertEqual(response["result"]["item_count"], 1)
            finally:
                bridge.close()

    def test_budget_blocked_matches_are_distinct_from_no_matches(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bridge = self._history_bridge(Path(directory), observed=1000, text="会议纪要" * 800)
            try:
                response = bridge.handle({"command": "query", "args": {"mode": "evidence", "token_budget": 512}})
                self.assertEqual(response["data_status"], "budget_exceeded")
                self.assertTrue(response["result"]["truncated"])
                self.assertIn("retry_hint", response["result"])
                response = bridge.handle({"command": "query", "args": {"mode": "outline", "token_budget": 4000}})
                self.assertEqual(len(response["result"]["items"]), 1)
            finally:
                bridge.close()

    def test_partial_outline_page_is_usable_and_not_budget_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bridge = self._history_bridge(Path(directory), observed=1000, count=2)
            try:
                response = bridge.handle({"command": "query", "args": {"max_items": 1}})
                self.assertEqual(response["data_status"], "ok")
                self.assertEqual(len(response["result"]["items"]), 1)
                self.assertTrue(response["result"]["truncated"])
                self.assertTrue(response["result"]["next_cursor"])
            finally:
                bridge.close()

    def test_disabled_bridge_does_not_create_a_store(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bridge = ActivityAgentBridge(root)
            try:
                response = bridge.handle({"command": "health"})
                self.assertEqual(response["status"], "disabled")
                self.assertFalse(response["enabled"])
                # The disabled configuration resolves to the platform data
                # directory, which may already contain a user's database.
                # The important contract is that this request never opens a
                # service (and therefore cannot create or mutate that store).
                self.assertIsNone(bridge._service)
            finally:
                bridge.close()

    def test_enabled_bridge_projects_existing_observations_on_demand(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "observations.sqlite3"
            assets = root / "assets"
            knowledge = root / "activity-knowledge.sqlite3"
            config = root / "activity.yaml"
            config.write_text(
                "\n".join(
                    [
                        "enabled: true",
                        "storage:",
                        f"  database_path: {database.as_posix()}",
                        f"  directory: {assets.as_posix()}",
                        f"  knowledge_database_path: {knowledge.as_posix()}",
                    ]
                ),
                encoding="utf-8",
            )
            observed = int(time.time() * 1000)
            with ObservationStore(database, asset_directory=assets) as store:
                store.append(
                    Observation.create(
                        kind="window_context",
                        source_id="test",
                        source_epoch="epoch",
                        source_seq=0,
                        boot_id="boot",
                        app_id="msedge.exe",
                        window_instance_id="window-1",
                        observed_at_utc_ms=observed,
                        payload={"window_title": "Inbox", "browser_url": "https://mail.google.com"},
                    )
                )
            bridge = ActivityAgentBridge(root, config)
            try:
                response = bridge.handle({"id": "1", "command": "recent", "args": {"seconds": 60}, "sync": True})
                self.assertEqual(response["status"], "ready")
                self.assertEqual(response["data_status"], "ok")
                self.assertEqual(len(response["result"]["items"]), 1)
                self.assertEqual(response["result"]["items"][0]["title"], "Inbox")
                self.assertGreaterEqual(response["sync"]["projected"], 1)
                self.assertTrue(knowledge.exists())
            finally:
                bridge.close()

    def test_queries_are_bounded_and_invalid_protocol_is_reported_per_line(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bridge = ActivityAgentBridge(root)
            input_stream = io.StringIO(
                "\n".join(
                    [
                        json.dumps({"id": "bad", "command": "search", "args": {"query": ""}}),
                        json.dumps({"id": "unknown", "command": "future_command", "args": {}}),
                        "not-json",
                    ]
                )
                + "\n"
            )
            output_stream = io.StringIO()
            try:
                self.assertEqual(run_jsonl(bridge, input_stream, output_stream), 0)
            finally:
                # run_jsonl closes the bridge; this is intentionally safe and
                # also verifies repeated close in the fixture teardown path.
                bridge.close()
            responses = [json.loads(line) for line in output_stream.getvalue().splitlines()]
            self.assertEqual(len(responses), 3)
            self.assertTrue(responses[0]["ok"])
            self.assertEqual(responses[0]["status"], "disabled")
            self.assertFalse(responses[1]["ok"])
            self.assertEqual(responses[1]["error"]["type"], "ValueError")
            self.assertFalse(responses[2]["ok"])
            self.assertEqual(responses[2]["error"]["type"], "JSONDecodeError")

    def test_missing_store_reports_not_ready_without_creating_database(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "activity.yaml"
            database = root / "observations.sqlite3"
            config.write_text(
                f"enabled: true\nstorage:\n  database_path: {database.as_posix()}\n",
                encoding="utf-8",
            )
            bridge = ActivityAgentBridge(root, config)
            try:
                response = bridge.handle({"command": "recent", "args": {}})
                self.assertEqual(response["status"], "not_ready")
                self.assertFalse(database.exists())
            finally:
                bridge.close()

    def test_summary_and_episode_metadata_cannot_bypass_response_budget(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bridge = self._history_bridge(Path(directory), observed=int(time.time() * 1000))
            try:
                query = bridge.handle({"command": "query"})
                episode_id = query["result"]["episodes"][0]["episode_id"]
                for command, params in (("summary", {}), ("episode", {"episode_id": episode_id})):
                    response = bridge.handle({"command": command, "args": {**params, "token_budget": 128}})
                    self.assertEqual(response["data_status"], "budget_exceeded")
                    self.assertTrue(response["result"]["truncated"])
                    self.assertLessEqual(len(json.dumps(response["result"], ensure_ascii=False).encode("utf-8")), 128 * 3)
            finally:
                bridge.close()


if __name__ == "__main__":
    unittest.main()

