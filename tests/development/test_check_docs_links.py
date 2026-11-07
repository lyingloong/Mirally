"""Offline checks for the repository documentation link and anchor checker."""

from __future__ import annotations

import importlib.util
import io
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]


def load_checker():
    """Load tools/check_docs_links.py by path; tools/ is not an importable package."""
    path = ROOT / "tools" / "check_docs_links.py"
    spec = importlib.util.spec_from_file_location("check_docs_links_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SlugAndRepositoryTests(unittest.TestCase):
    def test_github_style_slug(self):
        checker = load_checker()
        self.assertEqual(checker.slugify("会话、作业与权限"), "会话作业与权限")
        self.assertEqual(checker.slugify("Skill 兼容"), "skill-兼容")
        self.assertEqual(checker.slugify("**标题**"), "标题")
        self.assertEqual(checker.slugify("`code` 与 空格"), "code-与-空格")

    def test_repository_has_no_broken_links_or_anchors(self):
        checker = load_checker()
        problems, count = checker.collect_problems()
        self.assertEqual(problems, [])
        self.assertGreaterEqual(count, 30)


class AnchorFailureTests(unittest.TestCase):
    def test_anchors_are_checked(self):
        checker = load_checker()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "a.md"
            target.write_text("# 甲\n\n[x](#甲)\n", encoding="utf-8")
            cross_file = root / "b.md"
            cross_file.write_text("[x](a.md#乙)\n", encoding="utf-8")
            same_file = root / "c.md"
            same_file.write_text("[x](#乙)\n", encoding="utf-8")
            valid = root / "d.md"
            valid.write_text("[x](a.md#甲)\n", encoding="utf-8")

            anchors: dict[Path, tuple[set[str], bool]] = {}
            self.assertEqual(checker.check(cross_file, root=root, anchors=anchors), ["b.md -> a.md#乙"])
            self.assertEqual(checker.check(same_file, root=root, anchors=anchors), ["c.md -> #乙"])
            self.assertEqual(checker.check(target, root=root, anchors=anchors), [])
            self.assertEqual(checker.check(valid, root=root, anchors=anchors), [])

    def test_duplicate_headings_get_github_numeric_anchors(self):
        checker = load_checker()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            page = root / "a.md"
            page.write_text(
                "# Examples\n\n## Examples\n\n## Examples\n\n## Examples 1\n",
                encoding="utf-8",
            )
            # GitHub numbers repeats in document order, and a slug that is already taken
            # ("examples-1" from the literal "Examples 1" heading) is numbered again.
            self.assertEqual(checker.heading_anchors(page),
                             {"examples", "examples-1", "examples-2", "examples-1-1"})
            links = root / "b.md"
            links.write_text(
                "[a](a.md#examples)\n[b](a.md#examples-1)\n"
                "[c](a.md#examples-2)\n[d](a.md#examples-1-1)\n",
                encoding="utf-8",
            )
            self.assertEqual(checker.check(links, root=root), [])

    def test_duplicate_heading_numbers_that_do_not_exist_are_reported(self):
        checker = load_checker()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            page = root / "a.md"
            page.write_text(
                "## Examples\n\n## Examples\n\n[self](#examples-1)\n[self](#examples-3)\n",
                encoding="utf-8",
            )
            self.assertEqual(checker.check(page, root=root), ["a.md -> #examples-3"])
            cross = root / "b.md"
            cross.write_text("[x](a.md#examples-2)\n[y](a.md#examples-3)\n", encoding="utf-8")
            self.assertEqual(checker.check(cross, root=root),
                             ["b.md -> a.md#examples-2", "b.md -> a.md#examples-3"])

    def test_unverifiable_headings_are_unknown_not_broken(self):
        # GitHub renders a heading before slugging it; raw HTML and inline links are not
        # reproduced here, so this file's anchors (and the numbering they shift) are only
        # guesses. An unmatched fragment must be left unreported instead of failing.
        checker = load_checker()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            page = root / "a.md"
            page.write_text("## <span>Examples</span>\n\n## [Guide](b.md)\n", encoding="utf-8")
            links = root / "b.md"
            links.write_text("[x](a.md#examples-1)\n[y](a.md#nope)\n", encoding="utf-8")
            self.assertEqual(checker.check(links, root=root), [])

    def test_fenced_code_links_are_ignored(self):
        checker = load_checker()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            page = root / "a.md"
            page.write_text(
                "# 甲\n\n"
                "```powershell\n[x](missing.md#nope)\n[y](#nope)\n```\n\n"
                "~~~\n[z](also-missing.md)\n~~~\n\n"
                "````\n# 围栏里的标题\n````\n",
                encoding="utf-8",
            )
            self.assertEqual(checker.check(page, root=root), [])
            headings = root / "b.md"
            headings.write_text("[x](a.md#围栏里的标题)\n", encoding="utf-8")
            self.assertEqual(checker.check(headings, root=root), ["b.md -> a.md#围栏里的标题"])


class RepositoryLinkTests(unittest.TestCase):
    """GitHub absolute links that point at this repository are checked offline."""

    def setUp(self):
        self.checker = load_checker()
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def write(self, relative: str, text: str) -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def problems(self, text: str, repository: str | None = "lyingloong/Mirally") -> list[str]:
        page = self.write("page.md", text)
        with mock.patch.object(self.checker, "git_repository", return_value=repository):
            return self.checker.check(page, root=self.root)

    def test_remote_url_forms(self):
        self.assertEqual(self.checker._repository_slug("git@github.com:o/r.git"), "o/r")
        self.assertEqual(self.checker._repository_slug("https://github.com/o/r.git"), "o/r")
        self.assertEqual(self.checker._repository_slug("ssh://git@github.com/o/r"), "o/r")
        self.assertIsNone(self.checker._repository_slug("https://gitlab.com/o/r"))

    def test_repository_identifier_comes_from_git_remote(self):
        clone = self.root / "clone"
        subprocess.run(["git", "init", "-q", str(clone)], check=True)
        subprocess.run(["git", "-C", str(clone), "remote", "add", "origin",
                        "git@github.com:example/Mirally.git"], check=True)
        with mock.patch.dict(os.environ, {"GITHUB_REPOSITORY": ""}):
            self.assertEqual(self.checker.git_repository(clone), "example/Mirally")

    def test_github_repository_environment_fallback(self):
        plain = self.root / "plain"
        subprocess.run(["git", "init", "-q", str(plain)], check=True)
        with mock.patch.dict(os.environ, {"GITHUB_REPOSITORY": "acme/widgets"}):
            self.assertEqual(self.checker.git_repository(plain), "acme/widgets")

    def test_repository_paths_are_checked_offline(self):
        self.write("docs/exists.md", "# ok\n")
        text = (
            "[ok](https://github.com/lyingloong/Mirally/blob/main/docs/exists.md)\n"
            "[line](https://github.com/lyingloong/Mirally/blob/main/docs/exists.md#L3)\n"
            "[dir](https://github.com/lyingloong/Mirally/tree/main/docs)\n"
            "[missing](https://github.com/lyingloong/Mirally/blob/main/docs/missing.md)\n"
            "[missing dir](https://github.com/lyingloong/Mirally/tree/main/nope)\n"
        )
        self.assertEqual(self.problems(text), [
            "page.md -> https://github.com/lyingloong/Mirally/blob/main/docs/missing.md"
            "（本地不存在：docs/missing.md）",
            "page.md -> https://github.com/lyingloong/Mirally/tree/main/nope（本地不存在：nope）",
        ])

    def test_historical_refs_are_shape_checked_only(self):
        text = (
            "[old](https://github.com/lyingloong/Mirally/blob/2eeb778a/mirally/removed.py)\n"
            "[tree](https://github.com/lyingloong/Mirally/tree/2eeb778a/integrations/openclaw)\n"
            "[escape](https://github.com/lyingloong/Mirally/blob/2eeb778a/../secrets)\n"
        )
        self.assertEqual(self.problems(text), [
            "page.md -> https://github.com/lyingloong/Mirally/blob/2eeb778a/../secrets"
            "（路径不能包含 '..'：../secrets）",
        ])

    def test_issue_and_pull_numbers_are_format_checked(self):
        text = (
            "[issue](https://github.com/lyingloong/Mirally/issues/12)\n"
            "[pull](https://github.com/lyingloong/Mirally/pull/34)\n"
            "[bad pull](https://github.com/lyingloong/Mirally/pull/abc)\n"
            "[bad issue](https://github.com/lyingloong/Mirally/issues/0)\n"
        )
        self.assertEqual(self.problems(text), [
            "page.md -> https://github.com/lyingloong/Mirally/pull/abc（pull 编号必须是正整数：abc）",
            "page.md -> https://github.com/lyingloong/Mirally/issues/0（issues 编号必须是正整数：0）",
        ])

    def test_other_repositories_and_external_urls_are_skipped(self):
        text = (
            "[foreign](https://github.com/other/repo/blob/main/nope.py)\n"
            "[external](https://example.com/nope)\n"
            "[mail](mailto:someone@example.com)\n"
            "[site](https://github.com/lyingloong/Mirally-site)\n"
        )
        self.assertEqual(self.problems(text), [])

    def test_repository_links_are_skipped_without_a_repository(self):
        offline = self.root / "offline"
        subprocess.run(["git", "init", "-q", str(offline)], check=True)
        page = offline / "page.md"
        page.write_text("[x](https://github.com/lyingloong/Mirally/blob/main/nope.md)\n",
                        encoding="utf-8")
        with mock.patch.dict(os.environ, {"GITHUB_REPOSITORY": ""}):
            self.assertIsNone(self.checker.git_repository(offline))
            self.assertEqual(self.checker.check(page, root=offline), [])

    def test_main_reports_skipped_repository_checks(self):
        offline = self.root / "offline-main"
        subprocess.run(["git", "init", "-q", str(offline)], check=True)
        (offline / "README.md").write_text(
            "[x](https://github.com/lyingloong/Mirally/blob/main/nope.md)\n", encoding="utf-8")
        stdout, stderr = io.StringIO(), io.StringIO()
        with mock.patch.object(self.checker, "ROOT", offline), \
                mock.patch.dict(os.environ, {"GITHUB_REPOSITORY": ""}), \
                mock.patch.object(self.checker, "git_repository", return_value=None), \
                redirect_stdout(stdout), redirect_stderr(stderr):
            code = self.checker.main()
        self.assertEqual(code, 0)
        self.assertIn("OK: 1 Markdown files", stdout.getvalue())
        self.assertIn("skipped GitHub absolute link checks", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
