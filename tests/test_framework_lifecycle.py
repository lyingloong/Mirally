"""Current-host ownership, worker defaults and portable launcher contracts."""
from __future__ import annotations

from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import yaml

from mirally.framework.service import FrameworkError, FrameworkService
from mirally.framework_workers import WorkerSupervisor
from mirally.framework_host import product_home, discover_node


class RetiredHostOperationsTests(unittest.TestCase):
    def test_old_host_files_are_not_loaded_or_exposed(self):
        with tempfile.TemporaryDirectory(prefix="mirally retired host ") as directory:
            root = Path(directory)
            home = root / "home"
            framework = home / "framework"
            framework.mkdir(parents=True)
            retained = {
                framework / "migration-manifest.json": "private old manifest",
                framework / "legacy-history.json": "private old history index",
                root / "private-record.jsonl": "private old transcript",
            }
            for path, contents in retained.items():
                path.write_text(contents, encoding="utf-8")
            with FrameworkService(root, home) as service:
                for method in ("migration.status", "legacy.history", "legacy.history.read", "legacy.archive"):
                    with self.subTest(method=method), self.assertRaises(FrameworkError) as raised:
                        service.call(method)
                    self.assertEqual(raised.exception.code, "METHOD_NOT_FOUND")
            for path, contents in retained.items():
                self.assertEqual(path.read_text(encoding="utf-8"), contents)


class WorkerAndLauncherTests(unittest.TestCase):
    def test_profiles_are_owned_and_never_select_machine_legacy_defaults(self):
        with tempfile.TemporaryDirectory(prefix="mirally paths 中文 ") as directory:
            home = Path(directory) / "product home"
            supervisor = WorkerSupervisor(Path(directory), home)
            try:
                activity, memory = supervisor._profiles({"memory_enabled": True})
                a = yaml.safe_load(activity.read_text(encoding="utf-8"))
                m = yaml.safe_load(memory.read_text(encoding="utf-8"))
                self.assertFalse(a["enabled"])
                self.assertFalse(m["semantics"]["enabled"])
                self.assertEqual(Path(m["database_path"]), (home / "memory/memory.sqlite3").resolve())
                self.assertEqual(Path(m["projection_database_path"]), (home / "memory/activity-projection.sqlite3").resolve())
                self.assertNotEqual(m["projection_database_path"], a["storage"]["knowledge_database_path"])
                supervisor.update({"memory_enabled": False, "activity_enabled": False})
                deadline = time.monotonic() + 5
                while supervisor.status()["settings_pending"] and time.monotonic() < deadline: time.sleep(.02)
                self.assertEqual(supervisor.status()["activity"]["state"], "disabled")
                self.assertIsNone(supervisor._activity)
            finally:
                supervisor.close()

    def test_real_memory_owner_can_start_and_stop_without_model_or_capture(self):
        with tempfile.TemporaryDirectory(prefix="mirally owner ") as directory:
            home = Path(directory) / "home"
            supervisor = WorkerSupervisor(Path(directory), home)
            try:
                supervisor.update({"memory_enabled": True, "activity_enabled": False, "background_enabled": False})
                deadline = time.monotonic() + 10
                while (supervisor.status()["settings_pending"] or not (home / "memory/memory.sqlite3").is_file()) and time.monotonic() < deadline: time.sleep(.03)
                self.assertFalse(supervisor.status()["settings_pending"], supervisor.status())
                self.assertTrue((home / "memory/memory.sqlite3").is_file())
                self.assertIsNone(supervisor._activity)
            finally:
                supervisor.close()

    def test_home_override_is_portable(self):
        with tempfile.TemporaryDirectory(prefix="mirally 中文 ") as directory:
            with patch.dict("os.environ", {"MIRALLY_HOME": str(Path(directory) / "home")}) :
                self.assertEqual(product_home(), (Path(directory) / "home").resolve())

    def test_explicit_missing_node_has_actionable_error(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(RuntimeError, "does not exist"):
                discover_node(Path(directory), str(Path(directory) / "missing.exe"))


if __name__ == "__main__":
    unittest.main()
