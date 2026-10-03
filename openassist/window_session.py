"""Windows input ownership, capture handshake and focus-independent hotkeys."""

from __future__ import annotations

import ctypes
import time
from ctypes import wintypes

from openassist.raw_pointer import RawPointerMonitor, WM_INPUT


def enable_dpi_awareness():
    """Use physical desktop coordinates consistently with capture and input."""
    try:
        function = ctypes.windll.user32.SetProcessDpiAwarenessContext
        function.argtypes = [ctypes.c_void_p]
        function.restype = wintypes.BOOL
        function(ctypes.c_void_p(-4))  # PER_MONITOR_AWARE_V2, before Tk creates HWNDs
    except AttributeError:
        ctypes.windll.user32.SetProcessDPIAware()


class WindowSession:
    def __init__(self, root, *, demo=False):
        self.root = root
        self.demo = demo
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        u = self.user32
        for name, args, result in (
            ("GetAncestor", [wintypes.HWND, wintypes.UINT], wintypes.HWND),
            ("GetWindowRect", [wintypes.HWND, ctypes.POINTER(wintypes.RECT)], wintypes.BOOL),
            ("GetForegroundWindow", [], wintypes.HWND),
            ("IsWindow", [wintypes.HWND], wintypes.BOOL),
            ("IsWindowVisible", [wintypes.HWND], wintypes.BOOL),
            ("SetForegroundWindow", [wintypes.HWND], wintypes.BOOL),
            ("SetWindowDisplayAffinity", [wintypes.HWND, wintypes.DWORD], wintypes.BOOL),
            ("GetWindowDisplayAffinity", [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL),
            ("MonitorFromWindow", [wintypes.HWND, wintypes.DWORD], wintypes.HANDLE),
            ("GetMonitorInfoW", [wintypes.HANDLE, ctypes.c_void_p], wintypes.BOOL),
            ("SetWindowRgn", [wintypes.HWND, wintypes.HANDLE, wintypes.BOOL], ctypes.c_int),
            ("ShowWindow", [wintypes.HWND, ctypes.c_int], wintypes.BOOL),
            ("GetWindowLongPtrW", [wintypes.HWND, ctypes.c_int], ctypes.c_ssize_t),
            ("SetWindowLongPtrW", [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t], ctypes.c_ssize_t),
            ("SetWindowPos", [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT], wintypes.BOOL),
            ("RedrawWindow", [wintypes.HWND, ctypes.c_void_p, wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
            ("SetLayeredWindowAttributes", [wintypes.HWND, wintypes.DWORD, ctypes.c_ubyte, wintypes.DWORD], wintypes.BOOL),
            ("RegisterHotKey", [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT], wintypes.BOOL),
            ("UnregisterHotKey", [wintypes.HWND, ctypes.c_int], wintypes.BOOL),
            ("PeekMessageW", [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT], wintypes.BOOL),
            ("MsgWaitForMultipleObjectsEx", [wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
                                           wintypes.DWORD, wintypes.DWORD], wintypes.DWORD),
            ("TranslateMessage", [ctypes.POINTER(wintypes.MSG)], wintypes.BOOL),
            ("DispatchMessageW", [ctypes.POINTER(wintypes.MSG)], ctypes.c_ssize_t),
        ):
            function = getattr(u, name)
            function.argtypes, function.restype = args, result
        self.external_window = int(u.GetForegroundWindow() or 0)
        self.working = False
        self.capture_hidden = False
        self.capture_active = False
        self.prefer_native_capture = True
        self.was_visible = False
        self.topmost = True
        self.hotkeys = {}
        self.last_human_pointer = 0.0
        self.pointer_monitor_ready = False
        self._pointer_monitor = RawPointerMonitor(u)
        self._corner_key = None
        self._corner_gdi = None
        self._transitions_disabled_for = None

    @property
    def hwnd(self):
        child = self.root.winfo_id()
        return int(self.user32.GetAncestor(child, 2) or child)

    def remember_external(self):
        current = int(self.user32.GetForegroundWindow() or 0)
        if current and current != self.hwnd:
            self.external_window = current

    def work_area(self):
        class MonitorInfo(ctypes.Structure):
            _fields_ = [("size", wintypes.DWORD), ("monitor", wintypes.RECT),
                        ("work", wintypes.RECT), ("flags", wintypes.DWORD)]
        info = MonitorInfo()
        info.size = ctypes.sizeof(info)
        monitor = self.user32.MonitorFromWindow(self.hwnd, 2)
        if self.user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            r = info.work
            return r.left, r.top, r.right, r.bottom
        return 0, 0, self.root.winfo_screenwidth(), self.root.winfo_screenheight()

    def window_rect(self):
        rect = wintypes.RECT()
        if not self.user32.GetWindowRect(self.hwnd, ctypes.byref(rect)):
            raise ctypes.WinError(ctypes.get_last_error())
        return rect.right - rect.left, rect.bottom - rect.top, rect.left, rect.top

    def suppress_system_transitions(self):
        """Use our tween alone; prevent DWM from animating its intermediate sizes."""
        hwnd = self.hwnd
        if self._transitions_disabled_for == hwnd:
            return
        disabled = wintypes.BOOL(True)
        set_attribute = ctypes.windll.dwmapi.DwmSetWindowAttribute
        set_attribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
        set_attribute.restype = ctypes.c_long
        if set_attribute(hwnd, 3, ctypes.byref(disabled), ctypes.sizeof(disabled)) != 0:
            raise RuntimeError("无法关闭本窗口的系统过渡")
        self._transitions_disabled_for = hwnd

    def round_corners(self, radius=0, *, size=None, redraw=True, offset=(0, 0)):
        hwnd = self.hwnd
        width, height = size or (self.root.winfo_width(), self.root.winfo_height())
        left, top = offset
        key = (hwnd, radius, width, height, left, top) if radius else (hwnd, 0)
        if self._corner_key == key:
            return
        if not radius:
            self.user32.SetWindowRgn(hwnd, None, redraw)
            self._corner_key = key
            return
        if self._corner_gdi is None:
            self._corner_gdi = ctypes.WinDLL("gdi32", use_last_error=True)
            self._corner_gdi.CreateRoundRectRgn.argtypes = [ctypes.c_int] * 6
            self._corner_gdi.CreateRoundRectRgn.restype = wintypes.HANDLE
            self._corner_gdi.DeleteObject.argtypes = [wintypes.HANDLE]
        gdi = self._corner_gdi
        # Size comes from the native animation frame, not a stale Tk configure
        # event. Reusing the GDI bindings also avoids loading a DLL per frame.
        region = gdi.CreateRoundRectRgn(left, top, left + width + 1, top + height + 1, radius * 2, radius * 2)
        if region:
            if self.user32.SetWindowRgn(hwnd, region, redraw):
                self._corner_key = key
            else:
                gdi.DeleteObject(region)  # On success ownership passes to Windows.

    def resize_surface(self, rect, radius, *, clip=None):
        """Commit one complete edge frame without exposing erased child surfaces.

        Native resize and region changes normally repaint independently, before
        Tk has processed its child layout. Defer redraw until all three agree.
        Never hide/show or pump input events here.
        """
        width, height, x, y = (int(value) for value in rect)
        self.suppress_system_transitions()
        hwnd = self.hwnd
        # SWP_NOREDRAW | SWP_NOZORDER | SWP_NOACTIVATE. In particular, no
        # SWP_SHOWWINDOW: fallback capture must remain hidden during animation.
        if not self.user32.SetWindowPos(hwnd, None, x, y, width, height, 0x001C):
            raise ctypes.WinError(ctypes.get_last_error())
        if clip is None:
            self.round_corners(radius, size=(width, height), redraw=False)
        else:
            self.round_corners(radius, size=clip[:2], offset=(clip[2] - x, clip[3] - y), redraw=False)
        self.repaint_surface()

    def repaint_surface(self, *, exposed=False):
        """Paint newly exposed pixels before the compositor presents the region."""
        hwnd = self.hwnd
        self.root.update_idletasks()
        # INVALIDATE | NOERASE | ALLCHILDREN | UPDATENOW. Each Tk widget paints
        # its complete surface; don't expose an erased parent between them.
        # A growing clip exposes parent-frame background as well as controls.
        # Tk paints that background on WM_ERASEBKGND. With a fixed surface it
        # is safe to include it in this synchronous paint; skipping it leaves
        # stale transparent notches at the boundary between child windows.
        flags = 0x0185 if exposed else 0x01A1
        if not self.user32.RedrawWindow(hwnd, None, None, flags):
            raise ctypes.WinError(ctypes.get_last_error())

    def exclude_from_capture(self):
        """Windows 10 2004+ exclusion; unsupported systems use hidden capture."""
        if not self.prefer_native_capture:
            return False
        if not self.user32.SetWindowDisplayAffinity(self.hwnd, 0x11):
            return False
        affinity = wintypes.DWORD()
        return bool(self.user32.GetWindowDisplayAffinity(self.hwnd, ctypes.byref(affinity)) and affinity.value == 0x11)

    def restore_focus(self):
        if self.external_window and self.user32.IsWindow(self.external_window):
            self.user32.SetForegroundWindow(self.external_window)

    def passthrough(self, enabled):
        # Demo never controls the desktop and must remain clickable.
        enabled = enabled and not self.demo
        # Tk rewrites extended styles when alpha changes. Apply alpha FIRST,
        # then the native flags, and verify the final state, not an intermediate.
        self.root.attributes("-alpha", 1.0)
        self.root.update_idletasks()
        style = self.user32.GetWindowLongPtrW(self.hwnd, -20)
        mask = 0x00000020 | 0x08000000  # transparent input / no activate
        desired = (style | mask | 0x00080000) if enabled else style & ~mask
        ctypes.set_last_error(0)
        result = self.user32.SetWindowLongPtrW(self.hwnd, -20, desired)
        if not result and ctypes.get_last_error():
            raise ctypes.WinError(ctypes.get_last_error())
        if desired & 0x00080000:
            # We add WS_EX_LAYERED ourselves for cross-process click-through;
            # initialize its constant alpha explicitly even when Tk's alpha
            # was already 1.0 and did not create/configure a layered surface.
            if not self.user32.SetLayeredWindowAttributes(self.hwnd, 0, 255, 2):
                raise ctypes.WinError(ctypes.get_last_error())
        if (self.user32.GetWindowLongPtrW(self.hwnd, -20) & mask) != (mask if enabled else 0):
            raise RuntimeError("输入隔离设置未生效")
        # Preserve both window geometry and the user's pin preference.
        if not self.user32.SetWindowPos(self.hwnd, -1 if self.topmost else -2, 0, 0, 0, 0, 0x0033):
            raise ctypes.WinError(ctypes.get_last_error())
        if (self.user32.GetWindowLongPtrW(self.hwnd, -20) & mask) != (mask if enabled else 0):
            raise RuntimeError("输入隔离在窗口更新后失效")

    def apply(self, operation):
        if operation == "working":
            self.working = True
            self.passthrough(True)
            if not self.demo:
                self.restore_focus()
        elif operation == "interactive":
            self.remember_external()
            self.capture_end()
            self.working = False
            self.passthrough(False)
        elif operation == "capture_begin":
            self.capture_begin()
        elif operation == "capture_end":
            self.capture_end()
        else:
            raise ValueError(operation)

    def capture_begin(self, *, force_hide=False):
        if self.capture_active:
            raise RuntimeError("不允许重叠的截图请求")
        self.capture_active = True
        if not force_hide and self.exclude_from_capture():
            if ctypes.windll.dwmapi.DwmFlush() != 0:
                raise RuntimeError("无法确认桌面合成已完成")
            return
        self.was_visible = bool(self.user32.IsWindowVisible(self.hwnd))
        # ShowWindow(SW_HIDE) clears visibility immediately, but DWM can still
        # render a fading ghost for several frames. Disable transitions for
        # THIS window before acknowledging isolation; never change OS settings.
        self.suppress_system_transitions()
        self.user32.ShowWindow(self.hwnd, 0)
        self.capture_hidden = True
        # An acknowledged invisible window plus a compositor flush, rather than
        # a guessed delay. This covers pixels AND UIA window enumeration.
        if self.user32.IsWindowVisible(self.hwnd):
            raise RuntimeError("前端窗口未能隐藏")
        result = ctypes.windll.dwmapi.DwmFlush()
        if result != 0:
            raise RuntimeError("无法确认桌面合成已完成")

    def capture_end(self):
        if self.capture_hidden:
            if self.was_visible:
                self.user32.ShowWindow(self.hwnd, 4)  # SW_SHOWNOACTIVATE
            self.capture_hidden = False
        self.capture_active = False

    def register_hotkeys(self):
        # A dedicated thread owns the hotkeys and the Raw Input message window;
        # Tk must not consume their messages from its own thread queue.
        import queue
        import threading
        self._key_queue = queue.Queue()
        self._key_stop = threading.Event()
        ready = threading.Event()

        def pump():
            try:
                self.pointer_monitor_ready = self._pointer_monitor.start()
                for key_id, virtual_key in ((1, 0x20), (2, 0x1B), (3, 0x0D)):
                    self.hotkeys[key_id] = bool(self.user32.RegisterHotKey(None, key_id, 0x4003, virtual_key))
                ready.set()
                message = wintypes.MSG()
                while not self._key_stop.is_set():
                    # Wake when input arrives, with a bounded shutdown wait.
                    # Slow Python processing delays only OUR observation; it
                    # cannot hold up system pointer movement or mouse clicks.
                    result = self.user32.MsgWaitForMultipleObjectsEx(0, None, 50, 0x04FF, 0x0004)
                    if result == 0xFFFFFFFF:
                        break
                    # Bound each batch so high-polling-rate mice cannot prevent
                    # shutdown. Device events never invoke a Python global hook.
                    for _ in range(256):
                        if self._key_stop.is_set() or not self.user32.PeekMessageW(ctypes.byref(message), None, 0, 0, 1):
                            break
                        if message.message == 0x0312:
                            self._key_queue.put(int(message.wParam))
                        elif message.message == WM_INPUT:
                            try:
                                if self._pointer_monitor.physical_motion(message.lParam):
                                    self.last_human_pointer = time.monotonic()
                            finally:
                                # Required cleanup for foreground WM_INPUT.
                                self.user32.DefWindowProcW(message.hWnd, message.message, message.wParam, message.lParam)
                        else:
                            self.user32.TranslateMessage(ctypes.byref(message))
                            self.user32.DispatchMessageW(ctypes.byref(message))
            finally:
                self._pointer_monitor.close()
                self.pointer_monitor_ready = False
                for key_id, registered in self.hotkeys.items():
                    if registered:
                        self.user32.UnregisterHotKey(None, key_id)
                ready.set()

        self._key_thread = threading.Thread(target=pump, daemon=True, name="openassist-hotkeys")
        self._key_thread.start()
        ready.wait(1)
        return all(self.hotkeys.get(key) for key in (1, 2))

    def poll_hotkeys(self):
        import queue
        keys = []
        while True:
            try:
                keys.append(self._key_queue.get_nowait())
            except queue.Empty:
                return keys

    def close(self):
        self.capture_end()
        self._key_stop.set()
        self._key_thread.join(timeout=0.3)
