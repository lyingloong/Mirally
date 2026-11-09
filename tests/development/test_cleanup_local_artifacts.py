"""Offline tests for tools/check_cleanup_local_artifacts.py (dev-0029, seed-coverage spec A).

Every fixture is a throwaway ``TemporaryDirectory`` shaped like a repository
(``<root>/output/tmp/...``). Nothing here touches the real ``output/tmp``.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import sys
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools import check_cleanup_local_artifacts as cleanup  # noqa: E402

DAY = 86_400
NOW = 1_800_000_000.0


def _age(path: Path, days: float) -> None:
    stamp = NOW - days * DAY
    os.utime(path, (stamp, stamp))


def _age_second(path: Path, seconds: float) -> None:
    """Stamp a whole tree ``seconds`` before the wall clock the re-check reads."""
    stamp = time.time() - seconds
    for item in [path, *sorted(path.rglob("*"))]:
        if not item.is_symlink():
            os.utime(item, (stamp, stamp))


def _age_tree(path: Path, days: float) -> None:
    """Stamp a whole fixture tree so mtime newest==fixture age is deterministic."""
    stamp = NOW - days * DAY
    for item in [path, *sorted(path.rglob("*"))]:
        if item.is_symlink():
            continue
        os.utime(item, (stamp, stamp))


def _make_link(link: Path, target: Path) -> bool:
    """Create a real Windows junction (or a symlink fallback) for boundary tests."""
    if sys.platform == "win32":
        if os.system(f'mklink /J "{link}" "{target}" >nul 2>&1') == 0:
            return True
    try:
        link.symlink_to(target, target_is_directory=True)
        return True
    except (OSError, NotImplementedError):
        return False


class CleanupLocalArtifactsTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.repo = Path(temp.name)
        self.tmp = self.repo / "output" / "tmp"
        self.tmp.mkdir(parents=True)
        self._git_init(self.repo)

    # -- helpers ---------------------------------------------------------
    def _git_init(self, repo: Path) -> None:
        if shutil.which("git") is None:
            self.skipTest("git is required for the trusted-root checks")
        completed = subprocess.run(["git", "-C", str(repo), "init", "-q"],
                                   capture_output=True, text=True, timeout=60)
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def scratch(self, name: str, *, age_days: float, files: int = 2) -> Path:
        directory = self.tmp / name
        directory.mkdir()
        for index in range(files):
            (directory / f"f{index}.log").write_text("x", encoding="utf-8")
        (directory / "nested").mkdir()
        (directory / "nested" / "deep.txt").write_text("y", encoding="utf-8")
        _age_tree(directory, age_days)
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
        if not _make_link(junction, outside):
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

    # -- 4. trusted Git root, ancestor links and run-level age ------------
    def test_non_git_worktree_is_refused_and_target_untouched(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            tmp = repo / "output" / "tmp"
            tmp.mkdir(parents=True)
            victim = tmp / "run-old"
            victim.mkdir()
            (victim / "keep.txt").write_text("must survive", encoding="utf-8")
            _age_tree(victim, 30)

            code, report = cleanup.run(repo_root=repo, now=NOW, apply=True, days=7)
            self.assertEqual(code, 1)
            self.assertIn("Git", report)
            self.assertTrue((victim / "keep.txt").is_file())

            code = cleanup.main(["--root", str(tmp), "--apply", "--days", "7"],
                                now=NOW, repo_root=repo)
            self.assertEqual(code, 1)
            self.assertTrue((victim / "keep.txt").is_file())

    def test_trusted_git_root_with_unicode_and_spaces(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory) / "中文 repo with spaces"
            repo.mkdir()
            self._git_init(repo)
            tmp = repo / "output" / "tmp"
            tmp.mkdir(parents=True)
            stale = tmp / "run-old"
            stale.mkdir()
            (stale / "f.log").write_text("x", encoding="utf-8")
            _age_tree(stale, 30)
            code, _ = cleanup.run(repo_root=repo, now=NOW, apply=True, days=7)
            self.assertEqual(code, 0)
            self.assertFalse(stale.exists())

    def test_short_name_repo_and_long_root_paths_are_normalized(self):
        """Runner temp paths may mix 8.3 short and long forms in one compare."""
        if os.name != "nt":
            self.skipTest("Windows 8.3 short names only")
        import ctypes

        buffer = ctypes.create_unicode_buffer(32_768)
        length = ctypes.windll.kernel32.GetShortPathNameW(str(self.repo), buffer, len(buffer))
        if not length or not buffer.value or Path(buffer.value) == self.repo:
            self.skipTest("8.3 short names are unavailable on this volume")
        short_tmp = Path(buffer.value) / "output" / "tmp"
        stale = self.scratch("run-old", age_days=30)
        code, report = cleanup.run(repo_root=self.repo, root=short_tmp, now=NOW,
                                   apply=True, days=7)
        self.assertEqual(code, 0, report)
        self.assertFalse(stale.exists())

    def test_junction_tmp_root_is_refused_and_target_survives(self):
        outside = self.repo / "outside-tmp-root"
        victim = outside / "victim"
        victim.mkdir(parents=True)
        (victim / "keep.txt").write_text("must survive", encoding="utf-8")
        _age_tree(outside, 90)
        self.tmp.rmdir()
        if not _make_link(self.tmp, outside):
            self.skipTest("links unavailable on this platform")
        code, report = self.run_cleanup(apply=True, days=0)
        self.assertEqual(code, 1)
        self.assertIn("拒绝", report)
        self.assertTrue((victim / "keep.txt").is_file())
        self.assertTrue(cleanup._is_link(self.tmp))
        self.assertTrue(self.tmp.is_dir(), "链接本身不应被删除")

    def test_junction_output_ancestor_is_refused_and_target_survives(self):
        outside = self.repo / "outside-output"
        victim = outside / "tmp" / "victim"
        victim.mkdir(parents=True)
        (victim / "keep.txt").write_text("must survive", encoding="utf-8")
        _age_tree(outside, 90)
        self.tmp.rmdir()
        (self.repo / "output").rmdir()
        if not _make_link(self.repo / "output", outside):
            self.skipTest("links unavailable on this platform")
        code, report = self.run_cleanup(apply=True, days=0)
        self.assertEqual(code, 1)
        self.assertIn("拒绝", report)
        self.assertTrue((victim / "keep.txt").is_file())

    def test_old_tool_directory_with_a_fresh_run_is_kept(self):
        tool = self.tmp / "old-tool"
        run = tool / "run-new"
        run.mkdir(parents=True)
        (run / "work.log").write_text("active", encoding="utf-8")
        _age_tree(tool, 30)
        _age_tree(run, 0.1)
        code, report = self.run_cleanup(apply=True, days=7)
        self.assertEqual(code, 0)
        self.assertTrue((run / "work.log").is_file())
        self.assertIn("old-tool", report)
        self.assertIn("保留", report)

    def test_apply_still_removes_tool_directory_when_every_run_is_old(self):
        tool = self.tmp / "old-tool"
        run = tool / "run-old"
        run.mkdir(parents=True)
        (run / "work.log").write_text("stale", encoding="utf-8")
        _age_tree(tool, 30)
        _age_tree(run, 20)
        code, _ = self.run_cleanup(apply=True, days=7)
        self.assertEqual(code, 0)
        self.assertFalse(tool.exists())

    # -- 5. reporting and CLI --------------------------------------------
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
                    [sys.executable, str(ROOT / "tools" / "check_cleanup_local_artifacts.py"),
                     "--root", str(self.tmp), "--days", "7"],
                    cwd=str(self.repo), env=env, capture_output=True, timeout=120,
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
        code = cleanup.main(["--root", str(self.tmp), "--days", "7"], now=NOW,
                            repo_root=self.repo)
        self.assertEqual(code, 0)
        self.assertTrue(stale.is_dir())

    def test_cli_apply_deletes(self):
        stale = self.scratch("run-old", age_days=30)
        code = cleanup.main(["--root", str(self.tmp), "--apply", "--days", "7"], now=NOW,
                            repo_root=self.repo)
        self.assertEqual(code, 0)
        self.assertFalse(stale.exists())

    def test_main_accepts_explicit_repo_root_without_cwd_dependence(self):
        stale = self.scratch("run-old", age_days=30)
        code = cleanup.main(["--days", "7"], now=NOW, repo_root=self.repo)
        self.assertEqual(code, 0)
        self.assertTrue(stale.is_dir())

    def test_cli_rejects_non_tmp_root(self):
        elsewhere = self.repo / "output" / "not-tmp"
        elsewhere.mkdir()
        code = cleanup.main(["--root", str(elsewhere)], repo_root=self.repo)
        self.assertEqual(code, 1)

    def test_cli_rejects_negative_days(self):
        self.assertEqual(cleanup.main(["--root", str(self.tmp), "--days", "-1"],
                                      repo_root=self.repo), 2)

    def test_cli_rejects_non_finite_days(self):
        """``--days nan``/``inf`` must fail instead of silently disabling the floor.

        ``nan < 0`` is False and ``age < nan`` is always False, so the old check
        let ``--days nan`` mark every directory stale (and ``inf`` keep them all
        forever). Neither is a threshold; both are bad arguments (exit 2).
        """
        fresh = self.scratch("run-fresh", age_days=0.5)
        for value in ("nan", "NaN", "inf", "Infinity"):
            with self.subTest(days=value):
                code = cleanup.main(["--root", str(self.tmp), "--apply", "--days", value],
                                    now=NOW, repo_root=self.repo)
                self.assertEqual(code, 2)
                # Nothing may be planned or deleted while the threshold is invalid.
                self.assertTrue(fresh.is_dir())
        # A separated "-inf" is rejected by argparse itself (negatives look like
        # options); the "=" form reaches main() and the guard returns 2 there.
        with self.assertRaises(SystemExit) as raised:
            cleanup.main(["--root", str(self.tmp), "--apply", "--days", "-inf"],
                         now=NOW, repo_root=self.repo)
        self.assertEqual(raised.exception.code, 2)
        self.assertTrue(fresh.is_dir())
        for argv in (["--days=-inf"], ["--days=-nan"]):
            with self.subTest(argv=argv):
                code = cleanup.main(["--root", str(self.tmp), "--apply", *argv],
                                    now=NOW, repo_root=self.repo)
                self.assertEqual(code, 2)
                self.assertTrue(fresh.is_dir())
        self.assertTrue(fresh.is_dir())

    def test_run_rejects_non_finite_days_directly(self):
        """The guard belongs to ``run()`` too: its ``days`` is part of the contract."""
        fresh = self.scratch("run-fresh", age_days=0.5)
        for days in (float("nan"), float("inf"), float("-inf"), -0.5):
            with self.subTest(days=days):
                code, report = cleanup.run(repo_root=self.repo, now=NOW, apply=True,
                                           days=days)
                self.assertEqual(code, 2, report)
                self.assertIn("--days", report)
                self.assertTrue(fresh.is_dir())

    def test_days_normal_values_are_still_accepted(self):
        """The guard must not reject valid thresholds, including 0 and fractions."""
        fresh = self.scratch("run-fresh", age_days=0.5)
        for days in (0, 0.25, 7, 365.0):
            with self.subTest(days=days):
                self.assertTrue(cleanup.valid_days(days))
                code, _ = self.run_cleanup(days=days, apply=False)
                self.assertEqual(code, 0)
        self.assertFalse(cleanup.valid_days(float("nan")))
        self.assertFalse(cleanup.valid_days(float("inf")))
        self.assertFalse(cleanup.valid_days(-1))
        self.assertTrue(fresh.is_dir())

    def test_apply_keeps_run_created_after_the_audit(self):
        """audit() eligible → new run/tool/active.log → remove() keeps it, exit 0.

        The audit only sees the tree it scanned. A run written after it - the
        exact race Codex reproduced - must survive ``--apply``. Fixtures are
        stamped relative to the wall clock and the injected re-check clock
        matches it, because ``now=`` pins only the audit clock.
        """
        tool = self.scratch("old-tool", age_days=30, files=1)
        clock = time.time()
        _age_second(tool, seconds=30 * DAY)
        real_audit = cleanup.audit

        def audit_then_new_run(root, **kwargs):
            entries = real_audit(root, **kwargs)
            self.assertTrue(any(e.name == "old-tool" and e.eligible for e in entries))
            run = tool / "run-new"
            run.mkdir()
            (run / "active.log").write_text("still running", encoding="utf-8")
            return entries

        with mock.patch.object(cleanup, "audit", side_effect=audit_then_new_run):
            code, report = cleanup.run(repo_root=self.repo, now=clock, apply=True, days=7,
                                       recheck_now=lambda: clock)
        self.assertEqual(code, 0, report)
        self.assertTrue((tool / "run-new" / "active.log").is_file())
        self.assertTrue((tool / "f0.log").is_file())
        self.assertIn("删除前复核", report)
        self.assertIn("实际已删除 0 个", report)

    def test_apply_keeps_run_written_after_the_audit(self):
        """A stale run that receives a fresh file after audit() is not deleted."""
        run = self.scratch("run-old", age_days=30, files=1)
        clock = time.time()
        _age_second(run, seconds=30 * DAY)
        real_audit = cleanup.audit

        def audit_then_touch(root, **kwargs):
            entries = real_audit(root, **kwargs)
            self.assertTrue(any(e.name == "run-old" and e.eligible for e in entries))
            fresh = time.time()
            os.utime(run / "f0.log", (fresh, fresh))
            return entries

        with mock.patch.object(cleanup, "audit", side_effect=audit_then_touch):
            code, report = cleanup.run(repo_root=self.repo, now=clock, apply=True, days=7,
                                       recheck_now=lambda: time.time())
        self.assertEqual(code, 0, report)
        self.assertTrue((run / "f0.log").is_file())
        self.assertIn("删除前复核", report)

    def test_apply_still_removes_a_tree_old_against_the_wall_clock(self):
        """Protection must not become "never delete": really old trees still go."""
        tool = self.tmp / "old-tool"
        run = tool / "run-old"
        run.mkdir(parents=True)
        (run / "work.log").write_text("stale", encoding="utf-8")
        clock = time.time()
        _age_second(tool, seconds=30 * DAY)
        code, report = cleanup.run(repo_root=self.repo, now=clock, apply=True, days=7,
                                   recheck_now=lambda: time.time())
        self.assertEqual(code, 0, report)
        self.assertFalse(tool.exists())
        self.assertNotIn("删除前复核", report)

    def test_remove_rechecks_age_and_refuses_newly_active_run(self):
        """The guard lives in remove() too, for callers that delete a stale Entry."""
        run = self.scratch("run-old", age_days=30, files=1)
        clock = time.time()
        _age_second(run, seconds=30 * DAY)
        [entry] = cleanup.audit(self.tmp, days=7, now=clock, repo_root=self.repo)
        self.assertTrue(entry.eligible)
        fresh = time.time()
        os.utime(run / "f0.log", (fresh, fresh))
        with self.assertRaises(ValueError):
            cleanup.remove(entry, root=self.tmp, repo_root=self.repo, days=7, now=time.time())
        self.assertTrue((run / "f0.log").is_file())

    def test_apply_treats_a_vanished_candidate_as_nothing_to_delete(self):
        """A candidate removed after the audit is skipped, not reported as failure."""
        tool = self.scratch("run-vanish", age_days=30, files=1)
        clock = time.time()
        _age_second(tool, seconds=30 * DAY)
        real_audit = cleanup.audit

        def audit_then_remove(root, **kwargs):
            entries = real_audit(root, **kwargs)
            shutil.rmtree(tool)
            return entries

        with mock.patch.object(cleanup, "audit", side_effect=audit_then_remove):
            code, report = cleanup.run(repo_root=self.repo, now=clock, apply=True, days=7,
                                       recheck_now=lambda: time.time())
        self.assertEqual(code, 0, report)
        self.assertFalse(tool.exists())

    def test_tool_is_read_only_until_apply(self):
        """The default path must not modify mtimes anywhere in the tree."""
        self.scratch("run-old", age_days=30)
        before = {p: p.stat().st_mtime for p in sorted(self.tmp.rglob("*"))}
        self.run_cleanup(apply=False)
        after = {p: p.stat().st_mtime for p in sorted(self.tmp.rglob("*"))}
        self.assertEqual(before, after)


@contextlib.contextmanager
def race_after_listing(path: Path, *, occurrence: int, write):
    """Run ``write()`` right after the ``occurrence``-th listing of ``path``.

    Only ``Path.iterdir`` is wrapped: the write uses real files and real mtimes,
    and the deletion implementation is never replaced, so the production timing
    window is exercised instead of a mock.
    """
    real_iterdir = Path.iterdir
    state = {"seen": 0, "fired": False}

    def racing_iterdir(item):
        children = list(real_iterdir(item))
        if item == path:
            state["seen"] += 1
            if state["seen"] == occurrence and not state["fired"]:
                state["fired"] = True
                write()
        return iter(children)

    with mock.patch.object(Path, "iterdir", racing_iterdir):
        yield state


class FinalScanRaceTests(unittest.TestCase):
    """A run/file created during the final scan or the delete must survive.

    Codex P1 on b073dc63: ``_newest_mtime(tool)`` read the parent mtime once and
    listed its children once, so a ``run-new`` created while ``run-old`` was
    walked stayed invisible and the following recursive delete removed it.
    These tests run the real ``run(apply=True)`` and only schedule the write by
    wrapping ``Path.iterdir`` - no mtime is faked, no delete helper replaced.

    Listing occurrences per candidate: three pre-delete scans (audit, ``run()``
    re-check, ``remove()`` re-check) and the fourth listing happens inside the
    guarded delete.
    """

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.repo = Path(temp.name)
        self.tmp = self.repo / "output" / "tmp"
        self.tmp.mkdir(parents=True)
        if shutil.which("git") is None:
            self.skipTest("git is required for the trusted-root checks")
        completed = subprocess.run(["git", "-C", str(self.repo), "init", "-q"],
                                   capture_output=True, text=True, timeout=60)
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def aged_tool(self) -> tuple[Path, Path]:
        """``output/tmp/old-tool/run-old/old.log``, 30 days old, plus a control."""
        tool = self.tmp / "old-tool"
        run = tool / "run-old"
        run.mkdir(parents=True)
        (run / "old.log").write_text("stale", encoding="utf-8")
        stamp = time.time() - 30 * DAY
        for path in (run / "old.log", run, tool):
            os.utime(path, (stamp, stamp))
        control = self.tmp / "control"
        control.mkdir()
        (control / "fresh.log").write_text("fresh", encoding="utf-8")
        return tool, run

    def run_apply(self):
        # No pinned clocks: the audit and both pre-delete re-checks compare the
        # real wall clock against the 30-day-old fixture.
        return cleanup.run(repo_root=self.repo, apply=True, days=7)

    def test_final_scan_keeps_a_sibling_run_created_while_walking(self):
        """Codex's exact repro: run-new/active.log lands inside the final scan."""
        tool, run = self.aged_tool()

        def write():
            (tool / "run-new").mkdir()
            (tool / "run-new" / "active.log").write_text("still running", encoding="utf-8")

        with race_after_listing(run, occurrence=3, write=write) as state:
            code, report = self.run_apply()
        self.assertEqual(code, 0, report)
        self.assertTrue(state["fired"], "the final-scan window was never exercised")
        self.assertTrue((tool / "run-new" / "active.log").is_file(),
                        "a sibling run created during the final scan was deleted")
        self.assertTrue((run / "old.log").is_file(),
                        "the re-check must keep the whole tool dir, not half-delete it")
        self.assertIn("删除前复核", report)
        self.assertIn("实际已删除 0 个", report)
        self.assertTrue((self.tmp / "control" / "fresh.log").is_file())

    def test_final_scan_keeps_a_loose_file_created_while_walking(self):
        """A file landing directly in the tool dir mid-scan also stops the delete."""
        tool, run = self.aged_tool()

        def write():
            (tool / "active.log").write_text("live", encoding="utf-8")

        with race_after_listing(run, occurrence=3, write=write) as state:
            code, report = self.run_apply()
        self.assertEqual(code, 0, report)
        self.assertTrue(state["fired"], "the final-scan window was never exercised")
        self.assertTrue((tool / "active.log").is_file())
        self.assertTrue((run / "old.log").is_file())
        self.assertIn("删除前复核", report)

    def test_sibling_run_created_during_the_guarded_delete_is_kept(self):
        """After every re-check, a run appearing mid-delete must not be swept away.

        The tool dir was confirmed stale, but ``rmdir`` refuses while the new
        sibling exists, so the recursive delete can no longer take it.
        """
        tool, _ = self.aged_tool()

        def write():
            (tool / "run-new").mkdir()
            (tool / "run-new" / "active.log").write_text("still running", encoding="utf-8")

        with race_after_listing(tool, occurrence=4, write=write) as state:
            code, report = self.run_apply()
        self.assertEqual(code, 0, report)
        self.assertTrue(state["fired"], "the guarded-delete window was never exercised")
        self.assertTrue((tool / "run-new" / "active.log").is_file(),
                        "a sibling run created during the delete was deleted")
        self.assertTrue(tool.is_dir())
        self.assertIn("删除前复核", report)
        self.assertIn("实际已删除 0 个", report)

    def test_fresh_file_created_inside_an_old_run_during_delete_is_kept(self):
        """A new log inside the run being pruned also keeps its directory."""
        tool, run = self.aged_tool()

        def write():
            (run / "active.log").write_text("live", encoding="utf-8")

        with race_after_listing(run, occurrence=4, write=write) as state:
            code, report = self.run_apply()
        self.assertEqual(code, 0, report)
        self.assertTrue(state["fired"], "the guarded-delete window was never exercised")
        self.assertTrue((run / "active.log").is_file())
        self.assertTrue(tool.is_dir())
        self.assertIn("删除前复核", report)

    def test_newest_mtime_notices_a_sibling_created_during_the_walk(self):
        """The snapshot itself re-reads parent mtimes after the walk."""
        tool, run = self.aged_tool()
        before = tool.lstat().st_mtime

        def write():
            (tool / "run-new").mkdir()
            (tool / "run-new" / "active.log").write_text("live", encoding="utf-8")

        with race_after_listing(run, occurrence=1, write=write) as state:
            newest = cleanup._newest_mtime(tool)
        self.assertTrue(state["fired"])
        self.assertGreater(newest, before + 60,
                           "a sibling added during the walk must make the tree look fresh")

    def test_delete_still_removes_a_fully_stale_tree(self):
        """Control: safety must not turn the cleaner into a no-op."""
        tool, _ = self.aged_tool()
        code, report = self.run_apply()
        self.assertEqual(code, 0, report)
        self.assertFalse(tool.exists())
        self.assertIn("实际已删除 1 个", report)
        self.assertTrue((self.tmp / "control" / "fresh.log").is_file())


if __name__ == "__main__":
    unittest.main()
