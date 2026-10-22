"""Assistant workspace paths must not silently inherit coding instructions."""

from contextlib import redirect_stdout
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from mirally.runtime.workspace import resolve_workspace
from tools import configure_openclaw


class WorkspaceTests(unittest.TestCase):
    def test_default_uses_user_data_without_creating_it(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            root = base / "项目 with spaces"
            with patch.dict(os.environ, {"LOCALAPPDATA": str(base / "user data")}):
                result = resolve_workspace(root)
            self.assertEqual(result, base / "user data/Mirally/openclaw/workspace")
            self.assertFalse(result.exists())

    def test_default_falls_back_to_home(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            with patch.dict(os.environ, {"LOCALAPPDATA": ""}), patch.object(Path, "home", return_value=base / "home"):
                result = resolve_workspace(base / "repo")
            self.assertEqual(result, base / "home/.local/share/mirally/openclaw/workspace")

    def test_default_rejects_product_directory_and_git_ancestry(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            for git_marker in (None, "file", "directory"):
                with self.subTest(git_marker=git_marker):
                    checkout = base / str(git_marker)
                    checkout.mkdir()
                    if git_marker == "file":
                        (checkout / ".git").write_text("gitdir: elsewhere", encoding="utf-8")
                    elif git_marker == "directory":
                        (checkout / ".git").mkdir()
                    root = checkout if git_marker is None else base / "product"
                    with patch.dict(os.environ, {"LOCALAPPDATA": str(checkout / "data")}), self.assertRaisesRegex(ValueError, "--workspace"):
                        resolve_workspace(root)

    def test_explicit_paths_expand_and_ignore_the_callers_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            root = base / "项目"
            root.mkdir()
            (root / ".git").mkdir()
            with patch.dict(os.environ, {"MIRALLY_TEST_WORKSPACE": str(base / "external")}), \
                    patch.object(Path, "home", return_value=base / "home"):
                self.assertEqual(resolve_workspace(root, "relative workspace"), root / "relative workspace")
                self.assertEqual(resolve_workspace(root, "${MIRALLY_TEST_WORKSPACE}/中文"), base / "external/中文")
                # Explicit choices may be coding workspaces.
                self.assertEqual(resolve_workspace(root, root), root)
            with patch.dict(os.environ, {"USERPROFILE": str(base / "home"), "HOME": str(base / "home")}):
                self.assertEqual(resolve_workspace(root, "~/assistant"), base / "home/assistant")

    def test_existing_configuration_does_not_resolve_or_migrate_workspace(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / "config/mirally/openclaw/openclaw.json"
            config.parent.mkdir(parents=True)
            config.write_bytes(b'{"private": "preserve exact bytes"}')
            before = config.read_bytes()
            with patch.object(configure_openclaw, "ROOT", root), \
                    patch.object(configure_openclaw, "resolve_workspace") as resolve, \
                    patch.object(sys, "argv", ["configure_openclaw.py", "--workspace", "new location"]), \
                    redirect_stdout(io.StringIO()):
                configure_openclaw.main()
            resolve.assert_not_called()
            self.assertEqual(config.read_bytes(), before)

    def test_authored_identity_is_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary) / "workspace"
            workspace.mkdir()
            identity = workspace / "IDENTITY.md"
            identity.write_bytes(b"# My authored identity\n")
            with redirect_stdout(io.StringIO()):
                self.assertFalse(configure_openclaw.seed_identity(workspace))
            self.assertEqual(identity.read_bytes(), b"# My authored identity\n")
            self.assertFalse((workspace / "avatars").exists())

    def test_direct_script_imports_from_external_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            env = dict(os.environ)
            env.pop("PYTHONPATH", None)
            completed = subprocess.run([sys.executable, str(Path(configure_openclaw.__file__).resolve()), "--help"],
                cwd=temporary, env=env, capture_output=True, text=True, encoding="utf-8", timeout=20,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn("--workspace", completed.stdout)


if __name__ == "__main__":
    unittest.main()
