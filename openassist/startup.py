"""A short, interruptible brand reveal inside the main window.

No extra top-level window, focus changes, grabs, native hooks or blocking waits.
The selected mark is loaded once; animation only moves existing canvas items.
"""

from __future__ import annotations

import math
import time
import tkinter as tk
from collections.abc import Callable

from openassist.branding import APP_NAME, brand_photo
from openassist.presentation import Theme, blend


def _ease(value: float) -> float:
    value = min(1.0, max(0.0, value))
    return 1.0 - (1.0 - value) ** 3


class StartupReveal:
    """Call ``start`` after construction; ``finish`` skips, ``cancel`` cleans up.

    ``on_complete`` runs once after completion or skip, but not on cancellation
    or parent destruction. Reduced motion bypasses the reveal immediately.
    ``active`` is exposed for action handlers and deterministic GUI previews.
    """

    duration = 2.8

    def __init__(
        self,
        parent: tk.Misc,
        theme: Theme,
        *,
        scale: float = 1.0,
        body_font: str = "Segoe UI",
        reduced_motion: bool = False,
        on_complete: Callable[[], None] | None = None,
    ):
        self.parent = parent
        self.theme = theme
        self.scale = scale
        self.reduced_motion = reduced_motion
        self.on_complete = on_complete
        self.active = False
        self._started = False
        self._closed = False
        self._after_id = None
        self._bindings = []
        self._begin = 0.0
        self._frame = tk.Frame(parent, bg=theme.background, borderwidth=0, takefocus=False)
        self._canvas = tk.Canvas(self._frame, bg=theme.background, borderwidth=0,
                                 highlightthickness=0, takefocus=False)
        # Persistent layers: soft light, orbital ribbons, then the selected mark.
        # No per-frame bitmap allocation or additional native windows.
        self._halo = [self._canvas.create_oval(0, 0, 0, 0, outline="", fill=theme.background) for _ in range(24)]
        self._ribbons = [self._canvas.create_line(0, 0, 0, 0, smooth=True,
                          width=max(1, scale * width), capstyle="round", fill=theme.background)
                         for width in (1, 2, 1)]
        self._sparks = [self._canvas.create_oval(0, 0, 0, 0, outline="", fill=theme.background) for _ in range(12)]
        self._image = brand_photo(self._canvas, round(154 * scale))
        self._logo = self._canvas.create_image(0, 0, image=self._image)
        self._wordmark = self._canvas.create_text(0, 0, text=APP_NAME, fill=theme.background,
                                                  font=("Segoe UI", 29, "bold"))
        self._tagline = self._canvas.create_text(0, 0, text="与你一起，把事情做好。", fill=theme.background,
                                                 font=(body_font, 11))
        self._line = self._canvas.create_line(0, 0, 0, 0, fill=theme.background,
                                              width=max(2, round(2 * scale)), capstyle="round")
        self._signature = self._canvas.create_text(0, 0, text="M I R A   /   A L L Y", fill=theme.background,
                                                    font=("Segoe UI", 8))
        self._skip_hint = self._canvas.create_text(0, 0, text="点击或按任意键，开始", fill=theme.background,
                                                    font=(body_font, 9))

    def start(self) -> "StartupReveal":
        if self._started or self._closed:
            return self
        self._started = True
        self.active = True
        if self.reduced_motion:
            self.finish()
            return self
        self._begin = time.perf_counter()
        self._frame.place(x=0, y=0, relwidth=1, relheight=1)
        self._frame.lift()
        for sequence, callback in (
            ("<Configure>", self._configured),
            ("<Destroy>", self._destroyed),
            ("<ButtonPress>", self._skip),
            ("<KeyPress>", self._skip),
        ):
            self._bindings.append((sequence, self.parent.bind(sequence, callback, add="+")))
        self._tick()
        return self

    def _configured(self, event) -> None:
        if event.widget is self.parent and self.active:
            self._render(time.perf_counter() - self._begin)

    def _destroyed(self, event) -> None:
        if event.widget is self.parent:
            self.cancel()

    def _skip(self, _event=None) -> None:
        self.finish()

    def _tick(self) -> None:
        self._after_id = None
        if not self.active:
            return
        elapsed = time.perf_counter() - self._begin
        if elapsed >= self.duration:
            self.finish()
            return
        try:
            self._render(elapsed)
            # Schedule relative to elapsed wall time, never count Tk frames.
            self._after_id = self.parent.after(16, self._tick)
        except tk.TclError:
            self.cancel()

    def _render(self, elapsed: float) -> None:
        width = max(1, self.parent.winfo_width())
        height = max(1, self.parent.winfo_height())
        departure = _ease((elapsed - 2.2) / (self.duration - 2.2))
        self._frame.place_configure(relheight=0, height=max(1, round(height * (1 - departure))))
        self._canvas.place(x=0, y=0, width=width, height=height)
        x = width / 2
        y = height / 2 - 66 * self.scale
        drift = 26 * self.scale * (1 - _ease(elapsed / 0.85))
        light = _ease(elapsed / 0.65) * (1 - 0.65 * _ease((elapsed - 1.5) / 0.7))
        accent = self.theme.signal or self.theme.accent
        for index, item in enumerate(self._halo):
            radius = (178 - index * 4.8) * self.scale * (0.8 + 0.2 * _ease(elapsed / 1.0))
            self._canvas.coords(item, x-radius, y-radius*.72, x+radius, y+radius*.72)
            self._canvas.itemconfigure(item, fill=blend(self.theme.background, accent,
                                                        light * (0.008 + index * 0.0028)))
        orbit = elapsed * 1.5
        for index, item in enumerate(self._ribbons):
            points = []
            radius = (119 + index * 19) * self.scale
            tilt = (-0.40 + index * 0.36)
            for step in range(35):
                angle = orbit + index * 2.1 + step / 34 * math.pi * 1.20 * _ease(elapsed / .6)
                px, py = math.cos(angle)*radius, math.sin(angle)*radius*.62
                points.extend((x+px*math.cos(tilt)-py*math.sin(tilt),
                               y+px*math.sin(tilt)+py*math.cos(tilt)))
            self._canvas.coords(item, *points)
            self._canvas.itemconfigure(item, fill=blend(self.theme.background, accent, light*(.20 + index*.07)))
        for index, item in enumerate(self._sparks):
            angle = index * math.tau / 12 + elapsed * (.12 if index % 2 else -.10)
            radius = (152 + 23 * math.sin(index*2.4)) * self.scale
            px, py = x+math.cos(angle)*radius, y+math.sin(angle)*radius*.78
            size = (1.2 + .8 * (index % 3 == 0)) * self.scale
            self._canvas.coords(item, px-size, py-size, px+size, py+size)
            strength = light * (.20 + .25 * (.5 + .5*math.sin(elapsed*2+index)))
            self._canvas.itemconfigure(item, fill=blend(self.theme.background, accent, strength))
        self._canvas.coords(self._logo, x, y + drift)
        self._canvas.coords(self._wordmark, x, y + 142 * self.scale + drift * 0.45)
        self._canvas.coords(self._tagline, x, y + 186 * self.scale + drift * 0.25)
        self._canvas.itemconfigure(self._wordmark,
                                   fill=blend(self.theme.background, self.theme.text, _ease((elapsed - 0.38) / 0.60)))
        self._canvas.itemconfigure(self._tagline,
                                   fill=blend(self.theme.background, self.theme.muted, _ease((elapsed - 0.8) / 0.65)))
        half = 34 * self.scale * _ease((elapsed - 0.7) / 0.65)
        self._canvas.coords(self._line, x - half, y + 220 * self.scale, x + half, y + 220 * self.scale)
        self._canvas.itemconfigure(self._line,
                                   fill=blend(self.theme.background, self.theme.signal or self.theme.accent,
                                              _ease((elapsed - 0.7) / 0.65) * .65))
        self._canvas.coords(self._signature, x, y - 167 * self.scale)
        self._canvas.itemconfigure(self._signature, fill=blend(self.theme.background, self.theme.muted,
                                                               _ease((elapsed - .2) / .8) * .65))
        self._canvas.coords(self._skip_hint, x, height - 36 * self.scale)
        self._canvas.itemconfigure(self._skip_hint, fill=blend(self.theme.background, self.theme.muted,
                                                               _ease((elapsed - 1) / .6) * .7))

    def finish(self) -> None:
        if self._closed:
            return
        callback = self.on_complete
        self.cancel()
        if callback is not None:
            callback()

    def cancel(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.active = False
        self.on_complete = None
        if self._after_id is not None:
            try:
                self.parent.after_cancel(self._after_id)
            except tk.TclError:
                pass
            self._after_id = None
        for sequence, identifier in self._bindings:
            try:
                self.parent.unbind(sequence, identifier)
            except tk.TclError:
                pass
        self._bindings.clear()
        try:
            self._frame.destroy()
        except tk.TclError:
            pass
