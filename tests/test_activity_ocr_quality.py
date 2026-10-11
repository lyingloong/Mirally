"""OCR quality reflects source completeness without rewriting raw evidence."""

from __future__ import annotations

import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

from openassist.platform.activity.foreground import ForegroundSnapshot
from openassist.platform.activity.ocr import OCRResult
from openassist.platform.activity.wgc_capture import CaptureResult
from openassist.runtime.activity.monitor import build_test_config
from openassist.runtime.activity.service import ActivitySensor


class FixtureForeground:
    available = True
    _boot = "fixture_boot"

    def __init__(self):
        self.snapshot = ForegroundSnapshot(101, 201, "fixture_process", "fixture_window", "fixture.exe",
            int(time.time() * 1000), time.monotonic_ns(), "verified", self._boot, True, "Fixture")

    def matches(self, snapshot):
        return snapshot.window_instance_id == self.snapshot.window_instance_id


class FixtureCapture:
    available = True

    def capture(self, hwnd, path, **kwargs):
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (20, 20), "white").save(path)
        return CaptureResult("ok", path, 20, 20, signature={"fixture": "white"})


class ActivityOCRQualityTests(unittest.TestCase):
    def sensor(self, root, result, *, enabled=True):
        source = FixtureForeground()
        config = replace(build_test_config(root), database_path=root / "db.sqlite", asset_directory=root / "assets",
            input_enabled=False, ui_structure_enabled=False, input_text_enabled=False, browser_urls_enabled=False,
            visual_enabled=True, visual_scope="foreground", ocr_enabled=enabled, capture_interval_seconds=0)
        ocr = SimpleNamespace(available=True, recognize=lambda *args, **kwargs: result)
        monitors = SimpleNamespace(desktop_available=lambda: True, enumerate=lambda: ())
        return ActivitySensor(config, source=source, visual_source=FixtureCapture(), ocr_source=ocr,
                              monitor_source=monitors)

    @staticmethod
    def explicit_truncated_result():
        data = {"status": "ok", "text": "provided prefix", "lines": [{"text": "provided prefix", "words": []}],
                "missing_reasons": [], "truncated": True}
        return SimpleNamespace(status="ok", missing_reasons=(), truncated=True,
                               as_dict=lambda: dict(data))

    def assert_first_capture(self, result, quality, reasons, truncated=False, *, enabled=True):
        original = result.as_dict()
        with tempfile.TemporaryDirectory() as directory:
            sensor = self.sensor(Path(directory), result, enabled=enabled)
            try:
                observation = sensor._record_content(sensor.source.snapshot, None, None, "fixture")
                self.assertEqual(observation.quality, quality)
                self.assertEqual(observation.missing_reasons, reasons)
                self.assertEqual(observation.truncated, truncated)
                if enabled:
                    self.assertEqual(observation.ocr_summary, original)
                    self.assertEqual(result.as_dict(), original)
                stored = sensor.store.get(observation.observation_id)
                self.assertEqual(stored.ocr_summary, observation.ocr_summary)
                self.assertEqual(stored.missing_reasons, reasons)
            finally:
                sensor.close()

    def assert_retry(self, result, quality, reasons, truncated=False):
        original = result.as_dict()
        with tempfile.TemporaryDirectory() as directory:
            sensor = self.sensor(Path(directory), OCRResult("timeout", missing_reasons=("worker_deadline",)))
            try:
                snapshot = sensor.source.snapshot
                first = sensor._record_content(snapshot, None, None, "fixture")
                self.assertEqual(first.quality, "partial")
                key = snapshot.window_instance_id
                sensor._ocr_retries[key] = (first, 1, 0)
                sensor.ocr_source.recognize = lambda *args, **kwargs: result
                observation = sensor._record_content(snapshot, None, None, "fixture")
                self.assertEqual(observation.kind, "ocr_result")
                self.assertEqual(observation.quality, quality)
                self.assertEqual(observation.missing_reasons, reasons)
                self.assertEqual(observation.truncated, truncated)
                self.assertEqual(observation.ocr_summary, original)
                self.assertEqual(result.as_dict(), original)
                self.assertEqual(observation.caused_by, (first.observation_id,))
                self.assertEqual(observation.asset_refs, first.asset_refs)
                self.assertEqual(len(sensor.store.query(kinds=["content_snapshot"])), 1)
                stored = sensor.store.get(observation.observation_id)
                self.assertEqual(stored.ocr_summary, original)
                self.assertEqual(stored.missing_reasons, reasons)
            finally:
                sensor.close()

    def test_first_capture_full_large_ocr_is_complete_and_preserves_every_field(self):
        text = "\u4e2d\U0001f600" * 30_000
        result = OCRResult("ok", text=text, lines=({"text": text, "words": [{"text": text}]},))
        self.assert_first_capture(result, "complete", ())

    def test_first_capture_limited_source_is_partial_with_all_reasons(self):
        result = OCRResult("ok", text="provided prefix", missing_reasons=("ocr_result_limit", "custom_missing"))
        self.assert_first_capture(result, "partial", result.missing_reasons, truncated=True)

    def test_first_capture_other_missing_source_data_is_partial_without_mutation(self):
        result = OCRResult("ok", text="provided text", missing_reasons=("custom_missing",))
        self.assert_first_capture(result, "partial", result.missing_reasons)

    def test_first_capture_explicit_truncated_flag_cannot_claim_complete(self):
        self.assert_first_capture(self.explicit_truncated_result(), "partial", (), truncated=True)

    def test_intentionally_disabled_ocr_keeps_reason_in_configured_image_capture(self):
        self.assert_first_capture(OCRResult("ok", text="never called"), "complete", ("ocr_disabled",), enabled=False)

    def test_retry_full_large_ocr_is_complete_and_preserves_every_field(self):
        text = "\u4e2d\U0001f600" * 30_000
        result = OCRResult("ok", text=text, lines=({"text": text, "words": [{"text": text}]},))
        self.assert_retry(result, "complete", ())

    def test_retry_limited_source_is_partial_with_all_reasons(self):
        result = OCRResult("ok", text="provided prefix", missing_reasons=("ocr_result_limit", "custom_missing"))
        self.assert_retry(result, "partial", result.missing_reasons, truncated=True)

    def test_retry_other_missing_source_data_is_partial_without_mutation(self):
        result = OCRResult("ok", text="provided text", missing_reasons=("custom_missing",))
        self.assert_retry(result, "partial", result.missing_reasons)

    def test_retry_explicit_truncated_flag_cannot_claim_complete(self):
        self.assert_retry(self.explicit_truncated_result(), "partial", (), truncated=True)

    def test_retry_failure_preserves_all_source_reasons(self):
        result = OCRResult("timeout", missing_reasons=("worker_deadline", "custom_missing"))
        self.assert_retry(result, "partial", result.missing_reasons)


if __name__ == "__main__":
    unittest.main()
