"""Loss-aware, deterministic projections of original observation evidence.

The readable activity fields describe observations, not inferred intent or
successful application actions. Original UIA/OCR/edit content remains
in evidence even when the searchable item summary is compressed.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping, Optional

from ..models import Observation, ObservationQuality
from .models import ActivityItem, Evidence
from .repository import deterministic_id


TEXT_SUMMARY_LIMIT = 12_000


def _copy(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False))


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _first(mapping: Mapping[str, Any], *keys: str) -> Optional[str]:
    for key in keys:
        if mapping.get(key) not in (None, ""):
            return _text(mapping[key])
    return None


def _confidence(quality: str) -> float:
    return {"complete": 1.0, "partial": 0.7, "stale": 0.45,
            "gap": 0.25, "unavailable": 0.2}.get(quality, 0.5)


def _partial(quality: str) -> str:
    return "partial" if quality == "complete" else quality


def _kind(observation: Observation, payload: Mapping[str, Any], text: str) -> str:
    if observation.kind in {"foreground_changed", "window_context"}:
        return "web" if payload.get("browser_url") else "window"
    if observation.kind == "context_snapshot":
        return "document" if payload.get("uia_status") == "ok" else "ui"
    if observation.kind in {"content_snapshot", "ocr_result"}:
        return "screen"
    if observation.kind in {"input_batch", "input_text"}:
        return "input"
    if observation.kind in {"clipboard_changed", "clipboard_operation", "clipboard_content"}:
        return "clipboard"
    if observation.kind in {"collector_diagnostic", "collection_gap", "agent_control_changed"}:
        return "system"
    return "content" if text else "event"


def _source(observation: Observation, payload: Mapping[str, Any]) -> dict[str, Any]:
    fields = (
        "observation_id", "kind", "source_id", "source_epoch", "source_seq", "ingest_seq",
        "observed_at_utc_ms", "received_at_utc_ms", "monotonic_ns", "boot_id", "clock_epoch",
        "time_quality", "session_instance_id", "identity_confidence", "actor", "actor_basis",
        "control_owner", "policy_version", "collection_level", "privacy_status", "caused_by",
        "capture_group_id", "capture_started_at", "capture_finished_at", "truncated",
        "quality", "missing_reasons",
    )
    source = {name: _copy(getattr(observation, name)) for name in fields}
    capture_scope = (observation.uia_summary or {}).get("capture_scope")
    if isinstance(capture_scope, Mapping):
        source["capture_scope"] = {key: _copy(capture_scope[key]) for key in (
            "root", "content_enabled", "excluded_subtrees", "excluded_nodes", "focused_control",
        ) if key in capture_scope}
    if observation.kind == "window_context":
        # An explicit empty resource is a new unknown, not permission to
        # reuse a URL from the page that was visible earlier.
        resource_keys = {"url": ("browser_url", "url"), "title": ("window_title", "title"),
                         "document_ref": ("document_ref", "document_path")}
        source["resource_unknown_fields"] = [field for field, keys in resource_keys.items()
                                              if any(key in payload for key in keys)
                                              and not any(payload.get(key) for key in keys)]
    # Keep source metadata separate from content so query content filters can
    # remove UIA/OCR without leaving a duplicate raw payload behind.
    visual = observation.visual_summary or {}
    for key in ("scope", "monitor_id", "monitor_bounds", "bounds", "capture_bounds",
                "trigger", "capture_status", "browser_url_status", "uia_status", "ocr_status",
                "transition", "title_available"):
        if key in payload:
            source[key] = _copy(payload[key])
        elif key in visual:
            source[key] = _copy(visual[key])
    return source


def _source_limits(observation: Observation) -> tuple[str, ...]:
    limits: list[str] = []
    if observation.truncated or observation.payload.get("truncated"):
        limits.append("source_content_truncated")
    for label, content in (("uia", observation.uia_summary), ("ocr", observation.ocr_summary)):
        if not content:
            continue
        reasons = tuple(str(value) for value in (content.get("missing_reasons") or ()))
        nodes = content.get("nodes") or ()
        focused = content.get("focused_control") or {}
        limited = bool(content.get("truncated")) or any(
            "limit" in reason or reason == "uia_budget" for reason in reasons
        )
        limited |= isinstance(focused, Mapping) and bool(focused.get("truncated"))
        limited |= isinstance(nodes, (list, tuple)) and any(
            isinstance(node, Mapping) and node.get("truncated") for node in nodes
        )
        if limited:
            limits.append("source_content_truncated")
            limits.extend(reason for reason in reasons if "limit" in reason or reason == "uia_budget")
    return tuple(dict.fromkeys(limits))


def _content(observation: Observation) -> tuple[str, dict[str, Any], Optional[str], Optional[str], Optional[str]]:
    payload = observation.payload or {}
    uia = observation.uia_summary or {}
    ocr = observation.ocr_summary or {}
    title = _first(payload, "window_title", "title")
    url = _first(payload, "browser_url", "url")
    structured: dict[str, Any] = {}
    parts: list[str] = []
    if title:
        structured["title"] = title
    if url:
        structured["url"] = url
    for key in ("inserted_text", "text", "content", "value", "text_value"):
        if payload.get(key) not in (None, ""):
            parts.append(_text(payload[key]))
    if observation.kind == "input_text":
        structured["input_text"] = {
            key: _copy(payload[key]) for key in (
                "operation", "inserted_text", "text", "removed_char_count", "offset", "baseline_known",
                "control_id", "provenance", "status", "truncated", "missing_reasons",
            ) if key in payload
        }
    if observation.kind == "input_batch":
        structured["input"] = {key: _copy(payload[key]) for key in (
            "keyboard_count", "click_count", "scroll_count", "motion", "quiet_ms",
            "started_monotonic_ns", "ended_monotonic_ns", "helper_emitted_at_utc_ms",
        ) if key in payload}
    if observation.kind in {"clipboard_changed", "clipboard_operation", "clipboard_content"}:
        structured["clipboard"] = {key: _copy(payload[key]) for key in (
            "operation", "sequence", "status", "text", "content", "truncated", "missing_reasons",
        ) if key in payload}
        structured["clipboard_operation"] = payload.get("operation")
        if observation.kind == "clipboard_content":
            clipboard_text = _first(payload, "text", "content")
            if clipboard_text is not None:
                structured["clipboard_text"] = clipboard_text
    if ocr:
        structured["ocr_metadata"] = {key: _copy(value) for key, value in ocr.items()
                                      if key not in {"text", "lines"}}
        if "text" in ocr:
            structured["ocr_text"] = _text(ocr["text"])
            if structured["ocr_text"]:
                parts.append(structured["ocr_text"])
        if "lines" in ocr:
            structured["ocr_lines"] = _copy(ocr["lines"])
    if uia:
        structured["uia_metadata"] = {key: _copy(value) for key, value in uia.items()
                                      if key not in {"nodes", "focused_control"}}
        nodes = uia.get("nodes") or ()
        node_parts: list[str] = []
        if isinstance(nodes, (list, tuple)):
            structured["uia_nodes"] = _copy(nodes)
            for node in nodes:
                if isinstance(node, Mapping):
                    for key in ("text", "value", "text_value", "name"):
                        if node.get(key) not in (None, ""):
                            node_parts.append(_text(node[key]))
        focused = uia.get("focused_control")
        if isinstance(focused, Mapping):
            structured["focused_control"] = _copy(focused)
            for key in ("text", "value", "text_value", "name"):
                if focused.get(key) not in (None, ""):
                    node_parts.append(_text(focused[key]))
        if node_parts:
            structured["uia_text"] = "\n".join(dict.fromkeys(node_parts))
            parts.append(structured["uia_text"])
    if observation.visual_summary:
        structured["visual"] = _copy(observation.visual_summary)
    if observation.missing_reasons:
        structured["missing_reasons"] = list(observation.missing_reasons)
    # Captured content remains complete in evidence. Only the searchable item
    # summary below is shortened to the index limit.
    return ("\n".join(dict.fromkeys(parts)), structured,
            _first(payload, "document_ref"), title, url)


def _activity(observation: Observation, source: Mapping[str, Any], *, title: Optional[str],
              url: Optional[str], document_ref: Optional[str], evidence_id: str) -> dict[str, Any]:
    payload = observation.payload or {}
    kind = observation.kind
    change: dict[str, Any] = {}
    unknowns: list[str] = ["user_intent_unconfirmed", "application_outcome_unconfirmed"]
    unknowns.extend(f"{field}_unknown" for field in source.get("resource_unknown_fields", ()))
    result = "unconfirmed"
    object_type = "monitor" if source.get("scope") == "monitor" else "window"
    obj = {"type": object_type, "app_id": observation.app_id,
           "process_instance_id": observation.process_instance_id,
           "window_instance_id": observation.window_instance_id,
           "title": title, "url": url, "document_ref": document_ref}
    if source.get("monitor_id"):
        obj["monitor_id"] = source["monitor_id"]
    if kind == "input_text":
        obj.update(type="editable_control", control_id=payload.get("control_id"))
        operation = payload.get("operation")
        action = "committed_text_observed" if operation == "snapshot" else "committed_text_changed"
        change = {key: _copy(payload[key]) for key in (
            "operation", "removed_char_count", "offset", "baseline_known", "status", "provenance",
        ) if key in payload}
        if payload.get("inserted_text") is not None:
            change["inserted_char_count"] = len(_text(payload["inserted_text"]))
        if operation == "delete":
            summary = f"观察到控件文本删除 {payload.get('removed_char_count', '未知数量')} 个字符。"
        elif operation == "replace":
            summary = (f"观察到控件文本替换：删除 {payload.get('removed_char_count', '未知数量')} 个字符，"
                       f"新增 {change.get('inserted_char_count', '未知数量')} 个字符。")
        elif operation == "insert" or payload.get("inserted_text"):
            summary = f"观察到控件文本新增 {change.get('inserted_char_count', 0)} 个字符。"
        elif operation == "snapshot":
            summary = "观察到当前控件文本；缺少此前文本基线。"
        else:
            action, summary = "committed_text_unavailable", "此次未取得可用的控件文本变化。"
        if not payload.get("baseline_known", False):
            unknowns.append("previous_text_baseline_unknown")
        # UIA diffs can be programmatic changes or pasted text. Attribution is
        # carried separately from this direct statement about the control.
        unknowns.extend(("edit_origin_unconfirmed", "save_status_unconfirmed"))
    elif kind == "input_batch":
        action, summary = "input_metadata_observed", "观察到键盘、点击或滚动输入计数。"
        change = {key: _copy(payload[key]) for key in (
            "keyboard_count", "click_count", "scroll_count", "motion", "quiet_ms",
            "started_monotonic_ns", "ended_monotonic_ns", "helper_emitted_at_utc_ms",
        ) if key in payload}
        unknowns.append("input_content_unknown")
    elif kind in {"clipboard_changed", "clipboard_operation", "clipboard_content"}:
        obj["type"] = "clipboard"
        action = "clipboard_content_observed" if kind == "clipboard_content" else "clipboard_signal_observed"
        summary = "观察到剪贴板内容。" if kind == "clipboard_content" else "观察到剪贴板变化或操作信号。"
        change = {key: _copy(payload[key]) for key in ("operation", "sequence", "status", "truncated") if key in payload}
        if kind == "clipboard_content" and payload.get("status") not in {None, "ok"}:
            action, summary = "clipboard_content_unavailable", "此次未取得可用的剪贴板文字。"
        unknowns.append("paste_destination_and_success_unconfirmed")
    elif kind == "foreground_changed":
        action, summary = "foreground_window_observed", "观察到前台窗口变化。"
        unknowns.append("reading_unconfirmed")
    elif kind == "window_context":
        action, summary = "window_context_observed", "观察到窗口标题或浏览器地址。"
        unknowns.extend(("reading_unconfirmed", "page_load_unconfirmed"))
    elif kind in {"content_snapshot", "ocr_result"}:
        action, summary = "screen_content_observed", "观察到屏幕图像或识别出的文字。"
        ocr = observation.ocr_summary or {}
        if not observation.asset_refs and not ocr.get("text") and observation.quality == "unavailable":
            action, summary = "screen_content_unavailable", "此次未取得可用的屏幕内容。"
        change = {key: _copy(source[key]) for key in ("capture_status",) if key in source}
        if ocr.get("status"):
            change["ocr_status"] = ocr["status"]
        unknowns.append("reading_unconfirmed")
    elif kind == "context_snapshot":
        action, summary = "accessible_content_observed", "观察到控件结构或可访问的内容。"
        uia = observation.uia_summary or {}
        if not uia.get("nodes") and not uia.get("focused_control") and observation.quality == "unavailable":
            action, summary = "accessible_content_unavailable", "此次未取得可用的控件结构或内容。"
        if uia.get("status"):
            change["uia_status"] = uia["status"]
        unknowns.append("reading_unconfirmed")
    elif kind in {"collection_gap", "collector_diagnostic"}:
        action, summary = "collection_status_observed", "观察到采集状态或采集缺口。"
        change = {key: _copy(payload[key]) for key in ("stage", "reason", "status") if key in payload}
        result = "observation_recorded"
    else:
        action, summary = "event_observed", "观察到一条事件记录。"
    if observation.actor == "UNKNOWN":
        unknowns.append("actor_unconfirmed")
    if observation.identity_confidence == "unknown":
        unknowns.append("target_identity_unverified")
    if observation.quality in {"gap", "unavailable", "partial"} or observation.missing_reasons:
        unknowns.append("capture_incomplete")
    return {"action": action, "summary": summary, "object": obj, "change": change,
            "result": result, "unknowns": list(dict.fromkeys(unknowns)),
            "actor": observation.actor, "actor_basis": observation.actor_basis,
            "evidence_ids": [evidence_id], "source_observation_ids": [observation.observation_id]}


def build_projection(observation: Observation, projection_version: str) -> tuple[ActivityItem, Evidence]:
    """Project without changing raw data or consulting models/platform APIs."""
    observation.validate()
    text, structured, document_ref, title, url = _content(observation)
    source_id = observation.observation_id
    item_id = deterministic_id("item", source_id, projection_version)
    evidence_id = deterministic_id("evidence", source_id, projection_version)
    source = _source(observation, observation.payload)
    source_limits = _source_limits(observation)
    activity = _activity(observation, source, title=title, url=url,
                         document_ref=document_ref, evidence_id=evidence_id)
    # A deletion or unavailable edit still has useful, searchable meaning.
    summary_source = (text or activity["summary"]) if observation.kind == "input_text" else text
    summary = summary_source[:TEXT_SUMMARY_LIMIT]
    summary_truncated = len(summary) < len(summary_source)
    structured.update(source=source, activity=activity, projection={
        "version": projection_version, "text_summary_limit_chars": TEXT_SUMMARY_LIMIT,
        "text_summary_original_chars": len(summary_source), "text_summary_truncated": summary_truncated,
        "evidence_text_truncated": False, "source_truncated": bool(source_limits),
        "source_limit_reasons": list(source_limits),
    })
    source_quality = observation.quality or ObservationQuality.COMPLETE
    evidence_quality = _partial(source_quality) if source_limits else source_quality
    item_quality = _partial(evidence_quality) if summary_truncated else evidence_quality
    missing = tuple(dict.fromkeys(tuple(observation.missing_reasons) + source_limits
                                  + (("projection_text_summary_limit",) if summary_truncated else ())))
    if missing:
        structured["missing_reasons"] = list(missing)
    if source_limits:
        activity["unknowns"] = list(dict.fromkeys(activity["unknowns"] + ["capture_incomplete"]))
    if summary_truncated:
        activity["unknowns"] = list(dict.fromkeys(activity["unknowns"] + ["item_summary_truncated",
                                                                                 "full_captured_text_retained"]))
    kind = _kind(observation, observation.payload, text)
    digest_input = json.dumps([kind, title, url, text, structured], ensure_ascii=False, sort_keys=True)
    content_hash = hashlib.sha256(digest_input.encode("utf-8")).hexdigest()
    observed = int(observation.observed_at_utc_ms)
    end = max(observed, int(observation.capture_finished_at or observed))
    item = ActivityItem(
        item_id=item_id, kind=kind, start_utc_ms=observed, end_utc_ms=end,
        app_id=observation.app_id, process_instance_id=observation.process_instance_id,
        window_instance_id=observation.window_instance_id, title=title, url=url,
        document_ref=document_ref, text_summary=summary, structured=structured,
        asset_refs=tuple(observation.asset_refs), source_observation_ids=(source_id,),
        actor=observation.actor, actor_basis=observation.actor_basis, quality=item_quality,
        missing_reasons=missing, confidence=_confidence(item_quality),
        importance=0.85 if observation.kind in {"input_text", "clipboard_content", "window_context", "content_snapshot"} else 0.55,
        sensitivity="sensitive" if observation.kind in {"input_text", "clipboard_content"} else "normal",
        content_hash=content_hash, projection_version=projection_version,
    )
    evidence_type = "ocr" if observation.ocr_summary else "uia" if observation.uia_summary else kind
    evidence = Evidence(
        evidence_id=evidence_id, source_observation_ids=(source_id,), observed_at_utc_ms=observed,
        received_at_utc_ms=observation.received_at_utc_ms, content_type=evidence_type,
        text=text, structured=structured, asset_refs=tuple(observation.asset_refs),
        content_hash=content_hash, quality=evidence_quality, confidence=_confidence(evidence_quality),
        extractor_version=projection_version,
    )
    return item, evidence
