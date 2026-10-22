"""Complete, policy-filtered projection evidence for bounded model requests.

This module reads only the knowledge projection. It never loads raw assets,
starts collection, or calls a model. Wire sizes use compact UTF-8 JSON arrays.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any


def _unique(values):
    return list(dict.fromkeys(value for value in values if value is not None))


def _wire(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def _operations(structured: Mapping[str, Any]) -> dict[str, Any]:
    activity = structured.get("activity") or {}
    if not isinstance(activity, Mapping):
        return {}
    result = {key: activity[key] for key in ("action", "summary", "result", "actor", "actor_basis", "unknowns")
              if key in activity}
    change = activity.get("change") or {}
    if isinstance(change, Mapping):
        result["change"] = {key: change[key] for key in (
            "operation", "removed_char_count", "inserted_char_count", "offset", "baseline_known",
            "status", "provenance", "keyboard_count", "click_count", "scroll_count", "quiet_ms",
            "sequence", "truncated", "capture_status", "ocr_status", "uia_status", "stage", "reason")
            if key in change and isinstance(change[key], (str, int, float, bool, type(None)))}
    return result


def build_evidence(organizer: Any, states: list[dict]) -> list[dict]:
    """Read all window item evidence in pages of at most 200 item IDs.

    Duplicate captured text is coalesced across monitor/desktop views, retaining
    every source ID, scope, context and the complete observed time range.
    Missing windows/items remain explicit evidence gaps instead of disappearing.
    """
    knowledge = organizer.knowledge
    item_ids = _unique(value for state in states for value in state.get("item_ids", ()))
    state_scopes = {value: state.get("scope", "desktop") for state in states
                    for value in state.get("item_ids", ())}
    rows: list[dict] = []
    for state in states:
        if not state.get("item_ids"):
            rows.append({"text": "", "kind": "coverage_gap", "scopes": [state.get("scope", "desktop")],
                         "source_observation_ids": list(state.get("source_observation_ids", ())),
                         "missing_reasons": ["window_items_unavailable"], "window_id": state.get("id")})
    for offset in range(0, len(item_ids), 200):
        page = item_ids[offset:offset + 200]
        items = {value: knowledge.get_item(value) for value in page}
        evidence = knowledge.get_evidence_for_items(page)
        evidence_by_id = {row.evidence_id: row for row in evidence}
        links = knowledge.evidence_ids_for_items(page)
        for item_id in page:
            item = items[item_id]
            if item is None:
                rows.append({"text": "", "kind": "coverage_gap", "scopes": [state_scopes[item_id]],
                             "source_observation_ids": [], "missing_reasons": ["projection_item_unavailable"],
                             "item_id": item_id})
                continue
            item_evidence = [evidence_by_id[value] for value in links.get(item_id, ()) if value in evidence_by_id]
            for captured in item_evidence or [None]:
                structured = captured.structured if captured is not None else item.structured
                source = item.structured.get("source") or {}
                scope = ("monitor:" + str(source.get("monitor_id", "desktop"))
                         if source.get("scope") in {"monitor", "monitors"} else state_scopes[item_id])
                refs = _unique(list(item.source_observation_ids)
                               + list(captured.source_observation_ids if captured is not None else ())
                               + list(item.structured.get("context_observation_ids") or ())
                               + list(source.get("linked_observation_ids") or ()))
                text = captured.text if captured is not None and captured.text else item.text_summary
                rows.append({"text": text, "kind": item.kind, "scopes": [scope],
                             "app": item.app_id, "title": item.title, "url": item.url,
                             "document_ref": item.document_ref,
                             "observed_start_utc_ms": item.start_utc_ms,
                             "observed_end_utc_ms": item.end_utc_ms,
                             "source_observation_ids": refs,
                             "missing_reasons": _unique(list(item.missing_reasons)
                                 + ([] if captured is not None else ["projection_evidence_unavailable"])),
                             "quality": captured.quality if captured is not None else item.quality,
                             "operations": _operations(structured)})
    merged: dict[bytes, dict] = {}
    for row in sorted(rows, key=lambda value: value.get("observed_start_utc_ms", 0)):
        # Identical text captured by different sensors retains all context.
        key = _wire([row["text"], row.get("operations", {})]) if row["text"] else _wire(row)
        context = {field: row.get(field) for field in ("kind", "app", "title", "url", "document_ref", "quality")}
        if key not in merged:
            merged[key] = {**row, "contexts": [context]}
            continue
        previous = merged[key]
        for field in ("source_observation_ids", "scopes", "missing_reasons"):
            previous[field] = _unique(previous[field] + row[field])
        if context not in previous["contexts"]:
            previous["contexts"].append(context)
        previous["observed_end_utc_ms"] = max(previous.get("observed_end_utc_ms", 0), row.get("observed_end_utc_ms", 0))
    return list(merged.values())


def split_evidence(rows: list[dict], max_input_bytes: int = 24000) -> list[list[dict]]:
    """Split complete rows into JSON-array packets within the byte limit.

    Oversized rows use ``fragment_of`` (their input row index) and ``field``;
    strings retain their exact text in ordered fragments. Nonstring metadata
    uses ``encoding=json`` text fragments and can be reconstructed by joining.
    Source IDs have dedicated fragments sharing the same ``fragment_of``.
    No source text or reference is silently truncated.
    """
    if type(max_input_bytes) is not int or max_input_bytes < 256:
        raise ValueError("max_input_bytes must be an integer of at least 256")
    fragments: list[dict] = []
    for index, row in enumerate(rows):
        if len(_wire([row])) <= max_input_bytes:
            fragments.append(dict(row))
            continue
        refs = list(row.get("source_observation_ids") or ())
        context = {key: row[key] for key in ("kind", "observed_start_utc_ms", "observed_end_utc_ms") if key in row}
        # Repeat a small context on each part; the complete scopes field below
        # still covers all monitor/desktop memberships, however many exist.
        context["scopes"] = list(row.get("scopes") or ())[:1]
        reference_packet = {**context, "fragment_of": index, "source_observation_ids": []}
        for ref in refs:
            candidate = {**reference_packet, "source_observation_ids": reference_packet["source_observation_ids"] + [ref]}
            if len(_wire([candidate])) > max_input_bytes:
                if reference_packet["source_observation_ids"]:
                    fragments.append(reference_packet)
                    reference_packet = {**context, "fragment_of": index, "source_observation_ids": []}
                candidate = {**reference_packet, "source_observation_ids": [ref]}
                if len(_wire([candidate])) > max_input_bytes:
                    raise ValueError("a source observation ID exceeds max_input_bytes")
            reference_packet = candidate
        if reference_packet["source_observation_ids"]:
            fragments.append(reference_packet)
        for field, value in row.items():
            if field == "source_observation_ids":
                continue
            text = value if isinstance(value, str) else _wire(value).decode("utf-8")
            base = {**context, "fragment_of": index, "field": field, "source_observation_ids": refs[:1]}
            if not isinstance(value, str):
                base["encoding"] = "json"
            position = 0
            while position < len(text) or position == 0:
                low, high = 0, len(text) - position
                while low < high:
                    middle = (low + high + 1) // 2
                    if len(_wire([{**base, "text": text[position:position + middle]}])) <= max_input_bytes:
                        low = middle
                    else:
                        high = middle - 1
                if low == 0 and position < len(text):
                    raise ValueError("fragment metadata exceeds max_input_bytes")
                fragments.append({**base, "text": text[position:position + low]})
                position += low
                if position >= len(text):
                    break
    packets: list[list[dict]] = []
    packet: list[dict] = []
    for row in fragments:
        if len(_wire(packet + [row])) > max_input_bytes:
            if packet:
                packets.append(packet)
            packet = []
        packet.append(row)
    if packet:
        packets.append(packet)
    return packets
