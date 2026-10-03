"""Scrollable appearance cards and keyboard-accessible preference switches."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from openassist.presentation import THEMES, blend


def rounded(canvas, box, radius, **kwargs):
    x1, y1, x2, y2 = box
    r = radius
    return canvas.create_polygon(
        x1+r, y1, x2-r, y1, x2, y1, x2, y1+r, x2, y2-r,
        x2, y2, x2-r, y2, x1+r, y2, x1, y2, x1, y2-r,
        x1, y1+r, x1, y1, smooth=True, splinesteps=24, **kwargs)


class PreferenceTile(tk.Canvas):
    def __init__(self, parent, view, *, key=None, label="", variable=None, command=None):
        self.view, self.key = view, key
        self.label, self.variable, self.command = label, variable, command
        self.hover = self.pressed = False
        super().__init__(parent, highlightthickness=0, borderwidth=0, takefocus=True,
                         cursor="hand2", height=view.s(90 if key else 52))
        self.bind("<Configure>", self.draw)
        self.bind("<Enter>", lambda _: self.interaction(hover=True))
        self.bind("<Leave>", lambda _: self.interaction(hover=False, pressed=False))
        self.bind("<ButtonPress-1>", self.press)
        self.bind("<ButtonRelease-1>", self.release)
        self.bind("<FocusIn>", self.draw)
        self.bind("<FocusOut>", self.draw)
        self.bind("<space>", self.activate)
        self.bind("<Return>", self.activate)

    def interaction(self, **state):
        for name, value in state.items():
            setattr(self, name, value)
        self.draw()

    def press(self, _event):
        self.focus_set()
        self.interaction(pressed=True)

    def release(self, event):
        activate = self.pressed and 0 <= event.x < self.winfo_width() and 0 <= event.y < self.winfo_height()
        self.interaction(pressed=False)
        if activate:
            self.activate()

    def activate(self, _event=None):
        if self.key:
            self.view.app._theme_name.set(THEMES[self.key].name)
            self.view.app._choose_theme()
        else:
            self.variable.set(not self.variable.get())
            self.command()
            self.draw()
        return "break"

    def draw(self, _event=None):
        app, s = self.view.app, self.view.s
        t, w, h = app.theme, self.winfo_width(), self.winfo_height()
        self.configure(bg=t.background)
        self.delete("all")
        selected = self.key == app.theme_key if self.key else self.variable.get()
        focused = self.focus_get() == self
        fill = blend(t.surface, t.accent, .13 if self.pressed else .06 if self.hover else 0)
        border = t.accent if focused or (self.key and selected) else t.border
        rounded(self, (s(2), s(2), w-s(2), h-s(2)), s(12), fill=fill,
                outline=border, width=s(2) if focused or (self.key and selected) else 1)
        if self.key:
            palette = THEMES[self.key]
            rounded(self, (s(12), s(12), w-s(12), s(48)), s(7), fill=palette.background, outline=palette.border)
            self.create_line(s(24), s(24), w-s(50), s(24), fill=palette.accent, width=s(4), capstyle="round")
            self.create_line(s(24), s(36), w-s(65), s(36), fill=palette.muted, width=s(2), capstyle="round")
            self.create_oval(w-s(35), s(22), w-s(21), s(36), fill=palette.signal or palette.accent, outline="")
            self.create_text(s(14), s(67), anchor="w", text=palette.name,
                             fill=t.text, font=(app.body_font, 10))
            if selected:
                self.create_text(w-s(20), s(67), text="✓", fill=t.accent, font=(app.body_font, 11, "bold"))
        else:
            self.create_text(s(16), h/2, anchor="w", text=self.label, fill=t.text, font=(app.body_font, 10))
            x1, x2, y = w-s(48), w-s(28), h/2
            self.create_line(x1, y, x2, y, width=s(22), capstyle="round",
                             fill=t.accent if selected else blend(t.border, t.muted, .25))
            x = x2 if selected else x1
            self.create_oval(x-s(8), y-s(8), x+s(8), y+s(8), fill=t.surface, outline="")


class SettingsView(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, borderwidth=0)
        self.app = app
        self._columns = None
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.viewport = tk.Canvas(self, highlightthickness=0, borderwidth=0)
        self.viewport.grid(row=0, column=0, sticky="nsew")
        self.scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.viewport.yview,
                                      style="Mirally.Settings.Vertical.TScrollbar")
        self.viewport.configure(yscrollcommand=self.scrollbar.set)
        self.body = tk.Frame(self.viewport, borderwidth=0)
        self._window = self.viewport.create_window(0, 0, window=self.body, anchor="nw")
        self.labels = []
        self.label("按你的方式", 22, bold=True, pady=(12, 4))
        self.label("让工作空间，也有你的色彩。", 10, muted=True, pady=(0, 16))
        self.label("外观主题", 10, bold=True, pady=(0, 6))
        self.palettes = tk.Frame(self.body, borderwidth=0)
        self.palettes.pack(fill="x")
        self.cards = [PreferenceTile(self.palettes, self, key=key) for key in THEMES]
        self.switches = [
            PreferenceTile(self.body, self, label="减少动态效果", variable=app._reduced, command=app._motion),
            PreferenceTile(self.body, self, label="窗口保持置顶", variable=app._topmost, command=app._pin),
        ]
        for index, switch in enumerate(self.switches):
            switch.pack(fill="x", pady=(12 if index == 0 else 4, 0))
        self.note = self.label("执行时浮在屏幕侧边，靠近即可补充。\n发送后会按新信息安排下一步。", 9,
                               muted=True, pady=(14, 16))
        self.viewport.bind("<Configure>", self.resize)
        self.body.bind("<Configure>", self._scroll_region)
        self._bind_scroll(self)
        self.apply_theme()

    def s(self, value):
        return round(value * self.app.scale)

    def label(self, text, size, *, bold=False, muted=False, pady=0):
        label = tk.Label(self.body, text=text, anchor="w", justify="left", borderwidth=0,
                         font=(self.app.body_font, size, "bold" if bold else "normal"))
        label.pack(fill="x", padx=self.s(3), pady=tuple(self.s(v) for v in pady) if isinstance(pady, tuple) else self.s(pady))
        self.labels.append((label, muted))
        return label

    def _bind_scroll(self, widget):
        widget.bind("<MouseWheel>", self.scroll, add="+")
        widget.bind("<FocusIn>", self.reveal_focus, add="+")
        for child in widget.winfo_children():
            self._bind_scroll(child)

    def scroll(self, event):
        if self.body.winfo_height() > self.viewport.winfo_height():
            self.viewport.yview_scroll(-int(event.delta/120), "units")
        return "break"

    def reveal_focus(self, event):
        widget = event.widget if event is not None else self.focus_get()
        if not isinstance(widget, PreferenceTile) or widget.view is not self:
            return
        top = widget.winfo_rooty() - self.body.winfo_rooty()
        bottom = top + widget.winfo_height()
        y, height = self.viewport.canvasy(0), self.viewport.winfo_height()
        total = max(1, self.body.winfo_height())
        if top < y:
            self.viewport.yview_moveto(top/total)
        elif bottom > y + height:
            self.viewport.yview_moveto((bottom-height)/total)

    def _scroll_region(self, _event=None):
        self.viewport.configure(scrollregion=self.viewport.bbox("all"))
        overflow = self.body.winfo_height() > self.viewport.winfo_height()
        if overflow:
            self.scrollbar.grid(row=0, column=1, sticky="ns", padx=(self.s(6), 0))
        else:
            self.scrollbar.grid_remove()
            self.viewport.yview_moveto(0)

    def resize(self, event):
        self.viewport.itemconfigure(self._window, width=event.width)
        columns = 4 if event.width >= self.s(720) else 2
        if columns != self._columns:
            self._columns = columns
            for col in range(4):
                self.palettes.columnconfigure(col, weight=1 if col < columns else 0, uniform="cards" if col < columns else "")
            for index, card in enumerate(self.cards):
                card.grid(row=index//columns, column=index%columns, sticky="ew", padx=self.s(2), pady=self.s(3))
        self.note.configure(wraplength=max(100, event.width-self.s(12)))
        self._scroll_region()
        self.after_idle(lambda: self.reveal_focus(None))

    def apply_theme(self):
        t = self.app.theme
        style = ttk.Style(self)
        name = "Mirally.Settings.Vertical.TScrollbar"
        style.layout(name, [("Vertical.Scrollbar.trough", {"sticky": "ns", "children": [
            ("Vertical.Scrollbar.thumb", {"sticky": "nsew", "expand": True})]})])
        style.configure(name, background=t.border, troughcolor=t.background, borderwidth=0,
                        bordercolor=t.background, lightcolor=t.border, darkcolor=t.border,
                        width=self.s(6), arrowsize=self.s(6))
        style.map(name, background=[("active", t.muted), ("pressed", t.accent)])
        for widget in (self, self.viewport, self.body, self.palettes):
            widget.configure(bg=t.background)
        for label, muted in self.labels:
            label.configure(bg=t.background, fg=t.muted if muted else t.text)
        for tile in self.cards + self.switches:
            tile.draw()
