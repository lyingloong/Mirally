"""A user-directed capture of the external window under the pointer, kept in memory."""
from __future__ import annotations

import base64
import ctypes
import io
import os
from ctypes import wintypes


class DesktopContextError(RuntimeError):
    pass


def capture_target(own_hwnd: int, screen_point: tuple[int, int] | None = None) -> dict:
    if os.name != "nt":
        raise DesktopContextError("此操作需要 Windows 桌面。")
    from PIL import Image, ImageGrab

    user = ctypes.WinDLL("user32", use_last_error=True)
    gdi = ctypes.WinDLL("gdi32", use_last_error=True)
    user.WindowFromPoint.argtypes = [wintypes.POINT]
    user.WindowFromPoint.restype = wintypes.HWND
    user.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user.GetAncestor.restype = wintypes.HWND
    user.GetDC.argtypes = [wintypes.HWND]
    user.GetDC.restype = wintypes.HDC
    user.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    user.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
    for name, args, result in (
        ("CreateCompatibleDC", [wintypes.HDC], wintypes.HDC),
        ("CreateCompatibleBitmap", [wintypes.HDC, ctypes.c_int, ctypes.c_int], wintypes.HBITMAP),
        ("SelectObject", [wintypes.HDC, wintypes.HANDLE], wintypes.HANDLE),
        ("DeleteObject", [wintypes.HANDLE], wintypes.BOOL),
        ("DeleteDC", [wintypes.HDC], wintypes.BOOL),
    ):
        fn = getattr(gdi, name)
        fn.argtypes, fn.restype = args, result
    # Set pointer-size signatures for APIs whose default ctypes conversion truncates HWND.
    for name in ("IsWindowVisible", "IsIconic", "GetWindowTextLengthW"):
        getattr(user, name).argtypes = [wintypes.HWND]
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]

    point = wintypes.POINT(*screen_point) if screen_point is not None else wintypes.POINT()
    if screen_point is None and not user.GetCursorPos(ctypes.byref(point)):
        raise DesktopContextError("未能确定目标位置，请重新拖动。")
    hwnd = user.GetAncestor(user.WindowFromPoint(point), 2)
    pid = wintypes.DWORD()
    user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    cls = ctypes.create_unicode_buffer(256)
    user.GetClassNameW(hwnd, cls, len(cls))
    if (not hwnd or hwnd == own_hwnd or pid.value == os.getpid()
            or not user.IsWindowVisible(hwnd) or user.IsIconic(hwnd)
            or cls.value in {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"}):
        raise DesktopContextError("请拖到需要交给我的应用窗口中，再松开。")
    rect = wintypes.RECT()
    if not user.GetClientRect(hwnd, ctypes.byref(rect)):
        raise DesktopContextError("这个窗口暂时无法读取画面。")
    width, height = rect.right - rect.left, rect.bottom - rect.top
    if min(width, height) < 32 or width * height > 32_000_000:
        raise DesktopContextError("目标窗口尺寸不适合读取，请调整窗口后重试。")
    title = ctypes.create_unicode_buffer(min(user.GetWindowTextLengthW(hwnd), 1024) + 1)
    user.GetWindowTextW(hwnd, title, len(title))

    class Header(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("width", wintypes.LONG),
                    ("height", wintypes.LONG), ("planes", wintypes.WORD),
                    ("bits", wintypes.WORD), ("compression", wintypes.DWORD),
                    ("image_size", wintypes.DWORD), ("x", wintypes.LONG),
                    ("y", wintypes.LONG), ("used", wintypes.DWORD), ("important", wintypes.DWORD)]

    gdi.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT,
                            wintypes.UINT, ctypes.c_void_p, ctypes.POINTER(Header), wintypes.UINT]
    dc = user.GetDC(hwnd)
    memory = gdi.CreateCompatibleDC(dc) if dc else None
    bitmap = gdi.CreateCompatibleBitmap(dc, width, height) if memory else None
    old = gdi.SelectObject(memory, bitmap) if bitmap else None
    image = None
    try:
        if bitmap and user.PrintWindow(hwnd, memory, 3):
            gdi.SelectObject(memory, old)
            old = None
            header = Header(ctypes.sizeof(Header), width, -height, 1, 32, 0, 0, 0, 0, 0, 0)
            pixels = ctypes.create_string_buffer(width * height * 4)
            if gdi.GetDIBits(memory, bitmap, 0, height, pixels, ctypes.byref(header), 0) == height:
                image = Image.frombytes("RGB", (width, height), pixels.raw, "raw", "BGRX")
                # Some GPU windows report success while returning an empty black/white surface.
                extrema = image.resize((64, 64)).getextrema()
                if all(lo == hi for lo, hi in extrema):
                    image = None
    finally:
        if old:
            gdi.SelectObject(memory, old)
        if bitmap:
            gdi.DeleteObject(bitmap)
        if memory:
            gdi.DeleteDC(memory)
        if dc:
            user.ReleaseDC(hwnd, dc)
    source = "window"
    if image is None:
        origin = wintypes.POINT()
        if not user.ClientToScreen(hwnd, ctypes.byref(origin)):
            raise DesktopContextError("这个窗口暂时无法读取画面。")
        vx, vy = user.GetSystemMetrics(76), user.GetSystemMetrics(77)
        right, bottom = vx + user.GetSystemMetrics(78), vy + user.GetSystemMetrics(79)
        box = (max(origin.x, vx), max(origin.y, vy),
               min(origin.x + width, right), min(origin.y + height, bottom))
        if box[2] - box[0] < 32 or box[3] - box[1] < 32:
            raise DesktopContextError("请让目标窗口显示在屏幕内后重试。")
        try:
            image = ImageGrab.grab(bbox=box, all_screens=True)
        except Exception:
            raise DesktopContextError("暂时无法读取这个窗口，请改为添加文件。") from None
        source = "visible-area"
        if all(lo == hi for lo, hi in image.resize((64, 64)).convert("RGB").getextrema()):
            raise DesktopContextError("目标画面为空，未添加截图，请重试或添加文件。")
    image.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
    encoded = io.BytesIO()
    image.save(encoded, "PNG")
    return {"title": title.value or "目标窗口", "source": source,
            "width": image.width, "height": image.height,
            "dataUrl": "data:image/png;base64," + base64.b64encode(encoded.getvalue()).decode("ascii")}
