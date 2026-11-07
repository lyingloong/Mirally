"""Read-only generator for the Mirally development progress page (dev-0023).

Builds a single self-contained HTML page plus a side-car data snapshot from the
workflow owners. The generator only *reads*:

* ``<workdir>/state.json`` / ``config.json`` through ``agent_workflow``
  (``load_state`` / ``load_config`` / ``build_summary`` / ``validate_state``),
  so concurrency and scheduling are never recomputed here;
* ``<workdir>/specs/*.md``, ``verification/*.md``, ``review-*.md`` (headers only);
* ``<workdir>/page-tasks.json`` / ``page-decisions.md`` (captain-maintained);
* ``<workdir>/pr-cache.json`` (written only by the explicit ``--refresh-pr``).

It never reads the user's product home, credentials, ``.artifacts/`` or the
``.agent-teams/`` runtime directory, never writes product state, and never
touches the network unless ``--refresh-pr`` is given.

    python tools/dev_workflow/progress_page.py
    python tools/dev_workflow/progress_page.py --offline
    python tools/dev_workflow/progress_page.py --refresh-pr
"""

from __future__ import annotations

import argparse
import html
import os
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.dev_workflow import agent_workflow as wf  # noqa: E402

SCHEMA = "mirally.progress.page.v1"
DEFAULT_WORKDIR = ROOT / "output" / "agent-workflow"
HTML_NAME = "progress.html"
DATA_NAME = "progress.data.json"
PR_CACHE_NAME = "pr-cache.json"
TASKS_NAME = "page-tasks.json"
DECISIONS_NAME = "page-decisions.md"
MAX_FIELD = 400

# Config keys the page may surface (spec §3/§6: whitelist only, no credentials).
CONFIG_KEYS = ("concurrency", "max_auto_agent_in_flight", "max_auto_agent_per_day", "merge_queue_exempt")

STATUS_LABELS = {
    "inbox": "收件箱", "proposed": "AI 提案", "queued": "排队", "blocked": "阻塞",
    "specifying": "需求规格", "implementing": "实现", "verifying": "验证", "reviewing": "审查",
    "repairing": "修复", "awaiting_merge": "待批准合并", "merging": "合并中",
    "done": "已完成", "rejected": "已拒绝", "cancelled": "已取消",
}
STAGE_BY_STATUS = {
    "specifying": "需求", "implementing": "实现", "verifying": "验证", "reviewing": "审查",
    "repairing": "修复", "awaiting_merge": "待批准", "merging": "合并",
}
PENDING_GROUP, ACTIVE_GROUP, DONE_GROUP, CLOSED_GROUP = "待你决定", "进行中", "已完成", "已关闭"
ACTIVE_STATUSES = ("specifying", "implementing", "verifying", "reviewing", "repairing", "merging")
OPEN_VERDICT = {"PASS": "PASS", "FAIL": "FAIL", "待定": "待定"}
CI_KEYS = ("pass", "pending", "fail")


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def _esc(value: Any) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def _clip(value: Any, limit: int = MAX_FIELD) -> str:
    text = " ".join(str(value if value is not None else "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _rel_path(text: str, root: Path) -> str:
    """Replace absolute paths under root with repo-relative ones (never leak home paths)."""
    value = str(text)
    for base in (str(root).replace("\\", "/"), str(root)):
        value = value.replace(base + "/", "").replace(base + "\\", "")
    value = re.sub(r"[A-Za-z]:[\\/][^\s\"'<>]*", "<local path>", value)
    return value


def _verdict_of(text: str) -> str | None:
    """Read a verification/review conclusion from the header lines.

    Real files write ``- 结论：**PASS（验证门）**``, ``- 结论：**验证通过…**``
    or a bare ``结论：**PASS**``; anything unreadable degrades to ``None``.
    """
    line = next((ln for ln in text.splitlines() if "结论" in ln), None)
    if line is None:
        line = text
    upper = line.upper()
    if "FAIL" in upper or "不通过" in line or "未通过" in line:
        return "FAIL"
    if "PASS" in upper or "通过" in line:
        return "PASS"
    if "待定" in line or "未定" in line:
        return "待定"
    return None


def _first_lines(path: Path, count: int) -> str:
    try:
        with path.open(encoding="utf-8", errors="replace") as stream:
            return "".join(stream.readline() for _ in range(count))
    except OSError:
        return ""


# ---------------------------------------------------------------------------
# Collectors (all read-only)
# ---------------------------------------------------------------------------
def collect_pr(workdir: Path) -> dict[str, Any]:
    """Read the PR cache. Never calls the network here."""
    path = workdir / PR_CACHE_NAME
    if not path.is_file():
        return {"available": False, "generated_at": None, "pulls": {}, "note": "PR 缓存缺失，运行 --refresh-pr 获取"}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"available": False, "generated_at": None, "pulls": {}, "note": "PR 缓存无法解析"}
    pulls = {}
    for item in raw.get("pulls") or []:
        if not isinstance(item, dict) or "number" not in item:
            continue
        files = [f for f in item.get("files") or [] if isinstance(f, dict)]
        checks = {"pass": 0, "pending": 0, "fail": 0}
        for roll in item.get("statusCheckRollup") or []:
            if not isinstance(roll, dict):
                continue
            conclusion = str(roll.get("conclusion") or roll.get("state") or "").upper()
            if conclusion in {"SUCCESS", "NEUTRAL", "SKIPPED"}:
                checks["pass"] += 1
            elif conclusion in {"FAILURE", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED", "ERROR"}:
                checks["fail"] += 1
            else:
                checks["pending"] += 1
        pulls[str(item["number"])] = {
            "number": item["number"],
            "title": item.get("title"),
            "headRefName": item.get("headRefName"),
            "state": item.get("state"),
            "mergeable": item.get("mergeable"),
            "url": item.get("url"),
            "files": [{"path": f.get("path"), "additions": f.get("additions", 0), "deletions": f.get("deletions", 0)} for f in files],
            "checks": checks,
        }
    return {"available": True, "generated_at": raw.get("generated_at"), "pulls": pulls, "note": None}


def collect_tasks(workdir: Path) -> dict[str, Any]:
    path = workdir / TASKS_NAME
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def collect_decisions(workdir: Path) -> dict[str, list[str]]:
    """Optional hand-written ``dev-NNNN: text`` lines maintained by the captain."""
    path = workdir / DECISIONS_NAME
    result: dict[str, list[str]] = {}
    if not path.is_file():
        return result
    for line in _first_lines(path, 200).splitlines():
        match = re.match(r"\s*(dev-\d{4})\s*[:：]\s*(.+)", line)
        if match:
            result.setdefault(match.group(1), []).append(_clip(match.group(2)))
    return result


def collect_spec_summary(root: Path, spec_path: str | None) -> dict[str, Any]:
    """Title + first goal sentence from the spec header (first 60 lines only).

    Real specs use several shapes, all handled here:
    ``- 一句话目标：…``, ``**目标**：…``, and a ``### 目标`` heading followed by
    the first prose bullet. Anything unparsable degrades to ``None``.
    """
    if not spec_path:
        return {"available": False, "title": None, "goal": None, "decisions": 0, "path": None}
    path = (root / spec_path) if not Path(spec_path).is_absolute() else Path(spec_path)
    if not path.is_file():
        return {"available": False, "title": None, "goal": None, "decisions": 0, "path": spec_path}
    text = _first_lines(path, 60)
    title = None
    for line in text.splitlines():
        if line.startswith("# "):
            title = _clip(_clean_inline(line[2:].strip()))
            break
    goal = None
    inline = re.search(r"^-?\s*(?:一句话目标|\*\*目标\*\*)\s*[：:]\s*(.+)$", text, re.M)
    if inline:
        goal = _clip(_clean_inline(inline.group(1)))
    if goal is None:
        heading = re.search(r"^###+\s*目标\s*\n+(.+?)(?:\n#{2,}|\Z)", text, re.S | re.M)
        if heading:
            for line in heading.group(1).splitlines():
                cleaned = line.strip()
                if not cleaned or cleaned.startswith("|"):
                    continue
                cleaned = re.sub(r"^(?:\d+\.|[-*])\s*", "", cleaned)
                if cleaned:
                    goal = _clip(_clean_inline(cleaned))
                    break
    return {"available": True, "title": title, "goal": goal, "path": spec_path,
            "decisions": count_pending_decisions(path)}


def _clean_inline(text: str) -> str:
    """Drop markdown emphasis/code markers so the page shows plain text."""
    return re.sub(r"[`*]{1,3}", "", text).strip()


def count_pending_decisions(path: Path) -> int:
    """Count bullet items inside a spec's 待…决策 section (spec §4.4 rule 5)."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0
    match = re.search(r"^##+[^\n]*(?:待用户|待队长|待决定|待决策)[^\n]*\n(.*?)(?=^##+\s|\Z)", text, re.S | re.M)
    if not match:
        return 0
    return len(re.findall(r"^\s*(?:\d+\.|[-*])\s+\S", match.group(1), re.M))


_VERSION_SUFFIX = re.compile(r"-r(\d+)$")


def _latest_record(paths: list[Path]) -> Path | None:
    """Newest verification/review record by round then write time, not by name.

    Names such as ``dev-0033.md``, ``dev-0033-r2.md`` and ``dev-0033-r10.md`` sort
    lexicographically as ``r10 < r2``, so a plain ``sorted()`` would keep showing a
    stale PASS after a rework FAIL. Rounds are compared numerically first (an
    unsuffixed file counts as round 1) and the freshest write breaks ties, so a
    re-run that reuses a name still wins.
    """
    if not paths:
        return None

    def key(path: Path) -> tuple[int, float, str]:
        match = _VERSION_SUFFIX.search(path.stem)
        try:
            written = path.stat().st_mtime
        except OSError:
            written = 0.0
        return (int(match.group(1)) if match else 1, written, path.name)

    return max(paths, key=key)


def collect_verification(workdir: Path, point_id: str) -> dict[str, Any]:
    directory = workdir / "verification"
    if not directory.is_dir():
        return {"available": False, "verdict": None, "file": None}
    latest = _latest_record(list(directory.glob(f"{point_id}*.md")))
    if latest is None:
        return {"available": False, "verdict": None, "file": None}
    text = _first_lines(latest, 20)
    return {"available": True, "verdict": _verdict_of(text),
            "file": f"verification/{latest.name}", "note": None}


def collect_review(workdir: Path, point_id: str) -> dict[str, Any]:
    latest = _latest_record(list(workdir.glob(f"review-{point_id}*.md")))
    if latest is None:
        return {"available": False, "verdict": None, "file": None, "line": None}
    text = _first_lines(latest, 20)
    line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    return {"available": True, "verdict": _verdict_of(text), "file": latest.name,
            "line": _clip(line, 200) if line else None}


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------
def _change_summary(files: list[dict], title: str | None = None) -> dict[str, Any]:
    """What the PR actually changed: totals, busiest paths and, when known, its title."""
    if not files:
        return {"files": 0, "additions": 0, "deletions": 0, "top_paths": [], "paths": [],
                "title": _clip(title) if title else None, "missing": True}
    additions = sum(int(f.get("additions") or 0) for f in files)
    deletions = sum(int(f.get("deletions") or 0) for f in files)
    grouped: dict[str, list[int]] = {}
    for item in files:
        path = str(item.get("path") or "")
        top = path.split("/")[0] if "/" in path else "(root)"
        bucket = grouped.setdefault(top, [0, 0])
        bucket[0] += int(item.get("additions") or 0)
        bucket[1] += int(item.get("deletions") or 0)
    tops = sorted(grouped.items(), key=lambda kv: (-kv[1][0], kv[0]))[:3]
    ranked = sorted(files, key=lambda f: (-(int(f.get("additions") or 0) + int(f.get("deletions") or 0)),
                                          str(f.get("path") or "")))
    return {"files": len(files), "additions": additions, "deletions": deletions,
            "top_paths": [{"path": name, "additions": add, "deletions": dele} for name, (add, dele) in tops],
            "paths": [str(f.get("path")) for f in ranked[:3] if f.get("path")],
            "title": _clip(title) if title else None,
            "missing": False}


def merge_state(point: dict, pr_entry: dict | None) -> dict[str, Any]:
    """Classify an ``awaiting_merge`` point as Codex-mergeable or user-blocked.

    Merging is not a single decision. ``codex`` means the PR is open, GitHub says
    ``MERGEABLE``, CI reported at least one check and all of them passed, so the
    Codex merge gate can pick it up without a new user decision. ``user`` means the
    user still has to decide: the PR is missing from the cache, not mergeable, has
    failing/pending checks, or has no CI checks at all (a green mergeable bit alone
    is not evidence). ``None`` applies to every other status (the card then shows
    nothing extra).
    """
    if point.get("status") != "awaiting_merge":
        return {"state": None, "reason": None}
    pr = point.get("pr")
    if not pr:
        return {"state": "user", "reason": "未关联 PR"}
    if not pr_entry:
        return {"state": "user", "reason": "PR 不在缓存中"}
    checks = pr_entry.get("checks") or {}
    total = sum(int(checks.get(key) or 0) for key in CI_KEYS)
    if total == 0:
        # No checks means nobody has proved the head is safe to merge; a green
        # "mergeable" bit alone is not a substitute for CI.
        return {"state": "user", "reason": "无 CI 检查"}
    failing = int(checks.get("fail") or 0) + int(checks.get("pending") or 0)
    if failing:
        parts = [f'fail {checks.get("fail", 0)}' if checks.get("fail") else "",
                 f'pending {checks.get("pending", 0)}' if checks.get("pending") else ""]
        return {"state": "user", "reason": "检查未全绿：" + "/".join(p for p in parts if p)}
    if str(pr_entry.get("mergeable") or "").upper() != "MERGEABLE":
        return {"state": "user", "reason": f'PR 不可合并：{pr_entry.get("mergeable") or "未知"}'}
    return {"state": "codex", "reason": "可交给 Codex 合并（检查全绿、PR mergeable）"}


def derive_decisions(point: dict, spec: dict, review: dict, tasks: dict, manual: list[str],
                     merge: dict | None = None) -> list[str]:
    """Spec §4.4 priority order; the first hit wins, manual decisions are appended."""
    status = point.get("status")
    out: list[str] = []
    if status == "proposed":
        out.append("AI 提案待批准：promote 或 reject")
    elif status == "blocked":
        reason = point.get("blocked_reason") or "未知"
        detail = f"quota 重试 {point.get('quota_retries', 0)} 次" if reason == "quota" else str(reason)
        out.append(_clip(f"阻塞：{detail}"))
    elif status == "awaiting_merge":
        merge = merge or {"state": "user", "reason": None}
        target = f" PR #{point.get('pr')}" if point.get("pr") else ""
        if merge.get("state") == "codex":
            out.append(_clip(f"已审查通过，可交 Codex 合并{target}"))
        elif merge.get("reason"):
            out.append(_clip(f"等待用户批准合并{target}（{merge['reason']}）"))
        else:
            out.append(f"等待用户批准合并{target}")
    elif status == "reviewing" and review.get("verdict") == "FAIL":
        out.append(_clip(f"审查未通过：{review.get('line') or '见审查记录'}"))
    elif spec.get("decisions"):
        out.append(f"规格列有 {spec['decisions']} 项待决策（见 spec 链接）")
    elif status == "done":
        leftovers = [ln.strip() for ln in str(point.get("notes") or "").splitlines()
                     if re.search(r"遗留|未覆盖|TODO", ln)]
        out.extend(_clip(ln) for ln in leftovers[:2])
    out.extend(manual)
    return out


def build_model(*, state: dict, config: dict, summary: dict, issues: list[str], pr: dict,
                tasks: dict, decisions: dict, workdir: Path, root: Path, now: str,
                head: str | None) -> dict[str, Any]:
    points: list[dict[str, Any]] = []
    gaps = 0
    for point in state.get("points") or []:
        point_id = str(point.get("id") or "")
        spec = collect_spec_summary(root, point.get("spec_path"))
        verification = collect_verification(workdir, point_id)
        review = collect_review(workdir, point_id)
        pr_entry = pr["pulls"].get(str(point.get("pr"))) if point.get("pr") else None
        changes = _change_summary((pr_entry or {}).get("files") or [], (pr_entry or {}).get("title"))
        manual = decisions.get(point_id) or []
        merge = merge_state(point, pr_entry)
        derived = derive_decisions(point, spec, review, tasks, manual, merge)
        task_map = point.get("task_ids") or {}
        assignment = tasks.get(point_id) if isinstance(tasks.get(point_id), dict) else {}
        card = {
            "id": point_id,
            "title": _clip(point.get("title")),
            "source": point.get("source"),
            "risk": point.get("risk"),
            "auto_started": bool(point.get("auto_started")),
            "status": point.get("status"),
            "status_label": STATUS_LABELS.get(point.get("status"), str(point.get("status"))),
            "stage": STAGE_BY_STATUS.get(str(point.get("status"))),
            "purpose": spec.get("goal") or spec.get("title"),
            "spec_path": spec.get("path"),
            "changes": changes,
            "verification": {
                "verdict": verification.get("verdict"),
                "file": verification.get("file"),
                "ci": (pr_entry or {}).get("checks"),
            },
            "decisions": derived,
            "merge": merge,
            "pr": point.get("pr"),
            "pr_url": (pr_entry or {}).get("url"),
            "mergeable": (pr_entry or {}).get("mergeable"),
            "branch": point.get("branch"),
            "head_sha": (point.get("head_sha") or "")[:8] or None,
            "owner": assignment.get("role"),
            "task": assignment.get("task"),
            "task_ids": {k: v for k, v in task_map.items() if v},
            "updated_at": point.get("updated_at"),
            "merged_at": point.get("merged_at"),
        }
        for field in ("purpose", "changes", "verification", "owner", "task"):
            value = card[field]
            if value in (None, "", {}) or (field == "changes" and value.get("missing")):
                gaps += 1
        if not verification.get("available"):
            gaps += 1
        points.append(card)

    groups: dict[str, dict[str, list[dict]]] = {
        PENDING_GROUP: {"user": [], "agent": []},
        ACTIVE_GROUP: {"user": [], "agent": []},
        DONE_GROUP: {"user": [], "agent": []},
        CLOSED_GROUP: {"user": [], "agent": []},
    }
    for card in points:
        status = card["status"]
        if status in {"blocked", "proposed", "awaiting_merge"} or card["decisions"]:
            target = PENDING_GROUP
        elif status in ACTIVE_STATUSES:
            target = ACTIVE_GROUP
        elif status == "done":
            target = DONE_GROUP
        elif status in {"rejected", "cancelled"}:
            target = CLOSED_GROUP
        else:
            # queued / inbox are waiting for a slot: surface them with the work
            # that is already moving rather than silently dropping them.
            target = ACTIVE_GROUP
        groups[target]["user" if card["source"] == "user" else "agent"].append(card)
    groups[DONE_GROUP]["user"].sort(key=lambda c: c.get("merged_at") or "", reverse=True)
    groups[DONE_GROUP]["agent"].sort(key=lambda c: c.get("merged_at") or "", reverse=True)

    config_view = {key: config.get(key) for key in CONFIG_KEYS}
    blocked_points = [c["id"] for c in points if c["status"] == "blocked"]
    return {
        "schema": SCHEMA,
        "generated_at": now,
        "state_updated_at": state.get("updated_at"),
        "head": head,
        "workdir": str(workdir),
        "header": {
            "concurrency": summary.get("concurrency"),
            "active_dev": len(summary.get("active") or []),
            "merge_waiting": len(summary.get("merge_queue") or []),
            "load": summary.get("load"),
            "free_slots": summary.get("free_slots"),
            "ai_in_flight": len(summary.get("agent_auto_reserved") or []),
            "user_pending": len(summary.get("user_pending") or []),
            "agent_proposed": len(summary.get("agent_proposed") or []),
            "merge_head": (summary.get("merge_head") or {}).get("id"),
            "blocked": len(blocked_points),
            "blocked_points": blocked_points,
            "paused": bool(summary.get("paused")),
        },
        "config": config_view,
        "validate_issues": issues,
        "pr_cache": {"available": pr["available"], "generated_at": pr.get("generated_at"), "note": pr.get("note")},
        "summary": {"total": len(points), "data_gaps": gaps},
        "groups": groups,
        "external_prs": [p for key, p in sorted(pr["pulls"].items(), key=lambda kv: int(kv[0]))
                         if key not in {str(c["pr"]) for c in points if c["pr"]}],
    }


# ---------------------------------------------------------------------------
# Renderer
# ---------------------------------------------------------------------------
CSS = """
:root{color-scheme:light dark;--bg:#f6f7f8;--card:#fff;--ink:#1c2126;--muted:#5b6670;--line:#d9dee3;--accent:#2b6cb0;--warn:#b45309;--ok:#1f7a3d;--bad:#b32424}
@media (prefers-color-scheme:dark){:root{--bg:#14181c;--card:#1b2026;--ink:#e6e9ec;--muted:#9aa4ae;--line:#2c343b;--accent:#71a7db}}
*{box-sizing:border-box}
body{margin:0;padding:24px;background:var(--bg);color:var(--ink);font:14px/1.6 system-ui,"Segoe UI","Microsoft YaHei",sans-serif}
h1{font-size:20px;margin:0 0 4px}h2{font-size:16px;margin:28px 0 10px}
.notice{color:var(--muted);font-size:12px;margin-bottom:18px}
.strip{display:flex;flex-wrap:wrap;gap:10px;margin-bottom:8px}
.stat{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:8px 12px;min-width:104px}
.stat b{display:block;font-size:18px}.stat span{color:var(--muted);font-size:12px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px;margin:10px 0}
.card h3{margin:0 0 6px;font-size:15px}
.badge{display:inline-block;font-size:11px;border:1px solid var(--line);border-radius:999px;padding:1px 8px;margin-left:6px;color:var(--muted)}
.badge.warn{color:var(--warn);border-color:var(--warn)}.badge.ok{color:var(--ok);border-color:var(--ok)}.badge.bad{color:var(--bad);border-color:var(--bad)}
dl{display:grid;grid-template-columns:104px 1fr;gap:4px 12px;margin:0}
dt{color:var(--muted);font-size:12px}dd{margin:0;overflow-wrap:anywhere}
.empty{color:var(--muted);font-style:italic;padding:6px 2px}
details>summary{cursor:pointer;color:var(--accent)}
a{color:var(--accent)}
code{font-family:ui-monospace,Consolas,monospace;font-size:12px}
"""


def _stat(label: str, value: Any) -> str:
    return f'<div class="stat"><b>{_esc(value if value is not None else "—")}</b><span>{_esc(label)}</span></div>'


def _render_card(card: dict) -> str:
    checks = card["verification"].get("ci")
    ci = "—" if not checks else f'pass {checks["pass"]} / pending {checks["pending"]} / fail {checks["fail"]}'
    verdict = card["verification"].get("verdict") or "—"
    vfile = card["verification"].get("file")
    verdict_html = f'<span class="badge {"ok" if verdict == "PASS" else "bad" if verdict == "FAIL" else ""}">{_esc(verdict)}</span>'
    if vfile:
        verdict_html += f' <a href="{_esc(vfile)}">{_esc(vfile)}</a>'
    changes = card["changes"]
    if changes.get("missing"):
        change_text = "—"
    else:
        tops = "；".join(f'{t["path"]} +{t["additions"]}/−{t["deletions"]}' for t in changes["top_paths"])
        paths = "、".join(changes.get("paths") or [])
        change_text = f'{changes["files"]} 个文件 +{changes["additions"]}/−{changes["deletions"]}' + (f"（{tops}）" if tops else "")
        if changes.get("title"):
            change_text += f" · {changes['title']}"
        if paths:
            change_text += f" · 主要文件 {paths}"
    merge_html = ""
    merge_state_value = (card.get("merge") or {}).get("state")
    if merge_state_value == "codex":
        merge_html = ' <span class="badge ok">可交 Codex 合并</span>'
    elif merge_state_value == "user":
        merge_html = ' <span class="badge warn">待用户决定</span>'
    decisions = card["decisions"]
    decision_html = "<br>".join(_esc(d) for d in decisions) if decisions else "—"
    pr_html = "—"
    if card["pr"]:
        label = f'#{card["pr"]}'
        pr_html = f'<a href="{_esc(card["pr_url"])}">{_esc(label)}</a>' if card["pr_url"] else _esc(label)
        if card["mergeable"]:
            pr_html += f' <span class="badge">{_esc(card["mergeable"])}</span>'
    tasks = ", ".join(f"{k}={v}" for k, v in (card["task_ids"] or {}).items()) or "—"
    badges = [f'<span class="badge">{_esc("用户点" if card["source"] == "user" else "AI 点")}</span>',
              f'<span class="badge">{_esc(card["risk"])}</span>',
              f'<span class="badge {"warn" if card["decisions"] else ""}">{_esc(card["status_label"])}</span>{merge_html}']
    if card["auto_started"]:
        badges.append('<span class="badge">auto-start</span>')
    return (
        f'<div class="card"><h3>{_esc(card["id"])} · {_esc(card["title"])}{"".join(badges)}</h3><dl>'
        f'<dt>目的</dt><dd>{_esc(card["purpose"] or "—")}</dd>'
        f'<dt>当前进度</dt><dd>{_esc(card["status_label"])}{"（" + _esc(card["stage"]) + "）" if card["stage"] else ""} · 起于 {_esc(card["updated_at"] or "—")}</dd>'
        f'<dt>主要改动</dt><dd>{_esc(change_text)}</dd>'
        f'<dt>验证情况</dt><dd>{verdict_html} · CI {_esc(ci)}</dd>'
        f'<dt>待决定问题</dt><dd>{decision_html}</dd>'
        f'<dt>PR / 分支</dt><dd>{pr_html} · <code>{_esc(card["branch"] or "—")}</code> · head <code>{_esc(card["head_sha"] or "—")}</code></dd>'
        f'<dt>负责人 / 任务</dt><dd>{_esc(card["owner"] or "—")} · {_esc(tasks)}</dd>'
        f'<dt>最后更新</dt><dd>{_esc(card["updated_at"] or "—")}</dd>'
        f'</dl></div>'
    )


def render_html(model: dict) -> str:
    header = model["header"]
    stats = "".join([
        _stat("并发容量", header["concurrency"]), _stat("进行中", header["active_dev"]),
        _stat("待合并", header["merge_waiting"]), _stat("负载", header["load"]),
        _stat("空闲槽位", header["free_slots"]), _stat("AI 在飞", header["ai_in_flight"]),
        _stat("用户待开发", header["user_pending"]), _stat("AI 提案", header["agent_proposed"]),
        _stat("阻塞", header["blocked"]), _stat("数据缺口", model["summary"]["data_gaps"]),
    ])
    issues = "".join(f"<li>{_esc(i)}</li>" for i in model["validate_issues"]) or "<li>无</li>"
    prnote = model["pr_cache"]
    pr_text = ("PR 缓存 " + _esc(prnote["generated_at"])) if prnote["available"] else _esc(prnote["note"])
    sections = []
    for group, by_source in model["groups"].items():
        blocks = []
        for source, label in (("user", "用户点"), ("agent", "AI 点")):
            cards = by_source[source]
            body = "".join(_render_card(c) for c in cards) if cards else '<div class="empty">（暂无）</div>'
            blocks.append(f"<h3>{_esc(label)}（{len(cards)}）</h3>{body}")
        collapsed = group in (DONE_GROUP, CLOSED_GROUP)
        inner = "".join(blocks)
        if collapsed:
            total = len(by_source["user"]) + len(by_source["agent"])
            sections.append(f'<details><summary>{_esc(group)}（{total}）</summary>{inner}</details>')
        else:
            sections.append(f"<h2>{_esc(group)}</h2>{inner}")
    external = model["external_prs"]
    ext_html = ""
    if external:
        rows = "".join(
            f'<li>#{_esc(p["number"])} {_esc(p.get("title") or "")} — {_esc(p.get("state") or "")}</li>' for p in external)
        ext_html = f"<h2>外部 PR</h2><ul>{rows}</ul>"
    return (
        "<!doctype html>\n<html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        f"<title>Mirally 开发进度</title><style>{CSS}</style></head><body>"
        "<h1>Mirally 开发进度</h1>"
        '<p class="notice">只读生成物，可随时删除重建；状态以 <code>state.json</code> 为准。'
        f"生成于 {_esc(model['generated_at'])} · state 更新 {_esc(model['state_updated_at'] or '—')} · "
        f"HEAD <code>{_esc(model['head'] or '—')}</code> · schema {_esc(model['schema'])}</p>"
        f'<div class="strip">{stats}</div>'
        f'<p class="notice">路由与容量：concurrency={_esc(model["config"].get("concurrency"))} · '
        f'max_auto_agent_in_flight={_esc(model["config"].get("max_auto_agent_in_flight"))} · '
        f'max_auto_agent_per_day={_esc(model["config"].get("max_auto_agent_per_day"))} · '
        f'merge_queue_exempt={_esc(model["config"].get("merge_queue_exempt"))} · {pr_text}</p>'
        f'<details><summary>状态校验（{len(model["validate_issues"])} 条）</summary><ul>{issues}</ul></details>'
        f'{"".join(sections)}{ext_html}'
        "</body></html>\n"
    )


# ---------------------------------------------------------------------------
# PR cache refresh (the only networked step, explicit opt-in)
# ---------------------------------------------------------------------------
GH_FIELDS = "number,title,headRefName,state,mergeable,url,files,statusCheckRollup"


def refresh_pr_cache(workdir: Path, *, run=subprocess.run) -> dict[str, Any]:
    gh = shutil.which("gh")
    if gh is None:
        return {"ok": False, "code": 2, "message": "gh CLI 不可用：保留旧缓存（如有）"}
    command = [gh, "pr", "list", "--state", "all", "--limit", "100", "--json", GH_FIELDS]
    try:
        # gh prints UTF-8 JSON; on Windows a piped child defaults to the ANSI code
        # page (cp936/GBK, cp1252), which mangles Chinese PR titles or raises on
        # decode. Force UTF-8 and never let one bad byte discard the whole cache.
        completed = run(command, capture_output=True, text=True, encoding="utf-8",
                        errors="replace", timeout=120, check=False)
    except (OSError, subprocess.SubprocessError) as error:
        return {"ok": False, "code": 2, "message": f"gh 调用失败：{type(error).__name__}"}
    if completed.returncode != 0:
        return {"ok": False, "code": 2, "message": f"gh 返回 {completed.returncode}：保留旧缓存"}
    try:
        pulls = json.loads(completed.stdout or "null")
    except (json.JSONDecodeError, TypeError):
        return {"ok": False, "code": 2, "message": "gh 输出无法解析：保留旧缓存"}
    if not isinstance(pulls, list):
        return {"ok": False, "code": 2, "message": "gh 输出不是 PR 数组：保留旧缓存"}
    payload = {"generated_at": wf.iso(wf.utcnow()), "pulls": pulls}
    (workdir / PR_CACHE_NAME).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"ok": True, "code": 0, "message": f"已刷新 {len(pulls)} 个 PR"}


def _git_head(root: Path, *, run=subprocess.run) -> str | None:
    try:
        completed = run(["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
                        capture_output=True, text=True, encoding="utf-8", errors="replace",
                        timeout=20, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return completed.stdout.strip() or None if completed.returncode == 0 else None


def build_page(*, workdir: Path, root: Path | None = None, now: str | None = None,
               head: str | None = None) -> dict[str, Any]:
    """Collect + model + render. Returns {"model", "html", "data"} without writing."""
    root = root or ROOT
    state = wf.load_state(workdir / "state.json")
    config = wf.load_config(workdir / "config.json")
    summary = wf.build_summary(state, config)
    issues = wf.validate_state(state, config)
    pr = collect_pr(workdir)
    tasks = collect_tasks(workdir)
    decisions = collect_decisions(workdir)
    stamp = now or wf.iso(wf.utcnow())
    model = build_model(state=state, config=config, summary=summary, issues=issues, pr=pr,
                        tasks=tasks, decisions=decisions, workdir=workdir, root=root,
                        now=stamp, head=head)
    return {"model": model, "html": render_html(model), "data": model}


def fixture_root(root: Path | None = None) -> Path:
    """Where test fixtures may create workdirs.

    MIRALLY_WORKFLOW_FIXTURE_ROOT lets a sandboxed checkout (where writes are only
    permitted under another root) redirect fixtures; otherwise the gitignored
    <repo>/output directory is used.
    """
    root = (root or ROOT).resolve()
    override = os.environ.get("MIRALLY_WORKFLOW_FIXTURE_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    return root / "output"


class _SafeArgumentParser(argparse.ArgumentParser):
    """Argparse help/usage must survive a non-UTF-8 console as well."""

    def _print_message(self, message: str, file=None) -> None:  # noqa: D401 - argparse hook
        if message:
            wf._write_text(file or sys.stderr, message)


def main(argv: list[str] | None = None, *, run=subprocess.run) -> int:
    parser = _SafeArgumentParser(description="生成 Mirally 开发进度页（只读）")
    parser.add_argument("--workdir", default=str(DEFAULT_WORKDIR))
    parser.add_argument("--offline", action="store_true", help="仅使用缓存的 PR 数据（默认行为）")
    parser.add_argument("--refresh-pr", action="store_true", help="调用 gh 刷新 PR 缓存（唯一联网步骤）")
    parser.add_argument("--out", default=None, help="HTML 输出路径")
    parser.add_argument("--data", default=None, help="数据 JSON 输出路径")
    parser.add_argument("--now", default=None, help="注入生成时间（用于确定性测试）")
    args = parser.parse_args(argv)

    workdir = Path(args.workdir).expanduser()
    if not workdir.is_absolute():
        workdir = (Path.cwd() / workdir)
    workdir = workdir.resolve()
    root = ROOT.resolve()
    # Explicit boundary: the generator only writes inside the repository (or the
    # configured fixture root, which exists solely so sandboxed checkouts can run
    # the tests).
    allowed = [root, fixture_root(root)]
    inside = any(workdir == base or base in workdir.parents for base in allowed)
    if not inside:
        wf._write_text(sys.stderr, f"--workdir 必须位于仓库内：{workdir}\n")
        return 2
    if not workdir.is_dir():
        wf._write_text(sys.stderr, f"--workdir 不存在：{workdir}\n")
        return 2
    if args.refresh_pr:
        outcome = refresh_pr_cache(workdir, run=run)
        wf._write_text(sys.stderr, outcome["message"] + "\n")
        if not outcome["ok"]:
            return outcome["code"]

    page = build_page(workdir=workdir, root=root, now=args.now, head=_git_head(root, run=run))
    html_path = Path(args.out) if args.out else workdir / HTML_NAME
    data_path = Path(args.data) if args.data else workdir / DATA_NAME
    html_path.write_text(page["html"], encoding="utf-8")
    data_path.write_text(json.dumps(page["data"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    model = page["model"]
    wf._write_text(sys.stdout, f"已生成 {html_path}\n")
    wf._write_text(sys.stdout, f"  开发点 {model['summary']['total']} 个 · 数据缺口 {model['summary']['data_gaps']} 处 · "
                               f"校验问题 {len(model['validate_issues'])} 条 · PR 缓存 {'有' if model['pr_cache']['available'] else '无'}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
