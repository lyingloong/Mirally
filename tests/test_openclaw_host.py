"""Desktop host lifecycle checks without a live model or changes to native state."""
import json
from pathlib import Path
import subprocess
import socket
import sys
import time
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

import psutil

from openassist.openclaw_host import HostError, OpenClawHost
from openassist.control_ui_shell import ControlUIShell, startup_html
from openassist.__main__ import main


class OpenClawHostTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / ".artifacts").mkdir()
        for name in ("node.exe", "openclaw.mjs"):
            (self.root / name).touch()
        (self.root / ".artifacts/openclaw-runtime.json").write_text(json.dumps({
            "node": "node.exe", "entry": "openclaw.mjs",
        }))
        self.state = self.root / "config/openassist/openclaw"
        self.state.mkdir(parents=True)
        self.config = self.state / "openclaw.json"
        # A JSON5 config must be read by OpenClaw, not by the shell.
        self.original = '// private config\n{gateway: {port: 18789}, secret: "never-print"}'
        self.config.write_text(self.original)
        self.host = OpenClawHost(self.root)

    def test_running_gateway_is_reused_without_reading_or_changing_config(self):
        with patch.object(self.host, "dashboard", return_value="http://127.0.0.1/#one-time-pairing"), \
             patch.object(self.host, "_native_json") as native, \
             patch.object(self.host, "_start_gateway") as start:
            self.assertEqual(self.host.connect(Mock()), "http://127.0.0.1/#one-time-pairing")
            start.assert_not_called()
            native.assert_not_called()
        self.assertEqual(self.config.read_text(), self.original)

    def test_only_missing_local_gateway_is_started(self):
        with patch.object(self.host, "dashboard", side_effect=[HostError("offline"), "http://127.0.0.1/"]), \
             patch.object(self.host, "_native_json", return_value={"mode": "local", "port": 18789}), \
             patch.object(self.host, "_listening", side_effect=[False, True]), \
             patch.object(self.host, "_start_gateway") as start:
            self.assertEqual(self.host.connect(Mock()), "http://127.0.0.1/")
            start.assert_called_once()
        self.assertEqual(self.config.read_text(), self.original)

    @unittest.skipUnless(hasattr(subprocess, "CREATE_NO_WINDOW"), "Windows process flags required")
    def test_auto_gateway_hides_console_descendants(self):
        with patch("openassist.openclaw_host.subprocess.Popen", return_value=Mock(pid=1234)) as popen:
            self.host._start_gateway()
        flags = popen.call_args.kwargs["creationflags"]
        self.assertTrue(flags & subprocess.CREATE_NO_WINDOW)
        self.assertTrue(flags & subprocess.CREATE_NEW_PROCESS_GROUP)
        self.assertFalse(flags & getattr(subprocess, "DETACHED_PROCESS", 0))

    def test_busy_port_does_not_replace_or_kill_existing_service(self):
        with patch.object(self.host, "dashboard", side_effect=[HostError("not ready"), "http://127.0.0.1/"]), \
             patch.object(self.host, "_native_json", return_value={"port": 18789}), \
             patch.object(self.host, "_listening", return_value=True), \
             patch.object(self.host, "_start_gateway") as start:
            self.host.connect(Mock())
            start.assert_not_called()

    def test_disabled_control_ui_and_remote_deployment_are_preserved(self):
        for config in ({"controlUi": {"enabled": False}}, {"mode": "remote"}):
            with self.subTest(config=config), \
                 patch.object(self.host, "dashboard", side_effect=HostError("offline")), \
                 patch.object(self.host, "_native_json", return_value=config), \
                 patch.object(self.host, "_start_gateway") as start:
                with self.assertRaises(HostError):
                    self.host.connect(Mock())
                start.assert_not_called()
        self.assertEqual(self.config.read_text(), self.original)

    def test_raw_native_errors_and_pairing_urls_do_not_escape(self):
        secret = "secret-token http://localhost/#private-pairing"
        failures = [
            subprocess.CompletedProcess([], 1, json.dumps({"reason": secret}), secret),
            subprocess.CompletedProcess([], 0, secret, ""),
            subprocess.TimeoutExpired("native", 25, output=secret),
        ]
        for failure in failures:
            with self.subTest(failure=type(failure).__name__), \
                 patch("openassist.openclaw_host.subprocess.run", side_effect=failure if isinstance(failure, Exception) else None,
                       return_value=failure):
                with self.assertRaises(HostError) as caught:
                    self.host.dashboard()
                self.assertNotIn(secret, str(caught.exception))

    def test_window_close_does_not_stop_gateway(self):
        gateway = self.host._gateway = Mock()
        self.host.close()
        self.assertTrue(self.host.closed.is_set())
        gateway.terminate.assert_not_called()
        gateway.kill.assert_not_called()
        gateway.wait.assert_not_called()

    def test_restart_terminates_verified_listener_before_reconnecting(self):
        status, process, order = Mock(), Mock(), []
        with patch.object(self.host, "_native_json", return_value={"mode": "local", "port": 18789}), \
             patch.object(self.host, "_listening", side_effect=[True, False, False]), \
             patch.object(self.host, "_restart_target", return_value=process) as identify, \
             patch.object(self.host, "_stop_gateway", side_effect=lambda p: order.append("stopped")) as stop, \
             patch.object(self.host, "connect", side_effect=lambda s: order.append("connected") or "http://127.0.0.1/"):
            self.assertEqual(self.host.restart(status), "http://127.0.0.1/")
        identify.assert_called_once_with(18789)
        stop.assert_called_once_with(process)
        self.assertEqual(order, ["stopped", "connected"])
        self.assertEqual(self.config.read_text(), self.original)

    def test_offline_restart_uses_normal_connection_startup(self):
        with patch.object(self.host, "_native_json", return_value={"mode": "local"}), \
             patch.object(self.host, "_listening", return_value=False), \
             patch.object(self.host, "_restart_target") as identify, \
             patch.object(self.host, "connect", return_value="http://127.0.0.1/") as connect:
            self.assertEqual(self.host.restart(Mock()), "http://127.0.0.1/")
        identify.assert_not_called()
        connect.assert_called_once()

    def test_restart_refuses_remote_disabled_and_invalid_config_without_stopping(self):
        for config in ({"mode": "remote"}, {"controlUi": {"enabled": False}}, {"port": 0}):
            with self.subTest(config=config), \
                 patch.object(self.host, "_native_json", return_value=config), \
                 patch.object(self.host, "_stop_gateway") as stop, \
                 patch.object(self.host, "connect") as connect:
                with self.assertRaises(HostError):
                    self.host.restart(Mock())
                stop.assert_not_called()
                connect.assert_not_called()

    def test_restart_refuses_unidentified_process_and_does_not_reconnect(self):
        with patch.object(self.host, "_native_json", return_value={"mode": "local"}), \
             patch.object(self.host, "_listening", return_value=True), \
             patch.object(self.host, "_restart_target", side_effect=HostError("not ours")), \
             patch.object(self.host, "_stop_gateway") as stop, \
             patch.object(self.host, "connect") as connect:
            with self.assertRaises(HostError):
                self.host.restart(Mock())
        stop.assert_not_called()
        connect.assert_not_called()

    def test_restart_does_not_start_second_instance_when_port_remains_busy(self):
        with patch.object(self.host, "_native_json", return_value={"mode": "local"}), \
             patch.object(self.host, "_listening", return_value=True), \
             patch.object(self.host, "_restart_target", return_value=Mock()), \
             patch.object(self.host, "_stop_gateway"), \
             patch("openassist.openclaw_host.time.monotonic", side_effect=[0, 6]), \
             patch.object(self.host, "connect") as connect:
            with self.assertRaisesRegex(HostError, "端口仍被占用"):
                self.host.restart(Mock())
        connect.assert_not_called()

    def test_restart_verifies_listener_identity_without_saved_pid_or_credentials(self):
        process = Mock()
        process.exe.return_value = str(self.host.node)
        process.cmdline.return_value = [str(self.host.node), str(self.host.entry), "gateway", "run"]
        environment = {"OPENCLAW_STATE_DIR": str(self.state), "OPENCLAW_CONFIG_PATH": str(self.config)}
        process.environ.return_value = environment
        listener = Mock(pid=123, status=psutil.CONN_LISTEN, laddr=Mock(port=18789))
        with patch("openassist.openclaw_host.psutil.net_connections", return_value=[listener]), \
             patch("openassist.openclaw_host.psutil.Process", return_value=process):
            self.assertIs(self.host._restart_target(18789), process)
            process.create_time.assert_called_once()
            for key in environment:
                with self.subTest(key=key), patch.object(process, "environ", return_value={**environment, key: "other"}):
                    with self.assertRaises(HostError):
                        self.host._restart_target(18789)
            for method, value in (("exe", "other.exe"), ("cmdline", ["node", "other.mjs", "gateway", "run"]),
                                  ("cmdline", [str(self.host.node), str(self.host.entry), "gateway", "status"])):
                with self.subTest(method=method, value=value), patch.object(process, method, return_value=value):
                    with self.assertRaises(HostError):
                        self.host._restart_target(18789)
        process.terminate.assert_not_called()

    def test_restart_identity_access_error_has_safe_public_message(self):
        with patch("openassist.openclaw_host.psutil.net_connections",
                   side_effect=psutil.AccessDenied(123, "PRIVATE_PROCESS_NAME")):
            with self.assertRaises(HostError) as caught:
                self.host._restart_target(18789)
        self.assertNotIn("PRIVATE", str(caught.exception))

    def test_restart_stops_worker_tree_and_waits_for_termination(self):
        parent, child = Mock(), Mock()
        parent.children.return_value = [child]
        self.host._gateway = Mock()
        with patch("openassist.openclaw_host.psutil.wait_procs", side_effect=[([], [child]), ([child], [])]) as wait:
            self.host._stop_gateway(parent)
        parent.terminate.assert_called_once()
        child.terminate.assert_called_once()
        child.kill.assert_called_once()
        self.assertEqual(wait.call_args_list[0].args[0], [parent, child])
        self.assertIsNone(self.host._gateway)

    def test_failed_stop_does_not_report_gateway_stopped(self):
        parent = Mock()
        parent.children.side_effect = psutil.AccessDenied(123)
        original = self.host._gateway = Mock()
        with self.assertRaises(HostError):
            self.host._stop_gateway(parent)
        self.assertIs(self.host._gateway, original)
        parent.terminate.assert_not_called()

    @unittest.skipUnless(sys.platform == "win32", "Windows process restart")
    def test_restart_replaces_real_isolated_process_and_retires_its_worker(self):
        root = Path(__file__).resolve().parents[1]
        node = root / ".artifacts/node/node-v24.21.0-win-x64/node.exe"
        if not node.is_file():
            self.skipTest("Project Node runtime is not installed")
        (self.root / ".artifacts/openclaw-runtime.json").write_text(json.dumps({
            "node": str(node), "entry": "openclaw.mjs",
        }), encoding="utf-8")
        (self.root / "openclaw.mjs").write_text(
            (root / "tests/gateway_restart_fixture.mjs").read_text(encoding="utf-8"), encoding="utf-8")
        host = OpenClawHost(self.root)
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        host.env["OPENCLAW_GATEWAY_PORT"] = str(port)
        started, handles = [], []

        def dashboard():
            if not host._listening(port):
                raise HostError("synthetic gateway offline")
            return f"http://127.0.0.1:{port}/"

        try:
            host._start_gateway()
            handles.append(host._gateway)
            old = psutil.Process(host._gateway.pid)
            started.append(old)
            deadline = time.monotonic() + 5
            while not host._listening(port) and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertTrue(host._listening(port), "synthetic gateway did not start")
            workers = old.children(recursive=True)
            self.assertTrue(workers, "synthetic child did not start")
            # A newly opened shell has no in-memory Popen handle, as in a real
            # restart after reopening Mirally. Listener/config identity suffices.
            reconnecting = OpenClawHost(self.root)
            reconnecting.env["OPENCLAW_GATEWAY_PORT"] = str(port)
            with patch.object(reconnecting, "_native_json", return_value={"mode": "local", "port": port}), \
                 patch.object(reconnecting, "dashboard", side_effect=dashboard):
                self.assertEqual(reconnecting.restart(Mock()), f"http://127.0.0.1:{port}/")
            new = psutil.Process(reconnecting._gateway.pid)
            started.append(new)
            handles.append(reconnecting._gateway)
            self.assertNotEqual(old.pid, new.pid)
            self.assertFalse(old.is_running())
            self.assertTrue(all(not worker.is_running() for worker in workers))
            self.assertEqual(self.config.read_text(), self.original)
        finally:
            # Only processes launched into this temporary fixture are cleaned.
            if "reconnecting" in locals() and reconnecting._gateway is not None:
                candidate = psutil.Process(reconnecting._gateway.pid)
                if candidate not in started:
                    started.append(candidate)
            for process in started:
                if process.is_running():
                    host._stop_gateway(process)
            for handle in handles:
                handle.wait(timeout=3)

    def test_shell_restart_confirmation_reconnects_in_same_window(self):
        shell = ControlUIShell(self.root)
        shell.window, shell.host = Mock(), Mock()
        shell.window.create_confirmation_dialog.return_value = True
        shell.host.restart.return_value = "http://127.0.0.1/"
        shell.window.events.loaded.wait.return_value = True
        shell._startup_visible.set()
        with patch.object(shell, "_install_brand"), patch("openassist.control_ui_shell.time.monotonic", side_effect=[0, 2]):
            shell._connect(reload=True, restart=True)
        shell.host.restart.assert_called_once_with(shell._status)
        shell.host.connect.assert_not_called()
        shell.window.load_html.assert_called_once()
        shell.window.load_url.assert_called_once_with("http://127.0.0.1/")
        shell.window.destroy.assert_not_called()

    def test_declined_or_duplicate_restart_has_no_navigation_or_host_effects(self):
        shell = ControlUIShell(self.root)
        shell.window, shell.host = Mock(), Mock()
        shell.window.create_confirmation_dialog.return_value = False
        shell._connect(reload=True, restart=True)
        shell.window.load_html.assert_not_called()
        shell.host.restart.assert_not_called()
        self.assertFalse(shell._connecting.locked())
        shell.window.reset_mock()
        shell._connecting.acquire()
        try:
            shell._connect(reload=True, restart=True)
            shell.window.create_confirmation_dialog.assert_not_called()
            shell.host.restart.assert_not_called()
        finally:
            shell._connecting.release()

    def test_shell_restart_failure_is_displayed_and_lock_released(self):
        shell = ControlUIShell(self.root)
        shell.window, shell.host = Mock(), Mock()
        shell.window.create_confirmation_dialog.return_value = True
        shell.host.restart.side_effect = HostError("restart unavailable")
        with patch.object(shell, "_status") as status:
            shell._connect(reload=True, restart=True)
        status.assert_called_once_with("restart unavailable", error=True)
        shell.window.load_url.assert_not_called()
        self.assertFalse(shell._connecting.locked())

    def test_native_dashboard_entry_falls_back_only_when_incompatible(self):
        adapter = self.root / 'integrations/openclaw/dashboard.mjs'
        adapter.parent.mkdir(parents=True)
        adapter.touch()
        handoff = 'http://127.0.0.1/#one-time-pairing'
        with patch('openassist.openclaw_host.subprocess.run', side_effect=[
            subprocess.CompletedProcess([], 78, '', ''),
            subprocess.CompletedProcess([], 0, json.dumps({'ok':True,'browserUrl':handoff}), ''),
        ]) as run:
            self.assertEqual(self.host.dashboard(), handoff)
            self.assertEqual(run.call_args_list[0].args[0], [str(self.host.node), str(adapter.resolve())])
            self.assertEqual(run.call_args_list[1].args[0][-2:], ['dashboard', '--json'])
        with patch('openassist.openclaw_host.subprocess.run', return_value=
                   subprocess.CompletedProcess([], 1, json.dumps({'ok':False,'reason':'not ready'}), '')) as run:
            with self.assertRaises(HostError): self.host.dashboard()
            self.assertEqual(run.call_count, 1)

    def test_startup_resolves_gateway_before_waiting_for_opening_document(self):
        shell = ControlUIShell(self.root)
        shell.window, shell.host = Mock(), Mock()
        shell.closed = Mock()
        shell.closed.is_set.return_value = False
        order = []
        shell.host.connect.side_effect = lambda status: order.append('native') or 'http://127.0.0.1/'
        shell.window.events.loaded.wait.side_effect = lambda timeout: order.append('opening document') or True
        shell._startup_visible = Mock()
        shell._startup_visible.wait.side_effect = lambda timeout: order.append('opening frame') or True
        with patch.object(shell, '_install_brand'):
            shell._connect()
        self.assertEqual(order, ['native', 'opening document', 'opening frame'])
        shell.window.load_url.assert_called_once_with('http://127.0.0.1/')

    def test_status_before_webview_initialization_is_delivered_later(self):
        shell = ControlUIShell(self.root)
        shell._status('正在连接 OpenClaw')
        self.assertEqual(shell._startup_status, ('正在连接 OpenClaw', False))
        shell.window = Mock()
        shell.window.events.loaded.is_set.return_value = False
        shell._status('暂时不可用', error=True)
        shell.window.get_current_url.assert_not_called()
        shell.window.evaluate_js.assert_not_called()
        shell.window.events.loaded.is_set.return_value = True
        shell.window.get_current_url.return_value = 'about:blank'
        shell._status(*shell._startup_status)
        shell.window.evaluate_js.assert_called_once()
        self.assertIn('true', shell.window.evaluate_js.call_args.args[0])

    def test_startup_fits_webview2_memory_document_limit(self):
        html = startup_html()
        self.assertLess(len(html.encode("utf-8")), 2 * 1024 * 1024)
        self.assertNotIn("__MIRALLY_LOGO__", html)
        self.assertNotIn("pywebview.api", html)

    def test_default_and_explicit_launch_choose_full_official_ui(self):
        for arguments in ([], ["--openclaw"]):
            with self.subTest(arguments=arguments), \
                 patch.object(sys, "argv", ["mirally", *arguments]), \
                 patch.object(sys, "platform", "win32"), \
                 patch("openassist.control_ui_shell.ControlUIShell") as shell:
                main()
                shell.return_value.run.assert_called_once()

    def test_incompatible_launch_modes_are_rejected_before_starting(self):
        with patch.object(sys, "argv", ["mirally", "--openclaw", "--ufo"]), \
             patch.object(sys, "platform", "win32"), \
             patch("openassist.control_ui_shell.ControlUIShell") as shell, \
             patch("sys.stderr"):
            with self.assertRaises(SystemExit) as caught:
                main()
            self.assertEqual(caught.exception.code, 2)
            shell.assert_not_called()


if __name__ == "__main__":
    unittest.main()
