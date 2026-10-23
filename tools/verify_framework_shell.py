"""Verify the real framework desktop with an isolated offline test-mode Host.

Run this Windows/WebView2 check separately from other GUI checks. Only the
current process's HWND and its Chromium child receive messages. It neither
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
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


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
    arguments = parser.parse_args()
    if sys.platform != "win32":
        raise SystemExit("This verification requires Windows and WebView2.")
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
    user.GetSystemMenu.argtypes = [wintypes.HWND, wintypes.BOOL]
    user.GetSystemMenu.restype = wintypes.HMENU
    user.GetMenuItemCount.argtypes = [wintypes.HMENU]
    user.GetMenuItemID.argtypes = [wintypes.HMENU, ctypes.c_int]
    user.GetMenuItemID.restype = wintypes.UINT
    user.GetMenuStringW.argtypes = [wintypes.HMENU, wintypes.UINT, wintypes.LPWSTR, ctypes.c_int, wintypes.UINT]

    results, failures, native_context, messages = [], [], [], []
    timeline, startup, saved = {}, {}, {}
    began = time.monotonic()
    with temporary_home() as home:
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
            if isinstance(value, dict) and value.get("type") in {"mirally-companion", "mirally-ufo-input"}:
                messages.append({"type": value["type"], "action": value.get("action"), "editing": value.get("editing")})
            original_message(sender, args)
            if first_paint:
                startup.update(opacity_after=float(shell.window.native.Opacity), gate=shell._startup_visible.is_set())
        shell._web_message = message

        def verify():
            try:
                from System import Action
                from System.Drawing import Point
                from System.Windows.Forms import MenuStrip
                form = shell.window.native
                def js(source):
                    return shell.window.evaluate_js(source)
                def native():
                    value = {}
                    def inspect():
                        value.update(hwnd=int(form.Handle.ToInt64()), visible=bool(form.Visible),
                            opacity=float(form.Opacity), region=form.Region is not None,
                            topmost=bool(form.TopMost), title=str(form.Text), show_icon=bool(form.ShowIcon),
                            has_icon=form.Icon is not None, strips=sum(isinstance(control, MenuStrip) for control in form.Controls),
                            dpi=int(form.DeviceDpi), view_size=[int(form.webview.ClientSize.Width), int(form.webview.ClientSize.Height)],
                            bounds=[int(form.Bounds.X), int(form.Bounds.Y), int(form.Bounds.Width), int(form.Bounds.Height)])
                    form.Invoke(Action(inspect))
                    return value
                def snapshot():
                    return {"native": native(), "surface_mode": shell._surface_mode, "surface_view": shell._surface_view,
                        "dom": js("(()=>{const s=document.querySelector('.mirally-companion');return {mode:document.documentElement.dataset.mirallyMode,form:s?.dataset.form,view:s?.dataset.view,page:window.__mirallyFramework?.state.page,connected:window.__mirallyFramework?.state.connected,notice:document.getElementById('notice')?.textContent,errors:window.__shellVerificationErrors||[]}})()")}
                def wait_for(predicate, label, timeout=10):
                    deadline = time.monotonic() + timeout
                    while time.monotonic() < deadline:
                        if predicate():
                            return
                        time.sleep(.04)
                    raise AssertionError(label + "; " + json.dumps(snapshot(), ensure_ascii=False))
                assert shell.window.events.loaded.wait(30), "Opening page did not load"
                wait_for(lambda: js("!!window.__mirallyFramework && !!window.__mirallyCompanion && !!window.__mirallyWork && !!window.__mirallyContext && window.__mirallyFramework.state.connected"),
                    "Owned product and retained experiences did not mount", timeout=90)
                js("window.__shellVerificationErrors=[];addEventListener('error',e=>window.__shellVerificationErrors.push(e.message));addEventListener('unhandledrejection',e=>window.__shellVerificationErrors.push(String(e.reason)))")
                assert host.runtime.get("mode") == "test", "Verification must use the deterministic isolated Host"
                assert startup == {"opacity_before": 0.0, "source": "about:blank", "opacity_after": 1.0, "gate": True}, startup
                assert timeline["host_start"] < timeline["product_navigation"], timeline
                assert timeline["first_paint"] <= timeline["product_navigation"], timeline
                initial = native()
                hwnd = initial["hwnd"]
                assert initial["title"] == "" and initial["show_icon"] and initial["has_icon"] and initial["strips"] == 0, initial
                assert initial["opacity"] == 1 and not initial["region"], initial
                saved.update(process=host.process, registration=shell._frontend_registration.path)
                results.append({"case": "real Host and opening first paint", "mode": "test", "startup": startup, "timeline_seconds": timeline,
                    "same_native_window": True, "formal_icon": True, "blank_caption": True, "no_menu_strip": True})

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
                wait_for(lambda: js("window.__mirallyFramework.state.page==='memory'"), "Real system-menu memory action was not delivered")
                js("window.__mirallyFramework.showPage('chat')")
                results.append({"case": "native product system menu", "labels": labels, "memory_navigation": True})

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
                def right_click(where):
                    post(0x200, where)
                    time.sleep(.08)
                    post(0x204, where, 2)
                    time.sleep(.06)
                    post(0x205, where)
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
                    anchor = target('.mirally-companion__rail')
                    assert anchor["width"] > 0 and anchor["height"] > 0 and point(anchor)["clipped"], appearance + " anchor clipped"
                    right_click(anchor)
                    check_menu(appearance)
                    results.append({"case": appearance + " native companion", "same_hwnd": True, "topmost": True,
                        "native_region": True, "anchor_visible": True, "right_click_menu": True, "menu_button_visible": True,
                        "native_size": compact["bounds"][2:], "device_dpi": compact["dpi"]})
                    js("window.__mirallyCompanion.workspace()")
                    wait_for(lambda: shell._workspace is None and not native()["region"], appearance + " did not restore workbench")
                    restored = native()
                    assert restored["hwnd"] == hwnd and restored["bounds"] == initial["bounds"] and not restored["topmost"], restored

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
                assert not js("window.__shellVerificationErrors.length"), snapshot()
                results.append({"case": "native menu click returns to same workbench HWND", "restored": True})

                wait_for(shell._tray_ready.is_set, "Tray was not installed")
                assert shell._tray is not None and bool(shell._tray.Visible), "Tray icon is not visible"
                user.PostMessageW(hwnd, 0x112, 0xF060, 0)
                wait_for(lambda: not native()["visible"], "Windows Close did not hide to tray")
                assert not shell.closed.is_set() and host.process.poll() is None, "Closing window terminated background Host"
                form.Invoke(Action(lambda: shell._tray.ContextMenuStrip.Items[0].PerformClick()))
                wait_for(lambda: native()["visible"], "Tray Open did not restore window")
                assert native()["hwnd"] == hwnd, "Tray restore created another HWND"
                results.append({"case": "close to tray and actual tray Open callback", "host_kept_running": True, "same_hwnd": True})
                saved["bridge_counts"] = {action: sum(item["action"] == action for item in messages) for action in ("dock", "shape", "workspace")}
                assert all(saved["bridge_counts"].values()), saved["bridge_counts"]
                # Exercise the actual tray callback rather than directly
                # calling cleanup or terminating the deterministic child.
                form.Invoke(Action(lambda: shell._tray.ContextMenuStrip.Items[2].PerformClick()))
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
            host.close()
        if saved.get("process") is not None:
            cleaned = {"host_exited": saved["process"].poll() is not None, "host_handle_released": host.process is None,
                "tray_disposed": shell._tray is None, "registration_removed": not saved["registration"].exists(),
                "shell_closed": shell.closed.is_set(), "host_log_closed": host.log is None}
            results.append({"case": "explicit tray Quit cleans up", **cleaned, "native_bridge_counts": saved.get("bridge_counts", {})})
            if not all(cleaned.values()):
                failures.append("Explicit quit did not complete cleanup: " + repr(cleaned))
    report = {"results": results, "native_context_events": native_context, "failures": failures,
        "scope": "Real FrameworkShell + real temporary test-mode Host + WebView2; only own HWND messages",
        "limits": ["No physical mouse/keyboard or multi-monitor/DPI changes", "No desktop capture, Activity collection, external model, or production profile", "Tray callback tested through NotifyIcon menu; Windows notification-area pointer hit testing is not exercised"]}
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
