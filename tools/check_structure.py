#!/usr/bin/env python3
"""Read-only repository structure audit.

Reports findings, never rewrites anything: tracked top-level entries must be
registered, tracked files must not look like build output, ``tools/`` keeps one
naming scheme, and the domain layers must not import product layers. The human
summary is the default; ``--json`` emits the full machine-readable report.
Exit code 0 means clean, 1 means findings (or the audit could not run).

Conventions and the known exceptions allowed here are documented in
``docs/reference/repository-layout.md``.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]

# Every tracked top-level entry must be listed here. Adding one requires the
# same registration in docs/reference/repository-layout.md.
TOP_LEVEL_ALLOWED = frozenset({
    ".github",
    ".gitignore",
    ".gitmodules",
    "AGENTS.md",
    "CHANGELOG.md",
    "CONTRIBUTING.md",
    "README.md",
    "UFO",
    "config",
    "docs",
    "frontend",
    "kernel",
    "mirally",
    "requirements-framework.txt",
    "requirements-ufo.txt",
    "run.ps1",
    "showcase",
    "tests",
    "tools",
})

# Directories under tools/ follow the same registration rule as top-level ones.
TOOLS_SUBDIRECTORIES_ALLOWED = frozenset({"dev_workflow", "testing"})
TOOLS_EXTENSIONS_ALLOWED = frozenset({".py", ".mjs", ".ps1"})
TOOLS_NAME = re.compile(r"^(check|verify|build|setup|redraw|render)[_-][a-z0-9_-]+$")

# Tracked paths that are build output or caches. The audit scans git's index,
# not the working tree, so ignored local artifacts never trigger it.
ARTIFACT_PATTERNS = (
    re.compile(r"(^|/)(dist|coverage|node_modules|__pycache__|\.pytest_cache|\.artifacts)/"),
    re.compile(r"\.tsbuildinfo$"),
    re.compile(r"\.map$"),
)

# Tree prefix -> import prefixes that tree must not depend on.
IMPORT_RULES = {
    "mirally/core/": ("mirally.framework", "mirally.runtime"),
    "mirally/runtime/": ("mirally.framework",),
}
# Known, reviewed violations: reported as exceptions instead of failures.
# See the "依赖方向" section of docs/reference/repository-layout.md (R2).
IMPORT_EXCEPTIONS = {
    ("mirally/runtime/speech_credentials.py", "mirally.framework"):
        "R2 known exception: CredentialProtector is shared with speech credentials; "
        "fixing the layer direction is a separate point.",
}


def _git_index_paths(root: Path) -> list[str]:
    """Return every index path as a filesystem string.

    ``-z`` prints raw, NUL-separated paths: unlike the default output it does
    not quote or octal-escape non-ASCII names, so the prefix checks below see
    the real path (e.g. ``mirally/core/中文.py``). The index, not ``HEAD``, is
    the source of truth, so staged-but-uncommitted files are audited too.
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z"],
            capture_output=True,
            check=True,
        )
    except FileNotFoundError as error:
        raise RuntimeError("git is required to audit tracked files") from error
    except subprocess.CalledProcessError as error:
        detail = (error.stderr or b"").decode("utf-8", "replace").strip() or "git ls-files -z failed"
        raise RuntimeError(detail) from error
    return [os.fsdecode(path) for path in result.stdout.split(b"\0") if path]


def _top_level_entries(files: list[str]) -> list[str]:
    """Top-level components of index paths (files and their directories)."""
    return sorted({path.split("/", 1)[0] for path in files})


def _tools_direct_entries(files: list[str]) -> list[str]:
    """Direct entries under tools/ derived from index paths."""
    entries: set[str] = set()
    for path in files:
        parts = path.split("/")
        if parts[0] == "tools" and len(parts) > 1:
            entries.add(parts[1])
    return sorted(entries)


def check_top_level(entries: list[str], allowed: frozenset[str] = TOP_LEVEL_ALLOWED) -> dict[str, Any]:
    unregistered = sorted(set(entries) - set(allowed))
    return {"ok": not unregistered, "entries": len(entries), "unregistered": unregistered}


def check_artifacts(files: list[str]) -> dict[str, Any]:
    matches = sorted(path for path in files if any(pattern.search(path) for pattern in ARTIFACT_PATTERNS))
    return {"ok": not matches, "matches": matches}


def check_tools(entries: list[str]) -> dict[str, Any]:
    violations: list[dict[str, str]] = []
    for entry in sorted(entries):
        if "." not in entry:  # a direct subdirectory, not a file
            if entry not in TOOLS_SUBDIRECTORIES_ALLOWED:
                violations.append({"entry": entry, "reason": "unregistered tools directory"})
            continue
        path = Path(entry)
        extension = path.suffix
        if extension not in TOOLS_EXTENSIONS_ALLOWED:
            violations.append({"entry": entry, "reason": f"extension {extension or '<none>'} is not allowed"})
            continue
        if not TOOLS_NAME.fullmatch(path.stem):
            violations.append({
                "entry": entry,
                "reason": "name must start with check_/verify_/build_/setup_/redraw_/render_",
            })
    return {"ok": not violations, "entries": len(entries), "violations": violations}


def _module_parts(relative: str) -> list[str]:
    """Module parts for a tracked .py path (``mirally/core/x.py`` -> [...])"""
    return relative[:-3].split("/")


def _resolved_imports(relative: str, source: str) -> list[str]:
    """Return every absolute module name imported by one Python file."""
    tree = ast.parse(source)
    package_parts = _module_parts(relative)[:-1]
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            parts: list[str] = []
            if node.level > 0:
                keep = max(0, len(package_parts) - (node.level - 1))
                parts = package_parts[:keep]
            if node.module:
                parts = parts + node.module.split(".")
            if parts:
                modules.append(".".join(parts))
                # `from mirally import framework` must still be caught; the
                # imported names are module parts for the direction check.
                modules.extend(".".join(parts + [alias.name]) for alias in node.names)
    return modules


def check_imports(root: Path, files: list[str]) -> dict[str, Any]:
    violations: list[dict[str, str]] = []
    exceptions: list[dict[str, str]] = []
    parse_errors: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for relative in sorted(files):
        rule = next((prefix for prefix in IMPORT_RULES if relative.startswith(prefix)), None)
        if not rule or not relative.endswith(".py"):
            continue
        try:
            source = (Path(root) / relative).read_text(encoding="utf-8")
            imported = _resolved_imports(relative, source)
        except (OSError, SyntaxError, UnicodeDecodeError) as error:
            parse_errors.append({"file": relative, "reason": f"{type(error).__name__}: {error}"})
            continue
        for module in imported:
            for forbidden in IMPORT_RULES[rule]:
                if module != forbidden and not module.startswith(forbidden + "."):
                    continue
                if (relative, forbidden) in seen:
                    break
                seen.add((relative, forbidden))
                exception = IMPORT_EXCEPTIONS.get((relative, forbidden))
                if exception:
                    exceptions.append({"file": relative, "import": module, "reason": exception})
                else:
                    violations.append({
                        "file": relative,
                        "import": module,
                        "rule": f"{rule}must not import {forbidden}",
                    })
                break
    return {
        "ok": not violations and not parse_errors,
        "violations": violations,
        "exceptions": exceptions,
        "parse_errors": parse_errors,
    }


def audit(root: Path = REPO_ROOT) -> dict[str, Any]:
    root = Path(root)
    files = _git_index_paths(root)
    checks = {
        "top_level": check_top_level(_top_level_entries(files)),
        "artifacts": check_artifacts(files),
        "tools": check_tools(_tools_direct_entries(files)),
        "imports": check_imports(root, files),
    }
    return {"ok": all(check["ok"] for check in checks.values()), "root": str(root), "checks": checks}


def format_summary(report: dict[str, Any]) -> str:
    checks = report["checks"]
    lines = [f"Structure audit: {report['root']}"]
    top = checks["top_level"]
    lines.append(f"  top-level entries: {'ok' if top['ok'] else 'FAIL'} ({top['entries']} tracked entries)")
    lines.extend(f"    unregistered: {entry}" for entry in top["unregistered"])
    artifacts = checks["artifacts"]
    lines.append(f"  tracked build output: {'ok' if artifacts['ok'] else 'FAIL'} ({len(artifacts['matches'])} matches)")
    lines.extend(f"    generated: {path}" for path in artifacts["matches"])
    tools = checks["tools"]
    lines.append(f"  tools/ naming: {'ok' if tools['ok'] else 'FAIL'} ({tools['entries']} entries)")
    lines.extend(f"    {item['entry']}: {item['reason']}" for item in tools["violations"])
    imports = checks["imports"]
    lines.append(
        f"  dependency direction: {'ok' if imports['ok'] else 'FAIL'} "
        f"({len(imports['violations'])} violations, {len(imports['exceptions'])} known exceptions)"
    )
    lines.extend(f"    {item['file']}: imports {item['import']} ({item['rule']})" for item in imports["violations"])
    lines.extend(f"    {item['file']}: imports {item['import']}" for item in imports["parse_errors"])
    lines.extend(f"    known exception: {item['file']}: imports {item['import']}" for item in imports["exceptions"])
    lines.append("result: " + ("ok" if report["ok"] else "FAILED"))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only repository structure audit")
    parser.add_argument("--root", default=str(REPO_ROOT), help="repository root (default: this checkout)")
    parser.add_argument("--json", action="store_true", help="print the full report as JSON")
    args = parser.parse_args(argv)
    try:
        report = audit(Path(args.root))
    except RuntimeError as error:
        report = {"ok": False, "root": str(args.root), "error": str(error)}
    if args.json:
        # Reports can quote non-ASCII paths; a legacy console code page (cp1252)
        # must not turn the audit result into a UnicodeEncodeError.
        encoding = sys.stdout.encoding or "utf-8"
        print(json.dumps(report, ensure_ascii=False, indent=2).encode(encoding, "backslashreplace").decode(encoding))
    elif "error" in report:
        print(f"error: {report['error']}", file=sys.stderr)
    else:
        print(format_summary(report))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
