"""Scoped desktop observation isolation for the unmodified UFO runtime.

The command boundary owns the desktop lease. These leaf hooks perform only
the UI capture handshake, so a tool running on an MCP worker never tries to
reacquire a lease held by the command's awaiting thread.
"""

from __future__ import annotations

from contextlib import contextmanager, ExitStack
from functools import wraps
import os
import threading

from mirally.core.execution import ExecutionCancelled


def _native_targets():
    # The caller must enter native_compatibility before importing these modules.
    from ufo.automator.ui_control.screenshot import ControlPhotographer, DesktopPhotographer
    from ufo.automator.ui_control.inspector import ControlInspectorFacade
    from ufo.client.mcp.local_servers import ui_mcp_server

    return ControlPhotographer, DesktopPhotographer, ControlInspectorFacade, ui_mcp_server


def _process_id(control):
    """Read ownership without trusting titles or the currently focused window."""
    for read in (
        lambda: control.process_id(),
        lambda: control.element_info.process_id,
        lambda: control.top_level_parent().process_id(),
    ):
        try:
            value = read()
            if isinstance(value, int) and value > 0:
                return value
        except Exception:
            # Stale UIA elements can no longer expose their owner. They are
            # discarded by the caller rather than becoming selectable targets.
            continue
    return None


@contextmanager
def observation_isolation(services, excluded_process_ids=None):
    """Isolate captures and UIA targets for one serialized native task.

    Class hooks are necessary because UFO constructs photographers inside its
    MCP workers. The native executor serializes task scopes; every hook is
    restored on exit. Concurrent captures serialize, and recursive window to
    desktop fallback shares a single UI handshake.
    """
    Control, Desktop, Inspector, ui_module = _native_targets()
    def excluded():
        values = excluded_process_ids() if callable(excluded_process_ids) else excluded_process_ids
        return set(values or ()) | {os.getpid()}
    capture_lock = threading.RLock()
    local = threading.local()
    state = ui_module.UIServerState()

    def allowed(control):
        pid = _process_id(control)
        return pid is not None and pid not in excluded()

    def require_allowed(control):
        if not allowed(control):
            raise ui_module.ToolError("The selected window or control is unavailable or belongs to Mirally.")

    def clear_targets():
        # UI servers outlive an individual task. A previous task's numeric
        # labels must never remain selectable, even with refresh=False.
        for name in ("last_app_windows", "selected_app_window", "selected_app_window_controls",
                     "control_dict", "puppeteer"):
            setattr(state, name, None)

    def check_cancelled():
        if services.cancelled():
            raise ExecutionCancelled()

    def wrap_capture(original, *, control=False):
        @wraps(original)
        def capture(self, *args, **kwargs):
            check_cancelled()
            if control:
                require_allowed(self.control)
            with capture_lock:
                check_cancelled()
                if getattr(local, "capturing", False):
                    return original(self, *args, **kwargs)
                local.capturing = True
                try:
                    # Cleanup is required even when before raises after only
                    # part of the UI has been hidden.
                    try:
                        services.capture("before")
                        check_cancelled()
                        return original(self, *args, **kwargs)
                    finally:
                        services.capture("after")
                finally:
                    local.capturing = False
        return capture

    original_windows = Inspector.get_desktop_windows
    original_descendants = Inspector.find_control_elements_in_descendants
    original_dict_info = Inspector.get_control_info_list_of_dict
    original_initialize = ui_module.UIServerState.initialize_for_window
    original_verify = ui_module._verify_id

    @wraps(original_windows)
    def desktop_windows(self, *args, **kwargs):
        # get_desktop_app_dict numbers this already-filtered list.
        return [window for window in original_windows(self, *args, **kwargs) if allowed(window)]

    @wraps(original_descendants)
    def descendants(self, window, *args, **kwargs):
        require_allowed(window)
        return [item for item in original_descendants(self, window, *args, **kwargs) if allowed(item)]

    @wraps(original_dict_info)
    def dict_info(self, window_dict, *args, **kwargs):
        # This also covers cached refresh=False dictionaries. Mutate the map
        # UFO will later use for selection, not only the model-facing result.
        for label, window in list(window_dict.items()):
            if not allowed(window):
                del window_dict[label]
        return original_dict_info(self, window_dict, *args, **kwargs)

    @wraps(original_initialize)
    def initialize(self, window):
        require_allowed(window)
        return original_initialize(self, window)

    @wraps(original_verify)
    def verify(id, name, control_dict):
        if control_dict and id in control_dict:
            require_allowed(control_dict[id])
        # Host tools call this before set_focus; guarding initialize alone
        # would permit a focus side effect before rejecting an own window.
        return original_verify(id, name, control_dict)

    clear_targets()
    with ExitStack() as restore:
        def install(owner, name, replacement):
            previous = getattr(owner, name)
            restore.callback(setattr, owner, name, previous)
            setattr(owner, name, replacement)

        install(Control, "capture", wrap_capture(Control.capture, control=True))
        install(Desktop, "capture", wrap_capture(Desktop.capture))
        install(Inspector, "get_desktop_windows", desktop_windows)
        install(Inspector, "find_control_elements_in_descendants", descendants)
        install(Inspector, "get_control_info_list_of_dict", dict_info)
        install(ui_module.UIServerState, "initialize_for_window", initialize)
        install(ui_module, "_verify_id", verify)
        try:
            yield
        finally:
            # The executor drains its tool workers before closing this scope.
            # Joining any final capture protects cleanup from early unpatching.
            with capture_lock:
                clear_targets()
