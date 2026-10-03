"""Mirally's responsive conversation workspace and desktop companion."""

from __future__ import annotations

import json
import queue
import time
import tkinter as tk
from pathlib import Path
from tkinter import ttk, messagebox
from tkinter.scrolledtext import ScrolledText

from openassist.lyric_view import LyricView
from openassist.presentation import ACTION_NAMES, PHASES, THEMES, DEFAULT_THEME, public_text, message_text, choose_ui_font
from openassist.task_controller import TaskController
from openassist.window_session import WindowSession, enable_dpi_awareness
from openassist.widgets import ButtonStyles
from openassist.conversation import ConversationStore
from openassist.edge_companion import EdgeCompanion
from openassist.branding import APP_NAME, apply_window_icon, brand_photo, prepare_process_identity
from openassist.startup import StartupReveal
from openassist.session_list import SessionList
from openassist.settings_view import SettingsView


class OpenAssistShell:
    def __init__(self, product_root: Path, *, demo=False, startup=True):
        self.product_root = product_root
        self.demo = demo
        enable_dpi_awareness()
        prepare_process_identity()
        self.root = tk.Tk()
        self.body_font = choose_ui_font(self.root)
        self.scale = max(1.0, self.root.winfo_fpixels("1i") / 96)
        self.root.title(APP_NAME + (" · 体验演示" if demo else ""))
        self.root.withdraw()
        apply_window_icon(self.root)
        self.session = WindowSession(self.root, demo=demo)
        self.conversation = ConversationStore(None if demo else product_root / "config" / "openassist" / "conversations")
        self.controller = TaskController(demo=demo, conversation=self.conversation)
        self.dock_on_run = True
        self.preference_path = product_root / "config" / "openassist" / "interface.json"
        self.preferences = self._load_preferences()
        self.theme_key = self.preferences.get("theme", DEFAULT_THEME)
        if self.theme_key not in THEMES:
            self.theme_key = DEFAULT_THEME
        self.theme = THEMES[self.theme_key]
        self.button_styles = ButtonStyles(self.root, self.scale)
        self._frames = []
        self._labels = []
        self._buttons = []
        self._lines = []
        self._closing = False
        self._records_open = False
        self._settings_open = False
        self._memory_open = False
        self._history_open = False
        self._phase = "idle"
        self._request_id = ""
        self._started = None
        self._elapsed = 0
        self._step = 0
        self._record_count = 0
        self._receipts = {}
        self._settling = False
        self._focus_editor_when_ready = False
        self._task_text = ""
        self._edge_request = None
        self._wide = False
        self._layout_job = None
        self._layout_key = None
        self.startup = None
        self._topmost = tk.BooleanVar(value=bool(self.preferences.get("topmost", True)))
        self._reduced = tk.BooleanVar(value=bool(self.preferences.get("reduced_motion", False)))
        self._theme_name = tk.StringVar(value=self.theme.name)
        self.status_var = tk.StringVar(value="准备就绪")
        self.time_var = tk.StringVar(value="")
        self.task_var = tk.StringVar(value="一起想清楚，也一起把事情做好。")
        self.composer_var = tk.StringVar(value="问一个问题，或交给我一件事")
        self.chat_status = tk.StringVar(value="")
        self.hint_var = tk.StringVar()
        self.receipt_var = tk.StringVar()
        self.memory_var = tk.StringVar()
        self.session_title = tk.StringVar(value="新的会话")
        self._build()
        self.edge = EdgeCompanion(self)
        width = min(round(540 * self.scale), self.root.winfo_screenwidth() - round(50 * self.scale))
        height = min(round(800 * self.scale), self.root.winfo_screenheight() - round(90 * self.scale))
        self.root.geometry(f"{width}x{height}+{max(0, (self.root.winfo_screenwidth()-width)//2)}+{max(0, (self.root.winfo_screenheight()-height)//2)}")
        self.root.minsize(min(width, round(450 * self.scale)), min(height, round(650 * self.scale)))
        self.root.protocol("WM_DELETE_WINDOW", self._close)
        self.root.bind("<Control-Return>", self._submit)
        self.root.bind("<Escape>", self._escape)
        self._apply_theme()
        self._pin()
        self.root.bind("<Map>", self._native_mapped, add="+")
        self.root.deiconify()
        self._hotkeys_ready = self.session.register_hotkeys()
        self.controller.desktop_available = self._hotkeys_ready
        self._update_hint()
        self._refresh_conversation()
        self.root.after(40, self._poll)
        # Keep the real editor focused under the reveal: the first key dismisses
        # the overlay and still reaches the same Text widget.
        self.root.after_idle(self.composer.focus_set)
        if startup:
            self.startup = StartupReveal(self.root, self.theme, scale=self.scale,
                                         body_font=self.body_font, reduced_motion=self._reduced.get(),
                                         on_complete=self.composer.focus_set)
            self.root.after_idle(self.startup.start)

    def _load_preferences(self):
        try:
            value = json.loads(self.preference_path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save_preferences(self):
        value = {"theme": self.theme_key, "topmost": self._topmost.get(), "reduced_motion": self._reduced.get()}
        try:
            self.preference_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.preference_path.with_suffix(".json.tmp")
            temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(self.preference_path)
        except OSError:
            self.hint_var.set("设置暂时无法保存，本次会话仍然有效。")

    def _frame(self, parent, *, surface=False, **kw):
        widget = tk.Frame(parent, borderwidth=0, **kw)
        self._frames.append((widget, surface))
        return widget

    def _label(self, parent, *, muted=False, surface=False, **kw):
        kw.setdefault("font", (self.body_font, 10))
        widget = tk.Label(parent, borderwidth=0, anchor="w", **kw)
        self._labels.append((widget, muted, surface))
        return widget

    def _button(self, parent, text, command, *, role="quiet", **kw):
        widget = ttk.Button(parent, text=text, command=command, cursor="hand2", takefocus=True, **kw)
        if any(frame is parent and surface for frame, surface in self._frames):
            role = "surface." + role
        self._buttons.append((widget, role))
        return widget

    def _rule(self, parent):
        widget = tk.Frame(parent, height=1)
        self._lines.append(widget)
        widget.pack(fill="x")

    def _build(self):
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        self.main = self._frame(self.root)
        self.main.grid(sticky="nsew")
        self.main.rowconfigure(0, weight=1)
        self.main.columnconfigure(1, weight=1)
        self._build_sidebar()
        main = self.content = self._frame(self.main, padx=26, pady=18)
        main.grid(row=0, column=1, sticky="nsew")
        self.main.bind("<Configure>", self._queue_layout)
        header = self._frame(main)
        header.pack(fill="x", pady=(0, 16))
        self.mark = tk.Canvas(header, width=round(34 * self.scale), height=round(34 * self.scale), highlightthickness=0)
        self.mark.pack(side="left", padx=(0, 10))
        self._label(header, text=APP_NAME, font=("Segoe UI", 17, "bold")).pack(side="left")
        self.settings_button = self._button(header, "设置", self._toggle_settings, width=-1)
        self.settings_button.pack(side="right")
        self.memory_button = self._button(header, "记忆", self._toggle_memory, width=-1)
        self.memory_button.pack(side="right", padx=(0, 6))
        self.history_button = self._button(header, "历史", self._toggle_history, width=-1)
        self.history_button.pack(side="right", padx=(0, 6))
        self.return_button = self._button(header, "浮窗", self._return_to_edge, width=-1)
        self.return_button.pack(side="right", padx=(0, 6))
        self._rule(main)

        task = self.task_row = self._frame(main, pady=17)
        task.pack(fill="x")
        self._label(task, textvariable=self.status_var, font=(self.body_font, 10, "bold")).pack(side="left")
        self._label(task, textvariable=self.time_var, muted=True, font=("Segoe UI", 10)).pack(side="right")
        self.task_label = self._label(main, textvariable=self.task_var, muted=True, wraplength=460)
        self.task_label.pack(fill="x", pady=(0, 10))
        self.task_label.bind("<Configure>", lambda e: self.task_label.configure(wraplength=max(100, e.width)))
        self.memory_summary = self._label(main, textvariable=self.memory_var, muted=True, font=(self.body_font, 9))
        self.memory_summary.pack(fill="x", pady=(0, 6))

        self.stack = self._frame(main)
        self.stack.pack(fill="both", expand=True)
        self.stack.rowconfigure(0, weight=1)
        self.stack.columnconfigure(0, weight=1)
        self.lyrics = LyricView(self.stack, self.theme)
        self.lyrics.reduced_motion = self._reduced.get()
        self.lyrics.grid(row=0, column=0, sticky="nsew")

        self.chat = self._frame(self.stack)
        self.chat_text = ScrolledText(self.chat, height=5, state="disabled", wrap="word", borderwidth=0,
                                     highlightthickness=0, padx=10, pady=12, font=(self.body_font, 11), spacing3=8)
        self.chat_text.pack(fill="both", expand=True)
        self._label(self.chat, textvariable=self.chat_status, muted=True, font=(self.body_font, 9)).pack(anchor="w", padx=10, pady=(4, 8))
        self._show_main_content()

        self.records = self._frame(self.stack)
        self._label(self.records, text="执行记录", font=(self.body_font, 14, "bold")).pack(anchor="w", pady=(10, 16))
        self.transcript = ScrolledText(self.records, height=5, state="disabled", wrap="word", borderwidth=0,
                                      highlightthickness=0, padx=5, pady=6, font=(self.body_font, 10), spacing3=10)
        self.transcript.pack(fill="both", expand=True)

        self.settings = SettingsView(self.stack, self)

        self.memory = self._frame(self.stack)
        self._label(self.memory, text="会话记忆", font=(self.body_font, 16, "bold")).pack(anchor="w", pady=(8, 6))
        memory_note = self._label(self.memory, text="本地保留最近 40 项交流，更早内容转为摘要。\n每次参考最近最多 8 项，受上下文长度限制。", muted=True, justify="left", font=(self.body_font, 9))
        memory_note.pack(fill="x", pady=(0, 8))
        memory_note.bind("<Configure>", lambda e: memory_note.configure(wraplength=max(100, e.width)))
        self._label(self.memory, textvariable=self.session_title, muted=True).pack(fill="x", pady=(0, 8))
        memory_controls = self._frame(self.memory)
        memory_controls.pack(fill="x", side="bottom", pady=(6, 0))
        self.new_conversation = self._button(memory_controls, "新建会话", self._new_conversation, role="outline")
        self.new_conversation.pack(side="left")
        self.clear_conversation = self._button(memory_controls, "清空本会话", self._clear_conversation)
        self.clear_conversation.pack(side="right")
        self.memory_text = ScrolledText(self.memory, height=4, wrap="word", state="disabled", borderwidth=0,
                                        highlightthickness=0, font=(self.body_font, 10), spacing3=8)
        self.memory_text.pack(fill="both", expand=True)

        self.history = self._frame(self.stack, surface=True, padx=12, pady=12)
        history_header = self._frame(self.history, surface=True)
        history_header.pack(fill="x", pady=(0, 12))
        self._label(history_header, text="历史会话", surface=True, font=(self.body_font, 15, "bold")).pack(side="left")
        self.history_new = self._button(history_header, "＋ 新建会话", self._new_conversation, role="outline")
        self.history_new.pack(side="right")
        self.history_note = self._label(self.history, text="选一段对话，接着聊。", surface=True, muted=True, font=(self.body_font, 9))
        self.history_note.pack(fill="x", pady=(0, 8))
        self.history_list = SessionList(self.history, self)
        self.history_list.pack(fill="both", expand=True)

        self.feedback = self._frame(main, surface=True, padx=14, pady=10)
        self._label(self.feedback, textvariable=self.receipt_var, surface=True, muted=True,
                    font=(self.body_font, 9)).pack(anchor="w", pady=(0, 5))
        self.receipt_text = ScrolledText(self.feedback, height=3, width=1, wrap="word", state="disabled",
                                         relief="flat", borderwidth=0, highlightthickness=0,
                                         font=(self.body_font, 10), spacing3=4, takefocus=True)
        self.receipt_text.pack(fill="x")

        self.interaction = self._frame(main, surface=True, padx=14, pady=12)
        self.interaction_text = tk.Text(self.interaction, height=5, wrap="word", state="disabled", relief="flat", borderwidth=0,
                                        font=(self.body_font, 10), padx=0, pady=0, takefocus=True)
        self.interaction_text.pack(fill="x")
        self.confirm_buttons = self._frame(self.interaction, surface=True)
        self.allow = self._button(self.confirm_buttons, "允许这一次", lambda: self._confirm(True), role="primary")
        self.allow.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.reject = self._button(self.confirm_buttons, "拒绝", lambda: self._confirm(False), role="outline")
        self.reject.pack(side="left", fill="x", expand=True)

        self.bottom = self._frame(main)
        self.bottom.pack(fill="x", side="bottom")
        self.record_button = self._button(self.bottom, "≡  查看执行记录", self._toggle_records)
        self.record_button.pack(fill="x", pady=(8, 8))
        self._rule(self.bottom)
        self.controls = self._frame(self.bottom)
        self.controls.pack(fill="x", pady=(14, 10))
        self.controls.columnconfigure((0, 1, 2), weight=1, uniform="controls")
        self.pause_button = self._button(self.controls, "Ⅱ  暂停", self._pause_resume, role="primary")
        self.pause_button.grid(row=0, column=0, sticky="ew", padx=(0, 7))
        self.stop_button = self._button(self.controls, "■  停止", self.controller.stop, role="outline")
        self.stop_button.grid(row=0, column=1, sticky="ew", padx=7)
        self.takeover_button = self._button(self.controls, "接管", lambda: self.controller.pause(takeover=True), role="outline")
        self.takeover_button.grid(row=0, column=2, sticky="ew", padx=(7, 0))

        self.composer_label = self._label(self.bottom, textvariable=self.composer_var, muted=True, font=(self.body_font, 9))
        self.composer_label.pack(anchor="w", pady=(4, 7))
        compose = self._frame(self.bottom, surface=True, padx=12, pady=10)
        compose.pack(fill="x")
        self.composer = tk.Text(compose, height=2, width=1, wrap="word", relief="flat", borderwidth=0,
                                highlightthickness=0, font=(self.body_font, 11), undo=True)
        self.composer.pack(side="left", fill="both", expand=True)
        self.send_button = self._button(compose, "↑", self._submit, role="primary", width=2)
        self.send_button.pack(side="right", padx=(10, 0))
        self.stop_reply_button = self._button(compose, "■", self.controller.stop, width=2)
        self.hint_label = self._label(self.bottom, textvariable=self.hint_var, muted=True, font=(self.body_font, 8), justify="left")
        self.hint_label.pack(anchor="w", pady=(12, 0))
        # Reserve the controls before giving the lyric area the remaining room.
        # Packing the expanding canvas first clipped controls at small sizes.
        self.stack.pack_forget()
        self.stack.pack(fill="both", expand=True)
        self._update_controls()

    def _build_sidebar(self):
        """Use wide windows for session context, preserving a full-height work area."""
        self.sidebar = self._frame(self.main, surface=True, padx=24, pady=30)
        self.sidebar.grid_propagate(False)
        self.sidebar.pack_propagate(False)
        self._label(self.sidebar, text="会话空间", surface=True,
                    font=(self.body_font, 16, "bold")).pack(fill="x", pady=(0, 10))
        self.session_title_label = self._label(self.sidebar, textvariable=self.session_title,
                                                surface=True, muted=True, justify="left", wraplength=240)
        self.session_title_label.pack(fill="x", pady=(0, 24))
        self.sidebar_new = self._button(self.sidebar, "＋  新建会话", self._new_conversation, role="outline")
        self.sidebar_new.pack(fill="x", pady=(0, 26))
        self._label(self.sidebar, text="最近交流", surface=True, muted=True,
                    font=(self.body_font, 9)).pack(anchor="w", pady=(0, 12))
        self.sidebar_note = self._label(self.sidebar, text="消息、补充与工作结果，\n都接在这段对话里。", muted=True,
                                        surface=True, justify="left", font=(self.body_font, 9))
        self.sidebar_note.pack(fill="x", side="bottom", pady=(16, 0))
        self._label(self.sidebar, textvariable=self.status_var, surface=True,
                    font=(self.body_font, 10, "bold")).pack(fill="x", side="bottom", pady=(16, 0))
        # Pack footer first so long histories scroll instead of hiding it.
        self.sidebar_list = SessionList(self.sidebar, self)
        self.sidebar_list.pack(fill="both", expand=True)

    def _queue_layout(self, _event=None):
        if self._closing or (hasattr(self, "edge") and self.edge.mode != "workspace"):
            return
        # Resolve responsive geometry during Configure, before idle painting.
        # Deferring this let the maximized window paint the narrow layout and
        # then expose an unpainted sidebar while the second layout caught up.
        self._layout_workspace()

    def _layout_workspace(self):
        self._layout_job = None
        if self._closing or (hasattr(self, "edge") and self.edge.mode != "workspace"):
            return
        width, height = self.main.winfo_width(), self.main.winfo_height()
        if width < 10 or (width, height) == self._layout_key:
            return
        self._layout_key = (width, height)
        wide = width >= round(980 * self.scale)
        self._wide = wide
        side_width = min(round(300 * self.scale), round(width * .25)) if wide else 0
        if wide:
            if self._history_open:
                self._dismiss_drawers()
            self.sidebar.configure(width=side_width)
            self.sidebar.grid(row=0, column=0, sticky="nsew")
            self.session_title_label.configure(wraplength=max(100, side_width - 48))
            self.sidebar_note.configure(wraplength=max(100, side_width - 48))
        else:
            self.sidebar.grid_remove()
        padding = round((32 if wide else 18) * self.scale)
        self.content.configure(padx=padding, pady=round((22 if wide else 12) * self.scale))
        available = max(100, width - side_width - padding * 2)
        self.hint_label.configure(wraplength=available)
        self.chat_text.configure(font=(self.body_font, 12 if wide else 11),
                                 padx=round((24 if wide else 8) * self.scale))
        self.composer.configure(height=3 if wide and height > 700 * self.scale else 2)
        self._layout_heading()

    def _layout_heading(self):
        # In a short confirmation window, reserve space for the question and
        # supplement receipt rather than repeating the introductory tagline.
        compact_prompt = self._phase in {"waiting_user", "waiting_confirmation"} and not self._wide
        if compact_prompt:
            self.task_label.pack_forget()
            self.memory_summary.pack_forget()
            self.task_row.configure(pady=2)
        else:
            if not self.task_label.winfo_manager():
                self.task_label.pack(fill="x", pady=(0, 10), after=self.task_row)
                self.memory_summary.pack(fill="x", pady=(0, 6), after=self.task_label)
            self.task_row.configure(pady=17)

    def _sync_drawer_buttons(self):
        for button, selected in ((self.settings_button, self._settings_open),
                                 (self.memory_button, self._memory_open),
                                 (self.history_button, self._history_open),
                                 (self.record_button, self._records_open)):
            button.state(["selected" if selected else "!selected"])

    def _apply_theme(self):
        t = self.theme
        self._theme_name.set(t.name)
        self.button_styles.apply(self.theme_key, t)
        self.root.configure(bg=t.background)
        self.session.set_native_background(t.background)
        for widget, surface in self._frames:
            widget.configure(bg=t.surface if surface else t.background)
        for widget, muted, surface in self._labels:
            widget.configure(bg=t.surface if surface else t.background, fg=t.muted if muted else t.text)
        for widget in self._lines:
            widget.configure(bg=t.border)
        for widget, role in self._buttons:
            widget.configure(style=f"{self.theme_key}.{role}.TButton")
        for widget in (self.composer, self.interaction_text, self.receipt_text):
            widget.configure(bg=t.surface, fg=t.text, insertbackground=t.accent, selectbackground=t.tint, selectforeground=t.text)
        self.receipt_text.tag_configure("status", foreground=t.accent, font=(self.body_font, 9))
        self.receipt_text.tag_configure("unused", foreground=t.muted, font=(self.body_font, 9))
        self.transcript.configure(bg=t.background, fg=t.text, selectbackground=t.tint, selectforeground=t.text)
        self.chat_text.configure(bg=t.background, fg=t.text, selectbackground=t.tint, selectforeground=t.text)
        self.chat_text.tag_configure("role", foreground=t.accent, font=(self.body_font, 9, "bold"), spacing1=14, spacing3=6)
        self.chat_text.tag_configure("user", foreground=t.text, background=t.tint, lmargin1=12, lmargin2=12, rmargin=12, spacing1=8, spacing3=8)
        self.chat_text.tag_configure("muted", foreground=t.muted, font=(self.body_font, 10))
        self.memory_text.configure(bg=t.background, fg=t.text, selectbackground=t.tint, selectforeground=t.text)
        self.sidebar_list.draw()
        self.history_list.draw()
        self.transcript.tag_configure("caption", foreground=t.muted, font=(self.body_font, 9))
        self.settings.apply_theme()
        self.mark.configure(bg=t.background)
        self.mark.delete("all")
        mark_size = round(34 * self.scale)
        self._brand_image = brand_photo(self.root, mark_size, background=t.background)
        self.mark.create_image(mark_size // 2, mark_size // 2, image=self._brand_image)
        self.lyrics.set_theme(t)
        if hasattr(self, "edge"):
            self.edge.apply_theme()
        self._sync_drawer_buttons()

    def _choose_theme(self, _event=None):
        self.theme_key = next(key for key, theme in THEMES.items() if theme.name == self._theme_name.get())
        self.theme = THEMES[self.theme_key]
        self._apply_theme()
        self._save_preferences()

    def _native_mapped(self, event):
        if event.widget is self.root and not self._closing:
            # Tk can replace its outer HWND when switching decorated/floating
            # modes. Initialize the real wrapper, including the first map.
            self.session.set_native_background(self.theme.background)

    def _motion(self):
        self.lyrics.reduced_motion = self._reduced.get()
        if self._reduced.get() and self.startup:
            self.startup.finish()
        if self._reduced.get():
            self.edge.settle()
        self._save_preferences()

    def _pin(self):
        self.session.topmost = self._topmost.get()
        self.root.attributes("-topmost", self._topmost.get())
        self._save_preferences()

    def _dismiss_drawers(self):
        self._records_open = self._settings_open = self._memory_open = self._history_open = False
        self.records.grid_remove()
        self.settings.grid_remove()
        self.memory.grid_remove()
        self.history.grid_remove()
        self._show_main_content()
        if self._receipts and not self.feedback.winfo_manager():
            self.feedback.pack(fill="x", side="bottom", after=self.bottom, pady=(4, 8))
        self.record_button.configure(text="≡  查看执行记录")
        self._sync_drawer_buttons()

    def _show_main_content(self):
        self.lyrics.grid_remove()
        self.chat.grid_remove()
        conversation = self.controller.mode != "desktop" or self._phase in {"idle", "completed", "failed", "cancelled"}
        (self.chat if conversation else self.lyrics).grid(row=0, column=0, sticky="nsew")

    def _return_to_edge(self):
        if self._phase in {"waiting_user", "waiting_confirmation", "pausing", "stopping"}:
            return
        if self.startup and self.startup.active:
            self.startup.finish()
        self.edge.dock()
        self._end_composing()

    def _escape(self, _event=None):
        if self.startup and self.startup.active:
            self.startup.finish()
            return "break"
        if self._records_open or self._settings_open or self._memory_open or self._history_open:
            self._dismiss_drawers()
        elif self.edge.mode == "workspace":
            self._return_to_edge()
        elif self.edge.mode == "expanded":
            self.edge.collapse()
        return "break"

    def _toggle_settings(self):
        was_open = self._settings_open
        self._dismiss_drawers()
        if not was_open:
            self.feedback.pack_forget()
            self.lyrics.grid_remove()
            self.chat.grid_remove()
            self.settings.grid(row=0, column=0, sticky="nsew")
            self._settings_open = True
        self._sync_drawer_buttons()

    def _toggle_history(self):
        was_open = self._history_open
        self._dismiss_drawers()
        if self._wide:
            self.sidebar_list.canvas.focus_set()
            return
        if not was_open:
            self.feedback.pack_forget()
            self._refresh_conversation()
            self.lyrics.grid_remove()
            self.chat.grid_remove()
            self.history.grid(row=0, column=0, sticky="nsew")
            self._history_open = True
            self.history_list.canvas.focus_set()
        self._sync_drawer_buttons()

    def _toggle_records(self):
        was_open = self._records_open
        self._dismiss_drawers()
        if not was_open:
            self.feedback.pack_forget()
            self.lyrics.grid_remove()
            self.chat.grid_remove()
            self.records.grid(row=0, column=0, sticky="nsew")
            self.record_button.configure(text="‹  返回当前任务")
            self._records_open = True
        self._sync_drawer_buttons()

    def _refresh_conversation(self):
        data = self.conversation.snapshot()
        self.session_title.set(data["title"])
        completed = [task for task in data["tasks"] if task["status"] != "running"]
        self.memory_var.set(("演示会话" if self.demo else "当前会话") + f" · 现有 {len(completed)} 项交流记录")
        self._render_chat(data)
        self._saved_sessions = self.conversation.sessions()
        for view in (self.sidebar_list, self.history_list):
            view.set_sessions(self._saved_sessions, data["id"])
        self.memory_text.configure(state="normal")
        self.memory_text.delete("1.0", "end")
        if not data["tasks"]:
            self.memory_text.insert("end", "后续对话和工作会参考本会话的消息、补充与结果。\n\n新建会话会开始独立上下文；旧会话可以从「历史」恢复。\n\n长期个人偏好记忆尚未启用。")
        for index, task in enumerate(data["tasks"], 1):
            status = PHASES.get(task["status"], ("上次运行中断" if task["status"] == "interrupted" else "进行中", ""))[0]
            if task.get("kind") == "conversation" and task["status"] == "completed":
                status = "已回复"
            self.memory_text.insert("end", f"{index:02d} · {status}\n{task['request']}\n")
            for addition in task["inputs"]:
                self.memory_text.insert("end", "补充：" + addition + "\n")
            if task["result"]:
                self.memory_text.insert("end", "结果：" + task["result"] + "\n")
            self.memory_text.insert("end", "\n")
        if data.get("older_summary"):
            self.memory_text.insert("end", "更早的摘要\n" + data["older_summary"])
        self.memory_text.configure(state="disabled")
        if self.conversation.error:
            self.hint_var.set(self.conversation.error)

    def _render_chat(self, data):
        at_bottom = self.chat_text.yview()[1] >= 0.98
        position = self.chat_text.yview()[0]
        self.chat_text.configure(state="normal")
        self.chat_text.delete("1.0", "end")
        if not data["tasks"]:
            self.chat_text.insert("end", APP_NAME + "\n", "role")
            self.chat_text.insert("end", "你好，有什么想一起聊聊的？\n\n可以问我问题、讨论一个想法，\n也可以让我帮你处理电脑上的事情。\n")
        for turn in data["tasks"]:
            self.chat_text.insert("end", "你\n", "role")
            self.chat_text.insert("end", message_text(turn["request"]) + "\n", "user")
            for addition in turn["inputs"]:
                self.chat_text.insert("end", "补充 · " + message_text(addition) + "\n", "user")
            if turn["result"]:
                self.chat_text.insert("end", APP_NAME + "\n", "role")
                self.chat_text.insert("end", message_text(turn["result"]) + "\n\n")
            elif turn["status"] == "running":
                self.chat_text.insert("end", "\n正在思考…\n", "muted")
        self.chat_text.configure(state="disabled")
        if at_bottom:
            self.chat_text.see("end")
        else:
            self.chat_text.yview_moveto(position)

    def _toggle_memory(self):
        was_open = self._memory_open
        self._dismiss_drawers()
        if not was_open:
            self.feedback.pack_forget()
            self._refresh_conversation()
            self.lyrics.grid_remove()
            self.chat.grid_remove()
            self.memory.grid(row=0, column=0, sticky="nsew")
            self._memory_open = True
        self._sync_drawer_buttons()

    def _reset_conversation_view(self):
        self._receipts.clear()
        self.edge.reset_receipt()
        self.feedback.pack_forget()
        self.transcript.configure(state="normal")
        self.transcript.delete("1.0", "end")
        self.transcript.configure(state="disabled")
        self._record_count = 0
        self.controller.mode = "conversation"
        self.task_var.set("一起想清楚，也一起把事情做好。")
        self._on_state({"phase": "idle", "title": PHASES["idle"][1]})
        self._refresh_conversation()

    def _new_conversation(self):
        if self.controller.active:
            return
        self.conversation.new()
        self._dismiss_drawers()
        self._reset_conversation_view()
        self.composer.focus_set()

    def _clear_conversation(self):
        if self.controller.active:
            return
        if messagebox.askyesno("清空本会话", "删除本会话保存的消息、补充与工作结果？其他会话不受影响。", parent=self.root):
            self.conversation.clear()
            self._reset_conversation_view()

    def _select_conversation(self, identifier):
        if self.controller.active:
            return
        if identifier not in {key for key, _ in self._saved_sessions}:
            return
        try:
            self.conversation.select(identifier)
        except (OSError, ValueError, KeyError, TypeError):
            self.hint_var.set("这个会话暂时无法读取，当前会话已保留。")
            return
        self._dismiss_drawers()
        self._reset_conversation_view()

    def _record(self, title, detail=""):
        self.transcript.configure(state="normal")
        self.transcript.insert("end", f"{time.strftime('%H:%M')}  {public_text(title)}\n")
        if detail:
            self.transcript.insert("end", public_text(detail, 800) + "\n", "caption")
        self.transcript.insert("end", "\n")
        self._record_count += 1
        if self._record_count > 150:
            self.transcript.delete("1.0", "5.0")
        self.transcript.configure(state="disabled")
        self.transcript.see("end")

    def _submit(self, _event=None):
        if self.startup:
            self.startup.finish()
        draft = self.composer.get("1.0", "end-1c")
        text = draft.strip()
        if not text:
            return "break"
        # Sending may synchronously dock and migrate any remaining draft. Clear
        # the submitted text first, and restore it if the controller rejects it.
        self.composer.configure(state="normal")
        self.composer.delete("1.0", "end")
        accepted = False
        try:
            accepted = self._send_text(text)
        finally:
            if not accepted:
                self.composer.configure(state="normal")
                self.composer.insert("1.0", draft)
            self._update_controls()
        return "break"

    def _send_text(self, text):
        accepted = False
        is_followup = self.controller.active
        if self._phase == "waiting_user":
            accepted = self.controller.reply(self._request_id, text)
        elif self.controller.active:
            accepted = self.controller.supplement(text)
        elif not self.controller.active:
            self._task_text = text
            self.task_var.set("一起想清楚，也一起把事情做好。")
            self.lyrics.reset()
            self._dismiss_drawers()
            self._started = time.monotonic()
            self._step = 0
            accepted = self.controller.start(text)
            self._edge_request = self.controller.task_id if accepted and self.edge.mode != "workspace" else None
            self._show_main_content()
        if accepted:
            if not is_followup:
                self._receipts.clear()
                self.edge.reset_receipt()
                self.feedback.pack_forget()
                self._record("你", text)
            elif self._phase not in {"paused", "takeover", "waiting_user", "waiting_confirmation"}:
                self._end_composing()
        return accepted

    def _begin_composing(self):
        if self._phase in {"stopping", "pausing"}:
            return False
        ready = not self.controller.active or self.controller.begin_edit()
        if ready:
            self.session.apply("interactive")
        self._update_controls()
        return ready

    def _end_composing(self):
        self._focus_editor_when_ready = False
        if self.controller.active and self.controller.mode == "desktop" and self._phase not in {"paused", "takeover", "waiting_user", "waiting_confirmation", "stopping", "pausing"}:
            self.edge.resume_working(dock=self.dock_on_run)
            self.session.apply("working")
        self.controller.end_edit()
        self._update_controls()

    def _focus_editor(self):
        ready = not self.controller.active or self.controller.mode != "desktop" or self.controller.editing_ready or self._phase in {"paused", "takeover", "waiting_user", "waiting_confirmation"}
        if self._focus_editor_when_ready and ready:
            self.root.focus_force()
            (self.edge.composer if self.edge.mode == "expanded" else self.composer).focus_set()
            self._focus_editor_when_ready = False

    def _on_receipt(self, event):
        receipt_id = event["receipt_id"]
        previous = self._receipts.get(receipt_id)
        if previous and previous["status"] == event["status"]:
            return
        self._receipts[receipt_id] = event
        self.edge.set_receipt(self._receipt_caption(event), event["detail"])
        self.receipt_var.set(f"你的补充与回复 · {len(self._receipts)} 条" + (" · 演示" if self.demo else ""))
        self.receipt_text.configure(state="normal")
        self.receipt_text.delete("1.0", "end")
        for index, receipt in enumerate(self._receipts.values(), 1):
            latest_start = self.receipt_text.index("end-1c")
            caption = self._receipt_caption(receipt)
            self.receipt_text.insert("end", f"{index:02d}  {caption}\n", "unused" if receipt["status"] == "unused" else "status")
            self.receipt_text.insert("end", receipt["detail"] + "\n")
        self.receipt_text.configure(state="disabled")
        # Keep the latest status visible even when its text spans many lines.
        self.receipt_text.yview(latest_start)
        if not self.feedback.winfo_manager() and not (self._memory_open or self._settings_open or self._records_open or self._history_open):
            self.feedback.pack(fill="x", side="bottom", after=self.bottom, pady=(4, 8))
        # Every entry remains scrollable here and searchable by eye in records.
        self._record(self._receipt_caption(event), event["detail"] if previous is None else f"第 {list(self._receipts).index(receipt_id) + 1} 条补充 / 回复")

    def _receipt_caption(self, receipt):
        if receipt["status"] == "applied":
            return "演示：已模拟接收" if self.demo else "已加入当前任务上下文"
        if receipt["status"] == "unused":
            return "未加入上下文 · 任务已结束"
        if receipt["source"] == "answer":
            return "已收到 · 正在交给 Agent"
        return "已收到 · 继续后交给 Agent" if receipt.get("delivery") == "resume" else "已收到 · 下一步将调整"

    def _confirm(self, allowed):
        if self.controller.reply(self._request_id, allowed):
            self.allow.configure(state="disabled")
            self.reject.configure(state="disabled")
            self._record("已允许当前操作" if allowed else "已拒绝当前操作")

    def _pause_resume(self):
        if self.controller.phase in {"paused", "takeover"}:
            self.controller.resume()
        else:
            self.controller.pause()

    def _update_hint(self):
        if self.demo:
            self.hint_var.set("体验演示 · 不连接模型，不操作电脑\nCtrl + Enter 发送")
        elif not getattr(self, "_hotkeys_ready", False):
            self.hint_var.set("可以正常对话 · 全局快捷键不可用，桌面操作暂不可启动")
        else:
            self.hint_var.set("Ctrl + Enter 发送 · 对话和工作共用上下文\nCtrl + Alt + Enter 补充 · Space 暂停 / 继续 · Esc 停止（均加 Ctrl + Alt）")

    def _update_controls(self):
        self._layout_heading()
        phase = self._phase
        active = phase not in {"idle", "completed", "cancelled", "failed"}
        waiting = phase in {"waiting_user", "waiting_confirmation"}
        paused = phase in {"paused", "takeover"}
        ending = phase in {"pausing", "stopping"}
        desktop = self.controller.mode == "desktop"
        self.return_button.configure(state="disabled" if waiting or ending else "normal")
        if desktop and active:
            if not self.controls.winfo_manager():
                self.controls.pack(fill="x", pady=(14, 10), before=self.composer_label)
        else:
            self.controls.pack_forget()
        if active and not desktop:
            self.stop_reply_button.pack(side="right", padx=(6, 0))
        else:
            self.stop_reply_button.pack_forget()
        for button in (self.pause_button, self.takeover_button):
            button.configure(state="normal" if active and desktop and not ending else "disabled")
        self.pause_button.configure(text="▶  继续" if paused else "Ⅱ  暂停")
        self.stop_button.configure(state="normal" if active and phase != "stopping" else "disabled")
        editable = not ending and ((not active and not self.controller.active) or not desktop or waiting or paused or self.controller.editing_ready)
        self.composer.configure(state="normal" if editable else "disabled")
        self.send_button.configure(state="normal" if editable else "disabled")
        self.settings_button.configure(state="normal" if not active or waiting or paused else "disabled")
        self.memory_button.configure(state="normal" if not active or waiting or paused or self.controller.editing_ready else "disabled")
        self.history_button.configure(state="normal" if not active or waiting or paused or self.controller.editing_ready else "disabled")
        for button in (self.new_conversation, self.clear_conversation, self.sidebar_new, self.history_new):
            button.configure(state="disabled" if self.controller.active else "normal")
        for view in (self.sidebar_list, self.history_list):
            view.set_enabled(not self.controller.active)
        self.history_note.configure(text="当前任务结束后即可切换会话。" if self.controller.active else "选一段对话，接着聊。")
        self.composer_var.set("回复这条追问" if phase == "waiting_user" else (
            "补充信息，继续任务后生效" if paused else ("可以继续补充，我会一起考虑" if active and not desktop else (
                "随时补充 · 发送后调整下一步" if active else "问一个问题，或交给我一件事"))))

    def _on_state(self, event):
        phase = event["phase"]
        previous = self._phase
        self._phase = phase
        if event.get("step"):
            self._step = event["step"]
        self.status_var.set(("●  " if phase not in {"idle", "cancelled", "failed"} else "○  ") + PHASES[phase][0])
        if phase == "completed" and self.controller.mode != "desktop":
            self.status_var.set("●  继续聊聊")
        self.chat_status.set("正在思考，你可以继续补充…" if phase == "thinking" else "")
        urgent = phase not in {"observing", "planning", "executing", "verifying"}
        detail = event.get("detail", "")
        self.lyrics.show(phase, event["title"], detail if phase not in {"waiting_user", "waiting_confirmation"} else "任务已暂停，等待你的回应。", urgent=urgent)
        self.interaction.pack_forget()
        self.confirm_buttons.pack_forget()
        self._request_id = event.get("request_id", "")
        self.edge.set_status(phase, event["title"], self._step)
        if phase in {"waiting_user", "waiting_confirmation"}:
            self.edge.open_workspace(interaction=True)
            self._dismiss_drawers()
            self.interaction.pack(fill="x", side="bottom", after=self.bottom, pady=(4, 8))
            message = detail
            if phase == "waiting_confirmation":
                action = ACTION_NAMES.get(event.get("action"), event.get("action", "操作"))
                message = f"操作：{action}\n对象：{event.get('target', '')}\n\n{detail}"
                self.confirm_buttons.pack(fill="x", pady=(10, 0))
                self.allow.configure(state="normal")
                self.reject.configure(state="normal")
            self.interaction_text.configure(state="normal")
            self.interaction_text.delete("1.0", "end")
            self.interaction_text.insert("1.0", message)
            self.interaction_text.configure(state="disabled")
        elif phase in {"paused", "takeover"} and self.edge.mode != "workspace":
            self.edge.expand()
        if phase in {"completed", "cancelled", "failed"}:
            if self._edge_request == self.controller.task_id:
                if self.controller.mode != "desktop" and self.edge.mode != "workspace":
                    self.edge.open_workspace(interaction=True)
                self._edge_request = None
            if self._started is not None:
                self._elapsed = time.monotonic() - self._started
            self._started = None
            self._record(PHASES[phase][0], detail)
        elif urgent and phase != previous:
            self._record(PHASES[phase][0], detail)
        self._update_controls()
        if not (self._records_open or self._settings_open or self._memory_open or self._history_open):
            self._show_main_content()

    def _poll(self):
        self.controller.pulse()
        if not self.session.working:
            self.session.remember_external()
        for key in self.session.poll_hotkeys():
            if key == 2:
                self.controller.stop()
            elif key == 3:
                self._focus_editor_when_ready = True
                if self.edge.mode == "workspace":
                    self._begin_composing()
                else:
                    self.edge.expand(keyboard=True)
                    self.edge.set_ready(self._begin_composing(), waiting=True)
                self._focus_editor()
            else:
                self._pause_resume()
        for _ in range(80):
            try:
                event = self.controller.events.get_nowait()
            except queue.Empty:
                break
            if event["task_id"] != self.controller.task_id:
                continue
            if event["type"] == "ui":
                call = event["call"]
                try:
                    if not call.expired:
                        if call.operation in {"working", "capture_begin"}:
                            self._dismiss_drawers()
                        if call.operation == "working":
                            self.edge.resume_working(dock=self.dock_on_run)
                        self.session.apply(call.operation)
                except Exception as error:
                    call.error = error
                finally:
                    call.done.set()
            elif event["type"] == "state":
                self._on_state(event)
            elif event["type"] == "mode":
                self.task_var.set(public_text(self._task_text, 80))
                self._record("开始处理", event["message"])
                self._update_controls()
            elif event["type"] == "record":
                self._record(event["title"], event.get("detail", ""))
            elif event["type"] == "receipt":
                self._on_receipt(event)
            elif event["type"] == "conversation":
                self._refresh_conversation()
            elif event["type"] == "edit_ready":
                if self.controller.editing_ready:
                    self.session.apply("interactive")
                    self.edge.set_ready(True)
                    self._update_controls()
                    self._focus_editor()
            elif event["type"] == "edit_wait":
                self.edge.set_ready(False, waiting=True)
            elif event["type"] == "settled":
                self._settling = True
        # Re-enable composer only when the worker has actually left its finally.
        if self._settling and not self.controller.active:
            self._settling = False
            self._update_controls()
        self.edge.poll()
        if self._started is not None:
            elapsed = int(time.monotonic() - self._started)
            prefix = f"第 {self._step} 步  ·  " if self._step else ""
            self.time_var.set(prefix + f"{elapsed // 60:02d}:{elapsed % 60:02d}")
        if self._closing and not self.controller.active:
            self.edge.close()
            self.session.close()
            self.root.destroy()
            return
        self.root.after(40, self._poll)

    def _close(self):
        if self.startup:
            self.startup.cancel()
        if self._layout_job is not None:
            self.root.after_cancel(self._layout_job)
            self._layout_job = None
        if self.controller.active:
            self._closing = True
            self.controller.stop()
            self.hint_var.set("正在停止任务，安全结束后自动关闭窗口。")
        else:
            self.edge.close()
            self.session.close()
            self.root.destroy()

    def run(self):
        self.root.mainloop()
