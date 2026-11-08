"""Declaration-level skip for tools/verify_framework.py when prerequisites are missing.

Offline and standard-library only: every case runs either the real script as a
subprocess or its ``main()`` against a temporary root, so no kernel build, Node
download, Host process or port is required.

Node stand-in executability
---------------------------
The fake Node is a real ``.cmd`` wrapper, and the tests that matter *run* it
(``node --version``), under several console code pages, from Chinese directories
and with a Chinese interpreter path. Writing the wrapper is never the assertion.

Known uncovered boundary: ``cmd.exe`` decodes a ``.cmd`` with the active ANSI
code page, so the wrapper must embed an ASCII interpreter path and relies on the
8.3 short name. On a volume with 8.3 name generation disabled,
``GetShortPathNameW`` returns the original Unicode path; ``_wrapper_executable``
now fails loudly there instead of emitting a wrapper that silently cannot run
(``test_wrapper_executable_refuses_an_unshortenable_unicode_path`` covers that
decision). ``test_node_fixture_runs_with_a_chinese_interpreter_path`` skips
itself on such a volume because the condition cannot be produced on the CI/dev
machines available to this repo; a machine that can create an 8.3-disabled
volume should turn that subtest into the primary regression.
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

# Stands in for Node: the wrapper file is itself executable (a ``.cmd`` on Windows, a
# shell script elsewhere) and runs the running interpreter on a script that answers
# ``--version`` from the version file kept beside it. Writing batch text into a file
# named ``node.exe`` was the old Windows fixture: CreateProcess cannot start it, so
# every "Node is installed" case silently exercised the "not Node" branch instead.
NODE_STAND_IN = """import sys
from pathlib import Path
if sys.argv[1:] == ["--version"]:
    print(Path(__file__).with_name("node-version.txt").read_text(encoding="ascii").strip())
"""


def _short_path(path: str) -> str | None:
    """The 8.3 short path for ``path``, or ``None`` when the volume cannot give one.

    ``GetShortPathNameW`` succeeds but returns the *original* Unicode path on
    volumes with 8.3 name generation disabled; callers must treat that as "no
    short path" instead of assuming an ASCII result.
    """
    if sys.platform != "win32":
        return None
    try:
        import ctypes

        buffer = ctypes.create_unicode_buffer(32768)
        length = ctypes.windll.kernel32.GetShortPathNameW(path, buffer, len(buffer))
        if length:
            return buffer.value
    except (AttributeError, OSError):
        pass
    return None


def _is_ascii(text: str) -> bool:
    try:
        text.encode("ascii")
    except UnicodeEncodeError:
        return False
    return True


def _wrapper_executable(path: str) -> str:
    """Return an ASCII-safe interpreter path for the Windows batch wrapper.

    ``cmd.exe`` reads ``.cmd`` files with the active ANSI code page, so UTF-8
    wrapper text breaks when an absolute path contains non-ASCII characters.
    The 8.3 short path is ASCII and refers to the same file. When the volume
    refuses to shorten (8.3 generation disabled), the short path is still the
    original Unicode path -- embedding it would produce a wrapper that cannot
    run under a non-UTF-8 code page, so callers get an explicit failure instead
    of a silently broken fixture.
    """
    if sys.platform != "win32":
        return path
    short = _short_path(path)
    if short is None or not _is_ascii(short):
        if _is_ascii(path):
            return path
        raise RuntimeError(
            "no ASCII-safe interpreter path: GetShortPathNameW returned "
            f"{short!r} for {path!r}. This volume has 8.3 name generation "
            "disabled, so the batch wrapper cannot embed a runnable "
            "interpreter path under a legacy code page."
        )
    return short


def fake_node(directory: Path, version: str) -> Path:
    """A real, runnable stand-in whose ``node --version`` prints ``version``."""
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "node-version.txt").write_text(version + "\n", encoding="ascii")
    script = directory / "node_version.py"
    script.write_text(NODE_STAND_IN, encoding="utf-8")
    python = _wrapper_executable(sys.executable)
    if sys.platform == "win32":
        path = directory / "node.cmd"
        # Only the interpreter path is absolute; the script is resolved from the
        # wrapper's own directory so a Chinese TEMP path never enters batch text.
        path.write_text('@echo off\r\n"%s" "%%~dp0node_version.py" %%*\r\n' % python, encoding="utf-8")
    else:
        path = directory / "node"
        path.write_text('#!/bin/sh\nexec "%s" "$(dirname "$0")/node_version.py" "$@"\n' % python,
                        encoding="utf-8")
        path.chmod(0o755)
    return path


def copy_runnable_interpreter(destination: Path) -> Path:
    """Copy an interpreter that can actually run from ``destination``.

    ``shutil.copy2(sys.executable, ...)`` copies the venv *shim*, which exits 106
    ("No pyvenv.cfg file") once it is outside its own ``Scripts`` directory, so a
    fixture built on it proves nothing about execution. Copy the base interpreter
    the shim points at (``sys.base_prefix``) together with the DLLs it needs.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    base = Path(sys.base_prefix) / Path(sys.executable).name
    source = base if base.is_file() else Path(sys.executable)
    shutil.copy2(source, destination)
    if sys.platform == "win32":
        # A relocated python.exe needs its runtime next to it.
        for dll in Path(sys.base_prefix).glob("python*.dll"):
            shutil.copy2(dll, destination.parent / dll.name)
    return destination


def run_node(path: Path, *arguments: str, code_page: str | None = None) -> subprocess.CompletedProcess:
    """Run a node stand-in for real, optionally under a specific console code page.

    Windows resolves ``.cmd`` through ``cmd.exe`` with the *active* ANSI code
    page, which is the whole reason the wrapper embeds an ASCII path. Passing
    ``code_page`` reproduces that decode step (936=GBK, 437=US-ASCII, 65001=UTF-8)
    without changing the machine-wide setting.
    """
    command = [str(path), *arguments]
    if code_page and sys.platform == "win32":
        command = ["cmd", "/c", "chcp", code_page, "&&", *command]
    return subprocess.run(command, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=60)


def not_node() -> Path:
    """A real executable that is not Node: the running interpreter itself."""
    return Path(sys.executable)


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
        self.node = fake_node(self.base / "node-ok", "v22.19.0")
        self.missing_node = self.base / "definitely-missing" / ("node.exe" if sys.platform == "win32" else "node")
        patcher = mock.patch.dict(os.environ, {"MIRALLY_NODE": str(self.node)})
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

    def run_script(self, *arguments: str, node: Path | None = None,
                   extra_environment: dict[str, str] | None = None) -> subprocess.CompletedProcess:
        # The script has no --node flag here, so the stand-in travels through
        # MIRALLY_NODE -- the configured slot discover_node() reads first.
        environment = dict(os.environ, PYTHONIOENCODING="utf-8",
                           MIRALLY_NODE=str(node or self.node))
        environment.update(extra_environment or {})
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--root", str(self.root), *arguments],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=environment,
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

    def test_node_fixture_is_a_real_executable(self):
        # Regression guard for the Windows fixture: batch text written into a file named
        # node.exe is not executable, so every "supported Node" case used to take the
        # "not Node" branch. The stand-in must really answer node --version.
        result = run_node(self.node, "--version")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "v22.19.0")

    def test_node_fixture_runs_from_a_chinese_temp_directory(self):
        # Regression: writing the wrapper with ascii raised UnicodeEncodeError when
        # TEMP contained Chinese characters, before any test body could run.
        chinese_temp = self.base / "中文临时目录"
        node = fake_node(chinese_temp / "node-中文", "v22.19.0")
        result = run_node(node, "--version")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "v22.19.0")

    def test_node_fixture_writes_wrapper_for_a_chinese_interpreter_path(self):
        # The wrapper embeds the interpreter path; a Chinese install path must not
        # fail the write. The copy only needs to exist for the short-path lookup.
        chinese_python = self.base / "中文解释器" / "python.exe"
        chinese_python.parent.mkdir(parents=True)
        shutil.copy2(sys.executable, chinese_python)
        with mock.patch.object(sys, "executable", str(chinese_python)):
            node = fake_node(self.base / "node-chinese-python", "v22.19.0")
        self.assertTrue(node.is_file())
        self.assertIn("node_version.py", node.read_text(encoding="utf-8"))

    def test_node_fixture_runs_with_a_chinese_interpreter_path(self):
        # Writing the wrapper is not enough: it must actually run. A Chinese path
        # that the volume shortens (8.3) yields an ASCII interpreter in the batch
        # text, so the wrapper executes under every legacy code page.
        chinese_python = self.base / "中文解释器 with spaces" / "python.exe"
        copy_runnable_interpreter(chinese_python)
        short = _short_path(str(chinese_python))
        if short is None or not _is_ascii(short):
            self.skipTest(
                f"volume cannot shorten {chinese_python} to an ASCII 8.3 name "
                f"(GetShortPathNameW -> {short!r}); see the runnable-fixture "
                "limitation documented in the test module docstring"
            )
        with mock.patch.object(sys, "executable", str(chinese_python)):
            node = fake_node(self.base / "node-chinese-interpreter", "v22.19.0")
        # The embedded path is the short form, never the Unicode one.
        self.assertNotIn("中文", node.read_text(encoding="utf-8"))
        for code_page in (None, "936", "437", "65001"):
            with self.subTest(code_page=code_page):
                result = run_node(node, "--version", code_page=code_page)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("v22.19.0", result.stdout)

    def test_wrapper_executable_refuses_an_unshortenable_unicode_path(self):
        # The regression that kept this thread open: GetShortPathNameW returns the
        # *original* Unicode path when 8.3 generation is off, and returning it as
        # though it were a short path produced a wrapper that cannot run under a
        # non-UTF-8 code page. Simulate that volume by making the lookup yield the
        # Unicode input, and require an explicit failure instead of a bad path.
        unicode_path = str(self.base / "中文解释器 with spaces" / "python.exe")
        module = sys.modules[__name__]
        with mock.patch.object(module, "_short_path", return_value=unicode_path):
            with self.assertRaises(RuntimeError) as raised:
                _wrapper_executable(unicode_path)
        self.assertIn("name generation disabled", str(raised.exception))

    def test_wrapper_executable_keeps_an_ascii_path_when_shortening_fails(self):
        # An ASCII interpreter needs no short name: the lookup failing must not
        # turn a perfectly runnable fixture into an error.
        ascii_path = str(self.base / "plain-python" / "python.exe")
        module = sys.modules[__name__]
        with mock.patch.object(module, "_short_path", return_value=None):
            self.assertEqual(_wrapper_executable(ascii_path), ascii_path)

    def test_outdated_node_skips(self):
        # The real v20 stand-in is actually run by the script (no mocking), so this also
        # proves the fixture is executable: too old means skip, not a start.
        build_prerequisites(self.root)
        old_node = fake_node(self.base / "node-old", "v20.11.0")
        result = self.run_script(node=old_node,
                                 extra_environment={"PATH": str(self.base / "no-node-on-path")})
        self.assert_process_skipped(result)
        self.assertIn("Node 22.19+", result.stderr)

    def test_missing_configured_node_is_a_configuration_error_even_with_node_on_path(self):
        # discover_node() raises for a broken MIRALLY_NODE and never falls through to
        # PATH; the precheck must fail the same way instead of starting the acceptance
        # run with a different Node than the user configured.
        build_prerequisites(self.root)
        path_node = fake_node(self.base / "node-on-path", "v22.19.0")
        # That PATH entry alone satisfies the check, so the failure below really comes
        # from the broken configured slot winning over it.
        with mock.patch.dict(os.environ, {"MIRALLY_NODE": "", "PATH": str(path_node.parent)}):
            self.assertEqual(self.module.node_supported(self.root), (True, []))
        result = self.run_script(node=self.missing_node,
                                 extra_environment={"PATH": str(path_node.parent)})
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("Configured Node executable does not exist", result.stderr)
        self.assertNotIn("SKIP:", result.stderr)
        self.assertNotIn("PASS ", result.stdout)
        self.assertNotIn("Traceback", result.stderr)

    def test_no_configured_node_and_no_node_on_path_skips(self):
        # Only a broken configured slot is a configuration error. No MIRALLY_NODE and no
        # Node on PATH (the Windows case that matters here) is a missing prerequisite:
        # skip without a traceback and without starting anything.
        build_prerequisites(self.root)
        result = self.run_script(extra_environment={"MIRALLY_NODE": "", "PATH": ""})
        self.assert_process_skipped(result)
        self.assertIn("Node 22.19+", result.stderr)
        self.assertNotIn("Configured Node executable does not exist", result.stderr)

    def test_configured_broken_node_fails_without_overriding_path(self):
        # MIRALLY_NODE (set in setUp) points at a runnable Node; an explicitly broken
        # configured path must still not fall through to it.
        build_prerequisites(self.root)
        result = self.run_script(node=self.missing_node)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("Configured Node executable does not exist", result.stderr)
        self.assertNotIn("SKIP:", result.stderr)
        self.assertNotIn("PASS ", result.stdout)

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
        # A real file that is not Node (the running interpreter), in a non-configured
        # slot: "no Node installed", not a configuration error.
        with mock.patch.dict(os.environ, {"MIRALLY_NODE": ""}), \
                mock.patch.object(self.module, "node_candidates", return_value=[str(not_node())]):
            result = self.call_main()
        self.assert_skipped(result)
        self.assertIn("Node 22.19+", result[2])

    def test_configured_node_is_checked_with_discover_node_semantics(self):
        # Same rule as discover_node(): a missing configured candidate is a configuration
        # error, a missing PATH/artifact candidate is just "no Node".
        with mock.patch.dict(os.environ, {"MIRALLY_NODE": str(self.missing_node)}), \
                mock.patch.object(self.module, "node_candidates",
                                  return_value=[str(self.missing_node), str(self.node)]):
            with self.assertRaises(RuntimeError) as caught:
                self.module.node_supported(self.root)
        self.assertIn("Configured Node executable does not exist", str(caught.exception))
        # Only PATH/artifact candidates remain: an empty configured slot is fine.
        with mock.patch.dict(os.environ, {"MIRALLY_NODE": ""}), \
                mock.patch.object(self.module, "node_candidates",
                                  return_value=[str(self.missing_node)]):
            found, problems = self.module.node_supported(self.root)
        self.assertFalse(found)
        self.assertTrue(any("does not exist" in problem for problem in problems))

    def test_precheck_agrees_with_discover_node(self):
        # The judgment itself, not only the message: both accept the same configured
        # Node and both reject the same broken one with the same error.
        import mirally.framework_host as framework_host

        accepted = [str(self.node)]
        with mock.patch.dict(os.environ, {"MIRALLY_NODE": str(self.node)}), \
                mock.patch.object(self.module, "node_candidates", return_value=accepted), \
                mock.patch.object(framework_host, "node_candidates", return_value=accepted):
            self.assertEqual(self.module.node_supported(self.root), (True, []))
            self.assertEqual(framework_host.discover_node(self.root).resolve(), self.node.resolve())

        broken = [str(self.missing_node), str(self.node)]
        with mock.patch.dict(os.environ, {"MIRALLY_NODE": str(self.missing_node)}), \
                mock.patch.object(self.module, "node_candidates", return_value=broken), \
                mock.patch.object(framework_host, "node_candidates", return_value=broken):
            with self.assertRaises(RuntimeError) as precheck:
                self.module.node_supported(self.root)
            with self.assertRaises(RuntimeError) as host_check:
                framework_host.discover_node(self.root)
        self.assertEqual(str(precheck.exception), str(host_check.exception))

    def test_main_reports_broken_configured_node_as_usage_error(self):
        build_prerequisites(self.root)
        with mock.patch.dict(os.environ, {"MIRALLY_NODE": str(self.missing_node)}), \
                mock.patch.object(self.module, "node_candidates",
                                  return_value=[str(self.missing_node), str(self.node)]):
            code, stdout, stderr = self.call_main()
        self.assertEqual(code, 2, stderr)
        self.assertIn("Configured Node executable does not exist", stderr)
        self.assertNotIn("SKIP:", stderr)
        self.assertEqual(stdout, "")

    def test_missing_node_never_starts_a_host(self):
        build_prerequisites(self.root)

        def explode(*_args, **_kwargs):
            raise AssertionError("a missing Node must skip before any process starts")

        with mock.patch.dict(os.environ, {"MIRALLY_NODE": ""}), \
                mock.patch.object(self.module, "node_candidates", return_value=[str(not_node())]), \
                mock.patch.object(self.module, "FrameworkHost", side_effect=explode), \
                mock.patch.object(self.module, "verify", side_effect=explode):
            self.assert_skipped(self.call_main())

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
