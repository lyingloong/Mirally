"""Isolated UIA worker preserving complete observations within a privacy scope.

The parent process owns the deadline. Legacy size arguments are accepted for
caller compatibility, but never turn a successful raw observation into a prefix.
"""

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


class _UIAReadError(Exception):
    """A protocol reason, never provider text or partially read content."""


def _property(control: Any, name: str, default: Any = None) -> Any:
    try:
        value = getattr(control, name)
        return default if value is None else value
    except Exception as error:
        raise _UIAReadError("uia_property_error") from error


def _pattern(control: Any, name: str) -> Any:
    try:
        method = getattr(control, name, None)
        pattern = method() if method is not None else None
        if pattern is not None:
            return pattern
        # The installed SDK's GetPattern catches every COMError and returns
        # None. Its availability property getter does not swallow that error,
        # allowing an advertised pattern's failed read to remain diagnosable.
        # Synthetic/other providers without this optional API keep the normal
        # None-means-unsupported convention.
        getter = getattr(control, "GetPropertyValue", None)
        generic = getattr(control, "GetPattern", None)
        if getter is None or (method is None and generic is None):
            return None
        try:
            from uiautomation import PatternId, PropertyId

            property_id = {"GetValuePattern": PropertyId.IsValuePatternAvailableProperty,
                           "GetTextPattern": PropertyId.IsTextPatternAvailableProperty}[name]
            available = getter(property_id)
            if not isinstance(available, (bool, int)) or available not in (False, True):
                raise _UIAReadError("uia_pattern_availability_error")
        except _UIAReadError:
            raise
        except Exception as error:
            raise _UIAReadError("uia_pattern_availability_error") from error
        if available:
            # SDK convenience methods depend on the control class, whereas
            # provider capabilities can also advertise patterns on a Pane or
            # Button. Honor those capabilities through the generic API.
            if method is None:
                pattern_id = {"GetValuePattern": PatternId.ValuePattern,
                              "GetTextPattern": PatternId.TextPattern}[name]
                pattern = generic(pattern_id)
                if pattern is not None:
                    return pattern
            raise _UIAReadError("uia_pattern_error")
        return None
    except _UIAReadError:
        raise
    except Exception as error:
        raise _UIAReadError("uia_pattern_error") from error


def _identity(control: Any) -> tuple:
    try:
        runtime_id = control.GetRuntimeId()
        if runtime_id:
            return ("runtime", *runtime_id)
    except Exception:
        pass
    return ("object", id(control))


def _scope(include_content: bool) -> dict[str, Any]:
    return {"root": "target_window", "content_enabled": include_content,
            "excluded_subtrees": ["offscreen", "password", "password_state_unknown", "visibility_unknown"],
            "excluded_nodes": "metadata_only", "focused_control": "within_included_subtree"}


def content_fields(control: Any, max_chars: int) -> dict[str, Any]:
    # max_chars is a legacy argument; complete raw property values are retained.
    # Unknown password state must not be interpreted as permission to read.
    password = _get(control, "IsPassword")
    result = {"password": password is None or bool(password), "focused": bool(_get(control, "HasKeyboardFocus", False)),
              "enabled": bool(_get(control, "IsEnabled", False))}
    if password is None or bool(password):
        result["text_status"] = "password" if password else "password_state_unknown"
        return result
    offscreen = _get(control, "IsOffscreen")
    if offscreen is None or bool(offscreen):
        result["text_status"] = "offscreen" if offscreen else "visibility_unknown"
        result["scope_excluded"] = result["text_status"]
        return result
    for name, key in (("Name", "name"), ("AutomationId", "automation_id"), ("ClassName", "class_name")):
        result[key] = str(_property(control, name, "") or "")
    try:
        runtime_id = control.GetRuntimeId()
    except Exception:
        runtime_id = (_get(control, "NativeWindowHandle", 0), result.get("automation_id"), result.get("class_name"))
    result["control_id"] = hashlib.sha256(repr(runtime_id).encode()).hexdigest()[:24]
    pattern = _pattern(control, "GetValuePattern")
    if pattern is not None:
        try:
            result.update(value=str(pattern.Value or ""), read_only=bool(pattern.IsReadOnly), text_status="ok")
        except Exception as error:
            raise _UIAReadError("uia_value_error") from error
    pattern = _pattern(control, "GetTextPattern")
    if pattern is not None:
        try:
            text = str(pattern.DocumentRange.GetText(-1) or "")
        except Exception as error:
            raise _UIAReadError("uia_text_error") from error
        result["text"] = text
        result.setdefault("text_status", "ok")
    result.setdefault("text_status", "unsupported")
    return result


def inspect_tree(control: Any, max_depth: int, max_nodes: int, *, include_content: bool = False,
                 max_text_chars: int = 12000, focused: Any = None) -> dict[str, Any]:
    """Read the complete declared scope or return a diagnostic without a prefix.

    Excluded roots retain structure metadata but neither their content nor their
    descendants are read. Finite provider trees are walked iteratively, so depth
    is not limited by Python recursion; a cyclic provider tree fails explicitly.
    """
    roles: Counter[str] = Counter()
    nodes = []
    deepest = 0
    focused_control = {}
    focus_status = "content_disabled" if not include_content else "focus_not_observed"
    try:
        focused_identity = _identity(focused) if include_content and focused is not None else None
        matched_focus = None
        focused_candidates = {}
        stack = [(control, 0, None, False)]
        active = set()
        while stack:
            current, depth, parent, leaving = stack.pop()
            if leaving:
                active.remove(current)
                continue
            identity = _identity(current)
            if identity in active:
                raise _UIAReadError("uia_tree_cycle")
            active.add(identity)
            stack.append((identity, depth, parent, True))
            role = str(_property(current, "ControlTypeName", "Unknown") or "Unknown")
            visible = _get(current, "IsOffscreen")
            password = _get(current, "IsPassword")
            excluded = ("visibility_unknown" if visible is None else "offscreen" if bool(visible)
                        else "password_state_unknown" if password is None else "password" if bool(password) else None)
            node = {"role": role, "depth": depth, "offscreen": bool(visible), "bounds": {}}
            rect = _property(current, "BoundingRectangle")
            if rect is not None:
                try:
                    node["bounds"] = {"x": int(rect.left), "y": int(rect.top),
                                      "width": max(0, int(rect.right - rect.left)),
                                      "height": max(0, int(rect.bottom - rect.top))}
                except Exception as error:
                    raise _UIAReadError("uia_bounds_error") from error
            if excluded:
                node["scope_excluded"] = excluded
            elif include_content:
                node.update(content_fields(current, max_text_chars))
                node["parent_id"] = parent
                if node.get("password"):
                    excluded = node.get("text_status", "password_state_unknown")
                    node["scope_excluded"] = excluded
                elif node.get("scope_excluded"):
                    excluded = node["scope_excluded"]
                if not excluded:
                    if identity == focused_identity:
                        matched_focus = node
                    # Some providers mark both the top-level window and the
                    # actual control as focused. A window flag alone cannot
                    # identify an editable control or repair an expired API
                    # candidate. Never prefer an arbitrary editable sibling.
                    if node.get("focused") is True and role != "WindowControl":
                        focused_candidates[node["control_id"]] = node
            nodes.append(node)
            roles[role] += 1
            deepest = max(deepest, depth)
            if excluded:
                continue
            try:
                children = list(current.GetChildren() or ())
            except Exception as error:
                raise _UIAReadError("uia_children_error") from error
            for child in reversed(children):
                stack.append((child, depth + 1, node.get("control_id"), False))
        selected = None
        if include_content:
            if matched_focus is not None and matched_focus.get("focused") is True:
                selected, focus_status = matched_focus, "api_verified"
            elif focused_identity is not None and matched_focus is None:
                # A globally focused element can belong to another window or
                # an excluded subtree. Do not substitute a target-tree node
                # for that element or read outside the declared privacy scope.
                focus_status = "focus_outside_scope"
            elif len(focused_candidates) == 1:
                selected = next(iter(focused_candidates.values()))
                focus_status = "tree_verified"
            elif len(focused_candidates) > 1:
                focus_status = "focus_ambiguous"
            elif matched_focus is not None:
                focus_status = "focus_stale"
            if selected is not None:
                focused_control = {key: value for key, value in selected.items()
                                   if key not in {"depth", "bounds", "offscreen", "parent_id"}}
    except _UIAReadError as error:
        return {"status": "unavailable", "truncated": False, "nodes": [], "node_count": 0,
                "focused_control": {}, "role_counts": {}, "max_depth": 0,
                "focus_status": "capture_unavailable",
                "content_enabled": include_content, "capture_scope": _scope(include_content),
                "missing_reasons": [str(error)]}
    return {"status": "ok", "node_count": len(nodes), "role_counts": dict(roles),
            "max_depth": deepest, "truncated": False, "nodes": nodes,
            "focused_control": focused_control, "content_enabled": include_content,
            "focus_status": focus_status,
            "capture_scope": _scope(include_content), "missing_reasons": []}


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
                            max_text_chars=args.max_text_chars), ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
