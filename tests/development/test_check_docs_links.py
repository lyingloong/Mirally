"""Offline checks for the repository documentation link and anchor checker."""

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

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

            anchors: dict[Path, set[str]] = {}
            self.assertEqual(checker.check(cross_file, root=root, anchors=anchors), ["b.md -> a.md#乙"])
            self.assertEqual(checker.check(same_file, root=root, anchors=anchors), ["c.md -> #乙"])
            self.assertEqual(checker.check(target, root=root, anchors=anchors), [])
            self.assertEqual(checker.check(valid, root=root, anchors=anchors), [])

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


if __name__ == "__main__":
    unittest.main()
