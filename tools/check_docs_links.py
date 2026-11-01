"""Check relative Markdown links and in-repository heading anchors.

Run from the repository root:

    python tools/check_docs_links.py

External URLs and fenced code blocks are ignored. Repository-internal fragments
(`other.md#heading` and `#heading`) are resolved against GitHub-style heading
slugs. Upstream kernel and frontend source documentation is not scanned;
`kernel/dsh/UPSTREAM.md` is an exception because Mirally maintains it and it
links into product documentation.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
SKIP_PARTS = {"node_modules", ".venv", ".git", "output", ".artifacts", "UFO", "dsh"}
# The kernel tree is upstream source, but this one file is maintained by Mirally.
FORCE_CHECK = {ROOT / "kernel" / "dsh" / "UPSTREAM.md"}
LINK = re.compile(r"!?\[[^\]]*\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
FENCE = re.compile(r"```.*?```|~~~.*?~~~", re.DOTALL)
HEADING = re.compile(r"^#{1,6}\s+(.+?)\s*#*\s*$", re.M)


def slugify(heading: str) -> str:
    """Approximate a GitHub heading anchor: strip markup, lowercase, spaces -> '-'."""
    text = heading.replace("`", "").strip().lower()
    return re.sub(r"\s+", "-", re.sub(r"[^\w\s-]", "", text))


def heading_anchors(path: Path) -> set[str]:
    """Anchors this file offers, ignoring headings inside fenced code."""
    text = FENCE.sub("", path.read_text(encoding="utf-8"))
    return {slugify(match.group(1)) for match in HEADING.finditer(text)}


def anchors_for(path: Path, cache: dict[Path, set[str]]) -> set[str]:
    key = path.resolve()
    if key not in cache:
        cache[key] = heading_anchors(path)
    return cache[key]


def owned_files() -> list[Path]:
    files = [ROOT / name for name in ("README.md", "AGENTS.md", "CONTRIBUTING.md", "CHANGELOG.md")]
    files += sorted((ROOT / "docs").rglob("*.md"))
    files += sorted((ROOT / "mirally").rglob("*.md"))
    files += [
        ROOT / "kernel" / "README.md",
        ROOT / "kernel" / "dsh" / "UPSTREAM.md",
        ROOT / "frontend" / "control-ui" / "README.md",
        ROOT / "frontend" / "control-ui" / "UPSTREAM.md",
        ROOT / "frontend" / "control-ui" / "THIRD_PARTY_NOTICES.md",
        ROOT / "showcase" / "README.md",
        ROOT / "tools" / "testing" / "README.md",
    ]
    files += sorted((ROOT / "integrations").rglob("*.md"))
    result = []
    for path in files:
        if not path.is_file():
            continue
        if any(part in SKIP_PARTS for part in path.relative_to(ROOT).parts) and path not in FORCE_CHECK:
            continue
        result.append(path)
    return sorted(set(result))


def check(path: Path, *, root: Path = ROOT, anchors: dict[Path, set[str]] | None = None) -> list[str]:
    """Relative link and anchor problems in one file; fenced code is ignored."""
    text = FENCE.sub("", path.read_text(encoding="utf-8"))
    cache: dict[Path, set[str]] = {} if anchors is None else anchors
    missing: list[str] = []
    for match in LINK.finditer(text):
        target = match.group(1).strip()
        if not target or target.startswith(("http://", "https://", "mailto:", "tel:", "data:")):
            continue
        relative, _, fragment = target.partition("#")
        relative = unquote(relative.replace("\\", "/"))
        if relative:
            resolved = (root / relative.lstrip("/")) if relative.startswith("/") else (path.parent / relative)
            if not resolved.exists():
                missing.append(f"{path.relative_to(root)} -> {target}")
                continue
            if not fragment or resolved.suffix != ".md":
                continue
        else:
            if not fragment:
                continue
            resolved = path
        if unquote(fragment) not in anchors_for(resolved, cache):
            missing.append(f"{path.relative_to(root)} -> {target}")
    return missing


def collect_problems() -> tuple[list[str], int]:
    """(problems, scanned file count); the running check and the tests share this."""
    problems: list[str] = []
    files = owned_files()
    anchors: dict[Path, set[str]] = {}
    for path in files:
        problems.extend(check(path, anchors=anchors))
    return problems, len(files)


def main() -> int:
    problems, files = collect_problems()
    if problems:
        print(f"{len(problems)} broken relative link(s) or anchor(s):")
        for problem in problems:
            print(f"  {problem}")
        return 1
    print(f"OK: {files} Markdown files, all relative links and anchors resolve.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
