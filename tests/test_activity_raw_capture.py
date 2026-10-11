"""Complete raw capture contracts using isolated, synthetic provider fixtures."""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, PropertyMock, patch

from openassist.platform.activity import clipboard_worker
from openassist.platform.activity.clipboard import WindowsClipboardSource
from openassist.platform.activity.ocr import WindowsNativeOCRSource
from openassist.platform.activity.uia_structure import WindowsUIASource
from openassist.platform.activity.uia_worker import content_fields, inspect_tree


class RawControl:
    ControlTypeName = "EditControl"
    Name = "synthetic name"
    AutomationId = "synthetic id"
    ClassName = "synthetic class"
    IsPassword = False
    IsOffscreen = False
    IsEnabled = True
    HasKeyboardFocus = False
    BoundingRectangle = SimpleNamespace(left=1, top=2, right=10, bottom=20)

    def __init__(self, value="", text=None, children=()):
        self.value = value
        self.text = value if text is None else text
        self.children = children
        self.text_counts = []

    def GetRuntimeId(self):
        return (id(self),)

    def GetChildren(self):
        return self.children

    def GetValuePattern(self):
        return SimpleNamespace(Value=self.value, IsReadOnly=False)

    def GetTextPattern(self):
        def get_text(count):
            self.text_counts.append(count)
            return self.text if count == -1 else self.text[:count]
        return SimpleNamespace(DocumentRange=SimpleNamespace(GetText=get_text))


class RawCaptureTests(unittest.TestCase):
    def test_uia_full_properties_and_document_range_ignore_old_character_limit(self):
        control = RawControl("value \u4e2d\U0001f600" * 20_000, "text \u4e2d\U0001f600" * 20_000)
        control.Name = "name " * 20_000
        control.AutomationId = "id " * 20_000
        control.ClassName = "class " * 20_000
        fields = content_fields(control, 3)
        for property_name, key in (("Name", "name"), ("AutomationId", "automation_id"),
                                   ("ClassName", "class_name"), ("value", "value"), ("text", "text")):
            self.assertEqual(fields[key], getattr(control, property_name))
        self.assertEqual(control.text_counts, [-1])
        self.assertFalse(fields.get("truncated", False))

    def test_uia_complete_broad_tree_exceeds_legacy_node_and_response_budgets(self):
        controls = [RawControl(str(index) + "\u4e2d" * 500) for index in range(250)]
        result = inspect_tree(RawControl(children=controls), 0, 1, include_content=True, max_text_chars=3)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["node_count"], 251)
        self.assertEqual([node["value"] for node in result["nodes"][1:]], [control.value for control in controls])
        self.assertGreater(len(json.dumps(result, ensure_ascii=False).encode("utf-8")), 64 * 1024)
        self.assertFalse(result["truncated"])
        self.assertEqual(result["missing_reasons"], [])

    def test_uia_iterative_walk_keeps_deep_tree_past_python_recursion_limit(self):
        leaf = RawControl("deep leaf")
        leaf.HasKeyboardFocus = True
        root = leaf
        for _ in range(1_100):
            root = RawControl(children=(root,))
        result = inspect_tree(root, 0, 1, include_content=True, focused=leaf)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["node_count"], 1_101)
        self.assertEqual(result["max_depth"], 1_100)
        self.assertEqual(result["nodes"][-1]["value"], "deep leaf")
        self.assertEqual(result["focused_control"]["value"], "deep leaf")
        self.assertFalse(result["truncated"])

    def test_uia_privacy_scope_excludes_hidden_password_and_unknown_subtrees(self):
        for reason, fields in (("offscreen", {"IsOffscreen": True}),
                               ("password", {"IsPassword": True}),
                               ("password_state_unknown", {"IsPassword": None}),
                               ("visibility_unknown", {"IsOffscreen": None})):
            with self.subTest(reason=reason):
                child = RawControl("excluded descendant")
                excluded = RawControl("excluded content", children=(child,))
                for key, value in fields.items():
                    setattr(excluded, key, value)
                with patch.object(excluded, "GetValuePattern", side_effect=AssertionError("private read")) as value, \
                     patch.object(excluded, "GetChildren", side_effect=AssertionError("private traversal")) as children:
                    result = inspect_tree(RawControl(children=(excluded,)), 0, 1,
                                          include_content=True, focused=child)
                value.assert_not_called()
                children.assert_not_called()
                self.assertEqual(result["status"], "ok")
                self.assertEqual(result["node_count"], 2)
                self.assertEqual(result["nodes"][1]["scope_excluded"], reason)
                self.assertIn(reason, result["capture_scope"]["excluded_subtrees"])
                self.assertEqual(result["focused_control"], {})
                self.assertNotIn("excluded content", json.dumps(result))
                self.assertNotIn("excluded descendant", json.dumps(result))

    def test_uia_focus_outside_target_tree_cannot_bypass_declared_scope(self):
        result = inspect_tree(RawControl("included"), 0, 1, include_content=True,
                              focused=RawControl("outside target"))
        self.assertEqual(result["focused_control"], {})
        self.assertNotIn("outside target", json.dumps(result))

    def test_uia_privacy_state_change_before_content_read_blocks_subtree(self):
        for property_name, reason in (("IsPassword", "password"), ("IsOffscreen", "offscreen")):
            with self.subTest(property_name=property_name):
                control = RawControl("excluded content", children=(RawControl("excluded descendant"),))
                with patch.object(RawControl, property_name, new_callable=PropertyMock,
                                  side_effect=[False, True]), \
                     patch.object(control, "GetChildren", side_effect=AssertionError("private traversal")) as children:
                    result = inspect_tree(control, 0, 1, include_content=True, focused=control)
                children.assert_not_called()
                self.assertEqual(result["status"], "ok")
                self.assertEqual(result["nodes"][0]["scope_excluded"], reason)
                self.assertEqual(result["focused_control"], {})
                self.assertNotIn("excluded content", json.dumps(result))
                self.assertNotIn("excluded descendant", json.dumps(result))

    def test_uia_structure_only_does_not_read_content_but_keeps_complete_tree(self):
        child = RawControl("not requested")
        root = RawControl(children=(child,))
        root.ControlTypeName = "role" * 50
        with patch.object(root, "GetValuePattern", side_effect=AssertionError("not requested")), \
             patch.object(child, "GetValuePattern", side_effect=AssertionError("not requested")):
            result = inspect_tree(root, 0, 1)
        self.assertEqual(result["node_count"], 2)
        self.assertEqual(result["nodes"][0]["role"], root.ControlTypeName)
        self.assertNotIn("name", result["nodes"][0])
        self.assertNotIn("not requested", json.dumps(result))

    def test_uia_child_read_failure_discards_every_successful_prefix(self):
        failing = RawControl("partly observed")
        root = RawControl("prefix", children=(RawControl("prefix child"), failing))
        with patch.object(failing, "GetChildren", side_effect=RuntimeError("private provider text")):
            result = inspect_tree(root, 0, 1, include_content=True, focused=root)
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["missing_reasons"], ["uia_children_error"])
        self.assertEqual(result["nodes"], [])
        self.assertEqual(result["focused_control"], {})
        self.assertNotIn("prefix", json.dumps(result))
        self.assertNotIn("private provider text", json.dumps(result))

    def test_uia_pattern_failure_discards_partial_fields(self):
        control = RawControl("value prefix")
        with patch.object(control, "GetTextPattern", side_effect=RuntimeError("private provider text")):
            result = inspect_tree(control, 0, 1, include_content=True)
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["missing_reasons"], ["uia_pattern_error"])
        self.assertEqual(result["nodes"], [])
        self.assertNotIn("value prefix", json.dumps(result))

    def test_uia_property_failure_is_not_silently_saved_as_empty_success(self):
        control = RawControl("value prefix")
        with patch.object(RawControl, "Name", new_callable=PropertyMock,
                          side_effect=AttributeError("private provider text")):
            result = inspect_tree(control, 0, 1, include_content=True)
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["missing_reasons"], ["uia_property_error"])
        self.assertEqual(result["nodes"], [])
        self.assertNotIn("private provider text", json.dumps(result))

    def test_uia_provider_cycle_is_failure_without_successful_prefix(self):
        root = RawControl("prefix")
        root.children = (root,)
        result = inspect_tree(root, 0, 1, include_content=True)
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["missing_reasons"], ["uia_tree_cycle"])
        self.assertEqual(result["nodes"], [])

    def test_uia_parent_accepts_full_response_larger_than_64_kib(self):
        body = "\u4e2d\U0001f600" * 30_000
        scope = {"excluded_subtrees": ["offscreen", "password"]}
        envelope = {"status": "ok", "node_count": 1, "nodes": [{"role": "EditControl", "value": body}],
                    "focused_control": {"value": body}, "capture_scope": scope}
        process = Mock(returncode=0)
        process.communicate.return_value = (json.dumps(envelope, ensure_ascii=False), None)
        source = WindowsUIASource()
        source.available = True
        with patch("openassist.platform.activity.uia_structure.subprocess.Popen", return_value=process):
            result = source.inspect(1, include_content=True, max_text_chars=3, max_depth=0, max_nodes=1)
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.nodes[0]["value"], body)
        self.assertEqual(result.focused_control["value"], body)
        self.assertEqual(result.as_dict()["capture_scope"], scope)
        self.assertFalse(result.truncated)

    def test_uia_parent_deadline_kills_worker_and_never_returns_timeout_prefix(self):
        process = Mock(returncode=0)
        process.communicate.side_effect = [subprocess.TimeoutExpired("fixture", 0.1, output="partial content"),
                                          ("partial content", None)]
        source = WindowsUIASource()
        source.available = True
        with patch("openassist.platform.activity.uia_structure.subprocess.Popen", return_value=process):
            result = source.inspect(1, timeout_ms=100, include_content=True)
        process.kill.assert_called_once()
        self.assertEqual(result.status, "timeout")
        self.assertEqual(result.missing_reasons, ("worker_deadline",))
        self.assertEqual(result.nodes, ())
        self.assertEqual(result.focused_control, {})

    def test_uia_parent_failed_worker_cannot_publish_successful_prefix(self):
        process = Mock(returncode=1)
        process.communicate.return_value = ('{"status":"ok","nodes":[{"value":"prefix"}]}', None)
        source = WindowsUIASource()
        source.available = True
        with patch("openassist.platform.activity.uia_structure.subprocess.Popen", return_value=process):
            result = source.inspect(1, include_content=True)
        self.assertEqual(result.missing_reasons, ("worker_exit",))
        self.assertEqual(result.nodes, ())

    def test_ocr_parent_preserves_complete_text_lines_and_words(self):
        body = "\u4e2d\U0001f600" * 30_000
        lines = [{"text": body, "words": [{"text": body, "bounds": [1, 2, 3, 4]}]}] * 8
        envelope = {"status": "ok", "text": body, "lines": lines}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "synthetic.png"
            path.write_bytes(b"fixture only; provider process is mocked")
            source = WindowsNativeOCRSource()
            source.available = True
            with patch("openassist.platform.activity.ocr.sys.platform", "win32"), \
                 patch("openassist.platform.activity.ocr.subprocess.run", return_value=
                       subprocess.CompletedProcess([], 0, json.dumps(envelope, ensure_ascii=False), "")):
                result = source.recognize(path)
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.text, body)
        self.assertEqual(list(result.lines), lines)
        self.assertNotIn("ocr_result_limit", result.missing_reasons)

    def test_ocr_parent_deadline_never_returns_partial_worker_text(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "synthetic.png"
            path.write_bytes(b"fixture")
            source = WindowsNativeOCRSource()
            source.available = True
            with patch("openassist.platform.activity.ocr.sys.platform", "win32"), \
                 patch("openassist.platform.activity.ocr.subprocess.run", side_effect=
                       subprocess.TimeoutExpired("fixture", 0.1, output='{"text":"prefix"}')):
                result = source.recognize(path, timeout_ms=100)
        self.assertEqual(result.status, "timeout")
        self.assertEqual(result.text, "")
        self.assertEqual(result.lines, ())
        self.assertEqual(result.missing_reasons, ("worker_deadline",))

    def clipboard_fixture(self, data, sequences=(11, 11)):
        user = Mock()
        user.GetClipboardSequenceNumber.side_effect = sequences
        user.OpenClipboard.return_value = True
        user.IsClipboardFormatAvailable.return_value = True
        user.GetClipboardData.return_value = 5
        user.GetClipboardOwner.return_value = 9
        kernel = Mock()
        kernel.GlobalLock.return_value = 123
        kernel.GlobalSize.return_value = len(data)
        return user, kernel

    def read_clipboard_fixture(self, data, sequences=(11, 11)):
        user, kernel = self.clipboard_fixture(data, sequences)
        with patch.object(clipboard_worker.sys, "platform", "win32"), \
             patch.object(clipboard_worker.ctypes, "windll", SimpleNamespace(user32=user, kernel32=kernel), create=True), \
             patch.object(clipboard_worker.ctypes, "string_at", return_value=data) as read:
            result = clipboard_worker.read(11, 3)
        read.assert_called_once_with(123, len(data))
        kernel.GlobalUnlock.assert_called_once_with(5)
        user.CloseClipboard.assert_called_once()
        return result

    def test_clipboard_worker_reads_full_global_size_and_preserves_unicode(self):
        body = "\u4e2d\U0001f600a" * 30_000
        result = self.read_clipboard_fixture((body + "\x00").encode("utf-16-le"))
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["text"], body)
        self.assertEqual(result["sequence"], 11)
        self.assertFalse(result["truncated"])

    def test_clipboard_worker_invalid_or_unterminated_text_is_failure_without_prefix(self):
        for data, reason in (("prefix".encode("utf-16-le"), "clipboard_unterminated_text"),
                             (b"\x00\xd8\x00\x00", "clipboard_invalid_unicode")):
            with self.subTest(reason=reason):
                result = self.read_clipboard_fixture(data)
                self.assertEqual(result["status"], "error")
                self.assertEqual(result["missing_reasons"], [reason])
                self.assertNotIn("text", result)

    def test_clipboard_worker_sequence_replacement_discards_complete_but_stale_text(self):
        result = self.read_clipboard_fixture("prefix\x00".encode("utf-16-le"), (11, 12))
        self.assertEqual(result["status"], "stale")
        self.assertEqual(result["missing_reasons"], ["clipboard_replaced"])
        self.assertNotIn("text", result)

    def test_clipboard_parent_accepts_large_full_response_ignoring_old_max_chars(self):
        body = "\u4e2d\U0001f600" * 50_000
        output = json.dumps({"status": "ok", "sequence": 11, "text": body, "truncated": False})
        self.assertGreater(len(output), 160_000)
        source = WindowsClipboardSource()
        source.available = True
        with patch("openassist.platform.activity.clipboard.subprocess.run", return_value=
                   subprocess.CompletedProcess([], 0, output, "")):
            result = source.read(sequence=11, max_chars=3)
        self.assertEqual(result["text"], body)
        self.assertFalse(result["truncated"])

    def test_clipboard_parent_deadline_never_publishes_partial_text(self):
        source = WindowsClipboardSource()
        source.available = True
        with patch("openassist.platform.activity.clipboard.subprocess.run", side_effect=
                   subprocess.TimeoutExpired("fixture", 0.1, output='{"text":"prefix"}')):
            result = source.read(timeout_ms=100)
        self.assertEqual(result["status"], "timeout")
        self.assertEqual(result["missing_reasons"], ["worker_deadline"])
        self.assertNotIn("text", result)


if __name__ == "__main__":
    unittest.main()
