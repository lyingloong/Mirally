"""UI-independent task lifecycle and acknowledged desktop interaction barriers."""

from __future__ import annotations

import asyncio
import logging
import queue
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from openassist.core.state import PHASE_NAMES
from openassist.core.text import message_text, public_text
from openassist.core.conversation import ConversationStore
from openassist.core.assistant import Assistant, DemoAssistant
from openassist.core.execution import (
    ExecutionCancelled, ExecutionCapabilities, ExecutionRequest, ExecutionServices,
    Reobserve, Supplement, TaskResult, TERMINAL_STATUSES,
)


class TaskStopped(ExecutionCancelled):
    pass


@dataclass
class UiCall:
    operation: str
    done: threading.Event = field(default_factory=threading.Event)
    error: Optional[Exception] = None
    expired: bool = False


class TaskController:
    """Only the worker acknowledges pause; stop always wins over pending input.

    The GUI must call pulse and service UiCall objects on its own thread.
    A timed-out GUI can never authorize another desktop action.
    """

    def __init__(self, *, demo: bool = False, executor=None, runner_factory=None, assistant=None, ui_timeout=4.0, input_timeout=300.0, conversation=None):
        self.events: queue.Queue = queue.Queue()
        self.cancel_event = threading.Event()
        self.worker: Optional[threading.Thread] = None
        self.demo = demo
        if executor is not None and runner_factory is not None:
            raise ValueError("Pass executor or legacy runner_factory, not both")
        if executor is None and runner_factory is not None:
            from openassist.core.legacy_execution import LegacyRunnerExecutor
            executor = LegacyRunnerExecutor(runner_factory, lambda: self.cancel_event)
        if executor is None and demo:
            from openassist.core.demo_execution import DemoExecutor
            executor = DemoExecutor()
        self.executor = executor
        self.assistant = assistant or (DemoAssistant() if demo else Assistant())
        self.mode = "conversation"
        self.desktop_available = True
        self.ui_timeout = ui_timeout
        self.input_timeout = input_timeout
        self.conversation = conversation or ConversationStore()
        self._task_context = {}
        self._condition = threading.Condition(threading.RLock())
        self._last_pulse = time.monotonic()
        self._pause = False
        self._takeover = False
        self._editing = False
        self._desktop_busy = False
        self._revision = 0
        self._sequence = 0
        self.task_id = ""
        self.phase = "idle"
        self._pending: Optional[queue.Queue] = None
        self._pending_id = ""
        self._pending_kind = ""
        self._pending_replied = False
        self._supplements: list[tuple[str, str]] = []
        self._receipts: dict[str, dict] = {}
        self._receipt_inputs: dict[str, str] = {}
        self._awaiting_context: dict[str, list[str]] = {"supplement": [], "answer": []}

    @property
    def capabilities(self):
        return self.executor.capabilities if self.executor is not None else ExecutionCapabilities()

    def revision(self) -> int:
        with self._condition:
            return self._revision

    def answer_receipt_ids(self) -> tuple[str, ...]:
        with self._condition:
            return tuple(self._awaiting_context["answer"])

    def execution_services(self) -> ExecutionServices:
        task_id = self.task_id

        def publish(event):
            self.on_agent_event({"task_id": task_id, **event})

        return ExecutionServices(
            event=publish, ask=self.ask_user, confirm=self.confirm,
            checkpoint=self.checkpoint, capture=self.capture,
            take_supplements=self.take_supplements, desktop_access=self.desktop_access,
            cancelled=self.cancel_event.is_set, revision=self.revision,
            answer_receipt_ids=self.answer_receipt_ids,
        )

    @property
    def active(self):
        return self.worker is not None and self.worker.is_alive()

    def pulse(self):
        self._last_pulse = time.monotonic()

    def _alive(self):
        if self.cancel_event.is_set():
            raise TaskStopped()
        if time.monotonic() - self._last_pulse > self.ui_timeout:
            self.cancel_event.set()
            raise RuntimeError("前端响应超时，任务已安全中止。")

    def _emit(self, kind, **values):
        with self._condition:
            self._sequence += 1
            self.events.put({"type": kind, "task_id": self.task_id, "sequence": self._sequence, **values})

    def _state(self, phase, title=None, detail="", **values):
        with self._condition:
            if phase not in PHASE_NAMES:
                raise ValueError(f"Unknown task phase: {phase}")
            if self.cancel_event.is_set() and phase not in {"stopping", "cancelled", "failed"}:
                return
            self.phase = phase
            self._emit("state", phase=phase, title=message_text(title, 120) or None, detail=public_text(detail), **values)

    def start(self, request: str):
        with self._condition:
            if self.active:
                raise RuntimeError("当前任务尚未结束。")
            if not request.strip():
                return False
            self.cancel_event = threading.Event()
            self._pause = self._takeover = False
            self._editing = self._desktop_busy = False
            self._revision = 0
            self._pending = None
            self._pending_id = self._pending_kind = ""
            self._pending_replied = False
            self._supplements.clear()
            self._receipts.clear()
            self._receipt_inputs.clear()
            for pending in self._awaiting_context.values():
                pending.clear()
            self.task_id = "openassist-" + uuid.uuid4().hex[:12]
            self._task_context = self.conversation.context()
            self.mode = "conversation"
            self.conversation.begin(self.task_id, request, kind="conversation")
            self._emit("conversation")
            self.pulse()
            self._state("thinking")
            self.worker = threading.Thread(target=self._run, args=(request.strip(),), daemon=True, name=self.task_id)
            self.worker.start()
            return True

    def ui(self, operation: str, *, cleanup=False):
        if not cleanup:
            self._alive()
        call = UiCall(operation)
        self._emit("ui", call=call)
        deadline = time.monotonic() + self.ui_timeout
        while not call.done.wait(0.05):
            if time.monotonic() >= deadline:
                call.expired = True
                self.cancel_event.set()
                raise RuntimeError("界面隔离未能及时确认。")
            if not cleanup:
                try:
                    self._alive()
                except BaseException:
                    call.expired = True
                    raise
        if call.error:
            self.cancel_event.set()
            raise RuntimeError("无法确认桌面隔离状态。") from call.error

    def checkpoint(self) -> int:
        self._alive()
        with self._condition:
            while self._editing:
                self._condition.wait(0.05)
                self._alive()
            pause = self._pause
        if pause:
            self.ui("interactive")
            with self._condition:
                self._state("takeover" if self._takeover else "paused", detail="继续后会重新观察桌面，再决定下一步。")
                while self._pause:
                    self._condition.wait(0.05)
                    self._alive()
            self.ui("working")
            self._state("observing")
        return self._revision

    @property
    def editing_ready(self):
        with self._condition:
            return self._editing and not self._desktop_busy

    def begin_edit(self):
        """Let the user compose while a model call continues in the background.

        Desktop operations use an atomic gate. If a click/capture is already
        underway, the UI waits for that operation's completion before focusing.
        """
        with self._condition:
            if not self.active or self.phase in {"stopping", "pausing"} or self.cancel_event.is_set():
                return False
            if self.mode != "desktop":
                return True
            if not self.capabilities.supplement:
                return False
            if self.phase in {"paused", "takeover", "waiting_user", "waiting_confirmation"}:
                return True
            if not self._editing:
                self._editing = True
                self._revision += 1
                self._emit("edit_ready" if not self._desktop_busy else "edit_wait")
            return not self._desktop_busy

    def end_edit(self):
        # GUI restores click-through and external focus BEFORE releasing this.
        with self._condition:
            self._editing = False
            self._condition.notify_all()

    def desktop_access(self, stage, expected_revision=None):
        if stage == "after":
            with self._condition:
                self._desktop_busy = False
                if self._editing:
                    self._emit("edit_ready")
                self._condition.notify_all()
                return self._revision
        while True:
            self.checkpoint()
            with self._condition:
                self._alive()
                if self._editing or self._pause:
                    continue
                if expected_revision is not None and expected_revision != self._revision:
                    raise Reobserve()
                if self._desktop_busy:
                    self._condition.wait(0.05)
                    continue
                self._desktop_busy = True
                return self._revision

    def pause(self, *, takeover=False):
        with self._condition:
            if not self.active or self.cancel_event.is_set() or self.mode != "desktop":
                return
            if not self.capabilities.pause:
                return False
            already_paused = self.phase in {"paused", "takeover"}
            self._takeover = takeover
            self._editing = False
            self._pause = True
            self._revision += 1
            if self._pending is not None:
                self._pending_replied = True
                # Taking over invalidates the pending interaction; it can never
                # approve an action after the user has changed the desktop.
                try:
                    self._pending.put_nowait(False if self._pending_kind == "confirmation" else "")
                except queue.Full:
                    # The revision check invalidates even a reply already read
                    # by the worker. Do not race its queue.get by draining here.
                    pass
            self._state(("takeover" if takeover else "paused") if already_paused else "pausing")
            self._condition.notify_all()

    def resume(self):
        with self._condition:
            if self.phase not in {"paused", "takeover"} or self.cancel_event.is_set():
                return False
            self._pause = self._takeover = False
            self._revision += 1
            self._state("preparing", "正在重新连接\n你的工作空间")
            self._condition.notify_all()
            return True

    def stop(self):
        with self._condition:
            if not self.active or self.phase in TERMINAL_STATUSES:
                return
            self.cancel_event.set()
            self._pause = False
            self._editing = False
            self._state("stopping", detail="停止请求已发出；当前外部调用返回后结束。")
            self._condition.notify_all()

    def reply(self, request_id: str, value: Any) -> bool:
        with self._condition:
            if self.cancel_event.is_set() or self._pending is None or self._pending_replied or request_id != self._pending_id:
                return False
            if self._pending_kind == "confirmation" and not isinstance(value, bool):
                return False
            if self._pending_kind == "question" and (not isinstance(value, str) or not value.strip()):
                return False
            try:
                self._pending.put_nowait(value)
            except queue.Full:
                return False
            self._pending_replied = True
            if self._pending_kind == "question":
                receipt_id = self._receive("answer", value)
                self._awaiting_context["answer"].append(receipt_id)
            return True

    def supplement(self, text: str):
        with self._condition:
            if self.cancel_event.is_set() or self.phase in TERMINAL_STATUSES | {"idle", "stopping"} or not text.strip():
                return False
            if self.mode == "desktop" and not self.capabilities.supplement:
                return False
            receipt_id = self._receive("supplement", text)
            self._supplements.append((receipt_id, text.strip()))
            self._revision += 1
            if self._pending is not None and self._pending_kind == "confirmation":
                self._pending_replied = True
                try:
                    self._pending.put_nowait(False)
                except queue.Full:
                    pass  # Revision change also invalidates a queued approval.
            return True

    def _receive(self, source, text):
        """Called under the condition lock so queued precedes any consumption."""
        receipt_id = uuid.uuid4().hex
        receipt = {"receipt_id": receipt_id, "source": source, "status": "queued", "detail": public_text(text, 800),
                   "delivery": "resume" if self.phase in {"paused", "takeover"} else "next_step"}
        self._receipts[receipt_id] = receipt
        self._receipt_inputs[receipt_id] = text
        self._emit("receipt", **receipt)
        return receipt_id

    def take_supplements(self):
        with self._condition:
            result, self._supplements = self._supplements, []
            self._awaiting_context["supplement"].extend(receipt_id for receipt_id, _ in result)
            return [Supplement(receipt_id, text) for receipt_id, text in result]

    def _context_applied(self, source, receipt_ids=None):
        # This acknowledgment comes from the runner AFTER user_answers changes,
        # not when the UI accepts input or when the worker drains its queue.
        with self._condition:
            if source not in self._awaiting_context:
                return
            available = self._awaiting_context[source]
            pending = list(available) if receipt_ids is None else [item for item in receipt_ids if item in available]
            self._awaiting_context[source] = [item for item in available if item not in pending]
            for receipt_id in pending:
                receipt = self._receipts[receipt_id]
                if receipt["status"] == "queued":
                    receipt["status"] = "applied"
                    self.conversation.note(self.task_id, self._receipt_inputs.pop(receipt_id, receipt["detail"]))
                    self._emit("receipt", **receipt)

    def _finish_receipts(self):
        with self._condition:
            for receipt in self._receipts.values():
                if receipt["status"] == "queued":
                    receipt["status"] = "unused"
                    self._emit("receipt", **receipt)
            self._supplements.clear()
            self._receipt_inputs.clear()
            for pending in self._awaiting_context.values():
                pending.clear()

    def _input(self, kind: str, **fields):
        self._alive()
        self.ui("interactive")
        reply: queue.Queue = queue.Queue(maxsize=1)
        with self._condition:
            self._pending = reply
            request_revision = self._revision
            self._pending_id = uuid.uuid4().hex
            self._pending_kind = kind
            self._pending_replied = False
            self._state("waiting_confirmation" if kind == "confirmation" else "waiting_user",
                        request_id=self._pending_id, **fields)
        deadline = time.monotonic() + self.input_timeout
        try:
            while True:
                self._alive()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    with self._condition:
                        # Atomically close input or consume a reply already
                        # accepted at the timeout boundary. Never acknowledge
                        # a queued answer that is replaced with an empty one.
                        try:
                            value = reply.get_nowait()
                        except queue.Empty:
                            self._pending = None
                            self._emit("record", title="等待已超时", detail="没有自动允许操作，任务进入暂停。")
                            self.pause()
                            value = False if kind == "confirmation" else ""
                    break
                try:
                    value = reply.get(timeout=min(0.05, remaining))
                    break
                except queue.Empty:
                    pass
        finally:
            with self._condition:
                self._pending = None
                self._pending_id = self._pending_kind = ""
        self._alive()
        with self._condition:
            if kind == "confirmation" and self._revision != request_revision:
                value = False
            if kind == "question":
                self._revision += 1
            response_revision = self._revision
        resumed_revision = self.checkpoint()
        if kind == "confirmation" and resumed_revision != response_revision:
            value = False
        self.ui("working")
        self._state("observing")
        return value

    def ask_user(self, question: str):
        return str(self._input("question", detail=public_text(question, 600)))

    def confirm(self, action: str, target: str):
        return self._input("confirmation", action=public_text(action, 120), target=public_text(target, 400),
                           detail="此操作可能改变目标应用中的内容。仅允许当前这一次操作。") is True

    def capture(self, stage: str):
        self.ui("capture_begin" if stage == "before" else "capture_end", cleanup=stage == "after")

    def on_agent_event(self, event):
        with self._condition:
            if event.get("task_id", self.task_id) != self.task_id:
                return
            if self.phase in TERMINAL_STATUSES | {"idle"}:
                return
            self._accept_agent_event(event)

    def _accept_agent_event(self, event):
        kind = event.get("type")
        if kind == "context_applied":
            self._context_applied(event.get("source"), event.get("receipt_ids"))
        elif kind == "phase":
            phase = event.get("phase")
            if phase in {"preparing", "observing", "planning", "waiting_model", "executing", "verifying"}:
                title = public_text(event.get("summary"), 28) or None
                self._state(phase, title=title, action=public_text(event.get("action"), 80),
                            detail=event.get("application", ""), step=event.get("step"))
        elif kind == "waiting":
            stage = event.get("stage")
            with self._condition:
                if self.phase in TERMINAL_STATUSES | {"idle"}:
                    return
                if stage == "cleanup" or event.get("timed_out"):
                    phase, detail = "stopping", "正在等待已发出的调用结束并恢复运行状态，暂不能开始新任务。"
                else:
                    phase = {"model": "waiting_model", "tool": "executing", "initialization": "preparing"}.get(stage, self.phase)
                    detail = "当前调用尚未返回，收到停止请求后将不再启动后续操作。"
                if self.phase in {"pausing", "paused", "takeover", "waiting_user", "waiting_confirmation", "stopping"}:
                    phase = self.phase
                self._state(phase, detail=detail)
        elif kind == "retry":
            self._emit("record", title="正在重试模型请求", detail="上次调用未取得有效结果。")
        elif kind == "action_result":
            # No field contents, raw model observation/thought or exception text.
            self._emit("action_result", action=public_text(event.get("action"), 80), ok=bool(event.get("ok")))
        elif kind == "finished":
            # Worker shutdown, not a callback, authoritatively finishes the task.
            return

    def _run(self, request):
        phase, detail = "failed", "暂时没有收到有效回复，请检查模型连接后重试。"
        result = None
        desktop_started = False
        try:
            additions = []
            while True:
                self._alive()
                with self._condition:
                    revision = self._revision
                    additions.extend(item.text for item in self.take_supplements())
                    self._context_applied("supplement")
                self._state("thinking", detail="正在等待模型回复。停止请求会阻止后续执行，当前请求需先返回。")
                decision = self.assistant.respond(request, self._task_context, additions)
                with self._condition:
                    self._alive()
                    if revision != self._revision:
                        continue  # New input supersedes an outstanding reply or handoff.
                    if decision.mode == "desktop":
                        self.mode = "desktop"
                        self.conversation.set_kind(self.task_id, "desktop")
                        self._emit("mode", mode="desktop", message=public_text(decision.message, 300))
                    break
            if decision.mode == "reply":
                result = TaskResult("completed", decision.message)
            elif not self.desktop_available and not self.demo:
                result = TaskResult("failed", "桌面操作暂时无法启动：全局暂停/停止快捷键被占用。请关闭冲突程序后重启；仍然可以在这里正常对话。", reason="desktop_unavailable")
            else:
                desktop_started = True
                self._state("preparing")
                self.ui("working")
            if desktop_started:
                if self.executor is None:
                    raise RuntimeError("No task executor was configured by the application")
                execution_request = ExecutionRequest(
                    task_id=self.task_id, text=request,
                    conversation_context=self._task_context, initial_inputs=tuple(additions),
                )
                result = asyncio.run(self.executor.run(execution_request, self.execution_services()))
                if not isinstance(result, TaskResult):
                    raise TypeError("Executor must return TaskResult")
            if self.cancel_event.is_set() or result.status == "cancelled":
                phase, detail = "cancelled", "已停止发出新的桌面操作。"
            else:
                phase = result.status
                detail = public_text(result.message, 600) or "执行未完成。可以调整要求后重试。"
        except asyncio.CancelledError:
            # asyncio task/future boundaries may normalize our subclass to the
            # base CancelledError. It remains outside ordinary Exception handlers.
            phase, detail = "cancelled", "已停止发出新的桌面操作。"
        except Exception:
            # Keep the public logger name stable for integrations and tests.
            logging.getLogger("openassist.task_controller").exception("OpenAssist task failed")
        finally:
            try:
                if desktop_started:
                    self.ui("interactive", cleanup=True)
            except Exception:
                phase, detail = "failed", "界面连接异常，任务已停止。请重新启动应用。"
            with self._condition:
                if self.cancel_event.is_set() and phase != "failed":
                    phase, detail = "cancelled", "已停止本次回复。" if self.mode != "desktop" else "已停止发出新的桌面操作。"
                self._finish_receipts()
                message = result.message if isinstance(result, TaskResult) and result.status == phase else detail
                execution_result = result if isinstance(result, TaskResult) and result.status == phase else None
                self.conversation.finish(self.task_id, phase, message or detail, execution_result=execution_result)
                if execution_result:
                    for artifact in execution_result.artifacts:
                        if isinstance(artifact, dict) and artifact.get("path"):
                            self._emit("record", title="产物位置（仅验证文件存在）",
                                       detail=public_text(artifact["path"], 4000))
                self._emit("conversation")
                self._state(phase, title="我们继续聊。" if phase == "completed" and self.mode != "desktop" else None, detail=detail)
                self._emit("settled")
