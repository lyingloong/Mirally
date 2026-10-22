"""Product actions in the existing Windows caption-icon menu."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import logging
from collections.abc import Callable, Sequence


WM_SYSCOMMAND = 0x0112
WM_NCDESTROY = 0x0082
COMMAND_BASE = 0xA010


class _MenuItem(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT), ("fMask", wintypes.UINT),
        ("fType", wintypes.UINT), ("fState", wintypes.UINT),
        ("wID", wintypes.UINT), ("hSubMenu", wintypes.HMENU),
        ("hbmpChecked", wintypes.HBITMAP), ("hbmpUnchecked", wintypes.HBITMAP),
        ("dwItemData", ctypes.c_size_t), ("dwTypeData", wintypes.LPWSTR),
        ("cch", wintypes.UINT), ("hbmpItem", wintypes.HBITMAP),
    ]


class WindowSystemMenu:
    """Install/remove on the owning UI thread; dispatch actions off that thread.

    Windows supplies icon-click, caption right-click and Alt+Space handling.
    The existing menu, window commands and HWND are retained. Keep this object
    alive until the window is destroyed so its subclass callback remains valid.
    """

    def __init__(self, hwnd: int, actions: Sequence[tuple[str, Callable] | None],
                 dispatch: Callable[[Callable], None]) -> None:
        if not hwnd or not 1 <= len(actions) <= 32:
            raise ValueError("a native window and 1 to 32 menu entries are required")
        if any(entry is not None and (not entry[0] or not callable(entry[1])) for entry in actions):
            raise ValueError("menu actions need a label and callable")
        self.hwnd = int(hwnd)
        self.actions = tuple(actions)
        self.dispatch = dispatch
        self.installed = False
        self._inserted = []
        self._commands = {COMMAND_BASE + index * 16: entry[1]
                          for index, entry in enumerate(actions) if entry is not None}
        self._subclass_id = id(self)
        self._user = ctypes.WinDLL("user32", use_last_error=True)
        self._common = ctypes.WinDLL("comctl32", use_last_error=True)
        self._user.GetSystemMenu.argtypes = [wintypes.HWND, wintypes.BOOL]
        self._user.GetSystemMenu.restype = wintypes.HMENU
        self._user.InsertMenuItemW.argtypes = [wintypes.HMENU, wintypes.UINT, wintypes.BOOL,
                                              ctypes.POINTER(_MenuItem)]
        self._user.InsertMenuItemW.restype = wintypes.BOOL
        self._user.DeleteMenu.argtypes = [wintypes.HMENU, wintypes.UINT, wintypes.UINT]
        self._user.DeleteMenu.restype = wintypes.BOOL
        self._user.DrawMenuBar.argtypes = [wintypes.HWND]
        self._user.DrawMenuBar.restype = wintypes.BOOL
        callback = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT,
                                     ctypes.c_size_t, ctypes.c_ssize_t, ctypes.c_size_t, ctypes.c_size_t)
        self._proc = callback(self._message)
        self._common.SetWindowSubclass.argtypes = [wintypes.HWND, callback, ctypes.c_size_t, ctypes.c_size_t]
        self._common.SetWindowSubclass.restype = wintypes.BOOL
        self._common.RemoveWindowSubclass.argtypes = [wintypes.HWND, callback, ctypes.c_size_t]
        self._common.RemoveWindowSubclass.restype = wintypes.BOOL
        self._common.DefSubclassProc.argtypes = [wintypes.HWND, wintypes.UINT, ctypes.c_size_t, ctypes.c_ssize_t]
        self._common.DefSubclassProc.restype = ctypes.c_ssize_t
        self._menu = None

    def install(self) -> None:
        if self.installed:
            return
        self._menu = self._user.GetSystemMenu(self.hwnd, False)
        if not self._menu:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            for index, entry in enumerate(self.actions):
                command = COMMAND_BASE + index * 16
                item = _MenuItem(cbSize=ctypes.sizeof(_MenuItem), wID=command,
                                 fMask=0x0002 | 0x0100)  # MIIM_ID | MIIM_FTYPE
                if entry is None:
                    item.fType = 0x0800  # MFT_SEPARATOR; retain our ID for removal.
                else:
                    item.fMask |= 0x0040  # MIIM_STRING
                    item.dwTypeData = entry[0]
                if not self._user.InsertMenuItemW(self._menu, index, True, ctypes.byref(item)):
                    raise ctypes.WinError(ctypes.get_last_error())
                self._inserted.append(command)
            if not self._common.SetWindowSubclass(self.hwnd, self._proc, self._subclass_id, 0):
                raise ctypes.WinError(ctypes.get_last_error())
            self.installed = True
            self._user.DrawMenuBar(self.hwnd)
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        """Remove only this contribution; never reset someone else's menu."""
        if self.installed:
            if not self._common.RemoveWindowSubclass(self.hwnd, self._proc, self._subclass_id):
                raise ctypes.WinError(ctypes.get_last_error())
            self.installed = False
        for command in reversed(self._inserted):
            self._user.DeleteMenu(self._menu, command, 0)  # MF_BYCOMMAND
        self._inserted.clear()
        if self._menu:
            self._user.DrawMenuBar(self.hwnd)

    def _message(self, hwnd, message, wparam, lparam, subclass_id, data):
        try:
            if message == WM_NCDESTROY:
                self._common.RemoveWindowSubclass(hwnd, self._proc, subclass_id)
                self.installed = False
                self._inserted.clear()  # The window owns and destroys the menu.
                self._menu = None
            elif message == WM_SYSCOMMAND and self.installed:
                action = self._commands.get(int(wparam) & 0xFFF0)
                if action is not None:
                    self.dispatch(action)
                    return 0
        except BaseException:
            # No Python exception may escape across the native callback ABI.
            logging.getLogger(__name__).exception("Window menu callback failed")
        return self._common.DefSubclassProc(hwnd, message, wparam, lparam)
