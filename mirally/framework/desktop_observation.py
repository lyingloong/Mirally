"""On-demand desktop observations; never called by the Activity collector."""
import base64
import ctypes
import io
import json
import os
import sys
from contextlib import contextmanager
from ctypes import wintypes

from mirally.platform.window_identity import is_mirally_window

from .desktop_controls import (
    DesktopControlError, prepare_operation, read_verified_value, resolve_target, scan_controls, snapshot_record,
)

DWMWA_CLOAKED = 14
CLOAK_SHOWN = 'shown'
CLOAK_CLOAKED = 'cloaked'
CLOAK_UNKNOWN = 'unknown'


def observe(kind):
    if os.name != 'nt':
        raise RuntimeError('桌面观察仅支持 Windows。')
    if kind == 'screen':
        from PIL import Image, ImageGrab
        image = ImageGrab.grab(all_screens=True).convert('RGB')
        image.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
        output = io.BytesIO()
        image.save(output, 'JPEG', quality=80, optimize=True)
        return {'width': image.width, 'height': image.height,
                'data': base64.b64encode(output.getvalue()).decode('ascii')}
    if kind != 'foreground':
        raise RuntimeError('未知桌面观察类型。')
    user = ctypes.WinDLL('user32', use_last_error=True)
    user.GetForegroundWindow.restype = wintypes.HWND
    user.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    hwnd = user.GetForegroundWindow()
    if not hwnd:
        raise RuntimeError('当前没有可读取的前台窗口。')
    title = ctypes.create_unicode_buffer(min(user.GetWindowTextLengthW(hwnd), 1024) + 1)
    user.GetWindowTextW(hwnd, title, len(title))
    rect = wintypes.RECT()
    if not user.GetWindowRect(hwnd, ctypes.byref(rect)):
        raise RuntimeError('当前窗口的位置不可用。')
    return {'title': title.value, 'bounds': {'left': rect.left, 'top': rect.top,
            'right': rect.right, 'bottom': rect.bottom}}


def windows_api():
    if os.name != 'nt':
        raise RuntimeError('桌面操作仅支持 Windows。')
    user = ctypes.WinDLL('user32', use_last_error=True)
    for name in ('IsWindow', 'IsWindowVisible', 'IsIconic', 'GetWindowTextLengthW'):
        getattr(user, name).argtypes = [wintypes.HWND]
    user.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    return user


def dwm_api():
    dwm = ctypes.WinDLL('dwmapi', use_last_error=True)
    dwm.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
    return dwm


def cloak_state(dwm, hwnd):
    """Whether DWM shows the window to the user, hides it, or could not answer.

    Only CLOAK_SHOWN confirms the window is on screen; unknown states must never be
    presented or operated as a window the user can see.
    """
    cloaked = wintypes.DWORD()
    if dwm.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, ctypes.byref(cloaked), ctypes.sizeof(cloaked)) != 0:
        return CLOAK_UNKNOWN
    return CLOAK_CLOAKED if cloaked.value else CLOAK_SHOWN


def window_record(user, hwnd):
    if not user.IsWindow(hwnd) or not user.IsWindowVisible(hwnd) or user.IsIconic(hwnd):
        raise RuntimeError('目标窗口不存在、已隐藏或已最小化。')
    title = ctypes.create_unicode_buffer(min(user.GetWindowTextLengthW(hwnd), 1024) + 1)
    user.GetWindowTextW(hwnd, title, len(title))
    pid, rect = wintypes.DWORD(), wintypes.RECT()
    user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if not user.GetWindowRect(hwnd, ctypes.byref(rect)):
        raise RuntimeError('无法读取窗口位置。')
    return {'hwnd': int(hwnd), 'pid': pid.value, 'title': title.value,
            'bounds': {'left': rect.left, 'top': rect.top, 'right': rect.right, 'bottom': rect.bottom}}


def list_windows():
    user, records = windows_api(), []
    try:
        dwm = dwm_api()
    except OSError as error:
        raise RuntimeError('无法确认窗口的显示状态。请稍后重试。') from error
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(hwnd, _):
        try:
            if is_mirally_window(user, hwnd) or cloak_state(dwm, hwnd) != CLOAK_SHOWN:
                return True
            record = window_record(user, hwnd)
            if record['title'] and len(records) < 100:
                records.append(record)
        except RuntimeError:
            pass
        return True
    user.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    if not user.EnumWindows(callback_type(visit), 0):
        raise RuntimeError('无法列出桌面窗口。')
    return {'windows': records, 'untrusted_content': True}


@contextmanager
def physical_window_coordinates(user):
    user.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    user.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
    previous = user.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
    if not previous:
        raise DesktopControlError('无法确认窗口的物理像素坐标。')
    try:
        yield
    finally:
        user.SetThreadDpiAwarenessContext(previous)


def _process_started(pid):
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        raise DesktopControlError('无法确认窗口所属进程。请重新观察窗口。')
    try:
        created, ended, kernel_time, user_time = (wintypes.FILETIME() for _ in range(4))
        if not kernel.GetProcessTimes(handle, created, ended, kernel_time, user_time):
            raise DesktopControlError('无法确认窗口所属进程。请重新观察窗口。')
        return str((created.dwHighDateTime << 32) | created.dwLowDateTime)
    finally:
        kernel.CloseHandle(handle)


def _requested_window(args):
    window = args.get('window') if isinstance(args, dict) else None
    if not isinstance(window, dict):
        raise DesktopControlError('请选择一个已观察到的窗口。')
    hwnd, pid = window.get('hwnd'), window.get('pid')
    if (type(hwnd) is not int or not 0 < hwnd < 2**53 or type(pid) is not int or not 0 < pid < 2**32
            or ('title' in window and (not isinstance(window['title'], str) or len(window['title']) > 1024))):
        raise DesktopControlError('窗口选择无效。请重新观察窗口。')
    return hwnd, pid


def _control_window(user, hwnd, expected_pid):
    if is_mirally_window(user, hwnd):
        raise DesktopControlError('请选择要操作的应用窗口。Mirally 窗口中的确认需要你亲自处理。')
    try:
        record = window_record(user, hwnd)
    except RuntimeError as error:
        raise DesktopControlError(str(error)) from error
    if record['pid'] != expected_pid:
        raise DesktopControlError('窗口所属进程已变化。请重新观察窗口。')
    root = 2  # GA_ROOT
    user.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user.GetAncestor.restype = wintypes.HWND
    if user.GetAncestor(hwnd, root) != hwnd:
        raise DesktopControlError('请选择可见的顶层窗口。')
    try:
        cloak = cloak_state(dwm_api(), hwnd)
    except OSError as error:
        raise DesktopControlError('无法确认目标窗口是否已显示。请重新观察窗口。') from error
    if cloak == CLOAK_CLOAKED:
        raise DesktopControlError('目标窗口当前未显示。请重新观察窗口。')
    if cloak != CLOAK_SHOWN:
        raise DesktopControlError('无法确认目标窗口是否已显示。请重新观察窗口。')
    process_started = _process_started(expected_pid)
    user.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    class_name = ctypes.create_unicode_buffer(256)
    if not user.GetClassNameW(hwnd, class_name, len(class_name)):
        raise DesktopControlError('无法确认窗口身份。请重新观察窗口。')
    pid = wintypes.DWORD()
    thread = user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    bounds = record['bounds']
    if not thread or pid.value != expected_pid or bounds['left'] >= bounds['right'] or bounds['top'] >= bounds['bottom']:
        raise DesktopControlError('窗口状态已变化。请重新观察窗口。')
    return {**record, 'process_started': process_started, 'thread_id': thread, 'class_name': class_name.value}


def _scan_window(auto, window, read_window):
    root = auto.ControlFromHandle(window['hwnd'])
    if root is None or root.ProcessId != window['pid'] or root.NativeWindowHandle != window['hwnd']:
        raise DesktopControlError('无法确认窗口的辅助功能树。请重新观察窗口。')
    scan = scan_controls(root, auto, window['bounds'])
    if read_window() != window:
        raise DesktopControlError('窗口在观察期间发生变化。请重新观察窗口。')
    return scan


def snapshot(args):
    from uiautomation import uiautomation as auto

    hwnd, pid = _requested_window(args)
    include_image = args.get('include_image', False)
    if type(include_image) is not bool:
        raise DesktopControlError('窗口截图选项无效。')
    user = windows_api()

    def read_window():
        return _control_window(user, hwnd, pid)

    with physical_window_coordinates(user):
        window = read_window()
        if 'title' in args['window'] and window['title'] != args['window']['title']:
            raise DesktopControlError('窗口标题已变化。请重新观察窗口。')
        result = snapshot_record(window, _scan_window(auto, window, read_window))
        if include_image:
            from .desktop_snapshot_image import capture_annotated_window

            image = capture_annotated_window(result, read_window)
            if image['status'] == 'ok':
                try:
                    fresh = snapshot_record(window, _scan_window(auto, window, read_window))
                except Exception:
                    fresh = None
                if fresh != result:
                    image = {'status': 'unavailable', 'reason': 'window_changed'}
            result['image'] = image
        return result


def act(args):
    from uiautomation import uiautomation as auto

    hwnd, pid = _requested_window(args)
    operation, text = args.get('operation'), args.get('text')
    if operation not in ('invoke', 'set_value') or (operation == 'invoke' and 'text' in args):
        raise DesktopControlError('桌面动作参数无效。')
    if operation == 'set_value' and (not isinstance(text, str) or len(text) > 512 or
            any(ord(char) < 32 or 127 <= ord(char) <= 159 or 0xd800 <= ord(char) <= 0xdfff for char in text)):
        raise DesktopControlError('输入支持 0–512 个可见字符。')
    user = windows_api()

    def read_window():
        return _control_window(user, hwnd, pid)

    with physical_window_coordinates(user):
        expected_window = args['window']
        window = read_window()
        if window != expected_window:
            raise DesktopControlError('窗口身份或位置已变化。请重新观察窗口。')
        target = resolve_target(_scan_window(auto, window, read_window), args.get('target'), operation)
        perform = prepare_operation(auto, target, operation, text, window['bounds'])
        if read_window() != expected_window:
            raise DesktopControlError('窗口在操作前发生变化。请重新观察窗口。')

        result = {'status': 'unknown'}
        try:
            if perform(waitTime=0.1) is True:
                result['status'] = 'sent'
        except Exception:
            # An external provider can raise even after producing a side effect.
            pass

        try:
            current_window = read_window()
            if any(current_window[key] != window[key] for key in ('hwnd', 'pid', 'process_started', 'thread_id', 'class_name')):
                return result
            fresh = _scan_window(auto, current_window, read_window)
            result['after'] = snapshot_record(current_window, fresh)
            if operation == 'set_value' and result['status'] == 'sent':
                current_target = resolve_target(fresh, target.record, operation)
                if read_verified_value(auto, current_target, current_window['bounds']) == text:
                    result['status'] = 'verified'
        except Exception:
            # Report the observed dispatch status if the application closed,
            # changed its controls, or did not provide fresh verification.
            pass
        return result


def main():
    kind = sys.argv[1] if len(sys.argv) == 2 else ''
    try:
        if kind == 'windows':
            result = list_windows()
        elif kind == 'snapshot':
            result = snapshot(json.load(sys.stdin))
        elif kind == 'action':
            result = act(json.load(sys.stdin))
        else:
            result = observe(kind)
        print(json.dumps(result, ensure_ascii=True))
    except DesktopControlError as error:
        print(json.dumps({'status': 'rejected', 'error': str(error)}, ensure_ascii=True))
        raise SystemExit(1)
    except Exception as error:
        # Provider exceptions can contain window text. Avoid exposing it in the
        # task-scoped protocol or treating an unexpected action exit as a reject.
        message = '无法读取 Windows 窗口控件。' if kind in ('snapshot', 'action') else str(error)
        print(json.dumps({'error': message}, ensure_ascii=True))
        raise SystemExit(1)


if __name__ == '__main__':
    main()
