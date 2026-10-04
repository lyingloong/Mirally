"""A branded WebView2 host for the official OpenClaw Control UI."""
from __future__ import annotations

import base64
import ctypes
import json
import logging
import math
from pathlib import Path
import threading
import time
import webbrowser
from urllib.parse import urlsplit

from openassist.openclaw_host import HostError, OpenClawHost
from openassist.control_ui_brand import brand_script, startup_message


ASSETS = Path(__file__).resolve().parent / "assets"


def _frame_style(form, style: int | None = None) -> int:
    """Change chrome on the existing HWND; recreating it breaks WebView2 clips."""
    user = ctypes.windll.user32
    get = user.GetWindowLongPtrW
    get.argtypes, get.restype = [ctypes.c_void_p, ctypes.c_int], ctypes.c_ssize_t
    hwnd = int(form.Handle.ToInt64())
    previous = int(get(hwnd, -16))
    if style is not None:
        put = user.SetWindowLongPtrW
        put.argtypes, put.restype = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t], ctypes.c_ssize_t
        put(hwnd, -16, style)
        user.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint]
        user.SetWindowPos(hwnd, None, 0, 0, 0, 0, 0x0037)  # FRAMECHANGED | NOACTIVATE | NOZORDER | NOMOVE | NOSIZE
    return previous


def _frame_composition(form, policy: int | None = None) -> int:
    """DWM's automatic frame otherwise paints outside a custom window region."""
    dwm = ctypes.windll.dwmapi
    hwnd = int(form.Handle.ToInt64())
    get, put = dwm.DwmGetWindowAttribute, dwm.DwmSetWindowAttribute
    get.argtypes = put.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint]
    previous = ctypes.c_int(2)
    get(hwnd, 2, ctypes.byref(previous), 4)
    if policy is not None:
        value = ctypes.c_int(policy)
        put(hwnd, 2, ctypes.byref(value), 4)
    return previous.value


def _capture_affinity(form, exclude: bool) -> None:
    # Supported Windows versions can omit our own floating chrome from capture.
    api = ctypes.windll.user32.SetWindowDisplayAffinity
    api.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    api(int(form.Handle.ToInt64()), 0x11 if exclude else 0)


def startup_html() -> str:
    # Use the canonical vector's production PNG export. The SVG master is over
    # WebView2 NavigateToString's 2 MB limit and expensive to animate as an image.
    logo = base64.b64encode((ASSETS / "mirally-512.png").read_bytes()).decode("ascii")
    return (ASSETS / "control-ui-startup.html").read_text(encoding="utf-8").replace("__MIRALLY_LOGO__", logo)


class ControlUIShell:
    def __init__(self, root: Path):
        self.root = root
        self.window = None
        self.host = None
        self.closed = threading.Event()
        self._connecting = threading.Lock()
        self._brand_id = None
        self._gateway_origin = None
        self._workspace = None
        self._shape_key = None
        self._surface_mode = 'badge'
        self._context_request = None
        self._startup_status = None

    def _status(self, message: str, error: bool = False) -> None:
        if self.closed.is_set():
            return
        self._startup_status = (message, error)
        if self.window is None or not self.window.events.loaded.is_set():
            return
        # Internal connection vocabulary stays out of the product startup.
        if self.window.get_current_url() in {None, "about:blank"}:
            display = message if error else startup_message(message)
            self.window.evaluate_js(f"window.setStartupStatus?.({json.dumps(display)}, {str(error).lower()})")

    def _connect(self, reload: bool = False) -> None:
        if not self._connecting.acquire(blocking=False):
            return
        try:
            if self.closed.is_set():
                return
            if reload:
                if self._workspace is not None:
                    from System import Action
                    self.window.native.Invoke(Action(lambda: self._companion_action({'action': 'workspace'})))
                self.window.load_html(startup_html())
            started = time.monotonic()
            if self.host is None:
                self.host = OpenClawHost(self.root)
            url = self.host.connect(self._status)
            if not self.window.events.loaded.wait(20) or self.closed.is_set():
                return
            if not self.closed.is_set():
                self._install_brand(url)
            # Give the brand a brief entrance; never hold a ready app for a long
            # decorative animation. Any actual wait above has already counted.
            self.closed.wait(max(0, 1.1 - (time.monotonic() - started)))
            if not self.closed.is_set():
                self.window.load_url(url)
        except HostError as error:
            self._status(str(error), error=True)
        finally:
            self._connecting.release()

    def _install_brand(self, url: str) -> None:
        """Apply the skin at document creation, before the native UI paints."""
        from System import Action, String
        from System.Threading.Tasks import Task
        ready = threading.Event()
        result = {}
        source = brand_script(url)
        parts = urlsplit(url)
        self._gateway_origin = (parts.scheme, parts.netloc)

        def register():
            view = self.window.native.webview.CoreWebView2
            if self._brand_id:
                view.RemoveScriptToExecuteOnDocumentCreated(self._brand_id)
            task = view.AddScriptToExecuteOnDocumentCreatedAsync(source)

            def completed(task):
                if task.IsFaulted or task.IsCanceled:
                    result['error'] = True
                else:
                    result['id'] = str(task.Result)
                ready.set()

            task.ContinueWith(Action[Task[String]](completed))

        self.window.native.Invoke(Action(register))
        if not ready.wait(10) or result.get('error'):
            raise HostError("工作空间外观未能加载，请重新连接。")
        self._brand_id = result['id']

    def _retry(self) -> None:
        threading.Thread(target=self._connect, kwargs={"reload": True}, daemon=True).start()

    def _start_connect(self) -> None:
        threading.Thread(target=self._connect, daemon=True).start()

    def _browser(self) -> None:
        def open_native():
            try:
                if self.host:
                    # A handoff is single-use, so obtain a fresh one for browser.
                    webbrowser.open(self.host.dashboard())
            except HostError:
                self.window.create_confirmation_dialog("Mirally", "原生界面暂不可用，请先通过 Mirally 菜单重新连接。")
        threading.Thread(target=open_native, daemon=True).start()

    def _close(self) -> None:
        self.closed.set()
        if self.host:
            self.host.close()

    def _native_style(self) -> None:
        # pywebview's icon= is GTK/Qt only. Set the icon on our own WinForms
        # window on its UI thread. Do this once, before the window is displayed.
        from System import Action
        from System.Drawing import Icon
        form = self.window.native

        def apply():
            form.Icon = Icon(str(ASSETS / "mirally.ico"))
            form.ShowIcon = True
            form.ShowInTaskbar = True
            # The official UI emits a WebView2 {type:"ready"} notification for
            # native hosts. pywebview expects its own three-element messages;
            # route only those to it, without changing the native webpage.
            form.webview.WebMessageReceived -= form.browser.on_script_notify
            form.webview.WebMessageReceived += self._web_message

        form.Invoke(Action(apply))

    def _web_message(self, sender, args) -> None:
        try:
            value = json.loads(str(args.get_WebMessageAsJson()))
        except (TypeError, ValueError):
            return
        if isinstance(value, dict) and value.get('type') == 'mirally-companion':
            parts = urlsplit(str(args.Source))
            if (parts.scheme, parts.netloc) == self._gateway_origin:
                self._companion_action(value)
            return
        if value == "FilesDropped" or isinstance(value, list) and len(value) == 3:
            self.window.native.browser.on_script_notify(sender, args)

    def _companion_action(self, message: dict) -> None:
        """Window-only messages from our authenticated, same-origin presentation.

        Keep the WebView fixed while its CSS panel grows; clip the host to that
        panel, rather than resizing and recapturing its contents every frame.
        """
        from System.Drawing import Color, Rectangle, Region, Size
        from System.Drawing.Drawing2D import GraphicsPath
        from System.Windows.Forms import FormWindowState, Screen
        form = self.window.native
        action = message.get('action')
        if action == 'dock':
            if self._workspace is not None:
                return
            self._workspace = (form.RestoreBounds if form.WindowState != FormWindowState.Normal else form.Bounds,
                               form.WindowState, form.MinimumSize, form.TopMost,
                               form.webview.DefaultBackgroundColor, _frame_style(form), _frame_composition(form))
            form.WindowState = FormWindowState.Normal
            form.MinimumSize = Size(0, 0)
            _frame_style(form, _frame_style(form) & ~0x00C40000)
            _frame_composition(form, 1)
            _capture_affinity(form, True)
            form.webview.DefaultBackgroundColor = Color.Transparent
            if form.MainMenuStrip:
                form.MainMenuStrip.Visible = False
            area = Screen.FromControl(form).WorkingArea
            scale = form.DeviceDpi / 96
            width = min(round(400 * scale), area.Width)
            height = min(round(680 * scale), area.Height - 24)
            form.Bounds = Rectangle(area.Right - width - 12, area.Top + (area.Height - height) // 2, width, height)
            form.ShowInTaskbar = True
            form.TopMost = True
            self._shape_key = None
            self._initial_companion_shape(message)
        elif action == 'workspace' and self._workspace is not None:
            bounds, state, minimum, topmost, background, style, composition = self._workspace
            self._workspace = None
            self._shape_key = None
            previous = form.Region
            form.Region = None
            if previous:
                previous.Dispose()
            _frame_style(form, style)
            _frame_composition(form, composition)
            _capture_affinity(form, False)
            form.MinimumSize = minimum
            form.Bounds = bounds
            if form.MainMenuStrip:
                form.MainMenuStrip.Visible = True
            form.WindowState = state
            form.TopMost = topmost
            form.webview.DefaultBackgroundColor = background
        elif action == 'surface' and self._workspace is not None:
            self._initial_companion_shape(message)
        elif action == 'drag' and self._workspace is not None:
            # Only the webpage's real pointer gesture initiates a native move.
            user = ctypes.windll.user32
            user.ReleaseCapture()
            user.PostMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
            user.PostMessageW(int(form.Handle.ToInt64()), 0x00A1, 2, 0)  # WM_NCLBUTTONDOWN / HTCAPTION
        elif action == 'minimize':
            form.WindowState = FormWindowState.Minimized
        elif action == 'capture-target':
            self._capture_target(message.get('requestId'))
        elif action == 'shape' and self._workspace is not None:
            rect, viewport = message.get('rect'), message.get('viewport')
            if not isinstance(rect, list) or len(rect) != 4 or not isinstance(viewport, list) or len(viewport) != 2:
                return
            if any(not isinstance(v, (int, float)) or not math.isfinite(v) for v in rect + viewport):
                return
            x, y, width, height = rect
            vw, vh = viewport
            if vw <= 0 or vh <= 0 or width <= 0 or height <= 0 or x < 0 or y < 0 or x + width > vw + 1 or y + height > vh + 1:
                return
            sx, sy = form.ClientSize.Width / vw, form.ClientSize.Height / vh
            # An old workspace viewport can arrive while docking. WebView zoom
            # and DPI scale uniformly; unequal axes identify stale resize geometry.
            if not math.isclose(sx, sy, rel_tol=0.05):
                return
            left, top, right, bottom = round(x * sx), round(y * sy), round((x + width) * sx), round((y + height) * sy)
            key = (left, top, right, bottom)
            if key == self._shape_key or right <= left or bottom <= top:
                return
            self._shape_key = key
            corner = 32 if self._surface_mode == 'badge' and width <= 80 and height <= 80 else 25
            radius = min(round(corner * sx), (right - left) // 2, (bottom - top) // 2)
            diameter = radius * 2
            path = GraphicsPath()
            if diameter < 2:
                path.AddRectangle(Rectangle(left, top, right-left, bottom-top))
            else:
                for px, py, angle in ((left, top, 180), (right-diameter, top, 270),
                                      (right-diameter, bottom-diameter, 0), (left, bottom-diameter, 90)):
                    path.AddArc(px, py, diameter, diameter, angle, 90)
            path.CloseFigure()
            previous = form.Region
            form.Region = Region(path)
            path.Dispose()
            if previous:
                previous.Dispose()

    def _initial_companion_shape(self, message: dict) -> None:
        form = self.window.native
        requested = message.get('surfaceMode')
        if requested in {'rail', 'badge', 'pet'}:
            self._surface_mode = requested
        scale = form.DeviceDpi / 96
        vw, vh = form.ClientSize.Width / scale, form.ClientSize.Height / scale
        width, height = (376, 560) if message.get('expanded') is True else {
            'rail': (80, 300), 'badge': (64, 64), 'pet': (80, 96),
        }[self._surface_mode]
        width, height = min(width, vw - 24), min(height, vh - 32)
        # Position-only viewport changes do not trigger ResizeObserver.
        self._companion_action({'action': 'shape', 'expanded': message.get('expanded'),
            'viewport': [vw, vh], 'rect': [vw - 12 - width, (vh - height) / 2, width, height]})
        form.webview.CoreWebView2.ExecuteScriptAsync(
            "requestAnimationFrame(() => window.__mirallyCompanion?.syncShape?.())"
        )

    def _capture_target(self, request_id) -> None:
        if not isinstance(request_id, str) or not 0 < len(request_id) <= 128:
            return
        from ctypes import wintypes
        from System import Action
        form = self.window.native
        hwnd = int(form.Handle.ToInt64())
        point = wintypes.POINT()
        if not ctypes.windll.user32.GetCursorPos(ctypes.byref(point)):
            return
        self._context_request = request_id
        position = (point.x, point.y)

        def capture():
            from openassist.desktop_context import capture_target, DesktopContextError
            try:
                result = capture_target(own_hwnd=hwnd, screen_point=position)
            except DesktopContextError as error:
                result = {'error': str(error)}
            except Exception:
                result = {'error': '暂时无法读取目标窗口，请重试或添加文件。'}
            result['requestId'] = request_id

            def deliver():
                if self.closed.is_set() or self._context_request != request_id:
                    return
                view = form.webview.CoreWebView2
                parts = urlsplit(str(view.Source))
                if (parts.scheme, parts.netloc) != self._gateway_origin:
                    return
                view.ExecuteScriptAsync(f"window.__mirallyContext?.receive({json.dumps(result)})")
            try:
                if not self.closed.is_set():
                    form.BeginInvoke(Action(deliver))
            except Exception:
                pass  # Closing the native window retires the pending handoff.
        threading.Thread(target=capture, daemon=True).start()

    def _dock(self) -> None:
        self.window.evaluate_js("window.__mirallyCompanion?.dock()")

    def _browser_ready(self) -> None:
        if self.closed.is_set():
            return
        from System import Action

        def apply():
            settings = self.window.native.webview.CoreWebView2.Settings
            settings.AreDefaultContextMenusEnabled = True
            settings.AreBrowserAcceleratorKeysEnabled = True

        self.window.native.Invoke(Action(apply))
        if self._startup_status:
            self._status(*self._startup_status)

    def run(self) -> None:
        try:
            import webview
            from webview.menu import Menu, MenuAction, MenuSeparator
        except ImportError:
            raise SystemExit("请在项目虚拟环境安装 requirements-openclaw.txt。") from None
        # pywebview debug logs include loaded URLs. Keep native one-time handoffs
        # private even when a developer's environment asks for verbose logging.
        logging.getLogger("pywebview").setLevel(logging.WARNING)
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("MIRALab.Mirally.ControlUI")
        webview.settings["ALLOW_DOWNLOADS"] = True
        webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True
        self.window = webview.create_window(
            "Mirally", html=startup_html(), width=1280, height=860,
            min_size=(640, 480), background_color="#0d1520",
            text_select=True, zoomable=True, easy_drag=False,
        )
        self.window.events.before_show += self._native_style
        self.window.events.loaded += self._browser_ready
        self.window.events.closed += self._close
        menu = [Menu("Mirally", [
            MenuAction("侧边伙伴", self._dock),
            MenuAction("重新连接", self._retry),
            MenuAction("在浏览器中打开", self._browser),
            MenuSeparator(),
            MenuAction("关闭窗口", self.window.destroy),
        ])]
        # Resolve the native handoff while WebView2 initializes the opening page.
        self._start_connect()
        try:
            webview.start(
                gui="edgechromium", private_mode=False,
                storage_path=str(self.root / "config/openassist/openclaw/mirally-webview"),
                menu=menu,
            )
        except webview.WebViewException:
            raise SystemExit("无法启动 WebView2，请检查 Microsoft Edge WebView2 Runtime 和 requirements-openclaw.txt。") from None
