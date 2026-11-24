"""Check Mirally-owned sources for merge-conflict markers, Python and JavaScript
syntax errors and trace whitespace / line-ending damage.

Run from the repository root:

    python tools/check_sources.py

The scan covers Mirally-owned source, documentation and configuration, including
the Mirally product chain under ``kernel/dsh/packages/mirally`` and every
frontend file this repository owns: files that are not part of the retained
upstream snapshot (``upstream-files.json``) plus the Mirally patches listed in
``local-changes.json``. Untouched upstream kernel/frontend files, dependencies
and generated output are not scanned; excluded
directories are pruned before descending. Binary assets with a text suffix are
skipped instead of failing UTF-8 decoding.

The whitespace checks are read-only and re-runnable: they report the same
findings every time and never rewrite a file. A file is reported when

* a line ends in a space or a tab (an all-whitespace line is trailing whitespace
  too: the line exists only to hold invisible bytes),
* the last byte is not ``\\n`` (empty files are exempt), or
* it mixes CRLF and LF line endings.

Files listed in ``CRLF_EXEMPT`` are excused from the CRLF-mixing check only,
each with a written reason; ``CRLF`` alone is not a finding, so a checkout that
normalizes line endings on Windows does not fail the guard.

Every ``.mjs``/``.cjs`` file is additionally parsed with ``node --check``:
parse only — nothing is executed, imports are not resolved, there is no
network access, no dependency installation and no file is rewritten. An
unparseable file reports ``path:line: JavaScript syntax error: <message>``
just like a Python finding, and a file that already shows merge-conflict
markers reports only the marker, so the two layers never stack on the same
file. A missing or unrunnable ``node`` fails the guard with an actionable
message instead of passing silently (README.md requires Node.js 24+); the
executable is injectable through ``--node`` for tooling and tests.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_PARTS = {".git", ".venv", "node_modules", "output", ".artifacts", "__pycache__",
              "dist", "build", "coverage", ".vite", ".vite-temp", ".turbo"}
SCAN_DIRS = ("tools", "tests", "mirally", "docs", "config", ".github", "kernel/dsh/packages/mirally", "website")
EXTRA_FILES = ("kernel/README.md", "kernel/dsh/UPSTREAM.md")
FRONTEND_ROOT = "frontend/control-ui"
FRONTEND_UPSTREAM = "upstream-files.json"
FRONTEND_CHANGES = "local-changes.json"
ROOT_FILES = ("README.md", "AGENTS.md", "CONTRIBUTING.md", "CHANGELOG.md", "run.ps1")
TEXT_SUFFIXES = {".py", ".md", ".mjs", ".cjs", ".js", ".cpp", ".ps1", ".yml", ".yaml", ".template",
                 ".json", ".toml", ".txt", ".css", ".html", ".webmanifest", ".ts", ".tsx", ".jsx"}
MARKERS = ("<<<<<<<", "|||||||", ">>>>>>>")
# ``node --check`` parses these unambiguously as ESM / CommonJS. ``.js`` follows
# the nearest package.json scope, and ``.jsx`` / ``.ts`` / ``.tsx`` are covered
# by the frontend build and ``verify:source``, so neither group is parsed here.
JS_SUFFIXES = (".mjs", ".cjs")
NODE_TIMEOUT_SECONDS = 60
# Mixed CRLF/LF findings that are accepted on purpose. Every entry needs a reason;
# anything else is reported, and the allowlist never covers trailing whitespace or
# the missing final newline.
CRLF_EXEMPT = {
    "tests/test_framework_session_models_host.mjs":
        "committed with a single CRLF on its last line only (4a37cb41); the line endings are "
        "not part of what the test asserts, so rewriting it would add an unrelated diff. "
        "Drop this entry once the file is rewritten with a single line-ending style.",
}


def _walk_files(base: Path, *, extra_skip: tuple[str, ...] = ()) -> list[Path]:
    """Text files under one root; excluded directories are pruned before descending."""
    if not base.is_dir():
        return []
    found: list[Path] = []
    for current, dirs, names in os.walk(base):
        dirs[:] = [name for name in dirs if name not in SKIP_PARTS and name not in extra_skip]
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
        files.extend(_walk_files(root / name, extra_skip=("exports",) if name == "website" else ()))
    files.extend(frontend_owned_files(root))
    return sorted({path for path in files
                   if path.is_file() and not any(part in SKIP_PARTS for part in path.relative_to(root).parts)})


def whitespace_problems(label: str, data: bytes, *, line_ending_exempt: bool = False) -> list[str]:
    """Trailing whitespace, missing final newline and mixed CRLF/LF, in line order."""
    try:
        lines = data.decode("utf-8").splitlines(keepends=True)
    except UnicodeDecodeError:
        return []  # binary asset with a text suffix: nothing to check
    problems = []
    for number, chunk in enumerate(lines, 1):
        body = chunk.rstrip("\r\n")
        if body != body.rstrip(" \t"):
            problems.append(f"{label}:{number}: trailing whitespace")
    if data and not data.endswith(b"\n"):
        problems.append(f"{label}:{len(lines)}: missing final newline")
    if not line_ending_exempt and b"\r\n" in data and b"\n" in data.replace(b"\r\n", b""):
        problems.append(f"{label}:1: mixed CRLF and LF line endings")
    return problems


def node_syntax_problems(label: str, path: Path, *, node: str = "node") -> list[str]:
    """Parse one .mjs/.cjs file with ``node --check``; nothing is executed."""
    try:
        result = subprocess.run([node, "--check", str(path)], capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=NODE_TIMEOUT_SECONDS)
    except OSError as error:  # missing, not an executable or not launchable
        # str(error) carries a localized OS strerror, which is not printable on every
        # console encoding; the ASCII exception class plus fixed guidance is actionable.
        return [f"{label}: JavaScript syntax check unavailable: cannot execute '{node}' "
                f"({type(error).__name__}); install Node.js 24+ (see README.md) or pass --node <path>"]
    except subprocess.TimeoutExpired:
        return [f"{label}: JavaScript syntax check timed out after {NODE_TIMEOUT_SECONDS}s: '{node} --check'"]
    if result.returncode == 0:
        return []
    return [_node_check_finding(label, (result.stderr or "") + (result.stdout or ""))]


def _node_check_finding(label: str, output: str) -> str:
    """Fold ``node --check`` failure output into one ``label:line: …`` finding.

    Node prints ``<path>:<line>``, the offending source line, a caret, then a
    ``SyntaxError: <message>`` block and a stack. The echoed path can contain
    colons (Windows drive letters), so only the trailing ``:<digits>`` is read.
    """
    lines = output.splitlines()
    header = lines[0].strip() if lines else ""
    match = re.fullmatch(r".*:(\d+)", header)
    location = f"{label}:{match.group(1)}" if match else label
    summary = ""
    for line in lines:
        if line.startswith(("SyntaxError:", "ReferenceError:")):
            summary = line.split(":", 1)[1].strip()
            break
    else:
        for line in lines:
            stripped = line.strip()
            if stripped and not stripped.startswith(("at ", "Node.js v")) and set(stripped) != {"^"} \
                    and stripped != header:
                summary = stripped
                break
    return f"{location}: JavaScript syntax error: {summary or 'unknown error'}"


def check(path: Path, *, root: Path = ROOT, node: str = "node") -> list[str]:
    try:
        data = path.read_bytes()
    except OSError:
        return []
    try:
        text = data.decode("utf-8")
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
    elif path.suffix in JS_SUFFIXES and not problems:
        # a marked file already failed loudly; the syntax layer never stacks on it
        problems.extend(node_syntax_problems(label, path, node=node))
    problems.extend(whitespace_problems(label, data, line_ending_exempt=label in CRLF_EXEMPT))
    return problems


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        # findings quote source snippets and OS errors, which may not encode into
        # a legacy console codepage; printing the report must never crash the guard
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(
        prog="check_sources.py",
        description="Check owned sources for merge-conflict markers, Python and "
                    "JavaScript syntax errors and whitespace / line-ending damage.",
    )
    parser.add_argument("--node", default="node", metavar="PATH",
                        help="node executable used to --check every .mjs/.cjs file")
    args = parser.parse_args(sys.argv[1:] if argv is None else list(argv))
    files = owned_files()
    js_count = sum(1 for path in files if path.suffix in JS_SUFFIXES)
    problems = [p for f in files for p in check(f, node=args.node)]
    if problems:
        print(f"{len(problems)} source hygiene problem(s):")
        for problem in problems:
            print(f"  {problem}")
        return 1
    print(f"OK: {len(files)} files ({js_count} .mjs/.cjs parsed with node --check), "
          "no merge-conflict markers, Python or JavaScript syntax errors, "
          "trailing whitespace or line-ending damage.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
