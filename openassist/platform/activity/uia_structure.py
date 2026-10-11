"""Process-isolated Windows UI Automation capture with a bounded deadline."""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional


@dataclass(frozen=True)
class UIAStructureResult:
    status: str
    node_count: int = 0
    role_counts: Mapping[str, int] = field(default_factory=dict)
    max_depth: int = 0
    truncated: bool = False
    duration_ms: int = 0
    missing_reasons: tuple[str, ...] = ()
    nodes: tuple[Mapping[str, Any], ...] = ()
    focused_control: Mapping[str, Any] = field(default_factory=dict)
    focus_status: str = "unknown"
    content_enabled: bool = False
    capture_scope: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "node_count": self.node_count,
            "role_counts": dict(self.role_counts or {}),
            "max_depth": self.max_depth,
            "truncated": self.truncated,
            "duration_ms": self.duration_ms,
            "missing_reasons": list(self.missing_reasons),
            "nodes": [dict(node) for node in self.nodes],
            "focused_control": dict(self.focused_control),
            "focus_status": self.focus_status,
            "content_enabled": self.content_enabled,
            "capture_scope": dict(self.capture_scope),
        }


class WindowsUIASource:
    """Run each tree walk in a killable process; content is explicitly requested."""

    def __init__(self, *, python_executable: Optional[str] = None) -> None:
        self.available = sys.platform == "win32"
        self.python_executable = python_executable or sys.executable

    def inspect(
        self,
        hwnd: int,
        *,
        timeout_ms: int = 1200,
        max_depth: Optional[int] = None,
        max_nodes: int = 180,
        include_content: bool = False,
        max_text_chars: int = 12000,
    ) -> UIAStructureResult:
        if not self.available:
            return UIAStructureResult("unavailable", missing_reasons=("not_windows",))
        if int(hwnd) <= 0:
            return UIAStructureResult("unavailable", missing_reasons=("invalid_hwnd",))
        if max_depth is None:
            max_depth = 16 if include_content else 6
        command = [
            self.python_executable,
            "-m",
            "openassist.platform.activity.uia_worker",
            "--hwnd",
            str(int(hwnd)),
            "--max-depth",
            str(int(max_depth)),
            "--max-nodes",
            str(int(max_nodes)),
        ]
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        if include_content:
            command.extend(["--content", "--max-text-chars", str(max_text_chars)])
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
            return UIAStructureResult("unavailable", missing_reasons=("worker_start", type(error).__name__))
        try:
            output, _ = process.communicate(timeout=max(0.1, timeout_ms / 1000.0))
        except subprocess.TimeoutExpired:
            process.kill()
            try:
                process.communicate(timeout=0.5)
            except (OSError, subprocess.TimeoutExpired):
                pass
            return UIAStructureResult("timeout", missing_reasons=("worker_deadline",))
        if process.returncode != 0:
            return UIAStructureResult("unavailable", missing_reasons=("worker_exit",))
        try:
            value = json.loads(output or "{}")
            if not isinstance(value, dict):
                raise ValueError("invalid UIA envelope")
            roles = {
                str(key): max(0, int(count))
                for key, count in (value.get("role_counts") or {}).items()
            }
            return UIAStructureResult(
                status=str(value.get("status", "unavailable")),
                node_count=max(0, int(value.get("node_count", 0))),
                role_counts=roles,
                max_depth=max(0, int(value.get("max_depth", 0))),
                truncated=bool(value.get("truncated", False)),
                duration_ms=max(0, int(value.get("duration_ms", 0))),
                missing_reasons=tuple(str(item) for item in value.get("missing_reasons", ())),
                nodes=tuple(
                    dict(node) if include_content else {
                        "role": str(node.get("role", "Unknown")),
                        "depth": max(0, int(node.get("depth", 0))),
                        "offscreen": bool(node.get("offscreen", False)),
                        "bounds": {
                            key: max(0, int(node.get("bounds", {}).get(key, 0)))
                            for key in ("width", "height")
                            if key in (node.get("bounds") or {})
                        },
                        **({"scope_excluded": str(node["scope_excluded"])} if "scope_excluded" in node else {}),
                    }
                    for node in (value.get("nodes") or ())
                    if isinstance(node, Mapping)
                ),
                focused_control=dict(value.get("focused_control") or {}) if include_content else {},
                focus_status=str(value.get("focus_status", "unknown")) if include_content else "content_disabled",
                content_enabled=include_content,
                capture_scope=dict(value.get("capture_scope") or {}),
            )
        except (TypeError, ValueError, json.JSONDecodeError):
            return UIAStructureResult("unavailable", missing_reasons=("invalid_worker_response",))
