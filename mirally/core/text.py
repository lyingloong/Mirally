"""Text normalization shared by persistence, execution, and presentation."""

from __future__ import annotations

import re


def message_text(value: object, limit: int | None = 12000) -> str:
    """Preserve paragraphs and redact recognizable credentials, not a DLP system."""
    text = str(value or "").strip()
    text = re.sub(r"(?i)\b(?:sk-|Bearer\s+)[\w.\-]{8,}", "[已隐藏凭据]", text)
    text = re.sub(r"(?i)(api[_ -]?key|password|token|secret)\s*[:=]\s*[^\s,;]+", r"\1=[已隐藏]", text)
    return text if limit is None or len(text) <= limit else text[:limit - 1] + "…"


def public_text(value: object, limit: int = 240) -> str:
    return message_text(" ".join(str(value or "").split()), limit)
