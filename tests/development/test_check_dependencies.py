"""Tests for tools/check_dependencies.py (pure standard library, offline fixtures).

Every case builds a temporary repository-shaped tree so parsing, duplicate
detection, unused-candidate heuristics and exit codes are exercised without
reading or writing the real manifests.
"""
from __future__ import annotations

import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("check_dependencies", ROOT / "tools" / "check_dependencies.py")
check_dependencies = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(check_dependencies)


class CheckDependenciesTests(unittest.TestCase):
    def temp_root(self) -> Path:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        return Path(directory.name)

    def write(self, root: Path, name: str, text: str) -> None:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def base_tree(self) -> Path:
        root = self.temp_root()
        self.write(root, "requirements-framework.txt", "# product runtime\nrequests==2.31.0\npywebview==6.1\n")
        self.write(root, "requirements-ufo.txt", "# optional\n-r requirements-framework.txt\n")
        self.write(root, "kernel/dsh/.github/review-ownership/requirements.txt", "Pygments==2.21.0\n")
        self.write(root, "tools/testing/package.json",
                   '{"devDependencies": {"playwright-core": "1.63.0"}}')
        self.write(root, "frontend/control-ui/package.json", '{"dependencies": {"vite": "6.0.0"}}')
        self.write(root, "frontend/control-ui/ui/package.json", '{"dependencies": {"vite": "6.0.0"}}')
        self.write(root, "kernel/dsh/package.json", '{"dependencies": {"zod": "^4.4.3"}}')
        self.write(root, "mirally/app.py", "import requests\nimport webview\n")
        return root

    def manifests_by_path(self, root: Path) -> dict[str, dict]:
        return {manifest["path"]: manifest for manifest in check_dependencies.audit(root)["manifests"]}

    def test_manifests_are_classified_and_parsed(self) -> None:
        root = self.base_tree()
        by_path = self.manifests_by_path(root)
        self.assertEqual(by_path["requirements-framework.txt"]["kind"], "runtime")
        self.assertEqual(by_path["requirements-framework.txt"]["environment"], "product-venv")
        self.assertEqual(by_path["requirements-ufo.txt"]["kind"], "optional")
        self.assertEqual(by_path["kernel/dsh/.github/review-ownership/requirements.txt"]["kind"], "ci")
        self.assertEqual(by_path["tools/testing/package.json"]["environment"], "test-tools")
        self.assertEqual(by_path["frontend/control-ui/package.json"]["kind"], "upstream")
        self.assertEqual([(item["name"], item["constraint"])
                          for item in by_path["requirements-framework.txt"]["dependencies"]],
                         [("requests", "==2.31.0"), ("pywebview", "==6.1")])
        self.assertEqual(by_path["requirements-ufo.txt"]["dependencies"], [])
        self.assertEqual([(item["name"], item["constraint"])
                          for item in by_path["tools/testing/package.json"]["dependencies"]],
                         [("playwright-core", "1.63.0")])

        self.write(root, "frontend/control-ui/package-lock.json", "{}")
        self.write(root, "kernel/dsh/fixtures/package.json", '{"dependencies": {"zod": "^3.0.0"}}')
        self.write(root, "frontend/control-ui/packages/broken/package.json", "<<<<<<< HEAD\n")
        report = check_dependencies.audit(root)
        self.assertIn("frontend/control-ui/package-lock.json", report["lockfiles"])
        self.assertNotIn("kernel/dsh/fixtures/package.json",
                         {manifest["path"] for manifest in report["manifests"]})
        self.assertIn("unreadable manifest: frontend/control-ui/packages/broken/package.json",
                      report["warnings"])
        self.assertGreaterEqual(report["summary"]["excluded_fixture_package_json"], 1)

    def test_same_environment_conflict_is_a_finding(self) -> None:
        root = self.base_tree()
        self.write(root, "requirements-ufo.txt", "-r requirements-framework.txt\nrequests>=2.0\n")
        report = check_dependencies.audit(root)
        conflict = next(item for item in report["duplicates"] if item["name"] == "requests")
        self.assertEqual(conflict["environment"], "product-venv")
        self.assertEqual(conflict["severity"], "finding")
        self.assertEqual({item["constraint"] for item in conflict["declarations"]}, {"==2.31.0", ">=2.0"})
        self.assertEqual([item["kind"] for item in report["findings"]], ["duplicate-constraint"])
        with redirect_stdout(io.StringIO()):
            self.assertEqual(check_dependencies.main(["--root", str(root)]), 1)

    def test_kernel_workspace_conflict_is_informational(self) -> None:
        root = self.base_tree()
        self.write(root, "kernel/dsh/apps/web/package.json", '{"dependencies": {"zod": "^3.0.0"}}')
        report = check_dependencies.audit(root)
        conflict = next(item for item in report["duplicates"] if item["name"] == "zod")
        self.assertEqual(conflict["severity"], "info")
        self.assertEqual(report["findings"], [])
        with redirect_stdout(io.StringIO()):
            self.assertEqual(check_dependencies.main(["--root", str(root)]), 0)

    def test_unused_candidates_need_a_missing_import(self) -> None:
        root = self.base_tree()
        (root / "mirally" / "app.py").unlink()
        report = check_dependencies.audit(root)
        self.assertEqual([(item["name"], item["imports"]) for item in report["unused_candidates"]],
                         [("requests", ["requests"]), ("pywebview", ["webview"])])
        self.assertEqual({item["kind"] for item in report["findings"]}, {"unused-candidate"})
        self.write(root, "mirally/app.py", "from requests import Session\n")
        report = check_dependencies.audit(root)
        self.assertEqual([item["name"] for item in report["unused_candidates"]], ["pywebview"])
        self.write(root, "mirally/shell.py", "import webview\n")
        self.assertEqual(check_dependencies.audit(root)["unused_candidates"], [])

    def test_json_report_and_exit_codes(self) -> None:
        root = self.base_tree()
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(check_dependencies.main(["--root", str(root), "--json"]), 0)
        payload = json.loads(output.getvalue())
        self.assertIn("summary", payload)
        self.assertEqual(payload["findings"], [])
        self.assertEqual(payload["summary"]["findings"], 0)

        self.write(root, "requirements-ufo.txt", "-r requirements-framework.txt\nrequests>=2.0\n")
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(check_dependencies.main(["--root", str(root), "--json"]), 1)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["findings"][0]["kind"], "duplicate-constraint")
        self.assertEqual(payload["summary"]["findings"], 1)


if __name__ == "__main__":
    unittest.main()
