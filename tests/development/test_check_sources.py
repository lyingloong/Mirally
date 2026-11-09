"""Tests for tools/check_sources.py (pure standard library, no venv required).

Every tree fixture writes into a directory the guard scans (``tools/``,
``docs/``, ``config/``, the Mirally kernel product chain, or the owned frontend
paths) so the cases exercise the same owned-files selection the real repository
uses.
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

    def write_bytes(self, root: Path, name: str, data: bytes) -> None:
        """Exact bytes, so line endings and trailing whitespace survive the fixture."""
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

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

    def test_skipped_directories_are_pruned_before_descent(self) -> None:
        root = self.temp_root()
        self.write(root, "tools/node_modules/x.md", "<<<<<<< HEAD\n")
        self.assertNotIn(root / "tools" / "node_modules" / "x.md", check_sources.owned_files(root))
        self.assertNotIn(root / "tools" / "node_modules" / "x.md", check_sources._walk_files(root / "tools"))
        self.assertEqual(self.problems(root), [])

    def test_website_jsx_is_owned_but_downloads_and_generated_output_are_pruned(self) -> None:
        root = self.temp_root()
        self.write(root, "website/src/App.jsx", "<<<<<<< HEAD\n")
        for name in ("website/node_modules/pkg/index.js", "website/dist/index.html",
                     "website/exports/standalone.html", "website/.vite/cache.json",
                     "website/.vite-temp/vite.config.js"):
            self.write(root, name, "<<<<<<< HEAD\n")
        self.assertEqual(self.problems(root), ["website/src/App.jsx:1: leftover merge-conflict marker"])
        self.assertEqual(check_sources.owned_files(root), [root / "website/src/App.jsx"])

    def test_owned_kernel_and_frontend_sources_are_scanned(self) -> None:
        root = self.temp_root()
        self.write(root, "kernel/dsh/packages/mirally/mirally-product/src/channel.mjs", "<<<<<<< HEAD\n")
        self.write(root, "frontend/control-ui/upstream-files.json",
                   '{"commit": "fixture", "files": {"ui/src/app/app-root.ts": {"gitBlob": "x"}}}\n')
        self.write(root, "frontend/control-ui/local-changes.json", '{"ui/src/styles/patched.css": "note"}\n')
        for name in (
            "frontend/control-ui/ui/mobile.html",
            "frontend/control-ui/ui/src/app/mirally-client.ts",
            "frontend/control-ui/ui/src/components/mirally-mark.ts",
            "frontend/control-ui/ui/src/i18n/mirally-product-copy.ts",
            "frontend/control-ui/ui/src/lib/mirally-browser-path.ts",
            "frontend/control-ui/ui/src/mobile/main.ts",
            "frontend/control-ui/ui/src/pages/mirally/page.ts",
            "frontend/control-ui/ui/src/styles/patched.css",
        ):
            self.write(root, name, "<<<<<<< HEAD\n")
        self.assertEqual(self.problems(root), [
            "frontend/control-ui/ui/mobile.html:1: leftover merge-conflict marker",
            "frontend/control-ui/ui/src/app/mirally-client.ts:1: leftover merge-conflict marker",
            "frontend/control-ui/ui/src/components/mirally-mark.ts:1: leftover merge-conflict marker",
            "frontend/control-ui/ui/src/i18n/mirally-product-copy.ts:1: leftover merge-conflict marker",
            "frontend/control-ui/ui/src/lib/mirally-browser-path.ts:1: leftover merge-conflict marker",
            "frontend/control-ui/ui/src/mobile/main.ts:1: leftover merge-conflict marker",
            "frontend/control-ui/ui/src/pages/mirally/page.ts:1: leftover merge-conflict marker",
            "frontend/control-ui/ui/src/styles/patched.css:1: leftover merge-conflict marker",
            "kernel/dsh/packages/mirally/mirally-product/src/channel.mjs:1: leftover merge-conflict marker",
        ])

    def test_javascript_cpp_and_config_templates_are_scanned(self) -> None:
        root = self.temp_root()
        for name in (
            "mirally/assets/control-ui-mirally.js",
            "mirally/platform/activity/native/desktop_wgc.cpp",
            "config/mirally/activity.yaml.template",
        ):
            self.write(root, name, "<<<<<<< HEAD\n")
        self.assertEqual(self.problems(root), [
            "config/mirally/activity.yaml.template:1: leftover merge-conflict marker",
            "mirally/assets/control-ui-mirally.js:1: leftover merge-conflict marker",
            "mirally/platform/activity/native/desktop_wgc.cpp:1: leftover merge-conflict marker",
        ])

    def test_frontend_manifest_itself_is_scanned(self) -> None:
        root = self.temp_root()
        self.write(root, "frontend/control-ui/local-changes.json", "<<<<<<< HEAD\n")
        self.assertEqual(self.problems(root), [
            "frontend/control-ui/local-changes.json:1: leftover merge-conflict marker",
        ])

    def test_upstream_and_generated_paths_are_not_scanned(self) -> None:
        root = self.temp_root()
        self.write(root, "frontend/control-ui/upstream-files.json",
                   '{"commit": "fixture", "files": {"ui/src/app/app-root.ts": {"gitBlob": "x"},'
                   ' "ui/src/mobile/mobile.css": {"gitBlob": "y"}}}\n')
        self.write(root, "frontend/control-ui/local-changes.json", "{}\n")
        for name in (
            "kernel/dsh/src/upstream.md",
            "frontend/control-ui/ui/src/app/app-root.ts",
            "frontend/control-ui/ui/src/mobile/mobile.css",
            "frontend/control-ui/ui/dist/bundle.css",
            "kernel/dsh/packages/mirally/mirally-product/dist/generated.css",
        ):
            self.write(root, name, "<<<<<<< HEAD\n")
        self.assertEqual(self.problems(root), [])

    def test_repository_is_green(self) -> None:
        files = check_sources.owned_files()
        problems = [problem for path in files for problem in check_sources.check(path)]
        self.assertEqual(problems, [])
        self.assertGreaterEqual(len(files), 300)

    def test_whitespace_and_line_ending_damage_is_reported(self) -> None:
        root = self.temp_root()
        self.write_bytes(root, "docs/trail.md", b"text \n")           # space before the newline
        self.write_bytes(root, "docs/tab.md", b"value\t\n")           # tab before the newline
        self.write_bytes(root, "docs/blank.md", b"a\n  \nb\n")        # whitespace-only line
        self.write_bytes(root, "docs/end.md", b"# Title\nno newline at end")  # missing final newline
        self.write_bytes(root, "docs/mixed.txt", b"first\r\nsecond\n")        # CRLF and LF in one file
        self.assertEqual(self.problems(root), [
            "docs/blank.md:2: trailing whitespace",
            "docs/end.md:2: missing final newline",
            "docs/mixed.txt:1: mixed CRLF and LF line endings",
            "docs/tab.md:1: trailing whitespace",
            "docs/trail.md:1: trailing whitespace",
        ])

    def test_clean_line_endings_are_not_reported(self) -> None:
        root = self.temp_root()
        self.write_bytes(root, "docs/empty.txt", b"")                    # empty file is exempt
        self.write_bytes(root, "docs/crlf.txt", b"one\r\ntwo\r\n")       # consistent CRLF is fine
        self.write_bytes(root, "docs/lf.txt", b"one\ntwo\n")
        self.write_bytes(root, "docs/last-line-space.md", b"trailing space in prose \n")
        self.write_bytes(root, "docs/emoji.md", "\U0001F9A6 tail\n".encode("utf-8"))
        self.assertEqual(self.problems(root), [
            "docs/last-line-space.md:1: trailing whitespace",
        ])

    def test_crlf_exemption_is_explicit_and_narrow(self) -> None:
        root = self.temp_root()
        allowed, reason = next(iter(check_sources.CRLF_EXEMPT.items()))
        self.assertTrue(reason.strip(), "every CRLF exemption needs a written reason")
        directory, name = allowed.rsplit("/", 1)
        suffix = Path(name).suffix
        trailing = f"{directory}/trailing{suffix}"
        sibling = f"{directory}/sibling{suffix}"
        self.write_bytes(root, allowed, b"one\ntwo\r\n")      # excused: mixed line endings only
        self.write_bytes(root, allowed, b"one \ntwo\r\n")     # whitelisted path keeps its whitespace finding
        self.assertEqual(self.problems(root), [f"{allowed}:1: trailing whitespace"])
        (root / allowed).unlink()
        self.write_bytes(root, sibling, b"one\ntwo\r\n")      # different file: still reported
        self.write_bytes(root, trailing, b"one\ntwo")         # different file: still reported
        self.assertEqual(self.problems(root), [
            f"{sibling}:1: mixed CRLF and LF line endings",
            f"{trailing}:2: missing final newline",
        ])

    def test_crlf_exemptions_point_at_real_owned_files(self) -> None:
        owned = {path.relative_to(ROOT).as_posix() for path in check_sources.owned_files()}
        for name, reason in check_sources.CRLF_EXEMPT.items():
            self.assertIn(name, owned, f"CRLF exemption for a file outside the scan: {name}")
            self.assertNotEqual(reason.strip(), "", f"CRLF exemption without a reason: {name}")

    def test_new_checks_stay_read_only(self) -> None:
        root = self.temp_root()
        self.write_bytes(root, "docs/dirty.md", b"a \r\nb")
        before = (root / "docs/dirty.md").read_bytes()
        self.problems(root)
        self.problems(root)
        self.assertEqual((root / "docs/dirty.md").read_bytes(), before)
        self.assertEqual(self.problems(root), [
            "docs/dirty.md:1: trailing whitespace",
            "docs/dirty.md:2: missing final newline",
        ])


if __name__ == "__main__":
    unittest.main()
