"""One controlled product task through the unmodified native UFO Session."""

from __future__ import annotations

import asyncio
import logging
from contextlib import ExitStack
from pathlib import Path

from openassist.core.execution import ExecutionCapabilities, TaskResult
from openassist.core.text import public_text
from .compat import native_compatibility
from .config import load_execution_limits, bounded_model_services
from .controls import ExecutionBudgetExceeded, NativeRuntime, runtime_controls
from .events import EventStream, session_result
from .locking import UFO_RUNTIME_LOCK as _RUN_LOCK


logger = logging.getLogger(__name__)


class UfoExecutor:
    # Controls apply between tool calls, not inside a system/remote operation.
    capabilities = ExecutionCapabilities(
        pause=True, supplement=True, capture_isolation=True,
        cancellation_mode="between_tools_after_in_flight_settles",
    )

    def __init__(self, product_root=None, *, limits=None):
        self.product_root = Path(product_root) if product_root else Path(__file__).resolve().parents[3]
        self.limits = limits or load_execution_limits(self.product_root)

    async def run(self, request, services):
        if not _RUN_LOCK.acquire(blocking=False):
            raise RuntimeError("另一个 UFO 原生任务仍在执行或清理。")
        events = EventStream(request.task_id, services.event)
        runtime = NativeRuntime(request, services, self.limits, events)
        stack = ExitStack()
        result = None
        session = None
        diagnostics_path = ""
        try:
            runtime.check()
            import sys
            if sys.platform == "win32":
                from openassist.platform.desktop_lock import desktop_execution_lock
                stack.enter_context(desktop_execution_lock())
            events.emit("phase", phase="preparing", summary="正在初始化原生执行器")
            stack.enter_context(native_compatibility())
            from ufo.module.sessions.session import Session
            from .observation import observation_isolation

            stack.enter_context(bounded_model_services(self.limits))
            stack.enter_context(runtime_controls(runtime))
            stack.enter_context(observation_isolation(services, services.excluded_process_ids))

            class SingleRequestSession(Session):
                def next_request(self):
                    if self.total_rounds == 0:
                        return self._init_request
                    self._finish = True
                    return ""

            future = runtime.pool.submit(SingleRequestSession, request.task_id, False, 0,
                                         request=request.text, mode="normal")
            runtime.register_worker(future)
            session = await runtime.wait_worker(future, "initialization", self.limits.initialization_seconds)
            runtime.session = session
            diagnostics_path = events.open(session.log_path)
            # Check size before screenshots/actions; preserve referenced text exactly.
            runtime.context_message()
            await session.run()
            runtime.check()
            result = session_result(session, declined=runtime.declined,
                                    action_count=sum(item.get("ok") is True for item in runtime.actions),
                                    errors=runtime.errors, terminal_answer=runtime.final_answer,
                                    terminal_status=runtime.final_status)
        except asyncio.CancelledError:
            result = TaskResult("cancelled", "已停止启动新的操作；已发出的操作已完成收尾。",
                                reason="user_cancelled")
        except ExecutionBudgetExceeded as error:
            result = TaskResult("incomplete", "等待预算已用尽，没有继续启动操作。请检查执行记录后再决定是否重试。",
                                reason=str(error))
        except Exception as error:
            logger.exception("Native task failed")
            result = TaskResult("failed", public_text(error, 600) or "原生执行失败。",
                                reason=type(error).__name__)
        finally:
            try:
                # Scope cleanup must never race a timed-out MCP/model thread.
                await runtime.drain()
                stack.close()
            except Exception as error:
                logger.exception("Native task cleanup failed")
                prior = result.as_dict() if result else {}
                result = TaskResult("failed", "任务已停止，但运行状态恢复失败，请重新启动应用。",
                                    reason="cleanup_failed", diagnostics={"prior_result": prior,
                                                                        "cleanup_error": type(error).__name__})
            finally:
                if result is None:
                    result = TaskResult("incomplete", "执行已结束，未取得完成结果。")
                diagnostics = {**result.diagnostics, "event_log": diagnostics_path,
                               "actions": len(runtime.actions), "budget_reason": runtime.expired}
                result = TaskResult(result.status, result.message, result.artifacts, result.reason, diagnostics)
                try:
                    events.emit("finished", **result.as_dict())
                finally:
                    events.close()
                    _RUN_LOCK.release()
        return result


UFOExecutor = UfoExecutor
