"""Exercise CI selection against real, isolated Git histories."""
from __future__ import annotations

import importlib.util
from contextlib import redirect_stdout
import io
import json
import os
import re
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("ci_scope", ROOT / "tools/check_ci_scope.py")
ci_scope = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(ci_scope)


class CiScopeTests(unittest.TestCase):
    def git(self, root: Path, *args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(root), *args], check=True,
            capture_output=True, text=True,
        ).stdout.strip()

    def repo(self) -> Path:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.git(root, "init", "-q", "-b", "main")
        self.git(root, "config", "user.name", "CI fixture")
        self.git(root, "config", "user.email", "ci@example.invalid")
        self.write(root, "README.md", "# Fixture\n")
        self.commit(root)
        return root

    def write(self, root: Path, name: str, text: str = "fixture\n") -> None:
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")

    def commit(self, root: Path) -> str:
        self.git(root, "add", "-A")
        self.git(root, "commit", "-qm", "fixture")
        return self.git(root, "rev-parse", "HEAD")

    def scope(self, root: Path, base: str, head: str, event: str = "push") -> bool:
        payload = ({"before": base, "after": head} if event == "push" else
                   {"pull_request": {"base": {"sha": base}}})
        return ci_scope.select_scope(event, payload, head, root)[0]

    def test_only_explicit_repository_maintenance_paths_skip_product(self) -> None:
        safe = ["docs/development/proposals/example.md", "README.md", "AGENTS.md",
                "CONTRIBUTING.md", "CHANGELOG.md", "LICENSE",
                ".github/pull_request_template.md", "tools/testing/README.md",
                "tools/dev_workflow/ops.py", "tests/development/test_example.py",
                "tools/check_docs_links.py", "tools/check_sources.py",
                "tools/check_structure.py", "tools/check_local_artifacts.py",
                "website/src/App.jsx", "website/package-lock.json", ".github/workflows/website.yml"]
        self.assertFalse(ci_scope.requires_product(safe))
        for path in ["mirally/framework/service.py", "tools/setup_framework.py",
                     "tools/check_ci_scope.py", ".github/workflows/mirally-pr.yml",
                     "tools/testing/package-lock.json", "requirements-framework.txt",
                     "skills/example/SKILL.md", "docs/example.py", "unknown.txt"]:
            with self.subTest(path=path):
                self.assertTrue(ci_scope.requires_product([*safe, path]))
        self.assertTrue(ci_scope.requires_product([]))

    def test_add_delete_and_cross_boundary_moves(self) -> None:
        for old, new, expected in [
            (None, "docs/中文 空格.md", False),
            (None, "mirally/new.py", True),
            ("docs/old.md", None, False),
            ("mirally/old.py", None, True),
            ("docs/old.md", "mirally/new.py", True),
            ("mirally/old.py", "docs/new.md", True),
            ("website/src/old.jsx", "mirally/new.py", True),
            ("mirally/old.py", "website/src/new.jsx", True),
            ("website/src/old.jsx", "website/src/new.jsx", False),
        ]:
            with self.subTest(old=old, new=new):
                root = self.repo()
                if old:
                    self.write(root, old)
                    self.commit(root)
                base = self.git(root, "rev-parse", "HEAD")
                if old:
                    (root / old).unlink()
                if new:
                    self.write(root, new)
                head = self.commit(root)
                self.assertEqual(self.scope(root, base, head), expected)

    def test_push_considers_all_commits_since_before(self) -> None:
        root = self.repo()
        base = self.git(root, "rev-parse", "HEAD")
        self.write(root, "mirally/product.py")
        self.commit(root)
        self.write(root, "docs/followup.md")
        head = self.commit(root)
        self.assertTrue(self.scope(root, base, head))

    def test_pr_uses_full_synthetic_merge_diff(self) -> None:
        root = self.repo()
        base = self.git(root, "rev-parse", "HEAD")
        self.git(root, "checkout", "-qb", "proposal")
        self.write(root, "docs/proposal.md")
        proposal = self.commit(root)
        self.assertFalse(self.scope(root, base, proposal, "pull_request"))
        self.git(root, "checkout", "-q", "main")
        self.write(root, "mirally/integration.py")
        self.commit(root)
        self.git(root, "merge", "--no-ff", "-qm", "synthetic PR merge", "proposal")
        merged = self.git(root, "rev-parse", "HEAD")
        self.assertTrue(self.scope(root, base, merged, "pull_request"))
        # A valid SHA belonging to another checkout must not authorize a skip.
        self.assertTrue(self.scope(root, base, proposal, "pull_request"))

    def test_unavailable_shallow_history_falls_back_to_product(self) -> None:
        root = self.repo()
        before = self.git(root, "rev-parse", "HEAD")
        self.write(root, "docs/one.md")
        self.commit(root)
        self.write(root, "docs/two.md")
        head = self.commit(root)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        shallow = Path(directory.name) / "clone"
        self.git(root, "clone", "--quiet", "--depth", "2", root.as_uri(), str(shallow))
        self.assertTrue(self.scope(shallow, before, head))

    def test_invalid_events_and_empty_diff_never_skip_product(self) -> None:
        root = self.repo()
        head = self.git(root, "rev-parse", "HEAD")
        for event, payload, supplied_head in [
            ("workflow_dispatch", {}, head), ("unexpected", {}, head),
            ("push", {}, head),
            ("push", {"before": "0" * 40, "after": head}, head),
            ("push", {"before": head, "after": "f" * 40}, head),
            ("push", {"before": head, "after": head}, head),
            ("push", {"before": "f" * 40, "after": head}, head),
            ("pull_request", {"pull_request": None}, head),
            ("pull_request", {"pull_request": {"base": {"sha": "--help"}}}, head),
            ("push", {"before": head, "after": head}, "--help"),
        ]:
            with self.subTest(event=event, payload=payload, head=supplied_head):
                self.assertTrue(ci_scope.select_scope(event, payload, supplied_head, root)[0])

    def test_main_writes_scope_and_unreadable_event_falls_back(self) -> None:
        root = self.repo()
        base = self.git(root, "rev-parse", "HEAD")
        self.write(root, "docs/proposal.md")
        head = self.commit(root)
        event_file = root / "event.json"
        event_file.write_text(json.dumps({"before": base, "after": head}), encoding="utf-8")
        output = root / "output.txt"
        summary = root / "summary.txt"
        output.write_text("existing=value\n", encoding="utf-8")
        environment = {
            "GITHUB_EVENT_NAME": "push", "GITHUB_EVENT_PATH": str(event_file),
            "GITHUB_SHA": head, "GITHUB_OUTPUT": str(output),
            "GITHUB_STEP_SUMMARY": str(summary),
        }
        with patch.object(ci_scope, "ROOT", root), patch.dict(os.environ, environment):
            with redirect_stdout(io.StringIO()):
                self.assertEqual(ci_scope.main(), 0)
            self.assertEqual(output.read_text(encoding="utf-8"), "existing=value\nproduct=false\nwebsite=false\nwebsite_changed=false\n")
            self.assertIn("CI scope:", summary.read_text(encoding="utf-8"))
            # A malformed event must append an explicit full-check selection.
            event_file.write_text("{not valid JSON", encoding="utf-8")
            with redirect_stdout(io.StringIO()):
                self.assertEqual(ci_scope.main(), 0)
            self.assertTrue(output.read_text(encoding="utf-8").endswith("product=true\nwebsite=true\nwebsite_changed=false\n"))
            event_file.unlink()
            with redirect_stdout(io.StringIO()):
                self.assertEqual(ci_scope.main(), 0)
            self.assertTrue(output.read_text(encoding="utf-8").endswith("product=true\nwebsite=true\nwebsite_changed=false\n"))

    def test_website_selection_uses_full_diff_beyond_hosted_path_filter_limit(self) -> None:
        root = self.repo()
        base = self.git(root, "rev-parse", "HEAD")
        for number in range(310):
            self.write(root, f"docs/fixture-{number:03}.md")
        self.write(root, "website/src/App.jsx")
        head = self.commit(root)
        paths, _ = ci_scope.comparison_paths("push", {"before": base, "after": head}, head, root)
        self.assertEqual(len(paths), 311)
        self.assertFalse(ci_scope.requires_product(paths))
        self.assertTrue(ci_scope.website_changed(paths))
        self.assertFalse(ci_scope.website_changed(None))
        self.assertFalse(ci_scope.website_changed([]))
        self.assertFalse(ci_scope.website_changed(["mirally/framework/service.py", "unknown.txt"]))
        self.assertTrue(ci_scope.website_changed([".github/workflows/website.yml"]))

    def test_website_output_distinguishes_validation_from_publication_evidence(self) -> None:
        for filename, product, website in [("website/src/App.jsx", "false", "true"),
                                            ("mirally/product.py", "true", "false")]:
            with self.subTest(filename=filename):
                root = self.repo()
                base = self.git(root, "rev-parse", "HEAD")
                self.write(root, filename)
                head = self.commit(root)
                self.write(root, "event.json", json.dumps({"before": base, "after": head}))
                environment = {"GITHUB_EVENT_NAME": "push", "GITHUB_EVENT_PATH": str(root / "event.json"),
                               "GITHUB_SHA": head, "GITHUB_OUTPUT": str(root / "output.txt"),
                               "GITHUB_STEP_SUMMARY": str(root / "summary.txt")}
                with patch.object(ci_scope, "ROOT", root), patch.dict(os.environ, environment), redirect_stdout(io.StringIO()):
                    self.assertEqual(ci_scope.main(), 0)
                actual = dict(line.split("=", 1) for line in (root / "output.txt").read_text(encoding="utf-8").splitlines())
                self.assertEqual(actual, {"product": product, "website": website, "website_changed": website})


WORKFLOW = ROOT / ".github/workflows/mirally-pr.yml"

# Added by dev-0039: pure-offline product modules that must stay in the CI subset.
OFFLINE_MODULES = (
    "tests.test_activity_projection",
    "tests.test_conversation",
    "tests.test_delivery_invalidation",
    "tests.test_execution_contract",
    "tests.test_framework_memory_replay",
    "tests.test_semantic_pipeline",
)


def python_product_subset() -> list[str]:
    """Return the product-core module list without requiring a YAML parser."""
    text = WORKFLOW.read_text(encoding="utf-8")
    marker = "Test Python product core"
    if marker not in text:
        raise AssertionError(f"{WORKFLOW} lost its {marker!r} step")
    block = re.search(r"\$tests = @\((.*?)^\s*\)", text[text.index(marker):], re.MULTILINE | re.DOTALL)
    if block is None:
        raise AssertionError(f"{marker!r} no longer declares a $tests = @( ... ) list")
    return re.findall(r"'([^']+)'", block.group(1))


class CiProductSubsetTests(unittest.TestCase):
    """Keep the offline product subset canonical so appends stay idempotent."""

    def test_offline_modules_are_in_the_subset(self) -> None:
        modules = python_product_subset()
        self.assertEqual(
            [module for module in OFFLINE_MODULES if module not in modules], [],
            "dev-0039 offline modules must stay in the CI product subset",
        )

    def test_read_dedupe_union_alphabetical_write_back_is_idempotent(self) -> None:
        modules = python_product_subset()
        self.assertEqual(len(modules), len(set(modules)), "the CI subset must not repeat a module")
        self.assertEqual(modules, sorted(modules), "the CI subset must be written back in alphabetical order")
        self.assertEqual(modules, sorted(set(modules) | set(OFFLINE_MODULES)),
                         "re-applying the dev-0039 append rule must be a no-op")

    def test_every_listed_module_is_a_real_test_file(self) -> None:
        for module in python_product_subset():
            path = ROOT / (module.replace(".", "/") + ".py")
            self.assertTrue(path.is_file(), f"{module} is listed in CI but {path} does not exist")

    def test_product_core_step_still_runs_the_declared_list(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        marker = "Test Python product core"
        step = text[text.index(marker):]
        self.assertIn("python.exe -m unittest @tests -v", step)


if __name__ == "__main__":
    unittest.main()
