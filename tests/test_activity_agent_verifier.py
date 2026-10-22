"""Regression checks for verification of the configured Activity transport."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from mirally.core.activity import Observation, ObservationStore
from mirally.runtime.activity.agent_bridge import ActivityAgentBridge
from tools.verify_activity_agent import decode_tool_response, gateway_caller, source_fingerprint


ROOT = Path(__file__).resolve().parents[1]


class ActivityAgentVerifierTests(unittest.TestCase):
    def _history(self, root: Path) -> Path:
        root.mkdir(parents=True, exist_ok=True)
        database = root / "observations.sqlite3"
        config = root / "历史.yaml"
        config.write_text(
            f"enabled: true\nstorage:\n  database_path: {database.as_posix()}\n"
            f"  knowledge_database_path: {(root / 'knowledge.sqlite3').as_posix()}\n",
            encoding="utf-8",
        )
        with ObservationStore(database) as store:
            store.append(Observation.create(
                kind="window_context", source_id="verifier-fixture", source_epoch="epoch",
                source_seq=1, boot_id="boot", observed_at_utc_ms=1_700_000_000_000,
                app_id="msedge.exe", window_instance_id="window-1",
                payload={"window_title": "会议页面", "browser_url": "https://example.com/meeting"},
            ))
        return config

    def _saved_config(self, root: Path, activity_config: Path) -> Path:
        saved = root / "openclaw.json"
        saved.write_text(json.dumps({"mcp": {"servers": {"mirally-activity": {
            "command": sys.executable,
            "args": ["-m", "mirally.runtime.activity.mcp_server", "--root", str(root),
                     "--config", str(activity_config)],
            "cwd": str(ROOT), "requestTimeoutMs": 15000, "connectionTimeoutMs": 15000,
        }}}}), encoding="utf-8")
        return saved

    def _run(self, activity_config: Path, saved: Path, report: Path) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(ROOT / "tools/verify_activity_agent.py"),
             "--activity-config", str(activity_config), "--gateway-config", str(saved),
             "--mcp", "--report", str(report)],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=45,
        )

    def test_verifier_calls_all_tools_over_saved_stdio_and_preserves_source(self) -> None:
        with tempfile.TemporaryDirectory(prefix="mirally-verifier-") as directory:
            root = Path(directory)
            activity_config = self._history(root)
            saved = self._saved_config(root, activity_config)
            report = root / "verification.json"
            database = root / "observations.sqlite3"
            before = source_fingerprint(database)
            completed = self._run(activity_config, saved, report)
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            result = json.loads(report.read_text(encoding="utf-8"))
            self.assertTrue(result["passed"])
            self.assertEqual(len(result["mcp_tools"]), 9)
            self.assertEqual(result["mcp_tools"], result["local_tools"])
            self.assertEqual(result["mcp_tools"]["activity_search"]["item_count"], 1)
            self.assertGreater(result["mcp_tools"]["activity_events"]["event_count"], 0)
            self.assertEqual(result["gateway_tools"], {})
            self.assertEqual(source_fingerprint(database), before)

    def test_verifier_rejects_an_unavailable_saved_profile_without_a_success_report(self) -> None:
        with tempfile.TemporaryDirectory(prefix="mirally-verifier-unavailable-") as directory:
            root = Path(directory)
            activity_config = self._history(root)
            other = root / "disabled.yaml"
            other.write_text(
                f"enabled: false\nstorage:\n  database_path: {(root / 'absent.sqlite3').as_posix()}\n",
                encoding="utf-8",
            )
            saved = self._saved_config(root, other)
            report = root / "verification.json"
            before = source_fingerprint(root / "observations.sqlite3")
            completed = self._run(activity_config, saved, report)
            self.assertEqual(completed.returncode, 1, completed.stdout + completed.stderr)
            self.assertIn("MCP activity tool is not ready: activity_health", completed.stdout)
            self.assertFalse(report.exists())
            self.assertFalse((root / "absent.sqlite3").exists())
            self.assertEqual(source_fingerprint(root / "observations.sqlite3"), before)

    def test_managed_mcp_cannot_be_claimed_as_legacy_gateway_verification(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            saved = self._saved_config(root, root / "unused.yaml")
            with self.assertRaisesRegex(RuntimeError, "use --mcp"):
                gateway_caller(saved)
        payload = {"status": "ready", "data_status": "ok", "result": {"items": []}}
        self.assertEqual(decode_tool_response({"details": {
            "mcpServer": "mirally-activity", "structuredContent": payload,
        }}), payload)
        with self.assertRaisesRegex(RuntimeError, "invocation failed"):
            decode_tool_response({"isError": True, "structuredContent": payload})

    def test_verifier_rejects_another_ready_dataset_with_the_same_item_count(self) -> None:
        with tempfile.TemporaryDirectory(prefix="mirally-verifier-mismatch-") as directory:
            root = Path(directory)
            activity_config = self._history(root)
            other = self._history(root / "other")
            bridge = ActivityAgentBridge(ROOT, other)
            try:
                self.assertEqual(bridge.handle({"command": "query"})["status"], "ready")
            finally:
                bridge.close()
            saved = self._saved_config(root, other)
            report = root / "verification.json"
            completed = self._run(activity_config, saved, report)
            self.assertEqual(completed.returncode, 1, completed.stdout + completed.stderr)
            self.assertIn("MCP response differs from the current local activity bridge: activity_health", completed.stdout)
            self.assertFalse(report.exists())


if __name__ == "__main__":
    unittest.main()
