"""Read-only static audit of the Mirally showcase site (offline, standard library only).

Run from the repository root:

    python tools/check_showcase.py            # standard audit
    python tools/check_showcase.py --strict   # also require aria-hidden/role=presentation for alt=""

The audit reads only tracked pages under ``website/``: document metadata
(``lang``, ``<title>``, viewport, exactly one ``<h1>``), external http(s)
references (upstream license/credits files are exempt through an explicit
list, and navigation targets through a navigation-only allowlist), per-tag
``<img>`` alt coverage (attributes are matched by complete name,
so ``data-alt`` does not count as ``alt``), ``prefers-reduced-motion`` support
and, when ``website/exports/*.html`` exists, the offline self-containment of
that export: script syntax, and every local script/stylesheet it references must
already exist next to the page. Nothing is written and nothing is downloaded;
exit code 0 means clean, 1 means findings, 2 means bad arguments.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHOWCASE = "website"
PAGE_SUFFIXES = {".html", ".jsx", ".js", ".mjs", ".ts", ".tsx", ".css"}
# Upstream license/credits files legitimately cite upstream URLs; they are not
# page resources, so the external-reference check excludes them explicitly.
EXTERNAL_WHITELIST = (
    "website/public/CREDITS.md",
    "website/public/LICENSES.txt",
    "website/public/fonts/OFL.txt",
)
# Intentional navigation links to the project and personal homepage. They are
# links, not page resources, so the allowlist applies only where the URL is a
# navigation target: inside an <a> element, or in a JS/JSX constant the audit
# can prove is used only as an anchor target. The same URL text used by an
# img/script/link resource is still an external reference and gets reported.
EXTERNAL_URL_ALLOWLIST = (
    "https://github.com/lyingloong/Mirally",
    "https://lyingloong.github.io/",
)
HTTP = re.compile(r"https?://[^\s\"'<>)]+")
ANCHOR_TAG = re.compile(r"<a\b[^>]*>", re.IGNORECASE | re.DOTALL)
DECLARATION_NAME = re.compile(r"\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)")
DECLARATION = re.compile(
    r"\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*"
    r"(?:\"([^\"]*)\"|'([^']*)'|`([^`]*)`)",
    re.DOTALL,
)
IMG_TAG = re.compile(r"<img\b[^>]*>", re.IGNORECASE | re.DOTALL)
# A JSX/HTML attribute: a complete attribute name plus ``=`` and its value.
# ``data-alt="…"`` must not satisfy the alt requirement, so attributes are
# parsed by whole name instead of searching for a ``\balt`` substring.
ATTRIBUTE = re.compile(
    r"([A-Za-z_][\w:.-]*)\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|\{([^}]*)\})",
    re.DOTALL,
)
H1 = re.compile(r"<h1\b", re.IGNORECASE)
REDUCED_MOTION = re.compile(r"prefers-reduced-motion", re.IGNORECASE)
SCRIPT_TAG = re.compile(r"<script\b([^>]*)>(.*?)</script>", re.IGNORECASE | re.DOTALL)
LINK_TAG = re.compile(r"<link\b([^>]*)>", re.IGNORECASE)
EXPORT_ATTR_HTTP = re.compile(r"\b(?:src|href)\s*=\s*[\"']https?://", re.IGNORECASE)
RESIDUAL_IMAGE_PATH = re.compile(r"[\"'](?:\./)?images/")
EMBEDDED_URL = re.compile(r"^(?:data:|#|$)", re.IGNORECASE)


def page_files(root: Path) -> list[Path]:
    base = root / SHOWCASE
    files = [base / "index.html"]
    source = base / "src"
    if source.is_dir():
        # The landing page lives directly in src/. ``src/updates`` is a separate
        # multi-page app with its own document roots, so it is not audited here.
        files += sorted(p for p in source.glob("*") if p.is_file() and p.suffix in PAGE_SUFFIXES)
    files += [base / name for name in ("public/CREDITS.md", "public/LICENSES.txt", "public/fonts/OFL.txt")]
    return [path for path in files if path.is_file()]


def label(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def check_document(files: list[Path], root: Path) -> tuple[list[str], dict[str, int]]:
    problems: list[str] = []
    index = root / SHOWCASE / "index.html"
    text = read(index) if index.is_file() else ""
    name = label(index, root)
    if not re.search(r"<html\b[^>]*\blang\s*=\s*[\"'][^\"']+[\"']", text, re.IGNORECASE):
        problems.append(f"{name}: <html> is missing a non-empty lang attribute")
    if not re.search(r"<title>\s*[^<\s][^<]*</title>", text, re.IGNORECASE):
        problems.append(f"{name}: <title> is missing or empty")
    if not re.search(r"<meta\b[^>]*\bname\s*=\s*[\"']viewport[\"'][^>]*\bcontent\s*=\s*[\"'][^\"']+[\"']", text, re.IGNORECASE):
        problems.append(f"{name}: viewport meta tag with content is missing")
    h1_count = sum(len(H1.findall(read(path))) for path in files)
    if h1_count != 1:
        problems.append(f"{name}: expected exactly one <h1> across showcase pages, found {h1_count}")
    return problems, {"h1": h1_count}


def _inside(spans: list[tuple[int, int]], offset: int) -> bool:
    return any(start <= offset < end for start, end in spans)


def _closing_brace(text: str, start: int) -> int | None:
    """The ``}`` matching the JSX expression that opens at ``start``."""
    depth = 0
    for index in range(start, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return index
    return None


def anchor_href_spans(text: str) -> list[tuple[int, int]]:
    """Character spans of ``href`` values on ``<a>`` elements (navigation targets)."""
    spans: list[tuple[int, int]] = []
    for tag in ANCHOR_TAG.finditer(text):
        tag_text = tag.group(0)
        for match in re.finditer(r"\bhref\s*=\s*", tag_text, re.IGNORECASE):
            start = tag.start() + match.end()
            if start >= tag.end():
                continue
            opener = text[start]
            if opener in "\"'":
                end = text.find(opener, start + 1)
                if end != -1:
                    spans.append((start + 1, end))
            elif opener == "{":
                end = _closing_brace(text, start)
                if end is not None:
                    spans.append((start + 1, end))
    return spans


def declared_identifier(text: str, offset: int) -> str | None:
    """The ``const``/``let``/``var`` name whose literal value contains ``offset``."""
    for match in DECLARATION.finditer(text):
        if any(
            match.group(group) is not None and match.start(group) <= offset < match.end(group)
            for group in (2, 3, 4)
        ):
            return match.group(1)
    return None


def identifier_pattern(identifier: str) -> re.Pattern[str]:
    """Match whole JavaScript identifier uses, including ``$`` and ``_`` names.

    ``\\b`` treats ``$`` as a non-word character, so ``\\b\\$homepage\\b`` never
    matches ``$homepage``; the boundaries are spelled out from the identifier
    characters allowed by ``DECLARATION_NAME`` instead.
    """
    return re.compile(rf"(?<![A-Za-z0-9_$]){re.escape(identifier)}(?![A-Za-z0-9_$])")


def navigation_constant(text: str, identifier: str, seen: frozenset[str] = frozenset()) -> bool:
    """Whether every use of ``identifier`` is an anchor href target (aliases included).

    A URL string constant is only navigation if its identifier is never used as
    a page resource; a template constant that feeds another navigation constant
    keeps the proof by following that alias.
    """
    if identifier in seen:
        return False
    name_spans = [match.span(1) for match in DECLARATION_NAME.finditer(text)]
    targets = anchor_href_spans(text)
    for match in identifier_pattern(identifier).finditer(text):
        start = match.start()
        if _inside(name_spans, start):
            continue  # the declaration itself is not a use
        if _inside(targets, start):
            continue  # rendered as an <a href> target
        alias = declared_identifier(text, start)
        if alias is not None and alias != identifier and navigation_constant(text, alias, seen | {identifier}):
            continue  # feeds another constant that is itself navigation-only
        return False
    return True


def navigation_target(text: str, offset: int, targets: list[tuple[int, int]]) -> bool:
    """Whether the URL at ``offset`` is a navigation target, not a page resource."""
    if _inside(targets, offset):
        return True
    identifier = declared_identifier(text, offset)
    return identifier is not None and navigation_constant(text, identifier)


def check_external(files: list[Path], root: Path) -> tuple[list[str], dict[str, int]]:
    problems: list[str] = []
    exempt = 0
    for path in files:
        name = label(path, root)
        text = read(path)
        targets = anchor_href_spans(text)
        for match in HTTP.finditer(text):
            if name in EXTERNAL_WHITELIST:
                exempt += 1
                continue
            url = match.group(0)
            if url in EXTERNAL_URL_ALLOWLIST and navigation_target(text, match.start(), targets):
                continue
            problems.append(f"{name}:{line_of(text, match.start())}: external reference {url}")
    return problems, {"license_links": exempt}


def attributes(tag: str) -> dict[str, str | None]:
    """Map complete attribute names to their literal value (None if dynamic).

    ``{expression}`` values are returned as ``None`` because the audit cannot
    evaluate them statically. Names are compared case-insensitively, matching
    HTML attribute semantics.
    """
    parsed: dict[str, str | None] = {}
    for match in ATTRIBUTE.finditer(tag):
        name = match.group(1).lower()
        dynamic = match.group(4) is not None
        value = next((group for group in match.groups()[1:] if group is not None), "")
        parsed.setdefault(name, None if dynamic else value)
    return parsed


def check_images(files: list[Path], root: Path, strict: bool) -> tuple[list[str], list[str], int]:
    # ``alt=""`` is the HTML-spec marker for a decorative image, so the default
    # audit accepts it and reports the spec's extra aria-hidden/role rule as an
    # advisory note; ``--strict`` turns those notes into failures.
    problems: list[str] = []
    notes: list[str] = []
    total = 0
    for path in files:
        text = read(path)
        name = label(path, root)
        for match in IMG_TAG.finditer(text):
            total += 1
            tag = match.group(0)
            where = f"{name}:{line_of(text, match.start())}"
            parsed = attributes(tag)
            if "alt" not in parsed:
                problems.append(f"{where}: <img> is missing an alt attribute")
                continue
            alt = parsed["alt"]
            if alt is None:
                continue  # dynamic alt={expression}: author intent stated
            if alt.strip():
                continue  # descriptive alt text
            # Dynamic JSX values (``{expression}``) are parsed as None; the audit
            # cannot prove them decorative, so they must not crash the .strip().
            if (parsed.get("aria-hidden") or "").strip().lower() == "true" or (
                (parsed.get("role") or "").strip().lower() == "presentation"
            ):
                continue
            message = f'{where}: decorative alt="" without aria-hidden or role=presentation'
            (problems if strict else notes).append(message)
    return problems, notes, total


def check_reduced_motion(files: list[Path]) -> tuple[list[str], dict[str, int]]:
    uses = sum(len(REDUCED_MOTION.findall(read(path))) for path in files if path.suffix in {".css", ".js", ".jsx", ".mjs"})
    problems = [] if uses else ["website: no prefers-reduced-motion handling found in styles or scripts"]
    return problems, {"reduced_motion": uses}


def check_export_resources(text: str, page: Path, name: str) -> list[str]:
    """Require every local script/stylesheet an exported page references to exist."""
    problems: list[str] = []
    references: list[tuple[str, str, int]] = []
    for tag in SCRIPT_TAG.finditer(text):
        parsed = attributes(tag.group(1))
        if parsed.get("src"):
            references.append(("script", parsed["src"] or "", tag.start()))
    for tag in LINK_TAG.finditer(text):
        parsed = attributes(tag.group(1))
        if (parsed.get("rel") or "").strip().lower() == "stylesheet" and parsed.get("href"):
            references.append(("stylesheet", parsed["href"] or "", tag.start()))
    for kind, reference, offset in references:
        target = reference.strip()
        if EMBEDDED_URL.match(target):
            continue  # already inlined (data:), a fragment, or empty
        where = f"{name}:{line_of(text, offset)}"
        if target.startswith("//"):
            # Protocol-relative URLs load from the network. They are external
            # references, never evidence that the resource was inlined.
            problems.append(f"{where}: exported page references external protocol-relative {kind} {reference}")
            continue
        if re.match(r"^[a-z][a-z0-9+.-]*:", target, re.IGNORECASE) or target.startswith("/"):
            problems.append(f"{where}: exported page references non-relative {kind} {reference}")
            continue
        local = (page.parent / target.split("?", 1)[0].split("#", 1)[0]).resolve()
        if not local.is_file():
            problems.append(f"{where}: exported page references missing local {kind} {reference}")
    return problems


def check_exports(root: Path) -> tuple[list[str], list[str], int]:
    exports = root / SHOWCASE / "exports"
    problems: list[str] = []
    notes: list[str] = []
    if not exports.is_dir():
        return problems, notes, 0
    node = shutil.which("node")
    pages = sorted(exports.glob("*.html"))
    for path in pages:
        text = read(path)
        name = label(path, root)
        for match in EXPORT_ATTR_HTTP.finditer(text):
            problems.append(f"{name}:{line_of(text, match.start())}: exported page keeps an external src/href")
        for match in RESIDUAL_IMAGE_PATH.finditer(text):
            problems.append(f"{name}:{line_of(text, match.start())}: exported page keeps a residual relative image path")
        problems += check_export_resources(text, path, name)
        for number, script in enumerate(SCRIPT_TAG.finditer(text), 1):
            if re.search(r"\bsrc\s*=", script.group(1), re.IGNORECASE):
                continue
            body = script.group(2).strip()
            if not body:
                continue
            if node is None:
                notes.append(f"{name}: inline script {number} not syntax-checked (node is unavailable)")
                continue
            handle, script_path = tempfile.mkstemp(suffix=".js")
            try:
                with os.fdopen(handle, "w", encoding="utf-8") as stream:
                    stream.write(body)
                result = subprocess.run([node, "--check", script_path], capture_output=True, text=True)
            finally:
                os.unlink(script_path)
            if result.returncode != 0:
                detail = (result.stderr or result.stdout).strip().splitlines()
                problems.append(f"{name}: inline script {number} fails node --check: {detail[0] if detail else 'unknown error'}")
    return problems, notes, len(pages)


def audit(root: Path = ROOT, strict: bool = False) -> dict:
    files = page_files(root)
    problems: list[str] = []
    notes: list[str] = []
    summary: dict[str, int] = {"files_scanned": len(files)}

    for collected, extra in (check_document(files, root), check_external(files, root),
                             check_reduced_motion(files)):
        problems += collected
        summary.update(extra)

    image_problems, image_notes, image_count = check_images(files, root, strict)
    problems += image_problems
    notes += image_notes
    summary["images"] = image_count

    export_problems, export_notes, export_count = check_exports(root)
    problems += export_problems
    notes += export_notes
    summary["exports"] = export_count

    return {"root": str(root), "strict": strict, "summary": summary, "problems": problems, "notes": notes}


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    unknown = [argument for argument in args if argument != "--strict"]
    if unknown:
        print(f"unknown argument(s): {' '.join(unknown)}", file=sys.stderr)
        return 2
    result = audit(strict="--strict" in args)
    print(json.dumps(result, ensure_ascii=True, indent=2, sort_keys=True))
    if result["problems"]:
        print(f"FAIL: {len(result['problems'])} showcase static audit problem(s).", file=sys.stderr)
        for problem in result["problems"]:
            print(f"  {problem}", file=sys.stderr)
        return 1
    summary = result["summary"]
    print(f"OK: showcase static audit passed ({summary['files_scanned']} files, "
          f"{summary['images']} images, h1={summary['h1']}, "
          f"reduced_motion={summary['reduced_motion']}, exports={summary['exports']}).")
    if result["notes"]:
        print(f"note: {len(result['notes'])} advisory finding(s); run with --strict to treat them as failures.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
