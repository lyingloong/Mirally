"""Explicitly clean stale sub-directories under ``output/tmp/`` (dev-0029, seed-coverage spec A).

Verification and test runs leave scratch trees behind. Nothing here runs
automatically: the default is a **dry run** that only reports what it would
delete, and ``--apply`` is required to actually remove anything.

Scope is deliberately narrow:

* only direct **sub-directories** of ``output/tmp/`` are candidates;
* loose files directly under ``output/tmp/`` are reported and never deleted;
* nothing outside ``output/tmp/`` is inspected or removed;
* a directory younger than ``--days`` (default 7) is kept;
* symlinks are never followed or deleted - they are reported and skipped, so a
  link cannot be used to reach outside the temporary root;
* a path that resolves outside ``output/tmp/`` is refused.

    python tools/cleanup_local_artifacts.py             # dry run (default)
    python tools/cleanup_local_artifacts.py --apply     # actually delete
    python tools/cleanup_local_artifacts.py --days 3    # change the age floor
    python tools/cleanup_local_artifacts.py --root <dir>  # only if inside a repo

Exit codes: 0 clean or successful run, 1 refused / unsafe input, 2 bad arguments.
"""

from __future__ import annotations

import argparse
import os
import shutil
import stat
import sys
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TMP_RELATIVE = Path("output") / "tmp"
DEFAULT_DAYS = 7
DAY_SECONDS = 86_400


@dataclass(frozen=True)
class Entry:
    """One item found directly under the temporary root."""

    name: str
    path: Path
    kind: str  # "dir" | "file" | "symlink" | "other"
    age_days: float
    eligible: bool
    reason: str


def temporary_root(repo_root: Path) -> Path:
    return repo_root / TMP_RELATIVE


def _inside(path: Path, base: Path) -> bool:
    """True when ``path`` is ``base`` itself or lies beneath it (resolved)."""
    try:
        resolved, base_resolved = path.resolve(), base.resolve()
    except OSError:
        return False
    return resolved == base_resolved or base_resolved in resolved.parents


def _is_link(path: Path) -> bool:
    """True for symlinks *and* Windows junctions (reparse points).

    ``Path.is_symlink()`` reports False for a directory junction on Windows, so
    a junction would otherwise pass as an ordinary directory and be deleted.
    """
    try:
        if path.is_symlink():
            return True
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
    except OSError:
        return False
    return bool(attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def audit(root: Path, *, days: float = DEFAULT_DAYS, now: float | None = None,
          repo_root: Path | None = None) -> list[Entry]:
    """Return every direct child of ``root`` with its eligibility.

    ``root`` must be the ``output/tmp`` directory; callers that pass a custom
    directory are still held to that shape so a stray ``--root`` cannot turn
    this into a general-purpose deleter.
    """
    stamp = time.time() if now is None else now
    if not root.is_dir():
        return []
    base = repo_root or ROOT
    entries: list[Entry] = []
    for child in sorted(root.iterdir(), key=lambda item: item.name):
        age = (stamp - child.lstat().st_mtime) / DAY_SECONDS
        if _is_link(child):
            entries.append(Entry(child.name, child, "symlink", age, False,
                                 "符号链接/目录联接：不跟随、不删除"))
            continue
        if child.is_dir():
            if not _inside(child, base / TMP_RELATIVE):
                entries.append(Entry(child.name, child, "dir", age, False,
                                     "解析后位于 output/tmp 之外：拒绝"))
                continue
            if age < days:
                entries.append(Entry(child.name, child, "dir", age, False,
                                     f"未超过 {days:g} 天：保留"))
                continue
            entries.append(Entry(child.name, child, "dir", age, True, f"超过 {days:g} 天"))
            continue
        if child.is_file():
            entries.append(Entry(child.name, child, "file", age, False,
                                 "output/tmp 根下的散落文件：只报告，不删除"))
            continue
        entries.append(Entry(child.name, child, "other", age, False, "非普通文件/目录：跳过"))
    return entries


def remove(entry: Entry, *, root: Path, repo_root: Path | None = None) -> None:
    """Delete one eligible directory, re-checking the boundary right before acting."""
    base = repo_root or ROOT
    if not entry.eligible or entry.kind != "dir":
        raise ValueError(f"refusing to remove ineligible entry {entry.name}")
    if _is_link(entry.path) or not _inside(entry.path, base / TMP_RELATIVE):
        raise ValueError(f"refusing to remove {entry.name}: outside output/tmp")
    shutil.rmtree(entry.path)


def _human_age(age: float) -> str:
    return f"{age:.1f} 天" if age >= 0 else "时间戳在未来"


def format_report(entries: list[Entry], *, days: float, applied: bool, removed: int) -> str:
    lines: list[str] = []
    candidates = [entry for entry in entries if entry.eligible]
    files = [entry for entry in entries if entry.kind == "file"]
    skipped = [entry for entry in entries if not entry.eligible and entry.kind != "file"]
    mode = "已删除" if applied else "将删除（dry-run，未改动任何文件）"
    if not entries:
        return "output/tmp 下没有任何条目。"
    lines.append(f"扫描 output/tmp：共 {len(entries)} 项，过期阈值 {days:g} 天。")
    lines.append(f"{mode}：{len(candidates)} 个目录" + (f"，实际已删除 {removed} 个" if applied else ""))
    for entry in candidates:
        lines.append(f"  - {entry.name}/  ({_human_age(entry.age_days)}，{entry.reason})")
    if files:
        lines.append(f"保留（散落文件，只报告）：{len(files)} 个")
        for entry in files:
            lines.append(f"  · {entry.name}  ({_human_age(entry.age_days)})")
    if skipped:
        lines.append(f"保留（其它原因）：{len(skipped)} 项")
        for entry in skipped:
            lines.append(f"  · {entry.name}  ({entry.kind}，{_human_age(entry.age_days)}，{entry.reason})")
    return "\n".join(lines)


def run(*, repo_root: Path | None = None, days: float = DEFAULT_DAYS, apply: bool = False,
        now: float | None = None, root: Path | None = None,
        require_default_root: bool = True) -> tuple[int, str]:
    """Audit (and optionally delete) stale scratch directories. Returns (exit code, report).

    ``root`` may point at any directory for tests, but the boundary check always
    compares it against ``output/tmp`` of the *given* repository root, so a
    custom root can never widen the deletion surface beyond a tmp directory.
    """
    base = repo_root or ROOT
    target = (root if root is not None else temporary_root(base)).resolve()
    expected = (base / TMP_RELATIVE).resolve()
    if require_default_root and target != expected:
        return 1, f"拒绝：{target} 不是 {expected}。"
    if not _inside(target, expected):
        return 1, f"拒绝：{target} 不在 output/tmp 之内。"
    entries = audit(target, days=days, now=now, repo_root=base)
    removed = 0
    if apply:
        for entry in entries:
            if not entry.eligible:
                continue
            try:
                remove(entry, root=target, repo_root=base)
                removed += 1
            except (OSError, ValueError) as error:
                return 1, f"删除 {entry.name} 失败：{type(error).__name__}: {error}"
    return 0, format_report(entries, days=days, applied=apply, removed=removed)


def _console_safe() -> None:
    """Make reports printable on non-UTF-8 consoles (GBK, cp1252, ascii).

    Every message this tool prints is Chinese, so on a legacy code page the
    final ``print`` would raise ``UnicodeEncodeError`` *after* the work is done
    and turn a successful run into exit code 1. Unencodable characters degrade
    to ``?`` instead; the deletion logic itself is unaffected.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            pass


def main(argv: list[str] | None = None, *, now: float | None = None) -> int:
    _console_safe()
    parser = argparse.ArgumentParser(description="显式清理 output/tmp 下的过期临时目录（默认 dry-run）")
    parser.add_argument("--apply", action="store_true", help="真正删除；不指定时只打印候选")
    parser.add_argument("--days", type=float, default=DEFAULT_DAYS,
                        help=f"目录需超过的天数，默认 {DEFAULT_DAYS}")
    parser.add_argument("--root", default=None, help="临时根（默认仓库内 output/tmp，须位于其中）")
    args = parser.parse_args(argv)
    if args.days < 0:
        print("--days 必须 >= 0", file=sys.stderr)
        return 2
    root = None
    repo_root = None
    if args.root:
        candidate = Path(args.root).expanduser()
        if not candidate.is_absolute():
            candidate = Path.cwd() / candidate
        root = candidate.resolve()
        # A custom root keeps working only when it still ends in output/tmp, and
        # the boundary is then evaluated against that root's own repository.
        if root.name != TMP_RELATIVE.name or root.parent.name != TMP_RELATIVE.parent.name:
            print(f"拒绝：--root 必须指向某仓库的 output/tmp，收到 {root}", file=sys.stderr)
            return 1
        repo_root = root.parent.parent
    code, report = run(days=args.days, apply=args.apply, root=root, repo_root=repo_root,
                       require_default_root=root is None, now=now)
    print(report, file=sys.stderr if code else sys.stdout)
    return code


if __name__ == "__main__":
    sys.exit(main())
