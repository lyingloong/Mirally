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

from mirally.openclaw_host import HostError, OpenClawHost
from mirally.control_ui_brand import brand_script, startup_message


ASSETS = Path(__file__).resolve().parent / "assets"


def _companion_viewport_scale(client_size, viewport, fallback: float) -> float:
    """Use the WebView's actual CSS viewport, which can differ from Form DPI."""
    if not isinstance(viewport, list) or len(viewport) != 2 or any(
            not isinstance(value, (int, float)) or isinstance(value, bool)
            or not math.isfinite(value) or value <= 0 for value in viewport):
        return fallback
    sx, sy = client_size[0] / viewport[0], client_size[1] / viewport[1]
    if not 0.25 <= sx <= 8 or not math.isclose(sx, sy, rel_tol=0.05):
        return fallback
    return sx


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


def _client_glass(form, margin: int) -> None:
    """Let transparent WebView pixels composite through the host client area."""
    class Margins(ctypes.Structure):
        _fields_ = [(side, ctypes.c_int) for side in ('left', 'right', 'top', 'bottom')]
    values = Margins(margin, margin, margin, margin)
    api = ctypes.windll.dwmapi.DwmExtendFrameIntoClientArea
    api.argtypes = [ctypes.c_void_p, ctypes.POINTER(Margins)]
    api(int(form.Handle.ToInt64()), ctypes.byref(values))


def startup_html() -> str:
    # Use the canonical vector's production PNG export. The SVG master is over
    # WebView2 NavigateToString's 2 MB limit and expensive to animate as an image.
    logo = base64.b64encode((ASSETS / "mirally-512.png").read_bytes()).decode("ascii")
    return (ASSETS / "control-ui-startup.html").read_text(encoding="utf-8").replace("__MIRALLY_LOGO__", logo)


def _desktop_subagent_ids(config: dict) -> tuple[str, ...]:
    """Only product harness entries are hidden from direct conversation menus."""
    result = set()
    for agent_id, entry in (config.get('entries') or {}).items():
        if isinstance(entry, dict):
            models = entry.get('models') or {}
            if any(isinstance(value, dict) and (value.get('agentRuntime') or {}).get('id') == 'mirally-ufo'
                   for value in models.values()):
                result.add(agent_id)
    for entry in config.get('list') or []:
        if isinstance(entry, dict) and isinstance(entry.get('id'), str):
            result.update(_desktop_subagent_ids({'entries': {entry['id']: entry}}))
    return tuple(sorted(value for value in result if isinstance(value, str)))


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
        self._system_menu = None
        self._shape_key = None
        self._surface_mode = 'badge'
        self._surface_view = 'compact'
        self._companion_css_scale = 1.0
        self._shape_parts_key = None
        self._workspace_backcolor = None
        self._workspace_glass_margin = 0
        self._glass_active = False
        self._context_request = None
        self._startup_status = None
        self._context_menu_bound = False
        self._startup_visible = threading.Event()
        self._frontend_registration = None
        self._activity_settings_lock = threading.RLock()
        self._activity_control_lock = threading.Lock()
        self._activity_settings = None
        self._activity_auto_started = False

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

    def _connect(self, reload: bool = False, restart: bool = False) -> None:
        if not self._connecting.acquire(blocking=False):
            return
        try:
            if self.closed.is_set():
                return
            if restart and not self.window.create_confirmation_dialog(
                    "重启 Gateway",
                    "重启会中断当前任务和其他已连接窗口的工作。请先停止任务并保存未发送的草稿。\n\n"
                    "Mirally 窗口将保持打开，重启后自动重新连接。现在重启吗？"):
                return
            if reload:
                if self._workspace is not None:
                    from System import Action
                    self.window.native.Invoke(Action(lambda: self._companion_action({'action': 'workspace'})))
                self.window.load_html(startup_html())
            started = time.monotonic()
            if self.host is None:
                self.host = OpenClawHost(self.root)
            url = self.host.restart(self._status) if restart else self.host.connect(self._status)
            # Only a desktop window owns automatic capture. A reconnect never
            # restarts a capture the user temporarily stopped in this window.
            threading.Thread(target=self._auto_start_activity, daemon=True).start()
            if (not self.window.events.loaded.wait(20)
                    or not self._startup_visible.wait(20) or self.closed.is_set()):
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
        subagent_ids = ()
        if self.host is not None:
            try:
                subagent_ids = _desktop_subagent_ids(self.host._native_json('config', 'get', 'agents', '--json'))
            except Exception:
                logging.getLogger(__name__).debug('Desktop subagent menu metadata unavailable')
        source = brand_script(url, desktop_subagent_ids=subagent_ids)
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

    def _restart_gateway(self) -> None:
        threading.Thread(target=self._connect, kwargs={"reload": True, "restart": True}, daemon=True).start()

    def _start_connect(self) -> None:
        threading.Thread(target=self._connect, daemon=True).start()

    def _browser(self) -> None:
        def open_native():
            try:
                if self.host:
                    # A handoff is single-use, so obtain a fresh one for browser.
                    webbrowser.open(self.host.dashboard())
            except HostError:
                self.window.create_confirmation_dialog("Mirally", "原生界面暂不可用，请先通过左上角窗口图标菜单重新连接。")
        threading.Thread(target=open_native, daemon=True).start()

    def _activity_browser(self) -> None:
        """Keep the window-menu shortcut on the same profile as settings."""
        threading.Thread(target=self._activity_action, args=({'action': 'browse'},), daemon=True).start()

    def _activity_settings_page(self) -> None:
        if not self.closed.is_set() and self.window is not None:
            self.window.evaluate_js("window.__mirallyActivity?.open()")

    def _activity_backend(self):
        with self._activity_settings_lock:
            if self.closed.is_set():
                raise RuntimeError("Mirally 窗口已关闭。")
            if self._activity_settings is None:
                from mirally.runtime.activity.settings import ActivitySettings
                self._activity_settings = ActivitySettings(self.root, host=self.host)
            else:
                self._activity_settings.host = self.host
            return self._activity_settings

    def _auto_start_activity(self) -> None:
        with self._activity_control_lock:
            if self.closed.is_set() or self._activity_auto_started:
                return
            self._activity_auto_started = True
            try:
                self._activity_backend().auto_start_capture()
            except Exception:
                # Startup must still show the app when a collector is already
                # owned elsewhere or its configured sources are unavailable.
                logging.getLogger(__name__).exception("Activity automatic startup failed")

    def _activity_result(self, request_id: str | None, *, result=None, error: str | None = None) -> None:
        if self.closed.is_set() or self.window is None:
            return
        if request_id is None:
            if error:
                self.window.create_confirmation_dialog("活动与记忆", error)
            return
        parts = urlsplit(str(self.window.get_current_url()))
        if (parts.scheme, parts.netloc) != self._gateway_origin:
            return
        payload = {'requestId': request_id, 'result': result, 'error': error}
        try:
            self.window.evaluate_js(f"window.__mirallyActivity?.receive({json.dumps(payload)})")
        except Exception:
            # Navigation or closing retires that page's pending request.
            logging.getLogger(__name__).debug("Activity settings response retired during navigation")

    def _activity_action(self, message: dict) -> None:
        """Dispatch only product-owned operations, away from the WebView thread."""
        if self.closed.is_set():
            return
        request_id, action = message.get('requestId'), message.get('action')
        control = action in {'start', 'pause', 'resume', 'stop', 'set-enabled', 'set-semantics', 'set-semantic-model'}
        if control and not self._activity_control_lock.acquire(blocking=False):
            self._activity_result(request_id, error="上一项采集操作仍在处理中，请等待完成后刷新状态。")
            return
        try:
            backend = self._activity_backend()
            if action == 'status':
                result = backend.status()
            elif action == 'model-settings':
                if set(message) - {'type', 'action', 'requestId'}:
                    raise ValueError("不支持的模型设置读取字段。")
                result = backend.model_settings()
            elif action in {'browse', 'monitor'}:
                result = backend.open_browser('history' if action == 'browse' else 'monitor')
            elif action == 'memory-search':
                allowed = {'type', 'action', 'requestId', 'query', 'include_candidates', 'limit', 'offset',
                           'provenances', 'kinds', 'start_utc_ms', 'end_utc_ms'}
                if set(message) - allowed:
                    raise ValueError("不支持的记忆查询字段。")
                query = message.get('query', '')
                candidates, limit = message.get('include_candidates', False), message.get('limit', 20)
                offset = message.get('offset', 0)
                if not isinstance(query, str) or len(query) > 512 or type(candidates) is not bool:
                    raise ValueError("请使用有效的记忆查询条件。")
                if type(limit) is not int or not 1 <= limit <= 50:
                    raise ValueError("记忆查询数量无效。")
                if type(offset) is not int or not 0 <= offset <= 10000:
                    raise ValueError("记忆查询页码无效。")
                from mirally.runtime.activity.settings import validate_memory_search_filters
                filters = validate_memory_search_filters({key: message[key] for key in (
                    'provenances', 'kinds', 'start_utc_ms', 'end_utc_ms') if key in message})
                result = backend.memory_search(query, include_candidates=candidates, limit=limit, offset=offset, **filters)
            elif action == 'memory-read':
                memory_id = message.get('memory_id')
                if not isinstance(memory_id, str) or not memory_id or len(memory_id) > 256:
                    raise ValueError("记忆记录无效，请刷新后重试。")
                from_line, line_count = message.get('from_line', 1), message.get('line_count', 100)
                if type(from_line) is not int or not 1 <= from_line <= 10000:
                    raise ValueError("记忆正文起始行无效。")
                if type(line_count) is not int or not 1 <= line_count <= 200:
                    raise ValueError("记忆正文行数无效。")
                result = backend.memory_read(memory_id, from_line=from_line, line_count=line_count)
            elif action == 'start':
                state = backend.status()
                activity = state.get('activity', {})
                if activity.get('error'):
                    raise ValueError(activity['error'])
                result = (backend.start_capture(expected_activity=activity) if not self.closed.is_set()
                          else {'cancelled': True})
            elif action == 'set-enabled':
                enabled = message.get('enabled')
                if type(enabled) is not bool:
                    raise ValueError("活动记录开关无效。")
                result = backend.set_enabled(enabled)
            elif action == 'set-semantics':
                allowed = {'enabled', 'model', 'maxCallsPerHour', 'maxTokensPerDay', 'type', 'action', 'requestId'}
                if set(message) - allowed:
                    raise ValueError("不支持的模型语义整理设置字段。")
                if any(name in message and type(message[name]) is not int for name in ('maxCallsPerHour', 'maxTokensPerDay')):
                    raise ValueError("模型语义整理预算必须是整数。")
                result = backend.set_semantics(message.get('enabled'), model=message.get('model'),
                    model_provided='model' in message, max_calls_per_hour=message.get('maxCallsPerHour'),
                    max_tokens_per_day=message.get('maxTokensPerDay'))
            elif action == 'set-semantic-model':
                if set(message) - {'type', 'action', 'requestId', 'model', 'maxOutputTokens'}:
                    raise ValueError("不支持的整理模型设置字段。")
                if not ({'model', 'maxOutputTokens'} & set(message)):
                    raise ValueError("请至少提供整理模型或模型输出上限。")
                model = message.get('model')
                if 'model' in message and (not isinstance(model, str) or len(model) > 512
                        or any(char.isspace() for char in model.strip())):
                    raise ValueError("整理模型须填写完整模型标识，留空沿用宿主模型。")
                if ('maxOutputTokens' in message and (type(message['maxOutputTokens']) is not int
                        or not 128 <= message['maxOutputTokens'] <= 16000)):
                    raise ValueError("模型输出上限必须是 128 到 16000 之间的整数。")
                result = backend.set_semantic_model(model, max_output_tokens=message.get('maxOutputTokens'))
            elif action in {'pause', 'resume', 'stop'}:
                session_id = message.get('session_id')
                if not isinstance(session_id, str) or not 0 < len(session_id) <= 128:
                    raise ValueError("采集会话已变化，请刷新后重试。")
                result = getattr(backend, action + '_capture')(expected_session_id=session_id)
            else:
                raise ValueError("不支持的活动与记忆操作。")
            self._activity_result(request_id, result=result)
        except (ValueError, RuntimeError, HostError, FileNotFoundError) as error:
            self._activity_result(request_id, error=str(error))
        except Exception:
            self._activity_result(request_id, error="暂时无法访问活动与记忆，请检查本机配置和数据状态后重试。")
        finally:
            if control:
                self._activity_control_lock.release()

    def _close(self) -> None:
        self.closed.set()
        try:
            with self._activity_settings_lock:
                backend = self._activity_settings
                if backend is not None:
                    backend.close()
                    self._activity_settings = None
        finally:
            if self._frontend_registration is not None:
                self._frontend_registration.close()
                self._frontend_registration = None
            if self.host:
                self.host.close()

    def _native_style(self) -> None:
        # pywebview's icon= is GTK/Qt only. Set the icon on our own WinForms
        # window on its UI thread. Do this once, before the window is displayed.
        from System import Action
        from System.Drawing import Icon
        form = self.window.native

        def apply():
            from mirally.platform.frontend_registry import FrontendRegistration
            from mirally.platform.window_menu import WindowSystemMenu
            if self._frontend_registration is None:
                self._frontend_registration = FrontendRegistration(self.root)
            # WebView2 initializes after WinForms shows the host. Keep that
            # empty backplate invisible until the opening page has painted.
            form.Opacity = 0
            form.Text = ''
            form.Icon = Icon(str(ASSETS / "mirally.ico"))
            form.ShowIcon = True
            form.ShowInTaskbar = True
            # Wire first-paint before any optional chrome initialization can
            # fail; the window must still become visible after a native error.
            # The official UI emits a WebView2 {type:"ready"} notification for
            # native hosts. pywebview expects its own three-element messages;
            # route only those to it, without changing the native webpage.
            form.webview.WebMessageReceived -= form.browser.on_script_notify
            form.webview.WebMessageReceived += self._web_message
            if self._system_menu is None:
                menu = WindowSystemMenu(int(form.Handle.ToInt64()), [
                    ("侧边伙伴", self._dock),
                    ("重新连接", self._retry),
                    ("重启 Gateway…", self._restart_gateway),
                    ("活动与记忆设置", self._activity_settings_page),
                    ("活动历史", self._activity_browser),
                    ("在浏览器中打开", self._browser),
                    None,
                ], self._dispatch_window_action)
                menu.install()
                self._system_menu = menu
        form.Invoke(Action(apply))

    def _web_message(self, sender, args) -> None:
        try:
            value = json.loads(str(args.get_WebMessageAsJson()))
        except (TypeError, ValueError):
            return
        if value == {'type': 'mirally-startup-painted'}:
            if (str(args.Source) == 'about:blank' and not self.closed.is_set()
                    and not self._startup_visible.is_set()):
                self.window.native.Opacity = 1
                self._startup_visible.set()
            return
        if isinstance(value, dict) and value.get("type") == "mirally-ufo-input":
            parts = urlsplit(str(args.Source))
            if ((parts.scheme, parts.netloc) == self._gateway_origin
                    and type(value.get("editing")) is bool
                    and self._frontend_registration is not None):
                self._frontend_registration.update(value["editing"])
            return
        if isinstance(value, dict) and value.get('type') == 'mirally-companion':
            parts = urlsplit(str(args.Source))
            if (parts.scheme, parts.netloc) == self._gateway_origin:
                self._companion_action(value)
            return
        if isinstance(value, dict) and value.get('type') == 'mirally-activity':
            parts = urlsplit(str(args.Source))
            request_id = value.get('requestId')
            if (not self.closed.is_set() and (parts.scheme, parts.netloc) == self._gateway_origin
                    and isinstance(request_id, str) and 1 <= len(request_id) <= 128
                    and value.get('action') in {'status', 'browse', 'monitor', 'start', 'pause', 'resume',
                                              'stop', 'set-enabled', 'set-semantics', 'model-settings',
                                              'set-semantic-model', 'memory-search', 'memory-read'}):
                threading.Thread(target=self._activity_action, args=(value,), daemon=True).start()
            return
        if value == "FilesDropped" or isinstance(value, list) and len(value) == 3:
            self.window.native.browser.on_script_notify(sender, args)

    def _companion_action(self, message: dict) -> None:
        """Window-only messages from our authenticated, same-origin presentation.

        Keep the WebView fixed while its CSS panel grows; clip the host to that
        panel, rather than resizing and recapturing its contents every frame.
        """
        from System.Drawing import Color, Point, Rectangle, Region, Size
        from System.Drawing.Drawing2D import GraphicsPath
        from System.Windows.Forms import FormWindowState, Screen
        form = self.window.native
        action = message.get('action')
        if action == 'dock':
            if self._workspace is not None:
                return
            self._companion_css_scale = _companion_viewport_scale(
                (form.webview.ClientSize.Width, form.webview.ClientSize.Height),
                message.get('viewport'), form.DeviceDpi / 96)
            self._workspace = (form.RestoreBounds if form.WindowState != FormWindowState.Normal else form.Bounds,
                               form.WindowState, form.MinimumSize, form.TopMost,
                               form.webview.DefaultBackgroundColor, _frame_style(form), _frame_composition(form))
            self._workspace_backcolor = form.BackColor
            # pywebview's shadow setup extends a one-pixel client margin.
            self._workspace_glass_margin = 1 if self.window.shadow else 0
            form.WindowState = FormWindowState.Normal
            form.MinimumSize = Size(0, 0)
            _frame_style(form, _frame_style(form) & ~0x00C40000)
            _frame_composition(form, 1)
            _capture_affinity(form, True)
            form.webview.DefaultBackgroundColor = Color.Transparent
            form.PerformLayout()
            area = Screen.FromControl(form).WorkingArea
            scale = self._companion_css_scale
            width = min(round(400 * scale), area.Width)
            height = min(round(680 * scale), area.Height - 24)
            form.Bounds = Rectangle(area.Right - width - 2, area.Top + (area.Height - height) // 2, width, height)
            form.ShowInTaskbar = True
            form.TopMost = True
            self._shape_key = None
            self._shape_parts_key = None
            self._initial_companion_shape(message)
        elif action == 'workspace' and self._workspace is not None:
            bounds, state, minimum, topmost, background, style, composition = self._workspace
            self._workspace = None
            self._shape_key = None
            self._shape_parts_key = None
            previous = form.Region
            form.Region = None
            if previous:
                previous.Dispose()
            # WindowState owns minimization/maximization. Reapplying the saved
            # WS_MAXIMIZE/WS_MINIMIZE bits here makes Windows report that state
            # before the real ShowWindow transition, leaving sidebar bounds
            # in place when WindowState is assigned below.
            _frame_style(form, style & ~0x21000000)
            _frame_composition(form, composition)
            _client_glass(form, self._workspace_glass_margin)
            form.BackColor = self._workspace_backcolor
            self._workspace_backcolor = None
            self._glass_active = False
            _capture_affinity(form, False)
            form.MinimumSize = minimum
            form.Bounds = bounds
            form.PerformLayout()
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
            view = form.webview
            sx, sy = view.ClientSize.Width / vw, view.ClientSize.Height / vh
            # An old workspace viewport can arrive while docking. WebView zoom
            # and DPI scale uniformly; unequal axes identify stale resize geometry.
            if not math.isclose(sx, sy, rel_tol=0.05):
                return
            self._companion_css_scale = sx
            origin = view.PointToScreen(Point(0, 0))
            ox, oy = origin.X - form.Bounds.X, origin.Y - form.Bounds.Y
            left, top = ox + round(x * sx), oy + round(y * sy)
            right, bottom = ox + round((x + width) * sx), oy + round((y + height) * sy)
            key = (left, top, right, bottom)
            parts = message.get('parts', [[x, y, width, height, min(25, width / 2, height / 2)]])
            if not isinstance(parts, list) or not 1 <= len(parts) <= 2:
                return
            for part in parts:
                if not isinstance(part, list) or len(part) != 5 or any(
                    not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v) for v in part
                ):
                    return
                px, py, pw, ph, corner = part
                if pw <= 0 or ph <= 0 or corner < 0 or px < x-1 or py < y-1 or px+pw > x+width+1 or py+ph > y+height+1:
                    return
            parts_key = (key, self._surface_mode, tuple(tuple(part) for part in parts))
            if message.get('view') in {'compact', 'quick', 'menu', 'detail', 'reply'}:
                self._surface_view = message['view']
                self._companion_background()
            if parts_key == self._shape_parts_key or right <= left or bottom <= top:
                return
            region = Region()
            region.MakeEmpty()
            for px, py, pw, ph, corner in parts:
                pl, pt = ox + round(px*sx), oy + round(py*sy)
                pr, pb = ox + round((px+pw)*sx), oy + round((py+ph)*sy)
                # CSS draws the smooth badge edge inside the binary native clip.
                padding = max(1, math.ceil(sx)) if self._surface_mode == 'badge' and pw <= 64 and ph <= 64 else 0
                pl, pt, pr, pb = pl-padding, pt-padding, pr+padding, pb+padding
                radius = min(round(corner*sx)+padding, (pr-pl)//2, (pb-pt)//2)
                diameter = radius*2
                path = GraphicsPath()
                if diameter < 2:
                    path.AddRectangle(Rectangle(pl, pt, pr-pl, pb-pt))
                else:
                    for ax, ay, angle in ((pl, pt, 180), (pr-diameter, pt, 270),
                                           (pr-diameter, pb-diameter, 0), (pl, pb-diameter, 90)):
                        path.AddArc(ax, ay, diameter, diameter, angle, 90)
                path.CloseFigure()
                region.Union(path)
                path.Dispose()
            self._shape_key, self._shape_parts_key = key, parts_key
            previous = form.Region
            form.Region = region
            if previous:
                previous.Dispose()

    def _initial_companion_shape(self, message: dict) -> None:
        form = self.window.native
        requested = message.get('surfaceMode')
        if requested in {'rail', 'badge', 'pet'}:
            self._surface_mode = requested
        requested_view = message.get('view', 'detail' if message.get('expanded') is True else 'compact')
        if requested_view in {'compact', 'quick', 'menu', 'detail', 'reply'}:
            self._surface_view = requested_view
        self._companion_background()
        scale = self._companion_css_scale
        vw, vh = form.webview.ClientSize.Width / scale, form.webview.ClientSize.Height / scale
        anchor_width, anchor_height = {
            'rail': (80, 300), 'badge': (48, 48), 'pet': (80, 96),
        }[self._surface_mode]
        if self._surface_view in {'quick', 'menu', 'reply'}:
            bubble_width, bubble_height = {'quick': (248, 148), 'menu': (220, 252), 'reply': (296, 228)}[self._surface_view]
            width, height = anchor_width + 8 + bubble_width, max(anchor_height, bubble_height)
            parts = [[vw-2-anchor_width, (vh-anchor_height)/2, anchor_width, anchor_height, min(25, anchor_width/2)],
                     [vw-2-width, (vh-bubble_height)/2, bubble_width, bubble_height, 18]]
        else:
            width, height = (376, 560) if self._surface_view == 'detail' else (anchor_width, anchor_height)
            width, height = min(width, vw-4), min(height, vh-32)
            parts = [[vw-2-width, (vh-height)/2, width, height, min(25, width/2, height/2)]]
        # Position-only viewport changes do not trigger ResizeObserver.
        self._companion_action({'action': 'shape', 'expanded': message.get('expanded'),
            'viewport': [vw, vh], 'rect': [vw-2-width, (vh-height)/2, width, height], 'parts': parts})
        form.webview.CoreWebView2.ExecuteScriptAsync(
            "requestAnimationFrame(() => window.__mirallyCompanion?.syncShape?.())"
        )

    def _companion_background(self) -> None:
        from System.Drawing import Color
        transparent = self._surface_view != 'detail'
        if transparent == self._glass_active or self._workspace is None:
            return
        form = self.window.native
        # Black is the transparent clear color for DWM glass, not a color key.
        # Full detail and workspace views keep their original opaque backplate.
        form.BackColor = Color.Black if transparent else self._workspace_backcolor
        _client_glass(form, -1 if transparent else 0)
        self._glass_active = transparent

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
            from mirally.desktop_context import capture_target, DesktopContextError
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

    def _dispatch_window_action(self, action) -> None:
        # Existing menu callbacks use workers: evaluate_js/host calls can wait
        # for the UI thread, so they must not run inside a window procedure.
        def run():
            if self.closed.is_set():
                return
            try:
                action()
            except Exception:
                logging.getLogger(__name__).exception("Window menu action failed")
        threading.Thread(target=run, daemon=True).start()

    def _context_menu_requested(self, sender, args) -> None:
        # A hover may expand the anchor before WebView delivers right-click.
        # Let the page hit-test the current companion rather than relying on
        # a particular form/view or the host's asynchronous shape snapshot.
        if self.closed.is_set() or self._workspace is None:
            return
        parts = urlsplit(str(sender.Source))
        if (parts.scheme, parts.netloc) != self._gateway_origin:
            return
        target = args.ContextMenuTarget
        if target.IsEditable or target.HasSelection:
            return  # Keep native copy/paste and selected-text commands.
        location = args.Location
        size = self.window.native.webview.ClientSize
        if size.Width <= 0 or size.Height <= 0:
            return
        args.Handled = True
        # WebView's event point is in client pixels; DOM hit testing uses CSS
        # pixels, including when the display DPI or browser zoom has changed.
        sender.ExecuteScriptAsync(
            "window.__mirallyCompanion?.requestContextMenu("
            f"{int(location.X)} * innerWidth / {int(size.Width)}, "
            f"{int(location.Y)} * innerHeight / {int(size.Height)})"
        )

    def _browser_ready(self) -> None:
        if self.closed.is_set():
            return
        from System import Action

        def apply():
            view = self.window.native.webview.CoreWebView2
            # The CoreWebView2 instance survives navigation; subscribe once.
            if not self._context_menu_bound:
                view.ContextMenuRequested += self._context_menu_requested
                self._context_menu_bound = True
            settings = view.Settings
            settings.AreDefaultContextMenusEnabled = True
            settings.AreBrowserAcceleratorKeysEnabled = True
            if not self._startup_visible.is_set():
                # Two frames let the startup document reach composition before
                # revealing the same HWND; do not Show/Activate it again.
                view.ExecuteScriptAsync(
                    "requestAnimationFrame(() => requestAnimationFrame(() => "
                    "window.chrome.webview.postMessage({type:'mirally-startup-painted'})))"
                )

        self.window.native.Invoke(Action(apply))
        if self._startup_status:
            self._status(*self._startup_status)

    def run(self) -> None:
        try:
            import webview
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
        # Resolve the native handoff while WebView2 initializes the opening page.
        self._start_connect()
        try:
            webview.start(
                gui="edgechromium", private_mode=False,
                storage_path=str(self.root / "config/mirally/openclaw/mirally-webview"),
            )
        except webview.WebViewException:
            raise SystemExit("无法启动 WebView2，请检查 Microsoft Edge WebView2 Runtime 和 requirements-openclaw.txt。") from None
        finally:
            self._close()
