from __future__ import annotations

import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from mirally.core.activity.models import CollectionLevel
from mirally.core.activity.policy import ActivityPolicy
from mirally.core.activity.store import ObservationStore
from mirally.platform.activity.foreground import ForegroundSnapshot, WindowsForegroundSource
from mirally.platform.activity.input_metadata import InputBatch
from mirally.platform.activity.uia_structure import UIAStructureResult
from mirally.runtime.activity.config import ActivityConfig
from mirally.runtime.activity.service import ActivitySensor


class ForegroundFixture:
    available = True

    def __init__(self):
        self.snapshot = ForegroundSnapshot(
            hwnd=101, pid=201, process_instance_id="fixture-process",
            window_instance_id="fixture-window", app_id="fixture.exe",
            observed_at_utc_ms=int(time.time() * 1000), monotonic_ns=time.monotonic_ns(),
            identity_confidence="verified_process_start", boot_id="fixture-boot",
            title_available=True, window_title="Private fixture document",
        )

    def matches(self, snapshot):
        return self.snapshot.window_instance_id == snapshot.window_instance_id


class TextFixture:
    available = True

    def __init__(self):
        self.text = ""
        self.calls = 0

    def inspect(self, hwnd, **kwargs):
        self.calls += 1
        return UIAStructureResult(
            "ok", node_count=1, nodes=({"role": "EditControl", "value": self.text},),
            focused_control={"control_id": "fixture-edit", "role": "EditControl",
                             "text_status": "ok", "value": self.text},
            content_enabled=bool(kwargs.get("include_content")),
        )


class InputTimingTests(unittest.TestCase):
    def make_sensor(self, root, *, source=None, text=None, policy=None):
        source = source or ForegroundFixture()
        config = replace(
            ActivityConfig.disabled(root), enabled=True,
            database_path=root / "observations.sqlite3", asset_directory=root / "assets",
            policy=policy or ActivityPolicy(enabled=True, default_level=CollectionLevel.CONTENT,
                                           exclude_self=False),
            input_enabled=False, visual_enabled=False, ocr_enabled=False,
            clipboard_enabled=False, browser_urls_enabled=False,
            ui_structure_enabled=text is not None, uia_content_enabled=text is not None,
            input_text_enabled=text is not None,
        )
        sensor = ActivitySensor(
            config, source=source, uia_source=text,
            monitor_source=SimpleNamespace(available=True),
        )
        sensor.start()
        return sensor

    @staticmethod
    def batch(**changes):
        return replace(InputBatch(
            keyboard_count=3, click_count=1, scroll_count=2, motion=True,
            started_monotonic_ns=9_007_199_254_740_991,
            ended_monotonic_ns=9_007_199_254_750_123, quiet_ms=1500,
            hwnd=101, pid=201, observed_at_utc_ms=1_234_567_890,
        ), **changes)

    def test_full_batch_timing_and_identity_survive_storage_reopen(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sensor = self.make_sensor(root)
            sensor._previous_foreground = sensor.source.snapshot
            sensor._submit = Mock()
            batch = self.batch()
            try:
                with patch("mirally.runtime.activity.service.time.time", return_value=2_000_000):
                    observation = sensor.record_input_metadata(batch)
                self.assertIsNotNone(observation)
                self.assertEqual(observation.observed_at_utc_ms, 2_000_000_000)
                self.assertNotEqual(observation.observed_at_utc_ms, batch.observed_at_utc_ms)
                observation_id = observation.observation_id
            finally:
                sensor.close()
            with ObservationStore(root / "observations.sqlite3", asset_directory=root / "assets",
                                  read_only=True) as store:
                stored = store.get(observation_id)
                self.assertEqual(stored.payload["started_monotonic_ns"], batch.started_monotonic_ns)
                self.assertEqual(stored.payload["ended_monotonic_ns"], batch.ended_monotonic_ns)
                self.assertEqual(stored.payload["helper_emitted_at_utc_ms"], batch.observed_at_utc_ms)
                self.assertEqual(stored.time_quality, "unknown")
                self.assertEqual(stored.identity_confidence, "verified_process_start")
                self.assertEqual(stored.window_instance_id, "fixture-window")
                self.assertEqual(stored.payload["keyboard_count"], 3)

    def test_zero_source_times_remain_explicit_unknown_values(self):
        with tempfile.TemporaryDirectory() as directory:
            sensor = self.make_sensor(Path(directory))
            sensor._previous_foreground = sensor.source.snapshot
            sensor._submit = Mock()
            try:
                observation = sensor.record_input_metadata(self.batch(
                    started_monotonic_ns=0, ended_monotonic_ns=0, observed_at_utc_ms=0,
                ))
                self.assertEqual(observation.payload["started_monotonic_ns"], 0)
                self.assertEqual(observation.payload["ended_monotonic_ns"], 0)
                self.assertEqual(observation.payload["helper_emitted_at_utc_ms"], 0)
                self.assertEqual(observation.time_quality, "unknown")
            finally:
                sensor.close()

    def test_changed_current_window_rejection_is_counted_and_does_not_capture(self):
        with tempfile.TemporaryDirectory() as directory:
            sensor = self.make_sensor(Path(directory))
            original = sensor.source.snapshot
            sensor._previous_foreground = original
            sensor.source.snapshot = replace(original, hwnd=102, pid=202,
                                             window_instance_id="new-window")
            sensor._submit = Mock()
            try:
                self.assertIsNone(sensor.record_input_metadata(self.batch()))
                sensor.stop()
                self.assertEqual(sensor.store.get_state("collector_diagnostics")
                                 ["known_gap_counts"]["unattributed_input_batches"], 1)
                self.assertFalse(sensor.store.query(kinds=["input_batch"]))
                sensor._submit.assert_not_called()
            finally:
                sensor.close()

    def test_other_window_identity_rejection_is_counted_once(self):
        with tempfile.TemporaryDirectory() as directory:
            sensor = self.make_sensor(Path(directory))
            sensor._previous_foreground = sensor.source.snapshot
            sensor._submit = Mock()
            try:
                self.assertIsNone(sensor.record_input_metadata(self.batch(hwnd=999, pid=999)))
                sensor.stop()
                self.assertEqual(sensor.store.get_state("collector_diagnostics")
                                 ["known_gap_counts"]["unattributed_input_batches"], 1)
                self.assertFalse(sensor.store.query(kinds=["input_batch"]))
                sensor._submit.assert_not_called()
            finally:
                sensor.close()

    def test_caption_change_during_live_validation_preserves_input_batch(self):
        with tempfile.TemporaryDirectory() as directory:
            initial = ForegroundFixture().snapshot
            latest = replace(initial, window_title="Fixture document edited")
            source = WindowsForegroundSource()
            source.poll = Mock(side_effect=[initial, latest, latest])
            sensor = self.make_sensor(Path(directory), source=source)
            sensor._submit = Mock()
            try:
                observation = sensor.record_input_metadata(self.batch())
                self.assertIsNotNone(observation)
                self.assertEqual(observation.window_instance_id, initial.window_instance_id)
                self.assertEqual(sensor.metrics_snapshot().get("unattributed_input_batches", 0), 0)
                self.assertEqual(sensor._submit.call_args.args[0].window_title, latest.window_title)
                self.assertEqual(source.poll.call_count, 2)
            finally:
                sensor.close()

    def test_new_private_caption_during_validation_rejects_input(self):
        with tempfile.TemporaryDirectory() as directory:
            initial = replace(ForegroundFixture().snapshot, window_title="Public fixture")
            source = WindowsForegroundSource()
            source.poll = Mock(side_effect=[initial, replace(initial, window_title="Private fixture")])
            policy = ActivityPolicy(enabled=True, default_level=CollectionLevel.CONTENT,
                                    exclude_self=False, excluded_windows=("private*",))
            sensor = self.make_sensor(Path(directory), source=source, policy=policy)
            sensor._submit = Mock()
            try:
                self.assertIsNone(sensor.record_input_metadata(self.batch()))
                self.assertFalse(sensor.store.query(kinds=["input_batch"]))
                self.assertEqual(sensor.metrics_snapshot()["input_batch_rejections"]["privacy_denied"], 1)
                sensor._submit.assert_not_called()
            finally:
                sensor.close()

    def test_window_lifetime_change_during_validation_rejects_input(self):
        with tempfile.TemporaryDirectory() as directory:
            initial = ForegroundFixture().snapshot
            source = WindowsForegroundSource()
            source.poll = Mock(side_effect=[initial, replace(initial, window_instance_id="replacement-window")])
            sensor = self.make_sensor(Path(directory), source=source)
            sensor._submit = Mock()
            try:
                self.assertIsNone(sensor.record_input_metadata(self.batch()))
                self.assertFalse(sensor.store.query(kinds=["input_batch"]))
                self.assertEqual(sensor.metrics_snapshot()["input_batch_rejections"]
                                 ["foreground_changed_during_validation"], 1)
                sensor._submit.assert_not_called()
            finally:
                sensor.close()

    def test_excluded_app_and_window_batches_are_never_saved_or_captured(self):
        exclusions = ({"excluded_apps": ("fixture.exe",)},
                      {"excluded_windows": ("private*",)})
        for fields in exclusions:
            with self.subTest(fields=fields), tempfile.TemporaryDirectory() as directory:
                policy = ActivityPolicy(enabled=True, default_level=CollectionLevel.CONTENT,
                                        exclude_self=False, **fields)
                text = TextFixture()
                sensor = self.make_sensor(Path(directory), policy=policy, text=text)
                sensor._previous_foreground = sensor.source.snapshot
                sensor._submit = Mock()
                try:
                    self.assertIsNone(sensor.record_input_metadata(self.batch()))
                    self.assertFalse(sensor.store.query(kinds=["input_batch"]))
                    self.assertEqual(text.calls, 0)
                    sensor._submit.assert_not_called()
                finally:
                    sensor.close()

    def test_paused_batch_is_never_saved_or_captured(self):
        with tempfile.TemporaryDirectory() as directory:
            text = TextFixture()
            sensor = self.make_sensor(Path(directory), text=text)
            sensor._previous_foreground = sensor.source.snapshot
            sensor._submit = Mock()
            try:
                sensor.pause()
                self.assertIsNone(sensor.record_input_metadata(self.batch()))
                self.assertFalse(sensor.store.query(kinds=["input_batch"]))
                self.assertEqual(text.calls, 0)
                sensor._submit.assert_not_called()
            finally:
                sensor.close()

    def test_timing_preservation_keeps_typing_pause_text_causality(self):
        with tempfile.TemporaryDirectory() as directory:
            text = TextFixture()
            sensor = self.make_sensor(Path(directory), text=text)
            try:
                sensor.record_foreground(sensor.source.snapshot)
                text.text = "Committed Chinese text: \u4e2d\u6587\U0001f642"
                observation = sensor.record_input_metadata(self.batch())
                edits = sensor.store.query(kinds=["input_text"])
                self.assertEqual(len(edits), 1)
                self.assertEqual(edits[0].caused_by, (observation.observation_id,))
                self.assertEqual(edits[0].payload["inserted_text"], text.text)
                self.assertTrue(edits[0].payload["baseline_known"])
                contexts = sensor.store.query(kinds=["context_snapshot"])
                self.assertEqual(contexts[-1].payload["trigger"], "typing_pause")
                self.assertEqual(contexts[-1].caused_by, (observation.observation_id,))
            finally:
                sensor.close()


if __name__ == "__main__":
    unittest.main()
