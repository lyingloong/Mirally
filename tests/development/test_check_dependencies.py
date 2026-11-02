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

    def test_malformed_manifest_types_warn_and_keep_auditing(self) -> None:
        """Valid JSON with unexpected types becomes a path-qualified warning (Codex P2)."""
        root = self.base_tree()
        self.write(root, "tools/testing/package.json", "[1, 2]")
        report = check_dependencies.audit(root)
        self.assertIn("invalid manifest: tools/testing/package.json (top-level value is list, "
                      "expected object)", report["warnings"])
        # The other manifests stay auditable and the run does not abort.
        by_path = {manifest["path"]: manifest for manifest in report["manifests"]}
        self.assertNotIn("tools/testing/package.json", by_path)
        self.assertEqual([(item["name"], item["constraint"])
                          for item in by_path["requirements-framework.txt"]["dependencies"]],
                         [("requests", "==2.31.0"), ("pywebview", "==6.1")])
        self.assertEqual(by_path["frontend/control-ui/package.json"]["dependencies"][0]["name"], "vite")
        with redirect_stdout(io.StringIO()):
            self.assertEqual(check_dependencies.main(["--root", str(root)]), 0)

        self.write(root, "tools/testing/package.json", '{"dependencies": ["x"]}')
        report = check_dependencies.audit(root)
        self.assertIn("invalid manifest field: tools/testing/package.json dependencies is list, "
                      "expected object", report["warnings"])
        # A malformed field is skipped; sibling dependency fields are still read.
        by_path = {manifest["path"]: manifest for manifest in report["manifests"]}
        self.assertEqual(by_path["tools/testing/package.json"]["dependencies"], [])
        self.assertEqual(by_path["requirements-framework.txt"]["dependencies"][0]["name"], "requests")

        self.write(root, "tools/testing/package.json",
                   '{"devDependencies": {"playwright-core": {"nested": true}, "vite": 6}}')
        report = check_dependencies.audit(root)
        self.assertIn("invalid manifest entry: tools/testing/package.json "
                      "devDependencies.playwright-core has dict constraint", report["warnings"])
        by_path = {manifest["path"]: manifest for manifest in report["manifests"]}
        self.assertEqual([(item["name"], item["constraint"])
                          for item in by_path["tools/testing/package.json"]["dependencies"]],
                         [("vite", "6")])

    def test_imports_use_full_ast_import_statements(self) -> None:
        """Every name of a multi-name import counts, so real deps are not flagged (Codex P2)."""
        root = self.base_tree()
        self.write(root, "mirally/app.py", "import os, requests\n")
        self.assertEqual(check_dependencies.imports_under(root / "mirally"), {"os", "requests"})
        self.assertEqual([item["name"] for item in check_dependencies.audit(root)["unused_candidates"]],
                         ["pywebview"])

        self.write(root, "mirally/app.py", "import requests as rq, os as operating_system\n")
        self.assertIn("requests", check_dependencies.imports_under(root / "mirally"))

        self.write(root, "mirally/app.py", "import os, requests\nfrom webview import Window\n")
        self.assertEqual(check_dependencies.audit(root)["unused_candidates"], [])

        # A file with a syntax error must not abort the audit or hide other imports.
        self.write(root, "mirally/app.py", "import requests\n\ndef broken(:\n")
        self.write(root, "mirally/shell.py", "import webview\n")
        modules = check_dependencies.imports_under(root / "mirally")
        self.assertIn("requests", modules)
        self.assertIn("webview", modules)
        self.assertEqual(check_dependencies.audit(root)["unused_candidates"], [])

    def test_syntax_error_recovery_keeps_from_imports(self) -> None:
        """A broken file must still yield its from-import, not offer it for removal (Codex P2)."""
        root = self.base_tree()
        # Valid line followed by a syntax error: `ast.parse` fails for the whole
        # file, so the recovery path decides whether `requests` stays imported.
        self.write(root, "mirally/app.py",
                   "from requests import get\nimport webview\n\ndef broken(:\n")
        modules = check_dependencies.imports_under(root / "mirally")
        self.assertIn("requests", modules)
        self.assertIn("webview", modules)
        report = check_dependencies.audit(root)
        self.assertEqual(report["unused_candidates"], [])
        self.assertNotIn("requests", [item["name"] for item in report["findings"]])

        # Aliases and dotted modules survive the fallback too.
        self.write(root, "mirally/app.py",
                   "from requests.adapters import HTTPAdapter as Adapter\n\ndef broken(:\n")
        self.write(root, "mirally/shell.py", "from webview import Window\n")
        recovered = check_dependencies.imports_under(root / "mirally")
        self.assertIn("requests.adapters", recovered)
        self.assertEqual(check_dependencies.audit(root)["unused_candidates"], [])

        # A relative from-import names no absolute module and must not leak one.
        self.write(root, "mirally/app.py", "from . import sibling\nfrom .sibling import helper\n\ndef broken(:\n")
        self.assertEqual(check_dependencies._imports_from_lines(
            self.relative_source(root, "mirally/app.py")), set())

    def test_unparseable_file_is_always_reported_as_a_warning(self) -> None:
        """An incomplete scan must be visible: path plus reason, never a silent empty set."""
        root = self.base_tree()
        self.write(root, "mirally/app.py", "import requests\nimport webview\n")
        clean = check_dependencies.audit(root)
        self.assertEqual([warning for warning in clean["warnings"] if "unparseable" in warning], [])

        # Recovery finds nothing: the file contributes no imports and says so.
        self.write(root, "mirally/broken.py", "def broken(:\n")
        report = check_dependencies.audit(root)
        warnings = [warning for warning in report["warnings"] if "unparseable" in warning]
        self.assertEqual(len(warnings), 1, report["warnings"])
        self.assertIn("mirally/broken.py", warnings[0])
        self.assertIn("SyntaxError", warnings[0])
        self.assertIn("no modules recovered", warnings[0])

        # Recovery finds some modules: still reported, and it says what it kept.
        self.write(root, "mirally/broken.py", "from requests import get\n\ndef broken(:\n")
        warnings = [w for w in check_dependencies.audit(root)["warnings"] if "unparseable" in w]
        self.assertEqual(len(warnings), 1, warnings)
        self.assertIn("mirally/broken.py", warnings[0])
        self.assertIn("recovered 1 module(s)", warnings[0])

        # A file that cannot even be decoded is reported, not skipped silently.
        (root / "mirally" / "binary.py").write_bytes(b"\xff\xfeimport requests\x00")
        warnings = [w for w in check_dependencies.audit(root)["warnings"] if "unparseable" in w]
        self.assertEqual(len(warnings), 2, warnings)
        self.assertTrue(any("mirally/binary.py" in w and "unreadable" in w for w in warnings), warnings)

    def relative_source(self, root: Path, name: str) -> str:
        return (root / name).read_text(encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
