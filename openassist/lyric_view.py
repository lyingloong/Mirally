"""A resizable lyric surface with bounded, coalesced, interruptible transitions."""

from __future__ import annotations

import time
import tkinter as tk
from tkinter import font

from openassist.presentation import blend, choose_ui_font


class LyricView(tk.Canvas):
    def __init__(self, parent, theme, **kwargs):
        super().__init__(parent, highlightthickness=0, borderwidth=0, bg=theme.background, **kwargs)
        self.theme = theme
        self.body_font = choose_ui_font(self)
        self.scale = max(1.0, self.winfo_fpixels("1i") / 96)
        self.reduced_motion = False
        self.history = []
        self.title = "让想法，\n成为下一步。"
        self.detail = "告诉我你想完成什么"
        self.phase = "idle"
        self._last_change = 0.0
        self._animation = None
        self._pending = None
        self._timer = None
        self._progress = 1.0
        self._fonts = {}
        self.bind("<Configure>", lambda _: self.draw())

    def set_theme(self, theme):
        self.theme = theme
        self.configure(bg=theme.background)
        self.draw()

    def reset(self):
        self.history.clear()
        self.title = ""
        self._last_change = 0
        self._pending = None

    def show(self, phase, title, detail="", *, urgent=False):
        if phase == self.phase and title == self.title:
            self._pending = None
            if self._timer:
                self.after_cancel(self._timer)
                self._timer = None
            self.detail = detail
            self.draw()
            return
        remaining = 0.75 - (time.monotonic() - self._last_change)
        if not urgent and remaining > 0:
            self._pending = (phase, title, detail)
            if self._timer is None:
                self._timer = self.after(max(1, int(remaining * 1000)), self._flush)
            return
        self._pending = None
        if self._timer:
            self.after_cancel(self._timer)
            self._timer = None
        if self._animation:
            self.after_cancel(self._animation)
            self._animation = None
        if self.title and phase not in {"idle", "preparing"} and self.phase not in {"idle", "preparing", "paused", "takeover", "waiting_confirmation", "waiting_user"}:
            old = self.title.replace("\n", "")
            if not self.history or self.history[-1] != old:
                self.history = (self.history + [old])[-2:]
        self.phase, self.title, self.detail = phase, title, detail
        self._last_change = time.monotonic()
        self._progress = 1.0 if self.reduced_motion else 0.0
        self._tick()

    def _flush(self):
        self._timer = None
        if self._pending:
            phase, title, detail = self._pending
            self._pending = None
            self.show(phase, title, detail, urgent=True)

    def _tick(self):
        self._animation = None
        self._progress = 1.0 if self.reduced_motion else min(1, (time.monotonic() - self._last_change) / 0.28)
        self.draw()
        if self._progress < 1:
            self._animation = self.after(16, self._tick)

    def draw(self):
        self.delete("all")
        t = self.theme
        width, height = max(self.winfo_width(), 200), max(self.winfo_height(), 120)
        inset = 12 * self.scale
        wide = width >= 740 * self.scale
        column_width = min(width, 940 * self.scale)
        gutter = max(0, (width - column_width) / 2)
        x = gutter + (36 if self.phase != "idle" else 12) * self.scale
        available = column_width - (x - gutter) - inset
        compact = height < 230 * self.scale
        size = 24 if compact else (42 if wide else (30 if width > 420 * self.scale else 26))
        very_compact = height < 170 * self.scale
        title = self.title.replace("\n", "") if very_compact else self.title
        if very_compact:
            size = 18
        top = max(14 * self.scale, (height - (190 if compact else (310 if wide else 250)) * self.scale) / 2)
        y = top
        if self.phase not in {"idle", "waiting_confirmation", "waiting_user", "paused", "takeover", "failed"} and not compact:
            for line in self.history:
                # History means previous status, never a fabricated completed step.
                history_size = 13 if wide else 11
                self.create_text(x, y, text=self._fit(line, available, history_size), anchor="nw", fill=t.muted, font=(self.body_font, history_size))
                y += (39 if wide else 34) * self.scale
            y = max(y + 20 * self.scale, top + 82 * self.scale)
        ease = 1 - (1 - self._progress) ** 3
        offset = 14 * self.scale * (1 - ease)
        color = blend(t.muted, t.text, ease)
        item = self.create_text(x, y + offset, text=title, width=available,
                                anchor="nw", fill=color, font=(self.body_font, size, "bold"))
        bounds = self.bbox(item)
        bottom = bounds[3] if bounds else y + 80
        if self.phase not in {"idle", "completed", "cancelled", "failed"}:
            marker = x - 24 * self.scale
            self.create_line(marker, y + 6 + offset, marker, bottom - 6, fill=t.signal or t.accent, width=max(3, round(2 * self.scale)))
            self.create_oval(marker - 4 * self.scale, y - 12 + offset, marker + 4 * self.scale, y - 4 + offset, fill=t.signal or t.accent, outline="")
        if bottom + 36 * self.scale < height:
            self.create_text(x, bottom + (12 if compact else 20) * self.scale, text=self.detail, width=available,
                             anchor="nw", fill=t.muted, font=(self.body_font, 12 if wide else 10))

    def _fit(self, text, width, size):
        if size not in self._fonts:
            self._fonts[size] = font.Font(self, family=self.body_font, size=size)
        face = self._fonts[size]
        while len(text) > 1 and face.measure(text) > width:
            text = text[:-2] + "…"
        return text
