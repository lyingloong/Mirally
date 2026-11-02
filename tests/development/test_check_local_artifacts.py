"""Tests for tools/check_local_artifacts.py (pure standard library, read-only).

Every case builds a throwaway git repository under the temporary directory, so
the real checkout's untracked files never influence the result and the tool is
verified against exactly the states the spec calls out: clean root, tmp file,
negative control, and "the check writes nothing".
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "check_local_artifacts", ROOT / "tools" / "check_local_artifacts.py")
check_local_artifacts = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(check_local_artifacts)


class LocalArtifactsTests(unittest.TestCase):
    def git(self, root: Path, *arguments: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", "-C", str(root), *arguments],
                              capture_output=True, text=True, check=True)

    def repo(self) -> Path:
        """Git repository whose .gitignore matches the real one for tmp/output."""
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        self.git(root, "init", "-q")
        (root / ".gitignore").write_text("/tmp/\n/output/\n", encoding="utf-8")
        (root / "README.md").write_text("# fixture\n", encoding="utf-8")
        self.git(root, "add", ".")
        self.git(root, "-c", "user.name=fixture", "-c", "user.email=fixture@example.invalid",
                 "commit", "-qm", "fixture")
        return root

    def status(self, root: Path) -> str:
        return self.git(root, "status", "--porcelain").stdout

    def test_empty_root_is_clean_and_writes_nothing(self) -> None:
        root = self.repo()
        before = self.status(root)
        report = check_local_artifacts.audit(root)
        self.assertTrue(report["ok"])
        self.assertEqual(report["candidates"], [])
        self.assertEqual(report["unignored"], [])
        self.assertEqual(report["summary"]["candidates"], 0)
        # A-V2: the check is read-only.
        self.assertEqual(self.status(root), before)
        self.data_unchanged(root, before)

    def data_unchanged(self, root: Path, before: str) -> None:
        """Run the CLI too: stdout/stderr are not files, so the tree stays identical."""
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = check_local_artifacts.main(["--root", str(root)])
        self.assertEqual(code, 0, out.getvalue() + err.getvalue())
        self.assertEqual(self.status(root), before)

    def test_root_tmp_file_is_reported_as_a_finding(self) -> None:
        root = self.repo()
        (root / "tmpXYZ").write_text("x\n", encoding="utf-8")
        report = check_local_artifacts.audit(root)
        self.assertFalse(report["ok"])
        self.assertEqual(report["unignored"], ["tmpXYZ"])
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(check_local_artifacts.main(["--root", str(root)]), 1)
        self.assertIn("tmpXYZ", out.getvalue())
        self.assertIn("FAILED", out.getvalue())

    def test_root_tmp_directory_is_reported_too(self) -> None:
        root = self.repo()
        (root / "tmp-run").mkdir()
        (root / "tmp-run" / "log.txt").write_text("x\n", encoding="utf-8")
        report = check_local_artifacts.audit(root)
        self.assertEqual(report["unignored"], ["tmp-run"])

    def test_negative_control_tracked_tmp_entry_is_still_reported(self) -> None:
        """Tracking a file does not make the root clean; only ignoring it does."""
        root = self.repo()
        (root / "tmp-kept").write_text("x\n", encoding="utf-8")
        self.git(root, "add", "tmp-kept")
        self.git(root, "-c", "user.name=fixture", "-c", "user.email=fixture@example.invalid",
                 "commit", "-qm", "tracked tmp file")
        report = check_local_artifacts.audit(root)
        self.assertFalse(report["ok"])
        self.assertEqual(report["tracked"], ["tmp-kept"])
        self.assertEqual(report["unignored"], ["tmp-kept"])

    def test_ignored_tmp_entries_are_not_findings(self) -> None:
        """/tmp/ and output/tmp/ are ignored: they never count, and the checker does not edit .gitignore."""
        root = self.repo()
        (root / "tmp").mkdir()
        (root / "tmp" / "scratch.txt").write_text("x\n", encoding="utf-8")
        (root / "output" / "tmp" / "tool" / "run").mkdir(parents=True)
        (root / "output" / "tmp" / "tool" / "run" / "log.txt").write_text("x\n", encoding="utf-8")
        gitignore_before = (root / ".gitignore").read_text(encoding="utf-8")
        report = check_local_artifacts.audit(root)
        self.assertTrue(report["ok"], report)
        self.assertEqual(report["ignored"], ["tmp"])
        self.assertEqual(report["unignored"], [])
        self.assertEqual((root / ".gitignore").read_text(encoding="utf-8"), gitignore_before)

    def test_mixed_case_sorts_and_partitions_entries(self) -> None:
        root = self.repo()
        (root / "tmp").mkdir()           # ignored
        (root / "tmp-a").write_text("x\n", encoding="utf-8")
        (root / "tmpB").write_text("x\n", encoding="utf-8")
        report = check_local_artifacts.audit(root)
        self.assertEqual(report["candidates"], ["tmp", "tmp-a", "tmpB"])
        self.assertEqual(report["ignored"], ["tmp"])
        self.assertEqual(report["unignored"], ["tmp-a", "tmpB"])
        self.assertFalse(report["ok"])

    def test_cli_json_and_non_repository_error(self) -> None:
        root = self.repo()
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(check_local_artifacts.main(["--root", str(root), "--json"]), 0)
        report = json.loads(out.getvalue())
        self.assertTrue(report["ok"])
        self.assertEqual(report["pattern"], "tmp*")
        self.assertEqual(report["scratch_root"], "output/tmp")

        with tempfile.TemporaryDirectory() as directory:
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                code = check_local_artifacts.main(["--root", directory])
            self.assertEqual(code, 2)
            self.assertIn("not a git work tree root", err.getvalue())

    def test_repository_without_commits_still_works(self) -> None:
        """A fresh clone's first run has no HEAD yet; the check must not error."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.git(root, "init", "-q")
            (root / "tmpProbe").write_text("x\n", encoding="utf-8")
            report = check_local_artifacts.audit(root)
            self.assertFalse(report["ok"])
            self.assertEqual(report["unignored"], ["tmpProbe"])

    def test_subdirectory_of_a_repository_is_rejected(self) -> None:
        """Being *inside* a work tree is not enough: only its root is checked."""
        root = self.repo()
        nested = root / "docs"
        nested.mkdir()
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = check_local_artifacts.main(["--root", str(nested)])
        self.assertEqual(code, 2)
        self.assertIn("not a git work tree root", err.getvalue())

    def test_scan_does_not_follow_into_subdirectories(self) -> None:
        """Only repository-root entries matter; nested tmp names elsewhere are out of scope."""
        root = self.repo()
        (root / "docs").mkdir()
        (root / "docs" / "tmp-note.md").write_text("x\n", encoding="utf-8")
        report = check_local_artifacts.audit(root)
        self.assertTrue(report["ok"], report)

    def test_tool_uses_relative_root_and_no_machine_paths(self) -> None:
        """A-V5: no drive letters or user names baked into the tool."""
        source = (ROOT / "tools" / "check_local_artifacts.py").read_text(encoding="utf-8")
        self.assertIn("Path(__file__).resolve().parents[1]", source)
        self.assertNotIn(os.path.expanduser("~"), source)
        self.assertNotIn("C:\\\\Users", source)


if __name__ == "__main__":
    unittest.main()
