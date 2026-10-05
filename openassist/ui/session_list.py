"""A scrollable, keyboard-accessible conversation list shared by both layouts."""
import tkinter as tk
from tkinter import font, ttk


class SessionList(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, borderwidth=0)
        self.app = app
        self.rows = []
        self.current = None
        self.enabled = True
        self.hover = None
        self.row_height = round(68 * app.scale)
        self.title_font = font.Font(self, family=app.body_font, size=10)
        self.canvas = tk.Canvas(self, highlightthickness=0, width=1, height=1,
                                takefocus=True, yscrollincrement=self.row_height)
        self.scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda _: self.draw())
        self.canvas.bind("<Motion>", self._motion)
        self.canvas.bind("<Leave>", lambda _: self._hover(None))
        self.canvas.bind("<Button-1>", self._click)
        self.canvas.bind("<MouseWheel>", self._wheel)
        self.canvas.bind("<Up>", lambda _: self._key(-1))
        self.canvas.bind("<Down>", lambda _: self._key(1))
        self.canvas.bind("<Return>", self._activate)
        self.canvas.bind("<space>", self._activate)
        self.canvas.bind("<FocusIn>", lambda _: self.draw())
        self.canvas.bind("<FocusOut>", lambda _: self.draw())

    def set_sessions(self, rows, current):
        changed = self.rows != rows or self.current != current
        self.rows, self.current = list(rows), current
        if changed:
            self.hover = None
            self.draw()
            index = next((i for i, row in enumerate(rows) if row[0] == current), 0)
            top = self.canvas.canvasy(0)
            if not top <= index * self.row_height < top + self.canvas.winfo_height():
                self.canvas.yview_moveto(index / max(1, len(rows)))

    def set_enabled(self, enabled):
        if enabled != self.enabled:
            self.enabled = enabled
            self.draw()

    def _index(self, event):
        index = int(self.canvas.canvasy(event.y) // self.row_height)
        return index if 0 <= index < len(self.rows) else None

    def _hover(self, index):
        if index != self.hover:
            self.hover = index
            self.draw()

    def _motion(self, event):
        self._hover(self._index(event))

    def _click(self, event):
        self.canvas.focus_set()
        index = self._index(event)
        if self.enabled and index is not None:
            self.app._select_conversation(self.rows[index][0])

    def _wheel(self, event):
        self.canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")
        return "break"

    def _key(self, direction):
        index = self.hover
        if index is None:
            index = next((i for i, row in enumerate(self.rows) if row[0] == self.current), 0)
        index = max(0, min(len(self.rows) - 1, index + direction))
        self._hover(index)
        top = self.canvas.canvasy(0)
        if not top <= index * self.row_height < top + self.canvas.winfo_height() - self.row_height:
            self.canvas.yview_moveto(index / max(1, len(self.rows)))
        return "break"

    def _activate(self, _event):
        if self.enabled and self.hover is not None and self.rows:
            self.app._select_conversation(self.rows[self.hover][0])
        return "break"

    def draw(self):
        t, c, s = self.app.theme, self.canvas, self.app.scale
        self.configure(bg=t.surface)
        c.configure(bg=t.surface, cursor="hand2" if self.enabled else "arrow")
        content_height = len(self.rows) * self.row_height
        if content_height > c.winfo_height():
            self.scrollbar.pack(side="right", fill="y")
        else:
            self.scrollbar.pack_forget()
        c.delete("all")
        width = max(20, c.winfo_width())
        c.configure(scrollregion=(0, 0, width, content_height))
        for index, (identifier, title) in enumerate(self.rows):
            y = index * self.row_height
            selected = identifier == self.current
            hovered = index == self.hover and self.enabled
            if selected or hovered:
                radius = min(round(11 * s), width // 4)
                left, right, top, bottom = 2, width - 3, y + 2, y + self.row_height - 6
                c.create_polygon(left+radius, top, right-radius, top, right, top,
                                 right, top+radius, right, bottom-radius, right, bottom,
                                 right-radius, bottom, left+radius, bottom, left, bottom,
                                 left, bottom-radius, left, top+radius, left, top,
                                 smooth=True, splinesteps=16, fill=t.tint if selected else t.background,
                                 outline=t.accent if hovered and c.focus_get() == c else "")
            if selected:
                c.create_line(7*s, y+20*s, 7*s, y+40*s, fill=t.accent, width=max(2, round(3*s)), capstyle="round")
            available = width - round(38*s)
            display = " ".join(title.split()) or "新的会话"
            while len(display) > 1 and self.title_font.measure(display) > available:
                display = display[:-2] + "…"
            c.create_text(19*s, y+23*s, anchor="w", text=display, font=self.title_font,
                          fill=t.muted if not self.enabled else t.text)
            c.create_text(19*s, y+45*s, anchor="w", text="当前会话" if selected else "继续这段对话",
                          font=(self.app.body_font, 8), fill=t.accent if selected else t.muted)
