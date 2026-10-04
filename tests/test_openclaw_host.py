"""Desktop host lifecycle checks without a live model or changes to native state."""
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

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
            self.assertEqual(run.call_args_list[0].args[0], [str(self.host.node), str(adapter)])
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
        with patch.object(shell, '_install_brand'):
            shell._connect()
        self.assertEqual(order, ['native', 'opening document'])
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
