"""Worker lifecycle fences without desktop capture or a model HTTP server."""

from __future__ import annotations

import io
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import yaml

from mirally.framework_workers import WorkerSupervisor, _Completion
from mirally.runtime.activity.config import load_activity_config


def wait_for(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("worker condition did not settle before deadline")


class FakeProcess:
    sequence = 1000

    def __init__(self, command, **_kwargs):
        self.command = command
        self.stdin = io.BytesIO()
        self.returncode = None
        self.wait_count = 0
        self.wait_timeout = False
        type(self).sequence += 1
        self.pid = type(self).sequence

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self.wait_count += 1
        if self.wait_timeout:
            raise subprocess.TimeoutExpired(self.command, timeout)
        self.returncode = 0
        return 0


class FakeMemory:
    def __init__(self, _root, _config):
        self.calls = []
        self.closed = False
        self.claimed = False
        self.completed = []
        self.failed = []

    def start(self):
        self.calls.append("start")

    def handle(self, operation, args=None):
        if self.closed:
            raise AssertionError("closed Memory owner was called")
        self.calls.append(operation)
        if operation == "health":
            return {"maintenance_status": "running"}
        if operation == "claim_job":
            if self.claimed:
                return {"status": "idle", "job": None}
            self.claimed = True
            return {"job": {"job_id": "fixture-job", "lease_token": "fixture-lease", "payload": {
                "system_prompt": "fixture", "prompt": "fixture", "model": "fixture"}}}
        if operation == "fail_job":
            self.failed.append(args)
            return {"status": "failed"}
        if operation == "complete_job":
            self.completed.append(args)
            return {"status": "completed"}
        raise AssertionError(operation)

    def close(self):
        self.calls.append("close")
        self.closed = True


class FrameworkWorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mirally worker 中文 ")
        self.root = Path(self.temp.name).resolve()
        self.home = self.root / "home with spaces"
        self.owners = []
        self.processes = []
        self.release = threading.Event()
        self.started = threading.Event()
        self.tasks = []
        self.supervisor = None
        def memory(*args, **kwargs):
            owner = FakeMemory(*args, **kwargs)
            self.owners.append(owner)
            return owner
        def process(*args, **kwargs):
            instance = FakeProcess(*args, **kwargs)
            self.processes.append(instance)
            return instance
        self.memory_patch = patch("mirally.runtime.memory.service.MemoryService", side_effect=memory)
        self.process_patch = patch("mirally.framework_workers.subprocess.Popen", side_effect=process)
        self.platform_patch = patch("mirally.framework_workers.sys.platform", "win32")
        self.memory_patch.start()
        self.process_patch.start()
        self.platform_patch.start()

    def tearDown(self):
        self.release.set()
        for process in self.processes:
            process.wait_timeout = False
        if self.supervisor is not None:
            self.supervisor.close()
        for task in self.tasks:
            task.thread.join(2)
        self.platform_patch.stop()
        self.process_patch.stop()
        self.memory_patch.stop()
        self.temp.cleanup()

    def start(self, complete=None):
        self.supervisor = WorkerSupervisor(self.root, self.home)
        if complete is not None:
            self.supervisor._complete = complete
        return self.supervisor

    def blocking_complete(self, _job, task):
        self.tasks.append(task)
        self.started.set()
        # Deliberately ignore cancellation to model a transport callback which
        # returns late. The supervisor still must stop/reconfigure immediately.
        if not self.release.wait(5):
            raise AssertionError("fixture was not released")
        return {"text": "late fixture", "usage": {}, "model": "fixture", "provider": "fixture"}

    def test_generated_activity_profile_matches_real_loader_contract(self):
        supervisor = self.start()
        activity, _ = supervisor._profiles({"activity_enabled": False, "memory_enabled": False})
        config = load_activity_config(self.root, activity)
        self.assertEqual(config.database_path, self.home / "data/activity/observations.sqlite3")
        self.assertEqual(config.knowledge_database_path, self.home / "data/activity/knowledge.sqlite3")
        self.assertEqual(config.asset_directory, self.home / "data/activity/assets")
        self.assertFalse(self.processes)

    def test_activity_profile_change_stops_old_process_before_replacement(self):
        supervisor = self.start()
        settings = {"activity_enabled": True, "memory_enabled": False, "background_enabled": False}
        supervisor.update(settings)
        wait_for(lambda: len(self.processes) == 1 and not supervisor.status()["settings_pending"])
        old = self.processes[0]
        profile = self.root / "second profile.yaml"
        profile.write_text(yaml.safe_dump({"storage": {"directory": str(self.root / "second"),
            "database_path": str(self.root / "second/observations.sqlite3"),
            "knowledge_database_path": str(self.root / "second/knowledge.sqlite3")}}), encoding="utf-8")
        supervisor.update({**settings, "activity_config_path": str(profile)})
        wait_for(lambda: len(self.processes) == 2 and not supervisor.status()["settings_pending"])
        self.assertEqual(old.returncode, 0)
        self.assertEqual(old.wait_count, 1)
        config = load_activity_config(self.root, self.home / "config/activity.yaml")
        self.assertEqual(config.database_path, self.root / "second/observations.sqlite3")
        self.assertEqual(supervisor._activity.pid, self.processes[1].pid)

    def test_external_profile_edit_is_detected_without_a_ui_toggle(self):
        supervisor = self.start()
        profile = self.root / "source.yaml"
        profile.write_text("poll_interval_seconds: 1\n", encoding="utf-8")
        supervisor.update({"activity_enabled": True, "memory_enabled": False, "activity_config_path": str(profile)})
        wait_for(lambda: len(self.processes) == 1)
        profile.write_text("poll_interval_seconds: 2\n", encoding="utf-8")
        supervisor._wake.set()
        wait_for(lambda: len(self.processes) == 2)
        self.assertEqual(self.processes[0].returncode, 0)

    def test_unconfirmed_activity_stop_never_starts_second_writer(self):
        supervisor = self.start()
        settings = {"activity_enabled": True, "memory_enabled": False}
        supervisor.update(settings)
        wait_for(lambda: len(self.processes) == 1)
        old = self.processes[0]
        old.wait_timeout = True
        profile = self.root / "changed.yaml"
        profile.write_text("poll_interval_seconds: 2\n", encoding="utf-8")
        supervisor.update({**settings, "activity_config_path": str(profile)})
        wait_for(lambda: supervisor.status()["activity"]["state"] == "stop_unconfirmed")
        self.assertEqual(len(self.processes), 1)
        self.assertIs(supervisor._activity, old)
        old.wait_timeout = False
        supervisor.update({"activity_enabled": False, "memory_enabled": False})
        wait_for(lambda: supervisor._activity is None)

    def test_blocked_model_does_not_delay_activity_shutdown_or_commit_after_disable(self):
        supervisor = self.start(self.blocking_complete)
        supervisor.update({"activity_enabled": True, "memory_enabled": True, "background_enabled": True})
        self.assertTrue(self.started.wait(3))
        old_owner = self.owners[0]
        old_process = self.processes[0]
        began = time.monotonic()
        supervisor.update({"activity_enabled": False, "memory_enabled": False, "background_enabled": False})
        wait_for(lambda: old_process.returncode == 0 and old_owner.closed, timeout=1.5)
        self.assertLess(time.monotonic() - began, 1.5)
        self.assertTrue(old_owner.failed)
        self.assertLess(old_owner.calls.index("fail_job"), old_owner.calls.index("close"))
        self.assertFalse(old_owner.completed)
        self.release.set()
        wait_for(lambda: self.tasks[0].done.is_set())
        supervisor._wake.set()
        wait_for(lambda: supervisor._completion is None)
        self.assertFalse(old_owner.completed)

    def test_source_revision_change_fences_result_even_when_settings_are_equal(self):
        supervisor = self.start(self.blocking_complete)
        settings = {"activity_enabled": False, "memory_enabled": True, "background_enabled": True}
        supervisor.update(settings)
        self.assertTrue(self.started.wait(3))
        owner = self.owners[0]
        supervisor.update(settings)  # Product source/Memory correction fence.
        wait_for(lambda: bool(owner.failed))
        self.release.set()
        wait_for(lambda: self.tasks[0].done.is_set())
        supervisor._wake.set()
        wait_for(lambda: supervisor._completion is None)
        self.assertFalse(owner.completed)

    def test_model_tasks_remain_bounded_during_repeated_reconfiguration(self):
        supervisor = self.start(self.blocking_complete)
        settings = {"activity_enabled": False, "memory_enabled": True, "background_enabled": True}
        supervisor.update(settings)
        self.assertTrue(self.started.wait(3))
        for _ in range(3):
            supervisor.update({**settings, "background_enabled": False})
            wait_for(lambda: not supervisor.status()["settings_pending"])
            supervisor.update(settings)
            wait_for(lambda: not supervisor.status()["settings_pending"])
        self.assertEqual(len(self.tasks), 1)
        self.assertTrue(self.tasks[0].cancelled.is_set())
        self.assertTrue(supervisor.status()["completion_pending"])

    def test_current_completion_commits_once(self):
        def complete(_job, _task):
            return {"text": "current result", "usage": {"tokens": 2}}
        supervisor = self.start(complete)
        supervisor.update({"memory_enabled": True, "activity_enabled": False, "background_enabled": True})
        wait_for(lambda: bool(self.owners) and len(self.owners[0].completed) == 1)
        self.assertFalse(self.owners[0].failed)
        self.assertEqual(self.owners[0].completed[0]["result"], "current result")
        supervisor._wake.set()
        wait_for(lambda: supervisor.status()["semantic"]["state"] == "idle")
        self.assertEqual(len(self.owners[0].completed), 1)

    def test_revoked_activity_or_memory_source_prevents_claim_and_model_send(self):
        supervisor = self.start(lambda *_: self.fail("revoked source reached a model"))
        settings = {"memory_enabled": True, "activity_enabled": False, "background_enabled": True,
                    "_framework_revoked_sources": ["memory:withdrawn"]}
        supervisor.update(settings)
        wait_for(lambda: supervisor.status()["semantic"]["state"] == "source_blocked")
        self.assertNotIn("claim_job", self.owners[0].calls)
        profile = yaml.safe_load((self.home / "config/memory.yaml").read_text(encoding="utf-8"))
        self.assertFalse(profile["semantics"]["enabled"])

    def test_preference_revocation_does_not_block_unrelated_semantic_job(self):
        supervisor = self.start(lambda *_: {"text": "unrelated observation", "usage": {}})
        supervisor.update({"memory_enabled": True, "activity_enabled": False, "background_enabled": True,
                           "_framework_revoked_sources": [{"source_id": "preference:user/local:topic"}]})
        wait_for(lambda: bool(self.owners) and bool(self.owners[0].completed))
        self.assertEqual(len(self.owners[0].completed), 1)

    def test_revoke_while_transport_blocked_fails_job_and_prevents_late_commit(self):
        supervisor = self.start(self.blocking_complete)
        settings = {"memory_enabled": True, "activity_enabled": False, "background_enabled": True}
        supervisor.update(settings)
        self.assertTrue(self.started.wait(3))
        old = self.owners[0]
        supervisor.update({**settings, "_framework_revoked_sources": ["observation:withdrawn"]})
        wait_for(lambda: supervisor.status()["semantic"]["state"] == "source_blocked")
        self.assertTrue(old.failed)
        self.assertTrue(old.closed)
        self.release.set()
        wait_for(lambda: self.tasks[0].done.is_set())
        supervisor._wake.set()
        wait_for(lambda: supervisor._completion is None)
        self.assertFalse(old.completed)
        self.assertEqual(len(self.tasks), 1)

    def test_http_transport_cancel_shuts_down_waiting_connection(self):
        entered, shutdown = threading.Event(), threading.Event()
        class Connection:
            def __init__(self, *_args, **_kwargs):
                self.sock = self
            def connect(self):
                pass
            def request(self, *_args, **_kwargs):
                pass
            def getresponse(self):
                entered.set()
                if not shutdown.wait(3):
                    raise AssertionError("socket was not shut down")
                raise OSError("fixture transport cancelled")
            def shutdown(self, _how):
                shutdown.set()
            def close(self):
                pass
        supervisor = self.start()
        supervisor.bridge_url, supervisor.token = "http://127.0.0.1:1/internal/completion", "fixture-token"
        job = {"job_id": "direct", "lease_token": "direct", "payload": {"system_prompt": "x", "prompt": "y"}}
        task = _Completion(None, job, 1, "test", supervisor._complete, threading.Event())
        self.tasks.append(task)
        with patch("mirally.framework_workers.http.client.HTTPConnection", Connection):
            task.thread.start()
            self.assertTrue(entered.wait(2))
            task.cancel()
            self.assertTrue(task.done.wait(1))
        self.assertTrue(shutdown.is_set())
        self.assertIsNotNone(task.error)
        self.assertIsNone(task.result)


if __name__ == "__main__":
    unittest.main()
