"""Product-owned UIA recovery without requiring a live desktop."""

from enum import Enum
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, PropertyMock, patch

from mirally.runtime.native_compat import (
    _load_windows_mcp_servers, _recover_controls, native_compatibility,
)


class ProviderError(Exception):
    pass


class NativeCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.uia = Mock()
        self.constants = SimpleNamespace(
            TreeScope_Element=1, TreeScope_Descendants=4,
            UIA_ControlTypePropertyId=30003, UIA_NamePropertyId=30005,
            UIA_BoundingRectanglePropertyId=30001,
        )
        self.original_find = Mock()
        strategy = type("Strategy", (), {
            "_get_uia_defs": staticmethod(lambda: (self.uia, self.constants)),
            "_get_control_filter_condition": Mock(return_value="filter"),
            "_get_uia_control_name_map": staticmethod(lambda: {50000: "Button"}),
            "find_control_elements_in_descendants": self.original_find,
        })
        self.strategy = strategy
        inspector = ModuleType("ufo.automator.ui_control.inspector")
        inspector.UIABackendStrategy = strategy
        inspector.pywinauto = SimpleNamespace(win32structures=SimpleNamespace(RECT=SimpleNamespace))
        inspector.UIAElementInfoFix = Mock(side_effect=lambda *args, **kwargs: SimpleNamespace())
        inspector.UIAWrapper = lambda info: info
        self.window = Mock()
        self.elements = Mock()
        self.window.element_info._element.FindAllBuildCache.return_value = self.elements

        class ContextNames(Enum):
            WEAVING_MODE = "weaving"
            SESSION_STEP = "step"

            @property
            def default_value(self):
                if self is ContextNames.WEAVING_MODE:
                    raise AssertionError("Unexpected Galaxy import")
                return 0

        self.names = ContextNames
        context = ModuleType("ufo.module.context")
        context.ContextNames = ContextNames
        ui_control = ModuleType("ufo.automator.ui_control")
        ui_control.inspector = inspector
        comtypes = ModuleType("comtypes")
        comtypes.COMError = ProviderError
        self.local_servers = ModuleType("ufo.client.mcp.local_servers")
        self.original_loader = Mock()
        self.local_servers.load_all_servers = self.original_loader
        mcp = ModuleType("ufo.client.mcp")
        mcp.local_servers = self.local_servers
        self.modules = patch.dict(sys.modules, {
            "comtypes": comtypes,
            "ufo": ModuleType("ufo"),
            "ufo.module": ModuleType("ufo.module"),
            "ufo.module.context": context,
            "ufo.automator": ModuleType("ufo.automator"),
            "ufo.automator.ui_control": ui_control,
            "ufo.automator.ui_control.inspector": inspector,
            "ufo.client": ModuleType("ufo.client"),
            "ufo.client.mcp": mcp,
            "ufo.client.mcp.local_servers": self.local_servers,
        })
        self.modules.start()
        self.addCleanup(self.modules.stop)

    def element(self, *, cached_error=False, live_error=False):
        element = Mock()
        bounds = SimpleNamespace(left=1, top=2, right=30, bottom=40)
        element.CachedControlType = 50000
        element.CachedName = "cached"
        element.CachedBoundingRectangle = bounds
        element.CurrentControlType = 50000
        element.CurrentName = "live"
        element.CurrentBoundingRectangle = bounds
        if cached_error:
            type(element).CachedControlType = PropertyMock(side_effect=ProviderError("cache"))
        if live_error:
            type(element).CurrentControlType = PropertyMock(side_effect=ProviderError("live"))
        return element

    def recover(self, elements):
        self.elements.Length = len(elements)
        self.elements.GetElement.side_effect = elements
        return _recover_controls(self.window, ["Button"], True, True)

    def test_cached_properties_are_preserved(self):
        controls = self.recover([self.element()])
        self.assertEqual(controls[0]._cached_name, "cached")
        self.assertEqual(controls[0]._cached_control_type, "Button")
        self.assertEqual(controls[0]._cached_rect.bottom, 40)
        self.assertEqual(self.uia.CreateCacheRequest.return_value.TreeScope, 1)
        self.assertEqual(self.uia.CreateCacheRequest.return_value.AddProperty.call_count, 3)

    def test_unavailable_cache_falls_back_to_live_properties(self):
        controls = self.recover([self.element(cached_error=True)])
        self.assertEqual(controls[0]._cached_name, "live")

    def test_bad_elements_do_not_discard_healthy_controls(self):
        controls = self.recover([
            ProviderError("GetElement failed"),
            self.element(cached_error=True, live_error=True), self.element(),
        ])
        self.assertEqual(len(controls), 1)
        self.assertEqual(controls[0]._cached_name, "cached")

    def test_all_unreadable_elements_fail_instead_of_returning_empty_success(self):
        with self.assertRaisesRegex(RuntimeError, "could not read any controls"):
            self.recover([self.element(cached_error=True, live_error=True)])

    def test_empty_window_is_a_valid_empty_result(self):
        self.assertEqual(self.recover([]), [])

    def test_query_failure_and_programming_errors_are_not_hidden(self):
        self.window.element_info._element.FindAllBuildCache.side_effect = ProviderError("query")
        with self.assertRaisesRegex(ProviderError, "query"):
            self.recover([])
        self.window.element_info._element.FindAllBuildCache.side_effect = None
        element = self.element()
        type(element).CachedControlType = PropertyMock(side_effect=ValueError("bad value"))
        with self.assertRaisesRegex(ValueError, "bad value"):
            self.recover([element])

    def test_normal_enumeration_is_unchanged_and_overrides_are_restored(self):
        default = self.names.default_value
        self.original_find.return_value = ["original"]
        with native_compatibility():
            self.assertIs(self.local_servers.load_all_servers, _load_windows_mcp_servers)
            self.assertEqual(self.names.WEAVING_MODE.default_value, "creation")
            self.assertEqual(self.names.SESSION_STEP.default_value, 0)
            self.assertEqual(self.strategy().find_control_elements_in_descendants(self.window),
                             ["original"])
            self.uia.CreateCacheRequest.assert_not_called()
        self.assertIs(self.names.default_value, default)
        self.assertIs(self.strategy.find_control_elements_in_descendants, self.original_find)
        self.assertIs(self.local_servers.load_all_servers, self.original_loader)

    def test_mcp_loader_registers_windows_tools_without_galaxy(self):
        with patch("mirally.runtime.ufo.compat.import_module") as load:
            _load_windows_mcp_servers()
        names = [call.args[0].rsplit(".", 1)[-1] for call in load.call_args_list]
        self.assertEqual(names, [
            "ui_mcp_server", "cli_mcp_server", "excel_wincom_mcp_server",
            "ppt_wincom_mcp_server", "word_wincom_mcp_server", "pdf_reader_mcp_server",
        ])

    def test_required_mcp_import_failure_is_not_hidden(self):
        with patch("mirally.runtime.ufo.compat.import_module",
                   side_effect=ImportError("missing dependency")):
            with self.assertRaisesRegex(ImportError, "missing dependency"):
                _load_windows_mcp_servers()

    def test_com_failure_uses_recovery_and_cleanup_survives_exception(self):
        default = self.names.default_value
        self.original_find.side_effect = ProviderError("original cache")
        self.elements.Length = 1
        self.elements.GetElement.return_value = self.element(cached_error=True)
        with self.assertRaisesRegex(ValueError, "failure inside task"):
            with native_compatibility():
                result = self.strategy().find_control_elements_in_descendants(
                    self.window, ["Button"], is_visible=False, is_enabled=False
                )
                self.assertEqual(result[0]._cached_name, "live")
                self.strategy._get_control_filter_condition.assert_called_with(["Button"], False, False)
                raise ValueError("failure inside task")
        self.assertIs(self.names.default_value, default)
        self.assertIs(self.strategy.find_control_elements_in_descendants, self.original_find)
        self.assertIs(self.local_servers.load_all_servers, self.original_loader)


if __name__ == "__main__":
    unittest.main()
