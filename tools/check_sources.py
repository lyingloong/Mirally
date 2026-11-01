"""Check Mirally-owned sources for merge-conflict markers and Python syntax errors.

Run from the repository root:

    python tools/check_sources.py

The scan covers Mirally-owned source, documentation and configuration, including
the Mirally product chain under ``kernel/dsh/packages/mirally`` and every
frontend file this repository owns: files that are not part of the retained
upstream snapshot (``upstream-files.json``) plus the Mirally patches listed in
``local-changes.json``. Untouched upstream kernel/frontend files, the UFO
submodule, dependencies and generated output are not scanned; excluded
directories are pruned before descending. Binary assets with a text suffix are
skipped instead of failing UTF-8 decoding.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_PARTS = {".git", ".venv", "node_modules", "output", ".artifacts", "UFO", "__pycache__",
              "dist", "build", "coverage", ".vite", ".turbo"}
SCAN_DIRS = ("tools", "tests", "mirally", "docs", "config", ".github", "kernel/dsh/packages/mirally")
EXTRA_FILES = ("kernel/README.md", "kernel/dsh/UPSTREAM.md")
FRONTEND_ROOT = "frontend/control-ui"
FRONTEND_UPSTREAM = "upstream-files.json"
FRONTEND_CHANGES = "local-changes.json"
ROOT_FILES = ("README.md", "AGENTS.md", "CONTRIBUTING.md", "CHANGELOG.md", "run.ps1", "run-openclaw.ps1")
TEXT_SUFFIXES = {".py", ".md", ".mjs", ".cjs", ".js", ".cpp", ".ps1", ".yml", ".yaml", ".template",
                 ".json", ".toml", ".txt", ".css", ".html", ".webmanifest", ".ts", ".tsx"}
MARKERS = ("<<<<<<<", "|||||||", ">>>>>>>")


def _walk_files(base: Path) -> list[Path]:
    """Text files under one root; excluded directories are pruned before descending."""
    if not base.is_dir():
        return []
    found: list[Path] = []
    for current, dirs, names in os.walk(base):
        dirs[:] = [name for name in dirs if name not in SKIP_PARTS]
        for name in names:
            path = Path(current) / name
            if path.is_file() and path.suffix in TEXT_SUFFIXES:
                found.append(path)
    return found


def _manifest_keys(manifest: Path, field: str | None = None) -> set[str]:
    if not manifest.is_file():
        return set()
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return set()
    if field is not None:
        payload = payload.get(field) if isinstance(payload, dict) else None
    return set(payload) if isinstance(payload, dict) else set()


def frontend_owned_files(root: Path = ROOT) -> list[Path]:
    """Mirally files under the retained frontend: own additions plus its own patches."""
    frontend = root / FRONTEND_ROOT
    if not frontend.is_dir():
        return []
    upstream = _manifest_keys(frontend / FRONTEND_UPSTREAM, "files")
    patched = _manifest_keys(frontend / FRONTEND_CHANGES)
    owned: list[Path] = []
    for path in _walk_files(frontend):
        relative = path.relative_to(frontend).as_posix()
        if relative in upstream and relative not in patched:
            continue  # retained upstream file without a Mirally change
        owned.append(path)
    return owned


def owned_files(root: Path = ROOT) -> list[Path]:
    files = [root / name for name in ROOT_FILES + EXTRA_FILES if (root / name).is_file()]
    for name in SCAN_DIRS:
        files.extend(_walk_files(root / name))
    files.extend(frontend_owned_files(root))
    return sorted({path for path in files
                   if path.is_file() and not any(part in SKIP_PARTS for part in path.relative_to(root).parts)})


def check(path: Path, *, root: Path = ROOT) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return []  # binary asset with a text suffix: nothing to check
    label = path.relative_to(root).as_posix()
    problems = [f"{label}:{n}: leftover merge-conflict marker"
                for n, line in enumerate(text.splitlines(), 1) if line.startswith(MARKERS)]
    if path.suffix == ".py":
        try:
            compile(text, label, "exec")
        except SyntaxError as error:
            problems.append(f"{label}:{error.lineno}: syntax error: {error.msg}")
    return problems


def main() -> int:
    files = owned_files()
    problems = [p for f in files for p in check(f)]
    if problems:
        print(f"{len(problems)} source hygiene problem(s):")
        for problem in problems:
            print(f"  {problem}")
        return 1
    print(f"OK: {len(files)} files, no merge-conflict markers or Python syntax errors.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
