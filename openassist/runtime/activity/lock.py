"""Per-user single-instance guard for the activity service."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional


class ActivityProcessLock:
    def __init__(self, name: str = "Local\\MirallyActivitySensor", fallback_path: Optional[Path] = None) -> None:
        self.name = name
        self.fallback_path = fallback_path or Path.home() / ".mirally-activity.lock"
        self.handle = None
        self.stream = None

    def acquire(self) -> bool:
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
            kernel.CreateMutexW.restype = wintypes.HANDLE
            handle = kernel.CreateMutexW(None, False, self.name)
            if not handle:
                return False
            already_exists = ctypes.get_last_error() == 183
            if already_exists:
                kernel.CloseHandle(handle)
                return False
            self.handle = (kernel, handle)
            return True
        try:
            import fcntl
            self.fallback_path.parent.mkdir(parents=True, exist_ok=True)
            self.stream = self.fallback_path.open("a+", encoding="utf-8")
            fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.stream.write(str(os.getpid()))
            self.stream.flush()
            return True
        except (OSError, ImportError):
            self.release()
            return False

    def release(self) -> None:
        if self.handle is not None:
            kernel, handle = self.handle
            kernel.CloseHandle(handle)
            self.handle = None
        if self.stream is not None:
            try:
                import fcntl
                fcntl.flock(self.stream.fileno(), fcntl.LOCK_UN)
            except (OSError, ImportError):
                pass
            self.stream.close()
            self.stream = None

    def __enter__(self) -> "ActivityProcessLock":
        if not self.acquire():
            raise RuntimeError("another Mirally activity sensor is already running")
        return self

    def __exit__(self, *_: object) -> None:
        self.release()
