"""On-demand desktop observations; never called by the Activity collector."""
import base64
import ctypes
import io
import json
import os
import sys
from ctypes import wintypes


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
    for name in ('GetForegroundWindow',):
        getattr(user, name).restype = wintypes.HWND
    for name in ('IsWindow', 'IsWindowVisible', 'IsIconic', 'GetWindowTextLengthW'):
        getattr(user, name).argtypes = [wintypes.HWND]
    user.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user.SetForegroundWindow.argtypes = [wintypes.HWND]
    user.WindowFromPoint.argtypes = [wintypes.POINT]
    user.WindowFromPoint.restype = wintypes.HWND
    user.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user.GetAncestor.restype = wintypes.HWND
    user.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
    return user


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
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(hwnd, _):
        try:
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


def act(args):
    user = windows_api()
    expected = args['window']
    hwnd = int(expected['hwnd'])
    if window_record(user, hwnd) != expected:
        raise RuntimeError('窗口信息已变化，请重新观察并确认。')
    if user.GetForegroundWindow() != hwnd:
        # A background helper cannot normally claim foreground focus. Attach to
        # the current foreground thread only for the explicitly approved switch.
        foreground = user.GetForegroundWindow()
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.GetCurrentThreadId.restype = wintypes.DWORD
        foreground_thread = user.GetWindowThreadProcessId(foreground, None) if foreground else 0
        own_thread = kernel.GetCurrentThreadId()
        user.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
        attached = bool(foreground_thread and foreground_thread != own_thread and
                        user.AttachThreadInput(own_thread, foreground_thread, True))
        try:
            user.SetForegroundWindow(hwnd)
        finally:
            if attached:
                user.AttachThreadInput(own_thread, foreground_thread, False)
        if user.GetForegroundWindow() != hwnd:
            raise RuntimeError('未能激活目标窗口，未执行输入。')
    if window_record(user, hwnd) != expected or user.GetForegroundWindow() != hwnd:
        raise RuntimeError('窗口状态已变化，未执行输入。')
    # Windows requires pointer-sized dwExtraInfo, and the full union even for keyboard input.
    class MouseInput(ctypes.Structure):
        _fields_ = [('dx', wintypes.LONG), ('dy', wintypes.LONG), ('mouseData', wintypes.DWORD),
                    ('dwFlags', wintypes.DWORD), ('time', wintypes.DWORD), ('extra', ctypes.c_size_t)]
    class KeyboardInput(ctypes.Structure):
        _fields_ = [('vk', wintypes.WORD), ('scan', wintypes.WORD), ('flags', wintypes.DWORD),
                    ('time', wintypes.DWORD), ('extra', ctypes.c_size_t)]
    class HardwareInput(ctypes.Structure):
        _fields_ = [('msg', wintypes.DWORD), ('low', wintypes.WORD), ('high', wintypes.WORD)]
    class Union(ctypes.Union):
        _fields_ = [('mouse', MouseInput), ('key', KeyboardInput), ('hardware', HardwareInput)]
    class Input(ctypes.Structure):
        _anonymous_ = ('value',)
        _fields_ = [('type', wintypes.DWORD), ('value', Union)]
    user.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(Input), ctypes.c_int]
    if args['action'] == 'click':
        x, y = args['x'], args['y']
        bounds = expected['bounds']
        if not bounds['left'] <= x < bounds['right'] or not bounds['top'] <= y < bounds['bottom']:
            raise RuntimeError('点击位置不在确认的窗口内。')
        point = wintypes.POINT(x, y)
        if user.GetAncestor(user.WindowFromPoint(point), 2) != hwnd:
            raise RuntimeError('点击位置被其他窗口覆盖。')
        if not user.SetCursorPos(x, y) or user.GetForegroundWindow() != hwnd:
            raise RuntimeError('无法定位鼠标或窗口焦点已变化。')
        items = [Input(type=0, mouse=MouseInput(0, 0, 0, flag, 0, 0)) for flag in (2, 4)]
    elif args['action'] == 'type':
        value = args['text']
        if not value or len(value) > 512 or any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise RuntimeError('文本必须为 1–512 个可见字符，不支持控制键。')
        units = value.encode('utf-16-le')
        items = [Input(type=1, key=KeyboardInput(0, int.from_bytes(units[i:i+2], 'little'), flag, 0, 0))
                 for i in range(0, len(units), 2) for flag in (4, 6)]
    else:
        raise RuntimeError('未知桌面动作。')
    array = (Input * len(items))(*items)
    sent = user.SendInput(len(items), array, ctypes.sizeof(Input))
    if sent != len(items):
        raise RuntimeError(f'系统只接收了 {sent}/{len(items)} 个输入事件，动作可能部分执行。')
    return {'status': 'sent', 'action': args['action'], 'window': expected}


def main():
    try:
        kind = sys.argv[1] if len(sys.argv) == 2 else ''
        result = list_windows() if kind == 'windows' else act(json.load(sys.stdin)) if kind == 'action' else observe(kind)
        print(json.dumps(result, ensure_ascii=False))
    except Exception as error:
        print(json.dumps({'error': str(error)}, ensure_ascii=False))
        raise SystemExit(1)


if __name__ == '__main__':
    main()
