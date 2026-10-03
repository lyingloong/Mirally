"""Mirally's selected mark and native application identity.

Assets are resolved beside this module so launchers may change working directory.
"""

from __future__ import annotations

import sys
import tkinter as tk
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageTk


APP_NAME = "Mirally"
APP_ID = "MIRALab.Mirally.Desktop"
ASSET_ROOT = Path(__file__).resolve().parents[1] / "assets"
LOGO_PATH = ASSET_ROOT / "mirally.png"
ICO_PATH = ASSET_ROOT / "mirally.ico"


def prepare_process_identity() -> None:
    """Group native taskbar windows under Mirally; call before creating Tk."""
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
        except (AttributeError, OSError):
            pass


@lru_cache(maxsize=24)
def _sized_mark(size: int) -> Image.Image:
    with Image.open(LOGO_PATH) as image:
        return image.convert("RGBA").resize((size, size), Image.Resampling.LANCZOS)


def brand_photo(master: tk.Misc, size: int, *, background: str | None = None) -> ImageTk.PhotoImage:
    """Return the selected logo at physical-pixel size; caller retains the image.

    Transparent PNGs work directly on Canvas/Label backgrounds. ``background``
    optionally composites onto a known widget color, avoiding Tk alpha seams.
    Resizing and background compositing happen only at theme/layout changes.
    """
    logo = _sized_mark(max(1, round(size)))
    if background is not None:
        base = Image.new("RGBA", logo.size, background)
        base.alpha_composite(logo)
        logo = base
    return ImageTk.PhotoImage(logo, master=master)


def apply_window_icon(root: tk.Misc) -> None:
    """Install the multi-resolution native icon, with a portable Tk fallback."""
    try:
        if sys.platform == "win32":
            root.iconbitmap(default=str(ICO_PATH))
            return
    except tk.TclError:
        pass
    try:
        photos = [brand_photo(root, size) for size in (16, 32, 48, 128, 256)]
        root.iconphoto(True, *photos)
        root._mirally_icon_photos = photos
    except (OSError, tk.TclError):
        # A missing packaging resource must not prevent the assistant opening.
        pass
