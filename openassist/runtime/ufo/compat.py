"""Scoped compatibility fixes for the unmodified UFO kernel dependency."""

from contextlib import contextmanager
from functools import wraps
from importlib import import_module
import logging


logger = logging.getLogger(__name__)


def _load_windows_mcp_servers():
    """Register desktop tools without importing Galaxy's constellation editor."""
    for name in (
        "ui_mcp_server", "cli_mcp_server", "excel_wincom_mcp_server",
        "ppt_wincom_mcp_server", "word_wincom_mcp_server", "pdf_reader_mcp_server",
    ):
        # Normal imports cache successful modules and propagate required-tool
        # failures instead of leaving partially initialized modules registered.
        import_module(f"ufo.client.mcp.local_servers.{name}")


def _recover_controls(window, control_type_list, is_visible, is_enabled):
    from comtypes import COMError
    from ufo.automator.ui_control import inspector

    strategy = inspector.UIABackendStrategy
    uia, constants = strategy._get_uia_defs()
    # The upstream cache request is process-wide. Create a fresh snapshot for
    # this recovery attempt instead of reusing it across MCP worker threads.
    cache = uia.CreateCacheRequest()
    cache.TreeScope = constants.TreeScope_Element
    for prop in (constants.UIA_ControlTypePropertyId, constants.UIA_NamePropertyId,
                 constants.UIA_BoundingRectanglePropertyId):
        cache.AddProperty(prop)
    condition = strategy._get_control_filter_condition(
        control_type_list, is_visible, is_enabled
    )
    elements = window.element_info._element.FindAllBuildCache(
        scope=constants.TreeScope_Descendants, condition=condition, cacheRequest=cache
    )
    controls = []
    skipped = 0
    count = min(elements.Length, 500)
    for index in range(count):
        try:
            element = elements.GetElement(index)
            try:
                control_type = element.CachedControlType
                name = element.CachedName
                bounds = element.CachedBoundingRectangle
            except COMError:
                # Browser providers may expose a live element but fail to
                # supply its cache. Retry its properties without the cache.
                control_type = element.CurrentControlType
                name = element.CurrentName
                bounds = element.CurrentBoundingRectangle
            info = inspector.UIAElementInfoFix(element, True, source="uia")
            rect = inspector.pywinauto.win32structures.RECT()
            rect.left, rect.top = bounds.left, bounds.top
            rect.right, rect.bottom = bounds.right, bounds.bottom
            info._cached_handle = 0
            info._cached_visible = is_visible
            info._cached_rect = rect
            info._cached_name = name
            info._cached_rich_text = name
            info._cached_control_type = strategy._get_uia_control_name_map().get(control_type, "")
            controls.append(inspector.UIAWrapper(info))
        except COMError:
            skipped += 1
    if count and not controls:
        raise RuntimeError("UIA could not read any controls from the selected window")
    logger.info("UIA cache recovery returned %d controls; skipped %d unavailable elements",
                len(controls), skipped)
    return controls


@contextmanager
def native_compatibility():
    """Install product-owned fixes only while a native Windows task runs."""
    from comtypes import COMError
    from ufo.module.context import ContextNames
    from ufo.automator.ui_control.inspector import UIABackendStrategy
    from ufo.client.mcp import local_servers

    original_default = ContextNames.default_value
    original_find = UIABackendStrategy.find_control_elements_in_descendants
    original_loader = local_servers.load_all_servers

    def default_value(name):
        if name is ContextNames.WEAVING_MODE:
            # The upstream property already uses this string when Galaxy is
            # absent. Normal Windows sessions do not consume this Galaxy field.
            return "creation"
        return original_default.fget(name)

    @wraps(original_find)
    def find_controls(self, window, control_type_list=None, class_name_list=None,
                      title_list=None, is_visible=True, is_enabled=True, depth=0):
        try:
            return original_find(self, window, control_type_list or [], class_name_list or [],
                                 title_list or [], is_visible, is_enabled, depth)
        except COMError:
            logger.warning("UIA cached control enumeration failed; rebuilding the snapshot")
            return _recover_controls(window, control_type_list or [], is_visible, is_enabled)

    ContextNames.default_value = property(default_value)
    UIABackendStrategy.find_control_elements_in_descendants = find_controls
    local_servers.load_all_servers = _load_windows_mcp_servers
    try:
        yield
    finally:
        local_servers.load_all_servers = original_loader
        UIABackendStrategy.find_control_elements_in_descendants = original_find
        ContextNames.default_value = original_default
