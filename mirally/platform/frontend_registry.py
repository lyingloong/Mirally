"""Identify product windows across the WebView host and UFO worker processes."""
from __future__ import annotations

import json
import os
from pathlib import Path
import time


class FrontendRegistration:
    def __init__(self, root: Path):
        import psutil
        self.pid = os.getpid()
        self.created = psutil.Process(self.pid).create_time()
        self.path = root / ".artifacts" / "frontends" / f"{self.pid}.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.update(False)

    def update(self, editing: bool):
        value = {"pid": self.pid, "created": self.created,
                 "editing": bool(editing), "updated": time.time()}
        pending = self.path.with_suffix(".pending")
        pending.write_text(json.dumps(value), encoding="utf-8")
        os.replace(pending, self.path)

    def close(self):
        self.path.unlink(missing_ok=True)


def registered_frontends(root: Path) -> tuple[dict, ...]:
    """Validate creation time as well as PID; stale files cannot adopt a new process."""
    import psutil
    entries = []
    directory = root / ".artifacts" / "frontends"
    if not directory.is_dir():
        return ()
    for path in directory.glob("*.json"):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            pid = value["pid"]
            if type(pid) is not int or pid <= 0 or path.stem != str(pid):
                continue
            process = psutil.Process(pid)
            if abs(process.create_time() - float(value["created"])) > 0.01:
                continue
            # WebView2 descendants are part of the host's UIA tree too.
            owners = {pid: process.create_time()}
            try:
                children = process.children(recursive=True)
            except psutil.Error:
                children = []
            for child in children:
                try:
                    # Product-host workers and apps they open are task targets;
                    # only WebView2 children belong to the frontend UI.
                    if child.name().casefold() == "msedgewebview2.exe":
                        owners[child.pid] = child.create_time()
                except psutil.Error:
                    continue
            value["owners"] = owners
            value["process_ids"] = tuple(value["owners"])
            entries.append(value)
        except (OSError, ValueError, KeyError, TypeError, psutil.Error):
            continue
    return tuple(entries)


class FrontendCapture:
    """Exclude registered frontend processes, with a scoped hide fallback."""
    def __init__(self, root: Path):
        import ctypes
        from ctypes import wintypes
        self.root, self.ctypes, self.wintypes = root, ctypes, wintypes
        self.user = ctypes.WinDLL("user32", use_last_error=True)
        self.records = {}
        self._process_cache = (0.0, ())
        for name, arguments, result in (
            ("GetWindowThreadProcessId", [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)], wintypes.DWORD),
            ("GetWindowDisplayAffinity", [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL),
            ("IsWindow", [wintypes.HWND], wintypes.BOOL),
            ("IsWindowVisible", [wintypes.HWND], wintypes.BOOL),
            ("IsIconic", [wintypes.HWND], wintypes.BOOL),
            ("ShowWindow", [wintypes.HWND, ctypes.c_int], wintypes.BOOL),
        ):
            function = getattr(self.user, name)
            function.argtypes, function.restype = arguments, result

    def process_ids(self):
        # UIA filters thousands of controls in one observation. Avoid walking
        # the WebView process tree separately for every element.
        checked, values = self._process_cache
        now = time.monotonic()
        if now - checked >= 0.1:
            values = tuple(pid for entry in registered_frontends(self.root) for pid in entry["process_ids"])
            self._process_cache = (now, values)
        return values

    def editing(self):
        return any(entry.get("editing") is True for entry in registered_frontends(self.root))

    def _pid(self, hwnd):
        pid = self.wintypes.DWORD()
        self.user.GetWindowThreadProcessId(hwnd, self.ctypes.byref(pid))
        return pid.value

    def before(self):
        if self.records:
            raise RuntimeError("A frontend capture has not been restored")
        entries = registered_frontends(self.root)
        owners = {pid: created for entry in entries for pid, created in entry["owners"].items()}
        pids = {pid for entry in entries for pid in entry["process_ids"]}
        callback_type = self.ctypes.WINFUNCTYPE(self.wintypes.BOOL,
                                                self.wintypes.HWND, self.wintypes.LPARAM)
        windows = []

        @callback_type
        def collect(hwnd, _):
            if self._pid(hwnd) in pids:
                windows.append(int(hwnd))
            return True

        self.user.EnumWindows.argtypes = [callback_type, self.wintypes.LPARAM]
        self.user.EnumWindows.restype = self.wintypes.BOOL
        try:
            if not self.user.EnumWindows(collect, 0):
                raise RuntimeError("Cannot enumerate Mirally windows")
            for hwnd in windows:
                if not self.user.IsWindowVisible(hwnd) or self.user.IsIconic(hwnd):
                    continue
                affinity = self.wintypes.DWORD()
                if (self.user.GetWindowDisplayAffinity(hwnd, self.ctypes.byref(affinity))
                        and affinity.value == 0x11):
                    continue
                # A worker cannot set another process's display affinity.
                # Hide only the validated product HWND, restoring without activation.
                pid = self._pid(hwnd)
                self.records[hwnd] = (pid, owners.get(pid))
                self.user.ShowWindow(hwnd, 0)
                if self.user.IsWindowVisible(hwnd):
                    raise RuntimeError("Cannot hide Mirally for capture")
            if self.ctypes.windll.dwmapi.DwmFlush() != 0:
                raise RuntimeError("Cannot flush desktop composition")
        except BaseException:
            self.after()
            raise

    def after(self):
        import psutil
        failed = {}
        for hwnd, (pid, created) in self.records.items():
            try:
                if not self.user.IsWindow(hwnd) or self._pid(hwnd) != pid:
                    continue
                if created is not None and abs(psutil.Process(pid).create_time() - created) > 0.01:
                    continue
                if not self.user.IsIconic(hwnd):
                    self.user.ShowWindow(hwnd, 4)  # SW_SHOWNOACTIVATE
                    if not self.user.IsWindowVisible(hwnd):
                        raise RuntimeError("Cannot restore Mirally after capture")
            except psutil.NoSuchProcess:
                continue
            except Exception:
                failed[hwnd] = (pid, created)
        self.records = failed
        if failed:
            raise RuntimeError("Some Mirally windows could not be restored")
