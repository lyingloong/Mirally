"""Run one request through UFO's native Windows multi-agent session."""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any, Callable, Dict


_RUN_LOCK = threading.Lock()


async def run_native_request(
    request: str,
    task: str,
    *,
    event_callback: Callable[[Dict[str, Any]], None],
    input_callback: Callable[[str], str],
    confirmation_callback: Callable[[str, str], bool],
    cancel_event: threading.Event,
) -> Dict[str, Any]:
    """Run UFO²'s normal HostAgent/AppAgent workflow with OpenAssist prompts."""
    if not _RUN_LOCK.acquire(blocking=False):
        raise RuntimeError("A native UFO task is already running.")

    basic_agent = None
    interactor = None
    root_logger = None
    previous_question_asker = None
    previous_handle = None
    previous_confirmation_asker = None
    previous_experience_asker = None
    previous_handlers = None
    previous_level = None
    try:
        from config.config_loader import get_ufo_config
        from ufo.agents.agent import basic as basic_agent_module
        from ufo.module import interactor as interactor_module
        from ufo.module.sessions.session import Session

        basic_agent = basic_agent_module
        interactor = interactor_module
        previous_question_asker = basic_agent.question_asker
        previous_handle = basic_agent.BasicAgent.handle
        previous_confirmation_asker = interactor.sensitive_step_asker
        previous_experience_asker = interactor.experience_asker
        root_logger = logging.getLogger()
        previous_handlers = root_logger.handlers[:]
        previous_level = root_logger.level

        def ask_user(question: str, index: int = 1) -> str:
            del index
            return input_callback(question)

        def confirm_action(action: str, target: str) -> bool:
            return confirmation_callback(str(action), str(target))

        async def cancellable_handle(agent: Any, context: Any) -> None:
            if cancel_event.is_set():
                raise asyncio.CancelledError()
            await previous_handle(agent, context)

        class UiLogHandler(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                event_callback({"type": "progress", "message": self.format(record)})

        class SingleRequestSession(Session):
            def next_request(self) -> str:
                if self.total_rounds == 0:
                    return self._init_request
                self._finish = True
                return ""

        basic_agent.question_asker = ask_user
        basic_agent.BasicAgent.handle = cancellable_handle
        interactor.sensitive_step_asker = confirm_action
        interactor.experience_asker = lambda: confirmation_callback(
            "保存任务经验", "UFO 经验库"
        )

        # Route UFO's Python log messages to the chat transcript.
        root_logger.handlers.clear()
        log_handler = UiLogHandler()
        log_handler.setFormatter(logging.Formatter("%(levelname)s · %(message)s"))
        root_logger.addHandler(log_handler)
        root_logger.setLevel(logging.INFO)

        config = get_ufo_config()
        session = SingleRequestSession(
            task=task,
            should_evaluate=config.system.eva_session,
            id=0,
            request=request,
            mode="normal",
        )
        event_callback(
            {
                "type": "progress",
                "message": "UFO 原生 HostAgent/AppAgent 工作流已启动。",
            }
        )
        try:
            await session.run()
        except asyncio.CancelledError:
            result = {"status": "cancelled", "message": "任务已停止。"}
            event_callback(
                {
                    "type": "native_finished",
                    "status": "cancelled",
                    "message": "任务已停止。",
                }
            )
        else:
            failed = session.is_error()
            result = {
                "status": "failed" if failed else "done",
                "message": "UFO 原生任务执行失败，请检查运行记录。"
                if failed
                else "UFO 原生任务已结束。",
            }
            event_callback(
                {
                    "type": "native_finished",
                    "status": "failed" if failed else "done",
                    "message": "UFO 原生任务执行失败，请检查运行记录。"
                    if failed
                    else "UFO 原生任务已结束。",
                }
            )
        return result
    finally:
        if basic_agent is not None and previous_question_asker is not None:
            basic_agent.question_asker = previous_question_asker
        if basic_agent is not None and previous_handle is not None:
            basic_agent.BasicAgent.handle = previous_handle
        if interactor is not None and previous_confirmation_asker is not None:
            interactor.sensitive_step_asker = previous_confirmation_asker
        if interactor is not None and previous_experience_asker is not None:
            interactor.experience_asker = previous_experience_asker
        if root_logger is not None and previous_handlers is not None:
            root_logger.handlers.clear()
            root_logger.handlers.extend(previous_handlers)
        if root_logger is not None and previous_level is not None:
            root_logger.setLevel(previous_level)
        _RUN_LOCK.release()
