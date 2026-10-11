from __future__ import annotations

import hashlib
import json
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from openassist.core.activity.models import CollectionLevel
from openassist.core.activity.policy import ActivityPolicy
from openassist.core.activity.store import ObservationStore
from openassist.platform.activity.input_metadata import InputBatch
from openassist.platform.activity.input_pump import InputPumpStatus, WindowsInputMetadataSource
from openassist.runtime.activity.config import ActivityConfig
from openassist.runtime.activity.service import ActivitySensor
from openassist.runtime.activity import cli


class FakeForeground:
    available = True

    def poll(self):
        return None


class FakeInput:
    available = True
    supports_content_events = True

    def __init__(self, *, start_success=True):
        self.start_success = start_success
        self.status = InputPumpStatus(True, False)

    def start(self, callback, *, event_callback=None):
        self.status = InputPumpStatus(True, self.start_success,
                                      None if self.start_success else "spawn_error")
        return self.start_success

    def stop(self):
        # The real parent-side input source also clears its reason on stop.
        self.status = InputPumpStatus(True, False)


class FakeVisual:
    available = True

    def __init__(self):
        self.closed = False

    def resume(self):
        pass

    def pause(self):
        pass

    def close(self):
        self.closed = True


class FakeEvidenceWorker:
    def __init__(self, *, finishes=True):
        self.alive = True
        self.finishes = finishes
        self.joined = False

    def is_alive(self):
        return self.alive

    def join(self, timeout):
        self.joined = True
        if self.finishes:
            self.alive = False


class CollectionDiagnosticsTests(unittest.TestCase):
    def make_sensor(self, root, *, input_source=None, **config_fields):
        config = replace(ActivityConfig.disabled(root), **{
            "enabled": True, "database_path": root / "observations.sqlite3", "asset_directory": root / "assets",
            "policy": ActivityPolicy(enabled=True, default_level=CollectionLevel.CONTENT, exclude_self=False),
            "input_enabled": True, "clipboard_enabled": True, **config_fields,
        })
        return ActivitySensor(config, source=FakeForeground(), input_source=input_source or FakeInput(),
                              uia_source=SimpleNamespace(available=True),
                              clipboard_source=SimpleNamespace(available=True),
                              monitor_source=SimpleNamespace(available=True))

    def test_worker_failure_is_retained_after_source_stop_and_store_reopen(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inputs = FakeInput()
            sensor = self.make_sensor(root, input_source=inputs)
            sensor.start()
            inputs.status = InputPumpStatus(True, False, "worker_exit")
            sensor.stop()
            sensor.close()
            self.assertIsNone(inputs.status.reason)
            with ObservationStore(root / "observations.sqlite3", read_only=True) as store:
                diagnostics = store.get_state("collector_diagnostics")
                self.assertEqual(store.get_state("collector")["status"], "stopped")
                self.assertEqual(diagnostics["status"], "stopped")
                self.assertEqual(diagnostics["sources"]["input_metadata"]["reason"], "worker_exit")
                self.assertEqual(diagnostics["sources"]["clipboard"]["reason"], "worker_exit")
                self.assertFalse(diagnostics["sources"]["input_metadata"]["running"])
                self.assertFalse(diagnostics["sources"]["input_metadata"]["running_before_stop"])
                self.assertGreaterEqual(diagnostics["stopped_at_utc_ms"], diagnostics["started_at_utc_ms"])
                self.assertEqual(store.query(), [])

    def test_start_failure_and_disabled_channels_are_distinct(self):
        with tempfile.TemporaryDirectory() as directory:
            sensor = self.make_sensor(Path(directory), input_source=FakeInput(start_success=False))
            try:
                sensor.start()
                diagnostics = sensor.store.get_state("collector_diagnostics")
                self.assertEqual(diagnostics["status"], "degraded")
                self.assertTrue(diagnostics["sources"]["input_metadata"]["enabled"])
                self.assertEqual(diagnostics["sources"]["input_metadata"]["reason"], "spawn_error")
                self.assertFalse(diagnostics["sources"]["visual"]["enabled"])
                self.assertEqual(diagnostics["sources"]["visual"]["reason"], "disabled_by_configuration")
            finally:
                sensor.close()

    def test_running_worker_snapshot_survives_repeated_stop(self):
        with tempfile.TemporaryDirectory() as directory:
            sensor = self.make_sensor(Path(directory))
            sensor.start()
            sensor.stop()
            stopped_at = sensor.store.get_state("collector_diagnostics")["stopped_at_utc_ms"]
            sensor.stop()
            sensor.close()
            with ObservationStore(Path(directory) / "observations.sqlite3", read_only=True) as store:
                diagnostics = store.get_state("collector_diagnostics")
                self.assertTrue(diagnostics["sources"]["input_metadata"]["running_before_stop"])
                self.assertFalse(diagnostics["sources"]["input_metadata"]["running"])
                self.assertIsNone(diagnostics["sources"]["input_metadata"]["reason"])
                self.assertEqual(diagnostics["stopped_at_utc_ms"], stopped_at)

    def test_pause_intervals_preserve_gaps_without_private_pause_text(self):
        with tempfile.TemporaryDirectory() as directory:
            sensor = self.make_sensor(Path(directory))
            try:
                sensor.start()
                sensor.pause("private_document_title")
                sensor.pause("user")
                sensor.resume()
                sensor.pause("manual")
                sensor.stop()
                diagnostics = sensor.store.get_state("collector_diagnostics")
                self.assertEqual(len(diagnostics["pause_intervals"]), 2)
                for interval in diagnostics["pause_intervals"]:
                    self.assertGreaterEqual(interval["ended_at_utc_ms"], interval["started_at_utc_ms"])
                self.assertEqual(diagnostics["pause_intervals"][0]["reason"], "unspecified")
                self.assertNotIn("private_document_title", json.dumps(diagnostics))
            finally:
                sensor.close()

    def test_sampled_health_detects_worker_exit_without_new_foreground(self):
        with tempfile.TemporaryDirectory() as directory:
            inputs = FakeInput()
            sensor = self.make_sensor(Path(directory), input_source=inputs, ui_structure_enabled=True)
            try:
                sensor.start()
                self.assertEqual(sensor.store.get_state("collector_diagnostics")["status"], "running")
                inputs.status = InputPumpStatus(True, False, "worker_exit")
                sensor._diagnostics_last_write = 0
                self.assertIsNone(sensor.run_once())
                diagnostics = sensor.store.get_state("collector_diagnostics")
                self.assertEqual(diagnostics["status"], "degraded")
                self.assertEqual(diagnostics["sources"]["input_metadata"]["reason"], "worker_exit")
                self.assertFalse(diagnostics["sources"]["input_metadata"]["running"])
                inputs.status = InputPumpStatus(True, True)
                sensor._diagnostics_last_write = 0
                sensor.run_once()
                self.assertEqual(sensor.store.get_state("collector_diagnostics")["status"], "running")
            finally:
                sensor.close()

    def test_error_counts_and_source_signal_survive_without_text_or_assets(self):
        with tempfile.TemporaryDirectory() as directory:
            sensor = self.make_sensor(Path(directory))
            try:
                sensor.start()
                sensor.record_input_event({"type": "source_diagnostic", "reason": "clipboard_listener_failed"})
                sensor._metric_status("ocr_by_status", "worker_deadline")
                sensor._metric_inc("black_frame_skips", 3)
                sensor._metric_inc("evidence_errors", 2)
                sensor._queue.dropped = 4
                sensor._metrics["private_runtime"] = "private text"
                sensor._metrics["last_triggers"] = ["private text"]
                sensor.stop()
                diagnostics = sensor.store.get_state("collector_diagnostics")
                self.assertEqual(diagnostics["source_signals"]["clipboard_listener_failed"], 1)
                self.assertEqual(diagnostics["metrics"]["ocr_by_status"]["worker_deadline"], 1)
                self.assertEqual(diagnostics["known_gap_counts"]["black_frame_skips"], 3)
                self.assertEqual(diagnostics["known_gap_counts"]["evidence_errors"], 2)
                self.assertEqual(diagnostics["scheduler"]["dropped"], 4)
                self.assertNotIn("private text", json.dumps(diagnostics))
                self.assertNotIn("asset_refs", json.dumps(diagnostics))
            finally:
                sensor.close()

    def test_rejected_previous_window_batch_is_counted_without_misattribution(self):
        with tempfile.TemporaryDirectory() as directory:
            sensor = self.make_sensor(Path(directory))
            try:
                sensor.start()
                sensor._previous_foreground = SimpleNamespace(hwnd=22, pid=222)
                batch = InputBatch(3, 0, 0, False, 100, 200, 0, hwnd=11, pid=111)
                self.assertIsNone(sensor.record_input_metadata(batch))
                sensor.stop()
                diagnostics = sensor.store.get_state("collector_diagnostics")
                self.assertEqual(diagnostics["known_gap_counts"]["unattributed_input_batches"], 1)
                self.assertFalse(sensor.store.query(kinds=["input_batch"]))
            finally:
                sensor.close()

    def test_diagnostics_write_failure_still_stops_platform_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            inputs = FakeInput()
            sensor = self.make_sensor(Path(directory), input_source=inputs)
            try:
                sensor.start()
                write = sensor.store.set_state

                def fail_diagnostics(key, value):
                    if key == "collector_diagnostics":
                        raise OSError("diagnostics write failed")
                    return write(key, value)

                with patch.object(sensor.store, "set_state", side_effect=fail_diagnostics):
                    with self.assertRaisesRegex(OSError, "diagnostics write failed"):
                        sensor.stop()
                self.assertTrue(sensor._stop.is_set())
                self.assertFalse(sensor._started)
                self.assertFalse(inputs.status.running)
            finally:
                sensor.close()

    def test_external_pause_resume_is_recorded_once_and_redacts_reason(self):
        with tempfile.TemporaryDirectory() as directory:
            sensor = self.make_sensor(Path(directory))
            try:
                sensor.start()
                sensor.store.set_state("collector_pause", {"paused": True, "reason": "private_document_title"})
                sensor.run_once()
                sensor.run_once()
                diagnostics = sensor.store.get_state("collector_diagnostics")
                self.assertEqual(diagnostics["status"], "paused")
                self.assertEqual(len(diagnostics["pause_intervals"]), 1)
                self.assertIsNone(diagnostics["pause_intervals"][0]["ended_at_utc_ms"])
                sensor.store.set_state("collector_pause", {"paused": False})
                sensor.run_once()
                diagnostics = sensor.store.get_state("collector_diagnostics")
                self.assertIsNotNone(diagnostics["pause_intervals"][0]["ended_at_utc_ms"])
                self.assertEqual(diagnostics["pause_intervals"][0]["reason"], "unspecified")
                self.assertNotIn("private_document_title", json.dumps(diagnostics))
                sensor.store.set_state("collector_pause", {"paused": True, "reason": "manual"})
                sensor.run_once()
                sensor.stop()
                diagnostics = sensor.store.get_state("collector_diagnostics")
                self.assertEqual(len(diagnostics["pause_intervals"]), 2)
                self.assertEqual(diagnostics["pause_intervals"][1]["reason"], "manual")
                self.assertIsNotNone(diagnostics["pause_intervals"][1]["ended_at_utc_ms"])
            finally:
                sensor.close()

    def test_startup_baseline_degraded_is_retained_on_periodic_diagnostics(self):
        with tempfile.TemporaryDirectory() as directory:
            sensor = self.make_sensor(Path(directory))
            try:
                sensor.start()
                self.assertEqual(sensor.store.get_state("collector_diagnostics")["status"], "degraded")
                sensor._diagnostics_last_write = 0
                sensor.run_once()
                self.assertEqual(sensor.store.get_state("collector_diagnostics")["status"], "degraded")
            finally:
                sensor.close()

    def test_metadata_and_structure_clipboard_signals_are_enabled_without_content(self):
        for level in (CollectionLevel.METADATA, CollectionLevel.STRUCTURE):
            with self.subTest(level=level), tempfile.TemporaryDirectory() as directory:
                sensor = self.make_sensor(Path(directory), policy=ActivityPolicy(
                    enabled=True, default_level=level, exclude_self=False))
                try:
                    sensor.start()
                    diagnostics = sensor.store.get_state("collector_diagnostics")
                    self.assertTrue(diagnostics["sources"]["clipboard"]["enabled"])
                    self.assertFalse(diagnostics["sources"]["clipboard_content"]["enabled"])
                    self.assertEqual(diagnostics["sources"]["clipboard_content"]["reason"], "disabled_by_configuration")
                    sensor.record_input_event({"type": "source_diagnostic", "reason": "clipboard_listener_failed"})
                    sensor._diagnostics_last_write = 0
                    sensor.run_once()
                    self.assertEqual(sensor.store.get_state("collector_diagnostics")["sources"]["clipboard"]["reason"],
                                     "clipboard_listener_failed")
                finally:
                    sensor.close()

    def test_real_input_reader_eof_changes_periodic_health_to_degraded(self):
        with tempfile.TemporaryDirectory() as directory:
            inputs = WindowsInputMetadataSource()
            inputs.available = True
            inputs._status = InputPumpStatus(True, True)
            sensor = self.make_sensor(Path(directory), input_source=inputs, ui_structure_enabled=True)
            try:
                # Exercise the actual parent reader's EOF handling against an
                # isolated stream, without starting the Raw Input process.
                with patch.object(inputs, "start", return_value=True):
                    sensor.start()
                self.assertEqual(sensor.store.get_state("collector_diagnostics")["status"], "running")
                inputs._process = SimpleNamespace(stdout=io.StringIO(""))
                inputs._read()
                inputs._process = None
                self.assertEqual(inputs.status.reason, "worker_exit")
                sensor._diagnostics_last_write = 0
                sensor.run_once()
                diagnostics = sensor.store.get_state("collector_diagnostics")
                self.assertEqual(diagnostics["status"], "degraded")
                self.assertEqual(diagnostics["sources"]["input_metadata"]["reason"], "worker_exit")
            finally:
                inputs._process = None
                sensor.close()

    def test_close_finishes_cleanup_then_rethrows_diagnostics_write_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            sensor = self.make_sensor(Path(directory))
            sensor.visual_source = visual = FakeVisual()
            sensor.start()
            sensor._evidence_thread = worker = FakeEvidenceWorker()
            write = sensor.store.set_state

            def fail_diagnostics(key, value):
                if key == "collector_diagnostics":
                    raise OSError("diagnostics write failed")
                return write(key, value)

            with patch.object(sensor.store, "set_state", side_effect=fail_diagnostics), \
                    patch.object(sensor.store, "close", wraps=sensor.store.close) as store_close:
                with self.assertRaisesRegex(OSError, "diagnostics write failed"):
                    sensor.close()
                self.assertTrue(worker.joined)
                self.assertTrue(visual.closed)
                self.assertTrue(sensor._diagnostics_closed)
                store_close.assert_called_once()
            sensor.close()

    def test_live_evidence_worker_keeps_store_and_visual_open_for_close_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            sensor = self.make_sensor(Path(directory))
            sensor.visual_source = visual = FakeVisual()
            sensor.start()
            sensor._evidence_thread = worker = FakeEvidenceWorker(finishes=False)
            write = sensor.store.set_state

            def fail_diagnostics(key, value):
                if key == "collector_diagnostics":
                    raise OSError("diagnostics write failed")
                return write(key, value)

            try:
                with patch.object(sensor.store, "set_state", side_effect=fail_diagnostics), \
                        patch.object(sensor.store, "close", wraps=sensor.store.close) as store_close:
                    with self.assertRaisesRegex(RuntimeError, "evidence worker did not stop") as raised:
                        sensor.close()
                    self.assertIsInstance(raised.exception.__cause__, OSError)
                    self.assertIs(sensor._evidence_thread, worker)
                    self.assertFalse(visual.closed)
                    self.assertFalse(sensor._diagnostics_closed)
                    store_close.assert_not_called()
                self.assertEqual(sensor.store.get_state("collector")["status"], "stopped")
                worker.finishes = True
                sensor.close()
                self.assertTrue(visual.closed)
                self.assertTrue(sensor._diagnostics_closed)
            finally:
                worker.finishes = True
                sensor.close()

    def test_cli_status_reads_diagnostics_after_collector_has_stopped(self):
        with tempfile.TemporaryDirectory() as directory:
            sensor = self.make_sensor(Path(directory))
            sensor.start()
            sensor.input_source.status = InputPumpStatus(True, False, "worker_exit")
            config = sensor.config
            sensor.close()
            original_hash = hashlib.sha256(config.database_path.read_bytes()).hexdigest()
            output = io.StringIO()
            with patch.object(cli, "_config", return_value=config), redirect_stdout(output):
                self.assertEqual(cli.command_status(SimpleNamespace(config=None)), 0)
            diagnostics = json.loads(output.getvalue())["collector_diagnostics"]
            self.assertEqual(hashlib.sha256(config.database_path.read_bytes()).hexdigest(), original_hash)
            self.assertEqual(diagnostics["status"], "stopped")
            self.assertEqual(diagnostics["sources"]["input_metadata"]["reason"], "worker_exit")


if __name__ == "__main__":
    unittest.main()
