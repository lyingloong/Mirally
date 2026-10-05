"""Separate disposable application for pixel-isolation verification."""

import ctypes
import json
import sys
import tkinter as tk
from ctypes import wintypes


def main():
    root = tk.Tk()
    root.title("OpenAssist generated capture fixture")
    root.configure(bg="#DAE6DF")
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    root.geometry(sys.argv[1])
    root.update()
    user32 = ctypes.WinDLL("user32")
    user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetAncestor.restype = wintypes.HWND
    hwnd = int(user32.GetAncestor(root.winfo_id(), 2))
    print(json.dumps({"hwnd": hwnd}), flush=True)
    # Parent controls only this synthetic window and closes it with WM_CLOSE.
    root.mainloop()


if __name__ == "__main__":
    main()
