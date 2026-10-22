"""Offline migration, worker defaults and portable launcher contracts."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch

import yaml

from mirally.framework.migration import MigrationPlan, MigrationError, rollback
from mirally.framework.service import FrameworkService
from mirally.framework_workers import WorkerSupervisor
from mirally.framework_host import product_home, discover_node


class Lock:
    def __init__(self, **_): pass
    def acquire(self): return True
    def release(self): pass


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mirally 迁移 ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / "新的 home"
        self.source = self.root / "legacy"
        self.source.mkdir()
        self.db = self.source / "memory.sqlite3"
        self.writer = sqlite3.connect(self.db)
        self.writer.execute("PRAGMA journal_mode=WAL")
        self.writer.execute("CREATE TABLE evidence(id INTEGER PRIMARY KEY, content TEXT)")
        self.writer.execute("INSERT INTO evidence(content) VALUES('committed WAL')")
        self.writer.commit()
        self.addCleanup(self.writer.close)
        self.profile = self.source / "memory.yaml"
        self.profile.write_text(yaml.safe_dump({"database_path": str(self.db), "projection_database_path": str(self.source / "projection.sqlite3")}), encoding="utf-8")

    def plan(self, **kwargs):
        return MigrationPlan(self.root, self.home, memory_profile=self.profile, **kwargs)

    def test_preview_is_readonly_and_apply_includes_wal(self):
        plan = self.plan()
        before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in plan.source_files}
        self.assertFalse(plan.preview()["writes"])
        self.assertFalse(self.home.exists())
        result = plan.apply(confirm_stopped=True, lock_factory=Lock)
        self.assertTrue(result["owner_locks_checked"])
        with sqlite3.connect(self.home / "memory/memory.sqlite3") as connection:
            self.assertEqual(connection.execute("SELECT content FROM evidence").fetchone()[0], "committed WAL")
        self.assertEqual(before, {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in plan.source_files})
        with FrameworkService(self.root, self.home) as service:
            self.assertTrue(service.call("migration.status")["migrated"])
            self.assertFalse(service.settings_get()["activity_enabled"])

    def test_requires_stop_and_never_overwrites(self):
        with self.assertRaises(MigrationError): self.plan().apply()
        self.home.mkdir()
        (self.home / "keep.txt").write_text("keep")
        with self.assertRaises(MigrationError): self.plan().preview()
        self.assertEqual((self.home / "keep.txt").read_text(), "keep")

    def test_source_change_refuses_publish(self):
        plan = self.plan()
        self.profile.write_text(self.profile.read_text() + "\n# changed\n")
        with self.assertRaises(MigrationError): plan.apply(confirm_stopped=True, lock_factory=Lock)
        self.assertFalse(self.home.exists())

    def test_new_asset_after_preview_refuses_publish(self):
        assets = self.source / "assets"
        assets.mkdir()
        (assets / "old.bin").write_bytes(b"old")
        activity = self.source / "activity.yaml"
        activity.write_text(yaml.safe_dump({"storage": {"directory": str(self.source), "database_path": str(self.db)}}), encoding="utf-8")
        plan = self.plan(activity_profile=activity)
        (assets / "new.bin").write_bytes(b"new")
        with self.assertRaises(MigrationError): plan.apply(confirm_stopped=True, lock_factory=Lock)
        self.assertFalse(self.home.exists())

    def test_failure_cleans_only_owned_stage(self):
        keep = self.root / ".mirally-migration-unrelated"
        keep.mkdir()
        with patch("mirally.framework.migration._backup", side_effect=OSError("fixture")):
            with self.assertRaises(OSError): self.plan().apply(confirm_stopped=True, lock_factory=Lock)
        self.assertEqual(list(self.root.glob(".mirally-migration-*")), [keep])
        self.assertFalse(self.home.exists())

    def test_model_preferences_legacy_index_and_rollback(self):
        config = self.source / "openclaw.json"
        config.write_text(json.dumps({"agents": {"defaults": {"model": {"primary": "fixture/model"}}}, "models": {"providers": {"fixture": {"baseUrl": "http://127.0.0.1:1/v1", "apiKey": "local-fixture-key", "models": [{"id": "model", "input": ["text", "image"]}]}}}}))
        preferences = self.source / "preferences.json"
        preferences.write_text('[{"key":"tone","value":"direct"}]')
        history = self.source / "sessions"
        history.mkdir()
        (history / "old.jsonl").write_text('{"text":"original"}\n')
        plan = self.plan(openclaw_config=config, preferences_json=preferences, legacy_history=history)
        self.assertNotIn("local-fixture-key", json.dumps(plan.preview()))
        plan.apply(confirm_stopped=True, lock_factory=Lock)
        with FrameworkService(self.root, self.home) as service:
            self.assertTrue(service.settings_get()["model"]["image_enabled"])
            self.assertEqual(service.preference_list()["preferences"][0]["value"], "direct")
            self.assertEqual(len(service.call("legacy.history")["sessions"]), 1)
        self.assertTrue(rollback(self.home, self.root)["new_home_preserved"])
        self.assertTrue(self.db.is_file())
        self.assertTrue((self.home / "memory/memory.sqlite3").is_file())
        self.assertEqual((history / "old.jsonl").read_text(), '{"text":"original"}\n')


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
