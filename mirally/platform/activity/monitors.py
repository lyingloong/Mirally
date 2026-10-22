"""Explicit monitor capture targets and excluded-window image masks."""

import ctypes
import math
import sys
from ctypes import wintypes
from dataclasses import dataclass

from mirally.core.activity.models import stable_opaque_id


@dataclass(frozen=True)
class MonitorSnapshot:
    handle: int
    monitor_id: str
    bounds: tuple[int, int, int, int]
    primary: bool = False


@dataclass(frozen=True)
class WindowRegion:
    bounds: tuple[int, int, int, int]
    excluded: bool
    occlusion_bounds: tuple[int, int, int, int] | None = None


def subtract_rectangle(rectangle, cover) -> list[tuple[int, int, int, int]]:
    left, top, right, bottom = rectangle
    x1, y1, x2, y2 = max(left, cover[0]), max(top, cover[1]), min(right, cover[2]), min(bottom, cover[3])
    if x1 >= x2 or y1 >= y2:
        return [rectangle]
    return [rect for rect in ((left, top, right, y1), (left, y2, right, bottom),
                              (left, y1, x1, y2), (x2, y1, right, y2))
            if rect[0] < rect[2] and rect[1] < rect[3]]


def opaque_client_candidate(exstyle: int, style: int, class_name: str) -> bool:
    if exstyle & (0x80000 | 0x20):  # Layered or transparent.
        return False
    if not exstyle & 0x200000:
        return True
    # Chromium removes its redirection bitmap for opaque main windows too.
    # Its translucent DirectComposition surfaces use borderless popup styles.
    return (class_name == "Chrome_WidgetWin_1" and not style & 0x80000000
            and not exstyle & (0x80 | 0x08000000))


def visible_exclusion_masks(windows) -> list[tuple[int, int, int, int]]:
    """Subtract opaque regions above each excluded window, in top-to-bottom Z order."""
    covers, masks = [], []
    for window in windows:
        if window.excluded:
            fragments = [window.bounds]
            for cover in covers:
                fragments = [piece for fragment in fragments for piece in subtract_rectangle(fragment, cover)]
                if len(fragments) > 4096:
                    fragments = [window.bounds]  # Fail closed on pathological region complexity.
                    break
                if not fragments:
                    break
            masks.extend(fragments)
        if window.occlusion_bounds is not None:
            covers.append(window.occlusion_bounds)
    return masks


class WindowsMonitorSource:
    available = sys.platform == "win32"

    def enumerate(self) -> tuple[MonitorSnapshot, ...]:
        if not self.available:
            return ()
        user = ctypes.windll.user32
        class Info(ctypes.Structure):
            _fields_ = [("size", wintypes.DWORD), ("monitor", wintypes.RECT),
                        ("work", wintypes.RECT), ("flags", wintypes.DWORD), ("device", wintypes.WCHAR * 32)]
        values = []
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HANDLE, wintypes.HDC,
                                          ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)
        user.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Info)]
        def visit(handle, dc, rectangle, data):
            info = Info()
            info.size = ctypes.sizeof(info)
            if user.GetMonitorInfoW(handle, ctypes.byref(info)):
                rect = info.monitor
                values.append(MonitorSnapshot(int(handle), stable_opaque_id("monitor", info.device),
                    (rect.left, rect.top, rect.right, rect.bottom), bool(info.flags & 1)))
            return True
        callback = callback_type(visit)
        user.EnumDisplayMonitors(None, None, callback, 0)
        return tuple(values[:16])

    def masks(self, policy) -> list[tuple[int, int, int, int]]:
        if not self.available:
            return []
        user = ctypes.windll.user32
        user.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        user.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
        previous = user.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
        try:
            return self._masks(policy)
        finally:
            if previous:
                user.SetThreadDpiAwarenessContext(previous)

    def _masks(self, policy) -> list[tuple[int, int, int, int]]:
        import psutil
        user = ctypes.windll.user32
        user.GetTopWindow.argtypes = [wintypes.HWND]
        user.GetTopWindow.restype = wintypes.HWND
        user.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
        user.GetWindow.restype = wintypes.HWND
        user.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        user.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
        user.GetWindowRgn.argtypes = [wintypes.HWND, wintypes.HANDLE]
        gdi = ctypes.windll.gdi32
        gdi.CreateRectRgn.argtypes = [ctypes.c_int] * 4
        gdi.CreateRectRgn.restype = wintypes.HANDLE
        gdi.DeleteObject.argtypes = [wintypes.HANDLE]
        gdi.GetRgnBox.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.RECT)]
        style_fn = user.GetWindowLongPtrW if ctypes.sizeof(ctypes.c_void_p) == 8 else user.GetWindowLongW
        style_fn.argtypes = [wintypes.HWND, ctypes.c_int]
        style_fn.restype = ctypes.c_ssize_t
        dwm = ctypes.windll.dwmapi
        dwm.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
        regions = {}
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def visit(hwnd, data):
            handle = wintypes.HWND(hwnd)
            if not user.IsWindowVisible(handle) or user.IsIconic(handle):
                return True
            cloaked = wintypes.DWORD()
            if dwm.DwmGetWindowAttribute(handle, 14, ctypes.byref(cloaked), ctypes.sizeof(cloaked)) == 0 and cloaked.value:
                return True
            pid = wintypes.DWORD()
            user.GetWindowThreadProcessId(wintypes.HWND(hwnd), ctypes.byref(pid))
            title = ctypes.create_unicode_buffer(2049)
            user.GetWindowTextW(wintypes.HWND(hwnd), title, len(title))
            try:
                name = psutil.Process(pid.value).name().lower()
            except (psutil.Error, OSError):
                name = None
            excluded = not policy.decide("content", app_id=name, window_name=title.value).allowed
            rect = wintypes.RECT()
            result = dwm.DwmGetWindowAttribute(handle, 9, ctypes.byref(rect), ctypes.sizeof(rect))
            if result != 0 and not user.GetWindowRect(handle, ctypes.byref(rect)):
                raise OSError("Cannot determine visible window bounds")
            bounds = (rect.left, rect.top, rect.right, rect.bottom)
            if rect.right <= rect.left or rect.bottom <= rect.top:
                return True
            occlusion = None
            class_name = ctypes.create_unicode_buffer(256)
            user.GetClassNameW(handle, class_name, len(class_name))
            if opaque_client_candidate(style_fn(handle, -20), style_fn(handle, -16), class_name.value):
                client = wintypes.RECT()
                origin = wintypes.POINT()
                if user.GetClientRect(handle, ctypes.byref(client)) and user.ClientToScreen(handle, ctypes.byref(origin)):
                    interior = (max(rect.left, origin.x + 1), max(rect.top, origin.y + 1),
                                min(rect.right, origin.x + client.right - 1),
                                min(rect.bottom, origin.y + client.bottom - 1))
                    if interior[0] < interior[2] and interior[1] < interior[3]:
                        occlusion = interior
                region = gdi.CreateRectRgn(0, 0, 0, 0)
                if not region:
                    occlusion = None
                else:
                    try:
                        kind = user.GetWindowRgn(handle, region)
                        if kind in {1, 3}:  # Empty or nonrectangular window.
                            occlusion = None
                        elif kind == 2 and occlusion is not None:
                            clip, outer = wintypes.RECT(), wintypes.RECT()
                            if not gdi.GetRgnBox(region, ctypes.byref(clip)) or not user.GetWindowRect(handle, ctypes.byref(outer)):
                                occlusion = None
                            else:
                                clipped = (max(interior[0], outer.left + clip.left),
                                           max(interior[1], outer.top + clip.top),
                                           min(interior[2], outer.left + clip.right),
                                           min(interior[3], outer.top + clip.bottom))
                                occlusion = clipped if clipped[0] < clipped[2] and clipped[1] < clipped[3] else None
                    finally:
                        gdi.DeleteObject(region)
            regions[int(hwnd)] = WindowRegion(bounds, excluded, occlusion)
            return True
        errors = []
        def safe_visit(hwnd, data):
            try:
                return visit(hwnd, data)
            except Exception as error:
                errors.append(error)
                return False
        if not user.EnumWindows(callback_type(safe_visit), 0) or errors:
            raise OSError("Cannot enumerate window masks")
        ordered, seen = [], set()
        hwnd = user.GetTopWindow(None)
        while hwnd and int(hwnd) not in seen and len(seen) < 4096:
            seen.add(int(hwnd))
            if int(hwnd) in regions:
                ordered.append(regions.pop(int(hwnd)))
            hwnd = user.GetWindow(hwnd, 2)  # GW_HWNDNEXT
        masks = visible_exclusion_masks(ordered)
        # Windows created/reordered during enumeration have uncertain occlusion.
        masks.extend(region.bounds for region in regions.values() if region.excluded)
        return masks

    def desktop_available(self) -> bool:
        if not self.available:
            return False
        user = ctypes.windll.user32
        user.OpenInputDesktop.restype = wintypes.HANDLE
        user.CloseDesktop.argtypes = [wintypes.HANDLE]
        handle = user.OpenInputDesktop(0, False, 0x0001)
        if not handle:
            return False
        try:
            name = ctypes.create_unicode_buffer(256)
            needed = wintypes.DWORD()
            user.GetUserObjectInformationW(wintypes.HANDLE(handle), 2, name,
                ctypes.sizeof(name), ctypes.byref(needed))
            return name.value.casefold() == "default"
        finally:
            user.CloseDesktop(handle)


def mask_image(path, monitor: MonitorSnapshot, rectangles) -> int:
    from PIL import Image, ImageDraw
    with Image.open(path) as image:
        image.load()
        draw = ImageDraw.Draw(image)
        left, top, right, bottom = monitor.bounds
        sx = image.width / max(1, right - left)
        sy = image.height / max(1, bottom - top)
        count = 0
        for x1, y1, x2, y2 in dict.fromkeys(rectangles):
            x1, y1, x2, y2 = max(left, x1), max(top, y1), min(right, x2), min(bottom, y2)
            if x2 <= x1 or y2 <= y1:
                continue
            draw.rectangle((math.floor((x1-left)*sx), math.floor((y1-top)*sy),
                            math.ceil((x2-left)*sx)-1, math.ceil((y2-top)*sy)-1), fill="black")
            count += 1
        if count:
            image.save(path, format="PNG")
        return count


def image_black_fraction(path, *, threshold: int = 8, max_dimension: int = 256) -> float:
    """Estimate the exact-black area without loading every source pixel."""

    from PIL import Image

    with Image.open(path) as image:
        image = image.convert("RGB")
        image.thumbnail((max_dimension, max_dimension), Image.Resampling.NEAREST)
        pixels = list(image.getdata())
    if not pixels:
        return 1.0
    limit = max(0, int(threshold))
    return sum(1 for red, green, blue in pixels if max(red, green, blue) <= limit) / len(pixels)
