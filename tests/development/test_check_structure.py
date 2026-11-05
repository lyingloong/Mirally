from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools import check_structure  # noqa: E402

TOOL = ROOT / "tools" / "check_structure.py"


class TopLevelTests(unittest.TestCase):
    def test_registered_top_level_entries_pass(self):
        result = check_structure.check_top_level([".github", "docs", "README.md", "tools"])
        self.assertTrue(result["ok"])
        self.assertEqual(result["unregistered"], [])
        self.assertEqual(result["entries"], 4)

    def test_unregistered_top_level_entry_is_reported(self):
        result = check_structure.check_top_level([".github", "docs", "new-area"])
        self.assertFalse(result["ok"])
        self.assertEqual(result["unregistered"], ["new-area"])


class ArtifactTests(unittest.TestCase):
    def test_tracked_generated_files_are_reported(self):
        files = [
            "docs/readme.md",
            "kernel/app/dist/bundle.js",
            "build/app.tsbuildinfo",
            "coverage/index.html",
            "node_modules/pkg/index.js",
            "mirally/core/__pycache__/cache.pyc",
            "src/app.js.map",
            ".pytest_cache/v/cache",
            "tests/development/test_check_structure.py",
        ]
        result = check_structure.check_artifacts(files)
        self.assertFalse(result["ok"])
        self.assertEqual(result["matches"], [
            ".pytest_cache/v/cache",
            "build/app.tsbuildinfo",
            "coverage/index.html",
            "kernel/app/dist/bundle.js",
            "mirally/core/__pycache__/cache.pyc",
            "node_modules/pkg/index.js",
            "src/app.js.map",
        ])

    def test_clean_file_list_passes(self):
        self.assertTrue(check_structure.check_artifacts(["README.md", "tools/check_structure.py"])["ok"])


class ToolsNamingTests(unittest.TestCase):
    def test_current_tool_names_pass(self):
        entries = [
            "build-activity-wgc.ps1",
            "build_brand_assets.py",
            "check_docs_links.py",
            "check_structure.py",
            "dev_workflow",
            "redraw_brand_vector.py",
            "render_brand_vector.py",
            "setup_framework.py",
            "testing",
            "verify_companion_menu.mjs",
            "verify_framework.py",
        ]
        result = check_structure.check_tools(entries)
        self.assertTrue(result["ok"], result["violations"])

    def test_bad_prefix_extension_and_directory_are_reported(self):
        result = check_structure.check_tools(["helper.py", "notes.txt", "misc-dir"])
        self.assertFalse(result["ok"])
        self.assertEqual(
            [(item["entry"], item["reason"]) for item in result["violations"]],
            [
                ("helper.py", "name must start with check_/verify_/build_/setup_/redraw_/render_"),
                ("misc-dir", "unregistered tools directory"),
                ("notes.txt", "extension .txt is not allowed"),
            ],
        )


class ImportDirectionTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def write(self, relative: str, source: str) -> str:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
        return relative

    def test_core_cannot_import_product_layers(self):
        files = [
            self.write("mirally/core/domain.py",
                       "from mirally.runtime.memory import recall\nimport mirally.framework.channels\n"),
        ]
        result = check_structure.check_imports(self.root, files)
        self.assertFalse(result["ok"])
        self.assertEqual(
            sorted(item["import"] for item in result["violations"]),
            ["mirally.framework.channels", "mirally.runtime.memory"],
        )

    def test_runtime_framework_import_is_a_known_exception_but_other_files_fail(self):
        files = [
            self.write("mirally/runtime/speech_credentials.py",
                       "from mirally.framework.channels import CredentialProtector\n"),
            self.write("mirally/runtime/other.py", "from ..framework.channels import channel_owner\n"),
        ]
        result = check_structure.check_imports(self.root, files)
        self.assertFalse(result["ok"])
        self.assertEqual(
            [(item["file"], item["import"]) for item in result["exceptions"]],
            [("mirally/runtime/speech_credentials.py", "mirally.framework.channels")],
        )
        self.assertEqual(
            [(item["file"], item["import"]) for item in result["violations"]],
            [("mirally/runtime/other.py", "mirally.framework.channels")],
        )

    def test_framework_may_read_runtime_and_unrelated_files_are_ignored(self):
        files = [
            self.write("mirally/framework/context.py", "from mirally.runtime.activity import source\n"),
            self.write("tests/test_x.py", "from mirally.framework.channels import channel_owner\n"),
        ]
        result = check_structure.check_imports(self.root, files)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["violations"], [])

    def test_from_package_alias_is_caught(self):
        files = [self.write("mirally/core/alias.py", "from mirally import runtime\n")]
        result = check_structure.check_imports(self.root, files)
        self.assertFalse(result["ok"])
        self.assertEqual([item["import"] for item in result["violations"]], ["mirally.runtime"])


class IndexDerivationTests(unittest.TestCase):
    def test_top_level_and_tools_entries_are_derived_from_index_paths(self):
        files = [
            "README.md",
            "docs/x.md",
            "tools/check_a.py",
            "tools/dev_workflow/ops.py",
            "tools/litter.py",
        ]
        self.assertEqual(check_structure._top_level_entries(files), ["README.md", "docs", "tools"])
        self.assertEqual(
            check_structure._tools_direct_entries(files),
            ["check_a.py", "dev_workflow", "litter.py"],
        )


class ReportTests(unittest.TestCase):
    def test_current_checkout_audit_is_clean_and_json_is_machine_readable(self):
        result = subprocess.run([sys.executable, str(TOOL), "--json"], cwd=str(ROOT), capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        report = json.loads(result.stdout)
        self.assertTrue(report["ok"])
        self.assertEqual(set(report["checks"]), {"top_level", "artifacts", "tools", "imports"})
        self.assertEqual(
            [(item["file"], item["import"]) for item in report["checks"]["imports"]["exceptions"]],
            [("mirally/runtime/speech_credentials.py", "mirally.framework.channels")],
        )
        self.assertEqual(report["checks"]["artifacts"]["matches"], [])

    def test_human_summary_reports_ok(self):
        result = subprocess.run([sys.executable, str(TOOL)], cwd=str(ROOT), capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("top-level entries: ok", result.stdout)
        self.assertIn("dependency direction: ok", result.stdout)
        self.assertTrue(result.stdout.rstrip().endswith("result: ok"))

    def test_cli_reports_findings_with_exit_code_one(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "-C", str(root), "init", "-q"], check=True, capture_output=True)
            (root / "README.md").write_text("# fixture\n", encoding="utf-8")
            (root / "unregistered").mkdir()
            (root / "unregistered" / "note.md").write_text("x\n", encoding="utf-8")
            (root / "tools").mkdir()
            (root / "tools" / "check_fixture.py").write_text("", encoding="utf-8")
            subprocess.run(["git", "-C", str(root), "add", "."], check=True, capture_output=True)
            subprocess.run([
                "git", "-C", str(root), "-c", "user.name=fixture", "-c", "user.email=fixture@example.invalid",
                "commit", "-qm", "fixture",
            ], check=True, capture_output=True)
            result = subprocess.run([sys.executable, str(TOOL), "--root", str(root), "--json"],
                                    capture_output=True, text=True)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        report = json.loads(result.stdout)
        self.assertFalse(report["ok"])
        self.assertEqual(report["checks"]["top_level"]["unregistered"], ["unregistered"])

    def test_staged_uncommitted_root_and_tools_files_are_reported(self):
        # No commit at all: the audit must follow the index, not HEAD.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "-C", str(root), "init", "-q"], check=True, capture_output=True)
            (root / "README.md").write_text("# fixture\n", encoding="utf-8")
            (root / "tools").mkdir()
            (root / "tools" / "check_fixture.py").write_text("", encoding="utf-8")
            subprocess.run(["git", "-C", str(root), "add", "."], check=True, capture_output=True)
            (root / "rogue_root.txt").write_text("x\n", encoding="utf-8")
            (root / "tools" / "litter.py").write_text("", encoding="utf-8")
            subprocess.run(["git", "-C", str(root), "add", "-A"], check=True, capture_output=True)
            result = subprocess.run([sys.executable, str(TOOL), "--root", str(root), "--json"],
                                    capture_output=True, text=True)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        report = json.loads(result.stdout)
        self.assertFalse(report["ok"])
        self.assertEqual(report["checks"]["top_level"]["unregistered"], ["rogue_root.txt"])
        self.assertEqual(
            [item["entry"] for item in report["checks"]["tools"]["violations"]],
            ["litter.py"],
        )

    def test_chinese_path_import_direction_is_checked_from_the_index(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "-C", str(root), "init", "-q"], check=True, capture_output=True)
            (root / "README.md").write_text("# fixture\n", encoding="utf-8")
            (root / "mirally" / "core").mkdir(parents=True)
            (root / "mirally" / "core" / "中文 模块.py").write_text("import mirally.runtime\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(root), "add", "-A"], check=True, capture_output=True)
            subprocess.run([
                "git", "-C", str(root), "-c", "user.name=fixture", "-c", "user.email=fixture@example.invalid",
                "commit", "-qm", "fixture",
            ], check=True, capture_output=True)
            result = subprocess.run([sys.executable, str(TOOL), "--root", str(root), "--json"],
                                    capture_output=True, text=True)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(
            [(item["file"], item["import"]) for item in report["checks"]["imports"]["violations"]],
            [("mirally/core/中文 模块.py", "mirally.runtime")],
        )


if __name__ == "__main__":
    unittest.main()
