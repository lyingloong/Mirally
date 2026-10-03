"""Regression tests for pause boundaries, confirmation freshness and capture."""
import asyncio
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from ufo.agents.single_agent.runner import SingleAgentRunner
from ufo.agents.single_agent.computer import WindowsComputer
from ufo.agents.single_agent.schemas import DesktopObservation, SingleAgentAction, SingleAgentResponse


def observation(name="Save", rect=None):
    return DesktopObservation(windows=[{"id": "w:1", "name": "Editor"}], active_window={"id": "w:1", "name": "Editor"},
                              controls=[{"id": "c:1", "name": name, "rect": rect or [1, 1, 20, 20]}],
                              screenshot="data:image/png;base64,", screenshot_path="test.png")


class FrontendControlTests(unittest.IsolatedAsyncioTestCase):
    async def test_conversation_stage_additions_reach_first_desktop_prompt(self):
        prompts = []
        def completion(messages, *_args):
            prompts.append(json.loads(messages[-1]["content"][-1]["text"]))
            return json.dumps({"observation": "ready", "thought": "private", "actions": [],
                               "status": "done", "message": "done"}), 0, {}
        with patch("ufo.agents.single_agent.runner.ConsoleWindow"):
            runner = SingleAgentRunner(request="整理资料", task="conversation-handoff", max_steps=1,
                                       computer=SimpleNamespace(observe=lambda path: observation(), BARRIER_ACTIONS=set()),
                                       initial_user_inputs=["只处理 PDF", "保留原件"])
        runner.skill_router = runner.loop_guard = None
        with patch("ufo.agents.single_agent.runner.llm_call.get_completion_with_metrics", side_effect=completion):
            result = await runner.run()
        self.assertEqual(result["status"], "done")
        self.assertEqual([entry["answer"] for entry in prompts[0]["user_answers"]], ["只处理 PDF", "保留原件"])

    async def test_additions_reach_prompt_and_ack_follows_context_update(self):
        prompts, acknowledgments = [], []
        additions = ["Keep originals", "Only PDF files"]

        def take_supplements():
            batch, additions[:] = additions[:], []
            return batch

        def event_callback(event):
            if event.get("type") == "context_applied":
                acknowledgments.append((event["source"], [a["answer"] for a in runner.user_answers]))

        def completion(messages, *_args):
            prompts.append(json.loads(messages[-1]["content"][-1]["text"]))
            response = {"observation": "ready", "thought": "private", "actions": [],
                        "status": "need_user" if len(prompts) == 1 else "done",
                        "question": "Which folder?", "message": "done"}
            return json.dumps(response), 0, {}

        with patch("ufo.agents.single_agent.runner.ConsoleWindow"):
            runner = SingleAgentRunner(request="Archive this week's files", task="frontend-context-regression", max_steps=2,
                                       computer=SimpleNamespace(observe=lambda path: observation(), BARRIER_ACTIONS=set()),
                                       supplement_callback=take_supplements, input_callback=lambda q: "Archive",
                                       event_callback=event_callback,
                                       conversation_context={"previous_tasks": [{"request": "previous task", "result": "D:/reports/week.pdf"}]})
        runner.skill_router = runner.loop_guard = None
        with patch("ufo.agents.single_agent.runner.llm_call.get_completion_with_metrics", side_effect=completion):
            result = await runner.run()
        self.assertEqual(result["status"], "done")
        self.assertEqual([a["answer"] for a in prompts[0]["user_answers"]], ["Keep originals", "Only PDF files"])
        self.assertEqual([a["answer"] for a in prompts[1]["user_answers"]], ["Keep originals", "Only PDF files", "Archive"])
        self.assertIn("Archive this week's files", prompts[1]["request"])
        self.assertEqual(prompts[1]["request"], prompts[0]["request"])
        self.assertEqual(prompts[1]["conversation_context"]["previous_tasks"][0]["result"], "D:/reports/week.pdf")
        self.assertEqual(acknowledgments, [("supplement", ["Keep originals", "Only PDF files"]),
                                           ("answer", ["Keep originals", "Only PDF files", "Archive"])])

    async def test_pause_while_model_is_running_forces_new_observation(self):
        revision = [0]
        seen = []
        executed = []
        computer = SimpleNamespace(observe=lambda path: seen.append(path) or observation(),
                                   execute=lambda action: executed.append(action) or {"ok": True}, BARRIER_ACTIONS=set())
        with patch("ufo.agents.single_agent.runner.ConsoleWindow"):
            runner = SingleAgentRunner(request="test", task="frontend-pause-regression", max_steps=2,
                                       computer=computer, control_callback=lambda: revision[0])
        async def decide(step, *_args, **_kwargs):
            if step == 1:
                revision[0] += 1  # user paused while awaiting this model reply
                return SingleAgentResponse(observation="ready", thought="private", status="continue", actions=[SingleAgentAction(type="click", x=0.5, y=0.5)]), {}, 0
            return SingleAgentResponse(observation="done", thought="private", status="done", message="done"), {}, 0
        runner._decide = decide
        result = await runner.run()
        self.assertEqual(result["status"], "done")
        self.assertEqual(len(seen), 2)
        self.assertFalse(executed)

    def runner(self):
        runner = SingleAgentRunner.__new__(SingleAgentRunner)
        runner.cancel_event = threading.Event()
        runner.control_callback = lambda: 0
        runner.capture_callback = None
        runner._observed_revision = 0
        runner._current_step = 1
        runner._last_observation = observation()
        runner.config = SimpleNamespace(system=SimpleNamespace(safe_guard=True))
        runner.console_window = SimpleNamespace(hide=lambda: None)
        runner.log_dir = Path("logs")
        runner.executed = []
        runner.computer = SimpleNamespace(execute=lambda a: runner.executed.append(a) or {"ok": True}, BARRIER_ACTIONS=set())
        return runner

    async def test_pause_revision_discards_all_actions(self):
        runner = self.runner()
        runner.control_callback = lambda: 1
        response = SingleAgentResponse(observation="ready", thought="private", status="continue", actions=[SingleAgentAction(type="click", x=0.5, y=0.5)])
        result = await runner._execute_actions(response)
        self.assertFalse(result[0]["executed"])
        self.assertFalse(runner.executed)

    async def test_last_moment_input_invalidates_action_at_desktop_gate(self):
        runner = self.runner()
        stages = []
        runner.desktop_callback = lambda stage: stages.append(stage) or 1
        response = SingleAgentResponse(observation="ready", thought="private", status="continue", actions=[SingleAgentAction(type="click", x=0.5, y=0.5)])
        result = await runner._execute_actions(response)
        self.assertFalse(result[0]["executed"])
        self.assertFalse(runner.executed)
        self.assertEqual(stages, ["before", "after"])

    def test_frontend_process_is_filtered_from_uia_windows_and_targets(self):
        from PIL import Image
        own = SimpleNamespace(handle=101, process_id=lambda: os.getpid())
        external = SimpleNamespace(handle=202, process_id=lambda: os.getpid() + 1, is_minimized=lambda: False)
        computer = WindowsComputer.__new__(WindowsComputer)
        computer._inspector = SimpleNamespace(get_desktop_app_dict=lambda **kw: {1: own, 2: external},
                                              get_control_info=lambda *args: {"control_text": "external", "control_rect": [0, 0, 100, 100]})
        computer._photographer = SimpleNamespace(capture_desktop_screen_screenshot=lambda **kw: Image.new("RGB", (100, 100)),
                                                 encode_image=lambda image: "data:image/png;base64,")
        computer._collect_controls = lambda *args: []
        with tempfile.TemporaryDirectory() as directory, \
                patch("ufo.agents.single_agent.computer.win32gui.GetForegroundWindow", return_value=202), \
                patch("ufo.agents.single_agent.computer.pyautogui.size", return_value=(100, 100)), \
                patch("ufo.agents.single_agent.computer.psutil.Process", return_value=SimpleNamespace(name=lambda: "test")):
            result = computer.observe(Path(directory) / "test.png")
        self.assertEqual([window["id"] for window in result.windows], ["w:202"])
        self.assertEqual(list(computer._window_map), ["w:202"])

    async def test_capture_restore_runs_even_if_observer_raises(self):
        runner = self.runner()
        calls = []
        runner.capture_callback = calls.append
        def fail(_path): raise ValueError("capture failed")
        runner.computer.observe = fail
        with self.assertRaises(ValueError): await runner._observe(Path("test.png"))
        self.assertEqual(calls, ["before", "after"])

    async def test_failed_capture_barrier_never_observes(self):
        runner = self.runner()
        observed = []
        def fail(_stage): raise RuntimeError("no UI ack")
        runner.capture_callback = fail
        runner.computer.observe = lambda p: observed.append(p)
        with self.assertRaises(RuntimeError): await runner._observe(Path("test.png"))
        self.assertFalse(observed)

    async def test_confirmation_reobserves_and_rejects_changed_target(self):
        runner = self.runner()
        runner._confirm_action = lambda a: True
        runner._observe = AsyncMock(return_value=observation(name="Delete"))
        action = SingleAgentAction(type="click_element", element_id="c:1", sensitive=True)
        result = await runner._execute_actions(SingleAgentResponse(observation="ready", thought="private", status="continue", actions=[action]))
        runner._observe.assert_awaited_once()
        self.assertFalse(result[0]["executed"])
        self.assertFalse(runner.executed)

    async def test_confirmation_uses_fresh_target_map_when_unchanged(self):
        runner = self.runner()
        runner._confirm_action = lambda a: True
        runner._observe = AsyncMock(return_value=observation())
        action = SingleAgentAction(type="click_element", element_id="c:1", sensitive=True)
        result = await runner._execute_actions(SingleAgentResponse(observation="ready", thought="private", status="continue", actions=[action]))
        self.assertTrue(result[0]["ok"])
        self.assertEqual(runner.executed, [action])

    async def test_event_sink_failure_cancels_task(self):
        runner = self.runner()
        def fail(event): raise RuntimeError("disconnected")
        runner.event_callback = fail
        with self.assertRaises(RuntimeError): runner._emit({"type": "phase"})
        self.assertTrue(runner.cancel_event.is_set())


if __name__ == "__main__":
    unittest.main()
