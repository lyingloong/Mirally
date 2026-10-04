"""A compact edge companion with an anchored, reversible expansion."""
from __future__ import annotations

import math
import time
import tkinter as tk
from tkinter import ttk, font

from openassist.ui.branding import APP_NAME, brand_photo
from openassist.ui.presentation import PHASES, public_text
from openassist.core.execution import TERMINAL_STATUSES


class EdgeCompanion:
    def __init__(self, app):
        self.app, self.root = app, app.root
        self.mode = "workspace"
        self._animation = None
        self._target = None
        self._surface_bounds = None
        self._visible_rect = None
        self._workspace = None
        self._workspace_state = "normal"
        self._workspace_fullscreen = False
        self._workspace_minsize = None
        self._interaction_return = False
        self._activity_timer = None
        self._activity_bars = []
        self._activity_frame = 0
        self._hover_blocked = False
        self._keep_for_keyboard = False
        self._compose_since = None
        self._status = ("idle", "随时为你待命", 0)
        self._receipt = "靠近即可补充，随时调整方向"
        self._ready = False
        self._draw_key = None
        self._canvas_items = None
        self._started = time.monotonic()
        self._title_font = font.Font(self.root, family=app.body_font, size=14, weight="bold")
        self.frame = tk.Frame(self.root, borderwidth=0)
        self.canvas = tk.Canvas(self.frame, highlightthickness=0, height=self.s(114))
        self.canvas.pack(fill="x")
        self.canvas.bind("<Configure>", lambda _: self.draw())
        self.canvas.bind("<Button-1>", self._header_click)
        self.minimize_button = ttk.Button(self.canvas, text="−", width=1, padding=0, command=self.minimize,
                                           cursor="hand2", takefocus=True)
        self.panel = tk.Frame(self.frame, padx=self.s(18), pady=self.s(6))
        self.receipt = tk.Label(self.panel, text=self._receipt, anchor="w", justify="left", wraplength=self.s(320),
                                font=(app.body_font, 9), padx=self.s(10), pady=self.s(8))
        self.receipt.pack(fill="x", pady=(0, self.s(10)))
        self.compose_label = tk.Label(self.panel, text="随时补充你的想法", anchor="w", font=(app.body_font, 9))
        self.compose_label.pack(fill="x", pady=(0, self.s(6)))
        compose = tk.Frame(self.panel, padx=self.s(10), pady=self.s(8))
        compose.pack(fill="x")
        self.compose_frame = compose
        self.composer = tk.Text(compose, height=2, width=1, wrap="word", relief="flat", borderwidth=0,
                                highlightthickness=0, font=(app.body_font, 11), undo=True)
        self.composer.pack(side="left", fill="both", expand=True)
        self.composer.bind("<Control-Return>", lambda _: self.submit())
        self.send = ttk.Button(compose, text="↑", width=2, command=self.submit)
        self.send.pack(side="right", padx=(self.s(8), 0))
        self.controls = tk.Frame(self.panel)
        self.controls.pack(fill="x", pady=(self.s(12), 0))
        self.pause = ttk.Button(self.controls, text="Ⅱ 暂停", width=-1, command=app._pause_resume)
        self.pause.pack(side="left")
        self.stop = ttk.Button(self.controls, text="■ 停止", width=-1, command=app.controller.stop)
        self.stop.pack(side="left", padx=self.s(6))
        self.open = ttk.Button(self.controls, text="打开会话 ↗", width=-1, command=self.open_workspace)
        self.open.pack(side="right")
        self.apply_theme()

    def s(self, value):
        return round(value * self.app.scale)

    def apply_theme(self):
        t, key = self.app.theme, self.app.theme_key
        for widget in (self.frame, self.canvas, self.panel, self.controls, self.compose_label):
            widget.configure(bg=t.background)
        self.compose_label.configure(fg=t.muted)
        self.receipt.configure(bg=t.tint, fg=t.accent)
        self.compose_frame.configure(bg=t.surface)
        self.composer.configure(bg=t.surface, fg=t.text, insertbackground=t.accent,
                                selectbackground=t.tint, selectforeground=t.text)
        self.send.configure(style=f"{key}.surface.primary.TButton")
        for button in (self.pause, self.stop, self.open):
            button.configure(style=f"{key}.quiet.TButton")
        self.minimize_button.configure(style=f"{key}.quiet.TButton")
        self._brand_image = brand_photo(self.root, self.s(26), background=t.background)
        self.draw()

    def _rect(self, expanded):
        left, top, right, bottom = self.app.session.work_area()
        width, height = (self.s(370), self.s(340)) if expanded else (self.s(272), self.s(120))
        width, height = min(width, right - left), min(height, bottom - top)
        # Both states share the same top even on a short display, so the fixed
        # header is always inside the visible compact region.
        full_height = min(self.s(340), bottom - top)
        y = min(top + self.s(150), bottom - full_height - self.s(16))
        return width, height, right - width - self.s(8), max(top, y)

    def _set_rect(self, rect):
        if self._surface_bounds is not None:
            self._visible_rect = tuple(rect)
            width, height, x, y = rect
            _, _, sx, sy = self._surface_bounds
            # The HWND and child controls stay still. Only the native clipping
            # region grows; the clipped-out area neither paints nor takes input.
            self.app.session.round_corners(self.s(16), size=(width, height), offset=(x-sx, y-sy), redraw=False)
            self.draw()
            self.app.session.repaint_surface(exposed=True)
            return
        # Win32 positions are absolute virtual-desktop coordinates, including
        # negative monitor origins; Tk's negative offsets mean right/bottom.
        self.app.session.resize_surface(rect, self.s(16))

    def _prepare_surface(self, visible):
        bounds = self._rect(True)
        if self._surface_bounds == bounds:
            return
        self._surface_bounds = bounds
        self._visible_rect = tuple(visible)
        self.canvas.configure(height=self.s(120))
        self.panel.pack(fill="both", expand=True)
        self.app.session.resize_surface(bounds, self.s(16), clip=visible)
        self.root.geometry(f"{bounds[0]}x{bounds[1]}")
        self.draw()

    def visible_rect(self):
        return self._visible_rect or self.app.session.window_rect()

    def _pointer_inside(self, rect):
        x, y = self.root.winfo_pointerxy()
        width, height, rx, ry = rect
        return rx - self.s(12) <= x <= rx + width and ry - self.s(8) <= y <= ry + height + self.s(8)

    def _animate_to(self, rect, *, anchor_right=False):
        if self._animation:
            self.root.after_cancel(self._animation)
            self._animation = None
        self._target = rect
        start = self.visible_rect()
        if anchor_right:
            self._prepare_surface(start)
        began = time.monotonic()

        def tick():
            self._animation = None
            now = time.monotonic()
            p = 1.0 if self.app._reduced.get() else min(1.0, (now - began) / 0.32)
            # A smooth start and finish, using elapsed time rather than frame
            # count so a busy desktop never makes the animation fall behind.
            ease = p * p * (3 - 2 * p)
            frame = [round(a + (b - a) * ease) for a, b in zip(start, rect)]
            if anchor_right:
                # Position and width must share one exact edge, not independent
                # interpolations or stale Tk coordinates during native resize.
                frame[2] = rect[2] + rect[0] - frame[0]
            self._set_rect(frame)
            if p < 1:
                # Subtract this paint's cost. Rounding up to the next global
                # frame boundary wastes another frame when paint exceeds 16ms.
                remaining = 1 / 60 - (time.monotonic() - now)
                self._animation = self.root.after(max(1, round(remaining * 1000)), tick)
            else:
                # Keep Tk's requested size in sync once, after the tween.
                # Position stays native to support negative monitor origins.
                if self._surface_bounds is None:
                    self._prepare_surface(rect)
        tick()

    def settle(self):
        if self._animation:
            self.root.after_cancel(self._animation)
            self._animation = None
            self._set_rect(self._target)
            if self._surface_bounds is None:
                self._prepare_surface(self._target)

    def dock(self, *, resumed=False):
        # Only a worker's acknowledged resume may bypass an unanswered prompt.
        # Its 'working' handshake precedes the next 'observing' state event.
        if self.mode != "workspace" or (not resumed and self.app._phase in {"waiting_user", "waiting_confirmation"}):
            return
        self._workspace_state = self.root.state()
        self._workspace_fullscreen = bool(self.root.attributes("-fullscreen"))
        if self._workspace_fullscreen:
            self.root.attributes("-fullscreen", False)
        if self._workspace_state == "zoomed":
            self.root.state("normal")
        self.root.update_idletasks()
        draft = self.app.composer.get("1.0", "end-1c")
        if draft:
            self.composer.configure(state="normal")
            self.composer.delete("1.0", "end")
            self.composer.insert("1.0", draft)
            self.app.composer.configure(state="normal")
            self.app.composer.delete("1.0", "end")
        self._workspace = self.root.geometry()
        self._workspace_minsize = self.root.minsize()
        self.root.minsize(1, 1)
        self.app.main.grid_remove()
        self.root.overrideredirect(True)
        self.frame.grid(row=0, column=0, sticky="nsew")
        self.mode = "compact"
        self.panel.pack_forget()
        self.canvas.configure(height=self.s(120))
        self._hover_blocked = self._pointer_inside(self._rect(False))
        self._animate_to(self._rect(False))
        self.app.session.ensure_taskbar()
        self.draw()

    def resume_working(self, *, dock=True):
        """Restore the working surface BEFORE enabling desktop passthrough.

        Called for the worker's resume handshake or after releasing an edit
        lease, never merely because the user clicked a confirmation button.
        """
        should_dock = dock or self._interaction_return
        self._interaction_return = False
        if should_dock:
            if self.mode == "workspace":
                self.dock(resumed=True)
            elif self.mode == "expanded":
                self.collapse(release_input=False)
        self.set_ready(False)

    def expand(self, *, keyboard=False):
        if self.mode == "workspace":
            return
        self.app.session.restore_minimized()
        self._keep_for_keyboard = keyboard
        self.mode = "expanded"
        self._animate_to(self._rect(True), anchor_right=True)
        # Merely looking at progress must not acquire desktop input ownership.
        # The pointer entering the input area (or the explicit keyboard shortcut)
        # requests an edit lease separately.
        ready = not self.app.controller.active or self.app.controller.editing_ready or self.app._phase in {"paused", "takeover", "waiting_user", "waiting_confirmation"}
        self.set_ready(ready)
        self.draw()

    def collapse(self, *, release_input=True):
        if self.mode != "expanded":
            return
        self.mode = "compact"
        self._keep_for_keyboard = False
        if release_input:
            self.app._end_composing()
        # A click/send that closes the card must not immediately reopen it
        # under the same stationary pointer. Re-arm on pointer exit, not time.
        self._hover_blocked = self._pointer_inside(self._rect(False))
        self._animate_to(self._rect(False), anchor_right=True)
        self.draw()

    def open_workspace(self, *, interaction=False):
        self.app.session.restore_minimized()
        if self.mode == "workspace":
            if interaction:
                self.app.session.passthrough(False)
            return
        self._interaction_return = interaction
        self.settle()
        self._copy_draft_to_workspace()
        self.mode = "workspace"
        self._keep_for_keyboard = False
        self._surface_bounds = None
        self._visible_rect = None
        self.frame.grid_remove()
        self.root.overrideredirect(False)
        self.app.session.round_corners(0)
        self.app.main.grid()
        if self._workspace_minsize is not None:
            self.root.minsize(*self._workspace_minsize)
        self.root.geometry(self._workspace or f"{self.s(540)}x{self.s(800)}")
        if self._workspace_state == "zoomed":
            self.root.state("zoomed")
        if self._workspace_fullscreen:
            self.root.attributes("-fullscreen", True)
        self._stop_activity()
        if not interaction:
            self.app._begin_composing()
        else:
            self.app.session.passthrough(False)
        self.app._update_controls()

    def _copy_draft_to_workspace(self):
        text = self.composer.get("1.0", "end-1c")
        if text:
            self.app.composer.configure(state="normal")
            self.app.composer.delete("1.0", "end")
            self.app.composer.insert("1.0", text)
            self.composer.configure(state="normal")
            self.composer.delete("1.0", "end")

    def set_ready(self, ready, *, waiting=False):
        self._ready = ready
        state = "normal" if ready else "disabled"
        self.composer.configure(state=state)
        self.send.configure(state=state)
        self.compose_label.configure(text=("正在接收补充 · 发送后调整下一步" if ready else (
            "正在让出桌面操作，请稍候…" if waiting else "助手继续工作 · 移入输入区即可补充")))
        self.draw()

    def set_status(self, phase, title, step):
        self._status = (phase, title, step)
        paused = phase in {"paused", "takeover"}
        active = phase not in TERMINAL_STATUSES | {"idle"}
        if not active:
            self._interaction_return = False
        self.pause.configure(text="▶ 继续" if paused else "Ⅱ 暂停", state="normal" if active else "disabled")
        self.stop.configure(state="normal" if active and phase != "stopping" else "disabled")
        self.draw()

    def set_receipt(self, caption, detail):
        self._receipt = public_text(caption, 35)
        self.receipt.configure(text=self._receipt + "\n" + public_text(detail, 65))
        self.draw()

    def reset_receipt(self):
        self._receipt = "靠近即可补充，随时调整方向"
        self.receipt.configure(text=self._receipt)
        self.draw()

    def submit(self):
        if not self._ready:
            return "break"
        draft = self.composer.get("1.0", "end-1c")
        text = draft.strip()
        if not text:
            return "break"
        # Sending may synchronously return to compact mode and disable this
        # Text widget. Clear before releasing the edit lease, while deletion
        # still works, so a sent supplement never becomes the next draft.
        self.composer.delete("1.0", "end")
        accepted = False
        try:
            accepted = self.app._send_text(text)
        finally:
            if not accepted:
                self.composer.configure(state="normal")
                self.composer.delete("1.0", "end")
                self.composer.insert("1.0", draft)
                self.composer.configure(state="normal" if self._ready else "disabled")
        if accepted:
            # A new request is still being classified. Stay at the edge during
            # thinking; only a finished chat reply needs the reading workspace.
            self.collapse()
        return "break"

    def _header_click(self, event):
        if self.mode == "compact":
            self.expand()
        elif event.x > self.canvas.winfo_width() - self.s(50) and self.s(8) <= event.y <= self.s(46):
            self.minimize()

    def minimize(self):
        # Release a desktop edit lease before hiding, so an invisible editor
        # cannot keep the running task waiting. Drafts remain in the composer.
        self.app._end_composing()
        self.settle()
        self.app.session.minimize()

    def poll(self):
        if self.mode == "workspace" or self.app.session.is_minimized() or self._animation:
            return
        x, y = self.root.winfo_pointerxy()
        width, height, rx, ry = self.visible_rect()
        inside = rx - self.s(12) <= x <= rx + width and ry - self.s(8) <= y <= ry + height + self.s(8)
        now = time.monotonic()
        if self.mode == "compact":
            if not inside:
                self._hover_blocked = False
            human = self.app.demo or now - self.app.session.last_human_pointer < 1.2
            if inside and human and not self._hover_blocked:
                self.expand()
        else:
            if inside:
                self._keep_for_keyboard = False
            human = self.app.demo or now - self.app.session.last_human_pointer < 1.2
            # While desktop input passes through the window, Tk cannot receive
            # Enter events. Show hover from the same physical-pointer poll and
            # request an edit lease only over the actionable minimize control.
            over_minimize = (rx + width - self.s(43) <= x <= rx + width - self.s(7)
                             and ry + self.s(9) <= y <= ry + self.s(45))
            self.minimize_button.state(["active" if over_minimize else "!active"])
            cx, cy = self.compose_frame.winfo_rootx(), self.compose_frame.winfo_rooty()
            editing_zone = over_minimize or (cx <= x <= cx + self.compose_frame.winfo_width() and cy <= y <= ry + height)
            self._compose_since = (self._compose_since or now) if human and editing_zone and not self._ready else None
            if self._compose_since and now - self._compose_since > 0.15:
                self.set_ready(self.app._begin_composing(), waiting=True)
                self._compose_since = None
            draft = self.composer.get("1.0", "end-1c").strip()
            if not inside and not draft and not self._keep_for_keyboard:
                self.collapse()

    def draw(self):
        if not hasattr(self, "canvas"):
            return
        phase, title, step = self._status
        key = (self.mode, phase, title, step, self._receipt, self.canvas.winfo_width(), self._visible_rect,
               self.app.theme_key, self._ready, self.app.controller.editing_ready, self.app._reduced.get())
        if key == self._draw_key:
            self._sync_activity()
            return
        self._draw_key = key
        t, c = self.app.theme, self.canvas
        if self._canvas_items is None:
            self._canvas_items = {
                "mark": c.create_image(0, 0),
                "name": c.create_text(0, 0, anchor="w", font=("Segoe UI", 11, "bold")),
                "toggle": c.create_text(0, 0, font=("Segoe UI", 15)),
                "title": c.create_text(0, 0, anchor="w", font=self._title_font),
                "caption": c.create_text(0, 0, anchor="w", font=(self.app.body_font, 9)),
                "bars": [c.create_line(0, 0, 0, 0, width=self.s(2), capstyle="round") for _ in range(4)],
            }
        items = self._canvas_items
        width = max(c.winfo_width(), self.s(272))
        visible_width = self._visible_rect[0] if self._surface_bounds is not None else width
        x = width - visible_width + self.s(20)
        c.coords(items["mark"], x + self.s(12), self.s(27))
        c.itemconfigure(items["mark"], image=self._brand_image)
        c.coords(items["name"], x + self.s(32), self.s(27))
        c.itemconfigure(items["name"], text=APP_NAME, fill=t.text)
        c.coords(items["toggle"], width - self.s(22), self.s(27))
        c.itemconfigure(items["toggle"], text="‹" if self.mode == "compact" else "−", fill=t.muted)
        c.itemconfigure(items["toggle"], state="normal" if self.mode == "compact" else "hidden")
        if self.mode == "expanded":
            self.minimize_button.place(relx=1, x=-self.s(43), y=self.s(9), width=self.s(36), height=self.s(36))
        else:
            self.minimize_button.place_forget()
        display = public_text(title.replace("\n", ""), 15 if self.mode == "compact" else 21)
        while len(display) > 1 and self._title_font.measure(display) > visible_width - self.s(40):
            display = display[:-2] + "…"
        c.coords(items["title"], x, self.s(58))
        c.itemconfigure(items["title"], text=display, fill=t.text)
        caption = (f"第 {step} 步 · " if step else "") + PHASES.get(phase, ("", ""))[0]
        if self._ready and self.app.controller.editing_ready and phase not in TERMINAL_STATUSES | {"paused", "takeover", "waiting_user", "waiting_confirmation"}:
            caption = "正在接收补充 · 工作进度已保留"
        if self.mode == "compact" and self._receipt != "靠近即可补充，随时调整方向":
            caption = public_text(self._receipt, 25)
        c.coords(items["caption"], x, self.s(85))
        c.itemconfigure(items["caption"], text=caption, fill=t.muted)
        self._activity_bars = []
        for item in items["bars"]:
            c.itemconfigure(item, state="hidden")
        if phase not in TERMINAL_STATUSES | {"idle", "paused", "takeover", "waiting_user", "waiting_confirmation"}:
            for index, item in enumerate(items["bars"]):
                bx, by = width - self.s(50 - index * 7), self.s(84)
                c.itemconfigure(item, state="normal", fill=t.signal or t.accent)
                self._activity_bars.append((item, bx, by))
            self._draw_activity()
        self._sync_activity()

    def _draw_activity(self):
        # Continuous time gives smooth motion at any display/event-loop rate.
        # Only four canvas coordinates change: labels and controls stay intact.
        beat = (time.monotonic() - self._started) * 3.2 if not self.app._reduced.get() else 0
        for index, (item, x, y) in enumerate(self._activity_bars):
            height = self.app.scale * (5 + 9 * (0.5 + 0.5 * math.sin(beat + index * 0.85)))
            self.canvas.coords(item, x, y - height / 2, x, y + height / 2)
        self._activity_frame += 1

    def _sync_activity(self):
        animated = self.mode != "workspace" and bool(self._activity_bars) and not self.app._reduced.get()
        if not animated:
            self._stop_activity()
        elif self._activity_timer is None:
            self._activity_timer = self.root.after(16, self._activity_tick)

    def _activity_tick(self):
        self._activity_timer = None
        if self.mode != "workspace" and self._activity_bars and not self.app._reduced.get():
            self._draw_activity()
            self._sync_activity()

    def _stop_activity(self):
        if self._activity_timer is not None:
            self.root.after_cancel(self._activity_timer)
            self._activity_timer = None

    def close(self):
        self._stop_activity()
        if self._animation:
            self.root.after_cancel(self._animation)
            self._animation = None
