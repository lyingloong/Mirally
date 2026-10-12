import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from openassist.core.execution import ExecutionCancelled, Reobserve
from openassist.platform.frontend_registry import FrontendRegistration, registered_frontends
from openassist.runtime.ufo.worker import WorkerChannel, WorkerServices, prepare_worker_config, worker_mode
from tools.configure_ufo_agent import agent_patch

ROOT = Path(__file__).resolve().parents[1]


class WorkerProtocolTests(unittest.TestCase):
    def test_worker_mode_defaults_to_conversation_and_rejects_unknown_values(self):
        self.assertEqual(worker_mode({}), "conversation")
        self.assertEqual(worker_mode({"mode": "desktop_subtask"}), "desktop_subtask")
        with self.assertRaises(ValueError):
            worker_mode({"mode": "reply"})

    def test_observation_is_not_started_after_host_refuses(self):
        channel, frontend = WorkerChannel(io.StringIO(), io.StringIO()), Mock()
        channel.rpc = Mock(return_value=False)
        frontend.editing.return_value = False
        with self.assertRaises(ExecutionCancelled):
            WorkerServices(channel, frontend).capture("before")

    def test_classifier_checkpoint_does_not_wait_for_a_focused_composer(self):
        channel, frontend = WorkerChannel(io.StringIO(), io.StringIO()), Mock()
        frontend.editing.return_value = True
        self.assertEqual(WorkerServices(channel, frontend).checkpoint(wait_for_editing=False), 1)
        frontend.editing.assert_called_once()

    def test_nonblocking_composer_checkpoint_still_honors_pause(self):
        channel, frontend = WorkerChannel(io.StringIO(), io.StringIO()), Mock()
        channel.paused = True
        frontend.editing.return_value = False
        services = WorkerServices(channel, frontend)
        result = []
        thread = threading.Thread(target=lambda: result.append(services.checkpoint(wait_for_editing=False)))
        thread.start()
        time.sleep(0.03)
        self.assertTrue(thread.is_alive())
        with channel.condition:
            channel.paused = False
            channel.condition.notify_all()
        thread.join(1)
        self.assertFalse(thread.is_alive())
        self.assertEqual(result, [0])

    def test_desktop_checkpoint_still_waits_for_a_focused_composer(self):
        channel, frontend = WorkerChannel(io.StringIO(), io.StringIO()), Mock()
        frontend.editing.side_effect = [True, False]
        services = WorkerServices(channel, frontend)
        self.assertEqual(services.checkpoint(wait_for_editing=True), 1)
        self.assertEqual(frontend.editing.call_count, 2)
        frontend.before.assert_not_called()

    def test_desktop_action_boundary_waits_for_composer(self):
        channel, frontend = WorkerChannel(io.StringIO(), io.StringIO()), Mock()
        frontend.editing.side_effect = [True, False]
        services = WorkerServices(channel, frontend)
        self.assertEqual(services.desktop_access("before", expected_revision=1), 1)
        self.assertEqual(frontend.editing.call_count, 2)

    def test_capture_boundary_waits_before_observation(self):
        channel, frontend = WorkerChannel(io.StringIO(), io.StringIO()), Mock()
        frontend.editing.side_effect = [True, False]
        channel.rpc = Mock(return_value=True)
        services = WorkerServices(channel, frontend)
        services.capture("before")
        self.assertEqual(frontend.editing.call_count, 2)
        frontend.before.assert_called_once()
        channel.rpc.assert_called_once_with("authorize_observation")

    def test_cancel_before_start_unblocks_worker(self):
        channel = WorkerChannel(io.StringIO('{"type":"cancel"}\n'), io.StringIO())
        channel.listen()
        self.assertTrue(channel.ready.is_set())
        self.assertIsNone(channel.first)
        with self.assertRaises(ExecutionCancelled):
            channel.check()

    def test_eof_revokes_execution(self):
        channel = WorkerChannel(io.StringIO('{"type":"run","text":"synthetic"}\n'), io.StringIO())
        channel.listen()
        self.assertTrue(channel.stopped)

    def test_revision_change_revokes_a_pending_approval(self):
        channel = WorkerChannel(io.StringIO(), io.StringIO())
        errors = []
        def request():
            try:
                channel.rpc("confirm", text="Synthetic only")
            except Reobserve as error:
                errors.append(error)
        thread = threading.Thread(target=request)
        thread.start()
        deadline = time.monotonic() + 2
        while not channel.pending and time.monotonic() < deadline:
            time.sleep(0.005)
        self.assertTrue(channel.pending)
        with channel.condition:
            channel.revision += 1
            channel.condition.notify_all()
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(errors), 1)
        self.assertFalse(channel.pending)

    def test_worker_snapshot_excludes_local_credentials(self):
        with tempfile.TemporaryDirectory() as source, tempfile.TemporaryDirectory() as target:
            root, temporary = Path(source), Path(target)
            config = root / "config/openassist/ufo"
            config.mkdir(parents=True)
            (config / "agents.yaml").write_text("PRIVATE-DO-NOT-READ")
            (config / "agents.yaml.template").write_text("GENERIC-TEMPLATE")
            (config / "system.yaml").write_text("MAX_STEP: 10")
            prepare_worker_config(root, temporary)
            self.assertEqual((temporary / "config/openassist/ufo/agents.yaml").read_text(), "GENERIC-TEMPLATE")

    def test_frontend_registration_validates_process_generation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registration = FrontendRegistration(root)
            try:
                registration.update(True)
                values = registered_frontends(root)
                self.assertEqual(len(values), 1)
                self.assertTrue(values[0]["editing"])
                data = json.loads(registration.path.read_text())
                data["created"] -= 100
                registration.path.write_text(json.dumps(data))
                self.assertEqual(registered_frontends(root), ())
            finally:
                registration.close()
            self.assertEqual(registered_frontends(root), ())

    def test_child_process_permission_failure_does_not_drop_the_frontend(self):
        import psutil
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registration = FrontendRegistration(root)
            try:
                process = Mock()
                process.create_time.return_value = registration.created
                process.children.side_effect = psutil.AccessDenied(registration.pid)
                with patch("psutil.Process", return_value=process):
                    values = registered_frontends(root)
                self.assertEqual(values[0]["process_ids"], (registration.pid,))
            finally:
                registration.close()

    def test_gateway_and_task_apps_are_not_classified_as_frontend_children(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registration = FrontendRegistration(root)
            try:
                process, webview, gateway, application = Mock(), Mock(), Mock(), Mock()
                process.create_time.return_value = registration.created
                for child, pid, name in ((webview, 300001, "msedgewebview2.exe"),
                                         (gateway, 300002, "node.exe"),
                                         (application, 300003, "notepad.exe")):
                    child.pid = pid
                    child.name.return_value = name
                    child.create_time.return_value = 100
                process.children.return_value = [webview, gateway, application]
                with patch("psutil.Process", return_value=process):
                    values = registered_frontends(root)
                self.assertEqual(values[0]["process_ids"], (registration.pid, webview.pid))
            finally:
                registration.close()

    def test_agent_patch_preserves_default_and_existing_plugin_paths(self):
        config = {"agents": {"defaults": {"model": "original/model"}},
                  "plugins": {"load": {"paths": ["existing"]}, "allow": ["existing"]}}
        patch = agent_patch(config, ROOT, "example/vision")
        self.assertNotIn("defaults", patch["agents"])
        self.assertEqual(patch["plugins"]["load"]["paths"][0], "existing")
        self.assertEqual(patch["plugins"]["load"]["paths"][1:], [
            str((ROOT / "integrations/openclaw/ufo").resolve()),
        ])
        self.assertNotIn("mirally-activity", patch["plugins"]["entries"])
        self.assertEqual(patch["plugins"]["allow"], ["existing", "mirally-ufo"])
        self.assertEqual(patch["agents"]["entries"]["ufo"]["models"]["example/vision"]["agentRuntime"]["id"], "mirally-ufo")
        self.assertEqual(config["agents"]["defaults"]["model"], "original/model")

    def test_agent_id_collision_is_not_overwritten(self):
        with self.assertRaises(ValueError):
            agent_patch({"agents": {"entries": {"ufo": {"name": "Authored agent"}}}}, ROOT, "example/model")


@unittest.skipUnless(sys.platform == "win32", "Windows native mutex")
class DesktopOwnershipTests(unittest.TestCase):
    def test_another_process_cannot_acquire_desktop_until_release(self):
        from openassist.platform.desktop_lock import desktop_execution_lock
        script = (
            "from openassist.platform.desktop_lock import desktop_execution_lock\n"
            "try:\n"
            "    with desktop_execution_lock(): print('acquired')\n"
            "except RuntimeError: print('busy')\n"
        )
        def attempt():
            return subprocess.run([sys.executable, "-c", script], cwd=ROOT, capture_output=True,
                                  text=True, timeout=10)
        with desktop_execution_lock():
            result = attempt()
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "busy")
        self.assertEqual(attempt().stdout.strip(), "acquired")


if __name__ == "__main__":
    unittest.main()
