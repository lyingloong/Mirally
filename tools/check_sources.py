"""Check Mirally-owned sources for merge-conflict markers and Python syntax errors.

Run from the repository root:

    python tools/check_sources.py

The scan covers Mirally-owned source, documentation and configuration. Upstream
kernel/frontend trees, the UFO submodule and generated output are not scanned;
binary assets with a text suffix are skipped instead of failing UTF-8 decoding.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_PARTS = {".git", ".venv", "node_modules", "output", ".artifacts", "UFO", "__pycache__"}
SCAN_DIRS = ("tools", "tests", "mirally", "docs", "config", ".github")
ROOT_FILES = ("README.md", "AGENTS.md", "CONTRIBUTING.md", "CHANGELOG.md", "run.ps1", "run-openclaw.ps1")
TEXT_SUFFIXES = {".py", ".md", ".mjs", ".cjs", ".ps1", ".yml", ".yaml", ".json", ".toml", ".txt", ".css", ".ts", ".tsx"}
MARKERS = ("<<<<<<<", "|||||||", ">>>>>>>")


def owned_files(root: Path = ROOT) -> list[Path]:
    files = [root / name for name in ROOT_FILES if (root / name).is_file()]
    for name in SCAN_DIRS:
        base = root / name
        if base.is_dir():
            files.extend(p for p in base.rglob("*") if p.is_file() and p.suffix in TEXT_SUFFIXES)
    return sorted(p for p in files if not any(part in SKIP_PARTS for part in p.relative_to(root).parts))


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
