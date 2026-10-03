"""Small always-on-top chat window for one UFO task at a time."""

from __future__ import annotations

import asyncio
import ctypes
import ctypes.wintypes
import queue
import threading
from datetime import datetime
from pathlib import Path
from tkinter import BOTH, END, LEFT, RIGHT, X, BooleanVar, Canvas, StringVar, Tk, messagebox, ttk
from tkinter.scrolledtext import ScrolledText
from typing import Any, Dict, Optional

from openassist.branding import APP_NAME, apply_window_icon, prepare_process_identity


ACTION_NAMES = {
    "focus_window": "切换窗口",
    "click_element": "点击控件",
    "click": "点击屏幕",
    "type_text": "输入文字",
    "fill_text": "填写输入框",
    "press_keys": "按键",
    "scroll": "滚动",
    "wait": "等待",
}
MODE_SINGLE = "单任务模式"
MODE_UFO_FULL = "UFO 原生多智能体模式"

# Keep the palette restrained so status color carries meaning instead of
# turning the window into a collection of unrelated accents.
INK = "#0b1020"
INK_RAISED = "#121a2b"
INK_PANEL = "#172238"
INK_LINE = "#263550"
TEXT = "#eef4ff"
TEXT_MUTED = "#91a0b8"
CYAN = "#63e6ff"
MINT = "#73e0b2"
AMBER = "#f6c177"
RED = "#ff7c8a"

GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020
WS_EX_LAYERED = 0x00080000
WS_EX_NOACTIVATE = 0x08000000
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020
HWND_TOPMOST = ctypes.wintypes.HWND(-1)


class OpenAssistWindow:
    """Own the UI thread and bridge user interaction to the agent worker."""

    def __init__(self, product_root: Path, ufo_root: Path) -> None:
        self.product_root = product_root
        self.ufo_root = ufo_root
        self.user32 = ctypes.windll.user32
        self._bind_user32()
        self.last_external_window = int(self.user32.GetForegroundWindow() or 0)
        prepare_process_identity()
        self.root = Tk()
        self.root.title(APP_NAME)
        apply_window_icon(self.root)
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        width = min(520, max(380, screen_width - 40))
        height = min(740, max(500, screen_height - 60))
        x = max(0, (screen_width - width) // 2)
        y = max(0, (screen_height - height) // 2)
        self.root.geometry(f"{width}x{height}+{x}+{y}")
        self.root.minsize(min(380, width), min(500, height))
        self.root.attributes("-topmost", True)
        self.root.protocol("WM_DELETE_WINDOW", self._close)

        self.events: queue.Queue[Dict[str, Any]] = queue.Queue()
        self.worker: Optional[threading.Thread] = None
        self.cancel_event = threading.Event()
        self.pending_reply: Optional[queue.Queue[Any]] = None
        self.busy = False
        self.confirming = False
        self.click_through: Optional[bool] = None

        self.status_var = StringVar(value=f"就绪 · {MODE_SINGLE}")
        self.phase_var = StringVar(value="待命")
        self.previous_var = StringVar(value="")
        self.current_var = StringVar(value="准备好开始下一项任务")
        self.step_var = StringVar(value="--")
        self.mode_var = StringVar(value=MODE_SINGLE)
        self.keep_on_top_var = BooleanVar(value=True)
        self._build_ui()
        self._set_click_through(False)
        self.root.after(100, self._drain_events)
        self.root.after(250, self._focus_input)
        self.root.after(200, self._remember_external_window)

    def _bind_user32(self) -> None:
        hwnd = ctypes.wintypes.HWND
        self.user32.GetAncestor.argtypes = [hwnd, ctypes.wintypes.UINT]
        self.user32.GetAncestor.restype = hwnd
        self.user32.GetForegroundWindow.restype = hwnd
        self.user32.IsChild.argtypes = [hwnd, hwnd]
        self.user32.IsChild.restype = ctypes.wintypes.BOOL
        self.user32.IsWindow.argtypes = [hwnd]
        self.user32.IsWindow.restype = ctypes.wintypes.BOOL
        self.user32.IsIconic.argtypes = [hwnd]
        self.user32.IsIconic.restype = ctypes.wintypes.BOOL
        self.user32.ShowWindow.argtypes = [hwnd, ctypes.c_int]
        self.user32.ShowWindow.restype = ctypes.wintypes.BOOL
        self.user32.SetForegroundWindow.argtypes = [hwnd]
        self.user32.SetForegroundWindow.restype = ctypes.wintypes.BOOL
        self.user32.GetWindowLongPtrW.argtypes = [hwnd, ctypes.c_int]
        self.user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
        self.user32.SetWindowLongPtrW.argtypes = [hwnd, ctypes.c_int, ctypes.c_ssize_t]
        self.user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
        self.user32.SetWindowPos.argtypes = [
            hwnd,
            hwnd,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.wintypes.UINT,
        ]
        self.user32.SetWindowPos.restype = ctypes.wintypes.BOOL

    def _build_ui(self) -> None:
        self.root.configure(bg=INK)
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("App.TFrame", background=INK)
        style.configure("Panel.TFrame", background=INK_RAISED)
        style.configure("Header.TLabel", background=INK, foreground=TEXT, font=("Segoe UI", 17, "bold"))
        style.configure("Eyebrow.TLabel", background=INK, foreground=CYAN, font=("Segoe UI", 8, "bold"))
        style.configure("Status.TLabel", background=INK, foreground=TEXT_MUTED, font=("Segoe UI", 9))
        style.configure("PanelLabel.TLabel", background=INK_RAISED, foreground=TEXT_MUTED, font=("Segoe UI", 9))
        style.configure("Previous.TLabel", background=INK_RAISED, foreground="#52627d", font=("Segoe UI", 9))
        style.configure("Current.TLabel", background=INK_RAISED, foreground=TEXT, font=("Segoe UI", 19, "bold"))
        style.configure("Step.TLabel", background=INK_RAISED, foreground=CYAN, font=("Segoe UI", 26, "bold"))
        style.configure("Send.TButton", font=("Segoe UI", 10, "bold"), padding=(16, 10), foreground=INK)
        style.map("Send.TButton", background=[("active", MINT), ("!disabled", CYAN)])
        style.configure("Mode.TLabel", background=INK, foreground=TEXT_MUTED, font=("Segoe UI", 9))
        style.configure("TCheckbutton", background=INK, foreground=TEXT_MUTED)
        style.map("TCheckbutton", background=[("active", INK)], foreground=[("active", TEXT)])
        style.configure("TCombobox", fieldbackground=INK_PANEL, background=INK_PANEL, foreground=TEXT)

        self.header = ttk.Frame(self.root, style="App.TFrame", padding=(20, 18, 20, 10))
        self.header.pack(fill=X)
        brand = ttk.Frame(self.header, style="App.TFrame")
        brand.pack(side=LEFT)
        ttk.Label(brand, text="OPENASSIST / DESKTOP AGENT", style="Eyebrow.TLabel").pack(anchor="w")
        ttk.Label(brand, text=APP_NAME, style="Header.TLabel").pack(anchor="w", pady=(2, 0))
        ttk.Checkbutton(
            self.header,
            text="始终置顶",
            variable=self.keep_on_top_var,
            command=self._toggle_topmost,
        ).pack(side=RIGHT)
        ttk.Button(self.header, text="×", width=3, command=self._close).pack(
            side=RIGHT, padx=(0, 8)
        )

        ttk.Label(self.root, textvariable=self.status_var, style="Status.TLabel", padding=(20, 0, 20, 12)).pack(fill=X)

        # The live panel is intentionally concise: it behaves like a lyric
        # line, replacing the current action as the agent advances.
        self.live_panel = ttk.Frame(self.root, style="Panel.TFrame", padding=(18, 16, 18, 16))
        self.live_panel.pack(fill=X, padx=16, pady=(0, 12))
        live_top = ttk.Frame(self.live_panel, style="Panel.TFrame")
        live_top.pack(fill=X)
        ttk.Label(live_top, textvariable=self.phase_var, style="PanelLabel.TLabel").pack(side=LEFT)
        ttk.Label(live_top, textvariable=self.step_var, style="Step.TLabel").pack(side=RIGHT)
        ttk.Label(
            self.live_panel,
            textvariable=self.previous_var,
            style="Previous.TLabel",
            wraplength=440,
            justify=LEFT,
        ).pack(fill=X, pady=(10, 0))
        ttk.Label(
            self.live_panel,
            textvariable=self.current_var,
            style="Current.TLabel",
            wraplength=440,
            justify=LEFT,
        ).pack(fill=X, pady=(9, 13))
        self.stage_canvas = Canvas(self.live_panel, height=31, bg=INK_RAISED, highlightthickness=0)
        self.stage_canvas.pack(fill=X)
        self.active_stage = "idle"
        self.stage_canvas.bind("<Configure>", lambda _event: self._draw_stages(self.active_stage))
        self._draw_stages("idle")

        self.composer = ttk.Frame(self.root, style="App.TFrame", padding=(16, 0, 16, 10))
        self.composer.pack(fill=X)
        ttk.Label(self.composer, text="下一项任务", style="Mode.TLabel").pack(anchor="w", pady=(0, 6))
        mode_row = ttk.Frame(self.composer, style="App.TFrame")
        mode_row.pack(fill=X, pady=(0, 6))
        ttk.Label(mode_row, text="运行模式", style="Mode.TLabel").pack(side=LEFT)
        self.mode_picker = ttk.Combobox(
            mode_row,
            textvariable=self.mode_var,
            values=(MODE_SINGLE, MODE_UFO_FULL),
            state="readonly",
            width=25,
        )
        self.mode_picker.pack(side=LEFT, padx=(8, 0))
        compose_row = ttk.Frame(self.composer, style="App.TFrame")
        compose_row.pack(fill=X)
        self.request_box = ScrolledText(
            compose_row,
            height=3,
            wrap="word",
            font=("Segoe UI", 10),
            padx=9,
            pady=8,
            relief="solid",
            borderwidth=2,
            bg=INK_PANEL,
            fg=TEXT,
            insertbackground=CYAN,
        )
        self.request_box.pack(side=LEFT, fill=X, expand=True)
        self.request_box.bind("<Control-Return>", self._submit_event)
        self.send_button = ttk.Button(compose_row, text="发送", style="Send.TButton", command=self._submit)
        self.send_button.pack(side=RIGHT, padx=(8, 0))

        self.transcript = ScrolledText(
            self.root,
            wrap="word",
            state="disabled",
            font=("Segoe UI", 10),
            padx=14,
            pady=12,
            relief="flat",
            bg=INK_RAISED,
            fg=TEXT,
            insertbackground=CYAN,
            spacing3=7,
        )
        self.transcript.pack(fill=BOTH, expand=True, padx=16, pady=(0, 14))
        self.transcript.tag_configure("user", foreground=CYAN, lmargin1=34)
        self.transcript.tag_configure("agent", foreground=TEXT, rmargin=30)
        self.transcript.tag_configure("system", foreground=TEXT_MUTED, font=("Segoe UI", 9, "italic"))
        self.transcript.tag_configure("title", foreground=TEXT_MUTED, font=("Segoe UI", 9, "bold"))
        self._append(
            APP_NAME,
            "发一条任务给 UFO。当前每次任务独立运行，结束后可以再开始下一条。",
            "system",
        )

    def _draw_stages(self, active: str) -> None:
        """Render a quiet progress rail without implying unverified progress."""
        if not hasattr(self, "stage_canvas"):
            return
        self.active_stage = active
        self.stage_canvas.delete("all")
        self.stage_canvas.update_idletasks()
        width = max(260, self.stage_canvas.winfo_width())
        stages = [("observe", "观察"), ("plan", "规划"), ("act", "执行"), ("verify", "验证"), ("done", "完成")]
        active_index = next((index for index, (key, _) in enumerate(stages) if key == active), -1)
        for index, (_, label) in enumerate(stages):
            x = 18 + index * (width - 36) / (len(stages) - 1)
            if index < len(stages) - 1:
                line_color = CYAN if index < active_index else INK_LINE
                next_x = 18 + (index + 1) * (width - 36) / (len(stages) - 1)
                self.stage_canvas.create_line(x, 9, next_x, 9, fill=line_color, width=2)
            color = CYAN if index == active_index else (MINT if index < active_index else TEXT_MUTED)
            radius = 5 if index == active_index else 3
            self.stage_canvas.create_oval(x - radius, 9 - radius, x + radius, 9 + radius, fill=color, outline="")
            self.stage_canvas.create_text(x, 17, text=label, fill=color, anchor="n", font=("Segoe UI", 8))

    def _set_live(self, phase: str, current: str, stage: str = "idle", step: Optional[Any] = None) -> None:
        old_current = self.current_var.get()
        if old_current and old_current != current and old_current != "准备好开始下一项任务":
            self.previous_var.set(f"刚刚 · {old_current.splitlines()[0]}")
        self.phase_var.set(phase)
        self.current_var.set(current)
        if step is not None:
            try:
                self.step_var.set(f"{int(step):02d}")
            except (TypeError, ValueError):
                self.step_var.set(str(step))
        self._draw_stages(stage)

    def run(self) -> None:
        self.root.mainloop()

    def _focus_input(self) -> None:
        if not self.root.winfo_exists():
            return
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()
        if not self.busy:
            self.request_box.focus_force()

    def _window_handle(self) -> int:
        child = self.root.winfo_id()
        return int(self.user32.GetAncestor(child, 2) or child)

    def _remember_external_window(self) -> None:
        if not self.root.winfo_exists():
            return
        own_window = self._window_handle()
        foreground = int(self.user32.GetForegroundWindow() or 0)
        foreground_owner = int(self.user32.GetAncestor(foreground, 3) or foreground)
        if (
            foreground
            and foreground_owner != own_window
            and not self.user32.IsChild(own_window, foreground)
        ):
            self.last_external_window = foreground
        self.root.after(200, self._remember_external_window)

    def _restore_external_window(self) -> None:
        window = self.last_external_window
        if window and self.user32.IsWindow(window):
            if self.user32.IsIconic(window):
                self.user32.ShowWindow(window, 9)  # SW_RESTORE
            self.user32.SetForegroundWindow(window)

    def _set_click_through(self, enabled: bool) -> None:
        """Let desktop input pass through the HUD while the agent is working."""
        if self.click_through is enabled:
            return
        self.root.update_idletasks()
        window = self._window_handle()
        style = int(self.user32.GetWindowLongPtrW(window, GWL_EXSTYLE))
        style |= WS_EX_LAYERED
        if enabled:
            style |= WS_EX_TRANSPARENT | WS_EX_NOACTIVATE
        else:
            style &= ~(WS_EX_TRANSPARENT | WS_EX_NOACTIVATE)
        self.user32.SetWindowLongPtrW(window, GWL_EXSTYLE, style)
        self.user32.SetWindowPos(
            window,
            HWND_TOPMOST,
            0,
            0,
            0,
            0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_FRAMECHANGED,
        )
        # Input pass-through is an interaction boundary, not a visual state.
        # Keep the window opaque and geometrically stable while the agent runs;
        # lowering alpha made the product feel like a temporary overlay.
        self.root.attributes("-alpha", 1.0)
        self.click_through = enabled

    def _toggle_topmost(self) -> None:
        self.root.attributes("-topmost", self.keep_on_top_var.get())

    def _append(self, speaker: str, message: str, tag: str) -> None:
        self.transcript.configure(state="normal")
        self.transcript.insert(END, f"{speaker}\n", ("title", tag))
        self.transcript.insert(END, f"{message}\n\n", tag)
        self.transcript.configure(state="disabled")
        self.transcript.see(END)

    def _submit_event(self, _event: Any) -> str:
        self._submit()
        return "break"

    def _submit(self) -> None:
        text = self.request_box.get("1.0", "end-1c").strip()
        if not text:
            return
        if self.pending_reply is not None:
            self._append("你", text, "user")
            reply = self.pending_reply
            self.pending_reply = None
            self.request_box.delete("1.0", END)
            self._set_busy(True, "Agent 正在继续…")
            # Restore desktop focus before allowing the worker to observe/type.
            reply.put(text)
            return
        if self.busy:
            return

        self.request_box.delete("1.0", END)
        self._append("你", text, "user")
        self.cancel_event = threading.Event()
        mode = self.mode_var.get()
        self._set_busy(True, f"正在启动 {mode}…")
        self.worker = threading.Thread(
            target=self._run_task,
            args=(text, mode),
            name="openassist-agent",
            daemon=True,
        )
        self.worker.start()

    def _run_task(self, request: str, mode: str) -> None:
        try:
            task = "openassist-" + datetime.now().strftime("%Y%m%d-%H%M%S")
            if mode == MODE_UFO_FULL:
                from openassist.ufo_full import run_native_request

                asyncio.run(
                    run_native_request(
                        request=request,
                        task=task,
                        event_callback=self.events.put,
                        input_callback=self._request_input,
                        confirmation_callback=self._confirm_action,
                        cancel_event=self.cancel_event,
                    )
                )
            else:
                from ufo.agents.single_agent.runner import SingleAgentRunner

                runner = SingleAgentRunner(
                    request=request,
                    task=task,
                    keep_console=True,
                    event_callback=self.events.put,
                    input_callback=self._request_input,
                    confirmation_callback=self._confirm_action,
                    cancel_event=self.cancel_event,
                )
                asyncio.run(runner.run())
        except Exception as exc:
            self.events.put({"type": "failed", "message": str(exc)})

    def _request_input(self, question: str) -> str:
        reply: queue.Queue[Any] = queue.Queue(maxsize=1)
        self.events.put({"type": "ask_user", "question": question, "reply": reply})
        while not self.cancel_event.is_set():
            try:
                return str(reply.get(timeout=0.2))
            except queue.Empty:
                continue
        return ""

    def _confirm_action(self, action: str, target: str) -> bool:
        reply: queue.Queue[Any] = queue.Queue(maxsize=1)
        self.events.put(
            {"type": "confirm_action", "action": action, "target": target, "reply": reply}
        )
        while not self.cancel_event.is_set():
            try:
                return bool(reply.get(timeout=0.2))
            except queue.Empty:
                continue
        return False

    def _drain_events(self) -> None:
        try:
            while True:
                event = self.events.get_nowait()
                self._handle_event(event)
        except queue.Empty:
            pass
        self.root.after(100, self._drain_events)

    def _handle_event(self, event: Dict[str, Any]) -> None:
        kind = event.get("type")
        if kind == "step":
            step = event.get("step", "?")
            summary = str(event.get("observation") or "正在处理当前界面。")
            actions = event.get("actions") or []
            action_text = "、".join(ACTION_NAMES.get(item.get("type"), item.get("type", "操作")) for item in actions)
            detail = f"第 {step} 步：{summary}"
            if action_text:
                detail += f"\n准备执行：{action_text}"
            self._append("UFO", detail, "agent")
            self.status_var.set(f"正在执行 · 第 {step} 步")
            self._set_live("执行中", summary, "act", step)
        elif kind == "action_result":
            label = ACTION_NAMES.get(event.get("action"), event.get("action", "操作"))
            detail = event.get("detail") or event.get("error") or "完成"
            self.status_var.set(f"{label} · {detail}")
            self._set_live("动作反馈", f"{label} · {detail}", "verify")
        elif kind == "progress":
            self._append("UFO", str(event.get("message", "正在运行。")), "agent")
            self.status_var.set(f"正在执行 · {MODE_UFO_FULL}")
            self._set_live("同步中", str(event.get("message", "正在运行。")), "act")
        elif kind == "native_finished":
            result_status = event.get("status", "done")
            title = "任务已停止" if result_status == "cancelled" else (
                "任务失败" if result_status == "failed" else "任务完成"
            )
            self._append(title, str(event.get("message", "任务结束。")), "agent")
            status = "已停止" if result_status == "cancelled" else (
                "执行失败" if result_status == "failed" else "就绪"
            )
            self._set_live(
                "已停止" if result_status == "cancelled" else ("需要处理" if result_status == "failed" else "已完成"),
                str(event.get("message", "任务结束。")),
                "done" if result_status == "done" else "idle",
            )
            self._set_busy(False, f"{status} · {MODE_UFO_FULL}")
        elif kind == "ask_user":
            self._append("UFO 需要补充信息", str(event.get("question", "请提供更多信息。")), "agent")
            self.pending_reply = event["reply"]
            self._set_busy(False, "等待你的回复")
            self.mode_picker.configure(state="disabled")
            self.send_button.configure(state="normal", text="回复")
            self.request_box.configure(state="normal")
            self.request_box.focus_force()
            self._set_live("需要你", str(event.get("question", "请提供更多信息。")), "idle")
        elif kind == "confirm_action":
            action = ACTION_NAMES.get(event.get("action"), event.get("action", "操作"))
            self._append("安全确认", f"Agent 请求执行：{action}\n目标：{event.get('target', '桌面')}", "system")
            self.confirming = True
            self._set_busy(False, "等待敏感操作确认")
            self._set_click_through(False)
            self._set_live("等待确认", f"{action} · {event.get('target', '桌面')}", "idle")
            approved = messagebox.askyesno(
                "确认敏感操作",
                f"允许 Agent 执行“{action}”吗？\n目标：{event.get('target', '桌面')}",
                parent=self.root,
            )
            self.confirming = False
            self._set_busy(True, "Agent 正在继续…")
            event["reply"].put(approved)
        elif kind == "finished":
            status = event.get("status", "done")
            message = str(event.get("message", "任务结束。"))
            self._append("任务完成" if status == "done" else "任务结束", message, "agent")
            if event.get("log"):
                self._append("运行记录", str(event["log"]), "system")
            self.pending_reply = None
            self._set_live("已完成", message, "done")
            self._set_busy(False, f"就绪 · {self.mode_var.get()}")
        elif kind == "failed":
            self._append("启动失败", str(event.get("message", "未知错误")), "system")
            self.pending_reply = None
            self._set_live("发生错误", str(event.get("message", "未知错误")), "idle")
            self._set_busy(False, "启动失败")

    def _set_busy(self, busy: bool, status: str) -> None:
        self.busy = busy
        self.status_var.set(status)
        self.send_button.configure(state="disabled" if busy else "normal", text="发送")
        self.request_box.configure(state="disabled" if busy else "normal")
        self.mode_picker.configure(state="disabled" if busy else "readonly")
        if busy:
            self._set_click_through(True)
            self._restore_external_window()
        else:
            self._set_click_through(False)

    def _close(self) -> None:
        if self.busy and not messagebox.askyesno(
            "正在运行",
            "任务仍在运行。停止等待中的任务并关闭窗口？",
            parent=self.root,
        ):
            return
        self.cancel_event.set()
        if self.pending_reply is not None:
            try:
                self.pending_reply.put_nowait("")
            except queue.Full:
                pass
        self.root.destroy()
