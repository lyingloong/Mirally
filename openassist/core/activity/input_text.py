"""Bounded committed-text deltas, including IME commits and pasted text."""

from collections import OrderedDict
from typing import Any, Mapping, Optional


class InputTextTracker:
    def __init__(self, capacity: int = 128) -> None:
        self.capacity = capacity
        self._values: OrderedDict[tuple[str, str], str] = OrderedDict()

    def clear(self) -> None:
        self._values.clear()

    def observe(self, window_id: str, control: Mapping[str, Any], *, input_trigger: bool) -> Optional[dict]:
        if control.get("password") or control.get("read_only") or control.get("text_status") != "ok":
            return None
        if control.get("role") not in {"EditControl", "DocumentControl", "ComboBoxControl"}:
            return None
        key = (window_id, str(control.get("control_id", "")))
        if not key[1]:
            return None
        text = str(control.get("value", control.get("text", "")))
        previous = self._values.pop(key, None)
        self._values[key] = text
        while len(self._values) > self.capacity:
            self._values.popitem(last=False)
        if not input_trigger or previous == text:
            return None
        if previous is None:
            return {"operation": "snapshot", "text": text, "inserted_text": None,
                    "control_id": key[1], "baseline_known": False,
                    "truncated": bool(control.get("truncated")), "provenance": "focused_uia"}
        prefix = 0
        while prefix < min(len(previous), len(text)) and previous[prefix] == text[prefix]:
            prefix += 1
        suffix = 0
        while suffix < min(len(previous), len(text)) - prefix and previous[-suffix - 1] == text[-suffix - 1]:
            suffix += 1
        inserted = text[prefix:len(text) - suffix if suffix else len(text)]
        removed = len(previous) - prefix - suffix
        return {"operation": "replace" if inserted and removed else "insert" if inserted else "delete",
                "inserted_text": inserted, "removed_char_count": removed, "offset": prefix,
                "control_id": key[1], "baseline_known": True,
                "truncated": bool(control.get("truncated")), "provenance": "focused_uia"}
