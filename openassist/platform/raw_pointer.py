"""Observe device input asynchronously, without intercepting the system mouse."""

from __future__ import annotations

import ctypes
from ctypes import wintypes


WM_INPUT = 0x00FF
RID_INPUT = 0x10000003
RIDEV_INPUTSINK = 0x00000100
RIDEV_REMOVE = 0x00000001


class RawInputDevice(ctypes.Structure):
    _fields_ = [("usage_page", wintypes.USHORT), ("usage", wintypes.USHORT),
                ("flags", wintypes.DWORD), ("target", wintypes.HWND)]


class RawInputHeader(ctypes.Structure):
    _fields_ = [("kind", wintypes.DWORD), ("size", wintypes.DWORD),
                ("device", wintypes.HANDLE), ("parameter", wintypes.WPARAM)]


class RawMouse(ctypes.Structure):
    # The native button union has DWORD size AND alignment.
    _fields_ = [("flags", wintypes.USHORT), ("buttons", wintypes.DWORD),
                ("raw_buttons", wintypes.DWORD), ("x", wintypes.LONG),
                ("y", wintypes.LONG), ("extra", wintypes.DWORD)]


class RawMouseInput(ctypes.Structure):
    _fields_ = [("header", RawInputHeader), ("mouse", RawMouse)]


class RawPointerMonitor:
    """A message-only window receives copies of mouse device events.

    Unlike WH_MOUSE_LL, Windows never waits for this Python observer before
    delivering mouse input to other applications. Use on its owning pump thread.
    SendInput / SetCursorPos do not produce hardware Raw Input. Events without a
    device identity are also ignored; the expand shortcut remains available.
    """

    def __init__(self, user32):
        self.user32 = user32
        self.hwnd = None
        self.registered = False
        self._packet = RawMouseInput()
        for name, args, result in (
            ("CreateWindowExW", [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                 wintypes.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                 ctypes.c_int, wintypes.HWND, wintypes.HMENU,
                                 wintypes.HINSTANCE, ctypes.c_void_p], wintypes.HWND),
            ("DestroyWindow", [wintypes.HWND], wintypes.BOOL),
            ("RegisterRawInputDevices", [ctypes.POINTER(RawInputDevice), wintypes.UINT,
                                         wintypes.UINT], wintypes.BOOL),
            ("GetRawInputData", [wintypes.HANDLE, wintypes.UINT, ctypes.c_void_p,
                                 ctypes.POINTER(wintypes.UINT), wintypes.UINT], wintypes.UINT),
            ("DefWindowProcW", [wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
                                 wintypes.LPARAM], ctypes.c_ssize_t),
        ):
            function = getattr(user32, name)
            function.argtypes, function.restype = args, result

    def start(self):
        # STATIC is a system class: no Python window-procedure callback needed.
        self.hwnd = self.user32.CreateWindowExW(
            0, "STATIC", "OpenAssist input observer", 0, 0, 0, 0, 0,
            -3, None, None, None,  # HWND_MESSAGE: invisible, never takes focus.
        )
        if not self.hwnd:
            return False
        device = RawInputDevice(1, 2, RIDEV_INPUTSINK, self.hwnd)
        # No NOLEGACY / CAPTUREMOUSE flags: never suppress ordinary mouse input.
        self.registered = bool(self.user32.RegisterRawInputDevices(
            ctypes.byref(device), 1, ctypes.sizeof(device)))
        if not self.registered:
            self.close()
        return self.registered

    def physical_motion(self, handle):
        size = wintypes.UINT(ctypes.sizeof(self._packet))
        copied = self.user32.GetRawInputData(
            handle, RID_INPUT, ctypes.byref(self._packet), ctypes.byref(size),
            ctypes.sizeof(RawInputHeader))
        if copied != ctypes.sizeof(self._packet):
            return False
        header, mouse = self._packet.header, self._packet.mouse
        if header.kind != 0 or not header.device or header.size != copied:
            return False
        return bool(mouse.x or mouse.y or mouse.flags & 0x0001)  # absolute motion

    def close(self):
        if self.registered:
            device = RawInputDevice(1, 2, RIDEV_REMOVE, None)
            self.user32.RegisterRawInputDevices(ctypes.byref(device), 1, ctypes.sizeof(device))
            self.registered = False
        if self.hwnd:
            self.user32.DestroyWindow(self.hwnd)
            self.hwnd = None
