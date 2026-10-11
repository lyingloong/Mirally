"""Read Unicode clipboard text in a killable worker, never on the input pump."""

import argparse
import ctypes
import json
import sys
import time
from ctypes import wintypes


def read(expected_sequence: int, max_chars: int, after_sequence: int = 0) -> dict:
    # max_chars is accepted for old callers, but raw clipboard text is complete.
    if sys.platform != "win32":
        return {"status": "unavailable", "missing_reasons": ["not_windows"]}
    user = ctypes.windll.user32
    kernel = ctypes.windll.kernel32
    user.GetClipboardData.argtypes = [wintypes.UINT]
    user.GetClipboardData.restype = wintypes.HANDLE
    user.GetClipboardOwner.restype = wintypes.HWND
    kernel.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel.GlobalLock.restype = ctypes.c_void_p
    kernel.GlobalSize.argtypes = [wintypes.HGLOBAL]
    kernel.GlobalSize.restype = ctypes.c_size_t
    kernel.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    deadline = time.monotonic() + 0.35
    while True:
        sequence = int(user.GetClipboardSequenceNumber())
        if after_sequence and sequence == after_sequence:
            if time.monotonic() < deadline:
                time.sleep(0.02)
                continue
            return {"status": "unchanged", "sequence": sequence}
        if expected_sequence and sequence != expected_sequence:
            return {"status": "stale", "sequence": sequence, "missing_reasons": ["clipboard_replaced"]}
        if user.OpenClipboard(None):
            break
        if time.monotonic() >= deadline:
            return {"status": "busy", "missing_reasons": ["clipboard_busy"]}
        time.sleep(0.02)
    try:
        if not user.IsClipboardFormatAvailable(13):
            return {"status": "non_text", "sequence": sequence}
        handle = user.GetClipboardData(13)
        if not handle:
            return {"status": "unavailable", "missing_reasons": ["clipboard_no_data"]}
        address = kernel.GlobalLock(handle)
        if not address:
            return {"status": "unavailable", "missing_reasons": ["clipboard_lock"]}
        try:
            size = int(kernel.GlobalSize(handle))
            if size <= 0:
                return {"status": "error", "missing_reasons": ["clipboard_invalid_size"]}
            data = ctypes.string_at(address, size)
            end = data.find(b"\x00\x00")
            while end >= 0 and end % 2:
                end = data.find(b"\x00\x00", end + 1)
            if end < 0:
                return {"status": "error", "missing_reasons": ["clipboard_unterminated_text"]}
            try:
                text = data[:end].decode("utf-16-le")
            except UnicodeDecodeError:
                return {"status": "error", "missing_reasons": ["clipboard_invalid_unicode"]}
        finally:
            kernel.GlobalUnlock(handle)
        if int(user.GetClipboardSequenceNumber()) != sequence:
            return {"status": "stale", "missing_reasons": ["clipboard_replaced"]}
        return {"status": "ok", "sequence": sequence, "text": text,
                "truncated": False, "owner_hwnd": int(user.GetClipboardOwner() or 0)}
    finally:
        user.CloseClipboard()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--sequence", type=int, default=0)
    parser.add_argument("--after-sequence", type=int, default=0)
    parser.add_argument("--max-chars", type=int, default=12000)
    args = parser.parse_args()
    try:
        value = read(args.sequence, args.max_chars, args.after_sequence)
    except Exception as error:
        value = {"status": "error", "missing_reasons": [type(error).__name__]}
    print(json.dumps(value, separators=(",", ":")))
