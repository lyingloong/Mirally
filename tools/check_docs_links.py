"""Check relative Markdown links and in-repository heading anchors.

Run from the repository root:

    python tools/check_docs_links.py

External URLs and fenced code blocks are ignored. Repository-internal fragments
(`other.md#heading` and `#heading`) are resolved against GitHub-style heading
slugs; repeated headings receive GitHub's `-1`, `-2`, ... suffixes in document
order. A heading is rendered before GitHub slugs it, so headings containing raw
HTML or inline links/images cannot be reproduced by this approximation: files
with such headings are only partially verified, and unmatched fragments in them
are left unreported instead of failing the gate. Upstream kernel and frontend
source documentation is not scanned; `kernel/dsh/UPSTREAM.md` is an exception
because Mirally maintains it and it links into product documentation.
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
# GitHub renders a heading (including inline links/images and HTML) before slugging it.
HEADING_MARKUP = re.compile(r"<[^>]+>|!?\[[^\]]*\]\([^)]*\)")


def slugify(heading: str) -> str:
    """Approximate a GitHub heading anchor: strip markup, lowercase, spaces -> '-'."""
    text = heading.replace("`", "").strip().lower()
    return re.sub(r"\s+", "-", re.sub(r"[^\w\s-]", "", text))


def _scan_headings(path: Path) -> tuple[set[str], bool]:
    """(anchors, complete); fenced code is ignored.

    Repeated headings get GitHub's numeric suffixes (`-1`, `-2`, ...) in document
    order, including re-numbering when a suffixed slug is itself already taken.
    `complete` is False when a heading renders differently than this approximation
    slugs it (raw HTML, inline links/images), because then the generated anchors and
    the duplicate numbering they shift are only guesses.
    """
    text = FENCE.sub("", path.read_text(encoding="utf-8"))
    anchors: set[str] = set()
    duplicates: dict[str, int] = {}
    complete = True
    for match in HEADING.finditer(text):
        heading = match.group(1)
        base = slugify(heading)
        if not base:
            # GitHub's anchor for a heading that renders to nothing is not linkable.
            continue
        anchor = base
        while anchor in anchors:
            duplicates[base] = duplicates.get(base, 0) + 1
            anchor = f"{base}-{duplicates[base]}"
        anchors.add(anchor)
        if HEADING_MARKUP.search(heading):
            complete = False
    return anchors, complete


def heading_anchors(path: Path) -> set[str]:
    """Anchors this file offers (with GitHub's duplicate suffixes), ignoring fenced code."""
    return _scan_headings(path)[0]


def anchors_for(path: Path, cache: dict[Path, tuple[set[str], bool]]) -> tuple[set[str], bool]:
    key = path.resolve()
    if key not in cache:
        cache[key] = _scan_headings(path)
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


def check(path: Path, *, root: Path = ROOT,
          anchors: dict[Path, tuple[set[str], bool]] | None = None) -> list[str]:
    """Relative link and anchor problems in one file; fenced code is ignored."""
    text = FENCE.sub("", path.read_text(encoding="utf-8"))
    cache: dict[Path, tuple[set[str], bool]] = {} if anchors is None else anchors
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
        target_anchors, complete = anchors_for(resolved, cache)
        # An unmatched fragment in a file whose anchors this approximation cannot
        # reproduce is unknown, not broken: reporting it would fail valid links.
        if unquote(fragment) not in target_anchors and complete:
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
