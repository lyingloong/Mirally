"""Process-isolated Windows native OCR provider."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple


def _normalized_worker_reasons(status: str, data: dict, returncode: int) -> Tuple[str, ...]:
    reasons = tuple(str(value) for value in (data.get("missing_reasons") or ()))
    if status == "error" and not reasons:
        # A worker can return a valid JSON envelope without a useful reason.
        # Keep the persisted observation diagnosable without copying stderr or
        # image/OCR content into the activity database.
        reasons = ("worker_exit" if returncode else "worker_error",)
    return reasons


@dataclass(frozen=True)
class OCRResult:
    status: str
    text: str = ""
    lines: Tuple[dict, ...] = ()
    engine: str = "windows.native"
    language: Optional[str] = None
    duration_ms: Optional[float] = None
    missing_reasons: Tuple[str, ...] = ()

    def as_dict(self) -> dict:
        return {
            "status": self.status,
            "text": self.text,
            "lines": [dict(line) for line in self.lines],
            "engine": self.engine,
            "language": self.language,
            "duration_ms": self.duration_ms,
            "missing_reasons": list(self.missing_reasons),
        }

    def bounded(self, max_bytes: int = 48 * 1024, max_text_chars: int = 24_000) -> "OCRResult":
        """Explicitly limit a processing/query view, never the raw capture.

        The caller chooses this lossy view; recognize() always returns the full
        worker result, including the complete text, line and word trees.
        """

        text = self.text[:max_text_chars]
        lines = tuple(self.lines)
        reasons = list(self.missing_reasons)
        was_limited = len(self.text) > len(text)
        while True:
            candidate = {
                "status": self.status, "text": text,
                "lines": [dict(line) for line in lines], "engine": self.engine,
                "language": self.language, "duration_ms": self.duration_ms,
                "missing_reasons": reasons,
            }
            import json
            if was_limited and "ocr_result_limit" not in reasons:
                reasons.append("ocr_result_limit")
                continue
            if len(json.dumps(candidate, ensure_ascii=False).encode("utf-8")) <= max_bytes:
                return OCRResult(self.status, text, lines, self.engine, self.language,
                                 self.duration_ms, tuple(reasons))
            if lines:
                lines = lines[:max(0, len(lines) // 2)]
                was_limited = True
                continue
            if text:
                text = text[:max(0, len(text) // 2)]
                was_limited = True
                continue
            if "ocr_result_limit" not in reasons:
                reasons.append("ocr_result_limit")
                continue
            return OCRResult(self.status, "", (), self.engine, self.language,
                             self.duration_ms, tuple(reasons))


class WindowsNativeOCRSource:
    """Use Windows.Media.Ocr in a short-lived Python worker.

    WinRT calls are kept out of the activity process so a missing OCR language
    pack or a projection/runtime fault is represented as an unavailable OCR
    result instead of blocking the foreground/input collectors.
    """

    def __init__(self, python_executable: Optional[str] = None) -> None:
        self.python_executable = python_executable or sys.executable
        self.available = sys.platform == "win32" and self._projection_available()

    @staticmethod
    def _projection_available() -> bool:
        try:
            from winrt.windows.media.ocr import OcrEngine  # noqa: F401
            from winrt.windows.graphics.imaging import BitmapDecoder  # noqa: F401
            from winrt.windows.storage.streams import DataWriter  # noqa: F401
        except Exception:
            return False
        return True

    def recognize(
        self,
        image_path: Path,
        *,
        languages: Tuple[str, ...] = (),
        timeout_ms: int = 2_500,
        max_dimension: int = 1_920,
    ) -> OCRResult:
        if sys.platform != "win32":
            return OCRResult("unavailable", missing_reasons=("not_windows",))
        if not self.available:
            return OCRResult("unavailable", missing_reasons=("winrt_ocr_unavailable",))
        image_path = Path(image_path)
        if not image_path.is_file():
            return OCRResult("unavailable", missing_reasons=("image_missing",))
        command = [
            self.python_executable,
            "-m",
            "openassist.platform.activity.ocr_worker",
            "--image",
            str(image_path),
            "--max-dimension",
            str(max(320, min(8_192, int(max_dimension)))),
        ]
        for language in languages:
            command.extend(("--language", str(language)))
        started = time.monotonic()
        startupinfo = None
        creationflags = 0
        if sys.platform == "win32":
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = 0
        try:
            completed = subprocess.run(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=max(0.1, float(timeout_ms) / 1000.0),
                check=False,
                creationflags=creationflags,
                startupinfo=startupinfo,
                text=True,
                encoding="utf-8",
                errors="replace",
                env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            )
        except subprocess.TimeoutExpired:
            return OCRResult(
                "timeout",
                duration_ms=(time.monotonic() - started) * 1000,
                missing_reasons=("worker_deadline",),
            )
        except OSError as error:
            return OCRResult(
                "error",
                duration_ms=(time.monotonic() - started) * 1000,
                missing_reasons=("spawn_error", type(error).__name__),
            )
        try:
            data = json.loads((completed.stdout or "").strip() or "{}")
        except ValueError:
            return OCRResult(
                "error",
                duration_ms=(time.monotonic() - started) * 1000,
                missing_reasons=("invalid_worker_output",),
            )
        if not isinstance(data, dict):
            return OCRResult("error", missing_reasons=("invalid_worker_output",))
        if completed.returncode:
            reason = "worker_output_encoding" if "UnicodeEncodeError" in (completed.stderr or "") else "worker_exit"
            return OCRResult("error", duration_ms=(time.monotonic() - started) * 1000,
                             missing_reasons=(reason,))
        status = str(data.get("status") or "error")
        reasons = _normalized_worker_reasons(status, data, completed.returncode)
        return OCRResult(
            status,
            text=str(data.get("text") or ""),
            lines=tuple(data.get("lines") or ()),
            engine=str(data.get("engine") or "windows.native"),
            language=data.get("language"),
            duration_ms=float(data.get("duration_ms", (time.monotonic() - started) * 1000)),
            missing_reasons=reasons,
        )
