"""Full committed-text deltas with a bounded number of control baselines."""

from collections import OrderedDict
from typing import Any, Mapping, Optional


class InputTextTracker:
    def __init__(self, capacity: int = 128) -> None:
        self.capacity = capacity
        self._values: OrderedDict[tuple[str, str], tuple[str, bool, str]] = OrderedDict()

    def clear(self) -> None:
        self._values.clear()

    def observe(self, window_id: str, control: Mapping[str, Any], *, input_trigger: bool) -> Optional[dict]:
        if control.get("password") or control.get("read_only") or control.get("text_status") != "ok":
            return None
        if control.get("scope_excluded") or control.get("focused") is False or control.get("enabled") is False:
            return None
        if control.get("role") not in {"EditControl", "DocumentControl", "ComboBoxControl"}:
            return None
        key = (window_id, str(control.get("control_id", "")))
        if not key[1]:
            return None
        # Some Chromium editors advertise an empty ValuePattern while their
        # TextPattern contains the complete document. Preserve both raw fields;
        # compare the full document text when that representation is available.
        text_source = "text" if control.get("text") is not None else "value"
        if control.get(text_source) is None:
            return None
        text = str(control[text_source])
        previous = self._values.pop(key, None)
        complete = not bool(control.get("truncated"))
        self._values[key] = (text, complete, text_source)
        while len(self._values) > self.capacity:
            self._values.popitem(last=False)
        if not input_trigger or (previous is not None and previous[0] == text and previous[2] == text_source):
            return None
        if previous is None or not previous[1] or not complete or previous[2] != text_source:
            return {"operation": "snapshot", "text": text, "inserted_text": None,
                    "control_id": key[1], "baseline_known": False,
                    "truncated": bool(control.get("truncated")), "provenance": "focused_uia",
                    "text_source": text_source}
        previous_text = previous[0]
        prefix = 0
        while prefix < min(len(previous_text), len(text)) and previous_text[prefix] == text[prefix]:
            prefix += 1
        suffix = 0
        while suffix < min(len(previous_text), len(text)) - prefix and previous_text[-suffix - 1] == text[-suffix - 1]:
            suffix += 1
        inserted = text[prefix:len(text) - suffix if suffix else len(text)]
        removed = len(previous_text) - prefix - suffix
        return {"operation": "replace" if inserted and removed else "insert" if inserted else "delete",
                "inserted_text": inserted, "removed_char_count": removed, "offset": prefix,
                "control_id": key[1], "baseline_known": True,
                "truncated": bool(control.get("truncated")), "provenance": "focused_uia",
                "text_source": text_source}
