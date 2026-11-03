"""Check relative Markdown links in Mirally-owned documentation.

Run from the repository root:

    python tools/check_docs_links.py

External URLs, anchors and fenced code blocks are ignored. Upstream kernel and
frontend source documentation is not scanned; `kernel/dsh/UPSTREAM.md` is an
exception because Mirally maintains it and it links into product documentation.
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


def check(path: Path) -> list[str]:
    text = FENCE.sub("", path.read_text(encoding="utf-8"))
    missing: list[str] = []
    for match in LINK.finditer(text):
        target = match.group(1).strip()
        if not target or target.startswith(("#", "http://", "https://", "mailto:", "tel:", "data:")):
            continue
        relative = unquote(target.split("#", 1)[0].replace("\\", "/"))
        if not relative:
            continue
        resolved = (ROOT / relative.lstrip("/")) if relative.startswith("/") else (path.parent / relative)
        if not resolved.exists():
            missing.append(f"{path.relative_to(ROOT)} -> {target}")
    return missing


def main() -> int:
    problems: list[str] = []
    files = owned_files()
    for path in files:
        problems.extend(check(path))
    if problems:
        print(f"{len(problems)} broken relative link(s):")
        for problem in problems:
            print(f"  {problem}")
        return 1
    print(f"OK: {len(files)} Markdown files, all relative links resolve.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
