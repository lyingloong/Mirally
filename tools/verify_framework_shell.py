"""Verify the retained Control UI desktop with an isolated test-mode Host.

Run this Windows/WebView2 check separately from other GUI checks. Only the
current process's HWND and Chromium child receive native messages; delayed
hover uses trusted CDP input in the same WebView2. It neither
moves the system pointer nor sends global keys, captures the user's desktop,
starts Activity collection, or invokes an external model.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import ctypes
from ctypes import wintypes
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from types import MethodType

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
FRONTEND_DIST = ROOT / "frontend/control-ui/dist"

# These inspect the real retained application. The legacy compatibility
# navigation helper intentionally does not recreate the discarded UI's state.
FRONTEND_READY = """(() => {
    const app = document.querySelector('openclaw-app');
    const gateway = app?.context?.gateway?.snapshot;
    return !!app && gateway?.phase === 'connected' && gateway.client?.connected === true &&
      !!window.__mirallyLegacyHost && !!window.__mirallyLegacyNativeBridge &&
      !!window.__mirallyCompanion && !!window.__mirallyWork && !!window.__mirallyContext;
})()"""

NATIVE_SESSION_READY = """(() => {
    const pane = [...document.querySelectorAll('openclaw-chat-pane.chat-pane-cache__pane--active')]
      .find(node => node.presented !== false && node.state?.handleSendChat);
    return !!window.__shellVerificationSessionKey &&
      pane?.state?.sessionKey === window.__shellVerificationSessionKey &&
      pane.state.connected === true && !pane.state.chatLoading;
})()"""

CREATE_NATIVE_SESSION = """(() => {
    const context = document.querySelector('openclaw-app')?.context;
    const client = context?.gateway?.snapshot?.client;
    if (!client || !document.querySelector('openclaw-app-shell')?.selectChatSession)
      throw new Error('Native application session entry is unavailable');
    window.__shellVerificationCreating = true;
    void client.request('sessions.create', {
      agentId: 'main', displayName: '原生窗口验收', idempotencyKey: crypto.randomUUID()
    }).then(result => {
      if (!result?.key || result.runStarted === true)
        throw new Error('Expected a real empty session without a model run');
      window.__shellVerificationSessionKey = result.key;
      document.querySelector('openclaw-app-shell').selectChatSession(result.key, 'main');
    }).catch(error => window.__shellVerificationErrors.push(String(error)))
      .finally(() => { window.__shellVerificationCreating = false; });
})()"""

RESTORE_NATIVE_SESSION = """(() => {
    window.__mirallyWork?.close();
    document.querySelector('openclaw-app-shell').selectChatSession(
      window.__shellVerificationSessionKey, 'main');
})()"""


@contextmanager
def temporary_home():
    profile = tempfile.TemporaryDirectory(prefix="mirally-framework-shell-", ignore_cleanup_errors=True)
    assert Path(profile.name).resolve().parent == Path(tempfile.gettempdir()).resolve()
    try:
        # Exercise spaces and non-ASCII paths without using a real profile.
        yield Path(profile.name) / "framework 原生验收"
    finally:
        # Crashpad may briefly retain files after the window exits. Do not let
        # its delayed release conceal the result of the actual verification.
        for attempt in range(5):
            try:
                profile.cleanup()
                break
            except PermissionError:
                if attempt < 4:
                    time.sleep(.25)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / ".artifacts/framework-shell-verification.json")
    parser.add_argument("--close-exit", action="store_true", help="verify configured Windows Close exits instead of hiding to tray")
    parser.add_argument("--page-quit", action="store_true", help="quit through the real About Mirally page")
    parser.add_argument("--reuse-host", action="store_true", help="connect the shell to an already running isolated Host")
    parser.add_argument("--workspace-paint-probe", action="store_true", help="record actual workspace transition colors and native clip ordering")
    parser.add_argument("--workspace-paint-baseline-ref", help="compare the native shell from an explicit local Git revision without editing the working tree")
    arguments = parser.parse_args()
    if sys.platform != "win32":
        raise SystemExit("This verification requires Windows and WebView2.")
    if not (FRONTEND_DIST / "index.html").is_file():
        raise SystemExit("Build the retained frontend/control-ui source before running native verification.")
    from mirally.framework_host import FrameworkHost
    from mirally.framework_shell import FrameworkShell

    user = ctypes.WinDLL("user32", use_last_error=True)
    child_callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user.EnumChildWindows.argtypes = [wintypes.HWND, child_callback, wintypes.LPARAM]
    user.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user.ScreenToClient.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
    user.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user.PostMessageW.restype = wintypes.BOOL
    user.SendMessageW.argtypes = user.PostMessageW.argtypes
    user.SendMessageW.restype = wintypes.LPARAM
    user.IsIconic.argtypes = [wintypes.HWND]
    user.IsIconic.restype = wintypes.BOOL
    user.GetSystemMenu.argtypes = [wintypes.HWND, wintypes.BOOL]
    user.GetSystemMenu.restype = wintypes.HMENU
    user.GetMenuItemCount.argtypes = [wintypes.HMENU]
    user.GetMenuItemID.argtypes = [wintypes.HMENU, ctypes.c_int]
    user.GetMenuItemID.restype = wintypes.UINT
    user.GetMenuStringW.argtypes = [wintypes.HMENU, wintypes.UINT, wintypes.LPWSTR, ctypes.c_int, wintypes.UINT]
    user.GetWindowDisplayAffinity.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user.GetWindowDisplayAffinity.restype = wintypes.BOOL

    results, failures, native_context, messages = [], [], [], []
    timeline, startup, saved = {}, {}, {}
    began = time.monotonic()
    with temporary_home() as home:
        if arguments.close_exit:
            home.mkdir(parents=True)
            (home / "ui-preferences.json").write_text(json.dumps({"mirally.window.closeBehavior": "exit"}), encoding="utf-8")
        previous_owner = None
        if arguments.reuse_host:
            previous_owner = FrameworkHost(ROOT, home, test_mode=True)
            previous_owner.start()
        host = FrameworkHost(ROOT, home, test_mode=True)
        shell = FrameworkShell(host)
        original_start = host.start
        def start(*args, **kwargs):
            timeline["host_start"] = time.monotonic() - began
            result = original_start(*args, **kwargs)
            timeline["host_ready"] = time.monotonic() - began
            return result
        host.start = start
        original_message = shell._web_message
        def message(sender, args):
            try:
                value = json.loads(str(args.get_WebMessageAsJson()))
            except (TypeError, ValueError):
                value = None
            first_paint = value == {"type": "mirally-startup-painted"} and str(args.Source) == "about:blank"
            if first_paint:
                timeline["first_paint"] = time.monotonic() - began
                startup.update(opacity_before=float(shell.window.native.Opacity), source=str(args.Source))
            if isinstance(value, dict) and value.get("type") in {"mirally-companion", "mirally-framework", "mirally-ufo-input"}:
                messages.append({"type": value["type"], "action": value.get("action"), "editing": value.get("editing"),
                    "parts": value.get("parts"), "rect": value.get("rect"), "viewport": value.get("viewport"), "view": value.get("view")})
            original_message(sender, args)
            if first_paint:
                startup.update(opacity_after=float(shell.window.native.Opacity), gate=shell._startup_visible.is_set())
        shell._web_message = message

        def verify():
            try:
                from System import Action, Array, Object
                from System.Drawing import Point, Rectangle
                from System.Reflection import BindingFlags
                from System.Windows.Forms import FormWindowState, MenuStrip, MouseButtons, MouseEventArgs, Screen
                from mirally.control_ui_shell import _frame_style
                form = shell.window.native
                def js(source):
                    return shell.window.evaluate_js(source)
                def native():
                    value = {}
                    def inspect():
                        value.update(hwnd=int(form.Handle.ToInt64()), visible=bool(form.Visible),
                            minimized=form.WindowState == FormWindowState.Minimized,
                            window_state=str(form.WindowState),
                            restore_bounds=[int(form.RestoreBounds.X), int(form.RestoreBounds.Y), int(form.RestoreBounds.Width), int(form.RestoreBounds.Height)],
                            native_minimized=bool(user.IsIconic(int(form.Handle.ToInt64()))),
                            window_style=hex(_frame_style(form)), minimize_box=bool(form.MinimizeBox),
                            opacity=float(form.Opacity), region=form.Region is not None,
                            topmost=bool(form.TopMost), title=str(form.Text), show_icon=bool(form.ShowIcon),
                            has_icon=form.Icon is not None, strips=sum(isinstance(control, MenuStrip) for control in form.Controls),
                            dpi=int(form.DeviceDpi), view_size=[int(form.webview.ClientSize.Width), int(form.webview.ClientSize.Height)],
                            client_size=[int(form.ClientSize.Width), int(form.ClientSize.Height)],
                            managed_border_style=str(form.FormBorderStyle),
                            working_area=[int(getattr(Screen.FromControl(form).WorkingArea, key)) for key in ("X", "Y", "Width", "Height")],
                            bounds=[int(form.Bounds.X), int(form.Bounds.Y), int(form.Bounds.Width), int(form.Bounds.Height)])
                    form.Invoke(Action(inspect))
                    return value
                def snapshot():
                    return {"native": native(), "surface_mode": shell._surface_mode, "surface_view": shell._surface_view,
                        "dom": js("""(() => {
                            const surface = document.querySelector('.mirally-companion');
                            const context = document.querySelector('openclaw-app')?.context;
                            const pane = [...document.querySelectorAll('openclaw-chat-pane.chat-pane-cache__pane--active')]
                              .find(node => node.presented !== false && node.state?.handleSendChat);
                            return {mode: document.documentElement.dataset.mirallyMode,
                              form: surface?.dataset.form, view: surface?.dataset.view,
                              route: location.pathname + location.search,
                              connected: context?.gateway?.snapshot?.phase === 'connected',
                              session_key: pane?.state?.sessionKey,
                              session_loading: pane?.state?.chatLoading,
                              activity_visible: !!document.querySelector('.mirally-activity:not([hidden])'),
                              errors: window.__shellVerificationErrors || []};
                        })()""")}
                def wait_for(predicate, label, timeout=10):
                    deadline = time.monotonic() + timeout
                    while time.monotonic() < deadline:
                        if predicate():
                            return
                        time.sleep(.04)
                    raise AssertionError(label + "; " + json.dumps(snapshot(), ensure_ascii=False))
                def tray_left_click():
                    # Raise the actual NotifyIcon event, including its registered
                    # single-click callback, without moving the user's pointer.
                    def click():
                        icon = shell._tray
                        method = icon.GetType().GetMethod("OnMouseClick", BindingFlags.Instance | BindingFlags.NonPublic)
                        assert method is not None, "NotifyIcon MouseClick event is unavailable"
                        method.Invoke(icon, Array[Object]([MouseEventArgs(MouseButtons.Left, 1, 0, 0, 0)]))
                    form.Invoke(Action(click))
                assert shell.window.events.loaded.wait(30), "Opening page did not load"
                wait_for(lambda: js(FRONTEND_READY),
                    "Retained Control UI and its real Mirally connection did not mount", timeout=90)
                js("window.__shellVerificationErrors=[];addEventListener('error',e=>window.__shellVerificationErrors.push(e.message));addEventListener('unhandledrejection',e=>window.__shellVerificationErrors.push(String(e.reason)))")
                assert host.runtime.get("mode") == "test", "Verification must use the deterministic isolated Host"
                assert startup == {"opacity_before": 0.0, "source": "about:blank", "opacity_after": 1.0, "gate": True}, startup
                assert timeline["host_start"] < timeline["product_navigation"], timeline
                assert timeline["first_paint"] <= timeline["product_navigation"], timeline
                initial = native()
                hwnd = initial["hwnd"]
                assert initial["title"] == "" and initial["show_icon"] and initial["has_icon"] and initial["strips"] == 0, initial
                assert initial["opacity"] == 1 and not initial["region"], initial
                assert shell._frontend_registration is None, "Default framework must not register a UFO input bridge"
                saved.update(process=previous_owner.process if previous_owner is not None else host.process)
                if arguments.reuse_host:
                    assert host.process is None and not host.owned, "Shell did not reuse the existing isolated owner"
                results.append({"case": "real Host and opening first paint", "mode": "test", "startup": startup, "timeline_seconds": timeline,
                    "same_native_window": True, "formal_icon": True, "blank_caption": True, "no_menu_strip": True,
                    "frontend_source": "frontend/control-ui", "native_app_mounted": True})

                js(CREATE_NATIVE_SESSION)
                wait_for(lambda: js(NATIVE_SESSION_READY),
                    "Real native session creation did not produce an active retained chat pane", timeout=30)
                session = js("""(() => {
                    const pane = [...document.querySelectorAll('openclaw-chat-pane.chat-pane-cache__pane--active')]
                      .find(node => node.presented !== false && node.state?.handleSendChat);
                    return {key: pane.state.sessionKey, message_count: pane.state.chatMessages?.length || 0,
                      has_run: !!pane.state.chatRunId, route: location.pathname};
                })()""")
                assert session["key"] and session["message_count"] == 0 and not session["has_run"], session
                results.append({"case": "real native client creates an empty session", "session": session,
                    "model_request_started": False, "native_chat_pane": True})

                if arguments.workspace_paint_probe:
                    from mirally.control_ui_shell import ControlUIShell
                    phase_reports = []
                    pixel_reports = []
                    versions = {}
                    form.Invoke(Action(lambda: versions.update(browser_version=str(form.webview.CoreWebView2.Environment.BrowserVersionString))))
                    # Read only this HWND's client DC. A contrasting DOM patch
                    # calibrates whether GDI can see WebView2's GPU surface.
                    # Failed calibration is not evidence of a black UI frame.
                    gdi = ctypes.WinDLL("gdi32", use_last_error=True)
                    user.GetDC.argtypes, user.GetDC.restype = [wintypes.HWND], wintypes.HDC
                    user.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
                    gdi.GetPixel.argtypes, gdi.GetPixel.restype = [wintypes.HDC, ctypes.c_int, ctypes.c_int], wintypes.DWORD
                    def own_pixel():
                        location = {}
                        def locate():
                            screen = form.webview.PointToScreen(Point(round(128 * form.webview.ClientSize.Width / js_size[0]), round(128 * form.webview.ClientSize.Height / js_size[1])))
                            local = form.PointToClient(screen)
                            location.update(x=local.X, y=local.Y)
                        form.Invoke(Action(locate))
                        dc = user.GetDC(hwnd)
                        try:
                            value = gdi.GetPixel(dc, location["x"], location["y"])
                            return None if value == 0xffffffff else [value & 255, value >> 8 & 255, value >> 16 & 255]
                        finally:
                            if dc: user.ReleaseDC(hwnd, dc)
                    js_size = js("[innerWidth,innerHeight]")
                    for patch_color in ([246, 250, 252], [37, 53, 79]):
                        js("""(() => {let n=document.querySelector('#mirally-paint-calibration');if(!n){n=document.createElement('div');n.id='mirally-paint-calibration';document.body.append(n);}n.style='position:fixed;left:48px;top:48px;width:160px;height:160px;z-index:2147483647;background:rgb(""" + ",".join(map(str, patch_color)) + ");';})()")
                        time.sleep(.25)
                        pixel_reports.append({"expected": patch_color, "observed_own_hwnd_dc": own_pixel()})
                    js("document.querySelector('#mirally-paint-calibration').remove()")
                    calibrated = all(item["expected"] == item["observed_own_hwnd_dc"] for item in pixel_reports)
                    implementations = [("current", ControlUIShell._companion_action)]
                    if arguments.workspace_paint_baseline_ref:
                        source = subprocess.check_output(["git", "show", arguments.workspace_paint_baseline_ref + ":mirally/control_ui_shell.py"], cwd=ROOT, text=True, encoding="utf-8")
                        namespace = {"__file__": str(ROOT / "mirally/control_ui_shell.py"), "__name__": "mirally_paint_probe_baseline"}
                        exec(compile(source, str(ROOT / "mirally/control_ui_shell.py") + "@" + arguments.workspace_paint_baseline_ref, "exec"), namespace)
                        implementations.insert(0, ("baseline native " + arguments.workspace_paint_baseline_ref, namespace["ControlUIShell"]._companion_action))
                    original_dispatch = shell._companion_action
                    original_theme = js("document.documentElement.dataset.theme")
                    try:
                        for label, implementation in implementations:
                            for theme in ("light", "dark"):
                                js("document.documentElement.dataset.theme=" + json.dumps(theme))
                                time.sleep(.15)
                                expected = js("getComputedStyle(document.body).backgroundColor.match(/\\d+/g).slice(0,3).map(Number)")
                                phases = []
                                def phase(frame, event, _argument):
                                    if frame.f_code is implementation.__code__ and event == "line":
                                        back, web_back = form.BackColor, form.webview.DefaultBackgroundColor
                                        phases.append({"line": frame.f_lineno, "wall_ms": time.time()*1000,
                                            "region": form.Region is not None, "glass": shell._glass_active,
                                            "back": [back.A, back.R, back.G, back.B],
                                            "webview_back": [web_back.A, web_back.R, web_back.G, web_back.B],
                                            "bounds": [form.Bounds.X, form.Bounds.Y, form.Bounds.Width, form.Bounds.Height], "state": str(form.WindowState)})
                                    return phase
                                def dispatch(_self, value):
                                    if value.get("action") == "workspace" and shell._workspace is not None:
                                        previous_trace = sys.gettrace()
                                        sys.settrace(phase)
                                        try: return implementation(shell, value)
                                        finally: sys.settrace(previous_trace)
                                    return implementation(shell, value)
                                shell._companion_action = MethodType(dispatch, shell)
                                js("window.__mirallyCompanion.setForm('rail',false);window.__mirallyCompanion.dock()")
                                wait_for(lambda: shell._workspace is not None, "Paint probe did not dock")
                                time.sleep(.15)
                                js("window.__mirallyCompanion.workspace()")
                                wait_for(lambda: shell._workspace is None and not native()["region"], "Paint probe did not restore workspace")
                                released = [row for row in phases if not row["region"]]
                                matching = bool(released) and all(row["back"] == [255, *expected] and row["webview_back"] == [255, *expected] and not row["glass"] for row in released)
                                phase_reports.append({"implementation": label, "theme": theme, "expected_css_rgb": expected,
                                    "release_clip_has_matching_opaque_backplates": matching, "phases": phases})
                                if label == "current":
                                    assert matching, "Workspace exposes a clear/startup color during unclipping: " + json.dumps(phase_reports[-1])
                    finally:
                        shell._companion_action = original_dispatch
                        js("document.documentElement.dataset.theme=" + json.dumps(original_theme or "light"))
                    results.append({"case": "workspace native paint ordering and real page colors", **versions,
                        "own_hwnd_pixel_calibration": pixel_reports, "dc_can_observe_webview_pixels": calibrated,
                        "pixel_limit": "DC results are unusable for visual/black-frame conclusions when calibration fails; no desktop capture performed",
                        "phase_reports": phase_reports, "baseline_scope": "Baseline native implementation only; the real currently built frontend remains in use"})

                menu = user.GetSystemMenu(hwnd, False)
                items = []
                for index in range(user.GetMenuItemCount(menu)):
                    text = ctypes.create_unicode_buffer(256)
                    user.GetMenuStringW(menu, index, text, len(text), 0x400)
                    items.append((user.GetMenuItemID(menu, index), text.value))
                labels = [label for _, label in items]
                assert all(label in labels for label in ("侧边伙伴", "工作", "重新连接", "活动与记忆设置", "记忆", "在浏览器中打开", "退出 Mirally")), labels
                assert 0xF060 in [identifier for identifier, _ in items] and not any("Gateway" in label for label in labels), labels
                memory_command = next(identifier for identifier, label in items if label == "记忆")
                user.SendMessageW(hwnd, 0x112, memory_command, 0)
                wait_for(lambda: js("/\\/memory\\/?$/.test(location.pathname) && !!document.querySelector('#mirally-memory-browser')"),
                    "Real system-menu memory action did not open the original Memory workspace", timeout=20)
                activity_command = next(identifier for identifier, label in items if label == "活动与记忆设置")
                user.SendMessageW(hwnd, 0x112, activity_command, 0)
                wait_for(lambda: js("/\\/activity\\/?$/.test(location.pathname) && !!document.querySelector('.mirally-activity:not([hidden])')"),
                    "Real system-menu Activity action did not open the retained settings contribution", timeout=20)
                js(RESTORE_NATIVE_SESSION)
                wait_for(lambda: js(NATIVE_SESSION_READY), "Original native chat did not restore after settings")
                work_command = next(identifier for identifier, label in items if label == "工作")
                user.SendMessageW(hwnd, 0x112, work_command, 0)
                wait_for(lambda: js("!!document.querySelector('.mirally-work:not([hidden])')"),
                    "Real system-menu Work action did not open the original preparation page", timeout=20)
                assert js("document.querySelectorAll('.mirally-work__cards [data-workflow]').length") == 10
                js(RESTORE_NATIVE_SESSION)
                wait_for(lambda: js(NATIVE_SESSION_READY), "Native chat did not restore after the work page")
                results.append({"case": "native product system menu", "labels": labels, "memory_navigation": True,
                    "activity_navigation": True, "work_navigation": True, "original_settings_routes": True})

                children = []
                @child_callback
                def collect(child, _data):
                    text = ctypes.create_unicode_buffer(256)
                    user.GetClassNameW(child, text, len(text))
                    rect = wintypes.RECT()
                    user.GetWindowRect(child, ctypes.byref(rect))
                    children.append((int(child), text.value, rect.right - rect.left, rect.bottom - rect.top))
                    return True
                user.EnumChildWindows(hwnd, collect, 0)
                renderers = [row for row in children if row[1] == "Chrome_RenderWidgetHostHWND"]
                assert renderers, "No Chromium input HWND below this verifier's window"
                render = max(renderers, key=lambda row: row[2] * row[3])[0]
                def context(_sender, args):
                    native_context.append({"handled": bool(args.Handled), "editable": bool(args.ContextMenuTarget.IsEditable)})
                def bind_context():
                    form.webview.CoreWebView2.ContextMenuRequested += context
                form.Invoke(Action(bind_context))
                def target(selector, fraction_x=.5, fraction_y=.5):
                    return js("(()=>{const r=document.querySelector(" + json.dumps(selector) + ").getBoundingClientRect();return {x:r.x+r.width*" + str(fraction_x) +
                        ",y:r.y+r.height*" + str(fraction_y) + ",vw:innerWidth,vh:innerHeight,width:r.width,height:r.height}})()")
                def point(where):
                    converted = {}
                    def convert():
                        view = form.webview
                        screen = view.PointToScreen(Point(round(where["x"] * view.ClientSize.Width / where["vw"]),
                            round(where["y"] * view.ClientSize.Height / where["vh"])))
                        local = form.PointToClient(screen)
                        converted.update(x=screen.X, y=screen.Y, clipped=form.Region is not None and form.Region.IsVisible(local.X, local.Y))
                    form.Invoke(Action(convert))
                    return converted
                def post(message, where, buttons=0):
                    converted = point(where)
                    position = wintypes.POINT(converted["x"], converted["y"])
                    assert user.ScreenToClient(render, ctypes.byref(position))
                    assert user.PostMessageW(render, message, buttons, (position.x & 0xffff) | ((position.y & 0xffff) << 16))
                def browser_input(method, parameters):
                    # Delayed hover requires Chromium's own pointer state.
                    # Posting WM_MOUSEMOVE without moving the physical cursor
                    # can be followed by a Windows-generated WM_MOUSELEAVE.
                    # CDP injects trusted browser input and leaves the user's
                    # system pointer alone. Native Region hit testing is still
                    # checked separately by point() against the actual Form.
                    pending = {}
                    def start():
                        pending["task"] = form.webview.CoreWebView2.CallDevToolsProtocolMethodAsync(
                            method, json.dumps(parameters))
                    form.Invoke(Action(start))
                    task = pending["task"]
                    deadline = time.monotonic() + 5
                    # Never block the UI thread on its own async completion.
                    while not task.IsCompleted and time.monotonic() < deadline:
                        time.sleep(.01)
                    assert task.IsCompleted, "WebView2 pointer dispatch did not complete"
                    assert not task.IsFaulted and not task.IsCanceled, "WebView2 pointer dispatch failed"
                    return json.loads(str(task.Result))
                def browser_mouse_move(where):
                    return browser_input("Input.dispatchMouseEvent", {"type": "mouseMoved",
                        "x": where["x"], "y": where["y"], "button": "none", "buttons": 0, "pointerType": "mouse"})
                def browser_escape():
                    for kind in ("keyDown", "keyUp"):
                        browser_input("Input.dispatchKeyEvent", {"type": kind, "key": "Escape", "code": "Escape",
                            "windowsVirtualKeyCode": 27, "nativeVirtualKeyCode": 27})
                def affinity():
                    value = wintypes.DWORD()
                    assert user.GetWindowDisplayAffinity(hwnd, ctypes.byref(value)), "GetWindowDisplayAffinity failed: " + str(ctypes.get_last_error())
                    return value.value
                def write_capture_preference(value):
                    # An independent HTTP caller must reach the existing dock
                    # through Host SSE and its browser bridge. Do not call a
                    # presentation refresh helper from the verification code.
                    request = urllib.request.Request(host.runtime["url"] + "/api/ui-preferences",
                        data=json.dumps({"key": "mirally.companion.hideFromCapture", "value": value}).encode("utf-8"),
                        headers={"Authorization": "Bearer " + host.runtime["token"], "Content-Type": "application/json"})
                    with urllib.request.urlopen(request, timeout=5) as response:
                        result = json.loads(response.read(65536))
                    assert result["preferences"]["mirally.companion.hideFromCapture"] is value
                def latest_shape():
                    return next(item for item in reversed(messages) if item["action"] == "shape")
                def tooltip_visible():
                    return js("!!document.querySelector('#mirally-companion-tip:not([hidden])')")
                def check_tooltip_clip(expected_parts):
                    tip_box = target('#mirally-companion-tip')
                    assert tip_box["width"] > 0 and tip_box["height"] > 0, "Tooltip has no visible bounds"
                    assert tip_box["x"] - tip_box["width"] / 2 >= 0 and tip_box["x"] + tip_box["width"] / 2 <= tip_box["vw"], "Tooltip extends outside the horizontal viewport"
                    assert tip_box["y"] - tip_box["height"] / 2 >= 0 and tip_box["y"] + tip_box["height"] / 2 <= tip_box["vh"], "Tooltip extends outside the vertical viewport"
                    wait_for(lambda: len(latest_shape()["parts"] or []) == expected_parts, "Tooltip native shape parts were not delivered")
                    # Check painted edge midpoints and points inside all four
                    # rounded corners, rather than the deliberately cut corners
                    # of the tip's rectangular bounding box.
                    for fx, fy in ((.5, .04), (.5, .96), (.04, .5), (.96, .5), (.1, .25), (.9, .25), (.1, .75), (.9, .75), (.5, .5)):
                        assert point(target('#mirally-companion-tip', fx, fy))["clipped"], "Native Region cuts off a painted tooltip sample"
                    assert js("(() => { const tip=document.querySelector('#mirally-companion-tip'); return tip.getAttribute('role')==='tooltip' && tip.textContent.trim() && tip.scrollWidth<=tip.clientWidth+1 && tip.scrollHeight<=tip.clientHeight+1; })()"), "Tooltip text is truncated or has lost its tooltip role"
                    return tip_box
                def right_click(where):
                    post(0x200, where)
                    time.sleep(.08)
                    post(0x204, where, 2)
                    time.sleep(.06)
                    post(0x205, where)
                def left_click(where):
                    post(0x200, where)
                    post(0x201, where, 1)
                    post(0x202, where)
                def menu_visible():
                    return shell._surface_view == "menu" and js("document.querySelector('.mirally-companion').dataset.view==='menu'")
                def check_menu(label):
                    wait_for(menu_visible, label + " failed to open menu")
                    time.sleep(.35)
                    assert menu_visible(), label + " menu vanished"
                    button = target('.mirally-companion__menu-actions [data-action="workspace"]')
                    assert button["width"] > 0 and point(button)["clipped"], label + " menu button was clipped by native Region"

                for appearance in ("pet", "badge", "rail"):
                    js(f"window.__mirallyCompanion.setForm('{appearance}',false);window.__mirallyCompanion.dock()")
                    wait_for(lambda: shell._workspace is not None and shell._surface_mode == appearance, appearance + " dock bridge was not delivered")
                    wait_for(lambda: native()["region"], appearance + " lacks native Region")
                    time.sleep(.3)
                    compact = native()
                    assert compact["hwnd"] == hwnd and compact["topmost"], compact
                    if not arguments.close_exit:
                        wait_for(shell._tray_ready.is_set, "Tray was not installed")
                        assert user.PostMessageW(hwnd, 0x10, 0, 0)
                        wait_for(lambda: not native()["visible"], appearance + " did not hide to tray")
                        tray_left_click()
                        wait_for(lambda: native()["visible"], appearance + " single tray click did not restore")
                        assert native()["region"] and native()["bounds"] == compact["bounds"], native()
                    anchor = target('.mirally-companion__rail')
                    assert anchor["width"] > 0 and anchor["height"] > 0 and point(anchor)["clipped"], appearance + " anchor clipped"
                    right_click(anchor)
                    check_menu(appearance)
                    results.append({"case": appearance + " native companion", "same_hwnd": True, "topmost": True,
                        "native_region": True, "anchor_visible": True, "right_click_menu": True, "menu_button_visible": True,
                        "tray_single_click_preserves_companion": not arguments.close_exit,
                        "native_size": compact["bounds"][2:], "device_dpi": compact["dpi"]})
                    js("window.__mirallyCompanion.workspace()")
                    wait_for(lambda: shell._workspace is None and not native()["region"], appearance + " did not restore workbench")
                    restored = native()
                    assert restored["hwnd"] == hwnd and restored["bounds"] == initial["bounds"] and not restored["topmost"], restored

                # Floating tooltips live outside the clipped surface DOM. They
                # remain separate native Region parts, so their transparent
                # gap must not turn into an invisible input-catching rectangle.
                js("window.__mirallyCompanion.setForm('pet',false);window.__mirallyCompanion.dock()")
                wait_for(lambda: shell._workspace is not None and shell._surface_mode == "pet", "Tooltip pet dock did not finish")
                browser_mouse_move({"x":5,"y":5})
                time.sleep(.25)
                wait_for(lambda: len(latest_shape()["parts"] or []) == 1, "Compact pet did not start with its anchor-only native clip")
                tip_bounds = native()["bounds"]
                original_parts = shell._shape_parts_key
                assert affinity() == 0x11, "New companion profile did not default to hidden from capture"
                anchor = target('.mirally-companion__rail')
                browser_mouse_move(anchor)
                wait_for(tooltip_visible, "Pet anchor did not reveal its delayed tooltip")
                tip_box = check_tooltip_clip(2)
                assert js("document.querySelector('.mirally-companion').dataset.view==='compact'"), "Pet tooltip opened the work panel"
                anchor_left = anchor["x"] - anchor["width"] / 2
                tip_right = tip_box["x"] + tip_box["width"] / 2
                assert tip_right < anchor_left, "Pet tooltip does not extend independently outside the anchor: " + json.dumps({"anchor": anchor, "tip": tip_box, "shape": latest_shape(), "native": native()})
                gap = {"x": (tip_right + anchor_left) / 2, "y": anchor["y"], "vw": anchor["vw"], "vh": anchor["vh"]}
                assert not point(gap)["clipped"], "Tooltip-to-anchor gap captures desktop input"
                assert native()["bounds"] == tip_bounds, "Showing a tooltip resized the native host"
                browser_escape()
                wait_for(lambda: not tooltip_visible() and shell._shape_parts_key == original_parts,
                    "Escape did not remove the tooltip's separate native clip")
                assert native()["bounds"] == tip_bounds, "Hiding a tooltip changed native bounds"
                assert not point(tip_box)["clipped"], "Hidden tooltip left a native hit-test island behind"
                results.append({"case": "pet compact tooltip escapes anchor clip without capturing its gap",
                    "tooltip_native_parts": 2, "rounded_tip_samples_visible": True, "gap_is_click_through": True,
                    "escape_restores_original_clip": True, "fixed_native_bounds": True, "default_display_affinity": "0x11"})
                browser_mouse_move({"x":5,"y":5})
                js("window.__mirallyCompanion.workspace()")
                wait_for(lambda: shell._workspace is None and not native()["region"], "Tooltip pet did not restore workbench")

                js("window.__mirallyCompanion.setForm('badge',false);window.__mirallyCompanion.dock()")
                wait_for(lambda: shell._workspace is not None and shell._surface_mode == "badge", "Tooltip quick dock did not finish")
                browser_mouse_move({"x":5,"y":5})
                js("window.__mirallyCompanion.setView('quick')")
                wait_for(lambda: shell._surface_view == "quick", "Explicit quick view did not reach native clipping")
                quick_bounds = native()["bounds"]
                browser_mouse_move(target('.mirally-companion__composer-footer [data-action=attach]'))
                wait_for(tooltip_visible, "Quick attachment action did not reveal its tooltip")
                check_tooltip_clip(3)
                assert native()["bounds"] == quick_bounds, "Quick tooltip changed native bounds"
                browser_escape()
                wait_for(lambda: not tooltip_visible() and len(latest_shape()["parts"] or []) == 2,
                    "Quick tooltip did not release its third native Region part")
                assert js("document.querySelector('.mirally-companion').dataset.view==='quick'"), "First Escape closed the quick view along with its tooltip"
                # A view transition must retire delayed/visible tooltip state.
                browser_mouse_move({"x":5,"y":5})
                browser_mouse_move(target('.mirally-companion__composer-footer [data-action=attach]'))
                wait_for(tooltip_visible, "Quick tooltip did not reopen for view-transition validation")
                js("window.__mirallyCompanion.setView('detail')")
                wait_for(lambda: not tooltip_visible() and len(latest_shape()["parts"] or []) == 1,
                    "Changing view left the tooltip or its native hit-test island behind")
                assert native()["bounds"] == quick_bounds, "Tooltip view transition changed native host bounds"
                results.append({"case": "quick tooltip is an independent third native clip part",
                    "tooltip_native_parts": 3, "rounded_tip_samples_visible": True, "inside_viewport": True,
                    "escape_keeps_quick_view": True, "view_change_clears_tip": True, "fixed_native_bounds": True})
                js("window.__mirallyCompanion.workspace()")
                wait_for(lambda: shell._workspace is None and not native()["region"], "Quick tooltip did not restore workbench")

                # Exercise live preference changes through the same HTTP/SSE
                # route used by another connected settings window. Reading
                # display affinity tests policy without taking a screenshot.
                assert affinity() == 0, "Workspace kept companion-only capture exclusion"
                js("window.__mirallyCompanion.setForm('pet',false);window.__mirallyCompanion.dock()")
                wait_for(lambda: shell._workspace is not None and affinity() == 0x11, "Companion did not apply the default capture preference")
                bridge_before = sum(item["action"] == "refresh-capture-preference" for item in messages)
                write_capture_preference(False)
                wait_for(lambda: affinity() == 0 and sum(item["action"] == "refresh-capture-preference" for item in messages) > bridge_before,
                    "External preference save did not make the dock capturable through SSE")
                assert json.loads((home / "ui-preferences.json").read_text(encoding="utf-8"))["mirally.companion.hideFromCapture"] is False
                js("window.__mirallyCompanion.workspace()")
                wait_for(lambda: shell._workspace is None and affinity() == 0, "Workspace did not clear display affinity")
                js("window.__mirallyCompanion.dock()")
                wait_for(lambda: shell._workspace is not None, "Persisted capture-off redock did not finish")
                assert affinity() == 0, "Redock ignored persisted capture visibility"
                bridge_before = sum(item["action"] == "refresh-capture-preference" for item in messages)
                write_capture_preference(True)
                wait_for(lambda: affinity() == 0x11 and sum(item["action"] == "refresh-capture-preference" for item in messages) > bridge_before,
                    "External preference save did not restore capture exclusion through SSE")
                js("window.__mirallyCompanion.workspace()")
                wait_for(lambda: shell._workspace is None and affinity() == 0, "Returning to workspace kept capture exclusion")
                results.append({"case": "live companion capture preference uses HTTP, SSE and native display affinity",
                    "default": "0x11", "saved_false_while_docked": "0x0", "persisted_false_after_redock": "0x0",
                    "saved_true_while_docked": "0x11", "workspace": "0x0", "direct_refresh_called": False,
                    "actual_screen_capture_performed": False})

                # Hover previews no longer morph the outer layout every frame.
                # Measure bridge traffic and frame cadence in the real WebView;
                # frame timing is reported, not compared to this machine's FPS.
                for appearance in ("rail", "badge"):
                    js(f"window.__mirallyCompanion.setForm('{appearance}',false);window.__mirallyCompanion.dock()")
                    wait_for(lambda: shell._workspace is not None and shell._surface_mode == appearance,
                        appearance + " preview dock did not finish")
                    # The preceding menu test can leave Chromium's pointer over
                    # the next anchor. Start outside, then measure an actual
                    # compact -> preview transition with trusted browser input.
                    browser_mouse_move({"x":5,"y":5})
                    time.sleep(.55)
                    assert js("document.querySelector('.mirally-companion').dataset.view==='compact'"), "Preview probe did not start compact"
                    compact = native()
                    baseline = sum(item["action"] == "shape" for item in messages)
                    js("""(() => {
                        const probe = window.__shellMotionProbe = {last: null, deltas: [], active: true};
                        requestAnimationFrame(function sample(now) {
                            if (probe.last !== null) probe.deltas.push(now - probe.last);
                            probe.last = now;
                            if (probe.active) requestAnimationFrame(sample);
                        });
                    })()""")
                    browser_mouse_move(target('.mirally-companion__rail'))
                    wait_for(lambda: js("document.querySelector('.mirally-companion').dataset.view==='quick'"),
                        appearance + " native hover did not open a quick preview")
                    assert js("document.querySelector('.mirally-companion').dataset.preview==='true' && document.querySelector('.mirally-companion').dataset.pinned==='false'"), "Hover acted like an explicitly pinned panel"
                    time.sleep(.2)
                    preview_shapes = sum(item["action"] == "shape" for item in messages) - baseline
                    assert 1 <= preview_shapes <= 4, "Preview produced per-frame native clipping traffic: " + str(preview_shapes)

                    # Selecting text protects reading while this real document
                    # owns focus. Minimize only our Form to produce a native
                    # blur; a stale selection must not keep a preview alive in
                    # the background. Never stub document.hasFocus().
                    def focus_companion():
                        form.Activate()
                        form.webview.Focus()
                    form.Invoke(Action(focus_companion))
                    wait_for(lambda: js("document.hasFocus()"), "Companion did not receive actual native focus")
                    selected = js("""(() => {
                        const text = document.querySelector('.mirally-companion [data-slot=mini-status]');
                        const range = document.createRange();
                        range.selectNodeContents(text);
                        const selection = getSelection();
                        selection.removeAllRanges();
                        selection.addRange(range);
                        return {text: selection.toString(), focused: document.hasFocus(),
                            pinned: document.querySelector('.mirally-companion').dataset.pinned};
                    })()""")
                    assert selected["text"] and selected["focused"] and selected["pinned"] == "false", "Text selection accidentally pinned or failed to focus the preview"
                    browser_mouse_move({"x":5,"y":5})
                    time.sleep(.6)
                    assert js("document.hasFocus() && !getSelection().isCollapsed && document.querySelector('.mirally-companion').dataset.view==='quick'"), "Focused text selection did not protect preview reading after pointer leave"
                    before_minimize = native()
                    form.Invoke(Action(lambda: setattr(form, "WindowState", FormWindowState.Minimized)))
                    wait_for(lambda: native()["minimized"] and not js("document.hasFocus()"), "Minimized companion retained document focus")
                    wait_for(lambda: js("document.querySelector('.mirally-companion').dataset.view==='compact'"),
                        "Unfocused selection kept a minimized preview open")
                    minimized = native()
                    tray_left_click()
                    wait_for(lambda: native()["visible"] and not native()["minimized"], "Selection test did not restore companion from tray")
                    restore_samples = []
                    restore_started = time.monotonic()
                    for delay in (0, .1, .3, .6):
                        time.sleep(max(0, delay - (time.monotonic() - restore_started)))
                        restore_samples.append({"seconds_after_restore": time.monotonic() - restore_started, **native()})
                    results.append({"case": appearance + " native focus and selection boundary geometry",
                        "before_minimize": before_minimize, "while_minimized": minimized,
                        "restore_samples": restore_samples})
                    js("getSelection().removeAllRanges()")
                    browser_mouse_move({"x":5,"y":5})
                    browser_mouse_move(target('.mirally-companion__rail'))
                    wait_for(lambda: js("document.querySelector('.mirally-companion').dataset.view==='quick'"),
                        "Preview did not reopen after actual blur and tray restoration")
                    # Focus then checks the editor's held-open contract while
                    # subsequent native button messages replace browser input.
                    js("document.querySelector('[data-slot=draft]').focus()")
                    time.sleep(.3)
                    assert native()["bounds"] == compact["bounds"], "Preview or minimize/restore changed the native WebView host: " + json.dumps({"before": compact, "after": native()})
                    editor = target('[data-slot=draft]')
                    assert editor["width"] > 0 and point(editor)["clipped"], "Quick preview editor was clipped"
                    detail = target('.mirally-companion__navigation [data-action=detail]')
                    assert detail["width"] > 0 and point(detail)["clipped"], "Preview detail action was clipped"
                    left_click(detail)
                    wait_for(lambda: js("document.querySelector('.mirally-companion').dataset.view==='detail'"),
                        appearance + " explicit detail action did not open progress")
                    assert js("document.querySelector('.mirally-companion').dataset.pinned==='true' && document.querySelector('.mirally-companion').dataset.preview==='false'"), "Explicit detail did not pin the interaction"
                    js("document.querySelector('[data-slot=draft]').focus()")
                    time.sleep(.3)
                    assert native()["bounds"] == compact["bounds"], "Detail resized the native WebView host"
                    stable_shapes = sum(item["action"] == "shape" for item in messages)
                    time.sleep(.5)
                    assert stable_shapes == sum(item["action"] == "shape" for item in messages), "Stable detail kept sending native clipping messages"
                    left_click(target('.mirally-companion__header [data-action=collapse]'))
                    wait_for(lambda: js("document.querySelector('.mirally-companion').dataset.view==='compact'"),
                        appearance + " explicit collapse did not return to the anchor")
                    assert js("document.querySelector('.mirally-companion').dataset.pinned==='false' && document.activeElement===document.querySelector('.mirally-companion__rail')"), "Collapse lost keyboard return focus or its pinned lock"
                    time.sleep(.25)
                    assert native()["bounds"] == compact["bounds"], "Collapse resized the native WebView host"
                    motion = js("""(() => {
                        const probe = window.__shellMotionProbe;
                        probe.active = false;
                        const deltas = probe.deltas.filter(Number.isFinite).sort((a,b) => a-b);
                        return {frames: deltas.length, p95_ms: deltas[Math.floor(deltas.length*.95)] ?? null,
                            maximum_ms: deltas.at(-1) ?? null};
                    })()""")
                    results.append({"case": appearance + " native quick preview, explicit detail and collapse",
                        "fixed_host_bounds": True, "preview_shape_messages": preview_shapes,
                        "native_editor_and_controls_visible": True, "stable_detail_has_no_shape_traffic": True,
                        "focused_selection_protects_reading": True, "native_minimize_blur_releases_stale_selection": True,
                        "hover_input": "WebView2 CDP Input.dispatchMouseEvent (trusted browser input)",
                        "frame_cadence": motion})
                    js("window.__mirallyCompanion.workspace()")
                    wait_for(lambda: shell._workspace is None and not native()["region"], "Preview did not restore workbench")
                    assert native()["bounds"] == initial["bounds"], "Preview changed saved workbench bounds"

                # Suppress only the page's two context-menu event handlers so a
                # real right-click must pass through native WebView2 fallback.
                js("window.__mirallyCompanion.setForm('pet',false);window.__mirallyCompanion.dock()")
                wait_for(lambda: shell._workspace is not None, "Fallback test dock failed")
                js("window.__mirallyCompanion.setView('detail');document.querySelector('[data-slot=draft]').focus();window.__shellStopContext=e=>{if(e.button===2)e.stopImmediatePropagation()};document.addEventListener('pointerup',window.__shellStopContext,true);document.addEventListener('contextmenu',window.__shellStopContext,true)")
                time.sleep(.3)
                count = len(native_context)
                right_click(target('.mirally-companion__header', .93, .3))
                check_menu("native WebView2 fallback")
                wait_for(lambda: len(native_context) > count, "Native ContextMenuRequested did not fire")
                assert native_context[-1]["handled"] and not native_context[-1]["editable"], native_context
                js("document.removeEventListener('pointerup',window.__shellStopContext,true);document.removeEventListener('contextmenu',window.__shellStopContext,true);delete window.__shellStopContext")
                results.append({"case": "real WebView2 ContextMenuRequested fallback", "handled": True, "menu_button_visible": True})

                js("window.__mirallyCompanion.setView('detail');document.querySelector('[data-slot=draft]').focus()")
                time.sleep(.3)
                count = len(native_context)
                right_click(target('[data-slot="draft"]'))
                wait_for(lambda: len(native_context) > count, "Native editing context menu did not fire")
                assert native_context[-1]["editable"] and not native_context[-1]["handled"], native_context
                assert js("document.querySelector('.mirally-companion').dataset.view==='detail'"), "Editing right-click opened companion menu"
                user.PostMessageW(hwnd, 0x1f, 0, 0)
                user.PostMessageW(render, 0x100, 0x1b, 0)
                user.PostMessageW(render, 0x101, 0x1b, 0)
                results.append({"case": "native editing menu preserved", "handled": False})
                time.sleep(.2)
                js("window.__mirallyCompanion.setView('detail');document.querySelector('[data-slot=draft]').focus()")
                time.sleep(.3)
                right_click(target('.mirally-companion__header', .93, .3))
                check_menu("native menu workspace click")
                button = target('.mirally-companion__menu-actions [data-action="workspace"]')
                post(0x200, button)
                post(0x201, button, 1)
                post(0x202, button)
                wait_for(lambda: shell._workspace is None and js("document.documentElement.dataset.mirallyMode==='workspace'"), "Native menu button did not return to workbench")
                assert native()["hwnd"] == hwnd, "Workspace created another native window"
                assert native()["bounds"] == initial["bounds"] and native()["window_state"] == initial["window_state"], snapshot()
                assert not js("window.__shellVerificationErrors.length"), snapshot()
                results.append({"case": "native menu click restores workspace HWND, size and position", "restored": True})

                # A user-sized, moved normal window must survive floating
                # movement and minimize/restore without adopting sidebar bounds.
                form.Invoke(Action(lambda: setattr(form, "Bounds", Rectangle(initial["bounds"][0] + 35, initial["bounds"][1] + 25,
                    initial["bounds"][2] - 140, initial["bounds"][3] - 110))))
                custom_normal = native()
                for appearance in ("rail", "badge", "pet"):
                    js(f"window.__mirallyCompanion.setForm('{appearance}',false);window.__mirallyCompanion.dock()")
                    wait_for(lambda: shell._workspace is not None and native()["region"], appearance + " normal dock did not finish")
                    form.Invoke(Action(lambda: form.SetDesktopLocation(100, 140)))
                    js("window.__mirallyCompanion.setView('detail')")
                    if not arguments.close_exit:
                        companion_before_minimize = native()
                        js("document.querySelector('.mirally-companion [data-action=minimize]').click()")
                        wait_for(lambda: native()["minimized"], appearance + " companion minimize did not finish")
                        tray_left_click()
                        wait_for(lambda: not native()["minimized"] and native()["visible"], appearance + " companion tray restore did not finish")
                        time.sleep(.2)
                        assert native()["bounds"] == companion_before_minimize["bounds"], appearance + " tray restore changed companion outer bounds: " + json.dumps({"before": companion_before_minimize, "after": native()})
                        assert native()["hwnd"] == hwnd and native()["region"], "Tray restore replaced the clipped companion window"
                        # Windows taskbar restoration bypasses _show_window.
                        # Exercise the actual WM_SYSCOMMAND path as well.
                        assert user.PostMessageW(hwnd, 0x112, 0xF020, 0)
                        wait_for(lambda: native()["minimized"], "Native SC_MINIMIZE did not minimize the companion")
                        assert user.PostMessageW(hwnd, 0x112, 0xF120, 0)
                        wait_for(lambda: not native()["minimized"] and native()["visible"], "Native SC_RESTORE did not restore the companion")
                        time.sleep(.2)
                        assert native()["bounds"] == companion_before_minimize["bounds"], appearance + " native SC_RESTORE changed companion outer bounds: " + json.dumps({"before": companion_before_minimize, "after": native()})
                        assert native()["hwnd"] == hwnd and native()["region"], "Taskbar restore replaced the clipped companion window"
                    js("document.querySelector('.mirally-companion [data-action=workspace]').click()")
                    wait_for(lambda: shell._workspace is None and js("document.documentElement.dataset.mirallyMode==='workspace'"), appearance + " normal control did not restore workspace")
                    restored = native()
                    assert restored["bounds"] == custom_normal["bounds"] and restored["window_state"] == "Normal", snapshot()
                    assert restored["restore_bounds"] == custom_normal["bounds"] and not restored["region"] and not restored["topmost"], snapshot()
                    results.append({"case": appearance + " moved/minimized companion restores user-sized normal workbench", "restored": True,
                        "normal_bounds": restored["bounds"], "companion_minimize_restore": not arguments.close_exit,
                        "tray_and_native_sc_restore_preserve_companion_bounds": not arguments.close_exit})
                form.Invoke(Action(lambda: setattr(form, "Bounds", Rectangle(*initial["bounds"]))))

                # Restore both the current maximized presentation and the
                # underlying normal bounds after moving the floating companion.
                form.Invoke(Action(lambda: setattr(form, "WindowState", FormWindowState.Maximized)))
                wait_for(lambda: native()["window_state"] == "Maximized", "Workbench did not maximize")
                maximized = native()
                assert maximized["restore_bounds"] == initial["bounds"], maximized
                for appearance in ("rail", "badge", "pet"):
                    js(f"window.__mirallyCompanion.setForm('{appearance}',false);window.__mirallyCompanion.dock()")
                    wait_for(lambda: shell._workspace is not None and native()["region"], appearance + " maximized dock did not finish")
                    form.Invoke(Action(lambda: form.SetDesktopLocation(80, 120)))
                    js("window.__mirallyCompanion.setView('detail')")
                    wait_for(lambda: js("!!document.querySelector('.mirally-companion [data-action=workspace]')"), "Workspace restore control missing")
                    js("document.querySelector('.mirally-companion [data-action=workspace]').click()")
                    wait_for(lambda: shell._workspace is None and js("document.documentElement.dataset.mirallyMode==='workspace'"), appearance + " control did not leave companion mode")
                    restored = native()
                    assert restored["window_state"] == maximized["window_state"] and restored["bounds"] == maximized["bounds"], snapshot()
                    assert restored["restore_bounds"] == initial["bounds"], snapshot()
                    results.append({"case": appearance + " moved companion restores maximized workbench and normal bounds", "restored": True,
                        "maximized_bounds": restored["bounds"], "normal_bounds": restored["restore_bounds"]})
                form.Invoke(Action(lambda: setattr(form, "WindowState", FormWindowState.Normal)))
                wait_for(lambda: native()["window_state"] == "Normal", "Workbench did not leave maximized state")
                assert native()["bounds"] == initial["bounds"], snapshot()

                wait_for(shell._tray_ready.is_set, "Tray was not installed")
                assert shell._tray is not None and bool(shell._tray.Visible), "Tray icon is not visible"
                if not arguments.close_exit:
                    user.PostMessageW(hwnd, 0x112, 0xF060, 0)
                    wait_for(lambda: not native()["visible"], "Windows Close did not hide to tray")
                    assert not shell.closed.is_set() and saved["process"].poll() is None, "Closing window terminated background Host"
                    tray_left_click()
                    wait_for(lambda: native()["visible"], "Single tray click did not restore window")
                    assert native()["hwnd"] == hwnd, "Tray restore created another HWND"
                    user.PostMessageW(hwnd, 0x112, 0xF020, 0)
                    wait_for(lambda: native()["minimized"], "Native minimize did not minimize")
                    tray_left_click()
                    wait_for(lambda: native()["visible"] and not native()["minimized"], "Single tray click did not restore minimized window")
                    results.append({"case": "close to tray, single-click restore and minimize restore", "host_kept_running": True, "same_hwnd": True})
                saved["bridge_counts"] = {action: sum(item["action"] == action for item in messages) for action in ("dock", "shape", "workspace")}
                assert all(saved["bridge_counts"].values()), saved["bridge_counts"]
                if arguments.page_quit:
                    # About is an application route, not one of the limited
                    # companion/native-menu compatibility page aliases.
                    js("(() => { document.querySelector('openclaw-app').context.navigate('about', {search: ''}); return true; })()")
                    wait_for(lambda: js("!!document.querySelector('[data-testid=mirally-settings-quit]:not([disabled])')"), "About Mirally quit button did not mount")
                    saved["exit_gesture"] = "About Mirally Quit" + (" with reused Host" if arguments.reuse_host else "")
                    js("document.querySelector('[data-testid=mirally-settings-quit]').click()")
                elif arguments.close_exit:
                    assert user.PostMessageW(hwnd, 0x10, 0, 0), "WM_CLOSE could not be posted"
                    saved["exit_gesture"] = "configured WM_CLOSE"
                else:
                    # Exercise the actual tray callback rather than directly
                    # calling cleanup or terminating the deterministic child.
                    form.Invoke(Action(lambda: shell._tray.ContextMenuStrip.Items[2].PerformClick()))
                    saved["exit_gesture"] = "explicit tray Quit"
            except BaseException as error:
                failures.append(repr(error))
                shell.quit()

        native_style = shell._native_style
        def ready():
            native_style()
            original_load_url = shell.window.load_url
            def load_url(url):
                timeline["product_navigation"] = time.monotonic() - began
                return original_load_url(url)
            shell.window.load_url = load_url
            threading.Thread(target=verify, name="mirally-framework-shell-verification", daemon=True).start()
        shell._native_style = ready
        try:
            shell.run()
        except BaseException as error:
            failures.append(repr(error))
        finally:
            # Inspect before emergency test cleanup; the harness must not make
            # a missing product shutdown look like a passing native exit.
            if saved.get("process") is not None:
                cleaned = {"host_exited": saved["process"].poll() is not None, "host_handle_released": host.process is None,
                    "tray_disposed": shell._tray is None, "no_ufo_registration": shell._frontend_registration is None,
                    "shell_closed": shell.closed.is_set(), "host_log_closed": host.log is None,
                    "runtime_retired": not (home / "runtime.json").exists()}
                results.append({"case": saved.get("exit_gesture", "failed verification") + " cleans up", **cleaned, "native_bridge_counts": saved.get("bridge_counts", {})})
                if not all(cleaned.values()):
                    failures.append("Native exit did not complete cleanup: " + repr(cleaned))
            host.close()
            if previous_owner is not None:
                previous_owner.close()
    report = {"results": results, "native_context_events": native_context, "failures": failures,
        "scope": "Real retained frontend/control-ui + FrameworkShell + temporary test-mode Host + WebView2; own HWND messages and trusted WebView2 CDP hover input",
        "limits": ["No physical mouse/keyboard or multi-monitor/DPI changes", "No desktop capture, Activity collection, external model, or production profile", "Tray callbacks tested through actual NotifyIcon MouseClick event and menu; notification-area pointer hit testing is not exercised"]}
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
