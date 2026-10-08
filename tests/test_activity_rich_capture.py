from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image

from openassist.core.activity.input_text import InputTextTracker
from openassist.core.activity.models import CollectionLevel, Observation
from openassist.core.activity.policy import ActivityPolicy
from openassist.core.activity.store import ObservationStore
from openassist.platform.activity.foreground import ForegroundSnapshot
from openassist.platform.activity.input_metadata import InputBatch, InputMetadataAggregator
from openassist.platform.activity.clipboard import WindowsClipboardSource
from openassist.platform.activity.monitors import (
    MonitorSnapshot, WindowRegion, image_black_fraction, mask_image, opaque_client_candidate,
    visible_exclusion_masks,
)
from openassist.platform.activity.ocr import OCRResult, WindowsNativeOCRSource
from openassist.platform.activity.uia_worker import content_fields, inspect_tree
from openassist.platform.activity.uia_structure import UIAStructureResult, WindowsUIASource
from openassist.platform.activity.wgc_capture import CaptureResult, VisualSignatureGate, WindowsWindowCaptureSource
from openassist.runtime.activity.config import load_activity_config
from openassist.runtime.activity.monitor import _safe_observation, build_test_config
from openassist.runtime.activity.scheduler import CaptureJob, CaptureQueue
from openassist.runtime.activity.service import ActivitySensor


def focus(text="", **fields):
    return {"control_id": "edit-1", "role": "EditControl", "text_status": "ok", "value": text, **fields}


class Control:
    ControlTypeName = "EditControl"
    Name = "Fixture label"
    AutomationId = "editor"
    ClassName = "Edit"
    IsPassword = False
    HasKeyboardFocus = False
    IsEnabled = True
    IsOffscreen = False
    BoundingRectangle = SimpleNamespace(left=1, top=2, right=300, bottom=100)

    def __init__(self, text="fixture", children=(), password=False):
        self.text, self.children, self.IsPassword = text, children, password

    def GetRuntimeId(self):
        return (id(self),)

    def GetChildren(self):
        return self.children

    def GetValuePattern(self):
        return SimpleNamespace(Value=self.text, IsReadOnly=False)

    def GetTextPattern(self):
        return SimpleNamespace(DocumentRange=SimpleNamespace(GetText=lambda count: self.text[:count]))


class RichSources:
    available = True

    def __init__(self):
        self.snapshot = ForegroundSnapshot(101, 201, "proc", "window", "fixture.exe",
            int(time.time()*1000), time.monotonic_ns(), "verified", "boot", True, "Fixture")
        self.text = ""
        self.calls = 0
        self.stale_on_inspect = False
        self.started = threading.Event()
        self.release = threading.Event()
        self.block = False

    def poll(self):
        return self.snapshot

    def matches(self, snapshot):
        return self.snapshot.window_instance_id == snapshot.window_instance_id

    def inspect(self, hwnd, **kwargs):
        self.calls += 1
        self.started.set()
        if self.block:
            self.release.wait(3)
        result = UIAStructureResult("ok", node_count=1, nodes=({"role": "EditControl", "value": self.text},),
            focused_control=focus(self.text), content_enabled=kwargs.get("include_content", False))
        if self.stale_on_inspect:
            self.snapshot = replace(self.snapshot, window_instance_id="other")
        return result


class FakeCapture:
    available = True

    def __init__(self):
        self.color = "white"
        self.calls = 0

    def capture(self, hwnd, path, **kwargs):
        self.calls += 1
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (120, 60), self.color).save(path)
        return CaptureResult("ok", path, 120, 60, signature={"fixture": self.color})

    capture_monitor = capture


class MostlyBlackCapture(FakeCapture):
    def capture(self, hwnd, path, **kwargs):
        self.calls += 1
        path.parent.mkdir(parents=True, exist_ok=True)
        image = Image.new("RGB", (100, 50), "black")
        for x in range(10):
            for y in range(50):
                image.putpixel((x, y), (255, 255, 255))
        image.save(path)
        return CaptureResult("ok", path, 100, 50, signature={"fixture": "mostly-black"})

    capture_monitor = capture


class InvalidImageCapture(FakeCapture):
    def capture(self, hwnd, path, **kwargs):
        self.calls += 1
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"not-an-image")
        return CaptureResult("ok", path, 120, 60, signature={"fixture": "invalid-image"})

    capture_monitor = capture


class FakeMonitors:
    available = True

    def __init__(self):
        self.targets = (MonitorSnapshot(10, "monitor-1", (0, 0, 120, 60), True),
                        MonitorSnapshot(11, "monitor-2", (120, 0, 240, 60)))
        self.desktop = True

    def enumerate(self):
        return self.targets

    def desktop_available(self):
        return self.desktop

    def masks(self, policy):
        return []


class LifecycleCapture(FakeCapture):
    def __init__(self):
        super().__init__()
        self.paused = False
        self.closed = False

    def pause(self):
        self.paused = True

    def resume(self):
        self.paused = False

    def close(self):
        self.paused = self.closed = True

    def status(self):
        return {"available": True, "paused": self.paused, "border_status": "disable_requested"}

    def capture(self, hwnd, path, **kwargs):
        if self.paused:
            return CaptureResult("cancelled", missing_reasons=("capture_paused",))
        return super().capture(hwnd, path, **kwargs)

    capture_monitor = capture


class RichCaptureTests(unittest.TestCase):
    def sensor(self, root, sources=None, **kwargs):
        sources = sources or RichSources()
        config = replace(build_test_config(root), database_path=root/"db.sqlite", asset_directory=root/"assets",
                         visual_enabled=False, ocr_enabled=False, input_enabled=False,
                         visual_scope="foreground", browser_urls_enabled=False, capture_interval_seconds=0)
        return ActivitySensor(config, source=sources, uia_source=sources, **kwargs)

    def test_persistent_capture_lifecycle_follows_pause_external_state_and_stop(self):
        with tempfile.TemporaryDirectory() as directory:
            capture = LifecycleCapture()
            sensor = self.sensor(Path(directory), visual_source=capture, monitor_source=FakeMonitors())
            sensor.config = replace(sensor.config, visual_enabled=True)
            try:
                sensor.start()
                sensor.pause()
                self.assertTrue(capture.paused)
                self.assertTrue(sensor.metrics_snapshot()["capture_runtime"]["paused"])
                sensor.resume()
                self.assertFalse(capture.paused)
                sensor.store.set_state("collector_pause", {"paused":True})
                sensor._refresh_external_state()
                self.assertTrue(capture.paused)
                sensor.store.set_state("collector_pause", {"paused":False})
                sensor._refresh_external_state()
                self.assertFalse(capture.paused)
                sensor.store.set_state("collector", {"status":"stop_requested"})
                sensor._refresh_external_state()
                self.assertTrue(capture.paused)
            finally:
                sensor.close()
            self.assertTrue(capture.closed)

    def test_persistent_capture_releases_during_agent_control_and_secure_desktop(self):
        with tempfile.TemporaryDirectory() as directory:
            capture, monitors = LifecycleCapture(), FakeMonitors()
            sensor = self.sensor(Path(directory), visual_source=capture, monitor_source=monitors)
            sensor.config = replace(sensor.config, visual_enabled=True)
            try:
                sensor.run_once()
                lease = sensor.begin_agent_control("fixture-task")
                self.assertTrue(capture.paused)
                sensor.run_once()
                self.assertTrue(capture.paused)
                sensor.end_agent_control(lease.lease_id)
                sensor.run_once()
                self.assertFalse(capture.paused)
                monitors.desktop = False
                sensor.run_once()
                self.assertTrue(capture.paused)
                monitors.desktop = True
                sensor.run_once()
                self.assertFalse(capture.paused)
            finally:
                sensor.close()

    def test_poll_failure_releases_persistent_capture_in_run_finally(self):
        with tempfile.TemporaryDirectory() as directory:
            capture, sources = LifecycleCapture(), RichSources()
            sensor = self.sensor(Path(directory), sources, visual_source=capture, monitor_source=FakeMonitors())
            sensor.config = replace(sensor.config, visual_enabled=True)
            try:
                with patch.object(sources, "poll", side_effect=RuntimeError("synthetic poll failure")):
                    with self.assertRaisesRegex(RuntimeError, "synthetic poll failure"):
                        sensor.run()
                self.assertTrue(capture.paused)
                self.assertTrue(sensor._stop.is_set())
            finally:
                sensor.close()

    def test_ime_insert_replace_delete_and_unknown_baseline(self):
        tracker = InputTextTracker()
        self.assertIsNone(tracker.observe("w", focus(""), input_trigger=False))
        delta = tracker.observe("w", focus("\u4f60\u597d"), input_trigger=True)
        self.assertEqual(delta["inserted_text"], "\u4f60\u597d")
        self.assertEqual(delta["operation"], "insert")
        delta = tracker.observe("w", focus("\u60a8\u597d"), input_trigger=True)
        self.assertEqual(delta["removed_char_count"], 1)
        self.assertEqual(delta["operation"], "replace")
        self.assertEqual(tracker.observe("w", focus(""), input_trigger=True)["operation"], "delete")
        self.assertFalse(tracker.observe("new", focus("existing"), input_trigger=True)["baseline_known"])

    def test_password_read_only_and_non_editor_do_not_produce_input(self):
        tracker = InputTextTracker()
        for fields in ({"password": True}, {"read_only": True}, {"role": "TextControl"}, {"text_status": "unsupported"}):
            self.assertIsNone(tracker.observe("w", focus("secret", **fields), input_trigger=True))

    def test_uia_body_properties_and_password_subtrees(self):
        root = Control(children=[Control("hello"), Control("secret", [Control("secret-child")], True)])
        result = inspect_tree(root, 6, 180, include_content=True, focused=root)
        self.assertEqual(result["nodes"][1]["value"], "hello")
        self.assertIn("control_id", result["nodes"][1])
        self.assertEqual(result["nodes"][1]["bounds"]["x"], 1)
        self.assertNotIn("secret", json.dumps(result))
        self.assertEqual(result["node_count"], 3)
        self.assertEqual(result["focused_control"]["value"], "fixture")

    def test_uia_structure_opt_out_never_calls_text_patterns(self):
        control = Control()
        with patch.object(control, "GetValuePattern", side_effect=AssertionError("text read")):
            result = inspect_tree(control, 6, 180)
        self.assertNotIn("name", result["nodes"][0])
        self.assertFalse(result["content_enabled"])

    def test_uia_unicode_budget_and_depth_are_explicit(self):
        root = Control("\u4e2d"*20000, [Control("\u4e2d"*20000) for _ in range(200)])
        result = inspect_tree(root, 6, 180, include_content=True, max_text_chars=12000, focused=root)
        self.assertLess(len(json.dumps(result, ensure_ascii=False).encode()), 60000)
        self.assertTrue(result["truncated"])
        limited = inspect_tree(root, 0, 180, include_content=True)
        self.assertTrue(limited["truncated"])
        self.assertTrue(inspect_tree(Control("long value"), 1, 10,
                                    include_content=True, max_text_chars=3)["truncated"])

    def test_native_uia_integer_password_state_and_raw_mouse_abi(self):
        from openassist.platform.activity.input_worker import RawMouse
        import ctypes
        ordinary = Control()
        ordinary.IsPassword = 0
        self.assertEqual(content_fields(ordinary,100)["value"], "fixture")
        ordinary.IsPassword = 1
        self.assertNotIn("value",content_fields(ordinary,100))
        self.assertEqual(ctypes.sizeof(RawMouse),24)
        self.assertEqual(RawMouse.button_flags.offset,4)
        self.assertEqual(RawMouse.last_x.offset,12)

    def test_copy_does_not_read_pre_update_clipboard_and_paste_checks_sequence(self):
        with tempfile.TemporaryDirectory() as directory:
            clipboard = SimpleNamespace(available=True, read=lambda **kwargs:
                {"status":"ok", "sequence":8, "text":"pasted text"})
            sensor = self.sensor(Path(directory),clipboard_source=clipboard)
            try:
                sensor.record_foreground(sensor.source.snapshot)
                with patch.object(clipboard,"read",wraps=clipboard.read) as read:
                    event = {"type":"clipboard_operation","sequence":8,"hwnd":101,"pid":201}
                    sensor.record_input_event({**event,"operation":"copy"})
                    read.assert_not_called()
                    sensor.record_input_event({**event,"operation":"paste"})
                    self.assertEqual(read.call_args.kwargs["sequence"],8)
                    self.assertEqual(sensor.store.query(kinds=["clipboard_content"])[0].payload["operation"],"paste")
            finally:
                sensor.close()

    def test_same_window_content_changes_and_unchanged_uia_dedup(self):
        with tempfile.TemporaryDirectory() as directory:
            source = RichSources()
            sensor = self.sensor(Path(directory), source)
            try:
                sensor.record_foreground(source.snapshot)
                sensor.record_foreground(source.snapshot)
                self.assertEqual(len(sensor.store.query(kinds=["context_snapshot"])), 1)
                source.text = "changed"
                sensor.record_foreground(source.snapshot)
                self.assertEqual(len(sensor.store.query(kinds=["context_snapshot"])), 2)
                self.assertEqual(sensor.store.query(kinds=["input_text"])[0].payload["inserted_text"], "changed")
            finally:
                sensor.close()

    def test_typing_pause_links_input_text_to_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            source = RichSources()
            sensor = self.sensor(Path(directory), source)
            try:
                sensor.record_foreground(source.snapshot)
                source.text = "committed \u4e2d\u6587"
                event = sensor.record_input_metadata(InputBatch(3,0,0,False,1,2,1500,101,201))
                text = sensor.store.query(kinds=["input_text"])[0]
                self.assertEqual(text.caused_by, (event.observation_id,))
                self.assertEqual(text.payload["inserted_text"], source.text)
                self.assertEqual(text.collection_level, CollectionLevel.CONTENT)
            finally:
                sensor.close()

    def test_other_window_input_is_not_misattributed(self):
        with tempfile.TemporaryDirectory() as directory:
            sensor = self.sensor(Path(directory))
            try:
                sensor.record_foreground(sensor.source.snapshot)
                self.assertIsNone(sensor.record_input_metadata(InputBatch(1,0,0,False,1,2,1500,999,999)))
                self.assertFalse(sensor.store.query(kinds=["input_batch"]))
            finally:
                sensor.close()

    def test_stale_uia_results_are_discarded(self):
        with tempfile.TemporaryDirectory() as directory:
            source = RichSources()
            source.stale_on_inspect = True
            sensor = self.sensor(Path(directory), source)
            try:
                sensor.record_foreground(source.snapshot)
                self.assertFalse(sensor.store.query(kinds=["context_snapshot"]))
                self.assertEqual(sensor.metrics_snapshot()["stale_results"], 1)
            finally:
                sensor.close()

    def test_background_evidence_does_not_block_polling_and_pause_discards_results(self):
        with tempfile.TemporaryDirectory() as directory:
            source = RichSources()
            source.block = True
            sensor = self.sensor(Path(directory), source)
            try:
                sensor.start()
                sensor.start_evidence_worker()
                began = time.monotonic()
                sensor.record_foreground(source.snapshot)
                self.assertLess(time.monotonic()-began, 0.2)
                self.assertTrue(source.started.wait(1))
                sensor.pause()
                source.release.set()
                sensor.close()
                with ObservationStore(Path(directory)/"db.sqlite", asset_directory=Path(directory)/"assets") as store:
                    self.assertFalse(store.query(kinds=["context_snapshot"]))
            finally:
                source.release.set()
                if sensor._started:
                    sensor.close()

    def test_content_policy_blocks_native_body_reads(self):
        with tempfile.TemporaryDirectory() as directory:
            source = RichSources()
            sensor = self.sensor(Path(directory), source)
            sensor.policy = replace(sensor.policy, default_level=CollectionLevel.STRUCTURE)
            try:
                with patch.object(source, "inspect", wraps=source.inspect) as inspect:
                    sensor.record_foreground(source.snapshot)
                    self.assertNotIn("include_content", inspect.call_args.kwargs)
                self.assertFalse(sensor.store.query(kinds=["input_text"]))
            finally:
                sensor.close()

    def test_clipboard_sequence_and_operation_are_linked_and_deduplicated(self):
        with tempfile.TemporaryDirectory() as directory:
            clipboard = SimpleNamespace(available=True, read=lambda **kwargs:
                {"status":"ok", "sequence":7, "text":"copied text", "truncated":False})
            sensor = self.sensor(Path(directory), clipboard_source=clipboard)
            try:
                sensor.record_foreground(sensor.source.snapshot)
                event = {"type":"clipboard_changed", "sequence":7, "hwnd":101, "pid":201}
                sensor.record_input_event(event)
                sensor.record_input_event(event)
                rows = sensor.store.query(kinds=["clipboard_content"])
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0].payload["text"], "copied text")
                self.assertTrue(rows[0].caused_by)
            finally:
                sensor.close()

    def test_clipboard_timeout_is_visible_not_fake_empty_text(self):
        with tempfile.TemporaryDirectory() as directory:
            clipboard = SimpleNamespace(available=True, read=lambda **kwargs:
                {"status":"timeout", "missing_reasons":["worker_deadline"]})
            sensor = self.sensor(Path(directory), clipboard_source=clipboard)
            try:
                sensor.record_foreground(sensor.source.snapshot)
                sensor.record_input_event({"type":"clipboard_changed", "sequence":7, "hwnd":101, "pid":201})
                row = sensor.store.query(kinds=["clipboard_content"])[0]
                self.assertEqual(row.quality, "unavailable")
                self.assertNotIn("text", row.payload)
            finally:
                sensor.close()

    def test_continuous_capture_dedup_and_changed_frame_persist(self):
        with tempfile.TemporaryDirectory() as directory:
            visual = FakeCapture()
            sensor = self.sensor(Path(directory), visual_source=visual)
            sensor.config = replace(sensor.config, visual_enabled=True)
            try:
                sensor.record_foreground(sensor.source.snapshot)
                sensor.record_foreground(sensor.source.snapshot)
                visual.color = "red"
                sensor.record_foreground(sensor.source.snapshot)
                rows = sensor.store.query(kinds=["content_snapshot"])
                self.assertEqual(len(rows), 2)
                self.assertEqual(visual.calls, 3)
                self.assertTrue(rows[1].asset_refs)
                self.assertIsNotNone(rows[1].capture_started_at)
                self.assertFalse(list((Path(directory)/"assets"/".staging").glob("*.png")))
            finally:
                sensor.close()

    def test_mostly_black_capture_is_filtered_using_its_own_region(self):
        with tempfile.TemporaryDirectory() as directory:
            capture = MostlyBlackCapture()
            sensor = self.sensor(Path(directory), visual_source=capture)
            sensor.config = replace(sensor.config, visual_enabled=True, ocr_enabled=True,
                                    drop_black_frames=True, window_black_fraction=0.80)
            try:
                snapshot = sensor.source.snapshot
                sensor.record_foreground(snapshot)
                rows = sensor.store.query(kinds=["content_snapshot"])
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0].payload["capture_status"], "black_frame_filtered")
                self.assertEqual(rows[0].quality, "unavailable")
                self.assertIn("mostly_black", rows[0].missing_reasons)
                self.assertGreater(rows[0].visual_summary["signature"]["black_fraction"], 0.8)
                self.assertEqual(sensor.metrics_snapshot()["black_frame_skips"], 1)
                self.assertFalse(sensor.store.query(kinds=["ocr_result"]))
                self.assertFalse(list((Path(directory) / "assets").rglob("*.png")))
            finally:
                sensor.close()

    def test_unreadable_capture_keeps_frame_with_filter_diagnostic(self):
        with tempfile.TemporaryDirectory() as directory:
            sensor = self.sensor(Path(directory), visual_source=InvalidImageCapture())
            sensor.config = replace(sensor.config, visual_enabled=True, ocr_enabled=False)
            try:
                sensor.record_foreground(sensor.source.snapshot)
                rows = sensor.store.query(kinds=["content_snapshot"])
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0].quality, "partial")
                self.assertIn("black_filter_unavailable", rows[0].missing_reasons)
                self.assertEqual(sensor.metrics_snapshot()["black_filter_errors"], 1)
                self.assertTrue(rows[0].asset_refs)
            finally:
                sensor.close()

    def test_monitor_capture_keeps_the_actual_trigger(self):
        with tempfile.TemporaryDirectory() as directory:
            capture, monitors = FakeCapture(), FakeMonitors()
            sensor = self.sensor(Path(directory), visual_source=capture, monitor_source=monitors)
            sensor.config = replace(sensor.config, visual_enabled=True, visual_scope="monitors")
            try:
                sensor.record_foreground(sensor.source.snapshot)
                first = sensor.store.query(kinds=["content_snapshot"])
                self.assertTrue(first)
                self.assertEqual(first[0].payload["trigger"], "foreground_settled")
                capture.color = "red"
                sensor._periodic_monitors(sensor.source.snapshot)
                rows = sensor.store.query(kinds=["content_snapshot"])
                self.assertIn("periodic_monitor", [row.payload["trigger"] for row in rows])
            finally:
                sensor.close()

    def test_ocr_result_is_bounded_before_observation_validation(self):
        result = OCRResult("ok", text="x" * 100_000,
                           lines=({"text": "x" * 2_000, "words": []},) * 100)
        bounded = result.bounded()
        self.assertLess(len(json.dumps(bounded.as_dict(), ensure_ascii=False).encode("utf-8")), 48 * 1024)
        self.assertIn("ocr_result_limit", bounded.missing_reasons)

    def test_black_fraction_is_measured_inside_the_image_not_desktop_area(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "small.png"
            image = Image.new("RGB", (10, 10), "black")
            for x in range(5):
                for y in range(10):
                    image.putpixel((x, y), (255, 255, 255))
            image.save(path)
            self.assertAlmostEqual(image_black_fraction(path), 0.5)

    def test_ocr_worker_unicode_output_under_ascii_environment(self):
        command = [sys.executable, "-c", "import sys; from openassist.platform.activity import ocr_worker as w; "
                   "sys.argv=['fixture','--image','unused.png']; "
                   "w.run=lambda *args: {'status':'ok','text':'\\u4e2d\\u6587 \\U0001f600'}; w.main()"]
        completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8",
                                   env={**os.environ, "PYTHONIOENCODING": "ascii"}, timeout=10,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout)["text"], "\u4e2d\u6587 \U0001f600")

    def test_ocr_parent_uses_utf8_and_rejects_failed_worker_success(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "frame.png"
            Image.new("RGB", (10, 10)).save(path)
            source = WindowsNativeOCRSource()
            source.available = True
            with patch("openassist.platform.activity.ocr.sys.platform", "win32"), \
                 patch("openassist.platform.activity.ocr.subprocess.run") as run:
                run.return_value = subprocess.CompletedProcess([], 0, '{"status":"ok","text":"\\u4e2d"}', "")
                self.assertEqual(source.recognize(path).text, "\u4e2d")
                self.assertEqual(run.call_args.kwargs["env"]["PYTHONIOENCODING"], "utf-8")
                run.return_value = subprocess.CompletedProcess([], 1, '{"status":"ok"}', "UnicodeEncodeError")
                result = source.recognize(path)
                self.assertEqual(result.status, "error")
                self.assertEqual(result.missing_reasons, ("worker_output_encoding",))

    def test_background_excluded_window_only_masks_visible_fragments(self):
        full = (0, 0, 100, 100)
        excluded = WindowRegion(full, True, full)
        allowed = WindowRegion(full, False, full)
        self.assertEqual(visible_exclusion_masks([allowed, excluded]), [])
        self.assertEqual(visible_exclusion_masks([excluded, allowed]), [full])
        left = WindowRegion((0, 0, 50, 100), False, (0, 0, 50, 100))
        self.assertEqual(visible_exclusion_masks([left, excluded]), [(50, 0, 100, 100)])
        right = WindowRegion((50, 0, 100, 100), False, (50, 0, 100, 100))
        self.assertEqual(visible_exclusion_masks([left, right, excluded]), [])
        # Transparent windows cannot authorize removing an underlying mask.
        self.assertEqual(visible_exclusion_masks([WindowRegion(full, False), excluded]), [full])

    def test_mask_rectangle_uses_exclusive_edges_and_unique_regions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "frame.png"
            Image.new("RGB", (100, 50), "white").save(path)
            target = MonitorSnapshot(1, "m", (-200, 0, 0, 100))
            rectangle = (-200, 0, -100, 100)
            self.assertEqual(mask_image(path, target, [rectangle, rectangle]), 1)
            with Image.open(path) as image:
                self.assertEqual(image.getpixel((49, 20)), (0, 0, 0))
                self.assertEqual(image.getpixel((50, 20)), (255, 255, 255))

    def test_signature_ignores_mask_counts_but_detects_changed_pixels(self):
        gate = VisualSignatureGate()
        gate.commit("m", {"sha256": "same", "masked_windows": 1})
        self.assertFalse(gate.changed("m", {"sha256": "same", "masked_windows": 2}))
        self.assertTrue(gate.changed("m", {"sha256": "different", "masked_windows": 1}))

    def test_chromium_directcomposition_main_windows_are_not_transparent(self):
        self.assertTrue(opaque_client_candidate(0x200000, 0x170b0000, "Chrome_WidgetWin_1"))
        self.assertTrue(opaque_client_candidate(0x200100, 0x15c70000, "Chrome_WidgetWin_1"))
        self.assertFalse(opaque_client_candidate(0x200000, 0x80000000, "Chrome_WidgetWin_1"))
        self.assertFalse(opaque_client_candidate(0x82000a8, 0x96000000, "Chrome_WidgetWin_1"))
        self.assertFalse(opaque_client_candidate(0x200000, 0x170b0000, "UnknownDComp"))
        self.assertFalse(opaque_client_candidate(0x80000, 0, "STATIC"))
        self.assertTrue(opaque_client_candidate(0, 0, "STATIC"))

    def test_changed_frame_resets_failed_ocr_retry_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            ocr = SimpleNamespace(available=True, recognize=lambda *args, **kwargs: OCRResult("timeout"))
            capture = FakeCapture()
            sensor = self.sensor(Path(directory), visual_source=capture, ocr_source=ocr)
            sensor.config = replace(sensor.config, visual_enabled=True, ocr_enabled=True)
            try:
                snapshot = sensor.source.snapshot
                original = sensor._record_content(snapshot, None, None, "fixture")
                sensor._ocr_retries[snapshot.window_instance_id] = (original, 3, 0)
                capture.color = "red"
                current = sensor._record_content(snapshot, None, None, "fixture")
                pending, attempts, _ = sensor._ocr_retries[snapshot.window_instance_id]
                self.assertEqual(pending.observation_id, current.observation_id)
                self.assertEqual(attempts, 1)
                self.assertEqual(len(sensor.store.query(kinds=["content_snapshot"])), 2)
            finally:
                sensor.close()

    def test_ocr_failure_retries_without_duplicate_assets_or_frames(self):
        with tempfile.TemporaryDirectory() as directory:
            ocr = SimpleNamespace(available=True, recognize=lambda *args, **kwargs: OCRResult("error"))
            sensor = self.sensor(Path(directory), visual_source=FakeCapture(), ocr_source=ocr)
            sensor.config = replace(sensor.config, visual_enabled=True, ocr_enabled=True)
            try:
                snapshot = sensor.source.snapshot
                original = sensor._record_content(snapshot, None, None, "fixture")
                self.assertEqual(original.quality, "partial")
                self.assertIsNone(sensor._record_content(snapshot, None, None, "fixture"))
                key = snapshot.window_instance_id
                sensor._ocr_retries[key] = (original, 1, 0)
                ocr.recognize = lambda *args, **kwargs: OCRResult("ok", text="recovered")
                retry = sensor._record_content(snapshot, None, None, "fixture")
                self.assertEqual(retry.kind, "ocr_result")
                self.assertEqual(retry.caused_by, (original.observation_id,))
                self.assertEqual(retry.asset_refs, original.asset_refs)
                self.assertEqual(retry.ocr_summary["text"], "recovered")
                self.assertEqual(len(sensor.store.query(kinds=["content_snapshot"])), 1)
                self.assertEqual(len(list((Path(directory) / "assets").rglob("*.png"))), 1)
                self.assertNotIn(key, sensor._ocr_retries)
                self.assertIsNone(sensor._record_content(snapshot, None, None, "fixture"))
            finally:
                sensor.close()

    def test_ocr_retry_budget_and_pause_invalidate_pending_work(self):
        with tempfile.TemporaryDirectory() as directory:
            ocr = SimpleNamespace(available=True, recognize=lambda *args, **kwargs: OCRResult("timeout"))
            sensor = self.sensor(Path(directory), visual_source=FakeCapture(), ocr_source=ocr)
            sensor.config = replace(sensor.config, visual_enabled=True, ocr_enabled=True)
            try:
                snapshot = sensor.source.snapshot
                original = sensor._record_content(snapshot, None, None, "fixture")
                key = snapshot.window_instance_id
                for attempt in (1, 2):
                    sensor._ocr_retries[key] = (original, attempt, 0)
                    self.assertIsNotNone(sensor._record_content(snapshot, None, None, "fixture"))
                self.assertIsNone(sensor._record_content(snapshot, None, None, "fixture"))
                self.assertEqual(len(sensor.store.query(kinds=["ocr_result"])), 2)
                sensor._ocr_retries[key] = (original, 1, 0)
                def pause_during_ocr(*args, **kwargs):
                    sensor.pause()
                    return OCRResult("ok", text="stale")
                ocr.recognize = pause_during_ocr
                self.assertIsNone(sensor._record_content(snapshot, None, None, "fixture"))
                self.assertFalse(sensor._ocr_retries)
                self.assertEqual(len(sensor.store.query(kinds=["ocr_result"])), 2)
                self.assertFalse(list((Path(directory) / "assets" / ".staging").glob("*.png")))
            finally:
                sensor.close()

    def test_uia_prioritizes_deep_document_over_toolbar_siblings(self):
        document = Control("document body")
        document.ControlTypeName = "DocumentControl"
        branch = document
        for _ in range(12):
            branch = Control("", children=[branch])
            branch.ControlTypeName = "PaneControl"
        buttons = [Control("toolbar") for _ in range(30)]
        for button in buttons:
            button.ControlTypeName = "ButtonControl"
        root = Control("", children=[*buttons, branch])
        result = inspect_tree(root, 16, 20, include_content=True)
        self.assertTrue(any(node.get("value") == "document body" for node in result["nodes"]))
        self.assertLessEqual(result["node_count"], 20)
        self.assertIn("uia_node_limit", result["missing_reasons"])
        limited = inspect_tree(root, 6, 180, include_content=True)
        self.assertIn("uia_depth_limit", limited["missing_reasons"])

    def test_multiple_monitor_identity_and_no_secure_desktop_capture(self):
        with tempfile.TemporaryDirectory() as directory:
            visual, monitors = FakeCapture(), FakeMonitors()
            sensor = self.sensor(Path(directory), visual_source=visual, monitor_source=monitors)
            sensor.config = replace(sensor.config, visual_enabled=True, visual_scope="monitors")
            try:
                sensor.record_foreground(sensor.source.snapshot)
                rows = sensor.store.query(kinds=["content_snapshot"])
                self.assertEqual({row.payload["monitor_id"] for row in rows}, {"monitor-1", "monitor-2"})
                self.assertTrue(all(row.app_id is None for row in rows))
                monitors.desktop = False
                sensor._periodic_monitors(sensor.source.snapshot)
                self.assertEqual(visual.calls, 2)
            finally:
                sensor.close()

    def test_masking_respects_negative_monitor_coordinates_and_scaling(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"frame.png"
            Image.new("RGB", (100,50), "white").save(path)
            target = MonitorSnapshot(1,"m",(-200,0,0,100))
            self.assertEqual(mask_image(path,target,[(-200,0,-100,100),(1,1,20,20)]), 1)
            with Image.open(path) as image:
                self.assertEqual(image.getpixel((10,20)), (0,0,0))
                self.assertEqual(image.getpixel((80,20)), (255,255,255))

    def test_rich_monitor_redacts_every_content_surface_until_reveal(self):
        value = Observation.create(kind="input_text", source_id="test", source_epoch="epoch", source_seq=0,
            boot_id="boot", payload={"inserted_text":"secret"},
            uia_summary={"nodes":[{"name":"secret", "value":"secret", "text":"secret"}],
                         "focused_control":{"value":"secret"}})
        self.assertNotIn("secret", json.dumps(_safe_observation(value)))
        self.assertIn("secret", json.dumps(_safe_observation(value, include_content=True)))
        self.assertEqual(_safe_observation(value)["payload"]["inserted_text_char_count"], 6)

    def test_config_and_test_default_include_all_three_content_streams(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = build_test_config(root)
            self.assertEqual(config.uia_max_depth, 16)
            self.assertTrue(config.uia_content_enabled and config.input_text_enabled and config.clipboard_enabled)
            self.assertEqual(config.visual_scope, "monitors")
            path = root/"config.yaml"
            # Use apply_patch only for source edits; this is a temporary test fixture.
            path.write_text("enabled: true\n", encoding="utf-8")
            config = load_activity_config(root, path)
            self.assertEqual(config.uia_max_depth, 16)
            self.assertEqual(config.policy.default_level, CollectionLevel.CONTENT)
            self.assertTrue(config.visual_enabled and config.visual_persist_images)
            self.assertTrue(config.continuous_enabled)

    def test_queue_coalesces_causes_bounds_memory_and_discards_expired(self):
        queue = CaptureQueue(capacity=2, deadline_seconds=0.2)
        queue.put(CaptureJob("w", None, 0, {"typing"}, ("a",)))
        queue.put(CaptureJob("w", None, 0, {"scroll"}, ("b",)))
        job = queue.get(threading.Event())
        self.assertEqual(job.causes, ("a","b"))
        self.assertEqual(job.triggers, {"typing","scroll"})
        for key in ("a","b","c"):
            queue.put(CaptureJob(key,None,0))
        self.assertEqual(queue.status()["dropped"],1)
        queue.clear()
        queue.put(CaptureJob("old",None,0,created=time.monotonic()-2))
        queue.put(CaptureJob("new",None,0))
        self.assertEqual(queue.get(threading.Event()).key,"new")
        self.assertEqual(queue.status()["expired"],1)

    def test_image_quota_evicts_oldest_with_expired_tombstones(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with ObservationStore(root/"db.sqlite", asset_directory=root/"assets") as store:
                first = store.reserve_asset(mime_type="image/png", scope="activity-content", policy_version=1)
                second = store.reserve_asset(mime_type="image/png", scope="activity-content", policy_version=1)
                for index, reservation in enumerate((first,second)):
                    observation = Observation.create(kind="content_snapshot", source_id="test", source_epoch="epoch",
                        source_seq=index, boot_id="boot", asset_refs=(reservation.asset_id,))
                    store.commit_asset_bytes(reservation,b"12345",observation)
                self.assertEqual(store.asset_bytes(),10)
                store.cleanup(max_asset_bytes=5)
                self.assertEqual(store.asset_bytes(),5)
                self.assertEqual(store.asset(first.asset_id)["status"],"expired")
                self.assertEqual(store.asset(second.asset_id)["status"],"ready")

    def test_isolated_clipboard_timeout_is_bounded(self):
        with patch("openassist.platform.activity.clipboard.subprocess.run", side_effect=subprocess.TimeoutExpired("fixture", 1)):
            source = WindowsClipboardSource()
            source.available = True
            self.assertEqual(source.read()["status"],"timeout")

    def test_raw_aggregation_with_zero_monotonic_origin(self):
        aggregator = InputMetadataAggregator(max_burst_ms=1000)
        aggregator.keyboard(now_monotonic_ns=0)
        aggregator.keyboard(now_monotonic_ns=1_000_000_000)
        self.assertTrue(aggregator.should_flush(now_monotonic_ns=1_000_000_001, quiet_ms=10000))


if __name__ == "__main__":
    unittest.main()
