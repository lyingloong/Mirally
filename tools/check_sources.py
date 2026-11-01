"""Check Mirally-owned sources for merge-conflict markers and Python syntax errors.

Run from the repository root:

    python tools/check_sources.py

The scan covers Mirally-owned source, documentation and configuration, including
the Mirally product chain under ``kernel/dsh/packages/mirally`` and the frontend
files this repository owns (Mirally pages/scripts/locales plus paths listed in
``frontend/control-ui/local-changes.json``). Upstream kernel/frontend files not
listed there, the UFO submodule, dependencies and generated output are not
scanned; binary assets with a text suffix are skipped instead of failing UTF-8
decoding.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_PARTS = {".git", ".venv", "node_modules", "output", ".artifacts", "UFO", "__pycache__",
              "dist", "build", "coverage", ".vite", ".turbo"}
SCAN_DIRS = ("tools", "tests", "mirally", "docs", "config", ".github", "kernel/dsh/packages/mirally")
EXTRA_FILES = ("kernel/README.md", "kernel/dsh/UPSTREAM.md")
FRONTEND_ROOT = "frontend/control-ui"
FRONTEND_DIRS = ("ui/src/pages/mirally", "scripts", "locales")
FRONTEND_MANIFEST = "local-changes.json"
ROOT_FILES = ("README.md", "AGENTS.md", "CONTRIBUTING.md", "CHANGELOG.md", "run.ps1", "run-openclaw.ps1")
TEXT_SUFFIXES = {".py", ".md", ".mjs", ".cjs", ".ps1", ".yml", ".yaml", ".json", ".toml", ".txt", ".css", ".ts", ".tsx"}
MARKERS = ("<<<<<<<", "|||||||", ">>>>>>>")


def _text_files(base: Path) -> list[Path]:
    if not base.is_dir():
        return []
    return [path for path in base.rglob("*") if path.is_file() and path.suffix in TEXT_SUFFIXES]


def frontend_owned_files(root: Path = ROOT) -> list[Path]:
    """Mirally pages/scripts/locales plus the retained files it patches itself."""
    frontend = root / FRONTEND_ROOT
    files = [path for relative in FRONTEND_DIRS for path in _text_files(frontend / relative)]
    manifest = frontend / FRONTEND_MANIFEST
    if manifest.is_file():
        try:
            entries = json.loads(manifest.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            entries = {}
        if isinstance(entries, dict):
            files.extend(path for relative in entries
                         if (path := frontend / str(relative)).is_file() and path.suffix in TEXT_SUFFIXES)
    return files


def owned_files(root: Path = ROOT) -> list[Path]:
    files = [root / name for name in ROOT_FILES + EXTRA_FILES if (root / name).is_file()]
    for name in SCAN_DIRS:
        files.extend(_text_files(root / name))
    files.extend(frontend_owned_files(root))
    return sorted(path for path in set(files)
                  if path.is_file() and not any(part in SKIP_PARTS for part in path.relative_to(root).parts))


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
