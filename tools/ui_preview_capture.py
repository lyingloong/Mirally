"""Capture our own Tk client area for visual previews without reading the screen.

PrintWindow asks the application to render into a memory bitmap. This can work
while a monitor is off, but success is not guaranteed on every Windows session.
It does not test screenshot exclusion, occlusion, or the desktop agent's capture
path. No external application, desktop pixels, power state or display settings
are accessed or changed.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import sys
import tkinter as tk

from PIL import Image


class CaptureUnavailable(RuntimeError):
    """The window could not produce a useful preview in this desktop session."""


class _BitmapInfoHeader(ctypes.Structure):
    _fields_ = [
        ("size", wintypes.DWORD), ("width", wintypes.LONG), ("height", wintypes.LONG),
        ("planes", wintypes.WORD), ("bit_count", wintypes.WORD), ("compression", wintypes.DWORD),
        ("image_size", wintypes.DWORD), ("x_pixels_per_meter", wintypes.LONG),
        ("y_pixels_per_meter", wintypes.LONG), ("colors_used", wintypes.DWORD),
        ("colors_important", wintypes.DWORD),
    ]


class _BitmapInfo(ctypes.Structure):
    _fields_ = [("header", _BitmapInfoHeader), ("colors", wintypes.DWORD * 3)]


def _has_rendered_content(image: Image.Image) -> bool:
    """Reject black/near-black frames and empty single-color buffers."""
    rgb = image.convert("RGB")
    extrema = rgb.getextrema()
    if max(high - low for low, high in extrema) <= 3:
        return False
    histogram = rgb.convert("L").histogram()
    black_pixels = sum(histogram[:9])
    return black_pixels / (rgb.width * rgb.height) < 0.995


def _apis():
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    declarations = (
        (user32.GetAncestor, [wintypes.HWND, wintypes.UINT], wintypes.HWND),
        (user32.GetWindowThreadProcessId, [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)], wintypes.DWORD),
        (user32.GetClientRect, [wintypes.HWND, ctypes.POINTER(wintypes.RECT)], wintypes.BOOL),
        (user32.GetDC, [wintypes.HWND], wintypes.HDC),
        (user32.ReleaseDC, [wintypes.HWND, wintypes.HDC], ctypes.c_int),
        (user32.PrintWindow, [wintypes.HWND, wintypes.HDC, wintypes.UINT], wintypes.BOOL),
        (gdi32.CreateCompatibleDC, [wintypes.HDC], wintypes.HDC),
        (gdi32.CreateDIBSection, [wintypes.HDC, ctypes.POINTER(_BitmapInfo), wintypes.UINT,
                                ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD], wintypes.HBITMAP),
        (gdi32.SelectObject, [wintypes.HDC, wintypes.HANDLE], wintypes.HANDLE),
        (gdi32.DeleteObject, [wintypes.HANDLE], wintypes.BOOL),
        (gdi32.DeleteDC, [wintypes.HDC], wintypes.BOOL),
    )
    for function, arguments, result in declarations:
        function.argtypes = arguments
        function.restype = result
    return user32, gdi32


def capture_window(root: tk.Misc, path: str | Path) -> Path:
    """Save our Tk window's client-area PNG or raise ``CaptureUnavailable``.

    Invoke on the Tk thread after the preview's regular update/pump step. The
    ``root`` must be a live Tk widget owned by this process; arbitrary HWNDs are
    deliberately not accepted. An unsuccessful capture never writes the target.
    """
    if sys.platform != "win32":
        raise CaptureUnavailable("PrintWindow previews are only available on Windows.")
    if not isinstance(root, tk.Misc):
        raise TypeError("capture_window only accepts this process's Tk widgets.")
    try:
        window = root.winfo_toplevel()
        window.update_idletasks()
        native = window.winfo_id()
    except tk.TclError as error:
        raise CaptureUnavailable("The preview window is no longer available.") from error
    user32, gdi32 = _apis()
    hwnd = user32.GetAncestor(native, 2) or native  # GA_ROOT: Tk's native wrapper.
    process = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process))
    if process.value != os.getpid():
        raise CaptureUnavailable("Refusing to capture a window outside the current process.")
    rect = wintypes.RECT()
    if not user32.GetClientRect(hwnd, ctypes.byref(rect)):
        raise CaptureUnavailable("Unable to read the preview window's client area.")
    width, height = rect.right - rect.left, rect.bottom - rect.top
    if width < 2 or height < 2:
        raise CaptureUnavailable("The preview window has no drawable client area.")
    source_dc = user32.GetDC(hwnd)
    memory_dc = bitmap = previous = None
    image = None
    try:
        if not source_dc:
            raise CaptureUnavailable("Unable to obtain the preview window's device context.")
        memory_dc = gdi32.CreateCompatibleDC(source_dc)
        if not memory_dc:
            raise CaptureUnavailable("Unable to create a preview memory context.")
        info = _BitmapInfo()
        info.header.size = ctypes.sizeof(_BitmapInfoHeader)
        info.header.width, info.header.height = width, -height  # top-down DIB
        info.header.planes, info.header.bit_count = 1, 32
        bits = ctypes.c_void_p()
        bitmap = gdi32.CreateDIBSection(memory_dc, ctypes.byref(info), 0, ctypes.byref(bits), None, 0)
        if not bitmap or not bits.value:
            raise CaptureUnavailable("Unable to allocate the preview memory bitmap.")
        previous = gdi32.SelectObject(memory_dc, bitmap)
        if not previous or previous == ctypes.c_void_p(-1).value:
            raise CaptureUnavailable("Unable to select the preview memory bitmap.")
        # PW_CLIENTONLY | PW_RENDERFULLCONTENT, then compatibility client-only.
        for flags in (3, 1):
            ctypes.memset(bits, 0, width * height * 4)
            if user32.PrintWindow(hwnd, memory_dc, flags):
                pixels = ctypes.string_at(bits, width * height * 4)
                candidate = Image.frombytes("RGB", (width, height), pixels, "raw", "BGRX")
                if _has_rendered_content(candidate):
                    image = candidate
                    break
        if image is None:
            raise CaptureUnavailable("PrintWindow returned no rendered content (black or empty frame); visual verification unavailable.")
    finally:
        if memory_dc and previous and previous != ctypes.c_void_p(-1).value:
            gdi32.SelectObject(memory_dc, previous)
        if bitmap:
            gdi32.DeleteObject(bitmap)
        if memory_dc:
            gdi32.DeleteDC(memory_dc)
        if source_dc:
            user32.ReleaseDC(hwnd, source_dc)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    image.save(target, format="PNG")
    return target
