"""Check the diff before skipping product CI for repository-only changes."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_FILES = {
    "README.md", "AGENTS.md", "CONTRIBUTING.md", "CHANGELOG.md", "LICENSE",
    ".github/pull_request_template.md", "tools/testing/README.md",
    "tools/check_docs_links.py", "tools/check_sources.py",
    "tools/check_structure.py", "tools/check_local_artifacts.py",
}


def requires_product(paths: list[str]) -> bool:
    # Do not broadly exclude Markdown or tools: runtime Skills and build scripts
    # live there too. New/unknown paths must opt in to the full product checks.
    def repository_only(path: str) -> bool:
        return (
            path in REPOSITORY_FILES
            or (path.startswith("docs/") and path.endswith(".md"))
            or path.startswith(("tools/dev_workflow/", "tests/development/"))
        )

    return not paths or not all(repository_only(path) for path in paths)


def valid_sha(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{40}", value) is not None and value != "0" * 40


def select_scope(event_name: str, event: dict, head: str, root: Path) -> tuple[bool, str]:
    if event_name not in {"pull_request", "push"}:
        return True, "Manual or unrecognized event: run all checks."
    try:
        if event_name == "pull_request":
            base = event["pull_request"]["base"]["sha"]
        else:
            base = event["before"]
            if event["after"] != head:
                return True, "Push SHA does not match the checkout: run all checks."
        if not valid_sha(base) or not valid_sha(head):
            return True, "Missing or invalid comparison SHA: run all checks."

        def git(*arguments: str) -> bytes:
            return subprocess.run(
                ["git", *arguments], cwd=root, check=True,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30,
            ).stdout

        if git("rev-parse", "HEAD").decode("ascii").strip() != head:
            return True, "Checkout differs from the event SHA: run all checks."
        # PR HEAD is GitHub's synthetic merge. Its base is available with depth 2.
        # Push compares the entire before/after range, never just the last commit.
        # No rename detection: moving product code into docs must still run tests.
        changed = git("diff", "--name-only", "--no-renames", "-z", base, head, "--")
        paths = [path.decode("utf-8") for path in changed.split(b"\0") if path]
    except (KeyError, TypeError, ValueError, OSError, subprocess.SubprocessError):
        # A shallow multi-commit push may lack before. Do not fetch extra history
        # or silently treat an incomplete comparison as documentation-only.
        return True, "Comparison unavailable: run all checks."
    product = requires_product(paths)
    reason = "Product, unknown or empty diff: run all checks." if product else "Only documentation/development tooling changed: skip product build and tests."
    return product, reason


def main() -> int:
    try:
        event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
        product, reason = select_scope(
            os.environ.get("GITHUB_EVENT_NAME", ""), event,
            os.environ.get("GITHUB_SHA", ""), ROOT,
        )
    except (KeyError, ValueError, OSError):
        product, reason = True, "Event unavailable: run all checks."
    value = "true" if product else "false"
    print(f"product={value}: {reason}")
    if output := os.environ.get("GITHUB_OUTPUT"):
        with Path(output).open("a", encoding="utf-8") as stream:
            stream.write(f"product={value}\n")
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(summary).open("a", encoding="utf-8") as stream:
            stream.write(f"CI scope: {reason}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
