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
        self.shell.window.native.Hide.assert_called_once()
        self.assertFalse(self.shell.closed.is_set())
        self.host.close.assert_not_called()
        self.shell._quitting.set()
        self.assertTrue(self.shell._closing())

    def test_close_preference_is_read_live_and_exit_closes_even_with_a_tray(self):
        self.shell._tray_ready.set()
        self.host.home.mkdir(parents=True)
        preference = self.host.home / "ui-preferences.json"
        preference.write_text(json.dumps({"mirally.window.closeBehavior": "tray"}), encoding="utf-8")
        self.assertFalse(self.shell._closing())
        preference.write_text(json.dumps({"mirally.window.closeBehavior": "exit"}), encoding="utf-8")
        with patch("mirally.framework_shell.urllib.request.urlopen") as opener:
            self.assertTrue(self.shell._closing())
            # Native closing must not wait on HTTP or recursively destroy.
            opener.assert_not_called()
            self.shell.window.destroy.assert_not_called()
            self.host.close.assert_not_called()
            self.shell._close()
            opener.assert_called_once()
        self.host.close.assert_called_once_with()
        self.assertTrue(self.shell._quitting.is_set())
        self.assertTrue(self.shell.closed.is_set())

    def test_close_without_available_tray_exits_instead_of_hiding_an_unreachable_app(self):
        self.assertTrue(self.shell._closing())
        self.shell.window.native.Hide.assert_not_called()
        self.shell.window.destroy.assert_not_called()
        self.assertTrue(self.shell.closed.is_set())

    def test_invalid_preferences_fall_back_to_tray_without_breaking_close(self):
        self.shell._tray_ready.set()
        self.host.home.mkdir(parents=True)
        for raw in ("{broken", "[]", '{"mirally.window.closeBehavior":"unknown"}', " " * 65537):
            with self.subTest(raw=raw[:80]):
                (self.host.home / "ui-preferences.json").write_text(raw, encoding="utf-8")
                self.assertFalse(self.shell._closing())
                self.assertFalse(self.shell.closed.is_set())

    def show_modules(self, state="minimized"):
        form = self.shell.window.native
        form.IsDisposed = False
        form.Disposing = False
        form.InvokeRequired = True
        form.Invoke.side_effect = lambda callback: callback()
        form.WindowState = state
        return {"System": SimpleNamespace(Action=lambda callback: callback),
                "System.Windows.Forms": SimpleNamespace(FormWindowState=SimpleNamespace(
                    Minimized="minimized", Normal="normal", Maximized="maximized"))}

    def test_tray_companion_restores_hidden_window_before_docking(self):
        with patch.dict("sys.modules", self.show_modules()):
            self.shell._dock()
        form = self.shell.window.native
        form.Show.assert_called_once_with()
        form.Activate.assert_called_once_with()
        self.assertEqual(form.WindowState, "normal")
        self.assertIn("__mirallyCompanion?.dock()", self.shell.window.evaluate_js.call_args.args[0])

    def test_tray_restore_uses_ui_thread_and_preserves_companion_region_and_maximized_state(self):
        for state in ("normal", "maximized", "minimized"):
            with self.subTest(state=state), patch.dict("sys.modules", self.show_modules(state)):
                form = self.shell.window.native
                region = form.Region
                workspace = self.shell._workspace = object()
                self.shell._show_window()
                self.assertEqual(form.WindowState, "normal" if state == "minimized" else state)
                self.assertIs(form.Region, region)
                self.assertIs(self.shell._workspace, workspace)
                form.Invoke.assert_called()
                form.BringToFront.assert_called()
                form.Activate.assert_called()
                form.webview.Focus.assert_called()
        self.shell.window.evaluate_js.assert_not_called()

    def test_workspace_restore_uses_real_state_transitions_instead_of_reapplying_native_state_bits(self):
        normal, sidebar, maximized = (100, 120, 1280, 860), (1810, 180, 400, 680), (-8, -8, 2576, 1456)
        states = SimpleNamespace(Normal="Normal", Minimized="Minimized", Maximized="Maximized")
        class NativeForm:
            """WinForms keeps restored Bounds separate while non-normal."""
            def __init__(self):
                self._state, self._bounds = states.Normal, sidebar
                self.RestoreBounds = normal
                self.Region = MagicMock()
                self.webview = SimpleNamespace(DefaultBackgroundColor="companion")
                self.PerformLayout = MagicMock()
            @property
            def Bounds(self):
                return self._bounds
            @Bounds.setter
            def Bounds(self, value):
                self.RestoreBounds = value
                if self._state == states.Normal:
                    self._bounds = value
            @property
            def WindowState(self):
                return self._state
            @WindowState.setter
            def WindowState(self, value):
                if value != self._state:
                    if value == states.Maximized:
                        self._bounds = maximized
                    elif value == states.Minimized:
                        self._bounds = (-32000, -32000, normal[2], normal[3])
                    else:
                        self._bounds = self.RestoreBounds
                self._state = value
        modules = {"System.Drawing": SimpleNamespace(**{name: MagicMock() for name in ("Color", "Point", "Rectangle", "Region", "Size")}),
            "System.Drawing.Drawing2D": SimpleNamespace(GraphicsPath=MagicMock()),
            "System.Windows.Forms": SimpleNamespace(FormWindowState=states, Screen=MagicMock())}
        for state, bits, expected in ((states.Normal, 0, normal), (states.Maximized, 0x01000000, maximized),
                                      (states.Minimized, 0x20000000, (-32000, -32000, normal[2], normal[3]))):
            with self.subTest(state=state):
                form = self.shell.window.native = NativeForm()
                previous_region = form.Region
                self.shell._workspace = (normal, state, (640, 480), False, "workspace", 0x16cf0000 | bits, 2)
                self.shell._workspace_backcolor = "workspace-color"
                def frame_style(_form, style):
                    # Native SetWindowLong state bits can report a state
                    # without performing the corresponding bounds transition.
                    if style & 0x01000000:
                        form._state = states.Maximized
                    elif style & 0x20000000:
                        form._state = states.Minimized
                with patch.dict("sys.modules", modules), \
                     patch("mirally.control_ui_shell._frame_style", side_effect=frame_style), \
                     patch("mirally.control_ui_shell._frame_composition"), \
                     patch("mirally.control_ui_shell._client_glass"), \
                     patch("mirally.control_ui_shell._capture_affinity"):
                    self.shell._companion_action({"action": "workspace"})
                self.assertEqual(form.WindowState, state)
                self.assertEqual(form.Bounds, expected)
                self.assertEqual(form.RestoreBounds, normal)
                self.assertEqual(form.MinimumSize, (640, 480))
                self.assertFalse(form.TopMost)
                self.assertIsNone(form.Region)
                previous_region.Dispose.assert_called_once_with()
                self.assertIsNone(self.shell._workspace)

    def test_tray_left_click_finishes_restore_before_return_and_right_click_keeps_the_menu(self):
        modules = {"System.Windows.Forms": SimpleNamespace(MouseButtons=SimpleNamespace(Left="left"))}
        with patch.dict("sys.modules", modules), patch.object(self.shell, "_show_window") as restore, \
             patch.object(self.shell, "_dispatch_window_action") as dispatch:
            self.shell._tray_mouse_click(None, SimpleNamespace(Button="right"))
            restore.assert_not_called()
            self.shell._tray_mouse_click(None, SimpleNamespace(Button="left"))
            restore.assert_called_once_with()
            dispatch.assert_not_called()

    def test_late_queued_tray_restore_cannot_reopen_closed_or_disposed_window(self):
        with patch.dict("sys.modules", self.show_modules()):
            self.shell.window.native.IsDisposed = True
            self.shell._show_window()
            self.shell.window.native.Show.assert_not_called()
            self.shell.closed.set()
            self.shell.window.native.IsDisposed = False
            self.shell._show_window()
            self.shell.window.native.Show.assert_not_called()

    def test_quit_uses_authenticated_local_shutdown_for_reused_host(self):
        with patch("mirally.framework_shell.urllib.request.urlopen") as opener:
            self.shell.quit()
            self.shell.quit()
        request = opener.call_args.args[0]
        self.assertEqual(request.full_url, self.host.runtime["url"] + "/api/shutdown")
        self.assertEqual(request.headers["Authorization"], "Bearer isolated-token")
        self.shell.window.destroy.assert_called_once()
        self.assertTrue(self.shell._quitting.is_set())
        opener.assert_called_once()
        self.host.close.assert_called_once_with()

    def test_explicit_quit_does_not_deadlock_reentrant_native_close_callbacks(self):
        def destroy():
            self.assertTrue(self.shell._closing())
            self.shell._close()
        self.shell.window.destroy.side_effect = destroy
        with patch("mirally.framework_shell.urllib.request.urlopen", side_effect=OSError("already stopped")) as opener:
            self.shell.quit()
        self.host.close.assert_called_once_with()
        opener.assert_called_once()

    def test_reused_owner_quit_waits_for_its_completed_disposal_record(self):
        self.host.home.mkdir(parents=True)
        self.host.runtime["pid"] = 58213
        runtime_path = self.host.home / "runtime.json"
        runtime_path.write_text(json.dumps(self.host.runtime), encoding="utf-8")
        with patch("mirally.framework_shell.urllib.request.urlopen"), \
             patch("mirally.framework_shell.time.sleep", side_effect=lambda _: runtime_path.unlink()) as wait:
            self.shell.quit()
        wait.assert_called_once()
        self.assertFalse(runtime_path.exists())
        self.host.close.assert_called_once_with()

    def test_reused_owner_quit_never_waits_for_or_removes_a_replacement_owner(self):
        self.host.home.mkdir(parents=True)
        self.host.runtime["pid"] = 58213
        replacement = dict(self.host.runtime, pid=58214, token="replacement-token")
        runtime_path = self.host.home / "runtime.json"
        runtime_path.write_text(json.dumps(replacement), encoding="utf-8")
        with patch("mirally.framework_shell.urllib.request.urlopen"), \
             patch("mirally.framework_shell.time.sleep") as wait:
            self.shell.quit()
        wait.assert_not_called()
        self.assertEqual(json.loads(runtime_path.read_text(encoding="utf-8")), replacement)

    def test_reused_process_handle_is_captured_before_shutdown_and_waited_until_actual_exit(self):
        self.host.runtime["pid"] = 58213
        kernel, order = MagicMock(), []
        kernel.OpenProcess.side_effect = lambda *_: order.append("observe") or 735291
        kernel.WaitForSingleObject.side_effect = lambda *_: order.append("wait") or 0
        response = MagicMock()
        with patch("mirally.framework_shell.ctypes.WinDLL", return_value=kernel, create=True), \
             patch("mirally.framework_shell.urllib.request.urlopen", side_effect=lambda *_args, **_kwargs: order.append("shutdown") or response):
            self.shell._connect()
            self.shell.quit()
            self.shell._close()
        self.assertEqual(order, ["observe", "shutdown", "wait"])
        kernel.OpenProcess.assert_called_once_with(0x00100000, False, 58213)
        kernel.WaitForSingleObject.assert_called_once_with(735291, 90_000)
        kernel.CloseHandle.assert_called_once_with(735291)
        self.assertIsNone(self.shell._observed_host_exit)

    def test_process_observation_failure_keeps_authoritative_shutdown_fallback(self):
        self.host.runtime["pid"] = 58213
        with patch("mirally.framework_shell.ctypes.WinDLL", side_effect=OSError("not available"), create=True), \
             patch("mirally.framework_shell.urllib.request.urlopen") as opener:
            self.shell._connect()
            self.shell.quit()
        opener.assert_called_once()
        self.host.close.assert_called_once_with()
        self.assertIsNone(self.shell._observed_host_exit)

    def test_process_exit_wait_failure_still_releases_the_read_only_handle(self):
        self.host.runtime["pid"] = 58213
        kernel = MagicMock()
        kernel.OpenProcess.return_value = 735291
        kernel.WaitForSingleObject.side_effect = OSError("wait failed")
        with patch("mirally.framework_shell.ctypes.WinDLL", return_value=kernel, create=True), \
             patch("mirally.framework_shell.urllib.request.urlopen"), \
             self.assertLogs("mirally.framework_shell", level="WARNING"):
            self.shell._connect()
            self.shell.quit()
        kernel.CloseHandle.assert_called_once_with(735291)
        self.assertIsNone(self.shell._observed_host_exit)

    def test_process_observer_does_not_wait_for_a_changed_runtime_identity(self):
        self.host.runtime["pid"] = 58213
        kernel = MagicMock()
        kernel.OpenProcess.return_value = 735291
        with patch("mirally.framework_shell.ctypes.WinDLL", return_value=kernel, create=True), \
             patch("mirally.framework_shell.urllib.request.urlopen"):
            self.shell._connect()
            self.host.runtime = dict(self.host.runtime, pid=58214, token="replacement-owner")
            self.shell.quit()
        kernel.WaitForSingleObject.assert_not_called()
        kernel.CloseHandle.assert_called_once_with(735291)

    def test_quit_still_cleans_up_when_native_destroy_fails(self):
        self.shell.window.destroy.side_effect = RuntimeError("already disposed")
        with patch("mirally.framework_shell.urllib.request.urlopen"), self.assertRaises(RuntimeError):
            self.shell.quit()
        self.host.close.assert_called_once_with()

    def test_cleanup_closes_tray_and_host_once(self):
        tray = MagicMock()
        self.shell._tray = tray
        with patch("mirally.framework_shell.urllib.request.urlopen"):
            self.shell._close()
            self.shell._close()
        tray.Dispose.assert_called_once()
        self.host.close.assert_called_once()
        self.assertTrue(self.shell.closed.is_set())

    def test_close_while_starting_discards_late_navigation(self):
        self.host.start.side_effect = lambda: self.shell.closed.set()
        with patch("mirally.framework_shell.urllib.request.urlopen"):
            self.shell._connect()
        self.shell.window.load_url.assert_not_called()
        self.host.close.assert_called_once()

    def test_close_during_startup_shuts_down_late_reused_owner_without_navigating(self):
        runtime = self.host.runtime
        self.host.runtime = {}
        def late_start():
            self.shell._close()
            self.host.runtime = runtime
        self.host.start.side_effect = late_start
        with patch("mirally.framework_shell.urllib.request.urlopen") as opener:
            self.shell._connect()
        opener.assert_called_once()
        self.assertEqual(opener.call_args.args[0].full_url, runtime["url"] + "/api/shutdown")
        self.shell.window.load_url.assert_not_called()

    def test_finalizer_waits_for_async_startup_to_publish_and_close_a_reused_owner(self):
        runtime = self.host.runtime
        self.host.runtime = {}
        started, publish, initial_cleanup = threading.Event(), threading.Event(), threading.Event()
        errors = []
        def late_start():
            started.set()
            publish.wait(5)
            self.host.runtime = runtime
        def finish():
            try:
                self.shell._finish()
            except BaseException as error:
                errors.append(error)
        self.host.start.side_effect = late_start
        self.host.close.side_effect = initial_cleanup.set
        finalizer = threading.Thread(target=finish)
        with patch("mirally.framework_shell.urllib.request.urlopen") as opener:
            self.shell._start_connect()
            try:
                self.assertTrue(started.wait(2))
                finalizer.start()
                self.assertTrue(initial_cleanup.wait(2))
                self.assertTrue(finalizer.is_alive(), "Finalizer returned before the in-flight owner lookup")
                opener.assert_not_called()
                self.assertTrue(self.shell.closed.is_set())
                publish.set()
                finalizer.join(2)
                self.assertFalse(finalizer.is_alive(), "Finalizer did not finish after startup published its owner")
                opener.assert_called_once()
                self.assertEqual(opener.call_args.args[0].full_url, runtime["url"] + "/api/shutdown")
                self.assertFalse(any(worker.is_alive() for worker in self.shell._connect_workers))
                self.assertEqual(errors, [])
                self.shell.window.load_url.assert_not_called()
            finally:
                publish.set()
                if finalizer.ident is not None:
                    finalizer.join(2)
                for worker in self.shell._connect_workers:
                    worker.join(2)

    def test_tracked_retry_keeps_one_startup_owner_and_rejects_new_work_after_finish(self):
        started, release = threading.Event(), threading.Event()
        def start():
            started.set()
            release.wait(5)
        self.host.start.side_effect = start
        with patch("mirally.framework_shell.urllib.request.urlopen"):
            self.shell._start_connect()
            try:
                self.assertTrue(started.wait(2))
                self.shell._retry()
                self.shell._connect_workers[-1].join(2)
                self.host.start.assert_called_once_with()
            finally:
                self.shell.closed.set()
                release.set()
                self.shell._finish()
        self.shell._retry()
        self.shell._start_connect()
        self.host.start.assert_called_once_with()
        self.assertFalse(any(worker.is_alive() for worker in self.shell._connect_workers))

    def test_finalizer_cancels_a_pending_first_paint_wait_without_an_extra_animation_timeout(self):
        waiting = threading.Event()
        def wait(timeout):
            waiting.set()
            return threading.Event().wait(timeout)
        self.shell.window.events.loaded = SimpleNamespace(is_set=lambda: False, wait=wait)
        with patch("mirally.framework_shell.urllib.request.urlopen"):
            self.shell._start_connect()
            try:
                self.assertTrue(waiting.wait(2))
            finally:
                self.shell._finish()
        self.assertFalse(any(worker.is_alive() for worker in self.shell._connect_workers))
        self.shell.window.load_url.assert_not_called()

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
