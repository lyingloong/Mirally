"""Explicitly clean stale sub-directories under ``output/tmp/`` (dev-0029, seed-coverage spec A).

Verification and test runs leave scratch trees behind. Nothing here runs
automatically: the default is a **dry run** that only reports what it would
delete, and ``--apply`` is required to actually remove anything.

Scope is deliberately narrow:

* only direct **sub-directories** of ``output/tmp/`` are candidates;
* loose files directly under ``output/tmp/`` are reported and never deleted;
* nothing outside ``output/tmp/`` is inspected or removed;
* a directory younger than ``--days`` (default 7) is kept; age is the newest
  mtime in the whole run directory, so an old tool directory holding a fresh
  run is not treated as stale;
* symlinks are never followed or deleted - they are reported and skipped, so a
  link cannot be used to reach outside the temporary root;
* ``output/tmp`` and every ancestor up to the Git root must be real
  directories; a symlink/junction in that chain is refused, not followed;
* the repository root is verified with ``git rev-parse --show-toplevel``; a
  non-Git directory is refused even when it contains ``output/tmp``.

    python tools/check_cleanup_local_artifacts.py             # dry run (default)
    python tools/check_cleanup_local_artifacts.py --apply     # actually delete
    python tools/check_cleanup_local_artifacts.py --days 3    # change the age floor
    python tools/check_cleanup_local_artifacts.py --root <dir>  # only if inside a repo

Exit codes: 0 clean or successful run, 1 refused / unsafe input, 2 bad arguments.
``--days`` must be finite and >= 0: ``nan``/``inf`` would otherwise make every
comparison False and silently disable the age floor.
"""

from __future__ import annotations

import argparse
import math
import os
import shutil
import stat
import subprocess
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


def valid_days(days: float) -> bool:
    """True when ``days`` is usable as an age floor.

    ``argparse``'s ``float`` accepts ``nan``/``inf``. With ``nan``,
    ``nan < 0`` is False and ``age < nan`` is always False, so every directory
    would be "past" the threshold; ``inf`` would keep everything forever. Both
    are configuration mistakes, not thresholds.
    """
    return math.isfinite(days) and days >= 0


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


def git_worktree_root(path: Path) -> Path | None:
    """Repository root reported by Git for ``path``, or None outside a worktree."""
    try:
        completed = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    reported = completed.stdout.strip()
    if not reported:
        return None
    try:
        return Path(reported).resolve()
    except OSError:
        return None


def _long_path(path: Path) -> Path:
    """Expand Windows 8.3 short components without following reparse points.

    GitHub runners keep fixtures under ``C:\\Users\\RUNNER~1\\AppData\\...``
    while ``Path.resolve()`` reports the long form. Comparing a short lexical
    path with a long trusted root would make an in-repo path look like it left
    the repository, so normalize names first; junctions stay detectable because
    ``GetLongPathNameW`` does not resolve them.
    """
    if os.name != "nt":
        return path
    try:
        import ctypes

        buffer = ctypes.create_unicode_buffer(32_768)
        length = ctypes.windll.kernel32.GetLongPathNameW(str(path), buffer, len(buffer))
    except (AttributeError, OSError, ValueError):
        return path
    if 0 < length < len(buffer):
        return Path(buffer.value)
    return path


def _reparse_components(base: Path, path: Path) -> list[Path]:
    """Existing components between ``base`` and ``path`` that are links.

    Only components at or below the trusted Git root are inspected; ancestors
    above ``base`` (runner temp parents, drive roots, short-name aliases) are
    never treated as attacks. A path that cannot be related to ``base`` is left
    to the separate boundary checks instead of being reported as a link.
    """
    base_abs, path_abs = _long_path(base.absolute()), _long_path(path.absolute())
    try:
        relative = path_abs.relative_to(base_abs)
    except ValueError:
        try:
            relative = Path(os.path.normcase(str(path_abs))).relative_to(
                Path(os.path.normcase(str(base_abs))))
        except ValueError:
            return []
    found: list[Path] = []
    current = base_abs
    for part in relative.parts:
        current = current / part
        if _is_link(current):
            found.append(current)
    return found


def _newest_mtime(path: Path) -> float:
    """Newest mtime in a run tree, without following links.

    POSIX does not update a directory mtime when files below it are written, so
    judging staleness by the top directory alone could delete an active run.
    """
    newest = path.lstat().st_mtime
    pending = [path]
    while pending:
        current = pending.pop()
        try:
            children = list(current.iterdir())
        except OSError:
            continue
        for child in children:
            if _is_link(child):
                continue
            try:
                info = child.lstat()
            except OSError:
                continue
            newest = max(newest, info.st_mtime)
            if stat.S_ISDIR(info.st_mode):
                pending.append(child)
    return newest


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
    base = (repo_root or ROOT).resolve()
    entries: list[Entry] = []
    for child in sorted(root.iterdir(), key=lambda item: item.name):
        try:
            lstat = child.lstat()
        except OSError:
            continue
        if _is_link(child):
            age = (stamp - lstat.st_mtime) / DAY_SECONDS
            entries.append(Entry(child.name, child, "symlink", age, False,
                                 "符号链接/目录联接：不跟随、不删除"))
            continue
        if child.is_dir():
            # A run directory is only stale when *everything* below it is old:
            # an old tool dir holding a fresh run must survive.
            age = (stamp - _newest_mtime(child)) / DAY_SECONDS
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
        age = (stamp - lstat.st_mtime) / DAY_SECONDS
        if child.is_file():
            entries.append(Entry(child.name, child, "file", age, False,
                                 "output/tmp 根下的散落文件：只报告，不删除"))
            continue
        entries.append(Entry(child.name, child, "other", age, False, "非普通文件/目录：跳过"))
    return entries


def remove(entry: Entry, *, root: Path, repo_root: Path | None = None) -> None:
    """Delete one eligible directory, re-checking the boundary right before acting."""
    base = (repo_root or ROOT).resolve()
    if not entry.eligible or entry.kind != "dir":
        raise ValueError(f"refusing to remove ineligible entry {entry.name}")
    if (_is_link(entry.path) or _reparse_components(base, entry.path)
            or not _inside(entry.path, base / TMP_RELATIVE)):
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
    The repository root itself must be a real Git worktree, and no component
    between it and the temporary root may be a symlink/junction.
    """
    base = (repo_root or ROOT).resolve()
    if not valid_days(days):
        return 2, f"拒绝：--days 必须是有限且 >= 0 的数，收到 {days!r}。"
    trusted = git_worktree_root(base)
    if trusted is None or trusted != base:
        return 1, f"拒绝：{base} 不是可信的 Git 工作树根；output/tmp 清理仅限 Git 仓库。"
    expected = base / TMP_RELATIVE
    target = expected if root is None else Path(root).absolute()
    if require_default_root and target.resolve() != expected.resolve():
        return 1, f"拒绝：{target} 不是 {expected}。"
    if not _inside(target, expected):
        return 1, f"拒绝：{target} 不在 output/tmp 之内。"
    linked = _reparse_components(base, expected)
    if root is not None:
        linked += _reparse_components(expected, target)
    if linked:
        return 1, (f"拒绝：output/tmp 或其祖先为符号链接/目录联接（{linked[0]}）；"
                   "不跟随、不删除。")
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


def main(argv: list[str] | None = None, *, now: float | None = None,
         repo_root: Path | None = None) -> int:
    """CLI entry point.

    ``repo_root`` is an explicit injection point for tests/runners: when set,
    the repository boundary is evaluated against it instead of deriving it from
    ``--root`` or the process cwd.
    """
    _console_safe()
    parser = argparse.ArgumentParser(description="显式清理 output/tmp 下的过期临时目录（默认 dry-run）")
    parser.add_argument("--apply", action="store_true", help="真正删除；不指定时只打印候选")
    parser.add_argument("--days", type=float, default=DEFAULT_DAYS,
                        help=f"目录需超过的天数，默认 {DEFAULT_DAYS}")
    parser.add_argument("--root", default=None, help="临时根（默认仓库内 output/tmp，须位于其中）")
    args = parser.parse_args(argv)
    if not valid_days(args.days):
        print("--days 必须是有限且 >= 0 的数", file=sys.stderr)
        return 2
    root = None
    trusted_root = Path(repo_root).absolute() if repo_root is not None else None
    if args.root:
        candidate = Path(args.root).expanduser()
        if not candidate.is_absolute():
            candidate = Path.cwd() / candidate
        # Keep the lexical path (not resolved): a junction must be detected by
        # run() instead of being silently followed to its target.
        root = candidate.absolute()
        # A custom root keeps working only when it still ends in output/tmp, and
        # the boundary is then evaluated against that root's own repository.
        if root.name != TMP_RELATIVE.name or root.parent.name != TMP_RELATIVE.parent.name:
            print(f"拒绝：--root 必须指向某仓库的 output/tmp，收到 {root}", file=sys.stderr)
            return 1
        if trusted_root is None:
            trusted_root = root.parent.parent.absolute()
    code, report = run(days=args.days, apply=args.apply, root=root, repo_root=trusted_root,
                       require_default_root=root is None, now=now)
    print(report, file=sys.stderr if code else sys.stdout)
    return code


if __name__ == "__main__":
    sys.exit(main())
