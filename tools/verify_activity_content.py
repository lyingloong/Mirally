"""Verify native UIA/WGC/OCR and occlusion using nonactivating owned windows."""

from __future__ import annotations

import ctypes
import json
import tempfile
import threading
import time
from ctypes import wintypes
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image, ImageStat
from openassist.core.activity.models import CollectionLevel
from openassist.core.activity.policy import ActivityPolicy
from openassist.platform.activity.monitors import WindowsMonitorSource
from openassist.platform.activity.ocr import WindowsNativeOCRSource
from openassist.platform.activity.uia_structure import WindowsUIASource
from openassist.platform.activity.wgc_capture import WindowsWindowCaptureSource


def main() -> int:
    if sys.platform != "win32":
        raise RuntimeError("Windows is required")
    user = ctypes.windll.user32
    user.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    user.CreateWindowExW.restype = wintypes.HWND
    user.CreateWindowExW.argtypes = [wintypes.DWORD,wintypes.LPCWSTR,wintypes.LPCWSTR,wintypes.DWORD,
        ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_int,wintypes.HWND,wintypes.HMENU,wintypes.HINSTANCE,ctypes.c_void_p]
    user.DestroyWindow.argtypes = [wintypes.HWND]
    user.ShowWindow.argtypes = [wintypes.HWND,ctypes.c_int]
    user.SetWindowPos.argtypes = [wintypes.HWND,wintypes.HWND,ctypes.c_int,ctypes.c_int,
                                  ctypes.c_int,ctypes.c_int,wintypes.UINT]
    user.GetWindowRect.argtypes = [wintypes.HWND,ctypes.POINTER(wintypes.RECT)]
    user.SetWindowTextW.argtypes = [wintypes.HWND,wintypes.LPCWSTR]
    user.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
    user.DispatchMessageW.restype = wintypes.LPARAM
    hwnd = user.CreateWindowExW(0x08000000,"STATIC","Mirally Activity Fixture",0x00CF0000,
                              50,50,440,220,None,None,None,None)
    if not hwnd:
        raise ctypes.WinError()
    child = user.CreateWindowExW(0,"EDIT","ACTIVITY FIXTURE 12345 \u4e2d\u6587",0x50000000 | 0x00800000,
                                18,28,370,70,hwnd,None,None,None)
    password = user.CreateWindowExW(0,"EDIT","PASSWORD MUST NOT APPEAR",0x50000000 | 0x20,
                                   18,120,370,24,hwnd,None,None,None)
    if not child or not password:
        user.DestroyWindow(hwnd)
        raise ctypes.WinError()
    background = user.CreateWindowExW(0x08000000,"STATIC","Mirally Activity Mask Fixture",0x00CF0000,
                                     50,50,440,220,None,None,None,None)
    if not background:
        user.DestroyWindow(hwnd)
        raise ctypes.WinError()
    user.ShowWindow(background,4)
    user.ShowWindow(hwnd,4)  # SW_SHOWNOACTIVATE
    user.SetWindowPos(hwnd,None,0,0,0,0,0x13)  # HWND_TOP, no move/size/activation
    results = {}
    failures = []
    def run_sources(directory):
        capture_source = WindowsWindowCaptureSource()
        try:
            uia = WindowsUIASource().inspect(hwnd,include_content=True,timeout_ms=6000,max_depth=4,max_nodes=30)
            results["uia_status"] = uia.status
            results["uia_duration_ms"] = uia.duration_ms
            results["uia_nodes"] = uia.node_count
            serialized = json.dumps(uia.as_dict())
            assert "ACTIVITY FIXTURE 12345" in serialized, uia.as_dict()
            assert "\u4e2d\u6587" in json.dumps(uia.as_dict(),ensure_ascii=False)
            assert "PASSWORD MUST NOT APPEAR" not in serialized
            path = Path(directory)/"owned-fixture.png"
            capture = capture_source.capture(hwnd,path,timeout_ms=6000)
            assert capture.status == "ok", capture.as_dict()
            with Image.open(path) as image:
                assert min(image.size) > 100
                assert max(ImageStat.Stat(image.convert("RGB")).stddev) > 2
                results["capture_size"] = list(image.size)
            results["capture_status"] = capture.status
            results["capture_duration_ms"] = round(capture.duration_ms or 0,2)
            assert capture.runtime.get("persistent") is True, capture.as_dict()
            assert capture.runtime.get("border_status") in {"disable_requested", "unsupported", "request_failed"}
            assert capture.runtime.get("readbacks") == 1
            results["border_status"] = capture.runtime.get("border_status")
            assert user.SetWindowTextW(child,"UPDATED FIXTURE 67890")
            time.sleep(0.3)
            changed = capture_source.capture(hwnd,Path(directory)/"changed.png",timeout_ms=6000)
            assert changed.status == "ok", changed.as_dict()
            assert changed.runtime["helper_pid"] == capture.runtime["helper_pid"]
            assert changed.runtime["session_id"] == capture.runtime["session_id"]
            assert changed.runtime["session_reused"] is True
            assert changed.runtime["frame_id"] > capture.runtime["frame_id"]
            assert changed.runtime["readbacks"] == 2
            assert changed.signature["sha256"] != capture.signature["sha256"]
            assert user.SetWindowPos(hwnd,None,0,0,540,260,0x16)  # no activation, move or Z-order change
            time.sleep(0.4)
            resized = capture_source.capture(hwnd,Path(directory)/"resized.png",timeout_ms=6000)
            assert resized.status == "ok", resized.as_dict()
            assert resized.runtime["session_id"] == capture.runtime["session_id"]
            assert resized.runtime["resize_count"] >= 1
            assert resized.width > capture.width and resized.height > capture.height
            results["session_reused_after_resize"] = True
            old_process = capture_source._worker._process.process
            capture_source.pause()
            assert old_process.poll() is not None
            assert capture_source.capture(hwnd,Path(directory)/"paused.png").status == "cancelled"
            assert capture_source.status()["running"] is False
            capture_source.resume()
            resumed = capture_source.capture(hwnd,Path(directory)/"resumed.png",timeout_ms=6000)
            assert resumed.status == "ok", resumed.as_dict()
            assert resumed.runtime["session_reused"] is False
            assert resumed.runtime["helper_pid"] != capture.runtime["helper_pid"]
            results["pause_releases_process_and_resume_restarts"] = True
            ocr = WindowsNativeOCRSource().recognize(path,timeout_ms=6000)
            assert ocr.status == "ok", ocr.as_dict()
            assert len(ocr.text) > 5
            assert "PASSWORD MUST NOT APPEAR" not in ocr.text
            results["ocr_status"] = ocr.status
            results["ocr_chars"] = len(ocr.text)
            results["ocr_language"] = ocr.language
            policy = ActivityPolicy(enabled=True,default_level=CollectionLevel.CONTENT,
                exclude_self=False,excluded_windows=("Mirally Activity Mask Fixture",))
            source = WindowsMonitorSource()
            masks = source.masks(policy)
            rect = wintypes.RECT()
            assert user.GetWindowRect(background,ctypes.byref(rect))
            center = ((rect.left+rect.right)//2,(rect.top+rect.bottom)//2)
            assert not any(left <= center[0] < right and top <= center[1] < bottom
                           for left,top,right,bottom in masks), "Occluded background masks the foreground"
            results["background_mask_center_clear"] = True
            user.ShowWindow(background,7)  # SW_SHOWMINNOACTIVE
            assert source.masks(policy) == [], "Minimized windows must not mask the desktop"
            results["minimized_mask_count"] = 0
            process = capture_source._worker._process.process
            process.stdin.close()
            process.wait(timeout=3)
            assert process.returncode == 0, process.returncode
            results["native_eof_releases_sessions"] = True
        except Exception as error:
            failures.append(error)
        finally:
            capture_source.close()
            assert capture_source.status()["running"] is False
    try:
        with tempfile.TemporaryDirectory() as directory:
            worker = threading.Thread(target=run_sources,args=(directory,))
            worker.start()
            message = wintypes.MSG()
            while worker.is_alive():
                while user.PeekMessageW(ctypes.byref(message),None,0,0,1):
                    user.TranslateMessage(ctypes.byref(message))
                    user.DispatchMessageW(ctypes.byref(message))
                worker.join(0.02)
            if failures:
                raise failures[0]
    finally:
        user.DestroyWindow(background)
        user.DestroyWindow(hwnd)
    print(json.dumps(results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
