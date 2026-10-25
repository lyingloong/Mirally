"""Mirally's original desktop experience over the owned framework Host.

Window geometry, clipping and explicit screen-intake gestures stay shared with
the established shell. No OpenClaw Host is created or contacted by this entry.
"""

from __future__ import annotations

import ctypes
import json
import logging
import re
import threading
import time
import urllib.request
import webbrowser
from urllib.parse import parse_qsl, urlsplit

from mirally.control_ui_shell import ASSETS, ControlUIShell, startup_html


class FrameworkShell(ControlUIShell):
    """Keep Mirally's native window and use the new product transport beneath it."""

    def __init__(self, host) -> None:
        # The base initializer only prepares native presentation state. Its
        # OpenClaw connection/brand/Activity methods are never used here.
        super().__init__(host.root)
        self.host = host
        self._quitting = threading.Event()
        self._tray_ready = threading.Event()
        self._tray = None
        self._cleanup_lock = threading.Lock()
        self._cleanup_done = False
        self._host_shutdown_lock = threading.Lock()
        self._shutdown_runtime = None
        self._observed_host_exit = None
        self._connect_workers_lock = threading.Lock()
        self._connect_workers = []
        self._companion_minimize_geometry = None
        self._native_resize_bound = False

    def _queue_connect(self, *, reload: bool = False) -> None:
        with self._connect_workers_lock:
            if self.closed.is_set():
                return
            self._connect_workers = [worker for worker in self._connect_workers if worker.is_alive()]
            worker = threading.Thread(target=self._connect, kwargs={"reload": reload},
                name="mirally-desktop-connect", daemon=True)
            self._connect_workers.append(worker)
            worker.start()

    def _start_connect(self) -> None:
        self._queue_connect()

    def _retry(self) -> None:
        self._queue_connect(reload=True)

    def _wait_for_presentation(self, event, timeout: float = 20) -> bool:
        deadline = time.monotonic() + timeout
        while not self.closed.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            if event.wait(min(.05, remaining)):
                return not self.closed.is_set()
        return False

    def _finish(self) -> None:
        """Retire startup/retry owners before the launcher can leave Python.

        Called after the UI loop returns, never from FormClosing. Host.start
        and Host.close keep their existing deadlines. A late reused owner
        must finish publication and _connect's shutdown finally block before
        daemon workers may be discarded by interpreter shutdown.
        """
        try:
            self._close()
        finally:
            with self._connect_workers_lock:
                workers = tuple(self._connect_workers)
            for worker in workers:
                if worker is not threading.current_thread():
                    worker.join()

    def _status(self, message: str, error: bool = False) -> None:
        if self.closed.is_set():
            return
        self._startup_status = (message, error)
        if self.window is None or not self.window.events.loaded.is_set():
            return
        if self.window.get_current_url() in {None, "about:blank"}:
            self.window.evaluate_js(
                f"window.setStartupStatus?.({json.dumps(message)}, {str(error).lower()})")

    def _connect(self, reload: bool = False) -> None:
        if not self._connecting.acquire(blocking=False):
            return
        try:
            if self.closed.is_set():
                return
            if reload:
                self._restore_workspace()
                self.window.load_html(startup_html())
            self._status("正在准备你的工作空间")
            self.host.start()
            self._observe_host_exit()
            if self.closed.is_set():
                return
            parts = urlsplit(self.host.runtime["url"])
            if parts.scheme != "http" or parts.hostname != "127.0.0.1":
                raise RuntimeError("本机工作空间返回了无效地址。")
            self._gateway_origin = (parts.scheme, parts.netloc)
            if (not self._wait_for_presentation(self.window.events.loaded)
                    or not self._wait_for_presentation(self._startup_visible)):
                return
            # First paint is the only presentation gate. A ready Host is never
            # held while a decorative animation finishes another cycle.
            self.window.load_url(self.host.url)
        except Exception as error:
            if not self.closed.is_set():
                self._status(str(error), error=True)
        finally:
            self._connecting.release()
            if self.closed.is_set():
                # Startup may have found a reused owner after the window was
                # already closed. It needs the same explicit shutdown signal.
                self._shutdown_host()

    def _restore_workspace(self) -> None:
        if self.window is not None and self._workspace is not None:
            from System import Action
            self.window.native.Invoke(Action(lambda: self._companion_action({"action": "workspace"})))

    def _navigate(self, page: str) -> None:
        if not isinstance(page, str) or page not in {"chat", "memory", "activity", "reminders", "inbox", "actions", "extensions", "settings", "work"}:
            return
        if self.closed.is_set() or self.window is None:
            return
        self._restore_workspace()
        # Native geometry and the companion's page mode are separate state.
        # Let its shared workspace transition finish before opening a page.
        self.window.evaluate_js(
            "(() => {"
            f"const navigate = () => window.__mirallyFramework?.showPage?.({json.dumps(page)});"
            "if (window.__mirallyCompanion?.workspace) window.__mirallyCompanion.workspace(navigate);"
            "else navigate();"
            "})()")

    def _activity_settings_page(self) -> None:
        self._navigate("activity")

    def _activity_browser(self) -> None:
        self._navigate("activity")

    def _memory_page(self) -> None:
        self._navigate("memory")

    def _browser(self) -> None:
        if self.host.runtime and not self.closed.is_set():
            webbrowser.open(self.host.url)

    def open_browser(self, url: str) -> dict[str, bool]:
        """Open only the current product owner's read-only Activity viewer."""
        if not isinstance(url, str) or len(url) > 4096 or any(character.isspace() or ord(character) < 32 or character == "\\" for character in url):
            raise ValueError("活动历史地址无效，请重新打开。")
        try:
            parts = urlsplit(url)
            port = parts.port
            query = parse_qsl(parts.query, keep_blank_values=True, strict_parsing=True)
            valid = (parts.scheme == "http" and parts.hostname == "127.0.0.1" and port is not None
                     and 1 <= port <= 65535 and parts.netloc == f"127.0.0.1:{port}"
                     and parts.username is None and parts.password is None and parts.path == "/"
                     and parts.fragment in {"", "diagnostics"} and len(query) == 1
                     and query[0][0] == "token" and re.fullmatch(r"[A-Za-z0-9_-]{24,128}", query[0][1]) is not None)
        except (TypeError, ValueError):
            valid = False
        if not valid:
            raise ValueError("只能打开当前 Mirally 的本机只读活动历史。")

        def current() -> bool:
            if self.closed.is_set() or self.window is None or self._gateway_origin is None:
                return False
            page = urlsplit(self.window.get_current_url() or "")
            return (page.scheme, page.netloc) == self._gateway_origin

        if not current():
            raise ValueError("请在当前 Mirally 工作空间中重新打开活动历史。")
        mode = "monitor" if parts.fragment == "diagnostics" else "history"
        runtime = self.host.runtime
        request = urllib.request.Request(runtime["url"] + "/api/rpc",
            data=json.dumps({"method": "activity.browse", "params": {"mode": mode}}).encode("utf-8"),
            headers={"Authorization": "Bearer " + runtime["token"], "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                raw = response.read(65537)
            if len(raw) > 65536:
                raise ValueError("Activity browser response exceeded its limit")
            result = json.loads(raw)
            expected = result.get("result", {}).get("url")
        except (OSError, ValueError, TypeError, AttributeError) as error:
            raise RuntimeError("暂时无法核对活动历史地址，请在工作空间中重新打开。") from error
        # The authoritative owner may have changed profile or retired its old
        # server while the UI awaited this click. Never open an old/other port.
        if not current() or expected != url:
            raise ValueError("活动历史地址已更新，请在当前工作空间重新打开。")
        if not webbrowser.open(url):
            raise RuntimeError("未能打开本机活动历史，请检查默认浏览器。")
        return {"opened": True}

    def _show_window(self) -> None:
        if self.closed.is_set() or self.window is None:
            return
        from System import Action
        from System.Windows.Forms import FormWindowState
        form = self.window.native

        def show():
            if self.closed.is_set() or form.IsDisposed or form.Disposing:
                return
            # Keep the existing workspace/companion Region and maximized
            # state. Only a genuinely minimized form needs restoration.
            if form.WindowState == FormWindowState.Minimized and self._workspace is not None:
                self._remember_companion_minimize_geometry()
            form.Show()
            if form.WindowState == FormWindowState.Minimized:
                form.WindowState = FormWindowState.Normal
            self._restore_companion_minimize_geometry()
            form.BringToFront()
            form.Activate()
            form.webview.Focus()

        if form.InvokeRequired:
            form.Invoke(Action(show))
        else:
            show()

    def _remember_companion_minimize_geometry(self) -> None:
        """Save native outer bounds before WinForms restores its cached frame."""
        current = self._companion_minimize_geometry
        if self._workspace is not None and (current is None or current[0] is not self._workspace):
            self._companion_minimize_geometry = (self._workspace, self.window.native.RestoreBounds)

    def _restore_companion_minimize_geometry(self, expected=None) -> None:
        """Finish both tray and taskbar restore on the original clipped HWND.

        Companion chrome is removed through the native style so WebView2 keeps
        its HWND. WinForms still caches a sizable frame and can add that frame
        back to Bounds when restoring its client-size bookkeeping. Reapply the
        saved outer rectangle after that transition, once per minimize cycle.
        """
        from System.Windows.Forms import FormWindowState
        pending = self._companion_minimize_geometry
        if pending is None or (expected is not None and pending is not expected):
            return
        form = self.window.native
        if (self.closed.is_set() or form.IsDisposed or form.Disposing
                or self._workspace is not pending[0]):
            self._companion_minimize_geometry = None
            return
        if form.WindowState != FormWindowState.Normal:
            return  # A second minimize may have overtaken the queued callback.
        self._companion_minimize_geometry = None
        if form.Bounds != pending[1]:
            form.Bounds = pending[1]

    def _native_resize(self, _sender, _args) -> None:
        from System import Action
        from System.Windows.Forms import FormWindowState
        form = self.window.native
        if self.closed.is_set() or form.IsDisposed or form.Disposing or self._workspace is None:
            self._companion_minimize_geometry = None
            return
        if form.WindowState == FormWindowState.Minimized:
            self._remember_companion_minimize_geometry()
        elif form.WindowState == FormWindowState.Normal and self._companion_minimize_geometry is not None:
            expected = self._companion_minimize_geometry
            # WM_SIZE/Resize occurs before Form's final cached-bounds restore.
            # Run after that stack returns; never resize on animation frames.
            form.BeginInvoke(Action(lambda: self._restore_companion_minimize_geometry(expected)))

    def _tray_mouse_click(self, _sender, args) -> None:
        from System.Windows.Forms import MouseButtons
        if args.Button == MouseButtons.Left:
            # NotifyIcon already raises this event on the UI thread. Restoring
            # is native-only, so finish it before returning from the gesture;
            # a queued worker can otherwise restore over a subsequent minimize.
            try:
                self._show_window()
            except Exception:
                logging.getLogger(__name__).exception("Tray window restore failed")

    def _dock(self) -> None:
        # A tray action can arrive while the original HWND is hidden.
        self._show_window()
        if not self.closed.is_set():
            super()._dock()

    def quit(self) -> None:
        """Exit the native shell and its owner, including a reused Host."""
        if self._quitting.is_set():
            return
        self._quitting.set()
        self.closed.set()
        try:
            if self.window is not None:
                self.window.destroy()
        finally:
            self._close()

    def _close_behavior(self) -> str:
        """Read the product preference at the gesture, without network/UI calls."""
        try:
            with (self.host.home / "ui-preferences.json").open(encoding="utf-8") as stream:
                raw = stream.read(65537)
            preferences = json.loads(raw) if len(raw) <= 65536 else {}
            if isinstance(preferences, dict) and preferences.get("mirally.window.closeBehavior") == "exit":
                return "exit"
        except (OSError, ValueError):
            pass
        return "tray"

    def _closing(self) -> bool:
        if not self._quitting.is_set() and self._tray_ready.is_set() and self._close_behavior() == "tray":
            self.window.native.Hide()
            return False
        # FormClosing is synchronous on the UI thread. Do not destroy this
        # same form recursively or wait for a Host/network call here.
        self._quitting.set()
        self.closed.set()
        return True

    def _observe_host_exit(self) -> None:
        """Keep a read-only handle to the reused process before showing its UI."""
        if self.host.process is not None:
            return  # The supervising launcher already owns a Popen exit handle.
        runtime = self.host.runtime
        identity = (runtime.get("pid"), runtime.get("url"), runtime.get("token"))
        if type(identity[0]) is not int or identity[0] <= 0 or not all(identity):
            return
        loader = getattr(ctypes, "WinDLL", None)
        if loader is None:
            return  # This native shell is Windows-only; non-GUI tests are portable.
        with self._host_shutdown_lock:
            if self._observed_host_exit is not None:
                previous, kernel, handle = self._observed_host_exit
                if previous == identity:
                    return
                self._observed_host_exit = None
                kernel.CloseHandle(handle)
            try:
                kernel = loader("kernel32", use_last_error=True)
                kernel.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
                kernel.OpenProcess.restype = ctypes.c_void_p
                kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
                kernel.WaitForSingleObject.restype = ctypes.c_uint32
                kernel.CloseHandle.argtypes = [ctypes.c_void_p]
                kernel.CloseHandle.restype = ctypes.c_int
                handle = kernel.OpenProcess(0x00100000, False, identity[0])  # SYNCHRONIZE only.
                if handle:
                    self._observed_host_exit = (identity, kernel, handle)
            except OSError:
                logging.getLogger(__name__).debug("Reused Host exit observation unavailable", exc_info=True)

    def _wait_for_observed_host_exit(self, runtime) -> None:
        observed, self._observed_host_exit = self._observed_host_exit, None
        if observed is None:
            return
        identity, kernel, handle = observed
        try:
            current = (runtime.get("pid"), runtime.get("url"), runtime.get("token"))
            if current == identity:
                # A retained process handle denotes that exact process even if
                # Windows later reuses its PID. Never terminate a reused owner.
                if kernel.WaitForSingleObject(handle, 90_000) != 0:
                    logging.getLogger(__name__).warning("Mirally Host process exit is still pending; inspect the product host log")
        except OSError:
            logging.getLogger(__name__).warning("Mirally Host process exit could not be observed", exc_info=True)
        finally:
            kernel.CloseHandle(handle)

    def _shutdown_host(self) -> None:
        with self._host_shutdown_lock:
            runtime = self.host.runtime
            identity = (runtime.get("url"), runtime.get("token"))
            if all(identity) and identity != self._shutdown_runtime:
                self._shutdown_runtime = identity
                request = urllib.request.Request(identity[0] + "/api/shutdown", data=b"{}",
                    headers={"Authorization": "Bearer " + identity[1], "Content-Type": "application/json"})
                try:
                    with urllib.request.urlopen(request, timeout=2):
                        pass
                except (OSError, ValueError):
                    pass  # The page may already have requested this shutdown.
            if all(identity) and self.host.process is None:
                # A reused Host has no Popen handle in this launcher. Its
                # authoritative runtime is retired only after agents, Python
                # worker and HTTP server finish disposal. Wait for that exact
                # owner; never stop or wait on a replacement process/PID.
                deadline = time.monotonic() + 90
                runtime_path = self.host.home / "runtime.json"
                while time.monotonic() < deadline:
                    try:
                        current = json.loads(runtime_path.read_text(encoding="utf-8"))
                    except (OSError, ValueError):
                        break
                    if not isinstance(current, dict) or any(current.get(key) != runtime.get(key) for key in ("pid", "url", "token")):
                        break
                    time.sleep(.05)
                else:
                    logging.getLogger(__name__).warning("Mirally Host shutdown is still pending; inspect the product host log")
            # An owned process is waited for and has its stdin/log released;
            # the HTTP request above is also necessary for a reused owner.
            try:
                self.host.close()
            finally:
                self._wait_for_observed_host_exit(runtime)

    def _close(self) -> None:
        with self._cleanup_lock:
            if self._cleanup_done:
                return
            self._cleanup_done = True
            self.closed.set()
            self._companion_minimize_geometry = None
            self._context_request = None
            try:
                if self._tray is not None:
                    self._tray.Visible = False
                    self._tray.Dispose()
                    self._tray = None
                if self._system_menu is not None:
                    self._system_menu.close()
            finally:
                self._shutdown_host()

    def _native_style(self) -> None:
        from System import Action
        from System.Drawing import Icon
        from mirally.platform.window_menu import WindowSystemMenu
        form = self.window.native

        def apply():
            form.Opacity = 0
            form.Text = ""
            form.Icon = Icon(str(ASSETS / "mirally.ico"))
            form.ShowIcon = True
            form.ShowInTaskbar = True
            if not self._native_resize_bound:
                form.Resize += self._native_resize
                self._native_resize_bound = True
            form.webview.WebMessageReceived -= form.browser.on_script_notify
            form.webview.WebMessageReceived += self._web_message
            if self._system_menu is None:
                menu = WindowSystemMenu(int(form.Handle.ToInt64()), [
                    ("侧边伙伴", self._dock),
                    ("工作", lambda: self._navigate("work")),
                    ("重新连接", self._retry),
                    ("活动与记忆设置", self._activity_settings_page),
                    ("记忆", self._memory_page),
                    ("在浏览器中打开", self._browser),
                    ("退出 Mirally", self.quit),
                    None,
                ], self._dispatch_window_action)
                menu.install()
                self._system_menu = menu
        form.Invoke(Action(apply))

    def _install_tray(self) -> None:
        from System import Action
        from System.Drawing import Icon
        from System.Windows.Forms import ContextMenuStrip, NotifyIcon, ToolStripMenuItem

        def install():
            icon = NotifyIcon()
            icon.Icon = Icon(str(ASSETS / "mirally.ico"))
            icon.Text = "Mirally · 单击打开，右键退出"
            menu = ContextMenuStrip()
            for label, action in (("打开 Mirally", self._show_window),
                                  ("侧边伙伴", self._dock), ("退出 Mirally", self.quit)):
                item = ToolStripMenuItem(label)
                item.Click += lambda *_, action=action: self._dispatch_window_action(action)
                menu.Items.Add(item)
            icon.ContextMenuStrip = menu
            icon.MouseClick += self._tray_mouse_click
            icon.Visible = True
            self._tray = icon
            self._tray_ready.set()
        try:
            self.window.native.Invoke(Action(install))
        except Exception:
            logging.getLogger(__name__).exception("Tray unavailable; closing the window will exit")

    def _web_message(self, sender, args) -> None:
        try:
            raw = str(args.get_WebMessageAsJson())
            if len(raw) > 65536:
                return
            value = json.loads(raw)
        except (TypeError, ValueError):
            return
        source = str(args.Source)
        if value == {"type": "mirally-startup-painted"}:
            if source == "about:blank" and not self.closed.is_set() and not self._startup_visible.is_set():
                self.window.native.Opacity = 1
                self._startup_visible.set()
            return
        if self.closed.is_set():
            return
        parts = urlsplit(source)
        local = (parts.scheme, parts.netloc) == self._gateway_origin
        if isinstance(value, dict) and local:
            kind = value.get("type")
            if kind == "mirally-companion":
                self._companion_action(value)
                return
            if kind == "mirally-framework":
                action = value.get("action")
                if action == "quit":
                    self._dispatch_window_action(self.quit)
                elif action == "retry":
                    self._retry()
                elif action == "show-page":
                    self._dispatch_window_action(lambda: self._navigate(value.get("page")))
                return
        # Keep the WebView's own file/drop/JS bridge protocol. A remote page may
        # not invoke it through navigation; startup can initialize its bridge.
        if (local or source == "about:blank") and (value == "FilesDropped" or isinstance(value, list) and len(value) == 3):
            self.window.native.browser.on_script_notify(sender, args)

    def _watch_host(self) -> None:
        while not self.closed.wait(1):
            process = self.host.process
            if process is not None and process.poll() is not None:
                self._quitting.set()
                if self.window is not None:
                    self.window.destroy()
                return

    def run(self) -> None:
        try:
            import webview
        except ImportError:
            raise SystemExit("请在项目虚拟环境安装 requirements-framework.txt。") from None
        logging.getLogger("pywebview").setLevel(logging.WARNING)
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("MIRALab.Mirally")
        webview.settings["ALLOW_DOWNLOADS"] = True
        webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True
        shell = self
        class Bridge:
            def quit(self):
                shell.quit()

            def open_browser(self, url):
                return shell.open_browser(url)

        self.window = webview.create_window("Mirally", html=startup_html(), js_api=Bridge(),
            width=1280, height=860, min_size=(640, 480), background_color="#0d1520",
            text_select=True, zoomable=True, easy_drag=False)
        self.window.events.before_show += self._native_style
        self.window.events.loaded += self._browser_ready
        self.window.events.shown += self._install_tray
        self.window.events.closing += self._closing
        self.window.events.closed += self._close
        self._start_connect()
        threading.Thread(target=self._watch_host, name="mirally-desktop-host-watch", daemon=True).start()
        try:
            webview.start(gui="edgechromium", private_mode=False, storage_path=str(self.host.home / "webview"))
        except webview.WebViewException:
            raise SystemExit("无法启动 WebView2，请检查 Microsoft Edge WebView2 Runtime 和 requirements-framework.txt。") from None
        finally:
            self._finish()
