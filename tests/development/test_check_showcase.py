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
            "website/index.html": index,
            "website/src/App.jsx": app,
            "website/src/style.css": style,
            "website/public/CREDITS.md": CREDITS,
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

    def test_prefixed_attribute_names_do_not_satisfy_alt(self) -> None:
        # ``data-alt``/``aria-label`` are not ``alt``; a regression here would
        # silently pass images that screen readers cannot describe.
        app = (
            'export function App() {\n'
            '  return <>\n'
            '    <h1>A</h1>\n'
            '    <img src="./a.png" data-alt="description" />\n'
            '    <img src="./b.png" aria-label="description" />\n'
            '    <img src="./c.png" alt="ok" />\n'
            '  </>;\n'
            '}\n'
        )
        result = self.audit(self.fixture(app=app))
        self.assertEqual(result["summary"]["images"], 3)
        self.assertEqual(len(result["problems"]), 2, result["problems"])
        self.assertTrue(all("missing an alt attribute" in problem for problem in result["problems"]))
        self.assertIn("App.jsx:4", result["problems"][0])

    def test_alt_attribute_name_is_matched_whole_and_case_insensitively(self) -> None:
        app = (
            'export function App() {\n'
            '  return <>\n'
            '    <h1>A</h1>\n'
            '    <img src="./a.png" ALT="" aria-hidden="true" />\n'
            '    <img src="./b.png" Alt={label} />\n'
            '  </>;\n'
            '}\n'
        )
        result = self.audit(self.fixture(app=app))
        self.assertEqual(result["problems"], [])
        self.assertEqual(result["notes"], [])

    def test_dynamic_decorative_attributes_do_not_crash_strip(self) -> None:
        # JSX ``{expression}`` attributes are parsed as None; the decorative
        # check must degrade to the advisory instead of calling None.strip().
        app = (
            'export function App() {\n'
            '  return <>\n'
            '    <h1>A</h1>\n'
            '    <img src="./a.png" alt="" aria-hidden={isHidden} role={role} />\n'
            '  </>;\n'
            '}\n'
        )
        result = self.audit(self.fixture(app=app))
        self.assertEqual(result["problems"], [])
        self.assertEqual(len(result["notes"]), 1, result["notes"])
        self.assertIn("aria-hidden", result["notes"][0])

    def test_external_reference_is_rejected_but_license_files_are_exempt(self) -> None:
        app = 'export function App() {\n  return <><h1>A</h1><img src="https://cdn.example.com/a.png" alt="A" /></>;\n}\n'
        result = self.audit(self.fixture(app=app))
        self.assertTrue(any("external reference https://cdn.example.com/a.png" in problem for problem in result["problems"]), result["problems"])
        self.assertGreaterEqual(result["summary"]["license_links"], 1)
        self.assertFalse(any("CREDITS.md" in problem for problem in result["problems"]))

    def test_navigation_allowlist_accepts_anchor_links(self) -> None:
        app = (
            'export function App() {\n'
            '  return <><h1>A</h1><a href="https://lyingloong.github.io/">home</a></>;\n'
            '}\n'
        )
        result = self.audit(self.fixture(app=app))
        self.assertEqual(result["problems"], [], result["problems"])

    def test_navigation_allowlist_does_not_exempt_image_resource(self) -> None:
        # The same URL text as a page resource is still an external reference.
        app = (
            'export function App() {\n'
            '  return <><h1>A</h1><img src="https://lyingloong.github.io/" alt="remote" /></>;\n'
            '}\n'
        )
        result = self.audit(self.fixture(app=app))
        self.assertTrue(any("external reference https://lyingloong.github.io/" in problem
                            for problem in result["problems"]), result["problems"])

    def test_navigation_allowlist_does_not_exempt_script_resource(self) -> None:
        app = (
            'export function App() {\n'
            '  return <><h1>A</h1><script src="https://github.com/lyingloong/Mirally"></script></>;\n'
            '}\n'
        )
        result = self.audit(self.fixture(app=app))
        self.assertTrue(any("external reference https://github.com/lyingloong/Mirally" in problem
                            for problem in result["problems"]), result["problems"])

    def test_navigation_constant_is_exempt_only_as_an_anchor_target(self) -> None:
        navigation = (
            'const homepage = "https://lyingloong.github.io/";\n'
            'export function App() {\n  return <><h1>A</h1><a href={homepage}>home</a></>;\n}\n'
        )
        result = self.audit(self.fixture(app=navigation))
        self.assertEqual(result["problems"], [], result["problems"])

        asset = (
            'const homepage = "https://lyingloong.github.io/";\n'
            'export function App() {\n  return <><h1>A</h1><img src={homepage} alt="remote" /></>;\n}\n'
        )
        result = self.audit(self.fixture(app=asset))
        self.assertTrue(any("external reference https://lyingloong.github.io/" in problem
                            for problem in result["problems"]), result["problems"])

    def test_navigation_alias_chain_is_proven_not_assumed(self) -> None:
        navigation = (
            'const repository = "https://github.com/lyingloong/Mirally";\n'
            'const installHref = `${repository}/blob/main/README.md`;\n'
            'export function App() {\n  return <><h1>A</h1><a href={installHref}>install</a></>;\n}\n'
        )
        result = self.audit(self.fixture(app=navigation))
        self.assertEqual(result["problems"], [], result["problems"])

        asset = (
            'const repository = "https://github.com/lyingloong/Mirally";\n'
            'const installHref = `${repository}/blob/main/README.md`;\n'
            'export function App() {\n  return <><h1>A</h1><img src={installHref} alt="remote" /></>;\n}\n'
        )
        result = self.audit(self.fixture(app=asset))
        self.assertTrue(any("external reference https://github.com/lyingloong/Mirally" in problem
                            for problem in result["problems"]), result["problems"])

    def test_missing_reduced_motion_is_reported(self) -> None:
        result = self.audit(self.fixture(style="body { color: #111; }\n"))
        self.assertTrue(any("prefers-reduced-motion" in problem for problem in result["problems"]), result["problems"])

    def test_export_html_is_checked_for_offline_self_containment(self) -> None:
        root = self.fixture()
        export = root / "website" / "exports" / "Mirally.html"
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

    def test_export_missing_local_script_or_stylesheet_is_reported(self) -> None:
        # An offline page that points at missing local files parses fine but
        # cannot render or run, so presence must be verified, not assumed.
        root = self.fixture()
        export = root / "website" / "exports" / "Mirally.html"
        export.parent.mkdir(parents=True)
        export.write_text(
            '<!doctype html><html><head><link rel="stylesheet" href="missing.css"></head>\n'
            '<body><script src="missing.js"></script></body></html>\n',
            encoding="utf-8",
        )
        result = self.audit(root)
        joined = "\n".join(result["problems"])
        self.assertIn("missing local script missing.js", joined)
        self.assertIn("missing local stylesheet missing.css", joined)

    def test_export_local_resources_that_exist_are_accepted(self) -> None:
        root = self.fixture()
        exports = root / "website" / "exports"
        (exports / "assets").mkdir(parents=True)
        (exports / "assets" / "app.js").write_text('var ok = 1;\n', encoding="utf-8")
        (exports / "assets" / "style.css").write_text("body { color: #111; }\n", encoding="utf-8")
        (exports / "Mirally.html").write_text(
            '<!doctype html><html><head>'
            '<link rel="stylesheet" href="./assets/style.css">'
            '<link rel="icon" href="missing-icon.png">'
            '<link rel="stylesheet" href="data:text/css,body%7B%7D">'
            '</head>\n<body><script src="./assets/app.js"></script></body></html>\n',
            encoding="utf-8",
        )
        result = self.audit(root)
        self.assertEqual(result["summary"]["exports"], 1)
        self.assertEqual(result["problems"], [], result["problems"])

    def test_export_non_relative_resource_reference_is_reported(self) -> None:
        root = self.fixture()
        export = root / "website" / "exports" / "Mirally.html"
        export.parent.mkdir(parents=True)
        export.write_text(
            '<!doctype html><html><head></head>\n'
            '<body><script src="/assets/app.js"></script></body></html>\n',
            encoding="utf-8",
        )
        result = self.audit(root)
        self.assertTrue(
            any("non-relative script /assets/app.js" in problem for problem in result["problems"]),
            result["problems"],
        )

    def test_export_protocol_relative_script_and_stylesheet_are_external(self) -> None:
        # ``//cdn…`` loads from the network; it is not evidence of inlining.
        root = self.fixture()
        export = root / "website" / "exports" / "Mirally.html"
        export.parent.mkdir(parents=True)
        export.write_text(
            '<!doctype html><html><head>'
            '<link rel="stylesheet" href="//cdn.example.com/app.css">'
            '</head>\n<body><script src="//cdn.example.com/app.js"></script></body></html>\n',
            encoding="utf-8",
        )
        result = self.audit(root)
        joined = "\n".join(result["problems"])
        self.assertIn("external protocol-relative script //cdn.example.com/app.js", joined)
        self.assertIn("external protocol-relative stylesheet //cdn.example.com/app.css", joined)
        self.assertEqual(result["summary"]["exports"], 1)

    def test_repository_baseline_is_clean(self) -> None:
        result = check_showcase.audit(ROOT)
        self.assertEqual(result["problems"], [])
        self.assertGreaterEqual(result["summary"]["files_scanned"], 9)
        self.assertGreaterEqual(result["summary"]["images"], 6)
        self.assertEqual(result["summary"]["h1"], 1)
        self.assertGreaterEqual(result["summary"]["reduced_motion"], 1)
        self.assertEqual(result["summary"]["exports"], 0)


if __name__ == "__main__":
    unittest.main()
