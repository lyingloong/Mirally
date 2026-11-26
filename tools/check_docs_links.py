"""Check relative Markdown links and in-repository heading anchors.

Run from the repository root:

    python tools/check_docs_links.py

External URLs and fenced code blocks are ignored. Repository-internal fragments
(`other.md#heading` and `#heading`) are resolved against GitHub-style heading
slugs; repeated headings receive GitHub's `-1`, `-2`, ... suffixes in document
order. A heading is rendered before GitHub slugs it, so headings containing raw
HTML or inline links/images cannot be reproduced by this approximation: files
with such headings are only partially verified, and unmatched fragments in them
are left unreported instead of failing the gate.

GitHub absolute links that point at this very repository (`blob`/`tree`/
`issues`/`pull`) are checked offline as well. The repository identifier is read
from the `origin` git remote (or `GITHUB_REPOSITORY`), never hard-coded; links to
other repositories stay skipped. For `main`/`master`/`HEAD` refs the target path
must exist in this checkout, historical refs are only shape-checked, and issue
and pull numbers must be positive integers. Without a repository identifier these
checks are skipped and the reason is printed once.

Upstream kernel and frontend source documentation is not scanned;
`kernel/dsh/UPSTREAM.md` is an exception because Mirally maintains it and it
links into product documentation.

Beyond resolving written links, the topic documentation index is checked for
coverage: `docs/README.md` must link every `*.md` under `docs/architecture`,
`docs/reference`, `docs/standards`, `docs/design` and `docs/product` directly,
and `docs/development/README.md` must do the same for `docs/development`. A page
missing from its index is reported as `docs/README.md -> docs/<sub>/<page>.md`.
Only entries a reader can actually click count: inline links (a fragment or a
title is allowed) and reference links whose label resolves through a definition
on the same index page. HTML comments, fenced code and inline code render
literally or not at all, images never navigate, and external targets leave the
docs tree, so none of those counts as an index entry; a definition without any
reference is not a link either. An index page is not required to link itself.
This stays offline and reads the same files as the link check.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
SKIP_PARTS = {"node_modules", ".venv", ".git", "output", ".artifacts", "dsh"}
# The kernel tree is upstream source, but this one file is maintained by Mirally.
FORCE_CHECK = {ROOT / "kernel" / "dsh" / "UPSTREAM.md"}
LINK = re.compile(r"!?\[[^\]]*\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
FENCE = re.compile(r"```.*?```|~~~.*?~~~", re.DOTALL)
# Index coverage only sees what the reader is shown, so HTML comments join fenced
# code in being removed before scanning. The alternation is scanned left to right,
# so a fence marker inside a comment cannot pair up with a real fence, and vice versa.
NON_CONTENT = re.compile(r"<!--.*?-->|```.*?```|~~~.*?~~~", re.DOTALL)
# A clickable inline link: a leading `!` renders an image and a backslash escapes
# the bracket, so neither form navigates to the target the way an index entry must.
INLINE_LINK = re.compile(r"(?<![\\!])\[[^\]]*\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
# Link reference definitions (`[label]: target`, optionally `<braced>` plus a title)
# and the reference forms that consume them: `[text][label]`, `[text][]`, `[text]`.
LINK_DEF = re.compile(r"^[ \t]{0,3}\[([^\]]+)\]:[ \t]*<?([^\s>]+)>?", re.M)
REF_LINK = re.compile(r"(?<![\\!])\[([^\]]*)\]\[([^\]]*)\]")
SHORTCUT_LINK = re.compile(r"(?<![\\!\[])\[([^\]]+)\](?![\[(])")
HEADING = re.compile(r"^#{1,6}\s+(.+?)\s*#*\s*$", re.M)
# GitHub renders a heading (including inline links/images and HTML) before slugging it.
HEADING_MARKUP = re.compile(r"<[^>]+>|!?\[[^\]]*\]\([^)]*\)")
GITHUB_LINK = re.compile(
    r"https?://(?:www\.)?github\.com/(?P<owner>[^/\s]+)/(?P<repo>[^/\s#?]+)/"
    r"(?P<kind>blob|tree|issues|pull)(?:/(?P<rest>[^\s#?]*))?"
)
GITHUB_REMOTE = re.compile(r"github\.com[:/](?P<owner>[^/\s]+)/(?P<repo>[^/\s]+?)(?:\.git)?/?$")
REPOSITORY_ARGUMENT = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
LOCAL_REFS = {"main", "master", "HEAD"}
# GitHub pages under /issues and /pull that carry no issue/pull number to validate.
# First segments: the list view, the new-issue form, and the number-less list views
# (assigned/mentions/...). Longer entries: sub-pages whose whole rest is number-less
# (the template chooser, the per-user lists). Any other first segment must be a number.
NON_NUMBER_PAGES = {
    "issues": {
        "", "new", "assigned", "mentions", "created_by", "subscribed",
        "new/choose", "assigned/*", "mentions/*", "created_by/*", "subscribed/*",
    },
    "pull": {""},
}
NUMBER = re.compile(r"\d+")
PULL_PATCH = re.compile(r"\d+\.(?:diff|patch)")
# Index page -> docs sub-trees whose pages it must link directly. Without this the
# checker only resolves links that were written, so a new page nobody indexes stays
# invisible: it is reported as a missing `index -> page` problem instead.
INDEX_PAGES = {
    "docs/README.md": ("architecture", "reference", "standards", "design", "product"),
    "docs/development/README.md": ("development",),
}


def _repository_slug(value: str) -> str | None:
    """``owner/repo`` from a git remote URL or a `GITHUB_REPOSITORY` value."""
    value = value.strip()
    match = GITHUB_REMOTE.search(value)
    if match:
        return f"{match.group('owner')}/{match.group('repo')}"
    if REPOSITORY_ARGUMENT.fullmatch(value):
        return value
    return None


def _discover_repository(root: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        result = None
    if result is not None and result.returncode == 0:
        slug = _repository_slug(result.stdout)
        if slug:
            return slug
    return _repository_slug(os.environ.get("GITHUB_REPOSITORY", ""))


_REPOSITORIES: dict[Path, str | None] = {}


def git_repository(root: Path = ROOT) -> str | None:
    """This checkout's ``owner/repo`` from git, or None when it cannot be derived."""
    key = Path(root).resolve()
    if key not in _REPOSITORIES:
        _REPOSITORIES[key] = _discover_repository(key)
    return _REPOSITORIES[key]


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


def repository_links(target: str, repository: str | None, root: Path = ROOT) -> list[str]:
    """Offline findings for a GitHub link that may point at this repository.

    Empty when the target is not one of GitHub's `blob`/`tree`/`issues`/`pull`
    links, points at another repository, or the repository identifier is unknown
    (then the whole group is skipped and `main()` prints the reason once). Never
    touches the network: `main`/`master`/`HEAD` refs must exist in `root`, every
    other ref is only shape-checked so historical links keep working. For issues
    and pulls only a pure-number first segment is validated; numbered sub-pages
    (`/pull/34/files`, `/pull/34.diff`, ...) and GitHub's number-less issue pages
    (`/issues`, `/issues/new`, `/issues/new/choose`, `/issues/assigned/<user>`,
    ...) are allowed instead of reported.
    """
    if repository is None:
        return []
    url = target.split("#", 1)[0].split("?", 1)[0]
    match = GITHUB_LINK.fullmatch(url)
    if not match:
        return []
    if f"{match.group('owner')}/{match.group('repo')}".lower() != repository.lower():
        return []
    kind = match.group("kind")
    rest = (match.group("rest") or "").strip("/")
    if kind in {"issues", "pull"}:
        number, _, suffix = rest.partition("/")
        if NUMBER.fullmatch(number):
            # /<n>, /<n>/files, /<n>/commits/<sha>, ...: only the number is checkable.
            return [] if int(number) else [f"（{kind} 编号必须是正整数：{rest}）"]
        pages = NON_NUMBER_PAGES[kind]
        if (not suffix and number in pages) or rest in pages:
            # /issues (list), /issues/new, /issues/new/choose, /pull (list).
            return []
        if suffix and (f"{number}/*" in pages or f"{number}/{suffix}" in pages):
            # /issues/assigned/<user>, /issues/mentions/<user>: no number to check.
            return []
        if kind == "pull" and PULL_PATCH.fullmatch(rest):
            return []  # /pull/<n>.diff and /pull/<n>.patch
        return [f"（{kind} 编号必须是正整数：{rest or '空'}）"]
    rest = unquote(rest).strip("/")
    if ".." in rest.split("/"):
        # The ref is part of `rest`: reject parent segments there and in the path.
        return [f"（ref/path 不能包含 '..'：{rest}）"]
    ref, _, path = rest.partition("/")
    if not ref:
        return [f"（{kind} 缺少 ref）"]
    path = path.strip("/")
    if not path:
        # `/tree/<ref>` is the repository root; `/blob/<ref>` must name a file.
        return [] if kind == "tree" else ["（blob 链接缺少文件路径）"]
    if ref not in LOCAL_REFS:
        return []
    if not (root / path).exists():
        return [f"（本地不存在：{path}）"]
    return []


def check(path: Path, *, root: Path = ROOT,
          anchors: dict[Path, tuple[set[str], bool]] | None = None) -> list[str]:
    """Relative link and anchor problems in one file; fenced code is ignored."""
    text = FENCE.sub("", path.read_text(encoding="utf-8"))
    cache: dict[Path, tuple[set[str], bool]] = {} if anchors is None else anchors
    repository = git_repository(root)
    missing: list[str] = []
    for match in LINK.finditer(text):
        target = match.group(1).strip()
        if not target:
            continue
        if target.startswith(("http://", "https://")):
            # Traffic to other hosts and repositories stays skipped (offline check);
            # only GitHub links to this very repository are verifiable here.
            for detail in repository_links(target, repository, root):
                missing.append(f"{path.relative_to(root)} -> {target}{detail}")
            continue
        if target.startswith(("mailto:", "tel:", "data:")):
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


def path_key(path: Path) -> str:
    """Case-insensitive comparison key for a path on this checkout."""
    return os.path.normcase(os.path.normpath(str(path)))


def _strip_code_spans(text: str) -> str:
    """Drop inline code spans; a span with no matching closer stays literal text.

    Following CommonMark, a span opens at a backtick string and closes only at a
    backtick string of the same length, so runs around the candidate are checked.
    """
    out: list[str] = []
    n = len(text)
    i = 0
    while i < n:
        if text[i] != "`":
            out.append(text[i])
            i += 1
            continue
        j = i
        while j < n and text[j] == "`":
            j += 1
        run, close, closed = j - i, i, False
        while True:
            close = text.find("`" * run, close + 1)
            if close == -1:
                break
            end = close + run
            if text[close - 1] != "`" and (end >= n or text[end] != "`"):
                closed = True
                break
            close = end - 1  # inside a longer run; keep looking for an equal-length one
        if closed:
            out.append(" ")  # the span's content renders literally, never as a link
            i = close + run
        else:
            out.append("`" * run)
            i = j
    return "".join(out)


def readable_text(text: str) -> str:
    """The text a reader is shown: fenced code, HTML comments and code spans removed.

    Those three render literally (or not at all), so a `[x](y.md)` written inside one
    of them is not a link the reader can click.
    """
    return _strip_code_spans(NON_CONTENT.sub("", text))


def _label_key(label: str) -> str:
    """CommonMark compares link labels case-insensitively across collapsed whitespace."""
    return " ".join(label.split()).casefold()


def _reference_target(definitions: dict[str, str], label: str) -> str | None:
    """The target a reference label resolves to, or None when no such link exists.

    Footnote-style labels (`[^1]`) belong to GFM footnotes, not link references.
    """
    label = label.strip()
    if not label or label.startswith("^"):
        return None
    return definitions.get(_label_key(label))


def linked_paths(path: Path, root: Path = ROOT) -> set[str]:
    """Keys of the targets one file links to, counting only links a reader can click.

    The `LINK`/`FENCE` parsing and path resolution of `check()` are reused (the inline
    form additionally drops image and backslash-escaped brackets), together with the
    same skipping of empty and external targets. Reference links count when their
    label resolves through a definition in the same file, which is what makes them
    clickable; a definition without any reference renders as nothing and is not a
    link. Existence and anchors stay `check()`'s business.
    """
    text = readable_text(path.read_text(encoding="utf-8"))
    definitions: dict[str, str] = {}
    for match in LINK_DEF.finditer(text):
        label = match.group(1).strip()
        if label and not label.startswith("^"):
            definitions.setdefault(_label_key(label), match.group(2))
    # The renderer removes definitions from the content; without this a definition
    # line like `[note]: page.md` would feed its own `[note]` shortcut below.
    text = LINK_DEF.sub(" ", text)
    targets = [match.group(1) for match in INLINE_LINK.finditer(text)]
    for match in REF_LINK.finditer(text):
        target = _reference_target(definitions, match.group(2).strip() or match.group(1))
        if target:
            targets.append(target)
    for match in SHORTCUT_LINK.finditer(text):
        target = _reference_target(definitions, match.group(1))
        if target:
            targets.append(target)
    keys: set[str] = set()
    for target in targets:
        target = target.strip()
        if not target:
            continue
        if target.startswith(("http://", "https://", "mailto:", "tel:", "data:")):
            continue
        relative = target.partition("#")[0]
        relative = unquote(relative.replace("\\", "/"))
        if not relative:
            continue
        resolved = (root / relative.lstrip("/")) if relative.startswith("/") else (path.parent / relative)
        keys.add(path_key(resolved))
    return keys


def index_problems(root: Path = ROOT) -> list[str]:
    """Index entries missing in `INDEX_PAGES`, as `docs/README.md -> docs/...` items."""
    problems: list[str] = []
    for index_name, directories in INDEX_PAGES.items():
        index = root / index_name
        if not index.is_file():
            continue
        linked = linked_paths(index, root)
        for name in directories:
            directory = root / "docs" / name
            if not directory.is_dir():
                continue
            for target in sorted(directory.rglob("*.md")):
                relative = target.relative_to(root)
                # An index page does not have to link itself, and skipped trees
                # (`node_modules/`, the kernel tree, ...) are not topic documentation.
                if target == index or any(part in SKIP_PARTS for part in relative.parts):
                    continue
                if path_key(target) not in linked:
                    problems.append(f"{index_name} -> {relative.as_posix()}")
    return problems


def collect_problems() -> tuple[list[str], int]:
    """(problems, scanned file count); the running check and the tests share this."""
    problems: list[str] = []
    files = owned_files()
    anchors: dict[Path, tuple[set[str], bool]] = {}
    for path in files:
        problems.extend(check(path, anchors=anchors))
    problems.extend(index_problems(ROOT))
    return problems, len(files)


def main() -> int:
    if git_repository(ROOT) is None:
        print(
            "note: skipped GitHub absolute link checks for this repository: "
            "no git remote origin and no GITHUB_REPOSITORY.",
            file=sys.stderr,
        )
    problems, files = collect_problems()
    if problems:
        print(f"{len(problems)} broken relative link(s), anchor(s) or missing index entry(ies):")
        for problem in problems:
            print(f"  {problem}")
        return 1
    print(f"OK: {files} Markdown files, all relative links and anchors resolve; "
          f"every topic docs page is indexed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
