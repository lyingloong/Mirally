"""The local event query groups fixture data without starting collection."""

from __future__ import annotations

import hashlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from openassist.core.activity import Observation, ObservationStore
from openassist.runtime.activity import cli


class ActivityEventsCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.raw = self.root / "observations.sqlite3"
        self.derived = self.root / "knowledge.sqlite3"
        self.config = self.root / "activity.yaml"
        self.config.write_text(
            "enabled: false\nstorage:\n"
            f"  database_path: {self.raw.as_posix()}\n"
            f"  knowledge_database_path: {self.derived.as_posix()}\n",
            encoding="utf-8",
        )
        self.rows = []
        with ObservationStore(self.raw) as source:
            fixtures = (
                ("window_context", {"payload": {"window_title": "Fixture 文档", "browser_url": "https://example.test/document"}}),
                ("context_snapshot", {"uia_summary": {"status": "ok", "nodes": [{"role": "Document", "text": "完整窗口文字 📝"}]}}),
                ("content_snapshot", {"payload": {"scope": "foreground"}, "ocr_summary": {"status": "ok", "text": "窗口识别正文"}}),
            )
            for index, (kind, fields) in enumerate(fixtures):
                self.rows.append(source.append(Observation.create(
                    kind=kind, source_id="fixture", source_epoch="epoch", source_seq=index,
                    boot_id="boot", app_id="fixture.exe", window_instance_id="fixture-window",
                    session_instance_id="fixture-session", observed_at_utc_ms=1000 + index,
                    **fields,
                )))
        self.raw_hash = hashlib.sha256(self.raw.read_bytes()).hexdigest()

    def tearDown(self) -> None:
        self.directory.cleanup()

    def run_cli(self, *arguments: str) -> dict:
        result = subprocess.run(
            [sys.executable, "-m", "openassist.runtime.activity.cli", "--config", str(self.config),
             "knowledge-events", *arguments],
            cwd=Path(__file__).resolve().parents[1], capture_output=True, encoding="utf-8", timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(hashlib.sha256(self.raw.read_bytes()).hexdigest(), self.raw_hash)
        return json.loads(result.stdout)

    def test_parser_event_defaults_include_window_text_without_changing_existing_queries(self) -> None:
        parser = cli.build_parser()
        args = parser.parse_args(["knowledge-events"])
        query = cli._knowledge_context(args)
        self.assertEqual((query.max_items, query.max_episodes, query.token_budget), (100, 30, 8000))
        self.assertTrue(query.include_uia)
        self.assertTrue(query.include_ocr)
        self.assertEqual(query.purpose, "diagnostics")
        for command in ("knowledge-query", "knowledge-timeline"):
            self.assertFalse(cli._knowledge_context(parser.parse_args([command])).include_uia)

    def test_parser_event_filters_and_content_switches(self) -> None:
        args = cli.build_parser().parse_args([
            "knowledge-events", "--query", "正文", "--start", "10", "--end", "20",
            "--app", "first.exe", "--app", "second.exe", "--window", "window",
            "--kind", "input", "--episode", "episode", "--after-item", "item",
            "--limit", "500", "--episodes", "100", "--tokens", "9000",
            "--include-assets", "--no-uia", "--no-ocr", "--sync", "--sync-limit", "3", "--sync-batches", "2",
        ])
        query = cli._knowledge_context(args)
        query.validate()
        self.assertEqual((query.query, query.start_utc_ms, query.end_utc_ms), ("正文", 10, 20))
        self.assertEqual(query.apps, ("first.exe", "second.exe"))
        self.assertEqual((query.windows, query.kinds, query.episode_id, query.after_item_id),
                         (("window",), ("input",), "episode", "item"))
        self.assertTrue(query.include_assets)
        self.assertFalse(query.include_uia or query.include_ocr)
        self.assertEqual((args.sync, args.sync_limit, args.sync_batches), (True, 3, 2))

    def test_real_cli_json_preserves_unicode_and_groups_window_state_and_text(self) -> None:
        result = self.run_cli("--sync")
        self.assertEqual(result["data_status"], "ok")
        self.assertEqual(len(result["events"]), 1)
        event = result["events"][0]
        self.assertEqual(set(event["source_observation_ids"]), {row.observation_id for row in self.rows})
        self.assertEqual(event["episode_item_count"], 3)
        self.assertFalse(event["partial_event"])
        self.assertTrue(event["window_states"])
        text = " ".join(value["excerpt"] for value in event["window_text"])
        self.assertIn("完整窗口文字 📝", text)
        self.assertIn("窗口识别正文", text)
        self.assertEqual(event["actions"], [])
        self.assertEqual(event["outcome"], "unconfirmed")

    def test_no_uia_and_no_ocr_remove_only_the_requested_window_text(self) -> None:
        self.run_cli("--sync")
        without_uia = self.run_cli("--no-uia")
        text = " ".join(value["excerpt"] for value in without_uia["events"][0]["window_text"])
        self.assertNotIn("完整窗口文字", text)
        self.assertIn("窗口识别正文", text)
        without_ocr = self.run_cli("--no-ocr")
        text = " ".join(value["excerpt"] for value in without_ocr["events"][0]["window_text"])
        self.assertIn("完整窗口文字", text)
        self.assertNotIn("窗口识别正文", text)
        without_both = self.run_cli("--no-uia", "--no-ocr")
        self.assertEqual(without_both["events"][0]["window_text"], [])
        self.assertTrue(without_both["events"][0]["evidence_ids"])

    def test_item_pagination_keeps_the_event_id_and_explicit_partial_event(self) -> None:
        first = self.run_cli("--sync", "--limit", "1")
        pages = [first]
        while pages[-1]["next_cursor"]:
            self.assertLess(len(pages), 4, "event pagination did not advance")
            pages.append(self.run_cli("--limit", "1", "--after-item", pages[-1]["next_cursor"]))
        self.assertEqual(len(pages), 3)
        events = [page["events"][0] for page in pages]
        self.assertEqual(len({event["event_id"] for event in events}), 1)
        self.assertTrue(all(event["partial_event"] for event in events))
        self.assertTrue(all(event["episode_item_count"] == 3 for event in events))
        items = [item for event in events for item in event["emitted_item_ids"]]
        self.assertEqual(len(items), len(set(items)))
        self.assertEqual(len(items), 3)
        self.assertEqual({ref for event in events for ref in event["source_observation_ids"]},
                         {row.observation_id for row in self.rows})

    def test_time_filter_reports_an_event_fragment_with_selected_evidence(self) -> None:
        self.run_cli("--sync")
        result = self.run_cli("--start", "1001", "--end", "1002")
        self.assertEqual(len(result["events"]), 1)
        event = result["events"][0]
        self.assertTrue(event["partial_event"])
        self.assertEqual(event["episode_item_count"], 3)
        self.assertIn(self.rows[1].observation_id, event["source_observation_ids"])
        self.assertNotIn(self.rows[2].observation_id, event["source_observation_ids"])
        self.assertEqual(len(event["emitted_item_ids"]), 1)
        self.assertFalse(result["truncated"])
        self.assertIsNone(result["next_cursor"])

    def test_app_filter_does_not_return_unmatched_window_events(self) -> None:
        result = self.run_cli("--sync", "--app", "another-fixture.exe")
        self.assertEqual(result["events"], [])
        self.assertIsNone(result["next_cursor"])

    def test_event_query_does_not_start_sensor_monitor_or_agent_services(self) -> None:
        output = io.StringIO()
        with patch.object(cli, "ActivitySensor", side_effect=AssertionError("collection started")), \
             patch.object(cli, "ActivityMonitorRuntime", side_effect=AssertionError("monitor started")), \
             patch.object(cli, "ActivityOrganizerService", side_effect=AssertionError("background service started")), \
             patch.object(cli, "_store", wraps=cli._store) as store, redirect_stdout(output):
            self.assertEqual(cli.main(["--config", str(self.config), "knowledge-events", "--sync"]), 0)
            self.assertTrue(all(call.kwargs.get("read_only") is True for call in store.call_args_list))
        self.assertEqual(len(json.loads(output.getvalue())["events"]), 1)
        self.assertEqual(hashlib.sha256(self.raw.read_bytes()).hexdigest(), self.raw_hash)

    def test_event_query_rejects_invalid_query_limits(self) -> None:
        errors = io.StringIO()
        with redirect_stderr(errors):
            self.assertEqual(cli.main(["--config", str(self.config), "knowledge-events", "--limit", "501"]), 1)
        self.assertIn("max_items must be between 1 and 500", errors.getvalue())


if __name__ == "__main__":
    unittest.main()
