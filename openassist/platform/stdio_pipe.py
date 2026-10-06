"""Read a Windows worker pipe without leaving a synchronous read pending."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import io
import msvcrt
import time


class _PipeReader(io.RawIOBase):
    def __init__(self, descriptor):
        super().__init__()
        # Borrow the inherited handle; the caller retains ownership of stdin.
        self._handle = msvcrt.get_osfhandle(descriptor)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self._read = kernel.ReadFile
        self._read.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                               ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
        self._read.restype = wintypes.BOOL
        self._peek = kernel.PeekNamedPipe
        self._peek.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                               ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
        self._peek.restype = wintypes.BOOL

    def readable(self):
        return True

    def readinto(self, buffer):
        if self.closed:
            raise ValueError("I/O operation on closed pipe")
        if not buffer:
            return 0
        count = wintypes.DWORD()
        available = wintypes.DWORD()
        while not available.value:
            if not self._peek(self._handle, None, 0, None, ctypes.byref(available), None):
                error = ctypes.get_last_error()
                if error in {38, 109, 232}:
                    return 0
                raise ctypes.WinError(error)
            if not available.value:
                time.sleep(0.02)
        storage = (ctypes.c_char * len(buffer)).from_buffer(buffer)
        if not self._read(self._handle, storage, min(len(buffer), available.value), ctypes.byref(count), None):
            error = ctypes.get_last_error()
            if error in {38, 109, 232}:  # EOF / broken pipe / closing pipe.
                return 0
            raise ctypes.WinError(error)
        return count.value


def control_pipe_reader(stream):
    """Private UTF-8 framing stream; closing it leaves inherited stdin intact.

    A pending synchronous stdin read can prevent native extension DLL loading
    on Windows. PeekNamedPipe waits for available bytes before ReadFile, so no
    pending read on the inherited handle blocks dependency initialization.
    """
    return io.TextIOWrapper(io.BufferedReader(_PipeReader(stream.fileno())), encoding="utf-8")
