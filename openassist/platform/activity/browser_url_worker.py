"""Isolated UIA worker for reading a visible browser address-bar value."""

from __future__ import annotations

import argparse
import json
import time
from typing import Any

from .browser_url import normalize_browser_url


_MARKERS = ("address", "omnibox", "urlbar", "url bar", "location bar", "网址", "地址", "url")


def _text(value: object) -> str:
    return str(value or "").strip()


def _value_from_control(control: Any) -> str:
    try:
        pattern = control.GetValuePattern()
        candidate = _text(getattr(pattern, "Value", ""))
        if normalize_browser_url(candidate):
            return candidate
    except Exception:
        pass
    for attribute in ("Value", "Name"):
        try:
            candidate = _text(getattr(control, attribute, ""))
        except Exception:
            candidate = ""
        if normalize_browser_url(candidate):
            return candidate
    return ""


def find_address_bar_url(root: Any, max_depth: int, max_nodes: int) -> str | None:
    stack = [(root, 0)]
    visited = 0
    while stack and visited < max_nodes:
        current, depth = stack.pop()
        visited += 1
        try:
            role = _text(getattr(current, "ControlTypeName", ""))
            if bool(getattr(current, "IsOffscreen", False)) or bool(getattr(current, "IsPassword", False)):
                continue
            # A document or a page's search field cannot identify the browser
            # address bar. Read only visible, named browser-chrome controls.
            if role == "DocumentControl":
                continue
            marker_text = " ".join(
                _text(getattr(current, attribute, ""))
                for attribute in ("Name", "AutomationId", "ClassName")
            ).lower()
            if role in {"EditControl", "ComboBoxControl", "CustomControl", "TextControl"} and any(
                marker in marker_text for marker in _MARKERS
            ):
                candidate = _value_from_control(current)
                if normalize_browser_url(candidate):
                    return normalize_browser_url(candidate)
        except Exception:
            pass
        if depth >= max_depth:
            continue
        try:
            children = current.GetChildren() or []
        except Exception:
            children = []
        for child in reversed(children):
            if visited + len(stack) >= max_nodes:
                break
            stack.append((child, depth + 1))
    return None


def inspect(hwnd: int, max_depth: int, max_nodes: int) -> dict[str, Any]:
    started = time.monotonic()
    if hwnd <= 0:
        return {"status": "unavailable", "missing_reasons": ["invalid_hwnd"]}
    try:
        import uiautomation as auto

        root = auto.ControlFromHandle(hwnd)
        if root is None:
            return {"status": "unavailable", "missing_reasons": ["no_control"]}
        url = find_address_bar_url(root, max_depth, max_nodes)
        return {
            "status": "ok" if url else "not_found",
            "url": url,
            "duration_ms": int((time.monotonic() - started) * 1000),
            "missing_reasons": [] if url else ["address_bar_not_found"],
        }
    except Exception as error:
        return {
            "status": "unavailable",
            "duration_ms": int((time.monotonic() - started) * 1000),
            "missing_reasons": ["uia_error", type(error).__name__],
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hwnd", type=int, required=True)
    parser.add_argument("--max-depth", type=int, default=8)
    parser.add_argument("--max-nodes", type=int, default=300)
    args = parser.parse_args()
    print(json.dumps(inspect(args.hwnd, args.max_depth, args.max_nodes), separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
