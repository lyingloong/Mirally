"""Windows Raw Input helper used by :mod:`input_pump`.

Emits aggregate counts and clipboard signals, never raw keys or device data.
"""

from __future__ import annotations

import ctypes
import json
import sys
import time
import argparse
from ctypes import wintypes

from .input_metadata import InputMetadataAggregator


WM_INPUT = 0x00FF
WM_QUIT = 0x0012
WM_CLIPBOARDUPDATE = 0x031D
RID_INPUT = 0x10000003
RIDEV_INPUTSINK = 0x00000100
RIDEV_REMOVE = 0x00000001
RIM_TYPEMOUSE = 0
RIM_TYPEKEYBOARD = 1
RI_KEY_BREAK = 0x0001
RI_MOUSE_BUTTON_1_DOWN = 0x0001
RI_MOUSE_BUTTON_2_DOWN = 0x0004
RI_MOUSE_BUTTON_3_DOWN = 0x0010
RI_MOUSE_BUTTON_4_DOWN = 0x0040
RI_MOUSE_BUTTON_5_DOWN = 0x0100
RI_MOUSE_WHEEL = 0x0400
RI_MOUSE_HWHEEL = 0x0800


class RawInputDevice(ctypes.Structure):
    _fields_ = [
        ("usage_page", wintypes.USHORT),
        ("usage", wintypes.USHORT),
        ("flags", wintypes.DWORD),
        ("target", wintypes.HWND),
    ]


class RawInputHeader(ctypes.Structure):
    _fields_ = [
        ("kind", wintypes.DWORD),
        ("size", wintypes.DWORD),
        ("device", wintypes.HANDLE),
        ("parameter", wintypes.WPARAM),
    ]


class RawMouse(ctypes.Structure):
    _fields_ = [
        ("flags", wintypes.USHORT),
        ("padding", wintypes.USHORT),
        ("button_flags", wintypes.USHORT),
        ("button_data", wintypes.USHORT),
        ("raw_buttons", wintypes.DWORD),
        ("last_x", wintypes.LONG),
        ("last_y", wintypes.LONG),
        ("extra", wintypes.DWORD),
    ]


class RawKeyboard(ctypes.Structure):
    _fields_ = [
        ("make_code", wintypes.USHORT),
        ("flags", wintypes.USHORT),
        ("reserved", wintypes.USHORT),
        ("vkey", wintypes.USHORT),
        ("message", wintypes.UINT),
        ("extra", wintypes.ULONG),
    ]


class RawInput(ctypes.Structure):
    class Data(ctypes.Union):
        _fields_ = [("mouse", RawMouse), ("keyboard", RawKeyboard)]

    _fields_ = [("header", RawInputHeader), ("data", Data)]


def _decode_packet(user32: ctypes.WinDLL, handle: int, aggregator: InputMetadataAggregator,
                   clipboard: bool = False) -> None:
    size = wintypes.UINT(0)
    header_size = ctypes.sizeof(RawInputHeader)
    if user32.GetRawInputData(handle, RID_INPUT, None, ctypes.byref(size), header_size) == 0xFFFFFFFF:
        return
    if size.value <= 0 or size.value > 64 * 1024:
        return
    buffer = ctypes.create_string_buffer(size.value)
    copied = user32.GetRawInputData(handle, RID_INPUT, buffer, ctypes.byref(size), header_size)
    if copied == 0xFFFFFFFF or copied < header_size:
        return
    packet = ctypes.cast(buffer, ctypes.POINTER(RawInput)).contents
    needed = header_size + (ctypes.sizeof(RawKeyboard) if packet.header.kind == RIM_TYPEKEYBOARD else ctypes.sizeof(RawMouse))
    if copied < needed:
        return
    if packet.header.kind == RIM_TYPEKEYBOARD:
        if not (packet.data.keyboard.flags & RI_KEY_BREAK):
            aggregator.keyboard()
            if clipboard and user32.GetAsyncKeyState(0x11) & 0x8000:
                operation = {0x43: "copy", 0x58: "cut", 0x56: "paste"}.get(packet.data.keyboard.vkey)
                if operation:
                    _signal(user32, "clipboard_operation", operation=operation)
    elif packet.header.kind == RIM_TYPEMOUSE:
        flags = packet.data.mouse.button_flags
        if flags & (
            RI_MOUSE_BUTTON_1_DOWN
            | RI_MOUSE_BUTTON_2_DOWN
            | RI_MOUSE_BUTTON_3_DOWN
            | RI_MOUSE_BUTTON_4_DOWN
            | RI_MOUSE_BUTTON_5_DOWN
        ):
            aggregator.click()
        if flags & (RI_MOUSE_WHEEL | RI_MOUSE_HWHEEL):
            aggregator.scroll()
        if packet.data.mouse.last_x or packet.data.mouse.last_y:
            aggregator.motion()


def _foreground(user32) -> tuple[int, int]:
    hwnd = int(user32.GetForegroundWindow() or 0)
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(wintypes.HWND(hwnd), ctypes.byref(pid))
    return hwnd, int(pid.value)


def _signal(user32, kind: str, **fields) -> None:
    hwnd, pid = _foreground(user32)
    print(json.dumps({"type": kind, "hwnd": hwnd, "pid": pid,
                      "sequence": int(user32.GetClipboardSequenceNumber()),
                      "observed_at_utc_ms": int(time.time() * 1000), **fields}, separators=(",", ":")), flush=True)


def run(*, quiet_ms: int = 1500, max_burst_ms: int = 10_000, clipboard: bool = False) -> int:
    if sys.platform != "win32":
        return 2
    user32 = ctypes.windll.user32
    user32.GetRawInputData.argtypes = [
        wintypes.HANDLE,
        wintypes.UINT,
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.UINT),
        wintypes.UINT,
    ]
    user32.GetRawInputData.restype = wintypes.UINT
    user32.RegisterRawInputDevices.argtypes = [
        ctypes.POINTER(RawInputDevice), wintypes.UINT, wintypes.UINT
    ]
    user32.RegisterRawInputDevices.restype = wintypes.BOOL
    user32.PeekMessageW.argtypes = [
        ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT
    ]
    user32.PeekMessageW.restype = wintypes.BOOL
    user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
        wintypes.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, ctypes.c_void_p]
    user32.CreateWindowExW.restype = wintypes.HWND
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.DestroyWindow.argtypes = [wintypes.HWND]
    user32.AddClipboardFormatListener.argtypes = [wintypes.HWND]
    user32.RemoveClipboardFormatListener.argtypes = [wintypes.HWND]
    user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
    user32.DispatchMessageW.restype = wintypes.LPARAM

    # A message-only STATIC window is sufficient: the helper consumes WM_INPUT
    # from PeekMessage and never becomes visible or focusable.
    hwnd = user32.CreateWindowExW(
        0,
        "STATIC",
        "Mirally activity input",
        0,
        0,
        0,
        0,
        0,
        wintypes.HWND(-3),
        None,
        None,
        None,
    )
    if not hwnd:
        return 3
    devices = (RawInputDevice * 2)(
        RawInputDevice(1, 2, RIDEV_INPUTSINK, hwnd),
        RawInputDevice(1, 6, RIDEV_INPUTSINK, hwnd),
    )
    if not user32.RegisterRawInputDevices(devices, 2, ctypes.sizeof(RawInputDevice)):
        user32.DestroyWindow(hwnd)
        return 4
    aggregator = InputMetadataAggregator(max_burst_ms=max_burst_ms)
    foreground = _foreground(user32)
    if clipboard and not user32.AddClipboardFormatListener(hwnd):
        print(json.dumps({"type": "source_diagnostic", "reason": "clipboard_listener_failed"}), flush=True)

    def flush(quiet: int) -> None:
        batch = aggregator.flush(quiet_ms=quiet)
        if batch is not None:
            print(json.dumps({**batch.__dict__, "type": "input_batch", "hwnd": foreground[0],
                              "pid": foreground[1], "observed_at_utc_ms": int(time.time() * 1000)},
                             separators=(",", ":")), flush=True)
    try:
        message = wintypes.MSG()
        while True:
            while user32.PeekMessageW(ctypes.byref(message), hwnd, 0, 0, 1):
                if message.message == WM_QUIT:
                    return 0
                if message.message == WM_INPUT:
                    current = _foreground(user32)
                    if current != foreground:
                        flush(0)
                        foreground = current
                    _decode_packet(user32, message.lParam, aggregator, clipboard)
                elif message.message == WM_CLIPBOARDUPDATE and clipboard:
                    _signal(user32, "clipboard_changed")
                user32.DispatchMessageW(ctypes.byref(message))
            now = time.monotonic_ns()
            if aggregator.should_flush(now_monotonic_ns=now, quiet_ms=quiet_ms):
                flush(quiet_ms)
            time.sleep(0.05)
    finally:
        if aggregator.should_flush(quiet_ms=0):
            batch = aggregator.flush(quiet_ms=0)
            if batch is not None:
                print(json.dumps(batch.__dict__, separators=(",", ":")), flush=True)
        remove = (RawInputDevice * 2)(
            RawInputDevice(1, 2, RIDEV_REMOVE, None),
            RawInputDevice(1, 6, RIDEV_REMOVE, None),
        )
        user32.RegisterRawInputDevices(remove, 2, ctypes.sizeof(RawInputDevice))
        if clipboard:
            user32.RemoveClipboardFormatListener(hwnd)
        user32.DestroyWindow(hwnd)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--quiet-ms", type=int, default=1500)
    parser.add_argument("--max-burst-ms", type=int, default=10_000)
    parser.add_argument("--clipboard", action="store_true")
    args = parser.parse_args()
    raise SystemExit(run(quiet_ms=max(100, min(60_000, args.quiet_ms)), max_burst_ms=max(1_000, min(300_000, args.max_burst_ms)), clipboard=args.clipboard))
