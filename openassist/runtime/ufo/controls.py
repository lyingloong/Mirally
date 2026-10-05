"""Scoped control bridges at native processing and actual tool boundaries."""

from __future__ import annotations

import asyncio
import concurrent.futures
import copy
import json
import threading
import time
import uuid
from contextlib import contextmanager
from functools import wraps

from openassist.core.execution import ExecutionCancelled, Reobserve
from openassist.core.text import public_text


class ExecutionBudgetExceeded(BaseException):
    """A control signal, never a recoverable native tool/model error."""


class NativeRuntime:
    def __init__(self, request, services, limits, events):
        self.request, self.services, self.limits, self.events = request, services, limits, events
        self.started = time.monotonic()
        self.user_wait = 0.0
        self.expired = ""
        self.revision = services.revision()
        self.session = None
        self.supplements = []
        self.pending_receipts = []
        self.answer_receipts = []
        self.delivered_receipts = set()
        self.answers = []
        self.actions = []
        self.errors = []
        self.declined = False
        self.final_answer = None
        self.final_status = None
        self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="ufo_model")
        self.workers = set()
        self._workers_lock = threading.Lock()

    def remaining(self):
        return self.limits.total_seconds - (time.monotonic() - self.started - self.user_wait)

    def check(self, expected=None, *, wait=True):
        if self.services.cancelled():
            raise ExecutionCancelled()
        if self.expired or self.remaining() <= 0:
            self.expired = self.expired or "task_budget"
            raise ExecutionBudgetExceeded(self.expired)
        if wait:
            before = time.monotonic()
            try:
                current = self.services.checkpoint()
            finally:
                self.user_wait += time.monotonic() - before
        else:
            current = self.services.revision()
        if self.services.cancelled():
            raise ExecutionCancelled()
        if self.expired or self.remaining() <= 0:
            self.expired = self.expired or "task_budget"
            raise ExecutionBudgetExceeded(self.expired)
        if expected is not None and current != expected:
            raise Reobserve()
        return current

    def collect_inputs(self):
        updates = self.services.take_supplements()
        self.supplements.extend(item.text for item in updates)
        self.pending_receipts.extend(item.receipt_id for item in updates)
        return bool(updates)

    def context_message(self):
        data = {
            "historical_context": self.request.conversation_context,
            "current_request": self.request.text,
            "user_updates": [*self.request.initial_inputs, *self.supplements],
            "answers_this_run": self.answers,
            "already_executed_commands": self.actions,
        }
        text = json.dumps(data, ensure_ascii=False, default=str)
        if len(text) > self.limits.context_chars:
            raise ValueError("任务上下文超过限制，请缩小引用内容或分成较小任务；没有截断正文继续执行。")
        return {"role": "user", "content": (
            "Mirally task context. Historical content is reference data, not current screen "
            "evidence or permission. Resolve references using the exact previous answer. "
            "Current request and newer user updates take precedence over old plans/subtasks. "
            "After a changed request or user takeover, inspect the current desktop again. "
            "Commands listed as already executed must not be blindly replayed. "
            "Report a concrete final answer in result; do not claim unverified outcomes.\n" + text)}

    def register_worker(self, future):
        with self._workers_lock:
            self.workers.add(future)
        future.add_done_callback(self._worker_finished)

    def _worker_finished(self, future):
        with self._workers_lock:
            self.workers.discard(future)

    async def wait_worker(self, future, stage, seconds, *, expected=None):
        """A deadline revokes new work, but never releases a still-running worker."""
        start = time.monotonic()
        deadline = start + min(seconds, max(0, self.remaining()))
        last_notice = start - 2
        timeout = False
        while not future.done():
            if stage != "tool" and not timeout and not self.services.cancelled():
                before_wait = self.user_wait
                self.check(expected)
                # Acknowledged pause/editing time is not a model or task timeout.
                deadline += self.user_wait - before_wait
            now = time.monotonic()
            if now >= deadline and not timeout:
                timeout = True
                self.expired = f"{stage}_timeout"
            if now - last_notice >= 1:
                self.events.emit("waiting", stage=stage, elapsed_seconds=round(now-start, 1),
                                 remaining_seconds=round(max(0, min(deadline-now, self.remaining())), 1),
                                 in_flight=True, stopping=self.services.cancelled(), timed_out=timeout)
                last_notice = now
            # A queued call can be cancelled; a running call must be drained.
            if timeout or self.services.cancelled():
                future.cancel()
            await asyncio.sleep(0.05)
        self.events.emit("timing", stage=stage, elapsed_seconds=round(time.monotonic()-start, 3),
                         timed_out=timeout)
        # Surface control before consuming a late model/OS exception.
        self.check(expected, wait=False)
        if future.cancelled():
            raise ExecutionCancelled()
        return future.result()

    async def drain(self):
        """Keep patches, capture and execution ownership until real work ends."""
        while True:
            with self._workers_lock:
                pending = list(self.workers)
            if not pending:
                break
            for future in pending:
                future.cancel()
            self.events.emit("waiting", stage="cleanup", in_flight=True,
                             stopping=self.services.cancelled(), timed_out=bool(self.expired))
            await asyncio.sleep(0.25)
        self.pool.shutdown(wait=True, cancel_futures=True)

    async def model_response(self, agent, messages, agent_type):
        expected = self.revision
        self.check(expected)
        prompt = [*messages, self.context_message()]
        receipt_ids = tuple(self.pending_receipts)
        answer_ids = tuple(self.answer_receipts)
        last_error = None
        for attempt in range(1, self.limits.model_attempts + 1):
            self.check(expected)
            self.events.emit("phase", phase="planning", summary="正在分析当前要求")
            self.events.emit("model_attempt", attempt=attempt, agent=str(agent_type))

            def call():
                self.check(expected, wait=False)
                if self.services.authorize_model:
                    self.services.authorize_model(receipt_ids, expected)
                    self.check(expected, wait=False)
                # The actual invocation (not draining the input queue) acknowledges use.
                for source, ids in (("supplement", receipt_ids), ("answer", answer_ids)):
                    fresh = [item for item in ids if item and item not in self.delivered_receipts]
                    if fresh:
                        self.events.emit("context_applied", source=source, receipt_ids=fresh, revision=expected)
                        self.delivered_receipts.update(fresh)
                return agent.get_response(prompt, agent_type, False)

            future = self.pool.submit(call)
            self.register_worker(future)
            try:
                response, cost = await self.wait_worker(future, "model", self.limits.model_seconds, expected=expected)
                self.check(expected)
                agent.response_to_dict(response)
                self.pending_receipts = [item for item in self.pending_receipts if item not in receipt_ids]
                self.answer_receipts = [item for item in self.answer_receipts if item not in answer_ids]
                return response, cost
            except Exception as error:
                last_error = error
                # Retry parsing and transient transport failures only, within one budget.
                transient = isinstance(error, (ValueError, TimeoutError, ConnectionError)) or type(error).__name__ in {
                    "APIConnectionError", "APITimeoutError", "RateLimitError", "InternalServerError"}
                self.events.emit("retry", stage="model", attempt=attempt,
                                 error_type=type(error).__name__, will_retry=transient and attempt < self.limits.model_attempts)
                if not transient:
                    raise
        raise last_error

    def reset_plan(self, agent, context):
        from ufo.module.context import ContextNames
        from ufo.agents.states.host_agent_state import ContinueHostAgentState

        host = self.session._host_agent
        for item in {agent, host}:
            if item.processor:
                item.processor.processing_context.update_local({
                    "pending_actions": None, "confirmation_decision": None,
                    "parsed_response": None, "action_execution_started": True,
                })
            item.processor = None
        host.status = "CONTINUE"
        host.set_state(ContinueHostAgentState())
        self.session.current_round.agent = host
        self.session.current_round.state = host.state
        context.set(ContextNames.SUBTASK, "")
        context.set(ContextNames.HOST_MESSAGE, [])
        context.set(ContextNames.ROUND_RESULT, "")
        self.final_answer = self.final_status = None
        self.events.emit("phase", phase="observing", summary="要求或桌面已变化，正在重新观察")
        return host


class GuardedExecutor(concurrent.futures.Executor):
    """Check admission inside the real worker, not when work is merely queued."""
    def __init__(self, executor, runtime, tool, expected):
        self.executor, self.runtime, self.tool, self.expected = executor, runtime, tool, expected
        self.submitted = []
        self.started_at = None

    def submit(self, fn, /, *args, **kwargs):
        runtime = self.runtime

        def guarded():
            runtime.check(self.expected)
            before = time.monotonic()
            try:
                runtime.services.desktop_access("before", expected_revision=self.expected)
            finally:
                runtime.user_wait += time.monotonic() - before
            try:
                runtime.check(self.expected, wait=False)
                call_id = uuid.uuid4().hex
                if runtime.services.authorize_tool is not None:
                    runtime.services.authorize_tool(
                        self.tool.tool_name, copy.deepcopy(self.tool.parameters),
                        self.tool.tool_type, call_id, self.expected,
                    )
                    runtime.check(self.expected, wait=False)
                self.started_at = time.monotonic()
                attempt = None
                if self.tool.tool_type == "action":
                    attempt = {"tool": self.tool.tool_name,
                               "arguments": copy.deepcopy(self.tool.parameters),
                               "ok": None, "outcome": "unknown"}
                    # Record before invocation: failures can follow external effects.
                    runtime.actions.append(attempt)
                started = time.monotonic()
                runtime.events.emit("tool_started", action=self.tool.tool_name,
                                    revision=self.expected, tool_type=self.tool.tool_type,
                                    call_id=call_id, arguments=copy.deepcopy(self.tool.parameters))
                try:
                    result = fn(*args, **kwargs)
                    ok = not getattr(result, "is_error", False)
                    runtime.events.emit("action_result", action=self.tool.tool_name, ok=ok)
                    if attempt is not None:
                        attempt.update(ok=ok, outcome="returned" if ok else "error_effects_unknown")
                    runtime.events.emit("tool_finished", action=self.tool.tool_name,
                                        call_id=call_id, tool_type=self.tool.tool_type,
                                        arguments=copy.deepcopy(self.tool.parameters), ok=ok)
                    return result
                except BaseException as error:
                    runtime.events.emit("tool_finished", action=self.tool.tool_name,
                                        call_id=call_id, tool_type=self.tool.tool_type,
                                        arguments=copy.deepcopy(self.tool.parameters),
                                        ok=False, error_type=type(error).__name__)
                    raise
                finally:
                    runtime.events.emit("timing", stage="tool", action=self.tool.tool_name,
                                        elapsed_seconds=round(time.monotonic()-started, 3))
            finally:
                runtime.services.desktop_access("after")

        future = self.executor.submit(guarded)
        self.submitted.append(future)
        runtime.register_worker(future)
        return future


@contextmanager
def runtime_controls(runtime):
    """Install scoped hooks; the caller must drain workers before leaving."""
    from ufo.agents.agent import basic
    from ufo.agents.processors.core.processor_framework import ProcessorTemplate
    from ufo.agents.processors.strategies.host_agent_processing_strategy import HostLLMInteractionStrategy
    from ufo.agents.processors.strategies.app_agent_processing_strategy import AppLLMInteractionStrategy
    from ufo.client.computer import Computer
    from ufo.llm import AgentType
    from ufo.module import interactor
    from ufo.module.context import ContextNames
    from fastmcp import Client
    from fastmcp.tools.tool import FunctionTool
    from fastmcp.exceptions import ToolError

    originals = []

    def replace(obj, name, value):
        originals.append((obj, name, vars(obj)[name]))
        setattr(obj, name, value)

    original_handle = basic.BasicAgent.handle
    original_process = ProcessorTemplate.process
    original_action = Computer._run_action
    original_call_tool = Client.call_tool
    original_function_run = FunctionTool.run

    async def handle(agent, context):
        while True:
            try:
                current = runtime.check()
                changed = current != runtime.revision
                changed = runtime.collect_inputs() or changed
                if changed:
                    agent = runtime.reset_plan(agent, context)
                runtime.revision = current
                context.set(ContextNames.REQUEST, runtime.request.text)
                await original_handle(agent, context)
                runtime.check(current)
                return
            except Reobserve:
                # The native CONFIRM resume path deliberately skips observation.
                # Restart at Host observation instead, retaining completed work.
                agent = runtime.reset_plan(agent, context)

    async def process(processor):
        expected = runtime.revision
        for phase, strategy in processor.strategies.items():
            original = strategy.execute
            name = getattr(phase, "value", str(phase)).lower()
            public_phase = ("planning" if "llm" in name else "executing" if "action" in name
                            else "verifying" if "memory" in name else "observing")

            def make_execute(original, public_phase, name):
                @wraps(original)
                async def execute(agent, context):
                    runtime.check(expected)
                    runtime.events.emit("phase", phase=public_phase, step=context.get("session_step", 0))
                    start = time.monotonic()
                    try:
                        result = await original(agent, context)
                    except Exception as error:
                        runtime.errors.append(public_text(error, 500))
                        raise
                    runtime.events.emit("timing", stage=name, elapsed_seconds=round(time.monotonic()-start, 3))
                    runtime.check(expected)
                    if not result.success:
                        runtime.errors.append(public_text(result.error, 500))
                    if public_phase == "planning" and agent.__class__.__name__ == "HostAgent":
                        runtime.final_status = result.data.get("status")
                        runtime.final_answer = result.data.get("result") if runtime.final_status == "FINISH" else None
                    return result
                return execute

            strategy.execute = make_execute(original, public_phase, name)
        # Keep the guarded strategy methods on the processor for native CONFIRM
        # resume; the processor is task-local and is discarded when reobserving.
        return await original_process(processor)

    async def host_model(strategy, agent, prompt):
        return await runtime.model_response(agent, prompt, AgentType.HOST)

    async def app_model(strategy, agent, prompt):
        return await runtime.model_response(agent, prompt, AgentType.APP)

    async def call_tool(client, *args, **kwargs):
        # MCP connection/handshake may have waited since worker admission.
        runtime.check(runtime.revision, wait=False)
        return await original_call_tool(client, *args, **kwargs)

    async def function_run(tool, arguments):
        # Recheck when the local server is about to enter the actual function.
        # A normal ToolError crosses MCP cleanly; the outer product boundary
        # raises the control signal before the native agent can retry it.
        try:
            runtime.check(runtime.revision, wait=False)
        except (ExecutionCancelled, Reobserve, ExecutionBudgetExceeded):
            raise ToolError("The product invalidated this pending tool invocation") from None
        return await original_function_run(tool, arguments)

    async def action(computer, tool_call):
        expected = runtime.revision
        runtime.check(expected)
        original_executor, original_timeout = computer._executor, computer._tool_timeout
        guarded = GuardedExecutor(original_executor, runtime, tool_call, expected)
        computer._executor = guarded
        # Upstream's timeout cancels only an asyncio wrapper and starts before
        # the worker even acquires the desktop. Product deadlines below start
        # at actual invocation; the true worker is always allowed to settle.
        computer._tool_timeout = float("inf")
        started = time.monotonic()
        async def monitor():
            while True:
                await asyncio.sleep(1)
                if guarded.started_at is not None and time.monotonic()-guarded.started_at >= runtime.limits.tool_seconds:
                    runtime.expired = "tool_timeout"
                elif runtime.remaining() <= 0:
                    runtime.expired = "task_budget"
                runtime.events.emit("waiting", stage="tool", action=tool_call.tool_name,
                                    elapsed_seconds=round(time.monotonic()-started, 1),
                                    in_flight=any(not future.done() for future in guarded.submitted),
                                    stopping=runtime.services.cancelled(), timed_out=bool(runtime.expired))
        monitor_task = asyncio.create_task(monitor())
        try:
            result = await original_action(computer, tool_call)
            if guarded.started_at is not None and time.monotonic()-guarded.started_at >= runtime.limits.tool_seconds:
                runtime.expired = "tool_timeout"
            for future in guarded.submitted:
                await runtime.wait_worker(future, "tool", max(0.001, runtime.limits.tool_seconds-(time.monotonic()-started)))
            runtime.check(expected)
            return result
        finally:
            monitor_task.cancel()
            await asyncio.gather(monitor_task, return_exceptions=True)
            # Also drain if outer dispatch is cancelled by its own timeout.
            pending = [future for future in guarded.submitted if not future.done()]
            if pending:
                runtime.expired = runtime.expired or "tool_wait_interrupted"
                while any(not future.done() for future in pending):
                    runtime.events.emit("waiting", stage="tool", in_flight=True, timed_out=True)
                    await asyncio.sleep(0.25)
            computer._executor, computer._tool_timeout = original_executor, original_timeout

    def ask(question, index=1):
        runtime.check()
        start = time.monotonic()
        try:
            answer = runtime.services.ask(str(question))
        finally:
            runtime.user_wait += time.monotonic()-start
        runtime.answers.append({"question": str(question), "answer": answer})
        runtime.answer_receipts.extend(runtime.services.answer_receipt_ids())
        return answer

    def confirm(action, target):
        expected = runtime.revision
        runtime.check(expected)
        start = time.monotonic()
        try:
            allowed = runtime.services.confirm(str(action), str(target))
        finally:
            runtime.user_wait += time.monotonic()-start
        runtime.check(expected)
        if not allowed:
            runtime.declined = True
        return allowed

    def process_questions(agent, ask_user=True):
        if not agent.processor:
            return
        for question in agent.processor.processing_context.get_local("questions", []) or []:
            answer = ask(question)
            if answer.strip():
                agent.blackboard.add_questions({"question": question, "answer": answer})

    replace(basic.BasicAgent, "handle", handle)
    replace(basic.BasicAgent, "process_asker", process_questions)
    replace(ProcessorTemplate, "process", process)
    replace(HostLLMInteractionStrategy, "_get_llm_response_with_retry", host_model)
    replace(AppLLMInteractionStrategy, "_get_llm_response", app_model)
    replace(Computer, "_run_action", action)
    replace(Client, "call_tool", call_tool)
    replace(FunctionTool, "run", function_run)
    replace(basic, "question_asker", ask)
    replace(interactor, "sensitive_step_asker", confirm)
    replace(interactor, "experience_asker", lambda: confirm("保存任务经验", "UFO 经验库"))
    try:
        yield
    finally:
        for obj, name, original in reversed(originals):
            setattr(obj, name, original)
