"""The framework installer must prepare the original UI and fail visibly."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("setup_framework_ui_test", ROOT / "tools/setup_framework.py")
setup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(setup)


class OriginalUISetupTests(unittest.TestCase):
    def prepare(self, directory: str):
        root = Path(directory) / "用户 workspace"
        node = root / "Node 24" / "node.exe"
        kernel = root / "kernel/dsh/packages/core/agent/lib/index.js"
        schedule = root / "kernel/dsh/packages/schedule/schedule/lib/index.js"
        frontend = root / "frontend/control-ui/dist"
        for path in (node, kernel, schedule, frontend / "index.html", frontend / ".mirally-build.json", node.parent / "node_modules/npm/bin/npm-cli.js"):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("", encoding="utf-8")
        return root, node, frontend

    def test_selected_node_and_separate_frontend_install_with_spaces_and_unicode(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, node, frontend = self.prepare(temporary)
            def run_command(command, **kwargs):
                return subprocess.CompletedProcess(command, 0, "v24.19.0\n" if command[-1] == "--version" else "", "")
            with patch.object(setup, "discover_node", return_value=node), patch.object(setup, "package_manager", return_value=["pnpm"]), patch.object(setup.subprocess, "run", side_effect=run_command) as run:
                self.assertEqual(setup.main(["--root", str(root)]), 0)
            self.assertEqual(run.call_args_list[0].args[0], [str(node), "--version"])
            calls = [(item.args[0], item.kwargs["cwd"]) for item in run.call_args_list if "cwd" in item.kwargs]
            self.assertEqual(calls[1][0][:3], [str(node), str(node.parent / "node_modules/npm/bin/npm-cli.js"), "ci"])
            self.assertIn("--ignore-scripts", calls[1][0])
            self.assertEqual(calls[1][1], frontend.parent.resolve())
            self.assertEqual(calls[3][0][-2:], ["run", "build"])

    def test_partial_frontend_build_is_not_ready(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, node, frontend = self.prepare(temporary)
            (frontend / ".mirally-build.json").unlink()
            with patch.object(setup, "discover_node", return_value=node):
                with self.assertRaisesRegex(RuntimeError, "Control UI has not been built"):
                    setup.main(["--root", str(root), "--skip-install", "--skip-build"])

    def test_missing_schedule_build_is_not_ready(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, node, _ = self.prepare(temporary)
            (root / "kernel/dsh/packages/schedule/schedule/lib/index.js").unlink()
            with patch.object(setup, "discover_node", return_value=node):
                with self.assertRaisesRegex(RuntimeError, "schedule package has not been built"):
                    setup.main(["--root", str(root), "--skip-install", "--skip-build"])

    def test_frontend_install_failure_stops_before_build(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, node, _ = self.prepare(temporary)
            def run(command, **kwargs):
                if command[-1] == "--version":
                    return subprocess.CompletedProcess(command, 0, "v24.19.0\n", "")
                if "ci" in command:
                    raise subprocess.CalledProcessError(1, command)
            with patch.object(setup, "discover_node", return_value=node), patch.object(setup, "package_manager", return_value=["pnpm"]), patch.object(setup.subprocess, "run", side_effect=run) as called:
                with self.assertRaises(subprocess.CalledProcessError):
                    setup.main(["--root", str(root)])
                self.assertEqual(called.call_count, 3)

    def test_explicit_and_discovered_node_22_fail_before_dependency_changes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, node, _ = self.prepare(temporary)
            for selected in (None, str(node)):
                with self.subTest(selected=selected), patch.object(setup, "discover_node", return_value=node) as discovery, patch.object(setup.subprocess, "run", return_value=subprocess.CompletedProcess([str(node), "--version"], 0, "v22.19.0\n", "")) as run:
                    arguments = ["--root", str(root), *(["--node", selected] if selected else [])]
                    with self.assertRaisesRegex(RuntimeError, "requires Node 24.*--node or MIRALLY_NODE"):
                        setup.main(arguments)
                    discovery.assert_called_once_with(root.resolve(), selected)
                    run.assert_called_once_with([str(node), "--version"], capture_output=True, text=True, timeout=15)

    def test_skip_install_still_checks_frontend_build_node_requirement(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, node, _ = self.prepare(temporary)
            with patch.object(setup, "discover_node", return_value=node), patch.object(setup.subprocess, "run", return_value=subprocess.CompletedProcess([str(node), "--version"], 0, "v22.21.0\n", "")) as run:
                with self.assertRaisesRegex(RuntimeError, "requires Node 24"):
                    setup.main(["--root", str(root), "--skip-install"])
                self.assertEqual(run.call_count, 1)

    def test_node_24_and_newer_satisfy_frontend_preparation(self):
        for version in ("v24.0.0", "v26.1.0"):
            with self.subTest(version=version), patch.object(setup.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, version + "\n", "")):
                self.assertEqual(setup.require_frontend_node(Path("chosen-node")), version)

    def test_unreadable_version_has_preparation_instructions(self):
        for result in (subprocess.CompletedProcess([], 0, "not a version", ""), subprocess.CompletedProcess([], 1, "v24.19.0", "failed")):
            with self.subTest(result=result), patch.object(setup.subprocess, "run", return_value=result):
                with self.assertRaisesRegex(RuntimeError, "Prepare Node 24.*npm.*--node or MIRALLY_NODE"):
                    setup.require_frontend_node(Path("chosen-node"))


if __name__ == "__main__":
    unittest.main()
