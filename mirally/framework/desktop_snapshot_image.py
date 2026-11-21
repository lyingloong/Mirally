"""Label a task's window image using the same target IDs as its UIA snapshot."""
from __future__ import annotations

import base64
import ctypes
import io
import json
import subprocess
from ctypes import wintypes

from PIL import Image, ImageDraw, ImageFont

from mirally.desktop_context import print_client_window
from mirally.platform.activity.wgc_capture import WindowsWindowCaptureSource


MAX_DIMENSION = 1600
LABEL_COLOR = (42, 112, 234)
RECT_FIELDS = ("left", "top", "right", "bottom")


def _rect_tuple(rect):
    return tuple(getattr(rect, key) for key in RECT_FIELDS)


def _dimensions(rect):
    left, top, right, bottom = rect
    return right - left, bottom - top


def _window_frames(user, hwnd):
    """WGC can include the full window or the DWM-visible frame."""
    dwm = ctypes.WinDLL("dwmapi")
    dwm.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
    frames = set()
    rect = wintypes.RECT()
    if dwm.DwmGetWindowAttribute(hwnd, 9, ctypes.byref(rect), ctypes.sizeof(rect)) == 0:
        frames.add(_rect_tuple(rect))
    user.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    if user.GetWindowRect(hwnd, ctypes.byref(rect)):
        frames.add(_rect_tuple(rect))
    return frames


def _matching_frame(frames, capture_size):
    matching = [frame for frame in frames if _dimensions(frame) == capture_size]
    return matching[0] if len(matching) == 1 else None


def _capture_wgc(hwnd, frames):
    source = WindowsWindowCaptureSource()
    if not source.available:
        return None
    # The one-shot helper streams pixels in memory. Aborting the observation
    # cannot leave an unmasked file behind.
    try:
        capture = subprocess.run(
            [str(source.helper_path), "--window-png", str(hwnd)], stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=3.5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=False,
        )
        header, _, png = capture.stdout.partition(b"\n")
        if capture.returncode or len(header) > 4096 or not 0 < len(png) <= 12 * 1024 * 1024:
            return None
        info = json.loads(header)
        if not info.get("captured") or info.get("near_black"):
            return None
        runtime = info["runtime"]
        frame = _matching_frame(frames, (runtime["frame_width"], runtime["frame_height"]))
        if frame is None:
            return None
        with Image.open(io.BytesIO(png)) as captured:
            if (captured.format != "PNG" or captured.size != tuple(info["size"])
                    or max(captured.size) > MAX_DIMENSION):
                return None
            return captured.convert("RGB"), frame
    except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired):
        return None


def _client_frame(user, hwnd):
    user.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
    rect, origin = wintypes.RECT(), wintypes.POINT()
    if not user.GetClientRect(hwnd, ctypes.byref(rect)) or not user.ClientToScreen(hwnd, ctypes.byref(origin)):
        return None
    return origin.x, origin.y, origin.x + rect.right, origin.y + rect.bottom


def _project_bounds(bounds, frame, size):
    left, top, right, bottom = frame
    x1, y1 = max(left, bounds["left"]), max(top, bounds["top"])
    x2, y2 = min(right, bounds["right"]), min(bottom, bounds["bottom"])
    if x2 <= x1 or y2 <= y1:
        return None
    width, height = size
    source_width, source_height = _dimensions(frame)
    return (
        (x1 - left) * width // source_width,
        (y1 - top) * height // source_height,
        ((x2 - left) * width - 1) // source_width,
        ((y2 - top) * height - 1) // source_height,
    )


def _overlaps(a, b):
    return a[0] <= b[2] and a[2] >= b[0] and a[1] <= b[3] and a[3] >= b[1]


def _annotate(image, frame, targets):
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=14)
    labels, marked = [], []
    for target in targets:
        box = _project_bounds(target["bounds"], frame, image.size)
        if box is None:
            continue
        left, top, right, bottom = box
        text = str(target["id"])
        label_width, label_height = round(font.getlength(text)) + 7, 18
        for x, y in ((left, top), (right - label_width, top),
                     (left, bottom - label_height), (right - label_width, bottom - label_height)):
            x = max(0, min(x, image.width - label_width - 1))
            y = max(0, min(y, image.height - label_height - 1))
            label = (x, y, x + label_width, y + label_height)
            if any(_overlaps(label, previous) for previous in labels):
                continue
            draw.rectangle(box, outline=LABEL_COLOR, width=2)
            draw.rectangle(label, fill=LABEL_COLOR)
            draw.text((x + 3, y + 2), text, anchor="lt", font=font, fill="white")
            labels.append(label)
            marked.append(target["id"])
            break
    return marked


def _mask_controls(image, frame, redactions):
    draw = ImageDraw.Draw(image)
    for bounds in redactions:
        box = _project_bounds(bounds, frame, image.size)
        if box is not None:
            draw.rectangle(box, fill=(10, 10, 10))


def capture_annotated_window(snapshot, read_window):
    """Capture only the selected window, with a client-only fallback."""
    def unavailable(reason):
        return {"status": "unavailable", "reason": reason}

    if snapshot.get("image_safe") is not True:
        return unavailable("control_privacy_undetermined")
    window = snapshot["window"]
    hwnd = window["hwnd"]
    user = ctypes.WinDLL("user32", use_last_error=True)
    try:
        if read_window() != window:
            return unavailable("window_changed")
        frames = _window_frames(user, hwnd)
        captured = _capture_wgc(hwnd, frames)
        if read_window() != window or _window_frames(user, hwnd) != frames:
            return unavailable("window_changed")
        source = "wgc"
        if captured is None:
            source = "window"
            frame = _client_frame(user, hwnd)
            if frame is None:
                return unavailable("window_capture_unavailable")
            image = print_client_window(user, hwnd, *_dimensions(frame))
            if read_window() != window or _client_frame(user, hwnd) != frame:
                return unavailable("window_changed")
            if image is None:
                return unavailable("window_capture_unavailable")
            image.thumbnail((MAX_DIMENSION, MAX_DIMENSION), Image.Resampling.LANCZOS)
        else:
            image, frame = captured
        _mask_controls(image, frame, snapshot.get("redactions", ()))
        marked = _annotate(image, frame, snapshot["targets"])
        encoded = io.BytesIO()
        image.save(encoded, "JPEG", quality=86, optimize=True)
        return {
            "status": "ok", "source": source, "width": image.width, "height": image.height,
            "frame": dict(zip(RECT_FIELDS, frame)), "annotated_targets": marked,
            "data": base64.b64encode(encoded.getvalue()).decode("ascii"),
        }
    except (OSError, RuntimeError, ValueError):
        return unavailable("window_capture_unavailable")
