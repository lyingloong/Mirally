#!/usr/bin/env python3
"""AgentTeams development workflow state manager.

The captain owns the loop: this helper only keeps a durable backlog, computes
concurrency, enforces the human merge gate, and records quota backoff. It does
not call GitHub or the AgentTeams API.

State and local config live under ``output/agent-workflow/`` by default (the
directory is gitignored). The tracked process definition is
``docs/development/agent-teams-workflow.md``.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STATE_PATH = REPO_ROOT / "output" / "agent-workflow" / "state.json"
DEFAULT_CONFIG_PATH = REPO_ROOT / "output" / "agent-workflow" / "config.json"

DEFAULT_CONFIG: dict[str, Any] = {
    "concurrency": 3,
    "max_auto_agent_in_flight": 1,
    "max_auto_agent_per_day": 2,
    "merge_queue_exempt": 1,
    "primary_route": {"provider": "ustc", "model": "deepseek-flash"},
    "quota": {
        "enabled": True,
        "retry_after_minutes": 30,
        "backoff_minutes": [30, 60, 120],
        "max_retries": 2,
        "fallback": {
            "enabled": True,
            "provider": "deepseek-account",
            "model": "deepseek-flash",
            "mode": "quota_restart_only",
        },
    },
    "branch_prefixes": {"user": "feat/", "agent": "agent/"},
    "worktree_root": None,
}

STATUSES = {
    "inbox",
    "proposed",
    "queued",
    "specifying",
    "implementing",
    "verifying",
    "reviewing",
    "repairing",
    "awaiting_merge",
    "merging",
    "done",
    "rejected",
    "cancelled",
    "blocked",
}
ACTIVE_STATUSES = {"specifying", "implementing", "verifying", "reviewing", "repairing"}
TERMINAL_STATUSES = {"done", "rejected", "cancelled"}
SOURCES = {"user", "agent"}
RISKS = {"low", "normal", "high"}

ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "inbox": {"queued", "rejected", "cancelled"},
    "proposed": {"queued", "rejected", "cancelled"},
    "queued": {"specifying", "rejected", "cancelled"},
    "specifying": {"implementing", "cancelled"},
    "implementing": {"verifying", "reviewing", "cancelled"},
    "verifying": {"reviewing", "repairing", "cancelled"},
    "reviewing": {"awaiting_merge", "repairing", "cancelled"},
    "repairing": {"verifying", "reviewing", "cancelled"},
    "awaiting_merge": {"merging", "repairing", "rejected", "cancelled"},
    "merging": {"done", "repairing"},
    "blocked": set(),
    "done": set(),
    "rejected": set(),
    "cancelled": set(),
}

META_FIELDS = ("branch", "worktree", "pr", "head_sha", "spec_path")


class WorkflowError(RuntimeError):
    """A user-facing workflow state error."""


# ---------------------------------------------------------------------------
# Utilities


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def deep_merge(default: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(default)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def read_json(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        return None
    except json.JSONDecodeError as error:
        raise WorkflowError(f"{path} is not valid JSON: {error}") from error


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def empty_state() -> dict[str, Any]:
    return {"version": 1, "next_id": 1, "updated_at": iso(utcnow()), "points": []}


def load_config(path: Path) -> dict[str, Any]:
    raw = read_json(path)
    if raw is None:
        config = copy.deepcopy(DEFAULT_CONFIG)
    elif not isinstance(raw, dict):
        raise WorkflowError(f"{path} top-level value must be an object")
    else:
        config = deep_merge(DEFAULT_CONFIG, raw)
    validate_config(config)
    return config


def validate_config(config: dict[str, Any]) -> None:
    concurrency = config.get("concurrency")
    if not isinstance(concurrency, int) or isinstance(concurrency, bool) or concurrency < 1:
        raise WorkflowError("config.concurrency must be an integer >= 1")
    for key in ("max_auto_agent_in_flight", "max_auto_agent_per_day", "merge_queue_exempt"):
        value = config.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise WorkflowError(f"config.{key} must be an integer >= 0")
    quota = config.get("quota")
    if not isinstance(quota, dict):
        raise WorkflowError("config.quota must be an object")
    retry = quota.get("retry_after_minutes")
    if not isinstance(retry, int) or isinstance(retry, bool) or retry < 1:
        raise WorkflowError("config.quota.retry_after_minutes must be a positive integer")
    backoff = quota.get("backoff_minutes")
    if not isinstance(backoff, list) or not backoff or any(
        not isinstance(item, int) or isinstance(item, bool) or item < 1 for item in backoff
    ):
        raise WorkflowError("config.quota.backoff_minutes must be a non-empty array of positive integers")
    fallback = quota.get("fallback")
    if fallback is not None and not isinstance(fallback, dict):
        raise WorkflowError("config.quota.fallback must be an object")
    if quota.get("enabled") and fallback and fallback.get("enabled"):
        if fallback.get("mode") != "quota_restart_only":
            raise WorkflowError("config.quota.fallback.mode currently supports only quota_restart_only")
        if not fallback.get("provider") or not fallback.get("model"):
            raise WorkflowError("fallback requires both provider and model")


def load_state(path: Path) -> dict[str, Any]:
    raw = read_json(path)
    if raw is None:
        return empty_state()
    if not isinstance(raw, dict) or not isinstance(raw.get("points"), list):
        raise WorkflowError(f"{path} is not a valid workflow state")
    state = raw
    state.setdefault("version", 1)
    if "next_id" not in raw:
        numbers = []
        for point in state["points"]:
            try:
                numbers.append(int(str(point.get("id", "")).split("-")[1]))
            except (IndexError, ValueError):
                pass
        state["next_id"] = max(numbers, default=0) + 1
    state.setdefault("updated_at", iso(utcnow()))
    return state


def save_state(path: Path, state: dict[str, Any]) -> None:
    state["updated_at"] = iso(utcnow())
    write_json(path, state)


def point_by_id(state: dict[str, Any], point_id: str) -> dict[str, Any]:
    for point in state["points"]:
        if point.get("id") == point_id:
            return point
    raise WorkflowError(f"point {point_id} not found")


def append_note(point: dict[str, Any], note: str | None) -> None:
    if not note:
        return
    existing = point.get("notes") or ""
    point["notes"] = (existing + "\n" + note).strip()


def touch(point: dict[str, Any]) -> None:
    point["updated_at"] = iso(utcnow())


def transition(
    state: dict[str, Any],
    config: dict[str, Any],
    point: dict[str, Any],
    status: str,
    *,
    force: bool = False,
    note: str | None = None,
) -> None:
    if status not in STATUSES:
        raise WorkflowError(f"unknown status {status}")
    current = point["status"]
    if status == current:
        raise WorkflowError(f"{point['id']} is already {status}")
    if not force and status not in ALLOWED_TRANSITIONS.get(current, set()):
        raise WorkflowError(
            f"transition {point['id']} from {current} to {status} is not allowed; use --force for recovery"
        )
    if not force:
        if current == "queued" and status == "specifying":
            raise WorkflowError("queued -> specifying must use start_point")
        if current == "proposed" and status == "queued":
            raise WorkflowError("proposed -> queued must use promote_point")
        if not _admit_transition(state, config, point, status, note):
            return
    point["status"] = status
    if status == "awaiting_merge":
        point["merge_ready_at"] = iso(utcnow())
    elif status == "merging":
        point["merge_approved_at"] = iso(utcnow())
    elif status == "done":
        point["merged_at"] = iso(utcnow())
    elif status == "specifying":
        point.setdefault("started_at", iso(utcnow()))
    append_note(point, note)
    touch(point)


# ---------------------------------------------------------------------------
# Points and scheduling


def new_point(
    state: dict[str, Any],
    *,
    source: str,
    title: str,
    risk: str = "normal",
    notes: str | None = None,
    spec_path: str | None = None,
) -> dict[str, Any]:
    if source not in SOURCES:
        raise WorkflowError("source must be user or agent")
    if risk not in RISKS:
        raise WorkflowError("risk must be low, normal or high")
    title = (title or "").strip()
    if not title:
        raise WorkflowError("title must not be empty")
    number = int(state.get("next_id") or 1)
    point_id = f"dev-{number:04d}"
    state["next_id"] = number + 1
    now = iso(utcnow())
    point = {
        "id": point_id,
        "source": source,
        "title": title,
        "risk": risk,
        "status": "queued" if source == "user" else "proposed",
        "labels": [f"source:{source}"],
        "notes": notes or "",
        "spec_path": spec_path,
        "branch": None,
        "worktree": None,
        "pr": None,
        "head_sha": None,
        "task_ids": {},
        "auto_started": False,
        "auto_started_at": None,
        "approved_by": None,
        "promoted_at": None,
        "started_at": None,
        "merge_ready_at": None,
        "merge_approved_at": None,
        "merged_at": None,
        "blocked_until": None,
        "blocked_from_status": None,
        "blocked_reason": None,
        "quota_retries": 0,
        "fallback": None,
        "created_at": now,
        "updated_at": now,
    }
    state["points"].append(point)
    return point


def _quota_blocked(state: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        point
        for point in state["points"]
        if point["status"] == "blocked" and point.get("blocked_reason") == "quota"
    ]


def _active_auto_others(state: dict[str, Any], point: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        other
        for other in state["points"]
        if other["id"] != point["id"]
        and other["source"] == "agent"
        and other.get("auto_started")
        and other["status"] in ACTIVE_STATUSES
    ]


def _projected_load(
    summary: dict[str, Any],
    config: dict[str, Any],
    previous: str,
) -> int:
    active = len(summary["active"]) + (1 if previous in ACTIVE_STATUSES else 0)
    waiting = len(summary["merge_queue"]) + (1 if previous == "awaiting_merge" else 0)
    exempt = min(int(config.get("merge_queue_exempt", 1)), waiting)
    return active + max(0, waiting - exempt)


def _park_for_capacity(point: dict[str, Any], target: str, note: str | None) -> None:
    append_note(point, note)
    point["blocked_from_status"] = target
    point["blocked_reason"] = "capacity"
    point["blocked_until"] = None
    point["status"] = "blocked"
    append_note(point, f"capacity full; deferred {target} until a slot frees")
    touch(point)


def _admit_transition(
    state: dict[str, Any],
    config: dict[str, Any],
    point: dict[str, Any],
    target: str,
    note: str | None,
) -> bool:
    if target not in ACTIVE_STATUSES and target != "awaiting_merge":
        return True
    before = build_summary(state, config)["load"]
    probe = copy.deepcopy(state)
    probe_point = point_by_id(probe, point["id"])
    probe_point["status"] = target
    after = build_summary(probe, config)["load"]
    if after > int(config["concurrency"]) and after > before:
        _park_for_capacity(point, target, note)
        return False
    if target in ACTIVE_STATUSES and point["status"] not in ACTIVE_STATUSES:
        if point["source"] == "agent" and point.get("auto_started"):
            if len(_active_auto_others(state, point)) >= int(config["max_auto_agent_in_flight"]):
                _park_for_capacity(point, target, note)
                return False
    return True


def _auto_today_count(state: dict[str, Any]) -> int:
    today = utcnow().date().isoformat()
    return sum(
        1
        for point in state["points"]
        if point.get("auto_started") and (point.get("auto_started_at") or "")[:10] == today
    )


def _merge_queue(state: dict[str, Any]) -> list[dict[str, Any]]:
    waiting = [point for point in state["points"] if point["status"] == "awaiting_merge"]
    return sorted(waiting, key=lambda point: (point.get("merge_ready_at") or "", point["id"]))


def _brief(point: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": point["id"],
        "source": point["source"],
        "title": point["title"],
        "status": point["status"],
        "risk": point["risk"],
        "pr": point.get("pr"),
        "auto_started": bool(point.get("auto_started")),
    }


def build_summary(state: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    points = state["points"]
    concurrency = int(config["concurrency"])
    active = [point for point in points if point["status"] in ACTIVE_STATUSES]
    merge_waiting = _merge_queue(state)
    merging = [point for point in points if point["status"] == "merging"]
    exempt = min(int(config.get("merge_queue_exempt", 1)), len(merge_waiting))
    load = len(active) + max(0, len(merge_waiting) - exempt)
    free = max(0, concurrency - load)

    user_pending = [
        point
        for point in points
        if point["source"] == "user" and point["status"] in {"inbox", "queued"}
    ]
    user_capacity_waiting = [
        point
        for point in points
        if point["source"] == "user"
        and point["status"] == "blocked"
        and point.get("blocked_reason") == "capacity"
    ]
    agent_in_flight = [
        point for point in points if point["source"] == "agent" and point["status"] in ACTIVE_STATUSES
    ]
    agent_auto_reserved = [
        point
        for point in points
        if point["source"] == "agent"
        and point.get("auto_started")
        and point["status"] in ACTIVE_STATUSES | {"queued"}
    ]
    agent_queued = [
        point for point in points if point["source"] == "agent" and point["status"] == "queued"
    ]
    agent_proposed = [
        point for point in points if point["source"] == "agent" and point["status"] == "proposed"
    ]
    agent_candidates = [point for point in agent_proposed if point["risk"] == "low"]

    quota_blocked = _quota_blocked(state)
    capacity_blocked = [
        point
        for point in points
        if point["status"] == "blocked" and point.get("blocked_reason") == "capacity"
    ]
    auto_today = _auto_today_count(state)
    quota = config["quota"]
    ai_cap_ok = (
        len(agent_auto_reserved) < int(config["max_auto_agent_in_flight"])
        and auto_today < int(config["max_auto_agent_per_day"])
    )
    paused = bool(quota_blocked)
    can_start = free > 0 and not paused

    start_user = user_pending[:free] if can_start else []
    if user_capacity_waiting:
        start_agent = []
    else:
        start_agent = agent_queued[: max(0, free - len(start_user))] if can_start else []

    can_auto_start_agent = bool(
        can_start
        and not user_pending
        and not user_capacity_waiting
        and not agent_queued
        and agent_candidates
        and ai_cap_ok
    )
    should_discover_agent = bool(
        can_start
        and not user_pending
        and not user_capacity_waiting
        and not agent_queued
        and not agent_proposed
        and ai_cap_ok
    )

    auto_block_reason = None
    if user_pending:
        auto_block_reason = "user pending points take priority"
    elif user_capacity_waiting:
        auto_block_reason = "user capacity-waiting rework must resume first"
    elif not can_start:
        auto_block_reason = "waiting for quota restart" if paused else "concurrency is full"
    elif not ai_cap_ok:
        auto_block_reason = "AI in-flight or daily auto-start cap reached"
    elif not agent_candidates:
        auto_block_reason = "no low-risk AI candidate"

    action = "idle"
    if paused:
        action = "wait_quota"
    elif merge_waiting:
        action = "ask_user_merge"
    elif start_user:
        action = "start_user"
    elif user_capacity_waiting and free > 0:
        action = "resume_capacity"
    elif start_agent:
        action = "start_agent"
    elif can_auto_start_agent:
        action = "promote_agent"
    elif should_discover_agent:
        action = "discover_agent"
    elif capacity_blocked and free > 0:
        action = "resume_capacity"

    return {
        "concurrency": concurrency,
        "load": load,
        "free_slots": free,
        "active": [_brief(point) for point in active],
        "user_pending": [_brief(point) for point in user_pending],
        "user_capacity_waiting": [_brief(point) for point in user_capacity_waiting],
        "start_user": [_brief(point) for point in start_user],
        "agent_in_flight": [_brief(point) for point in agent_in_flight],
        "agent_auto_reserved": [_brief(point) for point in agent_auto_reserved],
        "agent_queued": [_brief(point) for point in agent_queued],
        "start_agent": [_brief(point) for point in start_agent],
        "agent_proposed": [_brief(point) for point in agent_proposed],
        "agent_auto_candidates": [_brief(point) for point in agent_candidates],
        "can_auto_start_agent": can_auto_start_agent,
        "auto_start_block_reason": auto_block_reason,
        "should_discover_agent": should_discover_agent,
        "auto_today": auto_today,
        "max_auto_agent_in_flight": int(config["max_auto_agent_in_flight"]),
        "max_auto_agent_per_day": int(config["max_auto_agent_per_day"]),
        "merge_queue": [
            {
                **_brief(point),
                "exempt": index < exempt,
                "counts_toward_concurrency": index >= exempt,
            }
            for index, point in enumerate(merge_waiting)
        ],
        "merge_head": _brief(merge_waiting[0]) if merge_waiting else None,
        "merging": [_brief(point) for point in merging],
        "quota_blocked": [_brief(point) for point in quota_blocked],
        "capacity_blocked": [_brief(point) for point in capacity_blocked],
        "quota": {
            "enabled": bool(quota.get("enabled")),
            "retry_after_minutes": quota.get("retry_after_minutes"),
            "backoff_minutes": list(quota.get("backoff_minutes") or []),
            "fallback": copy.deepcopy(quota.get("fallback") or {}),
        },
        "paused": paused,
        "action": action,
    }


def promote_point(
    state: dict[str, Any],
    config: dict[str, Any],
    point_id: str,
    *,
    auto: bool = False,
) -> dict[str, Any]:
    point = point_by_id(state, point_id)
    if point["status"] != "proposed":
        raise WorkflowError(f"{point_id} is not proposed and cannot enter development")
    if auto:
        if point["source"] != "agent":
            raise WorkflowError("only agent-generated points can auto-start")
        if point["risk"] != "low":
            raise WorkflowError("only low-risk agent points can auto-start")
        summary = build_summary(state, config)
        if summary["paused"]:
            raise WorkflowError("workflow is paused for quota restart")
        if summary["user_pending"]:
            raise WorkflowError("user pending points must be scheduled first")
        if summary["user_capacity_waiting"]:
            raise WorkflowError("user capacity-waiting rework must resume first")
        if summary["free_slots"] <= 0:
            raise WorkflowError("concurrency is full")
        if len(summary["agent_auto_reserved"]) >= int(config["max_auto_agent_in_flight"]):
            raise WorkflowError("AI in-flight cap reached")
        if int(summary["auto_today"]) >= int(config["max_auto_agent_per_day"]):
            raise WorkflowError("daily auto-start cap reached")
        point["auto_started"] = True
        point["auto_started_at"] = iso(utcnow())
        point["approved_by"] = "auto"
        point["labels"] = sorted({*(point.get("labels") or []), "source:agent", "auto-start"})
    else:
        point["approved_by"] = "user"
    point["status"] = "queued"
    point["promoted_at"] = iso(utcnow())
    touch(point)
    return point


def start_point(state: dict[str, Any], config: dict[str, Any], point_id: str) -> dict[str, Any]:
    point = point_by_id(state, point_id)
    if point["status"] != "queued":
        raise WorkflowError(f"{point_id} is not queued and cannot start")
    summary = build_summary(state, config)
    if summary["paused"]:
        raise WorkflowError("workflow is paused for quota restart")
    if summary["free_slots"] <= 0:
        raise WorkflowError("concurrency is full")
    if point["source"] == "agent":
        user_priority = len(summary["user_pending"]) + len(summary["user_capacity_waiting"])
        if user_priority and user_priority >= summary["free_slots"]:
            raise WorkflowError("user pending or capacity-waiting points must fill the available slots first")
    if point["source"] == "agent" and point.get("auto_started"):
        if len(_active_auto_others(state, point)) >= int(config["max_auto_agent_in_flight"]):
            raise WorkflowError("AI auto-start in-flight cap reached")
    point["status"] = "specifying"
    point["started_at"] = iso(utcnow())
    touch(point)
    return point


def review_pass(state: dict[str, Any], config: dict[str, Any], point_id: str) -> dict[str, Any]:
    point = point_by_id(state, point_id)
    transition(
        state,
        config,
        point,
        "awaiting_merge",
        note="review passed; waiting for user merge approval",
    )
    return point


def review_fail(
    state: dict[str, Any],
    config: dict[str, Any],
    point_id: str,
    reason: str | None = None,
) -> dict[str, Any]:
    point = point_by_id(state, point_id)
    transition(
        state,
        config,
        point,
        "repairing",
        note=reason or "review requested changes; returned to engineer",
    )
    return point


def merge_approve(state: dict[str, Any], config: dict[str, Any], point_id: str) -> dict[str, Any]:
    point = point_by_id(state, point_id)
    if point["status"] != "awaiting_merge":
        raise WorkflowError(f"{point_id} is not awaiting_merge")
    queue = _merge_queue(state)
    if queue and queue[0]["id"] != point_id:
        raise WorkflowError(f"{point_id} is not the merge queue head (head is {queue[0]['id']})")
    transition(state, config, point, "merging", note="user approved merge")
    return point


def merge_done(state: dict[str, Any], config: dict[str, Any], point_id: str) -> dict[str, Any]:
    point = point_by_id(state, point_id)
    transition(state, config, point, "done", note="merged to main")
    return point


def merge_reject(
    state: dict[str, Any],
    config: dict[str, Any],
    point_id: str,
    *,
    reason: str | None = None,
    close: bool = False,
) -> dict[str, Any]:
    point = point_by_id(state, point_id)
    if point["status"] != "awaiting_merge":
        raise WorkflowError(f"{point_id} is not awaiting_merge")
    if close:
        transition(state, config, point, "rejected", note=reason or "user closed this point")
    else:
        transition(
            state,
            config,
            point,
            "repairing",
            note=reason or "user rejected merge; changes required",
        )
    return point


def reject_point(
    state: dict[str, Any],
    config: dict[str, Any],
    point_id: str,
    reason: str | None = None,
) -> dict[str, Any]:
    point = point_by_id(state, point_id)
    if point["status"] not in {"proposed", "queued", "inbox"}:
        raise WorkflowError(f"{point_id} cannot be rejected from its current status")
    transition(state, config, point, "rejected", note=reason or "user rejected this point")
    return point


def block_point(
    state: dict[str, Any],
    config: dict[str, Any],
    point_id: str,
    *,
    reason: str,
    minutes: int | None = None,
    note: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    point = point_by_id(state, point_id)
    if point["status"] in TERMINAL_STATUSES:
        raise WorkflowError(f"{point_id} is already terminal and cannot be blocked")
    if point["status"] == "blocked":
        raise WorkflowError(f"{point_id} is already blocked")
    if reason not in {"quota", "external", "other"}:
        raise WorkflowError("reason must be quota, external or other")

    previous = point["status"]
    quota = config["quota"]
    attempt = int(point.get("quota_retries") or 0)
    if reason == "quota":
        backoff = list(quota.get("backoff_minutes") or [quota["retry_after_minutes"]])
        wait_minutes = int(minutes or backoff[min(attempt, len(backoff) - 1)])
        point["quota_retries"] = attempt + 1
    else:
        wait_minutes = int(minutes or quota["retry_after_minutes"])
    retry_exhausted = reason == "quota" and point["quota_retries"] > int(quota.get("max_retries", 0))

    point["blocked_from_status"] = previous
    point["blocked_reason"] = reason
    point["blocked_until"] = iso(utcnow() + timedelta(minutes=wait_minutes))
    point["status"] = "blocked"
    fallback = None
    fallback_config = quota.get("fallback") or {}
    if reason == "quota" and quota.get("enabled") and fallback_config.get("enabled"):
        fallback = {**copy.deepcopy(fallback_config), "active": True, "used_at": iso(utcnow())}
        point["fallback"] = copy.deepcopy(fallback)
    append_note(point, note or f"{reason} block; retry after {wait_minutes} minutes")
    touch(point)
    result = {
        "id": point["id"],
        "blocked_until": point["blocked_until"],
        "wait_minutes": wait_minutes,
        "attempt": point["quota_retries"] if reason == "quota" else None,
        "retry_exhausted": retry_exhausted,
        "fallback": fallback,
        "fallback_policy": "only_during_quota_restart" if fallback else None,
        "primary_route": copy.deepcopy(config.get("primary_route") or {}),
    }
    return point, result


def resume_point(state: dict[str, Any], config: dict[str, Any], point_id: str) -> dict[str, Any]:
    point = point_by_id(state, point_id)
    if point["status"] != "blocked":
        raise WorkflowError(f"{point_id} is not blocked")
    previous = point.get("blocked_from_status")
    if previous not in STATUSES or previous == "blocked":
        raise WorkflowError(f"{point_id} has no valid previous status")
    if previous in ACTIVE_STATUSES or previous == "awaiting_merge":
        summary = build_summary(state, config)
        projected = _projected_load(summary, config, previous)
        if projected > int(config["concurrency"]):
            raise WorkflowError("cannot resume: projected load would exceed concurrency; point kept blocked")
        free_after = int(config["concurrency"]) - projected
        if previous == "specifying" and point["source"] == "agent" and summary["user_pending"]:
            if len(summary["user_pending"]) >= free_after:
                raise WorkflowError("cannot resume: user pending points need the available slots")
        if (
            previous in ACTIVE_STATUSES
            and point["source"] == "agent"
            and point.get("auto_started")
        ):
            if len(_active_auto_others(state, point)) >= int(config["max_auto_agent_in_flight"]):
                raise WorkflowError("cannot resume: AI auto-start in-flight cap reached")
    point["status"] = previous
    point["blocked_until"] = None
    point["blocked_from_status"] = None
    point["blocked_reason"] = None
    if point.get("fallback"):
        point["fallback"] = None
        append_note(point, "restored USTC primary route")
    touch(point)
    return point


def validate_state(state: dict[str, Any], config: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    if state.get("version") != 1:
        issues.append("state.version must be 1")
    if not isinstance(state.get("points"), list):
        return ["state.points must be an array"]
    seen: set[str] = set()
    max_number = 0
    for index, point in enumerate(state["points"]):
        label = point.get("id") if isinstance(point, dict) else None
        label = label or f"points[{index}]"
        if not isinstance(point, dict):
            issues.append(f"{label} is not an object")
            continue
        point_id = point.get("id")
        if not isinstance(point_id, str) or not point_id.startswith("dev-"):
            issues.append(f"{label} has an invalid id")
        elif point_id in seen:
            issues.append(f"{label} has a duplicate id")
        else:
            seen.add(point_id)
            try:
                max_number = max(max_number, int(point_id.split("-")[1]))
            except (IndexError, ValueError):
                issues.append(f"{label} has an invalid id number")
        if point.get("source") not in SOURCES:
            issues.append(f"{label} has an invalid source")
        if point.get("risk") not in RISKS:
            issues.append(f"{label} has an invalid risk")
        if point.get("status") not in STATUSES:
            issues.append(f"{label} has an invalid status")
        if point.get("status") == "blocked":
            if not point.get("blocked_from_status"):
                issues.append(f"{label} blocked point is missing blocked_from_status")
            if point.get("blocked_reason") != "capacity" and not point.get("blocked_until"):
                issues.append(f"{label} blocked point is missing blocked_until")
        if point.get("status") == "awaiting_merge" and not point.get("merge_ready_at"):
            issues.append(f"{label} awaiting_merge point is missing merge_ready_at")
    next_id = state.get("next_id")
    if not isinstance(next_id, int) or next_id < 1:
        issues.append("state.next_id must be a positive integer")
    elif next_id <= max_number:
        issues.append("state.next_id must be greater than every existing id")
    if not isinstance(config.get("concurrency"), int) or config["concurrency"] < 1:
        issues.append("config.concurrency is invalid")
    return issues


# ---------------------------------------------------------------------------
# CLI


def print_json(value: Any) -> None:
    json.dump(value, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


def format_point(point: dict[str, Any]) -> str:
    source = "AI" if point["source"] == "agent" else "user"
    bits = [f"{point['id']} [{source}/{point['risk']}] {point['title']}", point["status"]]
    if point.get("pr"):
        bits.append(f"PR #{point['pr']}")
    if point.get("auto_started"):
        bits.append("auto-started")
    if point.get("blocked_until"):
        bits.append(f"blocked until {point['blocked_until']}")
    return " | ".join(bits)


def print_status(summary: dict[str, Any]) -> None:
    print(f"concurrency {summary['concurrency']} | load {summary['load']} | free {summary['free_slots']}")
    print(f"next action: {summary['action']}")
    if summary["paused"]:
        print("quota restart pause: all new development is paused")
    if summary["active"]:
        print("active:")
        for point in summary["active"]:
            print("  " + format_point(point))
    if summary["merge_queue"]:
        print("waiting for user merge approval (only the head is exempt):")
        for point in summary["merge_queue"]:
            marker = "head/exempt" if point["exempt"] else "queued/counts toward concurrency"
            print(f"  {format_point(point)} ({marker})")
    if summary["user_pending"]:
        print("user points waiting:")
        for point in summary["user_pending"]:
            print("  " + format_point(point))
    if summary["user_capacity_waiting"]:
        print("user rework waiting for capacity:")
        for point in summary["user_capacity_waiting"]:
            print("  " + format_point(point))
    if summary["agent_auto_reserved"] or summary["max_auto_agent_in_flight"]:
        print(
            "AI auto-start reserved/in flight: "
            f"{len(summary['agent_auto_reserved'])}/{summary['max_auto_agent_in_flight']}, "
            f"auto-started today: {summary['auto_today']}/{summary['max_auto_agent_per_day']}"
        )
    if summary["agent_proposed"]:
        print("AI proposals:")
        for point in summary["agent_proposed"]:
            print("  " + format_point(point))
    if summary["quota_blocked"]:
        print("quota blocked:")
        for point in summary["quota_blocked"]:
            print("  " + format_point(point))
    if summary["capacity_blocked"]:
        print("capacity deferred (resume when a slot frees):")
        for point in summary["capacity_blocked"]:
            print("  " + format_point(point))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AgentTeams development workflow state manager")
    parser.add_argument("--state", default=str(DEFAULT_STATE_PATH), help="state JSON path")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="config JSON path")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="create state and default config if absent")

    add = sub.add_parser("add", help="add a development point")
    add.add_argument("--source", required=True, choices=sorted(SOURCES))
    add.add_argument("--title", required=True)
    add.add_argument("--risk", default="normal", choices=sorted(RISKS))
    add.add_argument("--notes", default=None)
    add.add_argument("--spec-path", default=None)

    list_cmd = sub.add_parser("list", help="list points")
    list_cmd.add_argument("--status", choices=sorted(STATUSES))
    list_cmd.add_argument("--source", choices=sorted(SOURCES))
    list_cmd.add_argument("--json", action="store_true")

    status = sub.add_parser("status", help="show scheduling summary")
    status.add_argument("--json", action="store_true")

    next_cmd = sub.add_parser("next", help="show the next scheduling decision as JSON")
    next_cmd.add_argument("--json", action="store_true", default=True)

    promote = sub.add_parser("promote", help="move an AI proposal into the development queue")
    promote.add_argument("id")
    promote.add_argument("--auto", action="store_true")

    start = sub.add_parser("start", help="move a queued point into specifying")
    start.add_argument("id")

    set_status = sub.add_parser("set-status", help="update status and engineering metadata")
    set_status.add_argument("id")
    set_status.add_argument("status", choices=sorted(STATUSES))
    set_status.add_argument("--force", action="store_true")
    set_status.add_argument("--branch")
    set_status.add_argument("--worktree")
    set_status.add_argument("--pr", type=int)
    set_status.add_argument("--head-sha")
    set_status.add_argument("--task", action="append", default=[], metavar="KEY=VALUE")
    set_status.add_argument("--note")

    review_pass_cmd = sub.add_parser("review-pass", help="mark review passed; never merges")
    review_pass_cmd.add_argument("id")
    review_fail_cmd = sub.add_parser("review-fail", help="return a point to the engineer")
    review_fail_cmd.add_argument("id")
    review_fail_cmd.add_argument("--reason")

    merge_approved = sub.add_parser("merge-approved", help="record user approval; only the head")
    merge_approved.add_argument("id")
    merge_done = sub.add_parser("merge-done", help="record the PR as merged")
    merge_done.add_argument("id")
    merge_rejected = sub.add_parser("merge-rejected", help="record a user merge rejection")
    merge_rejected.add_argument("id")
    merge_rejected.add_argument("--reason")
    merge_rejected.add_argument("--close", action="store_true")

    reject = sub.add_parser("reject-point", help="reject a proposed/queued point")
    reject.add_argument("id")
    reject.add_argument("--reason")

    block = sub.add_parser("block", help="block a point due to quota or external state")
    block.add_argument("id")
    block.add_argument("--reason", required=True, choices=["quota", "external", "other"])
    block.add_argument("--minutes", type=int)
    block.add_argument("--note")

    resume = sub.add_parser("resume", help="restore a blocked point to its previous status")
    resume.add_argument("id")

    validate = sub.add_parser("validate", help="validate state invariants")
    validate.add_argument("--json", action="store_true")

    sub.add_parser("config", help="print the merged config")
    return parser


def command_init(args: argparse.Namespace) -> int:
    state_path = Path(args.state)
    config_path = Path(args.config)
    if not state_path.exists():
        write_json(state_path, empty_state())
    if not config_path.exists():
        write_json(config_path, DEFAULT_CONFIG)
    print(f"state:  {state_path}")
    print(f"config: {config_path}")
    return 0


def command_add(args: argparse.Namespace) -> int:
    state_path = Path(args.state)
    state = load_state(state_path)
    point = new_point(
        state,
        source=args.source,
        title=args.title,
        risk=args.risk,
        notes=args.notes,
        spec_path=args.spec_path,
    )
    save_state(state_path, state)
    print_json(point)
    return 0


def command_list(args: argparse.Namespace) -> int:
    state = load_state(Path(args.state))
    points = state["points"]
    if args.status:
        points = [point for point in points if point["status"] == args.status]
    if args.source:
        points = [point for point in points if point["source"] == args.source]
    if args.json:
        print_json(points)
    else:
        for point in points:
            print(format_point(point))
    return 0


def command_status(args: argparse.Namespace) -> int:
    state = load_state(Path(args.state))
    config = load_config(Path(args.config))
    summary = build_summary(state, config)
    if args.json:
        print_json(summary)
    else:
        print_status(summary)
    return 0


def command_next(args: argparse.Namespace) -> int:
    state = load_state(Path(args.state))
    config = load_config(Path(args.config))
    print_json(build_summary(state, config))
    return 0


def command_promote(args: argparse.Namespace) -> int:
    state_path = Path(args.state)
    state = load_state(state_path)
    config = load_config(Path(args.config))
    point = promote_point(state, config, args.id, auto=args.auto)
    save_state(state_path, state)
    print_json(point)
    return 0


def command_start(args: argparse.Namespace) -> int:
    state_path = Path(args.state)
    state = load_state(state_path)
    config = load_config(Path(args.config))
    point = start_point(state, config, args.id)
    save_state(state_path, state)
    print_json(point)
    return 0


def command_set_status(args: argparse.Namespace) -> int:
    state_path = Path(args.state)
    state = load_state(state_path)
    config = load_config(Path(args.config))
    point = point_by_id(state, args.id)
    if args.status in {"blocked", "awaiting_merge", "merging", "done"} and not args.force:
        raise WorkflowError(
            f"{args.status} requires a dedicated command "
            "(block / review-pass / merge-approved / merge-done)"
        )
    started = False
    if not args.force and args.status == "specifying" and point["status"] == "queued":
        point = start_point(state, config, args.id)
        started = True
    elif not args.force and args.status == "queued" and point["status"] == "proposed":
        raise WorkflowError("proposed -> queued requires promote (user approval or --auto)")
    else:
        transition(state, config, point, args.status, force=args.force, note=args.note)
    for field in META_FIELDS:
        value = getattr(args, field, None)
        if value is not None:
            point[field] = value
    tasks = dict(point.get("task_ids") or {})
    for assignment in args.task:
        if "=" not in assignment:
            raise WorkflowError(f"--task requires KEY=VALUE, got {assignment}")
        key, value = assignment.split("=", 1)
        if not key:
            raise WorkflowError("--task KEY must not be empty")
        tasks[key] = value
    if tasks:
        point["task_ids"] = tasks
    if started:
        append_note(point, args.note)
    touch(point)
    save_state(state_path, state)
    print_json(point)
    return 0


def command_review_pass(args: argparse.Namespace) -> int:
    state_path = Path(args.state)
    state = load_state(state_path)
    config = load_config(Path(args.config))
    point = review_pass(state, config, args.id)
    save_state(state_path, state)
    print_json(point)
    return 0


def command_review_fail(args: argparse.Namespace) -> int:
    state_path = Path(args.state)
    state = load_state(state_path)
    config = load_config(Path(args.config))
    point = review_fail(state, config, args.id, args.reason)
    save_state(state_path, state)
    print_json(point)
    return 0


def command_merge_approved(args: argparse.Namespace) -> int:
    state_path = Path(args.state)
    state = load_state(state_path)
    config = load_config(Path(args.config))
    point = merge_approve(state, config, args.id)
    save_state(state_path, state)
    print_json(point)
    return 0


def command_merge_done(args: argparse.Namespace) -> int:
    state_path = Path(args.state)
    state = load_state(state_path)
    config = load_config(Path(args.config))
    point = merge_done(state, config, args.id)
    save_state(state_path, state)
    print_json(point)
    return 0


def command_merge_rejected(args: argparse.Namespace) -> int:
    state_path = Path(args.state)
    state = load_state(state_path)
    config = load_config(Path(args.config))
    point = merge_reject(state, config, args.id, reason=args.reason, close=args.close)
    save_state(state_path, state)
    print_json(point)
    return 0


def command_reject_point(args: argparse.Namespace) -> int:
    state_path = Path(args.state)
    state = load_state(state_path)
    config = load_config(Path(args.config))
    point = reject_point(state, config, args.id, args.reason)
    save_state(state_path, state)
    print_json(point)
    return 0


def command_block(args: argparse.Namespace) -> int:
    state_path = Path(args.state)
    state = load_state(state_path)
    config = load_config(Path(args.config))
    point, result = block_point(
        state,
        config,
        args.id,
        reason=args.reason,
        minutes=args.minutes,
        note=args.note,
    )
    save_state(state_path, state)
    print_json({**result, "point": point})
    return 0


def command_resume(args: argparse.Namespace) -> int:
    state_path = Path(args.state)
    state = load_state(state_path)
    config = load_config(Path(args.config))
    point = resume_point(state, config, args.id)
    save_state(state_path, state)
    print_json(point)
    return 0


def command_validate(args: argparse.Namespace) -> int:
    state = load_state(Path(args.state))
    config = load_config(Path(args.config))
    issues = validate_state(state, config)
    if args.json:
        print_json({"ok": not issues, "issues": issues})
    elif issues:
        for issue in issues:
            print("ERROR " + issue)
    else:
        print("state OK")
    return 0 if not issues else 1


def command_config(args: argparse.Namespace) -> int:
    config = load_config(Path(args.config))
    print_json({"_config_path": args.config, **config})
    return 0


COMMANDS = {
    "init": command_init,
    "add": command_add,
    "list": command_list,
    "status": command_status,
    "next": command_next,
    "promote": command_promote,
    "start": command_start,
    "set-status": command_set_status,
    "review-pass": command_review_pass,
    "review-fail": command_review_fail,
    "merge-approved": command_merge_approved,
    "merge-done": command_merge_done,
    "merge-rejected": command_merge_rejected,
    "reject-point": command_reject_point,
    "block": command_block,
    "resume": command_resume,
    "validate": command_validate,
    "config": command_config,
}


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return COMMANDS[args.command](args)
    except WorkflowError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
