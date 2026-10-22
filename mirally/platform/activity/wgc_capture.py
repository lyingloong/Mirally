"""Isolated HWND capture for the optional content collection layer.

The native helper is deliberately a separate process.  A graphics driver or
Windows.Graphics.Capture failure must not take down the activity writer or
foreground observer, and a timeout must have a hard process boundary.
"""

from __future__ import annotations

import hashlib
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional, Tuple

from .wgc_worker import WGCWorker, WorkerFailure


@dataclass(frozen=True)
class CaptureResult:
    status: str
    path: Optional[Path] = None
    width: int = 0
    height: int = 0
    method: str = "wgc"
    signature: Optional[Mapping[str, object]] = None
    duration_ms: Optional[float] = None
    missing_reasons: Tuple[str, ...] = ()
    runtime: Mapping[str, object] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "status": self.status,
            "width": self.width,
            "height": self.height,
            "method": self.method,
            "signature": dict(self.signature or {}),
            "duration_ms": self.duration_ms,
            "missing_reasons": list(self.missing_reasons),
            "runtime": dict(self.runtime),
        }


class WindowsWindowCaptureSource:
    """Reuse isolated WGC sessions against an explicit window or monitor.

    There is intentionally no desktop or monitor fallback.  If the helper is
    absent, the caller gets ``unavailable`` and can preserve the metadata/UIA
    observation without silently widening the capture scope.
    """

    def __init__(self, helper_path: Optional[Path] = None) -> None:
        configured = os.environ.get("MIRALLY_WGC_HELPER")
        default = Path(__file__).resolve().parents[3] / "tools" / "bin" / "desktop_wgc.exe"
        self.helper_path = Path(helper_path or configured) if (helper_path or configured) else default
        self.available = sys.platform == "win32" and bool(self.helper_path and self.helper_path.is_file())
        self._worker = WGCWorker(self.helper_path)

    def status(self) -> dict:
        reason = ("not_windows" if sys.platform != "win32" else
                  "helper_missing" if not self.helper_path.is_file() else None)
        return {"available": self.available, "reason": reason,
                "helper_path": str(self.helper_path), **self._worker.status()}

    def pause(self) -> None:
        self._worker.pause()

    def resume(self) -> None:
        self._worker.resume()

    def close(self) -> None:
        self._worker.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def capture(
        self,
        hwnd: int,
        output_path: Path,
        *,
        timeout_ms: int = 1_500,
        max_dimension: int = 1_920,
        target_type: str = "hwnd",
    ) -> CaptureResult:
        if sys.platform != "win32":
            return CaptureResult("unavailable", method="wgc", missing_reasons=("not_windows",))
        if not self.helper_path or not self.helper_path.is_file():
            return CaptureResult("unavailable", method="wgc", missing_reasons=("helper_missing",))
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            output_path.unlink()
        except FileNotFoundError:
            pass
        started = time.monotonic()
        try:
            data = self._worker.request({
                "target_type": target_type, "handle": str(int(hwnd)),
                "output": str(output_path.resolve()),
                "max_dimension": max(320, min(8_192, int(max_dimension))),
                "timeout_ms": max(100, min(30_000, int(timeout_ms))),
            }, timeout_ms)
        except WorkerFailure as error:
            output_path.unlink(missing_ok=True)
            return CaptureResult(
                "timeout" if error.reason in {"worker_deadline", "worker_busy"} else
                "cancelled" if error.reason in {"capture_paused", "capture_interrupted"} else "error",
                method="wgc",
                duration_ms=(time.monotonic() - started) * 1000,
                missing_reasons=(error.reason,), runtime=self._worker.status(),
            )
        duration_ms = float(data.get("elapsed_ms", (time.monotonic() - started) * 1000))
        if not bool(data.get("captured")):
            output_path.unlink(missing_ok=True)
            reason = str(data.get("error") or "capture_unavailable")
            return CaptureResult(
                "unavailable",
                method=str(data.get("method") or "wgc"),
                duration_ms=duration_ms,
                missing_reasons=(reason,),
                runtime={**dict(data.get("runtime") or {}), **{
                    key: data[key] for key in ("stage", "hresult") if key in data}},
            )
        if not output_path.is_file() or output_path.stat().st_size <= 0:
            return CaptureResult(
                "error",
                method="wgc",
                duration_ms=duration_ms,
                missing_reasons=("image_missing",),
            )
        size = data.get("size") or (0, 0)
        return CaptureResult(
            "ok",
            path=output_path,
            width=int(size[0]) if len(size) > 0 else 0,
            height=int(size[1]) if len(size) > 1 else 0,
            method=str(data.get("method") or "wgc"),
            signature={
                "sha256": hashlib.sha256(output_path.read_bytes()).hexdigest(),
                **{
                key: data[key]
                for key in ("dhash", "mean", "std", "black_fraction", "near_black")
                if key in data
                },
            },
            duration_ms=duration_ms,
            runtime=dict(data.get("runtime") or {}),
        )

    def capture_monitor(self, handle: int, output_path: Path, **kwargs) -> CaptureResult:
        return self.capture(handle, output_path, target_type="monitor", **kwargs)


class VisualSignatureGate:
    """Bounded per-window signature cache used before OCR work."""

    def __init__(self, max_entries: int = 128) -> None:
        self.max_entries = max(8, int(max_entries))
        self._values: dict[str, tuple[tuple[tuple[str, object], ...], float]] = {}

    def changed(self, key: str, signature: Optional[Mapping[str, object]]) -> bool:
        if not signature:
            return True
        normalized = self._normalize(signature)
        previous = self._values.get(str(key))
        if previous is not None and previous[0] == normalized:
            return False
        return True

    def commit(self, key: str, signature: Optional[Mapping[str, object]]) -> None:
        if not signature:
            return
        normalized = self._normalize(signature)
        self._values[str(key)] = (normalized, time.monotonic())
        if len(self._values) > self.max_entries:
            oldest = min(self._values.items(), key=lambda item: item[1][1])[0]
            self._values.pop(oldest, None)

    @staticmethod
    def _normalize(signature: Mapping[str, object]) -> tuple[tuple[str, object], ...]:
        # Diagnostics such as mask counts are not part of the image identity.
        if signature.get("sha256"):
            return (("sha256", signature["sha256"]),)
        diagnostic_keys = {
            "black_filter_threshold",
            "black_fraction",
            "masked_area_fraction",
            "masked_windows",
        }
        return tuple(sorted((str(k), v) for k, v in signature.items()
                            if str(k) not in diagnostic_keys))
