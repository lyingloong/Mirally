"""Safe collector-session coverage metadata for bounded event queries."""
from __future__ import annotations

from typing import Any, Mapping

from .models import ContextQuery


GAP_COUNTS = ("unattributed_input_batches", "unattributed_clipboard_events", "stale_jobs",
              "stale_results", "evidence_errors", "low_disk_skips", "black_frame_skips")
SOURCE_NAMES = ("foreground", "window_titles", "browser_urls", "input_metadata", "uia_structure",
                "uia_content", "input_text", "visual", "ocr", "clipboard", "clipboard_content")


def _code(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str) and 0 < len(value) <= 80 and all(
            char.isascii() and (char.isalnum() or char == "_") for char in value):
        return value
    return "unspecified"


def _timestamp(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def collection_coverage(diagnostics: Any, query: ContextQuery) -> dict[str, Any]:
    """Describe recorded session totals without assigning them to a window."""
    result = {"status": "unknown", "diagnostics_available": False,
              "scope": "last_recorded_collector_session", "query_specific": False,
              "completeness": "unconfirmed"}
    if not isinstance(diagnostics, Mapping) or not diagnostics:
        return {**result, "reason": "collector_diagnostics_missing"}
    counts = diagnostics.get("known_gap_counts")
    counts = counts if isinstance(counts, Mapping) else {}
    counts = {name: counts[name] for name in GAP_COUNTS if isinstance(counts.get(name), int)
              and not isinstance(counts[name], bool) and counts[name] >= 0}
    sources = diagnostics.get("sources")
    sources = sources if isinstance(sources, Mapping) else {}
    safe_sources = {}
    for name in SOURCE_NAMES:
        value = sources.get(name)
        if not isinstance(value, Mapping):
            continue
        safe_sources[name] = {"enabled": value.get("enabled") is True,
                              "available": value.get("available") is True,
                              "reason": _code(value.get("reason"))}
    started = _timestamp(diagnostics.get("started_at_utc_ms"))
    stopped = _timestamp(diagnostics.get("stopped_at_utc_ms"))
    sampled = _timestamp(diagnostics.get("updated_at_utc_ms"))
    end = stopped if stopped is not None else sampled
    overlaps = None if started is None or end is None else (
        (query.start_utc_ms is None or query.start_utc_ms <= end)
        and (query.end_utc_ms is None or query.end_utc_ms > started))
    failure = any(value["enabled"] and (not value["available"] or value["reason"])
                  for value in safe_sources.values())
    pauses = diagnostics.get("pause_intervals")
    pause_count = len(pauses) if isinstance(pauses, (list, tuple)) else 0
    status = "known_gaps" if any(counts.values()) or failure or pause_count else "no_known_gaps"
    if not counts:
        status = "unknown"
    return {**result, "status": status, "diagnostics_available": True,
            "source_epoch": diagnostics.get("source_epoch"),
            "collector_status": _code(diagnostics.get("status")),
            "started_at_utc_ms": started, "stopped_at_utc_ms": stopped,
            "updated_at_utc_ms": sampled, "overlaps_query_time": overlaps,
            "known_gap_counts": counts, "sources": safe_sources, "pause_interval_count": pause_count,
            "limitations": ["session_totals_not_window_or_query_specific",
                            "zero_known_gaps_does_not_prove_complete_capture"]}
