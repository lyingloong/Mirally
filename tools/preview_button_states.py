"""Capture native ttk button states at a chosen DPI without moving the pointer."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
import tkinter as tk
from tkinter import ttk

from PIL import ImageGrab

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from openassist.presentation import THEMES
from openassist.widgets import ButtonStyles
from openassist.window_session import enable_dpi_awareness


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scale", type=float, default=1.5)
    parser.add_argument("--theme", choices=tuple(THEMES), default="sky")
    parser.add_argument("--surface", action="store_true", help="Preview buttons on card/sidebar surfaces")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    enable_dpi_awareness()
    root = tk.Tk()
    root.title("Mirally - button states")
    root.tk.call("tk", "scaling", args.scale * 96 / 72)
    theme = THEMES[args.theme]
    background = theme.surface if args.surface else theme.background
    style_prefix = args.theme + (".surface" if args.surface else "")
    root.configure(bg=background)
    styles = ButtonStyles(root, args.scale)
    styles.apply(args.theme, theme)
    states = (
        ("Rest", ()), ("Hover", ("active",)), ("Pressed", ("pressed",)),
        ("Selected", ("selected",)), ("Selected + hover", ("selected", "active")),
        ("Selected + pressed", ("selected", "pressed")), ("Focus", ("focus",)),
        ("Focus + hover", ("focus", "active")), ("Focus + pressed", ("focus", "pressed")),
        ("Selected + focus", ("selected", "focus")), ("Disabled", ("disabled",)),
        ("Selected + disabled", ("selected", "disabled")),
    )
    tk.Label(root, text=f"{style_prefix} / {round(args.scale * 100)}%", bg=background,
             fg=theme.text, font=(styles.body_font, 13, "bold")).grid(row=0, column=0, padx=12, pady=12)
    for column, role in enumerate(("quiet", "outline", "primary"), 1):
        tk.Label(root, text=role, bg=background, fg=theme.muted,
                 font=(styles.body_font, 10)).grid(row=0, column=column, padx=12, pady=8)
    buttons = []
    for row, (label, state) in enumerate(states, 1):
        tk.Label(root, text=label, bg=background, fg=theme.muted,
                 font=(styles.body_font, 9)).grid(row=row, column=0, padx=12, pady=2, sticky="w")
        for column, role in enumerate(("quiet", "outline", "primary"), 1):
            button = ttk.Button(root, text={"quiet": "设置 / 记忆", "outline": "新建会话", "primary": "继续工作"}[role],
                                style=f"{style_prefix}.{role}.TButton", takefocus=True)
            button.grid(row=row, column=column, sticky="ew", padx=8, pady=2)
            buttons.append((button, state))
    root.update()
    # Position away from the taskbar, with a neutral focus target. State changes
    # below are local to these demo widgets and never inject real input.
    root.geometry("+24+24")
    root.focus_force()

    def capture():
        for button, state in buttons:
            button.state(("!active", "!pressed", "!selected", "!disabled", "!focus"))
            button.state(state)
        root.update_idletasks()
        args.out.parent.mkdir(parents=True, exist_ok=True)
        box = (root.winfo_rootx(), root.winfo_rooty(),
               root.winfo_rootx() + root.winfo_width(), root.winfo_rooty() + root.winfo_height())
        ImageGrab.grab(bbox=box).save(args.out)
        print(f"Saved {args.out} ({root.winfo_width()} x {root.winfo_height()})")
        root.destroy()

    root.after(500, capture)
    root.mainloop()


if __name__ == "__main__":
    main()
