"""Verify window-scoped UIA and screenshot behavior against an owned fixture."""

from __future__ import annotations

import argparse
import base64
import ctypes
import io
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURE_VALUE = "Mirally 桌面测试"
FIXTURE_PASSWORD = "DESKTOP_FIXTURE_PASSWORD"
BUTTON_NAME = "Accept fixture"
BUTTON_ID = 101
WS_CHILD_VISIBLE = 0x50000000
WS_TABSTOP = 0x00010000
EDIT_STYLE = 0x00800000 | WS_TABSTOP | 0x80  # WS_BORDER | WS_TABSTOP | ES_AUTOHSCROLL
ES_PASSWORD = 0x20
DWMWA_CLOAK = 13
DWMWA_CLOAKED = 14

LRESULT = ctypes.c_ssize_t
WINDOW_PROC = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)(
    LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
)


class WindowClass(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("procedure", WINDOW_PROC),
        ("class_extra", ctypes.c_int),
        ("window_extra", ctypes.c_int),
        ("instance", wintypes.HINSTANCE),
        ("icon", wintypes.HICON),
        ("cursor", wintypes.HANDLE),
        ("background", wintypes.HBRUSH),
        ("menu_name", wintypes.LPCWSTR),
        ("class_name", wintypes.LPCWSTR),
    ]


class DesktopFixture:
    """A native Edit and Button with the standard Windows accessibility providers."""

    def __init__(self):
        self.user = user = ctypes.WinDLL("user32", use_last_error=True)
        self.dwm = dwm = ctypes.WinDLL("dwmapi", use_last_error=True)
        dwm.DwmSetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
        dwm.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        kernel.GetModuleHandleW.restype = wintypes.HMODULE
        self.instance = kernel.GetModuleHandleW(None)
        self.class_name = f"MirallyTestWindow{os.getpid()}"
        self.title = f"UIA acceptance fixture {os.getpid()}"
        self.hwnd = None
        self.invoke_count = 0
        self._procedure = WINDOW_PROC(self._dispatch)
        user.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        user.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # Per-monitor v2
        user.RegisterClassW.argtypes = [ctypes.POINTER(WindowClass)]
        user.RegisterClassW.restype = wintypes.WORD
        user.UnregisterClassW.argtypes = [wintypes.LPCWSTR, wintypes.HINSTANCE]
        user.CreateWindowExW.argtypes = [
            wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, ctypes.c_void_p,
        ]
        user.CreateWindowExW.restype = wintypes.HWND
        user.SetWindowPos.argtypes = [
            wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
            ctypes.c_int, ctypes.c_int, wintypes.UINT,
        ]
        user.DestroyWindow.argtypes = [wintypes.HWND]
        user.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        user.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        user.SetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPCWSTR]
        user.GetForegroundWindow.restype = wintypes.HWND
        user.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        user.DefWindowProcW.restype = LRESULT
        user.PeekMessageW.argtypes = [
            ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT,
        ]
        user.MsgWaitForMultipleObjectsEx.argtypes = [
            wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE), wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
        ]
        user.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
        user.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
        user.DispatchMessageW.restype = LRESULT

    def _dispatch(self, hwnd, message, wparam, lparam):
        if message == 0x0111 and wparam == BUTTON_ID and lparam == self.button:  # WM_COMMAND / BN_CLICKED
            self.invoke_count += 1
            self.user.SetWindowTextW(self.button, f"Accepted fixture ({self.invoke_count})")
            return 0
        return self.user.DefWindowProcW(hwnd, message, wparam, lparam)

    def _child(self, kind, text, x, y, width, height, *, style=0, control_id=None):
        hwnd = self.user.CreateWindowExW(
            0, kind, text, WS_CHILD_VISIBLE | style, x, y, width, height,
            self.hwnd, control_id, self.instance, None,
        )
        if not hwnd:
            raise ctypes.WinError(ctypes.get_last_error())
        return hwnd

    def __enter__(self):
        self.button = None
        registration = WindowClass(
            procedure=self._procedure, instance=self.instance, background=6, class_name=self.class_name,
        )
        if not self.user.RegisterClassW(ctypes.byref(registration)):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            # WS_EX_NOACTIVATE prevents focus theft even when the fixture first shows.
            self.hwnd = self.user.CreateWindowExW(
                0x08000000, self.class_name, self.title, 0x00CF0000, 90, 90, 540, 280,
                None, None, self.instance, None,
            )
            if not self.hwnd:
                raise ctypes.WinError(ctypes.get_last_error())
            self._child("STATIC", "Task text", 30, 25, 170, 20)
            self.edit = self._child("EDIT", "original", 30, 49, 370, 28, style=EDIT_STYLE)
            self._child("STATIC", "Fixture password", 30, 92, 170, 20)
            self.password = self._child("EDIT", FIXTURE_PASSWORD, 30, 116, 370, 28, style=EDIT_STYLE | ES_PASSWORD)
            self.button = self._child(
                "BUTTON", BUTTON_NAME, 30, 167, 155, 31, style=WS_TABSTOP, control_id=BUTTON_ID,
            )
            # SWP_SHOWWINDOW | SWP_NOACTIVATE | SWP_NOMOVE | SWP_NOSIZE.
            if not self.user.SetWindowPos(self.hwnd, None, 0, 0, 0, 0, 0x53):
                raise ctypes.WinError(ctypes.get_last_error())
            self.pump()
            return self
        except Exception:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *_):
        if self.hwnd:
            self.user.DestroyWindow(self.hwnd)
        self.user.UnregisterClassW(self.class_name, self.instance)

    def pump(self, *, wait=False):
        message = wintypes.MSG()
        while self.user.PeekMessageW(ctypes.byref(message), None, 0, 0, 1):
            self.user.TranslateMessage(ctypes.byref(message))
            self.user.DispatchMessageW(ctypes.byref(message))
        if wait:
            # Wake for synchronous UIA messages without adding one sleep interval per property.
            self.user.MsgWaitForMultipleObjectsEx(0, None, 20, 0x04FF, 0x04)

    def text(self, hwnd):
        buffer = ctypes.create_unicode_buffer(512)
        self.user.GetWindowTextW(hwnd, buffer, len(buffer))
        return buffer.value

    def bounds(self, hwnd):
        rect = wintypes.RECT()
        if not self.user.GetWindowRect(hwnd, ctypes.byref(rect)):
            raise ctypes.WinError(ctypes.get_last_error())
        return {name: getattr(rect, name) for name in ("left", "top", "right", "bottom")}

    def set_cloak(self, cloaked):
        """Ask DWM to hide only this tool's own fixture window from the user."""
        value = wintypes.DWORD(1 if cloaked else 0)
        return self.dwm.DwmSetWindowAttribute(self.hwnd, DWMWA_CLOAK, ctypes.byref(value), ctypes.sizeof(value))

    def cloak_state(self):
        value = wintypes.DWORD()
        result = self.dwm.DwmGetWindowAttribute(self.hwnd, DWMWA_CLOAKED, ctypes.byref(value), ctypes.sizeof(value))
        if result != 0:
            raise RuntimeError(f"DWMWA_CLOAKED query failed with 0x{result & 0xFFFFFFFF:08X}")
        return value.value

    def wait_for_cloak(self, cloaked, *, timeout=3.0):
        # DWM applies cloak changes on the composition thread, so poll while keeping the fixture responsive.
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.pump(wait=True)
            if bool(self.cloak_state()) == cloaked:
                return True
        return bool(self.cloak_state()) == cloaked


def call_helper(fixture, kind, value=None, *, rejected=False):
    # Keep the fixture's GUI loop responsive while UIA operates cross-process.
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            subprocess.run,
            [sys.executable, "-m", "mirally.framework.desktop_observation", kind],
            cwd=ROOT,
            input=json.dumps(value or {}),
            capture_output=True,
            encoding="utf-8",
            env={**os.environ, "PYTHONUTF8": "1"},
            creationflags=subprocess.CREATE_NO_WINDOW,
            timeout=20,
        )
        while not future.done():
            fixture.pump(wait=True)
        result = future.result()
    data = json.loads(result.stdout)
    if rejected:
        assert result.returncode and data.get("status") == "rejected" and data.get("error"), data
    elif result.returncode:
        raise RuntimeError(data.get("error", "desktop helper failed"))
    return data


def verify_image(fixture, snapshot, button_id, *, required):
    from PIL import Image

    frame = snapshot["image"]
    if frame["status"] != "ok":
        assert not required, frame
        assert not frame.get("data"), "Capture failure must not return a wider image"
        print(f"Annotated window image unavailable ({frame['status']}).")
        return

    assert button_id in frame["annotated_targets"], "The observed button should be labeled in the image"
    with Image.open(io.BytesIO(base64.b64decode(frame["data"], validate=True))) as image:
        assert image.format == "JPEG"
        assert image.size == (frame["width"], frame["height"])
        bounds = frame["frame"]
        outer_bounds = fixture.bounds(fixture.hwnd)
        assert outer_bounds["left"] <= bounds["left"] < bounds["right"] <= outer_bounds["right"]
        assert outer_bounds["top"] <= bounds["top"] < bounds["bottom"] <= outer_bounds["bottom"]
        scale_x = image.width / (bounds["right"] - bounds["left"])
        scale_y = image.height / (bounds["bottom"] - bounds["top"])
        button_bounds = fixture.bounds(fixture.button)
        x = round((button_bounds["left"] - bounds["left"]) * scale_x)
        y = round(((button_bounds["top"] + button_bounds["bottom"]) / 2 - bounds["top"]) * scale_y)
        assert 5 <= x < image.width - 5 and 4 <= y < image.height - 4
        area = image.crop((x - 5, y - 4, x + 6, y + 5)).convert("RGB")
        pixels = (area.getpixel((x, y)) for y in range(area.height) for x in range(area.width))
        assert any(
            blue > green + 55 and blue > red + 90 and green > red + 20
            for red, green, blue in pixels
        ), "Window-local button outline does not align with its actual Win32 bounds"
        password_bounds = fixture.bounds(fixture.password)
        x = round(((password_bounds["left"] + password_bounds["right"]) / 2 - bounds["left"]) * scale_x)
        y = round(((password_bounds["top"] + password_bounds["bottom"]) / 2 - bounds["top"]) * scale_y)
        masked = image.crop((x - 3, y - 3, x + 4, y + 4)).convert("RGB")
        assert all(high < 30 for _, high in masked.getextrema()), "Password control is visible in the snapshot"
    print(f"Annotated window image aligned ({frame['source']}).")


def verify_cloak(fixture, window):
    """Only a window DWM confirms as shown is listed and observable."""

    from mirally.framework.desktop_observation import CLOAK_SHOWN, CLOAK_UNKNOWN, cloak_state, dwm_api

    dwm = dwm_api()

    def entries():
        return [record for record in call_helper(fixture, "windows")["windows"] if record["hwnd"] == fixture.hwnd]

    assert cloak_state(dwm, fixture.hwnd) == CLOAK_SHOWN, "The shown fixture window must classify as shown"
    assert entries(), "The shown fixture window must be listed before cloaking"
    refusal = fixture.set_cloak(True)
    if not fixture.wait_for_cloak(True):
        reason = (f"DwmSetWindowAttribute refused with 0x{refusal & 0xFFFFFFFF:08X}" if refusal
                  else "DWM kept the window uncloaked")
        print(f"Cloak round-trip unavailable ({reason}); the read-only DWMWA_CLOAKED check is the only cloak evidence.")
        return
    try:
        assert not entries(), "A cloaked window must not be offered as an operable target"
        refused = call_helper(fixture, "snapshot", {"window": window}, rejected=True)
        assert refused["error"] == "目标窗口当前未显示。请重新观察窗口。", refused
    finally:
        fixture.set_cloak(False)
        restored = fixture.wait_for_cloak(False)
    assert restored, "The fixture window stayed cloaked after the uncloak request"
    assert entries(), "The shown fixture window must return to the window list"
    # DWM refuses to answer for a handle that is not a window, which must never read as shown.
    assert cloak_state(dwm, 0) == CLOAK_UNKNOWN, "A failed cloak query must not report the window as shown"
    print("Cloaked window left the listing and was refused for observation; showing it again restored both,")
    print("and a refused cloak query is reported as unknown instead of shown.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--require-image", action="store_true", help="Fail if a window screenshot is unavailable")
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("An unlocked Windows desktop is required")

    sys.path.insert(0, str(ROOT))
    from mirally.platform.window_identity import mark_mirally_window, unmark_mirally_window

    with DesktopFixture() as fixture:
        windows = call_helper(fixture, "windows")["windows"]
        matches = [window for window in windows if window["title"] == fixture.title]
        assert len(matches) == 1 and matches[0]["hwnd"] == fixture.hwnd and matches[0]["pid"] == os.getpid()
        window = {"hwnd": fixture.hwnd, "pid": os.getpid()}
        snapshot = call_helper(fixture, "snapshot", {"window": window, "include_image": True})
        assert snapshot["window"]["hwnd"] == fixture.hwnd
        assert snapshot["untrusted_content"] is True
        assert not snapshot["truncated"]
        targets = snapshot["targets"]
        assert sorted(target["id"] for target in targets) == list(range(1, len(targets) + 1))
        assert FIXTURE_PASSWORD not in json.dumps(targets)
        assert all(target["bounds"] != fixture.bounds(fixture.password) for target in targets)
        entry, = [target for target in targets if target["name"] == "Task text" and "set_value" in target["actions"]]
        button, = [target for target in targets if target["name"] == BUTTON_NAME and "invoke" in target["actions"]]
        verify_image(fixture, snapshot, button["id"], required=args.require_image)

        action = {"window": snapshot["window"], "target": entry, "operation": "set_value", "text": FIXTURE_VALUE}
        filled = call_helper(fixture, "action", action)
        assert filled["status"] == "verified", filled
        assert fixture.text(fixture.edit) == FIXTURE_VALUE
        assert FIXTURE_VALUE not in json.dumps(filled.get("after", {}), ensure_ascii=False)

        changed = {**action, "window": {**action["window"], "pid": os.getpid() + 1}, "text": "blocked"}
        call_helper(fixture, "action", changed, rejected=True)
        assert fixture.text(fixture.edit) == FIXTURE_VALUE

        trigger = {"window": snapshot["window"], "target": button, "operation": "invoke"}
        receipt = call_helper(fixture, "action", trigger)
        assert receipt["status"] in {"sent", "verified"}, receipt
        assert receipt["after"]["window"]["hwnd"] == fixture.hwnd
        after = call_helper(fixture, "snapshot", {"window": window})
        renamed_button, = [target for target in after["targets"] if target["name"] == "Accepted fixture (1)"]
        assert fixture.invoke_count == 1
        call_helper(fixture, "action", trigger, rejected=True)
        assert fixture.invoke_count == 1

        mark_mirally_window(fixture.hwnd)
        try:
            listed = call_helper(fixture, "windows")["windows"]
            assert all(record["hwnd"] != fixture.hwnd for record in listed)
            call_helper(fixture, "snapshot", {"window": window}, rejected=True)
            marked_action = {"window": after["window"], "target": renamed_button, "operation": "invoke"}
            call_helper(fixture, "action", marked_action, rejected=True)
            assert fixture.invoke_count == 1
        finally:
            unmark_mirally_window(fixture.hwnd)

        verify_cloak(fixture, window)

        assert fixture.user.GetForegroundWindow() != fixture.hwnd
        print("Window scope, Unicode value readback, Invoke and stale control rejection passed.")
        print("Mirally native window boundary verified for listing, observation and action.")


if __name__ == "__main__":
    main()
