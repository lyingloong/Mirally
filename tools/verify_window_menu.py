"""Verify Mirally's native icon menu in an offline, temporary WebView2 profile.

Only this process's synthetic window receives messages. No Gateway connection,
model invocation, activity capture, user profile or desktop screenshot is used.
"""
import ctypes
import argparse
from ctypes import wintypes
import json
from pathlib import Path
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--interactive', action='store_true',
        help='Show the isolated fixture for human or authorized computer-use validation; send no input.')
    arguments = parser.parse_args()
    if sys.platform != 'win32':
        raise SystemExit('This verification requires Windows and WebView2.')
    from mirally.control_ui_shell import ControlUIShell
    user = ctypes.WinDLL('user32', use_last_error=True)
    for name in ('GetSystemMenu',):
        getattr(user, name).argtypes = [ctypes.c_void_p, ctypes.c_bool]
        getattr(user, name).restype = ctypes.c_void_p
    user.GetMenuItemCount.argtypes = [ctypes.c_void_p]
    user.GetMenuItemID.argtypes = [ctypes.c_void_p, ctypes.c_int]
    user.GetMenuItemID.restype = ctypes.c_uint
    user.GetMenuStringW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_wchar_p, ctypes.c_int, ctypes.c_uint]
    user.PostMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
    user.SendMessageW.argtypes = user.PostMessageW.argtypes
    user.SendMessageW.restype = ctypes.c_ssize_t
    user.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD)]
    user.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.RECT)]
    class GUIInfo(ctypes.Structure):
        _fields_ = [('cbSize', wintypes.DWORD), ('flags', wintypes.DWORD)] + [
            (name, wintypes.HWND) for name in ('hwndActive', 'hwndFocus', 'hwndCapture',
            'hwndMenuOwner', 'hwndMoveSize', 'hwndCaret')] + [('rcCaret', wintypes.RECT)]
    user.GetGUIThreadInfo.argtypes = [wintypes.DWORD, ctypes.POINTER(GUIInfo)]
    result, errors = {}, []
    # WebView2's Crashpad process can release its profile after the host closes.
    # Cleanup failure must not mask a menu verification failure.
    with tempfile.TemporaryDirectory(prefix='mirally-window-menu-', ignore_cleanup_errors=True) as directory:
        shell = ControlUIShell(Path(directory))
        shell._start_connect = lambda: None
        triggered = threading.Event()
        callback_threads = []
        shell._activity_settings_page = lambda: (callback_threads.append(threading.get_ident()), triggered.set())
        if arguments.interactive:
            # Every product operation is synthetic in this fixture, including
            # reconnect/restart and history. Only Windows Close remains native.
            def delivered(label):
                from System import Action
                print('ACTION DELIVERED: ' + label, flush=True)
                shell.window.native.Invoke(Action(lambda: setattr(shell.window.native, 'Text',
                    'Mirally synthetic menu verification — ' + label)))
            shell._dock = lambda: delivered('侧边伙伴')
            shell._retry = lambda: delivered('重新连接')
            shell._restart_gateway = lambda: delivered('重启 Gateway')
            shell._activity_settings_page = lambda: delivered('活动与记忆设置')
            shell._activity_browser = lambda: delivered('活动历史')
            shell._browser = lambda: delivered('在浏览器中打开')
            shell._activity_action = lambda message: delivered('synthetic activity bridge')

        def verify():
            try:
                from System import Action
                from System.Windows.Forms import MenuStrip
                assert shell.window.events.loaded.wait(30), 'WebView2 did not load'
                form = shell.window.native
                snapshot = {}
                def inspect():
                    snapshot['hwnd'] = int(form.Handle.ToInt64())
                    snapshot['ui_thread'] = threading.get_ident()
                    snapshot['strips'] = sum(isinstance(control, MenuStrip) for control in form.Controls)
                    snapshot['show_icon'] = bool(form.ShowIcon)
                    form.Opacity = 1
                    form.Text = 'Mirally synthetic menu verification'
                form.Invoke(Action(inspect))
                hwnd = snapshot['hwnd']
                assert snapshot['strips'] == 0, 'A MenuStrip still consumes a client row'
                menu = user.GetSystemMenu(hwnd, False)
                items = []
                for index in range(user.GetMenuItemCount(menu)):
                    label = ctypes.create_unicode_buffer(256)
                    user.GetMenuStringW(menu, index, label, len(label), 0x400)
                    items.append((user.GetMenuItemID(menu, index), label.value))
                labels = [label for _, label in items]
                assert '活动与记忆设置' in labels and '重启 Gateway…' in labels, labels
                assert 0xF060 in [identifier for identifier, _ in items], 'Windows Close was removed'
                tid = user.GetWindowThreadProcessId(hwnd, None)
                rectangle = wintypes.RECT()
                assert user.GetWindowRect(hwnd, ctypes.byref(rectangle))
                icon_position = ((rectangle.top + 16) & 0xFFFF) << 16 | ((rectangle.left + 16) & 0xFFFF)
                assert snapshot['show_icon'], 'Caption icon is disabled'
                hit_test = user.SendMessageW(hwnd, 0x84, 0, icon_position)  # WM_NCHITTEST
                assert hit_test == 3, f'Caption icon does not expose HTSYSMENU: {hit_test}'
                def menu_opened():
                    info = GUIInfo(cbSize=ctypes.sizeof(GUIInfo))
                    return bool(user.GetGUIThreadInfo(tid, ctypes.byref(info)) and
                        info.flags & 4 and info.hwndMenuOwner == hwnd)
                for name, message, wparam, lparam in (
                    ('native_keyboard_menu', 0x112, 0xF100, 0x20),
                ):
                    user.PostMessageW(hwnd, message, wparam, lparam)
                    deadline = time.monotonic() + 4
                    while not menu_opened() and time.monotonic() < deadline:
                        time.sleep(.03)
                    detail = GUIInfo(cbSize=ctypes.sizeof(GUIInfo))
                    user.GetGUIThreadInfo(tid, ctypes.byref(detail))
                    if not menu_opened():
                        errors.append(f'{name} did not open this window system menu '
                            f'(flags={detail.flags}, owner={detail.hwndMenuOwner}, '
                            f'active={detail.hwndActive}, own={hwnd})')
                        continue
                    user.PostMessageW(hwnd, 0x1F, 0, 0)  # WM_CANCELMODE only our window
                    deadline = time.monotonic() + 3
                    while menu_opened() and time.monotonic() < deadline:
                        time.sleep(.03)
                    assert not menu_opened(), 'Popup failed to close'
                    result[name] = True
                identifier = next(identifier for identifier, label in items if label == '活动与记忆设置')
                user.SendMessageW(hwnd, 0x112, identifier, 0)
                assert triggered.wait(3), 'Custom command callback not delivered'
                assert callback_threads[0] != snapshot['ui_thread'], 'Callback blocks UI thread'
                def roundtrip():
                    shell._companion_action({'action': 'dock'})
                    assert int(form.Handle.ToInt64()) == hwnd
                    shell._companion_action({'action': 'workspace'})
                    assert int(form.Handle.ToInt64()) == hwnd
                form.Invoke(Action(roundtrip))
                triggered.clear()
                user.SendMessageW(hwnd, 0x112, identifier, 0)
                assert triggered.wait(3), 'Menu stopped working after companion mode'
                result.update(menu_strip_count=0, original_windows_close=True,
                    callbacks_off_ui_thread=True, hwnd_preserved=True,
                    caption_icon_hit_test=hit_test, labels=labels,
                    physical_icon_click='Requires separate interactive verification')
            except BaseException as error:
                errors.append(repr(error))
            finally:
                shell.window.destroy()

        # pywebview's start callback runs in a background thread after the HWND exists.
        native_style = shell._native_style
        def ready():
            native_style()
            if arguments.interactive:
                from System import Action
                def reveal():
                    shell.window.native.Text = 'Mirally synthetic menu verification'
                    shell.window.native.Opacity = 1
                shell.window.native.Invoke(Action(reveal))
                print('INTERACTIVE FIXTURE READY: Mirally synthetic menu verification', flush=True)
            else:
                threading.Thread(target=verify, daemon=True).start()
        shell._native_style = ready
        shell.run()
        time.sleep(.5)
    result['failures'] = errors
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if errors:
        raise AssertionError('; '.join(errors))


if __name__ == '__main__':
    main()
