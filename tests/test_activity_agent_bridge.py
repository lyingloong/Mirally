"""Tests for the product-owned JSONL activity knowledge bridge."""

from __future__ import annotations

import hashlib
import io
import json
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from openassist.core.activity import Observation, ObservationStore
from openassist.runtime.activity.agent_bridge import ActivityAgentBridge, run_jsonl


class ActivityAgentBridgeTests(unittest.TestCase):
    def _history_bridge(self, root: Path, *, observed: int, text: str = "会议纪要", count: int = 1,
                        kind: str = "input_text") -> ActivityAgentBridge:
        config = root / "activity.yaml"
        database = root / "observations.sqlite3"
        config.write_text(
            f"enabled: true\nstorage:\n  database_path: {database.as_posix()}\n"
            f"  knowledge_database_path: {(root / 'knowledge.sqlite3').as_posix()}\n",
            encoding="utf-8",
        )
        with ObservationStore(database) as store:
            for seq in range(count):
                payload = {"inserted_text": text} if kind == "input_text" else {
                    "window_title": "Inbox", "browser_url": "https://mail.google.com",
                }
                store.append(Observation.create(kind=kind, source_id="fixture", source_epoch="epoch",
                                                source_seq=seq, boot_id="boot", observed_at_utc_ms=observed + seq,
                                                app_id="msedge.exe" if kind == "window_context" else None,
                                                window_instance_id="window-1" if kind == "window_context" else None,
                                                payload=payload))
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

    def test_health_exposes_retained_history_before_index_sync_without_claiming_live_collection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            # Late observations need not have timestamp order matching ingest
            # order; health must use the observation-time index for its bounds.
            bridge = self._history_bridge(Path(directory), observed=2000, count=2)
            with ObservationStore(bridge.config.database_path) as store:
                store.append(Observation.create(kind="input_text", source_id="late", source_epoch="epoch",
                    source_seq=0, boot_id="boot", observed_at_utc_ms=1000, payload={"inserted_text": "earlier"}))
            before = hashlib.sha256(bridge.config.database_path.read_bytes()).hexdigest()
            try:
                response = bridge.handle({"command": "health"})
                self.assertEqual(response["status"], "ready")
                self.assertTrue(response["profile_enabled"])
                self.assertFalse(response["bridge_starts_collector"])
                self.assertIsNone(response["collector_running"])
                history = response["result"]["history"]
                self.assertTrue(history["available"])
                self.assertEqual(history["observation_count"], 3)
                self.assertEqual(history["oldest_observed_utc_ms"], 1000)
                self.assertEqual(history["newest_observed_utc_ms"], 2001)
                self.assertEqual(history["indexed_item_count"], 0)
                self.assertIsNone(history["indexed_newest_observed_utc_ms"])
                collector = response["result"]["collector"]
                self.assertEqual(collector["recorded_status"], "unknown")
                self.assertEqual(collector["diagnostics_freshness"], "unknown")
                self.assertFalse(collector["diagnostics_available"])
                self.assertFalse(collector["process_liveness_confirmed"])
                self.assertIsNone(bridge._service.thread)
                self.assertIsNone(bridge._service.source._writer)
                self.assertEqual(bridge.handle({"command": "recent"})["data_status"], "empty")
                historical = bridge.handle({"command": "query", "args": {
                    "start_utc_ms": history["oldest_observed_utc_ms"],
                    "end_utc_ms": history["newest_observed_utc_ms"] + 1,
                }})
                self.assertEqual(len(historical["result"]["items"]), 3)
                indexed = bridge.handle({"command": "health"})["result"]["history"]
                self.assertEqual(indexed["indexed_item_count"], 3)
                self.assertEqual(indexed["indexed_oldest_observed_utc_ms"], 1000)
                self.assertEqual(indexed["indexed_newest_observed_utc_ms"], 2001)
            finally:
                bridge.close()
            self.assertEqual(hashlib.sha256(bridge.config.database_path.read_bytes()).hexdigest(), before)

    def test_health_distinguishes_recorded_collector_status_and_diagnostic_freshness(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bridge = self._history_bridge(Path(directory), observed=1000)
            try:
                for status, updated, freshness in (
                    ("running", 90_000, "recent"),
                    ("running", 60_000, "stale"),
                    ("degraded", 90_000, "recent"),
                    ("paused", 90_000, "recent"),
                    ("stopped", 60_000, "stale"),
                    ("running", 101_000, "future_timestamp"),
                ):
                    with self.subTest(status=status, updated=updated):
                        with ObservationStore(bridge.config.database_path) as store:
                            store.set_state("collector_diagnostics", {
                                "status": status, "started_at_utc_ms": 1000,
                                "stopped_at_utc_ms": 60_000 if status == "stopped" else None,
                                "updated_at_utc_ms": updated, "paused": status == "paused",
                                "sources": {"input_metadata": {"enabled": True, "available": False,
                                    "running": False, "reason": "input_worker_not_running"}},
                                "known_gap_counts": {"evidence_errors": 2},
                            })
                        with patch("openassist.runtime.activity.agent_bridge.time.time", return_value=100):
                            response = bridge.handle({"command": "health"})
                        collector = response["result"]["collector"]
                        self.assertEqual(collector["recorded_status"], status)
                        self.assertEqual(collector["diagnostics_freshness"], freshness)
                        self.assertEqual(collector["scope"], "last_recorded_collector_session")
                        self.assertFalse(collector["process_liveness_confirmed"])
                        self.assertIsNone(collector["running"])
                        self.assertEqual(collector["sources"]["input_metadata"]["reason"],
                                         "input_worker_not_running")
                        self.assertFalse(collector["sources"]["input_metadata"]["recorded_running"])
                        self.assertEqual(collector["known_gap_counts"]["evidence_errors"], 2)
                        self.assertEqual(collector["paused"], status == "paused")
                        self.assertTrue(response["result"]["history"]["available"])
            finally:
                bridge.close()

    def test_health_displays_verified_utc_and_host_local_dates_without_changing_query_timestamps(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            observed = 1_791_702_575_297
            bridge = self._history_bridge(Path(directory), observed=observed)
            try:
                history = bridge.handle({"command": "health"})["result"]["history"]
                expected_local = datetime(2026, 10, 11, 7, 9, 35, 297000,
                                          tzinfo=timezone.utc).astimezone().isoformat(timespec="milliseconds")
                for bound in ("oldest", "newest"):
                    self.assertEqual(history[f"{bound}_observed_utc_ms"], observed)
                    self.assertEqual(history[f"{bound}_observed_utc_iso"], "2026-10-11T07:09:35.297Z")
                    self.assertEqual(history[f"{bound}_observed_host_local_iso"], expected_local)
                self.assertEqual(history["host_local_timezone_basis"],
                                 "execution_host_timezone_may_differ_from_user")
                response = bridge.handle({"command": "query", "args": {
                    "start_utc_ms": history["oldest_observed_utc_ms"],
                    "end_utc_ms": history["newest_observed_utc_ms"] + 1,
                }})
                self.assertEqual(len(response["result"]["items"]), 1)
            finally:
                bridge.close()

    def test_health_leaves_absent_or_unrepresentable_dates_unknown(self) -> None:
        for observed, count in ((1000, 0), (2 ** 63 - 1, 1)):
            with self.subTest(observed=observed, count=count), tempfile.TemporaryDirectory() as directory:
                bridge = self._history_bridge(Path(directory), observed=observed, count=count)
                try:
                    response = bridge.handle({"command": "health"})
                    self.assertEqual(response["status"], "ready")
                    history = response["result"]["history"]
                    for bound in ("oldest", "newest"):
                        self.assertEqual(history[f"{bound}_observed_utc_ms"], observed if count else None)
                        self.assertIsNone(history[f"{bound}_observed_utc_iso"])
                        self.assertIsNone(history[f"{bound}_observed_host_local_iso"])
                finally:
                    bridge.close()

    def test_empty_observation_statistics_preserve_unknown_time_bounds(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "observations.sqlite3"
            with ObservationStore(database):
                pass
            before = hashlib.sha256(database.read_bytes()).hexdigest()
            with ObservationStore(database, read_only=True) as store:
                self.assertEqual(store.observation_statistics(), {"observation_count": 0,
                    "oldest_observed_utc_ms": None, "newest_observed_utc_ms": None})
            self.assertEqual(hashlib.sha256(database.read_bytes()).hexdigest(), before)

    def test_disabled_profile_does_not_inspect_or_deny_existing_history(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bridge = self._history_bridge(Path(directory), observed=1000)
            bridge.close()
            config = bridge.config_path
            config.write_text(config.read_text(encoding="utf-8").replace("enabled: true", "enabled: false"),
                              encoding="utf-8")
            before = hashlib.sha256(bridge.config.database_path.read_bytes()).hexdigest()
            bridge = ActivityAgentBridge(Path(directory), config)
            try:
                for command in ("health", "query"):
                    response = bridge.handle({"command": command})
                    self.assertEqual(response["status"], "disabled")
                    self.assertIsNone(response["result"]["history"]["available"])
                    self.assertFalse(response["result"]["history"]["availability_checked"])
                    self.assertIsNone(bridge._service)
            finally:
                bridge.close()
            self.assertEqual(hashlib.sha256(bridge.config.database_path.read_bytes()).hexdigest(), before)

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

    def test_events_expose_observed_activity_semantics_and_evidence_refs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bridge = self._history_bridge(Path(directory), observed=int(time.time() * 1000), kind="window_context")
            try:
                response = bridge.handle({"command": "events", "args": {"seconds": 60}})
                self.assertEqual(response["data_status"], "ok")
                result = response["result"]
                self.assertEqual(result["schema_version"], "activity.event.v1")
                self.assertIn("observed_window_activity", result["semantics"])
                self.assertTrue(result["unattributed_evidence"] or result["events"])
                evidence = result["unattributed_evidence"] or result["events"][0]["evidence_ids"]
                self.assertTrue(evidence)
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
                self.assertFalse(response["profile_enabled"])
                self.assertIsNone(response["history"]["available"])
                self.assertFalse(response["history"]["availability_checked"])
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

