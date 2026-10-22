"""Offline WebView2 right-click regression on this process's own window only.

Uses a temporary profile and a loopback fixture with the real companion assets.
No Gateway, Activity collector, model API, global mouse/keyboard input, or user
profile is used. Run separately from other GUI verification.
"""
from __future__ import annotations

import base64
import ctypes
from ctypes import wintypes
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@contextmanager
def temporary_profile():
    profile = tempfile.TemporaryDirectory(prefix="mirally-companion-menu-", ignore_cleanup_errors=True)
    assert Path(profile.name).resolve().parent == Path(tempfile.gettempdir()).resolve()
    try:
        yield profile.name
    finally:
        # WebView's child processes can briefly retain profile files after the
        # host exits. Python 3.10's ignore_cleanup_errors can still raise while
        # resetting permissions; never mask the verification result with it.
        for attempt in range(5):
            try:
                profile.cleanup()
                break
            except PermissionError:
                if attempt < 4:
                    time.sleep(.25)


def fixture_page(origin):
    assets = ROOT / "mirally/assets"
    css = (assets / "control-ui-companion.css").read_text(encoding="utf-8")
    script = "\n".join((assets / name).read_text(encoding="utf-8") for name in (
        "control-ui-office.js", "control-ui-companion.js"))
    script = script.replace("__MIRALLY_ORIGIN__", json.dumps(origin)).replace(
        "__MIRALLY_MARK__", json.dumps("data:image/png;base64," + base64.b64encode(
            (assets / "mirally-128.png").read_bytes()).decode("ascii"))).replace(
        "__MIRALLY_PETS__", json.dumps({key: (assets / path).read_text(encoding="utf-8")
            for key, path in (("otter", "mirally-pet-otter.svg"), ("fox", "mirally-pet.svg"))}))
    # Only the presentation contract is synthetic; menu handlers and shapes are real.
    setup = """
    window.__fixtureEvents=[];window.__fixtureErrors=[];window.__fixtureShapes=[];
    const realPostMessage=chrome.webview.postMessage.bind(chrome.webview);
    chrome.webview.postMessage=function(value){
      if(value?.type==='mirally-companion'&&value.action==='shape'){
        __fixtureShapes.push(structuredClone(value));
        if(__fixtureShapes.length>6)__fixtureShapes.shift();
      }
      return realPostMessage(value);
    };
    addEventListener('error',e=>__fixtureErrors.push(String(e.error||e.message)));
    addEventListener('unhandledrejection',e=>__fixtureErrors.push(String(e.reason)));
    for(const name of ['pointerdown','pointerup','contextmenu'])document.addEventListener(name,e=>{
      __fixtureEvents.push({type:name,button:e.button,x:e.clientX,y:e.clientY,
        target:e.target.tagName,view:document.querySelector('.mirally-companion')?.dataset.view});
    },true);
    const pane=document.querySelector('openclaw-chat-pane');pane.presented=true;
    pane.state={sessionKey:'fixture',connected:true,chatMessage:'',chatMessages:[],chatAttachments:[],
      chatSessionApprovalQueue:[],toolStreamOrder:[],sessionsResult:{sessions:[]},
      handleSendChat(){throw Error('Unexpected synthetic send')},
      handleChatDraftChange(value){this.chatMessage=value}};
    pane.runActivity={working:false};pane.questionProjection={prompts:[]};
    document.querySelector('openclaw-app').context={};
    window.__mirallyWork={close(){},open(){},sync(){}};
    """
    return ("<!doctype html><meta charset='utf-8'>"
        "<meta http-equiv='Content-Security-Policy' content=\"default-src 'none'; "
        "script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; connect-src 'none'\">"
        "<style>body{font-family:Segoe UI;margin:0}openclaw-app{display:block;padding:24px}" + css +
        "</style><openclaw-app><h1>Offline companion menu fixture</h1>"
        "<openclaw-chat-pane class='chat-pane-cache__pane--active'></openclaw-chat-pane>"
        "</openclaw-app><script>" + setup + "</script><script>" + script + "</script>")


def main():
    if sys.platform != "win32":
        raise SystemExit("This verification requires Windows and WebView2.")
    from mirally.control_ui_shell import ControlUIShell

    user = ctypes.WinDLL("user32", use_last_error=True)
    enum_callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user.EnumChildWindows.argtypes = [wintypes.HWND, enum_callback, wintypes.LPARAM]
    user.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user.ScreenToClient.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
    user.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user.PostMessageW.restype = wintypes.BOOL
    results, failures, native_events = [], [], []
    document = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != "/":
                self.send_error(404)
                return
            data = document["html"].encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    origin = f"http://127.0.0.1:{server.server_port}"
    document["html"] = fixture_page(origin)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with temporary_profile() as directory:
            shell = ControlUIShell(Path(directory))
            shell._start_connect = lambda: None
            shell._gateway_origin = ("http", f"127.0.0.1:{server.server_port}")

            def verify():
                try:
                    from System import Action
                    from System.Drawing import Point
                    assert shell.window.events.loaded.wait(30), "Initial WebView2 load failed"
                    form = shell.window.native
                    hwnd = int(form.Handle.ToInt64())

                    def context(sender, args):
                        native_events.append({"handled": bool(args.Handled),
                            "mode": shell._surface_mode, "view": shell._surface_view})

                    def initialize():
                        form.webview.CoreWebView2.ContextMenuRequested += context
                        form.Opacity = 1
                    form.Invoke(Action(initialize))
                    shell.window.load_url(origin + "/")

                    def js(source):
                        return shell.window.evaluate_js(source)

                    def wait_for(predicate, label, timeout=8):
                        deadline = time.monotonic() + timeout
                        while time.monotonic() < deadline:
                            if predicate():
                                return
                            time.sleep(.04)
                        raise AssertionError(label + "; " + json.dumps(snapshot(), ensure_ascii=False))

                    def snapshot():
                        geometry = {}
                        def inspect_geometry():
                            view = form.webview
                            geometry.update(device_dpi=int(form.DeviceDpi),
                                client_size=[int(view.ClientSize.Width), int(view.ClientSize.Height)],
                                zoom_factor=float(view.ZoomFactor),
                                host_bounds=[int(form.Bounds.Width), int(form.Bounds.Height)])
                        form.Invoke(Action(inspect_geometry))
                        return {"dom": js("(()=>{const s=document.querySelector('.mirally-companion');return {"
                            "mode:document.documentElement.dataset.mirallyMode,view:s?.dataset.view,"
                            "form:s?.dataset.form,events:window.__fixtureEvents?.slice(-8),"
                            "innerWidth,innerHeight,devicePixelRatio,shapes:window.__fixtureShapes,"
                            "errors:window.__fixtureErrors}})()"),
                            "native": {"mode": shell._surface_mode, "view": shell._surface_view,
                                       "docked": shell._workspace is not None, **geometry},
                            "context_events": native_events[-6:]}

                    wait_for(lambda: js("!!window.__mirallyCompanion"), "Companion failed to mount")
                    children = []
                    @enum_callback
                    def collect(child, _data):
                        label = ctypes.create_unicode_buffer(256)
                        user.GetClassNameW(child, label, len(label))
                        rect = wintypes.RECT()
                        user.GetWindowRect(child, ctypes.byref(rect))
                        pid = wintypes.DWORD()
                        user.GetWindowThreadProcessId(child, ctypes.byref(pid))
                        children.append((int(child), label.value, rect.right-rect.left,
                                         rect.bottom-rect.top, pid.value))
                        return True
                    user.EnumChildWindows(hwnd, collect, 0)
                    renders = [row for row in children if row[1] == "Chrome_RenderWidgetHostHWND"]
                    assert renders, f"No WebView2 render input HWND under own host: {children}"
                    render = max(renders, key=lambda row: row[2]*row[3])[0]

                    def target(selector, fraction_x=.5, fraction_y=.5):
                        return js("(()=>{const r=document.querySelector(" + json.dumps(selector) +
                            ").getBoundingClientRect();return {x:r.x+r.width*" + str(fraction_x) +
                            ",y:r.y+r.height*" + str(fraction_y) +
                            ",vw:innerWidth,vh:innerHeight,width:r.width,height:r.height}})()")

                    def post(message, where, buttons=0):
                        position = {}
                        def convert():
                            view = form.webview
                            screen = view.PointToScreen(Point(round(where["x"] * view.ClientSize.Width / where["vw"]),
                                round(where["y"] * view.ClientSize.Height / where["vh"])))
                            position.update(x=screen.X, y=screen.Y)
                        form.Invoke(Action(convert))
                        point = wintypes.POINT(position["x"], position["y"])
                        assert user.ScreenToClient(render, ctypes.byref(point))
                        packed = (point.x & 0xffff) | ((point.y & 0xffff) << 16)
                        assert user.PostMessageW(render, message, buttons, packed)

                    def right_click(where):
                        post(0x200, where)  # WM_MOUSEMOVE, own Chromium child only
                        time.sleep(.08)
                        post(0x204, where, 2)  # WM_RBUTTONDOWN / MK_RBUTTON
                        time.sleep(.06)
                        post(0x205, where)  # WM_RBUTTONUP

                    def menu_visible():
                        return js("document.querySelector('.mirally-companion').dataset.view==='menu'") and shell._surface_view == "menu"

                    def check_menu(name):
                        wait_for(menu_visible, name + " did not open the companion menu")
                        time.sleep(.8)  # A clipped pointer-leave must not make the menu flash and vanish.
                        assert menu_visible(), name + " closed before the user could choose an action"
                        button = target('.mirally-companion__menu-actions [data-action="workspace"]')
                        assert button["width"] > 0 and button["height"] > 0, "Menu button has no painted bounds"
                        clipping = {}
                        def check_clip():
                            view = form.webview
                            screen = view.PointToScreen(Point(round(button["x"] * view.ClientSize.Width / button["vw"]),
                                round(button["y"] * view.ClientSize.Height / button["vh"])))
                            point = form.PointToClient(screen)
                            clipping["visible"] = form.Region is not None and form.Region.IsVisible(point.X, point.Y)
                        form.Invoke(Action(check_clip))
                        assert clipping["visible"], "Native window region clips the menu button"
                        results.append({"case": name, "menu_visible": True, "native_clip_visible": True,
                                        "snapshot": snapshot()})

                    for appearance, hovered in (("pet", "compact"), ("badge", "quick"), ("rail", "detail")):
                        js(f"window.__mirallyCompanion.setForm('{appearance}',false);window.__mirallyCompanion.dock()")
                        wait_for(lambda: shell._workspace is not None, "Dock was not delivered")
                        time.sleep(.4)
                        anchor = target('.mirally-companion__rail')
                        if appearance != "pet":
                            post(0x200, anchor)
                            wait_for(lambda: js("document.querySelector('.mirally-companion').dataset.view") == hovered,
                                     appearance + " did not expand on native hover")
                            # Posted moves do not relocate the system cursor;
                            # Windows may synthesize a leave at its real position.
                            # Focus keeps the already-hovered editor open while
                            # we verify native right-click and clipping separately.
                            js("document.querySelector('[data-slot=\"draft\"]').focus()")
                            time.sleep(.3)
                        where = target('.mirally-companion__header') if appearance == "rail" else target('.mirally-companion__rail')
                        right_click(where)
                        check_menu(appearance + " " + hovered)
                        js("window.__mirallyCompanion.workspace()")
                        wait_for(lambda: shell._workspace is None, "Workspace bounds were not restored")

                    # The expanded panel has its own context surface, distinct from editing.
                    js("window.__mirallyCompanion.setForm('pet',false);window.__mirallyCompanion.dock()")
                    wait_for(lambda: shell._workspace is not None, "Final dock failed")
                    for name, selector in (("detail header", '.mirally-companion__header'),
                                           ("detail blank", '.mirally-companion__body')):
                        js("window.__mirallyCompanion.setView('detail')")
                        js("document.querySelector('[data-slot=\"draft\"]').focus()")
                        time.sleep(.35)
                        right_click(target(selector, .93, .05))
                        check_menu(name)

                    js("window.__mirallyCompanion.setView('detail')")
                    js("document.querySelector('[data-slot=\"draft\"]').focus()")
                    time.sleep(.35)
                    count = len(native_events)
                    right_click(target('[data-slot="draft"]'))
                    wait_for(lambda: len(native_events) > count, "Textarea did not retain native editing context menu")
                    assert not native_events[-1]["handled"], "Editing context menu was intercepted"
                    assert js("document.querySelector('.mirally-companion').dataset.view") == "detail", "Textarea opened companion menu"
                    results.append({"case": "textarea editing", "native_menu_preserved": True})
                    user.PostMessageW(hwnd, 0x1f, 0, 0)  # WM_CANCELMODE, own host only
                    user.PostMessageW(render, 0x100, 0x1b, 0)  # Escape, own render only
                    user.PostMessageW(render, 0x101, 0x1b, 0)
                    time.sleep(.15)
                    js("window.__mirallyCompanion.setView('detail')")
                    js("document.querySelector('[data-slot=\"draft\"]').focus()")
                    time.sleep(.35)
                    right_click(target('.mirally-companion__header'))
                    check_menu("final menu")
                    button = target('.mirally-companion__menu-actions [data-action="workspace"]')
                    post(0x200, button)
                    post(0x201, button, 1)
                    post(0x202, button)
                    wait_for(lambda: shell._workspace is None and js(
                        "document.documentElement.dataset.mirallyMode==='workspace'"), "Menu click did not return to workbench")
                    assert not js("window.__fixtureErrors.length"), snapshot()
                    results.append({"case": "menu to workspace", "restored": True})
                except BaseException as error:
                    failures.append(repr(error))
                finally:
                    shell.window.destroy()

            native_style = shell._native_style
            def ready():
                native_style()
                threading.Thread(target=verify, daemon=True).start()
            shell._native_style = ready
            shell.run()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
    print(json.dumps({"results": results, "native_events": native_events, "failures": failures}, ensure_ascii=False, indent=2))
    if failures:
        raise AssertionError("; ".join(failures))


if __name__ == "__main__":
    main()
