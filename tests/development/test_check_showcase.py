"""Tests for tools/check_showcase.py (offline, standard library only)."""
from __future__ import annotations

import importlib.util
import shutil
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("check_showcase", ROOT / "tools" / "check_showcase.py")
check_showcase = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(check_showcase)

INDEX = """<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Fixture</title>
  </head>
  <body><div id="root"></div></body>
</html>
"""
APP = 'export function App() {\n  return <h1>Fixture</h1>;\n}\n'
STYLE = "@media (prefers-reduced-motion: reduce) {\n  * { animation: none; }\n}\n"
CREDITS = "- Lucide: ISC, https://lucide.dev/license\n"


class ShowcaseAuditTests(unittest.TestCase):
    def fixture(self, *, index: str = INDEX, app: str = APP, style: str = STYLE) -> Path:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        files = {
            "showcase/index.html": index,
            "showcase/src/App.jsx": app,
            "showcase/src/style.css": style,
            "showcase/public/CREDITS.md": CREDITS,
        }
        for name, text in files.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        return root

    def audit(self, root: Path, *, strict: bool = False) -> dict:
        return check_showcase.audit(root, strict=strict)

    def test_clean_fixture_passes(self) -> None:
        result = self.audit(self.fixture())
        self.assertEqual(result["problems"], [])
        self.assertEqual(result["summary"]["images"], 0)
        self.assertEqual(result["summary"]["h1"], 1)
        self.assertEqual(result["summary"]["license_links"], 1)
        self.assertGreaterEqual(result["summary"]["reduced_motion"], 1)

    def test_missing_document_metadata_is_reported(self) -> None:
        index = "<!doctype html>\n<html>\n<head><title></title></head>\n<body></body>\n</html>\n"
        result = self.audit(self.fixture(index=index))
        joined = "\n".join(result["problems"])
        self.assertIn("lang", joined)
        self.assertIn("<title>", joined)
        self.assertIn("viewport", joined)

    def test_exactly_one_h1_is_required(self) -> None:
        app = "export function App() {\n  return <><h1>A</h1><h1>B</h1></>;\n}\n"
        result = self.audit(self.fixture(app=app))
        self.assertTrue(any("exactly one <h1>" in problem for problem in result["problems"]), result["problems"])

    def test_each_image_needs_alt_and_decorative_markup_is_allowed(self) -> None:
        app = (
            'export function App() {\n'
            '  return <>\n'
            '    <h1>A</h1>\n'
            '    <img src="./a.png" />\n'
            '    <img src="./b.png" alt="描述" />\n'
            '    <img src="./c.png" alt="" aria-hidden="true" />\n'
            '    <img src="./d.png" alt="" role="presentation" />\n'
            '  </>;\n'
            '}\n'
        )
        result = self.audit(self.fixture(app=app))
        self.assertEqual(result["summary"]["images"], 4)
        self.assertEqual(len(result["problems"]), 1, result["problems"])
        self.assertIn("missing an alt attribute", result["problems"][0])

    def test_decorative_alt_without_aria_is_advisory_unless_strict(self) -> None:
        app = 'export function App() {\n  return <><h1>A</h1><img src="./logo.png" alt="" /></>;\n}\n'
        root = self.fixture(app=app)
        standard = self.audit(root)
        self.assertEqual(standard["problems"], [])
        self.assertEqual(len(standard["notes"]), 1)
        strict = self.audit(root, strict=True)
        self.assertEqual(len(strict["problems"]), 1, strict["problems"])
        self.assertIn("aria-hidden", strict["problems"][0])

    def test_external_reference_is_rejected_but_license_files_are_exempt(self) -> None:
        app = 'export function App() {\n  return <><h1>A</h1><img src="https://cdn.example.com/a.png" alt="A" /></>;\n}\n'
        result = self.audit(self.fixture(app=app))
        self.assertTrue(any("external reference https://cdn.example.com/a.png" in problem for problem in result["problems"]), result["problems"])
        self.assertGreaterEqual(result["summary"]["license_links"], 1)
        self.assertFalse(any("CREDITS.md" in problem for problem in result["problems"]))

    def test_missing_reduced_motion_is_reported(self) -> None:
        result = self.audit(self.fixture(style="body { color: #111; }\n"))
        self.assertTrue(any("prefers-reduced-motion" in problem for problem in result["problems"]), result["problems"])

    def test_export_html_is_checked_for_offline_self_containment(self) -> None:
        root = self.fixture()
        export = root / "showcase" / "exports" / "Mirally.html"
        export.parent.mkdir(parents=True)
        export.write_text(
            '<!doctype html><html><head><script src="https://cdn.example.com/app.js"></script></head>\n'
            '<body><img src="./images/leftover.webp" alt="" aria-hidden="true" />\n'
            "<script>var broken = ;</script></body></html>\n",
            encoding="utf-8",
        )
        result = self.audit(root)
        joined = "\n".join(result["problems"])
        self.assertEqual(result["summary"]["exports"], 1)
        self.assertIn("external src/href", joined)
        self.assertIn("residual relative image path", joined)
        if shutil.which("node"):
            self.assertIn("node --check", joined)
        else:
            self.assertTrue(any("node is unavailable" in note for note in result["notes"]))

    def test_repository_baseline_is_clean(self) -> None:
        result = check_showcase.audit(ROOT)
        self.assertEqual(result["problems"], [])
        self.assertGreaterEqual(result["summary"]["files_scanned"], 15)
        self.assertGreaterEqual(result["summary"]["images"], 11)
        self.assertEqual(result["summary"]["h1"], 1)
        self.assertGreaterEqual(result["summary"]["reduced_motion"], 1)
        self.assertEqual(result["summary"]["exports"], 0)


if __name__ == "__main__":
    unittest.main()
