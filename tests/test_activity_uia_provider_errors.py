"""Real UIA SDK pattern failures with synthetic elements, without desktop calls."""

from __future__ import annotations

import importlib
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from openassist.platform.activity.uia_worker import inspect_tree


if sys.platform == "win32":
    try:
        import comtypes
        import uiautomation as auto
        sdk = importlib.import_module("uiautomation.uiautomation")
    except ImportError:
        auto = None
else:
    auto = None


class SyntheticElement:
    CurrentIsPassword = False
    CurrentIsOffscreen = False
    CurrentIsEnabled = True
    CurrentHasKeyboardFocus = False
    CurrentName = "synthetic name"
    CurrentAutomationId = "synthetic automation id"
    CurrentClassName = "synthetic class"
    CurrentNativeWindowHandle = 0
    CurrentBoundingRectangle = SimpleNamespace(left=1, top=2, right=10, bottom=20)

    def __init__(self, *, availability=None, pattern_failure=None, availability_failure=None,
                 value=None, text=None, events=None):
        self.availability = availability or {}
        self.pattern_failure = pattern_failure
        self.availability_failure = availability_failure
        self.value = value
        self.text = text
        self.events = events if events is not None else []
        self.value_reads = 0
        self.text_counts = []
        self.pattern_calls = []
        self.property_calls = []

    def GetRuntimeId(self):
        return (id(self),)

    def GetCurrentPattern(self, pattern_id):
        self.pattern_calls.append(pattern_id)
        if pattern_id == self.pattern_failure:
            self.events.append("pattern_failure")
            raise comtypes.COMError(-2147467259, "synthetic private provider message", (None, None, None, 0, None))
        if self.value is not None and pattern_id == auto.PatternId.ValuePattern:
            return SyntheticValueInterface(self)
        if self.text is not None and pattern_id == auto.PatternId.TextPattern:
            return SimpleNamespace(DocumentRange=SyntheticTextRange(self))
        return None

    def GetCurrentPropertyValue(self, property_id):
        self.property_calls.append(property_id)
        if property_id == self.availability_failure:
            raise comtypes.COMError(-2147467259, "synthetic private provider message", (None, None, None, 0, None))
        return self.availability.get(property_id, False)


class SyntheticValueInterface:
    CurrentIsReadOnly = False

    def __init__(self, element):
        self.element = element

    @property
    def CurrentValue(self):
        self.element.value_reads += 1
        self.element.events.append("value_read")
        return self.element.value


class SyntheticTextRange:
    def __init__(self, element):
        self.element = element

    def GetText(self, max_length):
        self.element.text_counts.append(max_length)
        return self.element.text if max_length == -1 else self.element.text[:max_length]


@unittest.skipUnless(auto is not None, "requires Windows and the installed uiautomation SDK")
class UIAProviderErrorTests(unittest.TestCase):
    def control(self, element=None, *, cls=None, children=()):
        cls = cls or auto.EditControl
        element = element or SyntheticElement()
        element.CurrentControlType = getattr(auto.ControlType, cls.__name__)
        control = cls(element=element)
        # Keep the SDK's real properties/GetPattern/GetPropertyValue. Only
        # tree enumeration is supplied locally, so no desktop COM API runs.
        control.GetChildren = lambda: list(children)
        return control

    def assert_failed_without_prefix(self, result, reason):
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["missing_reasons"], [reason])
        self.assertEqual(result["nodes"], [])
        self.assertEqual(result["focused_control"], {})
        self.assertEqual(result["node_count"], 0)
        self.assertNotIn("synthetic private provider message", str(result))

    def sdk_pattern_wrapper(self, pattern_id, pattern):
        wrapper = {auto.PatternId.ValuePattern: auto.ValuePattern,
                   auto.PatternId.TextPattern: auto.TextPattern}[pattern_id]
        return wrapper(pattern)

    def test_real_sdk_unsupported_patterns_are_successful_metadata(self):
        element = SyntheticElement()
        control = self.control(element)
        self.assertIsNone(control.GetValuePattern())
        self.assertIsNone(control.GetTextPattern())
        result = inspect_tree(control, 1, 1, include_content=True)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["nodes"][0]["text_status"], "unsupported")
        self.assertEqual(element.property_calls, [auto.PropertyId.IsValuePatternAvailableProperty,
                                                 auto.PropertyId.IsTextPatternAvailableProperty])

    def test_real_sdk_pane_and_button_without_text_methods_do_not_fail(self):
        for cls in (auto.PaneControl, auto.ButtonControl):
            with self.subTest(control=cls.__name__):
                element = SyntheticElement()
                result = inspect_tree(self.control(element, cls=cls), 1, 1, include_content=True)
                self.assertEqual(result["status"], "ok")
                self.assertEqual(result["nodes"][0]["text_status"], "unsupported")
                self.assertEqual(element.pattern_calls, [])
                self.assertEqual(element.property_calls, [auto.PropertyId.IsValuePatternAvailableProperty,
                                                         auto.PropertyId.IsTextPatternAvailableProperty])

    def test_real_sdk_pane_and_button_preserve_advertised_long_value_and_text(self):
        body = "complete \u4e2d\U0001f600 " * 20_000
        for cls in (auto.PaneControl, auto.ButtonControl):
            for kind, pattern_id, property_id in (
                ("value", auto.PatternId.ValuePattern, auto.PropertyId.IsValuePatternAvailableProperty),
                ("text", auto.PatternId.TextPattern, auto.PropertyId.IsTextPatternAvailableProperty),
            ):
                with self.subTest(control=cls.__name__, content=kind):
                    element = SyntheticElement(availability={property_id: True}, **{kind: body})
                    element.CurrentHasKeyboardFocus = True
                    control = self.control(element, cls=cls)
                    self.assertFalse(hasattr(control, "GetValuePattern"))
                    self.assertFalse(hasattr(control, "GetTextPattern"))
                    # Patch the implementation global actually used by
                    # Control.GetPattern, while retaining the real wrappers.
                    with patch.object(sdk, "CreatePattern", side_effect=self.sdk_pattern_wrapper):
                        result = inspect_tree(control, 0, 1, include_content=True, max_text_chars=3, focused=control)
                    self.assertEqual(result["status"], "ok")
                    self.assertEqual(result["nodes"][0][kind], body)
                    self.assertEqual(result["focused_control"][kind], body)
                    self.assertEqual(result["nodes"][0]["text_status"], "ok")
                    self.assertEqual(element.pattern_calls, [pattern_id])
                    self.assertIn(property_id, element.property_calls)
                    if kind == "text":
                        self.assertEqual(element.text_counts, [-1])
                    else:
                        self.assertEqual(element.value_reads, 1)

    def test_real_sdk_swallowed_com_error_is_failure_when_pattern_is_advertised(self):
        for method, pattern_id, property_id in (
            ("GetValuePattern", auto.PatternId.ValuePattern, auto.PropertyId.IsValuePatternAvailableProperty),
            ("GetTextPattern", auto.PatternId.TextPattern, auto.PropertyId.IsTextPatternAvailableProperty),
        ):
            with self.subTest(method=method):
                element = SyntheticElement(availability={property_id: True}, pattern_failure=pattern_id)
                control = self.control(element)
                # This is the real installed SDK behavior being guarded.
                self.assertIsNone(getattr(control, method)())
                result = inspect_tree(control, 1, 1, include_content=True)
                self.assert_failed_without_prefix(result, "uia_pattern_error")
                self.assertIn(property_id, element.property_calls)

    def test_advertised_pattern_returning_none_is_not_unsupported(self):
        for property_id in (auto.PropertyId.IsValuePatternAvailableProperty,
                            auto.PropertyId.IsTextPatternAvailableProperty):
            with self.subTest(property=property_id):
                element = SyntheticElement(availability={property_id: 1})
                self.assert_failed_without_prefix(
                    inspect_tree(self.control(element), 1, 1, include_content=True), "uia_pattern_error")

    def test_real_sdk_generic_advertised_pattern_failure_is_not_unsupported(self):
        for cls in (auto.PaneControl, auto.ButtonControl):
            for pattern_id, property_id in (
                (auto.PatternId.ValuePattern, auto.PropertyId.IsValuePatternAvailableProperty),
                (auto.PatternId.TextPattern, auto.PropertyId.IsTextPatternAvailableProperty),
            ):
                with self.subTest(control=cls.__name__, pattern=pattern_id):
                    element = SyntheticElement(availability={property_id: True}, pattern_failure=pattern_id)
                    result = inspect_tree(self.control(element, cls=cls), 1, 1, include_content=True)
                    self.assert_failed_without_prefix(result, "uia_pattern_error")
                    self.assertEqual(element.pattern_calls, [pattern_id])

    def test_availability_property_com_error_is_failure_not_unsupported(self):
        for property_id in (auto.PropertyId.IsValuePatternAvailableProperty,
                            auto.PropertyId.IsTextPatternAvailableProperty):
            with self.subTest(property=property_id):
                element = SyntheticElement(availability_failure=property_id)
                self.assert_failed_without_prefix(
                    inspect_tree(self.control(element), 1, 1, include_content=True),
                    "uia_pattern_availability_error")

    def test_pattern_failure_discards_successful_real_sdk_value_prefix_and_focus(self):
        events = []
        failure = SyntheticElement(availability={auto.PropertyId.IsTextPatternAvailableProperty: True},
                                   pattern_failure=auto.PatternId.TextPattern, events=events)
        child = self.control(failure)
        prefix = SyntheticElement(value="successful synthetic prefix", events=events)
        root = self.control(prefix, children=(child,))
        # SDK wrappers remain real. The interface constructor alone is replaced
        # because a synthetic element has no live IUnknown COM pointer.
        with patch.object(sdk, "CreatePattern", side_effect=self.sdk_pattern_wrapper):
            result = inspect_tree(root, 1, 1, include_content=True, focused=root)
        self.assert_failed_without_prefix(result, "uia_pattern_error")
        self.assertNotIn("successful synthetic prefix", str(result))
        self.assertEqual(prefix.value_reads, 1)
        self.assertIn(auto.PatternId.TextPattern, failure.pattern_calls)
        self.assertEqual(events, ["value_read", "pattern_failure"])


class NonSDKFallbackTests(unittest.TestCase):
    def test_plain_provider_without_com_property_api_keeps_unsupported_fallback(self):
        control = SimpleNamespace(
            ControlTypeName="EditControl", IsPassword=False, IsOffscreen=False,
            IsEnabled=True, HasKeyboardFocus=False, Name="fixture", AutomationId="fixture", ClassName="fixture",
            BoundingRectangle=SimpleNamespace(left=1, top=2, right=10, bottom=20),
            GetRuntimeId=lambda: (1,), GetValuePattern=lambda: None,
            GetTextPattern=lambda: None, GetChildren=lambda: [],
        )
        result = inspect_tree(control, 1, 1, include_content=True)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["nodes"][0]["text_status"], "unsupported")


if __name__ == "__main__":
    unittest.main()
