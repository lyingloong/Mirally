#!/usr/bin/env python3
"""Read-only check for unignored temporary artifacts at the repository root.

Run from the repository root:

    python tools/check_local_artifacts.py            # human summary, exit 0/1
    python tools/check_local_artifacts.py --json     # machine-readable report

Why this exists: ad-hoc verification runs leave scratch files such as
``tmpXYZ/`` at the repository root, where they show up in every ``git status``
and hide real changes. The canonical scratch root is ``output/tmp/<tool>/<run>/``
(``output/`` is git-ignored), so anything matching ``tmp*`` at the repository
root that git does *not* ignore is reported.

This tool only reports. It never writes, never deletes, never edits
``.gitignore``, and never runs ``git clean``: ignoring or removing such a file
is a human decision (see "临时产物" in CONTRIBUTING.md).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATTERN = "tmp*"
# Shown in the summary; the cleanup entry point only ever touches directories
# directly under this path.
SCRATCH_ROOT = "output/tmp"


def _git(root: Path, *arguments: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), *arguments], capture_output=True, text=True)


def _tracked_and_ignored(root: Path, names: list[str]) -> tuple[set[str], set[str]]:
    """Which candidate entries git tracks, and which git ignores."""
    tracked: set[str] = set()
    ignored: set[str] = set()
    if not names:
        return tracked, ignored
    listing = _git(root, "ls-files", "-z", "--", *names)
    if listing.returncode == 0:
        tracked = {path for path in listing.stdout.split("\0") if path}
    # ``-z`` is only valid together with ``--stdin``: pass the names on stdin so
    # non-ASCII names are not quoted/escaped in the output.
    check = subprocess.run(
        ["git", "-C", str(root), "check-ignore", "-z", "--stdin", "--no-index"],
        input="\0".join(names), capture_output=True, text=True)
    # exit 0: at least one path ignored; 1: none ignored; both carry a path list.
    if check.returncode in (0, 1):
        ignored = {path for path in check.stdout.split("\0") if path}
    return tracked, ignored


def root_entries(root: Path) -> list[str]:
    """Immediate repository-root entries matching ``tmp*``, sorted."""
    try:
        return sorted(entry.name for entry in root.iterdir() if entry.name.startswith("tmp"))
    except OSError:
        return []


def audit(root: Path = ROOT) -> dict:
    root = Path(root).resolve()
    # ``rev-parse --is-inside-work-tree`` is also true for a subdirectory or a
    # directory merely nested inside some enclosing repository, so compare the
    # toplevel instead: only the work tree root itself is meaningful here.
    toplevel = _git(root, "rev-parse", "--show-toplevel")
    reported = toplevel.stdout.strip() if toplevel.returncode == 0 else ""
    is_repo_root = False
    if reported:
        try:
            is_repo_root = Path(reported).resolve() == root
        except OSError:
            is_repo_root = False
    if not is_repo_root:
        return {"ok": False, "root": str(root), "pattern": PATTERN, "scratch_root": SCRATCH_ROOT,
                "candidates": [], "ignored": [], "tracked": [], "unignored": [],
                "error": "not a git work tree root: run this from the repository root",
                "summary": {"candidates": 0, "ignored": 0, "tracked": 0, "unignored": 0}}

    candidates = root_entries(root)
    tracked, ignored = _tracked_and_ignored(root, candidates)
    unignored = [name for name in candidates if name not in ignored]
    return {
        "ok": not unignored,
        "root": str(root),
        "pattern": PATTERN,
        "scratch_root": SCRATCH_ROOT,
        "candidates": candidates,
        "ignored": [name for name in candidates if name in ignored],
        "tracked": sorted(tracked),
        "unignored": unignored,
        "summary": {
            "candidates": len(candidates),
            "ignored": sum(1 for name in candidates if name in ignored),
            "tracked": len(tracked),
            "unignored": len(unignored),
        },
    }


def print_summary(report: dict) -> None:
    summary = report["summary"]
    print("Local artifact check (read-only)")
    print(f"  Root entries matching {report['pattern']}: {summary['candidates']} "
          f"(ignored: {summary['ignored']}, tracked: {summary['tracked']})")
    if "error" in report:
        print(f"  error: {report['error']}")
        return
    if not report["ok"]:
        print(f"  unignored at repository root: {summary['unignored']}")
        for name in report["unignored"]:
            print(f"    {name}")
        print(f"  Move verification output to {report['scratch_root']}/<tool>/<run>/ "
              "or delete it yourself; this check never does either.")
    print("result: " + ("ok" if report["ok"] else "FAILED"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only check for unignored tmp* entries at the repository root")
    parser.add_argument("--root", default=str(ROOT), help="repository root (default: this checkout)")
    parser.add_argument("--json", action="store_true", help="print the full report as JSON")
    args = parser.parse_args(argv)
    report = audit(Path(args.root))
    if "error" in report:
        print(f"error: {report['error']}", file=sys.stderr)
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        return 2
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_summary(report)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
