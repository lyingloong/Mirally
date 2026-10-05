"""One Windows desktop executor across sessions, workers and product instances."""
from contextlib import contextmanager
import ctypes
from ctypes import wintypes


@contextmanager
def desktop_execution_lock():
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.CreateMutexW.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.ReleaseMutex.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.CreateMutexW(None, False, "Local\\MirallyUFODesktopExecutor")
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    owned = False
    try:
        outcome = kernel.WaitForSingleObject(handle, 0)
        if outcome not in (0, 0x80):  # acquired or abandoned; a new session reobserves
            raise RuntimeError("另一个 UFO 任务仍在使用桌面或完成清理。")
        owned = True
        yield
    finally:
        if owned:
            kernel.ReleaseMutex(handle)
        kernel.CloseHandle(handle)
