"""Framework desktop presentation without a Gateway, GUI or model service."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from mirally.control_ui_shell import ControlUIShell, startup_html
from mirally.framework_shell import FrameworkShell
from mirally.framework_host import discover_node


class FrameworkShellTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="mirally 原生体验 ")
        self.addCleanup(self.temporary.cleanup)
        self.host = SimpleNamespace(root=Path(self.temporary.name), home=Path(self.temporary.name) / "data",
            runtime={"url": "http://127.0.0.1:37281", "token": "isolated-token"},
            url="http://127.0.0.1:37281/#token=isolated-token", process=None,
            start=MagicMock(), close=MagicMock())
        self.shell = FrameworkShell(self.host)
        loaded = threading.Event()
        loaded.set()
        self.shell.window = MagicMock()
        self.shell.window.events.loaded = loaded
        self.shell.window.get_current_url.return_value = "about:blank"
        self.shell._startup_visible.set()

    @staticmethod
    def frame(value, source="http://127.0.0.1:37281/"):
        return SimpleNamespace(Source=source, get_WebMessageAsJson=lambda: json.dumps(value))

    def test_constructor_reuses_presentation_without_creating_legacy_host(self):
        with patch("mirally.control_ui_shell.OpenClawHost") as legacy:
            shell = FrameworkShell(self.host)
        legacy.assert_not_called()
        self.assertIs(shell.host, self.host)
        self.assertIsInstance(shell, ControlUIShell)
        for name in ("_companion_action", "_initial_companion_shape", "_companion_background", "_capture_target", "_context_menu_requested"):
            self.assertIs(getattr(FrameworkShell, name), getattr(ControlUIShell, name))

    def test_ready_host_enters_without_decorative_delay_or_legacy_skin(self):
        with patch("mirally.control_ui_shell.OpenClawHost") as legacy, \
             patch.object(self.shell, "_install_brand") as legacy_skin:
            self.shell._connect()
        self.host.start.assert_called_once_with()
        self.shell.window.load_url.assert_called_once_with(self.host.url)
        self.assertEqual(self.shell._gateway_origin, ("http", "127.0.0.1:37281"))
        legacy.assert_not_called()
        legacy_skin.assert_not_called()

    def test_shared_opening_keeps_formal_mark_particles_and_reduced_motion(self):
        html = startup_html()
        self.assertIn("与你并肩，让想法向前。", html)
        self.assertIn('class="orbit a"', html)
        self.assertIn("prefers-reduced-motion", html)
        self.assertNotIn("__MIRALLY_LOGO__", html)
        self.assertLess(len(html.encode("utf-8")), 2 * 1024 * 1024)

    def test_failure_stays_on_opening_page_with_retry_diagnostic(self):
        self.host.start.side_effect = RuntimeError("isolated startup failure")
        self.shell._connect()
        self.shell.window.load_url.assert_not_called()
        self.assertEqual(self.shell._startup_status, ("isolated startup failure", True))
        self.assertIn("isolated startup failure", self.shell.window.evaluate_js.call_args.args[0])
        self.assertFalse(self.shell._connecting.locked())

    def test_first_paint_comes_only_from_opening_and_never_reopens_closed_window(self):
        self.shell._startup_visible.clear()
        self.shell.window.native.Opacity = 0
        self.shell._web_message(None, self.frame({"type": "mirally-startup-painted"}))
        self.assertFalse(self.shell._startup_visible.is_set())
        self.shell._web_message(None, self.frame({"type": "mirally-startup-painted"}, "about:blank"))
        self.assertTrue(self.shell._startup_visible.is_set())
        self.assertEqual(self.shell.window.native.Opacity, 1)
        self.shell._startup_visible.clear()
        self.shell.closed.set()
        self.shell.window.native.Opacity = 0
        self.shell._web_message(None, self.frame({"type": "mirally-startup-painted"}, "about:blank"))
        self.assertEqual(self.shell.window.native.Opacity, 0)

    def test_native_geometry_rejects_other_origins(self):
        self.shell._gateway_origin = ("http", "127.0.0.1:37281")
        with patch.object(self.shell, "_companion_action") as action:
            for origin in ("https://untrusted.invalid/", "http://127.0.0.1:37282/"):
                self.shell._web_message(None, self.frame({"type": "mirally-companion", "action": "dock"}, origin))
            action.assert_not_called()
            value = {"type": "mirally-companion", "action": "shape", "rect": [1, 1, 80, 96]}
            self.shell._web_message(None, self.frame(value))
            action.assert_called_once_with(value)

    def test_retired_ufo_focus_messages_do_not_register_or_update_worker_state(self):
        self.shell._gateway_origin = ("http", "127.0.0.1:37281")
        registration = MagicMock()
        self.shell._frontend_registration = registration
        for editing in (True, False, "yes"):
            for origin in ("http://127.0.0.1:37281/", "https://untrusted.invalid/"):
                self.shell._web_message(None, self.frame({"type": "mirally-ufo-input", "editing": editing}, origin))
        registration.update.assert_not_called()
        self.assertFalse((self.host.root / ".artifacts/frontends").exists())

    def test_native_style_keeps_window_menu_without_registering_ufo_input_tracking(self):
        self.shell.window.native.Invoke.side_effect = lambda callback: callback()
        modules = {"System": SimpleNamespace(Action=lambda callback: callback),
                   "System.Drawing": SimpleNamespace(Icon=MagicMock())}
        with patch.dict("sys.modules", modules), \
             patch("mirally.platform.frontend_registry.FrontendRegistration") as registration, \
             patch("mirally.platform.window_menu.WindowSystemMenu") as menu:
            self.shell._native_style()
        registration.assert_not_called()
        menu.return_value.install.assert_called_once_with()
        self.assertIsNone(self.shell._frontend_registration)
        self.assertFalse((self.host.root / ".artifacts/frontends").exists())
        self.assertEqual(self.shell.window.native.Opacity, 0)

    def test_remote_webview_protocol_is_ignored(self):
        self.shell._gateway_origin = ("http", "127.0.0.1:37281")
        self.shell._web_message(None, self.frame([1, 2, 3], "https://untrusted.invalid/"))
        self.shell.window.native.browser.on_script_notify.assert_not_called()
        message = self.frame([1, 2, 3])
        self.shell._web_message(None, message)
        self.shell.window.native.browser.on_script_notify.assert_called_once_with(None, message)

    def test_close_hides_when_tray_is_ready_but_explicit_quit_can_close(self):
        self.shell._tray_ready.set()
        self.assertFalse(self.shell._closing())
        self.shell.window.hide.assert_called_once()
        self.shell._quitting.set()
        self.assertTrue(self.shell._closing())

    def test_tray_companion_restores_hidden_window_before_docking(self):
        self.shell._dock()
        self.shell.window.show.assert_called_once()
        self.shell.window.restore.assert_called_once()
        self.assertIn("__mirallyCompanion?.dock()", self.shell.window.evaluate_js.call_args.args[0])

    def test_quit_uses_authenticated_local_shutdown_for_reused_host(self):
        with patch("mirally.framework_shell.urllib.request.urlopen") as opener:
            self.shell.quit()
        request = opener.call_args.args[0]
        self.assertEqual(request.full_url, self.host.runtime["url"] + "/api/shutdown")
        self.assertEqual(request.headers["Authorization"], "Bearer isolated-token")
        self.shell.window.destroy.assert_called_once()
        self.assertTrue(self.shell._quitting.is_set())

    def test_cleanup_closes_tray_and_host_once(self):
        tray = MagicMock()
        self.shell._tray = tray
        self.shell._close()
        self.shell._close()
        tray.Dispose.assert_called_once()
        self.host.close.assert_called_once()
        self.assertTrue(self.shell.closed.is_set())

    def test_close_while_starting_discards_late_navigation(self):
        self.host.start.side_effect = lambda: self.shell.closed.set()
        self.shell._connect()
        self.shell.window.load_url.assert_not_called()
        self.host.close.assert_called_once()

    def test_page_menu_is_allowlisted_and_restores_workspace_first(self):
        order = []
        self.shell.window.evaluate_js.side_effect = lambda _: order.append("navigate")
        with patch.object(self.shell, "_restore_workspace") as restore:
            restore.side_effect = lambda: order.append("restore")
            self.shell._navigate("memory")
            restore.assert_called_once()
        self.assertEqual(order, ["restore", "navigate"])
        self.assertIn('showPage?.("memory")', self.shell.window.evaluate_js.call_args.args[0])
        self.shell.window.evaluate_js.reset_mock()
        self.shell._navigate("not-a-page")
        self.shell._navigate(["memory"])
        self.shell.window.evaluate_js.assert_not_called()

    def test_native_page_menu_exits_shared_companion_mode_before_navigation(self):
        root = Path(__file__).resolve().parents[1]
        try:
            node = discover_node(root)
        except RuntimeError as error:
            self.skipTest(str(error))
        companion = (root / "mirally/assets/control-ui-companion.js").read_text(encoding="utf-8")
        # Run the shared transition itself without creating a browser or native
        # window. The animation frame is controlled so ordering is observable.
        set_mode = companion[companion.index("  function setMode(value)"):companion.index("  async function dock()")]
        workspace = companion[companion.index("  function workspace(callback)"):companion.index("  async function nativeFiles")]
        scripts = {}
        for page in ("chat", "memory", "activity", "work"):
            with patch.object(self.shell, "_restore_workspace"):
                self.shell._navigate(page)
            scripts[page] = self.shell.window.evaluate_js.call_args.args[0]
        script = r"""
const assert = require('node:assert/strict');
const vm = require('node:vm');
const input = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));
for (const [page, script] of Object.entries(input.scripts)) {
  const frames = [], events = [];
  const document = {documentElement: {dataset: {mirallyMode: 'companion'}}};
  const surface = {hidden: false};
  const window = {__mirallyFramework: {showPage(value) {
    assert.equal(document.documentElement.dataset.mirallyMode, 'workspace');
    assert.equal(surface.hidden, true);
    events.push(['navigate', value]);
  }}};
  const context = vm.createContext({window, document, surface,
    dismissReply() { events.push(['dismiss']); },
    post(value) { events.push(['native', value]); },
    schedule() {}, expand() {}, requestAnimationFrame(callback) { frames.push(callback); }});
  vm.runInContext('let mode = "companion";\n' + input.set_mode + input.workspace
    + '\nwindow.__mirallyCompanion = {workspace};', context);
  vm.runInContext(script, context);
  assert.equal(document.documentElement.dataset.mirallyMode, 'workspace');
  assert.equal(surface.hidden, true);
  assert.deepEqual(events, [['dismiss'], ['native', 'workspace']]);
  assert.equal(frames.length, 1);
  frames.shift()();
  assert.deepEqual(events.at(-1), ['navigate', page]);
  const fallbackPages = [];
  const fallback = vm.createContext({window: {__mirallyFramework: {showPage(value) { fallbackPages.push(value); }}}});
  vm.runInContext(script, fallback);
  assert.deepEqual(fallbackPages, [page]);
}
"""
        result = subprocess.run([str(node), "-e", script],
            input=json.dumps({"scripts": scripts, "set_mode": set_mode, "workspace": workspace}),
            text=True, encoding="utf-8", capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def browser_origin(self):
        self.shell._gateway_origin = ("http", "127.0.0.1:37281")
        self.shell.window.get_current_url.return_value = self.host.runtime["url"] + "/"

    def test_browser_bridge_opens_only_current_owner_url_and_monitor_mode(self):
        self.browser_origin()
        url = "http://127.0.0.1:48172/?token=" + "a" * 32 + "#diagnostics"
        with patch("mirally.framework_shell.urllib.request.urlopen") as opener, \
             patch("mirally.framework_shell.webbrowser.open", return_value=True) as browser:
            opener.return_value.__enter__.return_value.read.return_value = json.dumps({"result": {"url": url}}).encode()
            self.assertEqual(self.shell.open_browser(url), {"opened": True})
            request = opener.call_args.args[0]
            self.assertEqual(request.full_url, self.host.runtime["url"] + "/api/rpc")
            self.assertEqual(json.loads(request.data), {"method": "activity.browse", "params": {"mode": "monitor"}})
            self.assertEqual(request.headers["Authorization"], "Bearer isolated-token")
            browser.assert_called_once_with(url)

    def test_browser_bridge_rejects_remote_file_userinfo_invalid_ports_and_shapes(self):
        self.browser_origin()
        token = "a" * 32
        invalid = ["https://example.org/", "file:///C:/private.txt", "http://localhost:48172/?token=" + token,
            "http://127.0.0.1:0/?token=" + token, "http://127.0.0.1:65536/?token=" + token,
            "http://127.0.0.1:not-a-port/?token=" + token, "http://user@127.0.0.1:48172/?token=" + token,
            "http://127.0.0.1:48172/api/run?token=" + token, "http://127.0.0.1:48172/?token=" + token + "&other=1",
            "http://127.0.0.1:48172/?token=short", "http://127.0.0.1:48172/?token=" + token + "#execute",
            "http://127.0.0.1:48172/", "http://127.0.0.1:48172/?token=" + token + "\n", None]
        with patch("mirally.framework_shell.urllib.request.urlopen") as opener, \
             patch("mirally.framework_shell.webbrowser.open") as browser:
            for url in invalid:
                with self.subTest(url=url), self.assertRaises(ValueError):
                    self.shell.open_browser(url)
            opener.assert_not_called()
            browser.assert_not_called()

    def test_browser_bridge_rejects_stale_port_or_retired_page(self):
        self.browser_origin()
        url = "http://127.0.0.1:48172/?token=" + "a" * 32
        with patch("mirally.framework_shell.urllib.request.urlopen") as opener, \
             patch("mirally.framework_shell.webbrowser.open") as browser:
            opener.return_value.__enter__.return_value.read.return_value = json.dumps({"result": {"url": url.replace("48172", "48173")}}).encode()
            with self.assertRaises(ValueError): self.shell.open_browser(url)
            browser.assert_not_called()
            self.shell.window.get_current_url.return_value = "https://untrusted.invalid/"
            opener.reset_mock()
            with self.assertRaises(ValueError): self.shell.open_browser(url)
            opener.assert_not_called()


if __name__ == "__main__":
    unittest.main()
