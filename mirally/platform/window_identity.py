"""Identify Mirally's native windows across the Windows automation boundary."""

import ctypes
from ctypes import wintypes


MIRALLY_WINDOW_PROPERTY = 'Mirally.AutomationBoundary'


def mark_mirally_window(hwnd):
    user = ctypes.WinDLL('user32', use_last_error=True)
    user.SetPropW.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.HANDLE]
    user.SetPropW.restype = wintypes.BOOL
    if not user.SetPropW(hwnd, MIRALLY_WINDOW_PROPERTY, 1):
        raise ctypes.WinError(ctypes.get_last_error())


def unmark_mirally_window(hwnd):
    user = ctypes.WinDLL('user32', use_last_error=True)
    user.RemovePropW.argtypes = [wintypes.HWND, wintypes.LPCWSTR]
    user.RemovePropW.restype = wintypes.HANDLE
    user.RemovePropW(hwnd, MIRALLY_WINDOW_PROPERTY)


def is_mirally_window(user, hwnd):
    user.GetPropW.argtypes = [wintypes.HWND, wintypes.LPCWSTR]
    user.GetPropW.restype = wintypes.HANDLE
    if user.GetPropW(hwnd, MIRALLY_WINDOW_PROPERTY):
        return True
    user.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user.GetAncestor.restype = wintypes.HWND
    owner = user.GetAncestor(hwnd, 3)  # GA_ROOTOWNER includes native owned dialogs.
    return bool(owner and owner != hwnd and user.GetPropW(owner, MIRALLY_WINDOW_PROPERTY))
