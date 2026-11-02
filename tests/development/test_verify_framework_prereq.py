"""Declaration-level skip for tools/verify_framework.py when prerequisites are missing.

Offline and standard-library only: every case runs either the real script as a
subprocess or its ``main()`` against a temporary root, so no kernel build, Node
download, Host process or port is required.
"""

from __future__ import annotations

import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "verify_framework.py"
KERNEL_ENTRY = Path("kernel/dsh/packages/mirally/mirally-product/start.mjs")
KERNEL_BUILD = Path("kernel/dsh/packages/core/agent/lib/index.js")
FRONTEND_BUILD = Path("frontend/control-ui/dist/.mirally-build.json")
PYTHON = Path(".venv") / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")

# Stands in for a trivial Node: a copy of the running interpreter that prints the
# configured version when called with --version.
FAKE_NODE = """\
import os, sys
print(os.environ.get("FAKE_NODE_VERSION", "v22.19.0") if sys.argv[1:] == ["--version"] else "")
"""


def fake_node(directory: Path, version: str) -> tuple[Path, dict[str, str]]:
    """A runnable Node stand-in plus the environment the script needs to see it."""
    directory.mkdir(parents=True, exist_ok=True)
    script = directory / "node_version.py"
    script.write_text(FAKE_NODE, encoding="utf-8")
    path = directory / ("node.exe" if sys.platform == "win32" else "node")
    if sys.platform == "win32":
        path.write_text('@echo off\r\n"%s" "%s" %%*\r\n' % (sys.executable, script), encoding="ascii")
    else:
        path.write_text('#!/bin/sh\nexec "%s" "%s" "$@"\n' % (sys.executable, script), encoding="ascii")
        path.chmod(0o755)
    return path, {"FAKE_NODE_VERSION": version}


def build_prerequisites(root: Path) -> None:
    for relative in (KERNEL_ENTRY, KERNEL_BUILD, FRONTEND_BUILD, PYTHON):
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
    (root / KERNEL_ENTRY).write_text("// fixture kernel entry\n", encoding="utf-8")
    (root / KERNEL_BUILD).write_text("// fixture kernel build output\n", encoding="utf-8")
    (root / FRONTEND_BUILD).write_text("{}\n", encoding="utf-8")
    shutil.copy2(sys.executable, root / PYTHON)


class TempRootTestCase(unittest.TestCase):
    def setUp(self):
        sys.path.insert(0, str(ROOT))
        from tools import verify_framework

        self.module = verify_framework
        self.temporary = tempfile.TemporaryDirectory(prefix="verify-framework-prereq-")
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.root = self.base / "repo"
        self.root.mkdir()
        self.node, self.node_environment = fake_node(self.base / "node-ok", "v22.19.0")
        self.missing_node = self.base / "definitely-missing" / ("node.exe" if sys.platform == "win32" else "node")
        patcher = mock.patch.dict(os.environ, {"MIRALLY_NODE": str(self.node), **self.node_environment})
        patcher.start()
        self.addCleanup(patcher.stop)

    def call_main(self, *arguments: str) -> tuple[int, str, str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        with mock.patch.object(sys, "argv", ["verify_framework.py", "--root", str(self.root), *arguments]), \
                redirect_stdout(stdout), redirect_stderr(stderr):
            code = self.module.main()
        return code, stdout.getvalue(), stderr.getvalue()

    def assert_skipped(self, result: tuple[int, str, str]) -> None:
        code, stdout, stderr = result
        self.assertEqual(code, 3, stderr)
        self.assertIn("SKIP: missing prerequisites:", stderr)
        self.assertIn("tools\\setup_framework.py", stderr)
        self.assertEqual(stdout, "")
        self.assertNotIn("Traceback", stderr)


class VerifyFrameworkPrereqProcessTests(TempRootTestCase):
    """Exit codes and stderr of the real script, run as a subprocess."""

    def run_script(self, *arguments: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--root", str(self.root), *arguments],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=dict(os.environ, PYTHONIOENCODING="utf-8"),
            timeout=180,
        )

    def assert_process_skipped(self, result: subprocess.CompletedProcess) -> None:
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertIn("SKIP: missing prerequisites:", result.stderr)
        self.assertIn("tools\\setup_framework.py", result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertNotIn("Traceback", result.stderr)

    def test_missing_kernel_build_output_skips(self):
        build_prerequisites(self.root)
        (self.root / KERNEL_BUILD).unlink()
        result = self.run_script()
        self.assert_process_skipped(result)
        self.assertIn("kernel build output", result.stderr)
        self.assertIn("index.js", result.stderr)

    def test_missing_kernel_entry_skips(self):
        build_prerequisites(self.root)
        (self.root / KERNEL_ENTRY).unlink()
        result = self.run_script()
        self.assert_process_skipped(result)
        self.assertIn("kernel entry", result.stderr)

    def test_outdated_node_skips(self):
        build_prerequisites(self.root)
        old_node, old_environment = fake_node(self.base / "node-old", "v20.11.0")
        with mock.patch.dict(os.environ, {
            "MIRALLY_NODE": str(old_node),
            # An explicit MIRALLY_NODE cannot be walked past, so this case must skip
            # even when the host happens to have a supported Node on PATH.
            "PATH": str(self.base / "no-node-on-path"),
            **old_environment,
        }):
            result = self.run_script()
        self.assert_process_skipped(result)
        self.assertIn("Node 22.19+", result.stderr)

    def test_missing_frontend_dist_skips(self):
        build_prerequisites(self.root)
        (self.root / FRONTEND_BUILD).unlink()
        result = self.run_script()
        self.assert_process_skipped(result)
        self.assertIn("frontend build output", result.stderr)

    def test_missing_virtualenv_skips(self):
        build_prerequisites(self.root)
        (self.root / PYTHON).unlink()
        result = self.run_script()
        self.assert_process_skipped(result)
        self.assertIn("framework virtualenv", result.stderr)

    def test_non_empty_home_is_usage_error_not_skip(self):
        home = self.root / "used-home"
        home.mkdir()
        (home / "existing.json").write_text("{}", encoding="utf-8")
        result = self.run_script("--home", str(home))
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("--home must be missing or empty", result.stderr)
        self.assertNotIn("SKIP:", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_missing_prerequisites_never_start_host_or_bind_port(self):
        # The repository checkout has no built kernel here, so the script must skip
        # declaratively instead of raising from FrameworkHost.start().
        result = self.run_script()
        self.assert_process_skipped(result)
        self.assertNotIn("Mirally kernel is not built", result.stderr)

    def test_real_acceptance_failure_returns_one(self):
        build_prerequisites(self.root)
        # The prerequisite check now passes; starting the fixture "kernel" fails, which
        # is a real failure and must not be reported as a skip.
        result = self.run_script()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("FAIL:", result.stderr)
        self.assertNotIn("SKIP:", result.stderr)
        self.assertEqual(result.stdout, "")


class VerifyFrameworkPrereqUnitTests(TempRootTestCase):
    """The read-only helper and main() branches, without spawning the acceptance run."""

    def test_helper_is_read_only_and_reports_missing_items(self):
        before = sorted(str(path.relative_to(self.root)) for path in self.root.rglob("*"))
        missing = self.module.missing_prerequisites(self.root)
        self.assertTrue(missing)
        self.assertTrue(any("kernel build output" in item for item in missing))
        self.assertTrue(any("frontend build output" in item for item in missing))
        self.assertTrue(any("framework virtualenv" in item for item in missing))
        self.assertEqual(before, sorted(str(path.relative_to(self.root)) for path in self.root.rglob("*")))

    def test_helper_is_quiet_when_prerequisites_exist(self):
        build_prerequisites(self.root)
        self.assertEqual(self.module.missing_prerequisites(self.root), [])

    def test_missing_node_skips(self):
        build_prerequisites(self.root)
        with mock.patch.object(self.module, "node_candidates", return_value=[str(self.missing_node)]):
            result = self.call_main()
        self.assert_skipped(result)
        self.assertIn("Node 22.19+", result[2])

    def test_main_skips_without_constructing_host(self):
        def explode(*_args, **_kwargs):
            raise AssertionError("FrameworkHost/TemporaryDirectory must not run when prerequisites are missing")

        with mock.patch.object(self.module, "FrameworkHost", side_effect=explode), \
                mock.patch.object(self.module.tempfile, "TemporaryDirectory", side_effect=explode), \
                mock.patch.object(self.module, "verify", side_effect=explode):
            self.assert_skipped(self.call_main())

    def test_main_returns_two_for_non_empty_home(self):
        home = self.root / "used-home"
        home.mkdir()
        (home / "existing.json").write_text("{}", encoding="utf-8")
        code, stdout, stderr = self.call_main("--home", str(home))
        self.assertEqual(code, 2, stderr)
        self.assertIn("--home must be missing or empty", stderr)
        self.assertEqual(stdout, "")

    def test_main_returns_one_for_real_failure(self):
        build_prerequisites(self.root)

        def fail(*_args, **_kwargs):
            raise AssertionError("acceptance check failed")

        with mock.patch.object(self.module, "verify", side_effect=fail):
            code, stdout, stderr = self.call_main()
        self.assertEqual(code, 1, stderr)
        self.assertIn("FAIL: AssertionError: acceptance check failed", stderr)
        self.assertEqual(stdout, "")


if __name__ == "__main__":
    unittest.main()
