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
        self._frontend_lock = threading.Lock()

    def _set_editing(self, value: bool) -> None:
        # WebView messages run on the UI thread; pywebview API calls use
        # workers. Both can report the same focus change concurrently.
        with self._frontend_lock:
            if not self.closed.is_set() and self._frontend_registration is not None:
                self._frontend_registration.update(value)

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
            if self.closed.is_set():
                return
            parts = urlsplit(self.host.runtime["url"])
            if parts.scheme != "http" or parts.hostname != "127.0.0.1":
                raise RuntimeError("本机工作空间返回了无效地址。")
            self._gateway_origin = (parts.scheme, parts.netloc)
            if (not self.window.events.loaded.wait(20)
                    or not self._startup_visible.wait(20) or self.closed.is_set()):
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
                self.host.close()

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
        self.window.evaluate_js(
            f"window.__mirallyFramework?.showPage?.({json.dumps(page)})")

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
        if not self.closed.is_set() and self.window is not None:
            self.window.show()
            self.window.restore()

    def _dock(self) -> None:
        # A tray action can arrive while the original HWND is hidden.
        self._show_window()
        if not self.closed.is_set():
            super()._dock()

    def quit(self) -> None:
        """Explicit quit also closes a reused Host, unlike closing its window."""
        if self._quitting.is_set():
            return
        self._quitting.set()
        runtime = self.host.runtime
        if runtime.get("url") and runtime.get("token"):
            request = urllib.request.Request(runtime["url"] + "/api/shutdown", data=b"{}",
                headers={"Authorization": "Bearer " + runtime["token"], "Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(request, timeout=2):
                    pass
            except (OSError, ValueError):
                pass  # The page may already have requested this same shutdown.
        if self.window is not None:
            self.window.destroy()

    def _closing(self) -> bool:
        self._set_editing(False)
        if not self._quitting.is_set() and self._tray_ready.is_set():
            self.window.hide()
            return False
        return True

    def _close(self) -> None:
        with self._cleanup_lock:
            if self._cleanup_done:
                return
            self._cleanup_done = True
            self.closed.set()
            self._context_request = None
            try:
                if self._tray is not None:
                    self._tray.Visible = False
                    self._tray.Dispose()
                    self._tray = None
                if self._system_menu is not None:
                    self._system_menu.close()
                with self._frontend_lock:
                    if self._frontend_registration is not None:
                        self._frontend_registration.close()
                        self._frontend_registration = None
            finally:
                self.host.close()

    def _native_style(self) -> None:
        from System import Action
        from System.Drawing import Icon
        from mirally.platform.frontend_registry import FrontendRegistration
        from mirally.platform.window_menu import WindowSystemMenu
        form = self.window.native

        def apply():
            if self._frontend_registration is None:
                self._frontend_registration = FrontendRegistration(self.root)
            form.Opacity = 0
            form.Text = ""
            form.Icon = Icon(str(ASSETS / "mirally.ico"))
            form.ShowIcon = True
            form.ShowInTaskbar = True
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
            icon.Text = "Mirally · 关闭窗口后继续运行"
            menu = ContextMenuStrip()
            for label, action in (("打开 Mirally", self._show_window),
                                  ("侧边伙伴", self._dock), ("退出 Mirally", self.quit)):
                item = ToolStripMenuItem(label)
                item.Click += lambda *_, action=action: self._dispatch_window_action(action)
                menu.Items.Add(item)
            icon.ContextMenuStrip = menu
            icon.DoubleClick += lambda *_: self._dispatch_window_action(self._show_window)
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
            if kind == "mirally-ufo-input":
                if type(value.get("editing")) is bool and self._frontend_registration is not None:
                    self._set_editing(value["editing"])
                return
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
            def editing(self, value=False):
                if type(value) is bool:
                    shell._set_editing(value)

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
            self._close()
