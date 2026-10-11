"""Window activity fragments assembled from factual items, without a model.

An episode is an observed window activity span, not a completed user task.
Fragments retain its stable id so bounded pages can be merged by a consumer.
"""

from __future__ import annotations

import hashlib
from typing import Any, Iterable, Mapping

from .models import ActivityEpisode, ActivityItem


def _unique(values: Iterable[Any]) -> list[Any]:
    return list(dict.fromkeys(values))


def _references(item: ActivityItem) -> dict[str, Any]:
    source = item.structured.get("source") or {}
    activity = item.structured.get("activity") or {}
    return {
        "item_id": item.item_id,
        "source_observation_ids": list(item.source_observation_ids),
        "context_observation_ids": list(item.structured.get("context_observation_ids") or ()),
        "evidence_ids": list(activity.get("evidence_ids") or ()),
        "capture_group_id": source.get("capture_group_id"),
        "caused_by": list(source.get("caused_by") or ()),
    }


def _limited_fields(values: Mapping[str, Any], limit: int = 600) -> tuple[dict[str, Any], list[str]]:
    result, truncated = {}, []
    for key, value in values.items():
        if isinstance(value, str) and len(value) > limit:
            result[key] = value[:limit]
            truncated.append(key)
        else:
            result[key] = value
    return result, truncated


def _text_state(item: ActivityItem, text: str, source_kind: str,
                cache: dict[tuple[str, str], dict[str, Any]]) -> dict[str, Any]:
    key = (item.item_id, source_kind)
    if key in cache:
        return cache[key]
    source = item.structured.get("source") or {}
    result = {
        **_references(item), "observed_at_utc_ms": item.start_utc_ms,
        "source_kind": source_kind, "excerpt": text[:600],
        "excerpt_truncated": len(text) > 600, "original_char_count": len(text),
        "text_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "quality": source.get("quality", item.quality),
        "source_truncated": bool((item.structured.get("projection") or {}).get("source_truncated")),
        "capture_scope": dict(source.get("capture_scope") or {}),
    }
    cache[key] = result
    return result


def _action(item: ActivityItem, kind: str) -> dict[str, Any] | None:
    source = item.structured.get("source") or {}
    activity = item.structured.get("activity") or {}
    change = dict(activity.get("change") or {})
    if kind == "input_batch":
        if not any(change.get(key) for key in ("keyboard_count", "click_count", "scroll_count", "motion")):
            return None
        parts = [f"{label} {change[key]} 次" for key, label in (
            ("keyboard_count", "按键记录"), ("click_count", "点击记录"), ("scroll_count", "滚动记录"),
        ) if change.get(key)]
        if change.get("motion"):
            parts.append("鼠标移动信号")
        action, summary = "input_activity", "观察到" + "、".join(parts) + "。"
    elif kind == "input_text":
        edit = item.structured.get("input_text") or {}
        if not edit.get("baseline_known") or edit.get("operation") not in {"insert", "delete", "replace"}:
            return None
        if edit.get("truncated") or (item.structured.get("projection") or {}).get("source_truncated"):
            return None
        action, summary = "text_changed", str(activity.get("summary") or "观察到控件文字变化。")
    elif kind == "clipboard_operation" and change.get("operation") in {"copy", "cut", "paste"}:
        action = "clipboard_operation_signal"
        summary = {"copy": "观察到复制操作信号。", "cut": "观察到剪切操作信号。",
                   "paste": "观察到粘贴操作信号。"}[change["operation"]]
    else:
        return None
    change, truncated = _limited_fields(change)
    return {
        **_references(item), "observed_at_utc_ms": item.start_utc_ms,
        "action": action, "summary": summary[:600], "change": change,
        "truncated_fields": truncated, "actor": item.actor, "actor_basis": item.actor_basis,
        "time_quality": source.get("time_quality", "unknown"),
        "time_semantics": "observation_time; exact_action_wall_time_unconfirmed",
        "attribution": "user" if item.actor == "USER" else "agent" if item.actor == "AGENT"
                       else "system" if item.actor == "SYSTEM" else "unconfirmed",
        "outcome": "unconfirmed",
    }


def _background(item: ActivityItem) -> bool:
    source = item.structured.get("source") or {}
    return (item.actor == "BACKGROUND" or item.actor_basis == "monitor_observation"
            or source.get("scope") in {"monitor", "monitors"})


def build_event_fragments(
    items: Iterable[ActivityItem], episodes: Mapping[str, ActivityEpisode], *,
    include_uia: bool, include_ocr: bool, include_assets: bool,
    text_cache: dict[tuple[str, str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Combine factual items; text samples support an event, never cause one."""
    groups: dict[str, list[ActivityItem]] = {}
    text_cache = {} if text_cache is None else text_cache
    background, unattributed, collection_status = [], [], []
    for item in sorted(items, key=lambda value: (value.start_utc_ms,
                                               (value.structured.get("source") or {}).get("ingest_seq", 0), value.item_id)):
        source = item.structured.get("source") or {}
        kind = source.get("kind", item.kind)
        reference = {**_references(item), "observed_at_utc_ms": item.start_utc_ms,
                     "source_kind": kind, "quality": item.quality,
                     "missing_reasons": list(item.missing_reasons)}
        if _background(item):
            entry = {**reference, "scope": "background", "monitor_id": source.get("monitor_id"),
                     "window_ownership": "unconfirmed"}
            if include_ocr and item.structured.get("ocr_text"):
                entry.update(_text_state(item, str(item.structured["ocr_text"]), kind, text_cache))
            if include_assets:
                entry["asset_refs"] = list(item.asset_refs)
            background.append(entry)
        elif item.kind == "system":
            collection_status.append(reference)
        elif (not item.window_instance_id or not item.episode_id
              or source.get("linkage_status") == "unresolved"):
            unattributed.append(reference)
        else:
            groups.setdefault(item.episode_id, []).append(item)

    events = []
    for episode_id, members in groups.items():
        episode = episodes.get(episode_id)
        member_ids = [item.item_id for item in members]
        total = len(episode.item_ids) if episode else len(member_ids)
        partial = episode is None or set(member_ids) != set(episode.item_ids)
        states, texts, actions, input_states, clipboard_states = [], [], [], [], []
        unknowns = _unique(value for item in members for value in
                           (item.structured.get("activity") or {}).get("unknowns", ())
                           if value != "capture_or_projection_incomplete")
        summary_limited = any((item.structured.get("projection") or {}).get("text_summary_truncated")
                              for item in members)
        if summary_limited:
            unknowns.append("item_summary_truncated")
        if summary_limited and all(not (item.structured.get("projection") or {}).get("evidence_text_truncated", True)
                                   for item in members):
            unknowns.append("full_captured_text_retained")
        prior_window = None
        for item in members:
            source = item.structured.get("source") or {}
            kind = source.get("kind", item.kind)
            target = {"app_id": item.app_id, "process_instance_id": item.process_instance_id,
                      "window_instance_id": item.window_instance_id, "title": item.title,
                      "url": item.url, "document_ref": item.document_ref}
            if target != prior_window or kind in {"foreground_changed", "window_context"}:
                window, trimmed = _limited_fields(target)
                states.append({**_references(item), "observed_at_utc_ms": item.start_utc_ms,
                               "source_kind": kind, "window": window, "truncated_fields": trimmed,
                               "foreground_observed": kind == "foreground_changed",
                               "identity_confidence": source.get("identity_confidence", "unknown")})
                prior_window = target
            action = _action(item, kind)
            if action:
                actions.append(action)
            if kind == "input_text" and action is None:
                edit, trimmed = _limited_fields(dict(item.structured.get("input_text") or {}))
                input_states.append({**_references(item), "observed_at_utc_ms": item.start_utc_ms,
                                     "state": edit, "truncated_fields": trimmed,
                                     "semantics": "control_text_state; edit_origin_unconfirmed"})
            if kind in {"clipboard_changed", "clipboard_content"}:
                clipboard_states.append({**_references(item), "observed_at_utc_ms": item.start_utc_ms,
                                         "source_kind": kind,
                                         "status": (item.structured.get("clipboard") or {}).get("status"),
                                         "semantics": "clipboard_state; operation_success_unconfirmed"})
            if kind == "context_snapshot" and include_uia and item.structured.get("uia_text"):
                texts.append(_text_state(item, str(item.structured["uia_text"]), kind, text_cache))
            if kind in {"content_snapshot", "ocr_result"} and include_ocr and item.structured.get("ocr_text"):
                texts.append(_text_state(item, str(item.structured["ocr_text"]), kind, text_cache))
        if not actions:
            unknowns.append("action_evidence_missing")
        if not texts:
            unknowns.append("window_text_not_in_view")
        if partial:
            unknowns.append("event_members_not_fully_in_view")
        subject = (members[-1].app_id or "未知应用")[:120]
        facts = _unique(action["summary"] for action in actions)
        if input_states:
            facts.append("观察到控件文字状态，未确认新输入。")
        if texts:
            facts.append("有窗口文字证据。")
        if not actions:
            facts.insert(0, "观察到窗口或资源状态，具体操作未确认。")
        summary = ("片段：" if partial else "") + subject + "：" + "；".join(facts)
        event = {
            "event_id": episode_id, "episode_id": episode_id, "type": "window_activity",
            "observed_start_utc_ms": min(item.start_utc_ms for item in members),
            "observed_end_utc_ms": max(item.end_utc_ms for item in members),
            "episode_observed_start_utc_ms": episode.start_utc_ms if episode else None,
            "episode_observed_end_utc_ms": episode.end_utc_ms if episode else None,
            "time_semantics": "observed_span; active_duration_unconfirmed",
            "app_id": members[-1].app_id, "window_instance_id": members[-1].window_instance_id,
            "summary": summary[:800], "summary_truncated": len(summary) > 800,
            "actions": actions, "window_states": states, "window_text": texts,
            "input_states": input_states, "clipboard_states": clipboard_states,
            "user_actions_observed": any(action["attribution"] == "user" for action in actions),
            "outcome": "unconfirmed", "unknowns": _unique(unknowns),
            "emitted_item_ids": member_ids, "episode_item_count": total, "partial_event": partial,
            "source_observation_ids": _unique(oid for item in members for oid in item.source_observation_ids),
            "context_observation_ids": _unique(oid for item in members for oid in
                                              item.structured.get("context_observation_ids", ())),
            "evidence_ids": _unique(eid for item in members for eid in
                                    (item.structured.get("activity") or {}).get("evidence_ids", ())),
            "missing_reasons": _unique(reason for item in members for reason in item.missing_reasons),
            "source_missing_reasons": _unique(reason for item in members for reason in item.missing_reasons
                                               if reason != "projection_text_summary_limit"),
            "view_limits": (["item_summary_truncated"] if summary_limited else [])
                           + (["window_text_excerpt_truncated"] if any(text["excerpt_truncated"] for text in texts) else []),
            "data_gaps": list(episode.data_gaps) if episode else [],
            "finalized": episode.finalized if episode else False,
            "end_reason": episode.end_reason if episode else None,
            "revision": episode.revision if episode else None,
        }
        if include_assets:
            event["asset_refs"] = _unique(ref for item in members for ref in item.asset_refs)
        events.append(event)
    return {"events": events, "background_evidence": background,
            "unattributed_evidence": unattributed, "collection_status": collection_status}
