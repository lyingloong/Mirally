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
        self.console_window = ConsoleWindow(enabled=not keep_console)
        self.log_dir = Path("logs") / self.task
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.event_log = self.log_dir / "single_agent.jsonl"
        self.history: List[Dict[str, Any]] = []
        self.total_cost = 0.0
        self._system_prompt = self._load_system_prompt()
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
                if self.cancel_event.is_set():
                    return self._result("cancelled", "Task cancelled.", step - 1, started)
                screenshot_path = self.log_dir / f"single_step_{step}.png"
                observation = await asyncio.to_thread(
                    self.computer.observe, screenshot_path
                )
                guard_observation = (
                    self.loop_guard.observe(observation) if self.loop_guard else None
                )
                selected_skills = (
                    self.skill_router.select(self.request, observation)
                    if self.skill_router
                    else []
                )
                response, metrics, cost = await self._decide(
                    step,
                    observation,
                    selected_skills=selected_skills,
                )
                self.total_cost += cost

                self._emit(
                    {
                        "type": "step",
                        "step": step,
                        "observation": response.observation,
                        "actions": [action.model_dump() for action in response.actions],
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
                    message = response.message or response.thought
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
                    event["user_answer"] = answer
                    self._write_event(event)
                    self.console_window.hide()
                    continue

                effective_actions = self._effective_actions(response)
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
                action_results = await self._execute_actions(response)
                event["action_results"] = action_results
                for action_result in action_results:
                    self._emit({"type": "action_result", **action_result})
                self._write_event(event)
                if self.loop_guard:
                    self.loop_guard.record(
                        effective_actions[: len(action_results)]
                    )
                self.history.append(
                    {
                        "step": step,
                        "observation": response.observation,
                        "thought": response.thought,
                        "actions": [action.model_dump() for action in response.actions],
                        "results": action_results,
                    }
                )
                self.history = self.history[-6:]

                delay = (
                    0.2
                    if action_results and not action_results[-1]["ok"]
                    else self.config.system.sleep_time
                )
                await asyncio.sleep(delay)

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
            if action.sensitive and self.config.system.safe_guard:
                approved = self._confirm_action(action)
                self.console_window.hide()
                if not approved:
                    results.append(
                        {
                            "ok": False,
                            "action": action.type,
                            "error": "User rejected sensitive action",
                        }
                    )
                    break
            result = await asyncio.to_thread(self.computer.execute, action)
            results.append(result)
            if not result.get("ok") or action.type in self.computer.BARRIER_ACTIONS:
                break
        return results

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
        if self.event_callback is None:
            return
        try:
            self.event_callback(event)
        except Exception:
            console.exception("Single-agent event callback failed")

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
