"""Isolated UIA worker with bounded structure and optional rich content."""

from __future__ import annotations

import argparse
import json
import time
import hashlib
import sys
import ctypes
from collections import Counter
from typing import Any


def _get(control: Any, name: str, default: Any = None) -> Any:
    try:
        return getattr(control, name)
    except Exception:
        return default


def content_fields(control: Any, max_chars: int) -> dict[str, Any]:
    # Unknown password state must not be interpreted as permission to read.
    password = _get(control, "IsPassword")
    result = {"password": password is None or bool(password), "focused": bool(_get(control, "HasKeyboardFocus", False)),
              "enabled": bool(_get(control, "IsEnabled", False))}
    if password is None or bool(password):
        result["text_status"] = "password" if password else "password_state_unknown"
        return result
    for name, key in (("Name", "name"), ("AutomationId", "automation_id"), ("ClassName", "class_name")):
        value = str(_get(control, name, "") or "")
        limit = min(max_chars, 1024)
        result[key] = value[:limit]
        if len(value) > limit:
            result["truncated"] = True
    try:
        runtime_id = control.GetRuntimeId()
    except Exception:
        runtime_id = (_get(control, "NativeWindowHandle", 0), result.get("automation_id"), result.get("class_name"))
    result["control_id"] = hashlib.sha256(repr(runtime_id).encode()).hexdigest()[:24]
    try:
        pattern = control.GetValuePattern()
        if pattern is not None:
            value = str(pattern.Value or "")
            result.update(value=value[:max_chars], truncated=bool(result.get("truncated")) or len(value) > max_chars,
                          read_only=bool(pattern.IsReadOnly), text_status="ok")
    except Exception:
        pass
    try:
        pattern = control.GetTextPattern()
        if pattern is not None:
            text = str(pattern.DocumentRange.GetText(max_chars + 1) or "")
            if text[:max_chars] != result.get("value"):
                result["text"] = text[:max_chars]
            result["truncated"] = bool(result.get("truncated")) or len(text) > max_chars
            result.setdefault("text_status", "ok")
    except Exception:
        pass
    result.setdefault("text_status", "unsupported")
    return result


def inspect_tree(control: Any, max_depth: int, max_nodes: int, *, include_content: bool = False,
                 max_text_chars: int = 12000, focused: Any = None) -> dict[str, Any]:
    roles: Counter[str] = Counter()
    nodes = []
    deepest = 0
    truncated = False
    limits = set()
    budget = 42000
    focused_control = {}
    if include_content and focused is not None:
        focused_control = content_fields(focused, min(max_text_chars, 4000))
        focused_control["role"] = str(_get(focused, "ControlTypeName", "Unknown"))[:80]
        budget -= len(json.dumps(focused_control, ensure_ascii=False).encode("utf-8"))
        if focused_control.get("truncated"):
            limits.add("uia_text_limit")
    stack = [(control, 0, None)]
    while stack and len(nodes) < max_nodes:
        current, depth, parent = stack.pop()
        role = str(_get(current, "ControlTypeName", "Unreadable") or "Unknown")[:80]
        offscreen = bool(_get(current, "IsOffscreen", False))
        node = {"role": role, "depth": depth, "offscreen": offscreen, "bounds": {}}
        try:
            rect = current.BoundingRectangle
            node["bounds"] = {"x": int(rect.left), "y": int(rect.top),
                              "width": max(0, int(rect.right - rect.left)),
                              "height": max(0, int(rect.bottom - rect.top))}
        except Exception:
            pass
        if include_content and not offscreen:
            node.update(content_fields(current, min(max_text_chars, max(0, budget // 12))))
            node["parent_id"] = parent
        size = len(json.dumps(node, ensure_ascii=False).encode("utf-8"))
        if size > budget:
            truncated = True
            limits.add("uia_byte_limit")
            break
        budget -= size
        nodes.append(node)
        truncated |= bool(node.get("truncated"))
        if node.get("truncated"):
            limits.add("uia_text_limit")
        roles[role] += 1
        deepest = max(deepest, depth)
        if offscreen or node.get("password"):
            continue
        try:
            children = current.GetChildren() or []
        except Exception:
            continue
        if depth >= max_depth:
            truncated |= bool(children)
            if children:
                limits.add("uia_depth_limit")
            continue
        candidates = list(children[:max_nodes * 2])
        if len(children) > len(candidates):
            limits.add("uia_node_limit")
        if include_content:
            priority = {"DocumentControl": 0, "EditControl": 0, "PaneControl": 1, "GroupControl": 1}
            candidates.sort(key=lambda child: priority.get(_get(child, "ControlTypeName", ""), 2))
        for child in reversed(candidates):
            stack.append((child, depth + 1, node.get("control_id")))
        if len(stack) > max_nodes * 2:
            stack = stack[-max_nodes * 2:]
            limits.add("uia_node_limit")
    if stack and len(nodes) >= max_nodes:
        limits.add("uia_node_limit")
    truncated = truncated or bool(limits)
    return {"status": "ok", "node_count": len(nodes), "role_counts": dict(roles),
            "max_depth": deepest, "truncated": truncated or bool(stack), "nodes": nodes,
            "focused_control": focused_control, "content_enabled": include_content,
            "missing_reasons": ["uia_budget", *sorted(limits)] if truncated or stack else []}


def inspect(hwnd: int, max_depth: int, max_nodes: int, *, include_content: bool = False,
            max_text_chars: int = 12000) -> dict[str, Any]:
    started = time.monotonic()
    try:
        import uiautomation as auto
        control = auto.ControlFromHandle(hwnd)
        if control is None:
            return {"status": "unavailable", "missing_reasons": ["no_control"]}
        focused = None
        if include_content:
            try:
                user = ctypes.windll.user32
                user.GetForegroundWindow.restype = ctypes.c_void_p
                if int(user.GetForegroundWindow() or 0) == hwnd:
                    candidate = auto.GetFocusedControl()
                    if candidate is not None and candidate.ProcessId == control.ProcessId:
                        focused = candidate
            except Exception:
                pass
        result = inspect_tree(control, max_depth, max_nodes, include_content=include_content,
                              max_text_chars=max_text_chars, focused=focused)
        result["duration_ms"] = int((time.monotonic() - started) * 1000)
        return result
    except Exception as error:
        return {
            "status": "unavailable",
            "duration_ms": int((time.monotonic() - started) * 1000),
            "missing_reasons": ["uia_error", type(error).__name__],
        }


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--hwnd", type=int, required=True)
    parser.add_argument("--max-depth", type=int)
    parser.add_argument("--max-nodes", type=int, default=180)
    parser.add_argument("--content", action="store_true")
    parser.add_argument("--max-text-chars", type=int, default=12000)
    args = parser.parse_args()
    if args.max_depth is None:
        args.max_depth = 16 if args.content else 6
    print(json.dumps(inspect(args.hwnd, args.max_depth, args.max_nodes, include_content=args.content,
                            max_text_chars=max(256, min(24000, args.max_text_chars))), ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
