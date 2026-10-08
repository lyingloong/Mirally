"""Bounded browser address-bar extraction through an isolated UIA worker."""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlsplit


_BROWSER_NAMES = {
    "chrome.exe",
    "msedge.exe",
    "brave.exe",
    "firefox.exe",
    "opera.exe",
    "vivaldi.exe",
}


@dataclass(frozen=True)
class BrowserURLResult:
    status: str
    url: Optional[str] = None
    duration_ms: int = 0
    missing_reasons: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "url": self.url,
            "duration_ms": self.duration_ms,
            "missing_reasons": list(self.missing_reasons),
        }


def normalize_browser_url(value: object) -> Optional[str]:
    """Return a bounded URL-like address-bar value, excluding search text."""

    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not candidate or len(candidate) > 4096 or any(char.isspace() or ord(char) < 32 for char in candidate):
        return None
    lowered = candidate.lower()
    schemes = (
        "http://",
        "https://",
        "file://",
        "about:",
        "edge://",
        "chrome://",
        "brave://",
        "opera://",
        "vivaldi://",
    )
    try:
        if lowered.startswith(schemes):
            parsed = urlsplit(candidate)
            if parsed.scheme.lower() in {"http", "https"}:
                if not parsed.hostname:
                    return None
                parsed.port
            return candidate
        # Chromium may omit the scheme in its UIA address-bar value. Preserve
        # that exact value instead of inventing an HTTPS navigation fact.
        parsed = urlsplit("//" + candidate)
        host = parsed.hostname or ""
        parsed.port
        if host == "localhost" or ("." in host and not host.startswith(".") and not host.endswith(".")):
            if parsed.username is None and parsed.password is None:
                return candidate
    except ValueError:
        return None
    return None


class WindowsBrowserURLSource:
    """Read a browser's visible address bar without activating the window."""

    def __init__(self, *, python_executable: Optional[str] = None) -> None:
        self.available = sys.platform == "win32"
        self.python_executable = python_executable or sys.executable

    @staticmethod
    def supports_app(app_id: Optional[str]) -> bool:
        return (app_id or "").lower() in _BROWSER_NAMES

    def read(
        self,
        hwnd: int,
        *,
        app_id: Optional[str],
        timeout_ms: int = 1200,
        max_depth: int = 8,
        max_nodes: int = 300,
    ) -> BrowserURLResult:
        if not self.available:
            return BrowserURLResult("unavailable", missing_reasons=("not_windows",))
        if not self.supports_app(app_id):
            return BrowserURLResult("not_browser", missing_reasons=("unsupported_app",))
        if int(hwnd) <= 0:
            return BrowserURLResult("unavailable", missing_reasons=("invalid_hwnd",))
        command = [
            self.python_executable,
            "-m",
            "openassist.platform.activity.browser_url_worker",
            "--hwnd",
            str(int(hwnd)),
            "--max-depth",
            str(max(1, min(12, int(max_depth)))),
            "--max-nodes",
            str(max(1, min(2_000, int(max_nodes)))),
        ]
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            process = subprocess.Popen(
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
            return BrowserURLResult("unavailable", missing_reasons=("worker_start", type(error).__name__))
        try:
            output, _ = process.communicate(timeout=max(0.1, timeout_ms / 1000.0))
        except subprocess.TimeoutExpired:
            process.kill()
            try:
                process.communicate(timeout=0.5)
            except (OSError, subprocess.TimeoutExpired):
                pass
            return BrowserURLResult("timeout", missing_reasons=("worker_deadline",))
        if process.returncode != 0:
            return BrowserURLResult("unavailable", missing_reasons=("worker_exit",))
        try:
            value = json.loads(output or "{}")
            if not isinstance(value, dict):
                raise ValueError("worker response must be a mapping")
            status = str(value.get("status", "unavailable"))
            url = normalize_browser_url(value.get("url"))
            reasons = tuple(str(item) for item in value.get("missing_reasons", ()))
            duration_ms = max(0, int(value.get("duration_ms", 0)))
        except (TypeError, ValueError):
            return BrowserURLResult("unavailable", missing_reasons=("invalid_worker_response",))
        if status == "ok" and url is None:
            return BrowserURLResult("not_found", missing_reasons=reasons or ("invalid_url",))
        return BrowserURLResult(
            status,
            url=url,
            duration_ms=duration_ms,
            missing_reasons=reasons,
        )
