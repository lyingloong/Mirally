"""Owned activity controls and read-only memory/history regressions."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.request

import yaml

from mirally.framework import FrameworkError, FrameworkService
from mirally.framework.activity_experience import configured_activity
from mirally.framework_workers import WorkerSupervisor
from mirally.runtime.activity.config import load_activity_config
from mirally.core.activity.store import ObservationStore
from mirally.core.memory.store import MemoryStore


class Controller:
    def __init__(self): self.updates = []
    def update(self, settings): self.updates.append(settings)
    def status(self): return {"activity": {"state": "disabled"}, "settings_pending": False}
    def close(self): pass


class ActivityExperienceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="Mirally 活动 ")
        self.root = Path(self.temporary.name).resolve()
        self.home = self.root / "owned home"
        self.controller = Controller()
        self.product = FrameworkService(self.root, self.home, controller=self.controller)
        self.addCleanup(self.temporary.cleanup)
        self.addCleanup(self.product.close)

    def rpc(self, method, **params): return self.product.call(method, params)

    def test_start_requires_current_scope_and_only_updates_existing_owner(self):
        status = self.rpc("activity.status")
        self.assertFalse(status["enabled"])
        self.assertFalse((self.home / "data/activity/observations.sqlite3").exists())
        with self.assertRaises(FrameworkError): self.rpc("activity.start", confirmed=False, expected_fingerprint=status["fingerprint"])
        self.rpc("activity.configure", patch={"visual_scope": "foreground", "clipboard_enabled": False})
        with self.assertRaises(FrameworkError): self.rpc("activity.start", confirmed=True, expected_fingerprint=status["fingerprint"])
        status = self.rpc("activity.status")
        requested = self.rpc("activity.start", confirmed=True, expected_fingerprint=status["fingerprint"])
        self.assertTrue(requested["enabled"])
        self.assertTrue(self.controller.updates[-1]["activity_enabled"])
        self.assertFalse((self.home / "data/activity/observations.sqlite3").exists())

    def test_scope_changes_stop_capture_and_pause_retains_enabled_preference(self):
        status = self.rpc("activity.status")
        self.rpc("activity.start", confirmed=True, expected_fingerprint=status["fingerprint"])
        self.rpc("activity.pause")
        self.assertTrue(self.rpc("settings.get")["activity_enabled"])
        self.assertTrue(self.rpc("settings.get")["activity_paused"])
        self.rpc("activity.resume", confirmed=True, expected_fingerprint=status["fingerprint"])
        self.assertFalse(self.rpc("settings.get")["activity_paused"])
        changed = self.rpc("activity.configure", patch={"capture_level": "metadata"})
        self.assertFalse(changed["enabled"])
        self.assertTrue(changed["requires_confirmation"])
        self.rpc("activity.stop")
        self.assertFalse(self.rpc("settings.get")["activity_enabled"])

    def test_pause_releases_the_single_supervised_collector_before_resume(self):
        from tests.test_framework_workers import FakeProcess, wait_for
        processes = []
        def launch(*args, **kwargs):
            process = FakeProcess(*args, **kwargs)
            processes.append(process)
            return process
        with patch("mirally.framework_workers.subprocess.Popen", side_effect=launch), patch("mirally.framework_workers.sys.platform", "win32"):
            supervisor = WorkerSupervisor(self.root, self.home)
            try:
                settings = {"activity_enabled": True, "memory_enabled": False, "background_enabled": False}
                supervisor.update(settings)
                wait_for(lambda: len(processes) == 1 and not supervisor.status()["settings_pending"])
                supervisor.update({**settings, "activity_paused": True})
                wait_for(lambda: supervisor.status()["activity"]["state"] == "paused" and not supervisor.status()["settings_pending"])
                self.assertEqual(processes[0].returncode, 0)
                self.assertEqual(len(processes), 1)
                supervisor.update({**settings, "activity_paused": False})
                wait_for(lambda: len(processes) == 2 and not supervisor.status()["settings_pending"])
                self.assertEqual(processes[0].wait_count, 1)
                self.assertIsNone(processes[1].returncode)
            finally:
                supervisor.close()

    def test_explicit_private_profile_is_read_only_and_owned_profile_uses_real_parser(self):
        source = self.root / "private profile.yaml"
        source.write_text("visual:\n  scope: monitors\nprivacy:\n  excluded_apps: ['password.exe']\n", encoding="utf-8")
        before = source.read_bytes()
        self.rpc("settings.update", patch={"activity_config_path": str(source)})
        self.rpc("activity.configure", patch={"visual_scope": "foreground", "exclude_window_titles": ["*private*"]})
        supervisor = WorkerSupervisor.__new__(WorkerSupervisor)
        supervisor.root, supervisor.home = self.root, self.home
        with patch.object(supervisor, "_source_blocked", return_value=False):
            activity, memory = supervisor._profiles(self.product.settings_internal())
        parsed = load_activity_config(self.root, activity)
        expected = configured_activity(self.root, self.home, self.product.settings_internal())
        self.assertEqual(parsed.visual_scope, expected.visual_scope)
        self.assertEqual(parsed.policy.excluded_windows, ("*private*",))
        self.assertEqual(parsed.policy.excluded_apps, ("password.exe",))
        self.assertEqual(source.read_bytes(), before)

    def test_budget_settings_survive_restart_and_reach_owned_memory_profile(self):
        self.rpc("memory.configure", patch={"max_calls_per_hour": 7, "max_tokens_per_day": 45000, "max_output_tokens": 2048})
        supervisor = WorkerSupervisor.__new__(WorkerSupervisor)
        supervisor.root, supervisor.home = self.root, self.home
        with patch.object(supervisor, "_profile", return_value={}):
            _, memory = supervisor._profiles(self.product.settings_internal())
        raw = yaml.safe_load(memory.read_text(encoding="utf-8"))
        self.assertEqual(raw["semantics"]["max_calls_per_hour"], 7)
        self.assertEqual(raw["semantics"]["max_tokens_per_day"], 45000)
        with FrameworkService(self.root, self.home) as reader:
            self.assertEqual(reader.settings_get()["memory_semantics"]["max_output_tokens"], 2048)
        with self.assertRaises(FrameworkError): self.rpc("memory.configure", patch={"max_calls_per_hour": -1})

    def test_history_server_opens_existing_data_readonly_without_projection_or_collector(self):
        database = self.home / "data/activity/observations.sqlite3"
        with ObservationStore(database) as store:
            store.set_state("collector_diagnostics", {"status": "stopped", "sources": {"input": {"enabled": False}}})
        before = hashlib.sha256(database.read_bytes()).hexdigest()
        with patch("mirally.runtime.activity.browser.webbrowser.open") as opener:
            result = self.rpc("activity.browse", mode="history")
            self.assertTrue(result["readonly"])
            self.assertFalse(result["collector_started"])
            with urllib.request.urlopen(result["url"], timeout=3) as response:
                self.assertEqual(response.status, 200)
            opener.assert_not_called()
        self.assertEqual(hashlib.sha256(database.read_bytes()).hexdigest(), before)
        self.assertFalse((database.parent / "knowledge.sqlite3").exists())
        self.assertEqual(self.rpc("activity.status")["snapshot"]["diagnostics"]["status"], "stopped")

    def test_memory_candidate_filters_and_paging_do_not_promote_or_write(self):
        database = self.home / "memory/memory.sqlite3"
        with MemoryStore(database) as store:
            active = store.put_memory({"id": "active", "kind": "episodic", "scope": "user/local", "status": "active", "provenance": "observed", "title": "Observed", "content": "observed source"})
            candidate = store.put_memory({"id": "candidate", "kind": "semantic", "scope": "user/local", "status": "active", "provenance": "model_inference", "title": "Candidate", "content": "\n".join(f"line {index}" for index in range(120))})
            self.assertEqual(candidate["status"], "candidate")
        before = hashlib.sha256(database.read_bytes()).hexdigest()
        regular = self.rpc("memory.search", query="", kinds=["episodic"], provenances=["observed"])
        self.assertEqual([item["id"] for item in regular["memories"]], [active["id"]])
        found = self.rpc("memory.search", query="", include_candidates=True, kinds=["semantic"], provenances=["model_inference"])
        self.assertEqual([item["id"] for item in found["memories"]], [candidate["id"]])
        page = self.rpc("memory.read", memory_id="candidate", include_candidates=True, from_line=51, line_count=10)
        self.assertEqual(page["text"].splitlines()[0], "line 50")
        self.assertEqual(page["line_count"], 10)
        self.assertTrue(page["truncated"])
        self.assertEqual(self.rpc("memory.read", memory_id="candidate")["status"], "not_found")
        self.assertEqual(hashlib.sha256(database.read_bytes()).hexdigest(), before)
        with self.assertRaises(FrameworkError): self.rpc("memory.search", kinds=["invalid"])

    def test_candidate_and_active_reads_respect_source_retraction(self):
        memory = self.rpc("memory.correct", content="private content")["memory"]
        self.rpc("source.set", source_id="memory:" + memory["id"], enabled=False)
        self.assertEqual(self.rpc("memory.search", query="private")["memories"], [])
        with self.assertRaises(FrameworkError): self.rpc("memory.read", memory_id=memory["id"], from_line=1, line_count=50)


if __name__ == "__main__": unittest.main()
