"""Read-only static audit of the Mirally showcase site (offline, standard library only).

Run from the repository root:

    python tools/check_showcase.py            # standard audit
    python tools/check_showcase.py --strict   # also require aria-hidden/role=presentation for alt=""

The audit reads only tracked pages under ``showcase/``: document metadata
(``lang``, ``<title>``, viewport, exactly one ``<h1>``), external http(s)
references (upstream license/credits files are exempt through an explicit
list), per-tag ``<img>`` alt coverage, ``prefers-reduced-motion`` support and,
when ``showcase/exports/*.html`` exists, the offline self-containment and
script syntax of that export. Nothing is written and nothing is downloaded;
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
SHOWCASE = "showcase"
PAGE_SUFFIXES = {".html", ".jsx", ".js", ".mjs", ".ts", ".tsx", ".css"}
# Upstream license/credits files legitimately cite upstream URLs; they are not
# page resources, so the external-reference check excludes them explicitly.
EXTERNAL_WHITELIST = (
    "showcase/public/CREDITS.md",
    "showcase/public/LICENSES.txt",
    "showcase/public/fonts/OFL.txt",
)
HTTP = re.compile(r"https?://[^\s\"'<>)]+")
IMG_TAG = re.compile(r"<img\b[^>]*>", re.IGNORECASE | re.DOTALL)
ALT_ATTR = re.compile(r"\balt\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|\{([^}]*)\})", re.IGNORECASE)
H1 = re.compile(r"<h1\b", re.IGNORECASE)
REDUCED_MOTION = re.compile(r"prefers-reduced-motion", re.IGNORECASE)
SCRIPT_TAG = re.compile(r"<script\b([^>]*)>(.*?)</script>", re.IGNORECASE | re.DOTALL)
EXPORT_ATTR_HTTP = re.compile(r"\b(?:src|href)\s*=\s*[\"']https?://", re.IGNORECASE)
RESIDUAL_IMAGE_PATH = re.compile(r"[\"'](?:\./)?images/")


def page_files(root: Path) -> list[Path]:
    base = root / SHOWCASE
    files = [base / "index.html"]
    source = base / "src"
    if source.is_dir():
        files += sorted(p for p in source.rglob("*") if p.is_file() and p.suffix in PAGE_SUFFIXES)
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


def check_external(files: list[Path], root: Path) -> tuple[list[str], dict[str, int]]:
    problems: list[str] = []
    exempt = 0
    for path in files:
        name = label(path, root)
        for match in HTTP.finditer(read(path)):
            if name in EXTERNAL_WHITELIST:
                exempt += 1
                continue
            problems.append(f"{name}:{line_of(read(path), match.start())}: external reference {match.group(0)}")
    return problems, {"license_links": exempt}


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
            alt = ALT_ATTR.search(tag)
            if alt is None:
                problems.append(f"{where}: <img> is missing an alt attribute")
                continue
            value = next((group for group in alt.groups() if group is not None), "")
            if value.strip() or alt.group(3) is not None:
                continue  # descriptive or dynamic alt text: author intent stated
            marked = re.search(r"\baria-hidden\s*=\s*[\"']true[\"']|\brole\s*=\s*[\"']presentation[\"']", tag, re.IGNORECASE)
            if marked:
                continue
            message = f"{where}: decorative alt=\"\" without aria-hidden or role=presentation"
            (problems if strict else notes).append(message)
    return problems, notes, total


def check_reduced_motion(files: list[Path]) -> tuple[list[str], dict[str, int]]:
    uses = sum(len(REDUCED_MOTION.findall(read(path))) for path in files if path.suffix in {".css", ".js", ".jsx", ".mjs"})
    problems = [] if uses else ["showcase: no prefers-reduced-motion handling found in styles or scripts"]
    return problems, {"reduced_motion": uses}


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
