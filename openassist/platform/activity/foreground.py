"""Low-cost foreground/window identity source.

This module only reads the current foreground HWND.  It does not activate,
hide, move, focus, inspect UIA, or capture pixels.  Expensive evidence belongs
behind the runtime policy gate and isolated workers.
"""

from __future__ import annotations

import ctypes
import hashlib
import os
import sys
import time
from dataclasses import dataclass
from typing import Optional

try:
    import psutil
except ImportError:  # pragma: no cover - the product environment includes it
    psutil = None  # type: ignore[assignment]

from ...core.activity.models import stable_opaque_id


@dataclass(frozen=True)
class ForegroundSnapshot:
    hwnd: int
    pid: int
    process_instance_id: str
    window_instance_id: str
    app_id: Optional[str]
    observed_at_utc_ms: int
    monotonic_ns: int
    identity_confidence: str
    boot_id: str
    title_available: bool = False
    window_title: Optional[str] = None


def _boot_id() -> str:
    # A process-local boot epoch is safer than pretending an arbitrary Windows
    # boot identifier is available.  The service persists its own source epoch.
    return stable_opaque_id("boot", f"{os.getpid()}:{int(time.time() - time.monotonic())}")


class WindowsForegroundSource:
    """Poll the foreground window without touching the user's desktop."""

    def __init__(
        self,
        *,
        include_process_name: bool = True,
        include_window_title: bool = True,
    ) -> None:
        self.available = sys.platform == "win32"
        self.include_process_name = include_process_name
        self.include_window_title = include_window_title
        self._user32 = ctypes.windll.user32 if self.available else None
        if self._user32 is not None:
            self._user32.GetForegroundWindow.restype = ctypes.c_void_p
        self._boot = _boot_id()
        self._window_generations: dict[tuple[int, str], int] = {}
        self._last_hwnd: Optional[int] = None

    def _process_details(self, pid: int) -> tuple[str, str, str]:
        created: Optional[float] = None
        name = "unknown"
        if psutil is not None:
            try:
                process = psutil.Process(pid)
                created = process.create_time()
                if self.include_process_name:
                    name = process.name() or "unknown"
            except (psutil.Error, OSError):
                pass
        process_key = f"{self._boot}:{pid}:{created if created is not None else 'unknown'}"
        process_id = stable_opaque_id("proc", process_key)
        return process_id, name, process_key

    def _window_title(self, hwnd: int) -> Optional[str]:
        if not self.include_window_title or self._user32 is None:
            return None
        try:
            length = int(self._user32.GetWindowTextLengthW(ctypes.c_void_p(hwnd)) or 0)
            if length <= 0:
                return None
            buffer = ctypes.create_unicode_buffer(min(length, 2048) + 1)
            copied = int(
                self._user32.GetWindowTextW(
                    ctypes.c_void_p(hwnd), buffer, len(buffer)
                )
                or 0
            )
            title = buffer.value[: min(copied, 2048)].strip()
            return title or None
        except (AttributeError, OSError, TypeError, ValueError):
            return None

    def poll(self) -> Optional[ForegroundSnapshot]:
        if not self.available or self._user32 is None:
            return None
        hwnd = int(self._user32.GetForegroundWindow() or 0)
        if not hwnd:
            return None
        pid_value = ctypes.c_ulong(0)
        self._user32.GetWindowThreadProcessId(ctypes.c_void_p(hwnd), ctypes.byref(pid_value))
        pid = int(pid_value.value)
        if not pid:
            return None
        process_id, name, process_key = self._process_details(pid)
        key = (hwnd, process_id)
        if key not in self._window_generations:
            self._window_generations[key] = 1
        if self._last_hwnd is not None and self._last_hwnd == hwnd:
            generation = self._window_generations[key]
        else:
            generation = self._window_generations[key]
        self._last_hwnd = hwnd
        window_id = stable_opaque_id("window", f"{process_key}:{hwnd}:{generation}")
        title = self._window_title(hwnd)
        return ForegroundSnapshot(
            hwnd=hwnd,
            pid=pid,
            process_instance_id=process_id,
            window_instance_id=window_id,
            app_id=name.lower() if name != "unknown" else None,
            observed_at_utc_ms=int(time.time() * 1000),
            monotonic_ns=time.monotonic_ns(),
            identity_confidence="pid_only" if process_key.endswith(":unknown") else "verified_process_start",
            boot_id=self._boot,
            title_available=bool(title),
            window_title=title,
        )

    def matches(self, snapshot: ForegroundSnapshot) -> bool:
        current = self.poll()
        return bool(current and current.window_instance_id == snapshot.window_instance_id
                    and current.window_title == snapshot.window_title)
