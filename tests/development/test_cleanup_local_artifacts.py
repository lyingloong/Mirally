"""Offline tests for tools/cleanup_local_artifacts.py (dev-0029, seed-coverage spec A).

Every fixture is a throwaway ``TemporaryDirectory`` shaped like a repository
(``<root>/output/tmp/...``). Nothing here touches the real ``output/tmp``.
"""

from __future__ import annotations

import os
import sys
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools import cleanup_local_artifacts as cleanup  # noqa: E402

DAY = 86_400
NOW = 1_800_000_000.0


def _age(path: Path, days: float) -> None:
    stamp = NOW - days * DAY
    os.utime(path, (stamp, stamp))


class CleanupLocalArtifactsTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.repo = Path(temp.name)
        self.tmp = self.repo / "output" / "tmp"
        self.tmp.mkdir(parents=True)

    # -- helpers ---------------------------------------------------------
    def scratch(self, name: str, *, age_days: float, files: int = 2) -> Path:
        directory = self.tmp / name
        directory.mkdir()
        for index in range(files):
            (directory / f"f{index}.log").write_text("x", encoding="utf-8")
        (directory / "nested").mkdir()
        (directory / "nested" / "deep.txt").write_text("y", encoding="utf-8")
        _age(directory, age_days)
        return directory

    def run_cleanup(self, **kwargs):
        return cleanup.run(repo_root=self.repo, now=NOW, **kwargs)

    # -- 1. dry run is the default and changes nothing -------------------
    def test_dry_run_reports_candidates_but_deletes_nothing(self):
        stale = self.scratch("run-old", age_days=30)
        fresh = self.scratch("run-new", age_days=1)
        code, report = self.run_cleanup(apply=False)
        self.assertEqual(code, 0)
        self.assertIn("run-old", report)
        self.assertIn("dry-run", report)
        self.assertNotIn("run-new", report.split("保留")[0])
        self.assertTrue(stale.is_dir())
        self.assertTrue(fresh.is_dir())
        self.assertTrue((stale / "nested" / "deep.txt").is_file())

    def test_apply_removes_only_stale_directories(self):
        stale = self.scratch("run-old", age_days=10)
        fresh = self.scratch("run-new", age_days=2)
        code, report = self.run_cleanup(apply=True, days=7)
        self.assertEqual(code, 0)
        self.assertFalse(stale.exists())
        self.assertTrue(fresh.is_dir())
        self.assertIn("实际已删除 1 个", report)
        self.assertEqual([p.name for p in self.tmp.iterdir()], ["run-new"])

    def test_days_threshold_is_honoured(self):
        borderline = self.scratch("run-borderline", age_days=3)
        code, _ = self.run_cleanup(apply=True, days=7)
        self.assertEqual(code, 0)
        self.assertTrue(borderline.is_dir(), "3 天目录不应被 7 天阈值删除")
        code, _ = self.run_cleanup(apply=True, days=2)
        self.assertEqual(code, 0)
        self.assertFalse(borderline.exists())

    # -- 2. root files and other directories are untouched ---------------
    def test_loose_files_at_tmp_root_are_reported_never_deleted(self):
        loose = self.tmp / "stray.txt"
        loose.write_text("keep me", encoding="utf-8")
        _age(loose, 60)
        code, report = self.run_cleanup(apply=True)
        self.assertEqual(code, 0)
        self.assertTrue(loose.is_file())
        self.assertIn("只报告", report)

    def test_nothing_outside_output_tmp_is_touched(self):
        outside = self.repo / "output" / "keep-me"
        outside.mkdir()
        (outside / "data.txt").write_text("important", encoding="utf-8")
        _age(outside, 90)
        sibling = self.repo / "tmp"
        sibling.mkdir()
        (sibling / "x.txt").write_text("not ours", encoding="utf-8")
        _age(sibling, 90)
        repo_file = self.repo / "README.md"
        repo_file.write_text("# repo", encoding="utf-8")
        self.scratch("run-old", age_days=30)
        code, _ = self.run_cleanup(apply=True)
        self.assertEqual(code, 0)
        self.assertTrue(outside.is_dir())
        self.assertTrue((outside / "data.txt").is_file())
        self.assertTrue((sibling / "x.txt").is_file())
        self.assertTrue(repo_file.is_file())
        self.assertFalse((self.tmp / "run-old").exists())

    def test_empty_or_missing_tmp_root_is_not_an_error(self):
        code, report = self.run_cleanup(apply=True)
        self.assertEqual(code, 0)
        self.assertIn("没有任何条目", report)
        missing = self.repo / "output" / "tmp"
        missing.rmdir()
        code, report = self.run_cleanup(apply=True)
        self.assertEqual(code, 0)
        self.assertIn("没有任何条目", report)

    # -- 3. symlinks and out-of-bounds paths are refused -----------------
    def test_symlinked_directories_are_reported_and_not_deleted(self):
        outside = self.repo / "outside-secret"
        outside.mkdir()
        (outside / "secret.txt").write_text("must survive", encoding="utf-8")
        link = self.tmp / "link-to-outside"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable on this platform")
        code, report = self.run_cleanup(apply=True, days=0)
        self.assertEqual(code, 0)
        self.assertIn("符号链接", report)
        self.assertTrue(outside.is_dir())
        self.assertTrue((outside / "secret.txt").is_file())
        self.assertTrue(link.is_symlink())

    def test_windows_junction_is_detected_as_a_link(self):
        """A directory junction is a reparse point but Path.is_symlink() is False.

        Without the reparse-point check the junction would be treated as an
        ordinary directory and deleted; this test pins that behaviour.
        """
        outside = self.repo / "outside-secret"
        outside.mkdir()
        (outside / "secret.txt").write_text("must survive", encoding="utf-8")
        junction = self.tmp / "junction-out"
        created = False
        if sys.platform == "win32":
            created = os.system(f'mklink /J "{junction}" "{outside}" >nul 2>&1') == 0
        if not created:
            try:
                junction.symlink_to(outside, target_is_directory=True)
                created = True
            except (OSError, NotImplementedError):
                self.skipTest("links unavailable on this platform")
        code, report = self.run_cleanup(apply=True, days=0)
        self.assertEqual(code, 0)
        self.assertIn("不跟随、不删除", report)
        self.assertTrue(outside.is_dir())
        self.assertTrue((outside / "secret.txt").is_file())
        self.assertTrue(junction.exists(), "链接本身不应被删除")

    def test_root_outside_a_repository_is_refused(self):
        other = Path(tempfile.gettempdir()).resolve()
        code, report = cleanup.run(repo_root=self.repo, root=other, now=NOW, apply=True)
        self.assertEqual(code, 1)
        self.assertIn("拒绝", report)

    def test_custom_root_must_still_be_an_output_tmp_directory(self):
        not_tmp = self.repo / "output" / "elsewhere"
        not_tmp.mkdir()
        (not_tmp / "victim").mkdir()
        _age(not_tmp / "victim", 90)
        code, report = cleanup.run(repo_root=self.repo, root=not_tmp, now=NOW, apply=True)
        self.assertEqual(code, 1)
        self.assertIn("拒绝", report)
        self.assertTrue((not_tmp / "victim").is_dir())

    def test_remove_rejects_ineligible_entry(self):
        fresh = self.scratch("run-new", age_days=1)
        entries = cleanup.audit(self.tmp, days=7, now=NOW, repo_root=self.repo)
        entry = next(item for item in entries if item.name == "run-new")
        self.assertFalse(entry.eligible)
        with self.assertRaises(ValueError):
            cleanup.remove(entry, root=self.tmp, repo_root=self.repo)
        self.assertTrue(fresh.is_dir())

    # -- 4. reporting and CLI --------------------------------------------
    def test_report_lists_each_candidate_with_age(self):
        self.scratch("run-a", age_days=20)
        self.scratch("run-b", age_days=40)
        code, report = self.run_cleanup(apply=False, days=7)
        self.assertEqual(code, 0)
        self.assertIn("run-a/", report)
        self.assertIn("run-b/", report)
        self.assertIn("2 个目录", report)

    def test_cli_survives_gbk_and_ascii_consoles(self):
        """A non-UTF-8 console must not turn a successful run into exit 1.

        Every message this tool prints is Chinese, so without the entry-point
        reconfigure the final print raised UnicodeEncodeError after the work was
        already done. Mirrors test_workflow_ops' GBK/ascii console precedent.
        """
        self.scratch("run-old", age_days=30)
        for encoding in ("gbk:strict", "ascii:strict"):
            with self.subTest(encoding=encoding):
                env = {**os.environ, "PYTHONIOENCODING": encoding,
                       "PYTHONPATH": str(ROOT)}
                result = subprocess.run(
                    [sys.executable, str(ROOT / "tools" / "cleanup_local_artifacts.py"),
                     "--root", str(self.tmp), "--days", "7"],
                    cwd=str(ROOT), env=env, capture_output=True, timeout=120,
                )
                stderr = result.stderr.decode("utf-8", "replace")
                self.assertEqual(result.returncode, 0, stderr)
                self.assertNotIn("Traceback", stderr)
                self.assertNotIn("UnicodeEncodeError", stderr)
                self.assertIn(b"run-old", result.stdout)
                # dry run by default: the directory must still be there
                self.assertTrue((self.tmp / "run-old").is_dir())

    def test_cli_dry_run_is_default_and_keeps_directories(self):
        stale = self.scratch("run-old", age_days=30)
        code = cleanup.main(["--root", str(self.tmp), "--days", "7"], now=NOW)
        self.assertEqual(code, 0)
        self.assertTrue(stale.is_dir())

    def test_cli_apply_deletes(self):
        stale = self.scratch("run-old", age_days=30)
        code = cleanup.main(["--root", str(self.tmp), "--apply", "--days", "7"], now=NOW)
        self.assertEqual(code, 0)
        self.assertFalse(stale.exists())

    def test_cli_rejects_non_tmp_root(self):
        elsewhere = self.repo / "output" / "not-tmp"
        elsewhere.mkdir()
        code = cleanup.main(["--root", str(elsewhere)])
        self.assertEqual(code, 1)

    def test_cli_rejects_negative_days(self):
        self.assertEqual(cleanup.main(["--root", str(self.tmp), "--days", "-1"]), 2)

    def test_tool_is_read_only_until_apply(self):
        """The default path must not modify mtimes anywhere in the tree."""
        self.scratch("run-old", age_days=30)
        before = {p: p.stat().st_mtime for p in sorted(self.tmp.rglob("*"))}
        self.run_cleanup(apply=False)
        after = {p: p.stat().st_mtime for p in sorted(self.tmp.rglob("*"))}
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
