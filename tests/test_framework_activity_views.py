"""Activity presentation keeps privacy scope and validates filters without a host."""
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from mirally.framework.activity_views import capture_scope, scope_note, validate_memory_search_filters
from mirally.runtime.activity.config import load_activity_config


class ActivityViewTests(unittest.TestCase):
    def config(self):
        with tempfile.TemporaryDirectory() as directory:
            return load_activity_config(Path(directory))

    def test_scope_excludes_content_when_only_metadata_is_authorized(self):
        config = self.config()
        config = replace(config, visual_enabled=True, ocr_enabled=True, ui_structure_enabled=True,
                         uia_content_enabled=True, input_text_enabled=True, clipboard_enabled=True,
                         policy=replace(config.policy, default_level="metadata"))
        scope = {row["label"]: row["enabled"] for row in capture_scope(config)}
        for label in ("UIA 结构", "OCR 正文", "UIA 正文", "输入文本", "剪贴板正文"):
            self.assertFalse(scope[label], label)
        self.assertFalse(any(row["enabled"] for row in capture_scope(config) if row["label"].startswith("屏幕截图")))

    def test_scope_requires_uia_source_for_structure_and_text(self):
        config = self.config()
        config = replace(config, ui_structure_enabled=False, uia_content_enabled=True,
                         input_text_enabled=True, policy=replace(config.policy, default_level="content"))
        scope = {row["label"]: row["enabled"] for row in capture_scope(config)}
        for label in ("UIA 结构", "UIA 正文", "输入文本"):
            self.assertFalse(scope[label], label)

    def test_scope_note_retains_user_exclusions(self):
        config = self.config()
        config = replace(config, visual_scope="foreground", policy=replace(config.policy,
                         excluded_apps=("password.exe",), excluded_windows=("*private*",)))
        note = scope_note(config)
        self.assertIn("前台窗口", note)
        self.assertIn("password.exe", note)
        self.assertIn("*private*", note)

    def test_filter_validation_rejects_unsupported_and_ambiguous_ranges(self):
        for filters in ({"unknown": True}, {"kinds": ["unknown"]},
                        {"start_utc_ms": True}, {"start_utc_ms": 2, "end_utc_ms": 1}):
            with self.subTest(filters=filters), self.assertRaises(ValueError):
                validate_memory_search_filters(filters)
        expected = {"provenances": ["observed"], "start_utc_ms": 0, "end_utc_ms": 10}
        self.assertEqual(validate_memory_search_filters(expected), expected)


if __name__ == "__main__":
    unittest.main()
