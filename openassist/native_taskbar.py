"""Register a recreated Tk wrapper with the Windows shell without hiding it."""
import ctypes
import uuid
from ctypes import wintypes


def add_taskbar_tab(hwnd):
    ole = ctypes.WinDLL("ole32")
    ole.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    ole.CoInitializeEx.restype = ctypes.c_long
    ole.CoCreateInstance.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD,
                                     ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    ole.CoCreateInstance.restype = ctypes.c_long
    initialized = ole.CoInitializeEx(None, 2)
    # A pre-existing MTA is also usable. Balance only our successful init call.
    if initialized not in (0, 1, -2147417850):  # RPC_E_CHANGED_MODE
        raise OSError(f"COM initialization failed: {initialized:#x}")
    interface = ctypes.c_void_p()
    try:
        clsid = ctypes.create_string_buffer(uuid.UUID("56FDF344-FD6D-11D0-958A-006097C9A090").bytes_le)
        iid = ctypes.create_string_buffer(uuid.UUID("56FDF342-FD6D-11D0-958A-006097C9A090").bytes_le)
        result = ole.CoCreateInstance(clsid, None, 1, iid, ctypes.byref(interface))
        if result < 0:
            raise OSError(f"Taskbar service unavailable: {result:#x}")
        vtable = ctypes.cast(interface, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        initialize = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p)(vtable[3])
        add_tab = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, wintypes.HWND)(vtable[4])
        result = initialize(interface)
        if result >= 0:
            result = add_tab(interface, hwnd)
        if result < 0:
            raise OSError(f"Taskbar registration failed: {result:#x}")
    finally:
        if interface:
            vtable = ctypes.cast(interface, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
            ctypes.WINFUNCTYPE(wintypes.ULONG, ctypes.c_void_p)(vtable[2])(interface)
        if initialized in (0, 1):
            ole.CoUninitialize()
