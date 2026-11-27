"""Regression protection for the workflow's public-text sanitizer.

The workflow publishes requirement, implementation and review records to GitHub:
PR titles and bodies, the stored merge brief and requirement issues. They must not
carry machine-specific absolute paths, the local account name or credentials. The
rules live in ``docs/examples/development/public-text.mjs``, so the cases run in a
Node probe driven from here; that puts them in the same CI job that already covers
this workflow's tooling (``python -m unittest discover -s tests/development``).
"""

from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOLING = ROOT / "docs" / "examples" / "development"
PROBE = Path(__file__).with_name("public_text_probe.mjs")


@unittest.skipUnless(shutil.which("node"), "node is required to exercise the public-text sanitizer")
class PublicTextTests(unittest.TestCase):
    def run_probe(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["node", str(PROBE)],
            cwd=str(ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

    def test_probe_covers_the_shared_sanitizer(self):
        self.assertTrue(PROBE.is_file(), f"missing probe: {PROBE}")
        result = self.run_probe()
        self.assertEqual(
            result.returncode, 0,
            msg=f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )
        self.assertIn("public-text probe OK", result.stdout)

    def test_publication_modules_use_the_shared_sanitizer(self):
        # A re-inlined regex would quietly narrow the rules again. pr.mjs and
        # worktrees.mjs scrub PR-visible records through redactForPublic, so the
        # modules that must import the shared module directly are the ones that
        # own those rules: requirement-review.mjs (redactForPublic itself) and
        # brief-repair.mjs (the stored-brief scrub, which calls publicText
        # directly).
        for name in ("brief-repair.mjs", "requirement-review.mjs"):
            source = (TOOLING / name).read_text(encoding="utf-8")
            self.assertIn("./public-text.mjs", source, f"{name} no longer imports the shared public-text module")
        source = (TOOLING / "requirement-review.mjs").read_text(encoding="utf-8")
        self.assertIn("publicText(String(value ?? '')", source,
                      "redactForPublic no longer delegates to the shared public-text sanitizer")

    def test_brief_appends_records_through_the_sanitizer(self):
        # The stored brief markdown is republished verbatim, so every record must
        # be scrubbed where it is appended. redactForPublic is the shared rule
        # entry point (previous test); a re-inlined raw append would leak again.
        source = (TOOLING / "worktrees.mjs").read_text(encoding="utf-8")
        for field in ("artifact.summary", "artifact.verification", "review.summary", "review.verification"):
            self.assertIn(f"redactForPublic({field})", source, f"merge brief appends {field} unsanitized")

    def test_stored_brief_scrub_is_a_driver_stage(self):
        # A brief stored before the sanitizer exists must be cleanable in place:
        # publishers may run older code than the sanitizer until this branch is
        # merged, so the stored record — not a one-off PR body edit — is durable.
        driver = (TOOLING / "driver.mjs").read_text(encoding="utf-8")
        self.assertIn("'scrub-brief'", driver)
        source = (TOOLING / "brief-repair.mjs").read_text(encoding="utf-8")
        self.assertIn("export function scrubStoredBrief", source)
        self.assertIn("export async function scrubBrief", source)
        scrubber = source.split("export function scrubStoredBrief", 1)[1].split("export async function scrubBrief", 1)[0]
        # Only prose is rewritten; store references and commits must survive.
        for kept in ("artifactRef", "commit", "url"):
            self.assertNotIn(kept, scrubber, f"stored-brief scrub rewrites {kept}")
        for field in ("'summary'", "'verification'", "'reviewer'", "markdown", "body"):
            self.assertIn(field, scrubber, f"stored-brief scrub misses {field}")


if __name__ == "__main__":
    unittest.main()
