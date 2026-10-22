"""Out-of-process Windows Raw Input metadata source.

The helper process owns its hidden message-only window.  This is intentional:
Raw Input registration is process-wide for a usage, and Mirally already has a
pointer observer.  Keeping this registration in a child process prevents the
activity collector from replacing an existing UI registration.  Only bounded
counts and clipboard sequence signals cross the process boundary. Clipboard
text is read separately so delayed rendering cannot block this message pump.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from dataclasses import dataclass
from typing import Any, Callable, Optional

from .input_metadata import InputBatch


InputBatchCallback = Callable[[InputBatch], None]


@dataclass(frozen=True)
class InputPumpStatus:
    available: bool
    running: bool
    reason: Optional[str] = None


class WindowsInputMetadataSource:
    """Parent-side lifecycle for the isolated Raw Input helper."""
    supports_content_events = True

    def __init__(
        self,
        *,
        python_executable: Optional[str] = None,
        quiet_ms: int = 1500,
        max_burst_ms: int = 10_000,
        clipboard: bool = False,
    ) -> None:
        self.available = sys.platform == "win32"
        self.python_executable = python_executable or sys.executable
        self.quiet_ms = max(100, min(60_000, int(quiet_ms)))
        self.max_burst_ms = max(1_000, min(300_000, int(max_burst_ms)))
        self._process: Optional[subprocess.Popen[str]] = None
        self._reader: Optional[threading.Thread] = None
        self._callback: Optional[InputBatchCallback] = None
        self._event_callback = None
        self.clipboard = clipboard
        self._stop = threading.Event()
        self._status = InputPumpStatus(self.available, False, None if self.available else "not_windows")

    @property
    def status(self) -> InputPumpStatus:
        return self._status

    def start(self, callback: InputBatchCallback, *, event_callback=None) -> bool:
        if self._process is not None and self._process.poll() is None:
            return True
        if not self.available:
            self._status = InputPumpStatus(False, False, "not_windows")
            return False
        command = [
            self.python_executable,
            "-u",
            "-m",
            "mirally.platform.activity.input_worker",
            "--quiet-ms",
            str(self.quiet_ms),
            "--max-burst-ms",
            str(self.max_burst_ms),
        ]
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        if self.clipboard:
            command.append("--clipboard")
        try:
            self._process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=creationflags,
            )
        except (OSError, ValueError) as error:
            self._process = None
            self._status = InputPumpStatus(True, False, type(error).__name__)
            return False
        self._callback = callback
        self._event_callback = event_callback
        self._stop.clear()
        self._reader = threading.Thread(target=self._read, name="mirally-input-reader", daemon=True)
        self._reader.start()
        self._status = InputPumpStatus(True, True, None)
        return True

    def _read(self) -> None:
        process = self._process
        stream = process.stdout if process is not None else None
        if stream is None:
            return
        try:
            for line in stream:
                if self._stop.is_set():
                    break
                try:
                    value: Any = json.loads(line)
                    if value.get("type", "input_batch") != "input_batch":
                        if self._event_callback is not None:
                            self._event_callback(value)
                        continue
                    batch = InputBatch(
                        keyboard_count=max(0, int(value.get("keyboard_count", 0))),
                        click_count=max(0, int(value.get("click_count", 0))),
                        scroll_count=max(0, int(value.get("scroll_count", 0))),
                        motion=bool(value.get("motion", False)),
                        started_monotonic_ns=max(0, int(value.get("started_monotonic_ns", 0))),
                        ended_monotonic_ns=max(0, int(value.get("ended_monotonic_ns", 0))),
                        quiet_ms=max(0, int(value.get("quiet_ms", 0))),
                        hwnd=max(0, int(value.get("hwnd", 0))),
                        pid=max(0, int(value.get("pid", 0))),
                        observed_at_utc_ms=max(0, int(value.get("observed_at_utc_ms", 0))),
                    )
                except (TypeError, ValueError, json.JSONDecodeError):
                    continue
                callback = self._callback
                if callback is not None:
                    callback(batch)
        finally:
            if not self._stop.is_set():
                self._status = InputPumpStatus(True, False, "worker_exit")

    def stop(self) -> None:
        self._stop.set()
        process, self._process = self._process, None
        if process is not None:
            try:
                process.terminate()
                process.wait(timeout=1.0)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    process.kill()
                    process.wait(timeout=1.0)
                except OSError:
                    pass
        reader, self._reader = self._reader, None
        if reader is not None and reader is not threading.current_thread():
            reader.join(timeout=1.0)
        self._callback = None
        self._event_callback = None
        if process is not None and process.stdout is not None:
            process.stdout.close()
        self._status = InputPumpStatus(self.available, False, None if self.available else "not_windows")
