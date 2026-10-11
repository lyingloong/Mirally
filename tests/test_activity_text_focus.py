"""Focus and text-pattern regressions without reading the real desktop."""

from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from openassist.core.activity.input_text import InputTextTracker
from openassist.platform.activity.uia_structure import WindowsUIASource
from openassist.platform.activity.uia_worker import inspect_tree


class Control:
    IsPassword = False
    IsOffscreen = False
    IsEnabled = True
    Name = "synthetic label"
    AutomationId = "synthetic editor"
    ClassName = "synthetic class"
    BoundingRectangle = SimpleNamespace(left=1, top=2, right=100, bottom=100)

    def __init__(self, *, role="EditControl", focused=False, value="", text="", children=()):
        self.ControlTypeName = role
        self.HasKeyboardFocus = focused
        self.value, self.text, self.children = value, text, children

    def GetRuntimeId(self):
        return (id(self),)

    def GetChildren(self):
        return self.children

    def GetValuePattern(self):
        return SimpleNamespace(Value=self.value, IsReadOnly=False)

    def GetTextPattern(self):
        return SimpleNamespace(DocumentRange=SimpleNamespace(GetText=lambda count: self.text))


def editor(text=None, **fields):
    result = {"control_id": "synthetic editor", "role": "EditControl", "text_status": "ok",
              "focused": True, "enabled": True, "password": False, "read_only": False, "value": ""}
    if text is not None:
        result["text"] = text
    return result | fields


class ActivityTextFocusTests(unittest.TestCase):
    def test_empty_value_does_not_hide_complete_text_pattern_or_chinese_edits(self):
        tracker = InputTextTracker()
        tracker.observe("window", editor(""), input_trigger=False)
        text = "中文🙂" * 25000
        inserted = tracker.observe("window", editor(text), input_trigger=True)
        self.assertEqual(inserted["operation"], "insert")
        self.assertEqual(inserted["inserted_text"], text)
        self.assertEqual(inserted["text_source"], "text")
        self.assertTrue(inserted["baseline_known"])
        replaced = tracker.observe("window", editor(text[:-1] + "新"), input_trigger=True)
        self.assertEqual(replaced["operation"], "replace")
        self.assertEqual(replaced["inserted_text"], "新")
        self.assertEqual(replaced["removed_char_count"], 1)
        self.assertEqual(tracker.observe("window", editor(""), input_trigger=True)["operation"], "delete")

    def test_first_complete_document_stays_unknown_snapshot(self):
        tracker = InputTextTracker()
        result = tracker.observe("window", editor("already present\r", value=""), input_trigger=True)
        self.assertEqual(result["text"], "already present\r")
        self.assertEqual(result["operation"], "snapshot")
        self.assertFalse(result["baseline_known"])
        self.assertIsNone(result["inserted_text"])

    def test_value_pattern_only_keeps_complete_fallback(self):
        tracker = InputTextTracker()
        tracker.observe("window", editor(value="before"), input_trigger=False)
        result = tracker.observe("window", editor(value="before after"), input_trigger=True)
        self.assertEqual(result["inserted_text"], " after")
        self.assertEqual(result["text_source"], "value")

    def test_representation_change_does_not_invent_a_committed_edit(self):
        tracker = InputTextTracker()
        tracker.observe("window", editor(value="same"), input_trigger=False)
        result = tracker.observe("window", editor("same\r", value="same"), input_trigger=True)
        self.assertEqual(result["operation"], "snapshot")
        self.assertFalse(result["baseline_known"])
        self.assertEqual(result["text"], "same\r")
        self.assertIsNone(tracker.observe("window", editor("same\r", value="same"), input_trigger=True))
        result = tracker.observe("window", editor(value="same"), input_trigger=True)
        self.assertEqual(result["operation"], "snapshot")
        self.assertEqual(result["text_source"], "value")

    def test_stale_disabled_private_and_missing_text_do_not_replace_baseline(self):
        tracker = InputTextTracker()
        tracker.observe("window", editor("before"), input_trigger=False)
        for fields in ({"focused": False}, {"enabled": False}, {"scope_excluded": "offscreen"},
                       {"password": True}, {"read_only": True}, {"text_status": "unsupported"}):
            with self.subTest(fields=fields):
                self.assertIsNone(tracker.observe("window", editor("private or stale", **fields), input_trigger=True))
        self.assertIsNone(tracker.observe("window", editor(text=None, value=None), input_trigger=True))
        self.assertEqual(tracker.observe("window", editor("before after"), input_trigger=True)["inserted_text"], " after")

    def test_verified_api_focus_wins_over_window_and_other_provider_flags(self):
        selected = Control(focused=True, text="actual focused text")
        sibling = Control(focused=True, text="another provider flag")
        root = Control(role="WindowControl", focused=True, children=(selected, sibling))
        result = inspect_tree(root, 0, 1, include_content=True, focused=selected)
        self.assertEqual(result["focus_status"], "api_verified")
        self.assertEqual(result["focused_control"]["text"], "actual focused text")
        self.assertEqual(result["node_count"], 3)

    def test_stale_api_candidate_is_replaced_only_by_unique_tree_focus(self):
        old = Control(focused=False, text="stale focused text")
        actual = Control(focused=True, text="actual focused text")
        root = Control(role="WindowControl", focused=True, children=(old, actual))
        result = inspect_tree(root, 0, 1, include_content=True, focused=old)
        self.assertEqual(result["focus_status"], "tree_verified")
        self.assertEqual(result["focused_control"]["text"], "actual focused text")
        self.assertEqual([n["text"] for n in result["nodes"]], ["", "stale focused text", "actual focused text"])

    def test_missing_api_candidate_uses_unique_tree_focus(self):
        actual = Control(focused=True, text="actual focused text")
        result = inspect_tree(Control(role="WindowControl", focused=True, children=(actual,)),
                              0, 1, include_content=True)
        self.assertEqual(result["focus_status"], "tree_verified")
        self.assertEqual(result["focused_control"]["text"], "actual focused text")

    def test_ambiguous_tree_flags_do_not_pick_an_arbitrary_editor(self):
        old = Control()
        root = Control(role="WindowControl", focused=True,
                       children=(old, Control(focused=True), Control(focused=True)))
        result = inspect_tree(root, 0, 1, include_content=True, focused=old)
        self.assertEqual(result["focus_status"], "focus_ambiguous")
        self.assertEqual(result["focused_control"], {})
        self.assertEqual(result["node_count"], 4)

    def test_external_focus_cannot_be_substituted_with_target_tree_flag(self):
        root = Control(role="WindowControl", focused=True, children=(Control(focused=True),))
        result = inspect_tree(root, 0, 1, include_content=True, focused=Control(focused=True))
        self.assertEqual(result["focus_status"], "focus_outside_scope")
        self.assertEqual(result["focused_control"], {})

    def test_window_flag_alone_or_stale_candidate_does_not_claim_editor_focus(self):
        result = inspect_tree(Control(role="WindowControl", focused=True), 0, 1, include_content=True)
        self.assertEqual(result["focus_status"], "focus_not_observed")
        self.assertEqual(result["focused_control"], {})
        stale = Control(focused=False)
        result = inspect_tree(stale, 0, 1, include_content=True, focused=stale)
        self.assertEqual(result["focus_status"], "focus_stale")
        self.assertEqual(result["focused_control"], {})

    def test_excluded_subtree_focus_never_reads_content_or_descendants(self):
        for property_name, reason in (("IsPassword", True), ("IsPassword", None),
                                      ("IsOffscreen", True), ("IsOffscreen", None)):
            with self.subTest(property=property_name, value=reason):
                child = Control(focused=True, text="private descendant")
                excluded = Control(focused=True, text="private root", children=(child,))
                setattr(excluded, property_name, reason)
                root = Control(role="WindowControl", focused=True, children=(excluded,))
                with patch.object(excluded, "GetValuePattern", side_effect=AssertionError("private read")) as pattern, \
                     patch.object(excluded, "GetChildren", side_effect=AssertionError("private traversal")) as children:
                    result = inspect_tree(root, 0, 1, include_content=True, focused=child)
                pattern.assert_not_called()
                children.assert_not_called()
                self.assertEqual(result["focused_control"], {})
                self.assertEqual(result["focus_status"], "focus_outside_scope")
                self.assertNotIn("private root", json.dumps(result))
                self.assertNotIn("private descendant", json.dumps(result))

    def test_focus_diagnostic_survives_isolated_source_envelope(self):
        source = WindowsUIASource()
        source.available = True
        process = Mock(returncode=0)
        process.communicate.return_value = (json.dumps({"status": "ok", "focus_status": "focus_ambiguous"}), None)
        with patch("openassist.platform.activity.uia_structure.subprocess.Popen", return_value=process):
            result = source.inspect(1, include_content=True)
        self.assertEqual(result.focus_status, "focus_ambiguous")
        self.assertEqual(result.as_dict()["focus_status"], "focus_ambiguous")


if __name__ == "__main__":
    unittest.main()
