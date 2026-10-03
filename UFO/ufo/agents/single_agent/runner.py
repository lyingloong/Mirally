"""Lightweight observe-decide-act loop for Windows computer use."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import yaml
from pydantic import ValidationError
from rich.console import Console
from rich.prompt import Prompt

from config.config_loader import get_ufo_config
from ufo import utils
from ufo.agents.single_agent.computer import ConsoleWindow, WindowsComputer
from ufo.agents.single_agent.loop_guard import LoopGuard
from ufo.agents.single_agent.schemas import (
    DesktopObservation,
    SingleAgentAction,
    SingleAgentResponse,
)
from ufo.agents.single_agent.skill_router import SkillMatch, SkillRouter
from ufo.llm import AgentType, llm_call
from ufo.module import interactor
from ufo.prompter.prompt_sanitizer import sanitize_user_input

console = Console()


class SingleAgentRunner:
    """Run one task with one model and one observation/action loop."""

    def __init__(
        self,
        request: str,
        task: str,
        *,
        keep_console: bool = False,
        max_steps: Optional[int] = None,
        computer: Optional[WindowsComputer] = None,
        event_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
        input_callback: Optional[Callable[[str], str]] = None,
        confirmation_callback: Optional[Callable[[str, str], bool]] = None,
        cancel_event: Optional[threading.Event] = None,
        control_callback: Optional[Callable[[], int]] = None,
        capture_callback: Optional[Callable[[str], None]] = None,
        supplement_callback: Optional[Callable[[], List[str]]] = None,
        desktop_callback: Optional[Callable[[str], int]] = None,
        conversation_context: Optional[Dict[str, Any]] = None,
        initial_user_inputs: Optional[List[str]] = None,
    ) -> None:
        self.request = request
        self.task = utils.sanitize_task_name(task, fallback="single-agent")
        self.config = get_ufo_config()
        self.max_steps = max_steps or self.config.system.max_step
        self.computer = computer or WindowsComputer()
        self.event_callback = event_callback
        self.input_callback = input_callback
        self.confirmation_callback = confirmation_callback
        self.cancel_event = cancel_event or threading.Event()
        self.control_callback = control_callback
        self.capture_callback = capture_callback
        self.supplement_callback = supplement_callback
        self.desktop_callback = desktop_callback
        self.conversation_context = conversation_context or {}
        self._observed_revision = 0
        self._last_observation = None
        self._current_step = 0
        self.console_window = ConsoleWindow(enabled=not keep_console)
        self.log_dir = Path("logs") / self.task
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.event_log = self.log_dir / "single_agent.jsonl"
        self.history: List[Dict[str, Any]] = []
        self.user_answers: List[Dict[str, str]] = [
            {"question": "User added task information", "answer": text}
            for text in (initial_user_inputs or [])
        ]
        self.total_cost = 0.0
        self._system_prompt = self._load_system_prompt()
        self._system_prompt += (
            "\nConversation context contains historical user requests and task results, not current desktop observations. "
            "Use it to resolve references such as 'the previous result' and preserve relevant user constraints. "
            "The current request and newer user additions take precedence over older instructions. "
            "Verify referenced files and desktop state before acting. Previous approvals never authorize new sensitive actions. "
            "When finishing, report actual outcomes and any produced file paths so subsequent tasks can refer to them."
        )
        self.skill_router = self._create_skill_router()
        self.loop_guard = self._create_loop_guard()

    @staticmethod
    def _load_system_prompt() -> str:
        path = Path("ufo/prompts/share/base/single_agent.yaml")
        with path.open("r", encoding="utf-8") as stream:
            data = yaml.safe_load(stream)
        return data["system"]

    def _create_skill_router(self) -> Optional[SkillRouter]:
        if not self.config.system.single_agent_skills_enabled:
            return None
        skill_path = Path(self.config.system.single_agent_skills_path)
        if not skill_path.is_absolute():
            skill_path = Path(__file__).resolve().parents[3] / skill_path
        return SkillRouter(
            skill_path,
            top_k=self.config.system.single_agent_skills_top_k,
        )

    def _create_loop_guard(self) -> Optional[LoopGuard]:
        if not self.config.system.single_agent_loop_guard_enabled:
            return None
        return LoopGuard(
            repeat_limit=self.config.system.single_agent_repeat_limit,
            stagnant_limit=self.config.system.single_agent_stagnant_limit,
            max_blocked_attempts=(
                self.config.system.single_agent_max_guard_blocks
            ),
        )

    async def run(self) -> Dict[str, Any]:
        started = time.perf_counter()
        self.console_window.hide()
        try:
            await asyncio.sleep(0.2)
            for step in range(1, self.max_steps + 1):
                self._current_step = step
                revision = self._checkpoint()
                if self.cancel_event.is_set():
                    return self._result("cancelled", "Task cancelled.", step - 1, started)
                screenshot_path = self.log_dir / f"single_step_{step}.png"
                self._phase("verifying" if step > 1 else "observing")
                observation = await self._observe(screenshot_path)
                self._last_observation = observation
                self._observed_revision = revision
                if self._checkpoint() != revision:
                    continue
                if self.supplement_callback:
                    supplements = self.supplement_callback()
                    self.user_answers.extend(
                        {"question": "User added task information", "answer": text}
                        for text in supplements
                    )
                    if supplements:
                        self._emit({"type": "context_applied", "source": "supplement"})
                guard_observation = (
                    self.loop_guard.observe(observation) if self.loop_guard else None
                )
                selected_skills = (
                    self.skill_router.select(self.request, observation)
                    if self.skill_router
                    else []
                )
                self._phase("planning")
                response, metrics, cost = await self._decide(
                    step,
                    observation,
                    selected_skills=selected_skills,
                )
                self.total_cost += cost
                # Pausing while a model request is outstanding invalidates its
                # entire plan. Only a fresh observation can authorize actions.
                if self._checkpoint() != revision:
                    continue
                if self.cancel_event.is_set():
                    return self._result("cancelled", "Task cancelled.", step, started)
                prepared_actions = self._prepare_actions(response.actions, observation)
                execution_response = response.model_copy(
                    update={"actions": prepared_actions}
                )
                effective_actions = self._effective_actions(execution_response)

                self._emit(
                    {
                        "type": "step",
                        "step": step,
                        "observation": response.observation,
                        "actions": [action.model_dump() for action in effective_actions],
                        "status": response.status,
                    }
                )

                console.print(
                    f"[cyan]Step {step}[/cyan] {response.observation} "
                    f"[dim]({metrics.get('duration_seconds', 0):.2f}s)[/dim]"
                )
                event: Dict[str, Any] = {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "step": step,
                    "request": self.request,
                    "observation": observation.prompt_state(),
                    "response": response.model_dump(),
                    "execution_plan": [
                        action.model_dump() for action in prepared_actions
                    ],
                    "metrics": metrics,
                    "cost": cost,
                    "action_results": [],
                    "selected_skills": [
                        match.log_value() for match in selected_skills
                    ],
                }
                if guard_observation:
                    event["loop_guard"] = {"observation": guard_observation}

                if response.status == "done":
                    message = response.message or response.observation
                    self._write_event(event)
                    return self._result("done", message, step, started)

                if response.status == "error":
                    message = response.message or "The agent could not complete this task."
                    self._write_event(event)
                    return self._result("error", message, step, started)

                if response.status == "need_user":
                    answer = self._ask_user(response.question or "More information?")
                    if self.cancel_event.is_set():
                        return self._result("cancelled", "Task cancelled.", step, started)
                    self.history.append(
                        {
                            "step": step,
                            "question": response.question,
                            "answer": answer,
                        }
                    )
                    self.user_answers.append(
                        {"question": response.question or "", "answer": answer}
                    )
                    self._emit({"type": "context_applied", "source": "answer"})
                    event["user_answer"] = answer
                    self._write_event(event)
                    self.console_window.hide()
                    continue

                if self.loop_guard:
                    guard_decision = self.loop_guard.assess(effective_actions)
                    event["loop_guard"]["decision"] = guard_decision.to_dict()
                    if guard_decision.blocked:
                        event["action_results"] = [
                            {
                                "ok": False,
                                "action": "loop_guard",
                                "error": guard_decision.reason,
                            }
                        ]
                        self.history.append(
                            {
                                "step": step,
                                "loop_guard": guard_decision.to_dict(),
                                "blocked_actions": [
                                    action.model_dump() for action in effective_actions
                                ],
                            }
                        )
                        self.history = self.history[-6:]
                        self._write_event(event)
                        if guard_decision.terminal:
                            return self._result(
                                "error",
                                (
                                    "Stopped because the agent repeated a blocked "
                                    "action without making UI progress."
                                ),
                                step,
                                started,
                            )
                        await asyncio.sleep(0.2)
                        continue

                if self.cancel_event.is_set():
                    return self._result("cancelled", "Task cancelled.", step, started)
                action_results = await self._execute_actions(execution_response)
                event["action_results"] = action_results
                feedback = self._execution_feedback(prepared_actions, action_results)
                event.update(feedback)
                for action_result in action_results:
                    self._emit({"type": "action_result", **action_result})
                self._write_event(event)
                if self.loop_guard:
                    self.loop_guard.record(
                        effective_actions[: len(action_results)], results=action_results
                    )
                self.history.append(
                    {
                        "step": step,
                        "observation": response.observation,
                        "thought": response.thought,
                        "requested_actions": [
                            action.model_dump() for action in response.actions
                        ],
                        **feedback,
                        "results": action_results,
                    }
                )
                self.history = self.history[-6:]

                delay = (
                    0.2
                    if action_results and not action_results[-1]["ok"]
                    else self.config.system.sleep_time
                )
                deadline = time.monotonic() + delay
                while time.monotonic() < deadline and not self.cancel_event.is_set():
                    self._checkpoint()
                    await asyncio.sleep(min(0.1, max(0, deadline - time.monotonic())))

            message = f"Stopped after reaching the {self.max_steps}-step limit."
            return self._result("error", message, self.max_steps, started)
        except Exception as exc:
            message = f"Single-agent execution failed: {exc}"
            return self._result("error", message, len(self.history), started)
        finally:
            self.console_window.show()

    async def _decide(
        self,
        step: int,
        observation: DesktopObservation,
        *,
        selected_skills: Optional[List[SkillMatch]] = None,
    ) -> tuple[SingleAgentResponse, Dict[str, Any], float]:
        user_state = {
            "request": sanitize_user_input(self.request, "user_request"),
            "step": step,
            "desktop": observation.prompt_state(),
            "recent_history": self.history[-6:],
            "user_answers": self.user_answers,
            "conversation_context": getattr(self, "conversation_context", {}),
        }
        if selected_skills:
            user_state["selected_skills"] = [
                match.prompt_value() for match in selected_skills
            ]
        if self.loop_guard:
            guard_context = self.loop_guard.prompt_context()
            if guard_context:
                user_state["loop_guard"] = guard_context
        content: List[Dict[str, Any]] = []
        if self.config.app_agent.visual_mode:
            content.extend(
                [
                    {"type": "text", "text": "Current desktop screenshot:"},
                    {
                        "type": "image_url",
                        "image_url": {"url": observation.screenshot},
                    },
                ]
            )
        content.append(
            {
                "type": "text",
                "text": json.dumps(user_state, ensure_ascii=False),
            }
        )
        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": self._system_prompt},
            {"role": "user", "content": content},
        ]

        attempts = max(1, self.config.system.json_parsing_retry)
        last_error: Optional[Exception] = None
        total_cost = 0.0
        metrics: Dict[str, Any] = {}
        for attempt in range(attempts):
            response_text, cost, metrics = await asyncio.to_thread(
                llm_call.get_completion_with_metrics,
                messages,
                AgentType.SINGLE,
                False,
            )
            total_cost += cost or 0.0
            try:
                parsed = utils.json_parser(response_text)
                response = SingleAgentResponse.model_validate(parsed)
                return response, metrics, total_cost
            except (ValueError, ValidationError, TypeError) as exc:
                last_error = exc
                if attempt + 1 < attempts:
                    messages.extend(
                        [
                            {"role": "assistant", "content": response_text},
                            {
                                "role": "user",
                                "content": (
                                    "The response was invalid. Return corrected JSON "
                                    "only. "
                                    f"Validation error: {exc}"
                                ),
                            },
                        ]
                    )
        raise RuntimeError(
            f"Model returned no valid single-agent response: {last_error}"
        )

    async def _execute_actions(
        self, response: SingleAgentResponse
    ) -> List[Dict[str, Any]]:
        results = []
        for action in response.actions:
            if self._checkpoint() != getattr(self, "_observed_revision", 0):
                results.append({"ok": False, "action": action.type, "executed": False,
                                "error": "User interaction invalidated the old observation; re-observe."})
                break
            if self.cancel_event.is_set():
                results.append(
                    {"ok": False, "action": action.type, "executed": False,
                     "error": "Task cancelled before action"}
                )
                break
            if action.sensitive and (self.config.system.safe_guard or getattr(self, "confirmation_callback", None) is not None):
                approved = self._confirm_action(action)
                self.console_window.hide()
                if not approved:
                    results.append(
                        {
                            "ok": False,
                            "action": action.type,
                            "error": "User rejected sensitive action",
                            "executed": False,
                        }
                    )
                    break
                if getattr(self, "control_callback", None) is not None:
                    # Rebuild the computer's target map after every approval.
                    revision = self._checkpoint()
                    self._phase("observing")
                    fresh = await self._observe(self.log_dir / f"confirmation_{self._current_step}.png")
                    if self._checkpoint() != revision or not self._same_confirmed_target(action, self._last_observation, fresh):
                        results.append({"ok": False, "action": action.type, "executed": False,
                                        "error": "Desktop changed during confirmation; re-plan and confirm again."})
                        break
                    self._last_observation = fresh
                    self._observed_revision = revision
            if self.cancel_event.is_set():
                results.append(
                    {"ok": False, "action": action.type, "executed": False,
                     "error": "Task cancelled before action"}
                )
                break
            self._phase("executing", action=action.type, summary=action.description or "")
            desktop = getattr(self, "desktop_callback", None)
            access_revision = desktop("before") if desktop else self._checkpoint()
            try:
                if access_revision != getattr(self, "_observed_revision", 0):
                    results.append({"ok": False, "action": action.type, "executed": False,
                                    "error": "New input invalidated this action; re-observe."})
                    break
                result = await asyncio.to_thread(self.computer.execute, action)
            finally:
                if desktop:
                    desktop("after")
            result.setdefault("executed", True)
            results.append(result)
            if not result.get("ok") or action.type in self.computer.BARRIER_ACTIONS:
                break
        return results

    def _checkpoint(self) -> int:
        callback = getattr(self, "control_callback", None)
        return callback() if callback is not None else 0

    def _phase(self, phase: str, **values) -> None:
        observation = getattr(self, "_last_observation", None)
        application = (observation.active_window or {}).get("process", "") if observation else ""
        self._emit({"type": "phase", "phase": phase, "step": getattr(self, "_current_step", 0),
                    "application": application, **values})

    async def _observe(self, screenshot_path):
        callback = getattr(self, "capture_callback", None)
        desktop = getattr(self, "desktop_callback", None)
        if desktop:
            desktop("before")
        try:
            if callback:
                callback("before")
            try:
                return await asyncio.to_thread(self.computer.observe, screenshot_path)
            finally:
                if callback:
                    callback("after")
        finally:
            if desktop:
                desktop("after")

    @staticmethod
    def _same_confirmed_target(action, previous, fresh) -> bool:
        if previous is None or previous.active_window != fresh.active_window:
            return False
        if previous.display_size != fresh.display_size:
            return False
        if action.element_id:
            old = next((c for c in previous.controls if c["id"] == action.element_id), None)
            new = next((c for c in fresh.controls if c["id"] == action.element_id), None)
            return old is not None and old == new and previous.target_states.get(action.element_id) == fresh.target_states.get(action.element_id)
        if action.window_id:
            old = next((w for w in previous.windows if w["id"] == action.window_id), None)
            new = next((w for w in fresh.windows if w["id"] == action.window_id), None)
            return old is not None and old == new
        return (previous.screenshot == fresh.screenshot and previous.controls == fresh.controls
                and previous.foreground_handle == fresh.foreground_handle
                and previous.target_states == fresh.target_states)

    @staticmethod
    def _prepare_actions(
        actions: List[SingleAgentAction], observation: DesktopObservation
    ) -> List[SingleAgentAction]:
        """Combine entry into a known empty Edit without overwriting existing text."""
        controls = {control["id"]: control for control in observation.controls}
        prepared = []
        index = 0
        while index < len(actions):
            click = actions[index]
            text = actions[index + 1] if index + 1 < len(actions) else None
            target = controls.get(click.element_id, {})
            target_state = observation.target_states.get(click.element_id, {})
            if (
                click.type == "click_element"
                and click.button == "left" and not click.double
                and text is not None and text.type == "type_text"
                and text.element_id in (None, click.element_id)
                and target.get("type") == "Edit"
                and target_state.get("empty") is True
                and target.get("editable") is not False
                and target.get("enabled") is not False
            ):
                prepared.append(
                    SingleAgentAction(
                        type="fill_text", element_id=click.element_id, text=text.text,
                        sensitive=click.sensitive or text.sensitive,
                        description=text.description or click.description,
                    )
                )
                index += 2
            else:
                prepared.append(click)
                index += 1
        return prepared

    @staticmethod
    def _execution_feedback(
        actions: List[SingleAgentAction], results: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Report dispatched actions separately from a model's unexecuted plan."""
        executed = []
        not_executed = []
        unverified = []
        for index, action in enumerate(actions):
            result = results[index] if index < len(results) else None
            if result is not None and result.get("executed", True) is None:
                unverified.append({
                    "action": action.model_dump(),
                    "reason": "Execution failed; partial effects are unknown. "
                    "Inspect the UI before retrying.",
                })
            elif result is not None and result.get("executed", True):
                executed.append(action.model_dump())
            else:
                if result is not None:
                    reason = result.get("error") or result.get("detail")
                elif results and not results[-1].get("ok"):
                    reason = "Previous action failed; re-observe before retrying"
                else:
                    reason = (
                        "Observation barrier: this action was not executed. "
                        "Resume from a fresh observation."
                    )
                not_executed.append({"action": action.model_dump(), "reason": reason})
        return {
            "executed_actions": executed,
            "not_executed_actions": not_executed,
            "unverified_actions": unverified,
        }

    def _effective_actions(
        self, response: SingleAgentResponse
    ) -> List[SingleAgentAction]:
        """Return the prefix that can execute before the next observation."""
        actions = []
        for action in response.actions:
            actions.append(action)
            if action.type in self.computer.BARRIER_ACTIONS:
                break
        return actions

    def _ask_user(self, question: str) -> str:
        if self.input_callback is not None:
            return self.input_callback(question)
        self.console_window.show()
        return Prompt.ask(question, console=console)

    def _confirm_action(self, action) -> bool:
        target = (
            action.description or action.element_id or action.window_id or "desktop"
        )
        if self.confirmation_callback is not None:
            return self.confirmation_callback(action.type, target)
        self.console_window.show()
        return interactor.sensitive_step_asker(action.type, target)

    def _emit(self, event: Dict[str, Any]) -> None:
        if getattr(self, "event_callback", None) is None:
            return
        try:
            self.event_callback(event)
        except Exception:
            self.cancel_event.set()
            raise

    def _write_event(self, event: Dict[str, Any]) -> None:
        with self.event_log.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")

    def _result(
        self, status: str, message: str, steps: int, started: float
    ) -> Dict[str, Any]:
        result = {
            "status": status,
            "message": message,
            "steps": steps,
            "cost": self.total_cost,
            "duration_seconds": round(time.perf_counter() - started, 3),
            "log": str(self.event_log),
        }
        self._write_event({"final": result})
        self._emit({"type": "finished", **result})
        return result
