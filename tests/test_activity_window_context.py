"""Window context tests without reading or activating user applications."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from mirally.platform.activity.browser_url import WindowsBrowserURLSource, normalize_browser_url
from mirally.platform.activity.browser_url_worker import find_address_bar_url
from mirally.platform.activity.foreground import WindowsForegroundSource
from mirally.runtime.activity.config import ActivityConfig, load_activity_config


def _control(role="PaneControl", name="", value="", children=(), **properties):
    return SimpleNamespace(
        ControlTypeName=role,
        Name=name,
        AutomationId=properties.get("automation_id", ""),
        ClassName="",
        IsOffscreen=properties.get("offscreen", False),
        IsPassword=properties.get("password", False),
        GetChildren=Mock(return_value=list(children)),
        GetValuePattern=Mock(return_value=SimpleNamespace(Value=value)),
    )


class ActivityWindowContextTests(unittest.TestCase):
    def test_title_read_preserves_full_caption_and_can_be_disabled(self):
        source = WindowsForegroundSource()
        user32 = Mock()
        source._user32 = user32
        caption = " " + "窗" * 3998 + " "
        user32.GetWindowTextLengthW.return_value = len(caption)

        def read_title(hwnd, buffer, length):
            buffer.value = caption
            self.assertEqual(length, 4001)
            return len(buffer.value)

        user32.GetWindowTextW.side_effect = read_title
        self.assertEqual(source._window_title(100), caption)
        source.include_window_title = False
        self.assertIsNone(source._window_title(100))
        self.assertEqual(user32.GetWindowTextW.call_count, 1)

    def test_long_address_bar_value_is_preserved_without_capture_budget(self):
        address = "https://example.test/path?q=" + "a" * 70000
        self.assertEqual(normalize_browser_url(address), address)

    def test_growing_caption_retries_instead_of_returning_a_prefix(self):
        source = WindowsForegroundSource()
        source._user32 = Mock()
        source._user32.GetWindowTextLengthW.side_effect = [3, 9, 9, 9]

        def read_title(hwnd, buffer, length):
            buffer.value = "完整窗口标题内容长"[:length - 1]
            return len(buffer.value)

        source._user32.GetWindowTextW.side_effect = read_title
        self.assertEqual(source._window_title(100), "完整窗口标题内容长")
        self.assertEqual(source._user32.GetWindowTextW.call_count, 2)

    def test_address_bar_skips_web_document_and_uses_localized_browser_chrome(self):
        page_field = _control("EditControl", "Address", "https://wrong.test")
        document = _control("DocumentControl", children=(page_field,))
        address = _control("EditControl", "搜索或输入网址", "https://example.test/inbox")
        root = _control(children=(document, address))
        self.assertEqual(find_address_bar_url(root, 8, 100), "https://example.test/inbox")
        document.GetChildren.assert_not_called()
        page_field.GetValuePattern.assert_not_called()

    def test_unmarked_search_field_and_password_fields_are_not_address_bars(self):
        search = _control("EditControl", "Search", "https://wrong.test")
        password = _control("EditControl", "Address", "https://secret.test", password=True)
        hidden = _control("EditControl", "Address", "https://hidden.test", offscreen=True)
        root = _control(children=(search, password, hidden))
        self.assertIsNone(find_address_bar_url(root, 8, 100))
        search.GetValuePattern.assert_not_called()
        password.GetValuePattern.assert_not_called()
        hidden.GetValuePattern.assert_not_called()

    def test_tree_walk_is_bounded(self):
        address = _control("EditControl", "Address", "https://example.test")
        root = _control(children=(_control(children=(address,)),))
        self.assertIsNone(find_address_bar_url(root, 0, 100))
        self.assertIsNone(find_address_bar_url(root, 8, 1))
        self.assertIsNone(find_address_bar_url(root, 1, 100))

    def test_url_values_preserve_scheme_less_addresses_and_reject_search_or_malformed_values(self):
        self.assertEqual(normalize_browser_url("example.test/inbox?q=hello"), "example.test/inbox?q=hello")
        self.assertEqual(normalize_browser_url("localhost:3000/page"), "localhost:3000/page")
        self.assertEqual(normalize_browser_url("about:blank"), "about:blank")
        for value in ("find a document", "https://", "https://example.test:bad", "a" * 5000, "https://test\nsecret"):
            self.assertIsNone(normalize_browser_url(value))

    def test_browser_worker_timeout_is_terminated(self):
        source = WindowsBrowserURLSource()
        source.available = True
        process = Mock()
        process.communicate.side_effect = [subprocess.TimeoutExpired("worker", 1), ("", None)]
        with patch("mirally.platform.activity.browser_url.subprocess.Popen", return_value=process):
            result = source.read(100, app_id="msedge.exe", timeout_ms=100)
        self.assertEqual(result.status, "timeout")
        self.assertEqual(result.missing_reasons, ("worker_deadline",))
        process.kill.assert_called_once()

    def test_malformed_worker_output_is_a_diagnostic(self):
        source = WindowsBrowserURLSource()
        source.available = True
        process = Mock(returncode=0)
        for output in ("[]", "null", '{"duration_ms":"bad"}', "invalid json"):
            process.communicate.return_value = (output, None)
            with patch("mirally.platform.activity.browser_url.subprocess.Popen", return_value=process):
                result = source.read(100, app_id="msedge.exe")
            self.assertEqual(result.missing_reasons, ("invalid_worker_response",))

    def test_real_worker_module_rejects_invalid_target_without_desktop_access(self):
        completed = subprocess.run(
            [sys.executable, "-m", "mirally.platform.activity.browser_url_worker", "--hwnd", "0"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            encoding="utf-8",
            timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout)["missing_reasons"], ["invalid_hwnd"])

    @unittest.skipUnless(sys.platform == "win32", "Windows native caption check")
    def test_native_caption_read_of_owned_hidden_window(self):
        script = r'''
import ctypes
from ctypes import wintypes
from mirally.platform.activity.foreground import WindowsForegroundSource
user32 = ctypes.windll.user32
user32.CreateWindowExW.restype = wintypes.HWND
user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
    wintypes.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, ctypes.c_void_p]
user32.DestroyWindow.argtypes = [wintypes.HWND]
hwnd = user32.CreateWindowExW(0, "STATIC", "Mirally caption fixture", 0, 0, 0, 200, 100, None, None, None, None)
assert hwnd, "Unable to create the owned fixture"
try:
    assert WindowsForegroundSource()._window_title(hwnd) == "Mirally caption fixture"
finally:
    user32.DestroyWindow(hwnd)
'''
        completed = subprocess.run(
            [sys.executable, "-c", script], stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, encoding="utf-8", timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_config_defaults_are_rich_and_explicit_opt_out_is_respected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "activity.yaml"
            path.write_text("enabled: true\n", encoding="utf-8")
            config = load_activity_config(root, path)
            self.assertTrue(config.window_titles_enabled)
            self.assertTrue(config.browser_urls_enabled)
            self.assertEqual(config.knowledge_batch_size, 200)
            path.write_text(
                "enabled: true\ncollection:\n  window_titles: false\n  browser_urls: false\n"
                "  browser_url_poll_interval_seconds: 5\n  browser_url_timeout_ms: 2000\n",
                encoding="utf-8",
            )
            config = load_activity_config(root, path)
            self.assertFalse(config.window_titles_enabled)
            self.assertFalse(config.browser_urls_enabled)
            self.assertEqual(config.browser_url_poll_interval_seconds, 5)
            self.assertEqual(config.browser_url_timeout_ms, 2000)
            path.write_text(
                "enabled: true\nknowledge:\n  batch_size: 42\n  idle_timeout_seconds: 90\n"
                "  allowed_lateness_seconds: 12\n  poll_interval_seconds: 2\n",
                encoding="utf-8",
            )
            config = load_activity_config(root, path)
            self.assertEqual(config.knowledge_batch_size, 42)
            self.assertEqual(config.knowledge_idle_timeout_seconds, 90)
            self.assertEqual(config.knowledge_allowed_lateness_seconds, 12)
            self.assertEqual(config.knowledge_poll_interval_seconds, 2)
            disabled = ActivityConfig.disabled(root)
            self.assertFalse(disabled.window_titles_enabled)
            self.assertFalse(disabled.browser_urls_enabled)
            self.assertEqual(disabled.input_quiet_ms, 1500)


if __name__ == "__main__":
    unittest.main()
