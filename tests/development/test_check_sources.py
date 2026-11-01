"""Tests for tools/check_sources.py (pure standard library, no venv required).

Every tree fixture writes into a scanned directory (``tools/``, ``docs/``,
``config/``) so the cases exercise the same owned-files selection the real
repository uses.
"""
from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("check_sources", ROOT / "tools" / "check_sources.py")
check_sources = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(check_sources)


class CheckSourcesTests(unittest.TestCase):
    def temp_root(self) -> Path:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        return Path(directory.name)

    def write(self, root: Path, name: str, text: str) -> None:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def problems(self, root: Path) -> list[str]:
        return [problem for path in check_sources.owned_files(root) for problem in check_sources.check(path, root=root)]

    def test_clean_tree_is_green(self) -> None:
        root = self.temp_root()
        self.write(root, "docs/a.md", "# Title\n")
        self.write(root, "tools/a.py", "value = 1\n")
        self.write(root, "tools/sub/b.py", "def f():\n    return 2\n")
        self.assertEqual(len(check_sources.owned_files(root)), 3)
        self.assertEqual(self.problems(root), [])

    def test_left_marker_is_reported(self) -> None:
        root = self.temp_root()
        self.write(root, "docs/b.md", "<<<<<<< HEAD\n")
        self.assertEqual(self.problems(root), ["docs/b.md:1: leftover merge-conflict marker"])

    def test_right_and_base_markers_are_reported(self) -> None:
        root = self.temp_root()
        self.write(root, "docs/c.md", ">>>>>>> branch\n")
        self.write(root, "config/d.txt", "|||||||\n")
        self.assertEqual(self.problems(root), [
            "config/d.txt:1: leftover merge-conflict marker",
            "docs/c.md:1: leftover merge-conflict marker",
        ])

    def test_setext_heading_is_not_a_marker(self) -> None:
        root = self.temp_root()
        self.write(root, "docs/e.md", "标题\n=======\n")
        self.assertEqual(self.problems(root), [])

    def test_syntax_error_is_reported(self) -> None:
        root = self.temp_root()
        self.write(root, "tools/f.py", "def broken(:\n")
        problems = self.problems(root)
        self.assertEqual(len(problems), 1, problems)
        self.assertTrue(problems[0].startswith("tools/f.py:1: syntax error: "), problems[0])

    def test_skipped_directories_are_ignored(self) -> None:
        root = self.temp_root()
        self.write(root, "tools/node_modules/x.md", "<<<<<<< HEAD\n")
        self.assertNotIn(root / "tools" / "node_modules" / "x.md", check_sources.owned_files(root))
        self.assertEqual(self.problems(root), [])

    def test_repository_is_green(self) -> None:
        files = check_sources.owned_files()
        problems = [problem for path in files for problem in check_sources.check(path)]
        self.assertEqual(problems, [])
        self.assertGreaterEqual(len(files), 300)


if __name__ == "__main__":
    unittest.main()
