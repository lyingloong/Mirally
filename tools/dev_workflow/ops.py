#!/usr/bin/env python3
"""Captain-side helpers for the AgentTeams development workflow.

This module does not replace ``agent_workflow.py`` or its state machine. It adds
the small operations the captain performs after every report:

- list the pending merge pool with PR/head links;
- record a spec path without editing state JSON by hand;
- record branch, worktree, PR, head SHA and task ids in one command.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.dev_workflow import agent_workflow as wf

DEFAULT_STATE_PATH = REPO_ROOT / "output" / "agent-workflow" / "state.json"
DEFAULT_CONFIG_PATH = REPO_ROOT / "output" / "agent-workflow" / "config.json"
RESTRICTED_STATUSES = {"blocked", "awaiting_merge", "merging", "done"}


def pending_rows(state: dict[str, Any], config: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the reviewed PRs waiting for user/Codex approval."""
    summary = wf.build_summary(state, config)
    by_id = {point["id"]: point for point in state["points"]}
    rows = []
    for row in summary["merge_queue"]:
        point = by_id[row["id"]]
        rows.append(
            {
                "id": point["id"],
                "source": point["source"],
                "title": point["title"],
                "risk": point["risk"],
                "pr": point.get("pr"),
                "head_sha": point.get("head_sha"),
                "branch": point.get("branch"),
                "review_ready_at": point.get("merge_ready_at"),
                "exempt": row["exempt"],
                "counts_toward_concurrency": row["counts_toward_concurrency"],
            }
        )
    return rows


def set_spec_path(state: dict[str, Any], point_id: str, spec_path: str) -> dict[str, Any]:
    point = wf.point_by_id(state, point_id)
    path = (spec_path or "").strip()
    if not path:
        raise wf.WorkflowError("spec path must not be empty")
    if not Path(path).exists():
        raise wf.WorkflowError(f"spec file does not exist: {path}")
    point["spec_path"] = path
    wf.touch(point)
    return point


def record_point(
    state: dict[str, Any],
    config: dict[str, Any],
    point_id: str,
    *,
    status: str | None = None,
    branch: str | None = None,
    worktree: str | None = None,
    pr: int | None = None,
    head_sha: str | None = None,
    task_assignments: list[str] | None = None,
    note: str | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Record one report, optionally transitioning through the normal gates."""
    point = wf.point_by_id(state, point_id)
    if status in RESTRICTED_STATUSES and not force:
        raise wf.WorkflowError(
            f"{status} requires a dedicated command "
            "(block / review-pass / merge-approved / merge-done)"
        )
    if status is not None and status != point["status"]:
        wf.transition(state, config, point, status, force=force, note=note)
    elif note:
        wf.append_note(point, note)
    if branch is not None:
        point["branch"] = branch
    if worktree is not None:
        point["worktree"] = worktree
    if pr is not None:
        point["pr"] = pr
    if head_sha is not None:
        point["head_sha"] = head_sha
    tasks = dict(point.get("task_ids") or {})
    for assignment in task_assignments or []:
        if "=" not in assignment:
            raise wf.WorkflowError(f"--task requires KEY=VALUE, got {assignment}")
        key, value = assignment.split("=", 1)
        if not key:
            raise wf.WorkflowError("--task KEY must not be empty")
        tasks[key] = value
    if tasks:
        point["task_ids"] = tasks
    wf.touch(point)
    return point


def _write(stream: Any, text: str) -> None:
    """Write without assuming UTF-8: escape only what the stream cannot encode."""
    encoding = getattr(stream, "encoding", None) or "ascii"
    try:
        data = text.encode(encoding, "backslashreplace")
    except LookupError:
        encoding = "ascii"
        data = text.encode(encoding, "backslashreplace")
    buffer = getattr(stream, "buffer", None)
    if buffer is not None:
        buffer.write(data)
        buffer.flush()
    else:
        stream.write(data.decode(encoding, "replace"))
        stream.flush()


def print_json_safe(value: Any) -> None:
    _write(sys.stdout, json.dumps(value, ensure_ascii=True, indent=2) + "\n")


def print_pending(rows: list[dict[str, Any]], *, as_json: bool = False) -> None:
    if as_json:
        print_json_safe(rows)
        return
    if not rows:
        _write(sys.stdout, "pending merge pool: empty\n")
        return
    lines = [f"pending merge pool: {len(rows)}"]
    for row in rows:
        pr = f"PR #{row['pr']}" if row.get("pr") else "PR ?"
        head = (row.get("head_sha") or "")[:8]
        lines.append(
            f"  {row['id']} [{row['source']}/{row['risk']}] {row['title']} "
            f"{pr} head={head or '?'} exempt={row['exempt']}"
        )
    _write(sys.stdout, "\n".join(lines) + "\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Captain-side AgentTeams workflow operations")
    parser.add_argument("--state", default=str(DEFAULT_STATE_PATH), help="state JSON path")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="config JSON path")
    sub = parser.add_subparsers(dest="command", required=True)

    pending = sub.add_parser("pending", help="list reviewed PRs in the pending merge pool")
    pending.add_argument("--json", action="store_true")

    spec = sub.add_parser("spec-path", help="record a generated spec path for a point")
    spec.add_argument("id")
    spec.add_argument("path")

    record = sub.add_parser("record", help="record one report with optional transition")
    record.add_argument("id")
    record.add_argument("--status", choices=sorted(wf.STATUSES))
    record.add_argument("--branch")
    record.add_argument("--worktree")
    record.add_argument("--pr", type=int)
    record.add_argument("--head-sha")
    record.add_argument("--task", action="append", default=[], metavar="KEY=VALUE")
    record.add_argument("--note")
    record.add_argument("--force", action="store_true")
    return parser


def command_pending(args: argparse.Namespace) -> int:
    state = wf.load_state(Path(args.state))
    config = wf.load_config(Path(args.config))
    print_pending(pending_rows(state, config), as_json=args.json)
    return 0


def command_spec_path(args: argparse.Namespace) -> int:
    state_path = Path(args.state)
    state = wf.load_state(state_path)
    point = set_spec_path(state, args.id, args.path)
    wf.save_state(state_path, state)
    print_json_safe(point)
    return 0


def command_record(args: argparse.Namespace) -> int:
    state_path = Path(args.state)
    state = wf.load_state(state_path)
    config = wf.load_config(Path(args.config))
    point = record_point(
        state,
        config,
        args.id,
        status=args.status,
        branch=args.branch,
        worktree=args.worktree,
        pr=args.pr,
        head_sha=args.head_sha,
        task_assignments=args.task,
        note=args.note,
        force=args.force,
    )
    wf.save_state(state_path, state)
    print_json_safe(point)
    return 0


COMMANDS = {
    "pending": command_pending,
    "spec-path": command_spec_path,
    "record": command_record,
}


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return COMMANDS[args.command](args)
    except wf.WorkflowError as error:
        _write(sys.stderr, f"error: {error}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
