"""Regression coverage for targeted entry and changing verification countdowns."""

import json
import tempfile
import threading
import unittest
from itertools import count
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from ufo.agents.single_agent.computer import WindowsComputer
from ufo.agents.single_agent.loop_guard import LoopGuard
from ufo.agents.single_agent.runner import SingleAgentRunner
from ufo.agents.single_agent.schemas import DesktopObservation, SingleAgentAction, SingleAgentResponse


def form_observation(countdown=15, *, field_id="c:1", focused=True, token="empty", stable_id="uia:field"):
    window = {"id": "w:42", "name": "Verification", "process": "browser.exe", "active": True}
    return DesktopObservation(
        windows=[window], active_window=window, controls=[
            {"id": field_id, "stable_id": stable_id, "name": "Verification code", "type": "Edit",
             "rect": [100, 100, 250, 130], "focused": focused, "enabled": True, "editable": True},
            {"id": "c:resend", "name": f"Resend ({countdown}s)", "type": "Button",
             "rect": [260, 100, 360 + countdown, 130], "enabled": False},
        ], screenshot="data:image/png;base64,", screenshot_path="no-test-image.png",
        target_states={field_id: {"value_token": token, "empty": token == "empty"}},
    )


class TargetProgressTests(unittest.TestCase):
    def submission_observation(self, countdown=15, *, token="empty", enabled=True):
        observation = form_observation(countdown, token=token)
        observation.controls.append({
            "id": "c:submit", "stable_id": "uia:submit", "name": "Confirm",
            "type": "Button", "enabled": enabled, "rect": [100, 200, 150, 230],
        })
        return observation

    def test_submission_is_not_retried_due_to_a_resend_countdown(self):
        guard = LoopGuard(stagnant_limit=100)
        action = SingleAgentAction(type="click_element", element_id="c:submit")
        for seconds in (15, 10):
            guard.observe(self.submission_observation(seconds))
            self.assertFalse(guard.assess([action]).blocked)
            guard.record([action])
        guard.observe(self.submission_observation(6))
        self.assertTrue(guard.assess([action]).blocked)

    def test_changed_form_allows_submission_but_loading_button_requires_wait(self):
        guard = LoopGuard(stagnant_limit=100)
        action = SingleAgentAction(type="click_element", element_id="c:submit")
        for seconds in (15, 10):
            guard.observe(self.submission_observation(seconds))
            guard.record([action])
        guard.observe(self.submission_observation(6, token="updated"))
        self.assertFalse(guard.assess([action]).blocked)
        guard.record([action])
        guard.observe(self.submission_observation(1, token="updated", enabled=False))
        decision = guard.assess([action])
        self.assertTrue(decision.blocked)
        self.assertEqual(decision.reason, "target_disabled")

    def test_unreadable_fill_requires_verification_instead_of_endless_retyping(self):
        guard = LoopGuard(stagnant_limit=100)
        action = SingleAgentAction(type="fill_text", element_id="c:1", text="654321")
        for seconds in (15, 10):
            guard.observe(form_observation(seconds))
            guard.record([action], results=[{"ok": True, "verified": None}])
        state = guard.observe(form_observation(6))
        self.assertEqual(state["target_progress"][0]["outcome"], "unverified")
        self.assertTrue(guard.assess([action]).blocked)

    def test_countdown_and_temporary_ids_do_not_hide_repeated_field_clicks(self):
        for stable_id in ("uia:field", None):
            with self.subTest(stable_id=stable_id):
                guard = LoopGuard(stagnant_limit=100)
                for index, seconds in enumerate((15, 10)):
                    field_id = f"c:{index + 1}"
                    guard.observe(form_observation(seconds, field_id=field_id, stable_id=stable_id))
                    action = SingleAgentAction(type="click_element", element_id=field_id)
                    self.assertFalse(guard.assess([action]).blocked)
                    guard.record([action], results=[{"ok": True, "executed": True}])
                guard.observe(form_observation(6, field_id="c:9", stable_id=stable_id))
                decision = guard.assess([SingleAgentAction(type="click_element", element_id="c:9")])
                self.assertTrue(decision.blocked)
                self.assertEqual(decision.reason, "repeated_action_without_target_progress")
                self.assertIn("fill_text", decision.recovery_instruction)

    def test_gaining_focus_counts_as_progress_once(self):
        guard = LoopGuard(stagnant_limit=100)
        action = SingleAgentAction(type="click_element", element_id="c:1")
        guard.observe(form_observation(focused=False))
        guard.record([action])
        state = guard.observe(form_observation(10, focused=True))
        self.assertEqual(state["target_progress"][0]["no_progress_attempts"], 0)
        for seconds in (10, 6):
            self.assertFalse(guard.assess([action]).blocked)
            guard.record([action])
            guard.observe(form_observation(seconds, focused=True))
        self.assertTrue(guard.assess([action]).blocked)

    def test_changed_field_content_allows_a_fresh_action(self):
        guard = LoopGuard(stagnant_limit=100)
        action = SingleAgentAction(type="click_element", element_id="c:1")
        for seconds in (15, 10):
            guard.observe(form_observation(seconds))
            guard.record([action])
        guard.observe(form_observation(6, token="new-content"))
        self.assertFalse(guard.assess([action]).blocked)

    def test_failed_fill_does_not_count_wrong_content_as_success(self):
        guard = LoopGuard(stagnant_limit=100)
        action = SingleAgentAction(type="fill_text", element_id="c:1", text="654321")
        for index in range(2):
            guard.observe(form_observation(15 - index, token=f"wrong-{index}"))
            self.assertFalse(guard.assess([action]).blocked)
            guard.record([action], results=[{"ok": False, "verified": False, "executed": True}])
        guard.observe(form_observation(10, token="wrong-2"))
        self.assertTrue(guard.assess([action]).blocked)

    def test_rejected_action_is_not_counted_as_an_attempt(self):
        guard = LoopGuard()
        action = SingleAgentAction(type="fill_text", element_id="c:1", text="654321")
        for seconds in (15, 10, 6):
            guard.observe(form_observation(seconds))
            guard.record([action], results=[{"ok": False, "executed": False}])
        guard.observe(form_observation(1))
        self.assertFalse(guard.assess([action]).blocked)

    def test_verified_fill_and_page_changes_reset_attempts(self):
        guard = LoopGuard(stagnant_limit=100)
        action = SingleAgentAction(type="fill_text", element_id="c:1", text="654321")
        guard.observe(form_observation())
        guard.record([action], results=[{"ok": False, "verified": False}])
        guard.observe(form_observation(10))
        guard.record([action], results=[{"ok": True, "verified": True}])
        state = guard.observe(form_observation(6, token="matched"))
        self.assertEqual(state["target_progress"][0]["outcome"], "verified")
        self.assertFalse(guard.assess([action]).blocked)
        guard.record([action])
        next_page = form_observation(1, stable_id="uia:another-field")
        state = guard.observe(next_page)
        self.assertEqual(state["target_progress"][0]["outcome"], "unknown_target_changed")

    def test_value_tokens_are_runtime_only(self):
        observation = form_observation(token="private-value-token")
        self.assertNotIn("private-value-token", json.dumps(observation.prompt_state()))
        self.assertNotIn("private-value-token", observation.model_dump_json())


class _Edit:
    def __init__(self, *, value="", focus_allowed=True, protected=False):
        self.value = value
        self.focused = False
        self.focus_allowed = focus_allowed
        self.clicks = 0
        self.value_reads = 0
        self.element_info = SimpleNamespace(
            control_type="Edit", element=SimpleNamespace(CurrentIsPassword=protected)
        )

    def is_enabled(self):
        return True

    def is_visible(self):
        return True

    def is_editable(self):
        return True

    def get_value(self):
        self.value_reads += 1
        return self.value

    def has_keyboard_focus(self):
        return self.focused

    def click_input(self):
        self.clicks += 1
        self.focused = self.focus_allowed


class FillTextTests(unittest.TestCase):
    def make_computer(self, control):
        computer = WindowsComputer.__new__(WindowsComputer)
        computer._control_map = {"c:1": control}
        computer._observed_foreground = 42
        computer._config = SimpleNamespace(system=SimpleNamespace(input_text_inter_key_pause=0))
        return computer

    def test_fills_and_verifies_once_then_skips_the_matching_value(self):
        edit = _Edit(value="old")
        computer = self.make_computer(edit)
        action = SingleAgentAction(type="fill_text", element_id="c:1", text="654321")
        def send_keys(text, **kwargs):
            edit.value = "" if text == WindowsComputer._key_stroke("CTRL+A") else text
        with patch("ufo.agents.single_agent.computer.win32gui.GetForegroundWindow", return_value=42), patch(
            "ufo.agents.single_agent.computer.keyboard.send_keys", side_effect=send_keys
        ) as keyboard:
            result = computer.execute(action)
            self.assertTrue(result["ok"])
            self.assertTrue(result["verified"])
            repeat = computer.execute(action)
            self.assertFalse(repeat["executed"])
            self.assertEqual(keyboard.call_count, 2)
            self.assertEqual(edit.clicks, 1)
            self.assertEqual(edit.value, "654321")

    def test_unverified_focus_sends_no_keyboard_input(self):
        edit = _Edit(focus_allowed=False)
        ticks = count()
        with patch("ufo.agents.single_agent.computer.win32gui.GetForegroundWindow", return_value=42), patch(
            "ufo.agents.single_agent.computer.keyboard.send_keys"
        ) as keyboard, patch("ufo.agents.single_agent.computer.time.monotonic", side_effect=lambda: next(ticks)):
            result = self.make_computer(edit).execute(SingleAgentAction(type="fill_text", element_id="c:1", text="654321"))
        self.assertFalse(result["ok"])
        self.assertFalse(result["typed"])
        keyboard.assert_not_called()

    def test_changed_foreground_sends_no_click_or_keys(self):
        edit = _Edit()
        with patch("ufo.agents.single_agent.computer.win32gui.GetForegroundWindow", return_value=99), patch(
            "ufo.agents.single_agent.computer.keyboard.send_keys"
        ) as keyboard:
            result = self.make_computer(edit).execute(SingleAgentAction(type="fill_text", element_id="c:1", text="654321"))
        self.assertFalse(result["executed"])
        self.assertEqual(edit.clicks, 0)
        keyboard.assert_not_called()

    def test_wrong_content_is_not_reported_as_success(self):
        edit = _Edit()
        ticks = count()
        with patch("ufo.agents.single_agent.computer.win32gui.GetForegroundWindow", return_value=42), patch(
            "ufo.agents.single_agent.computer.keyboard.send_keys"
        ), patch("ufo.agents.single_agent.computer.time.monotonic", side_effect=lambda: next(ticks)):
            result = self.make_computer(edit).execute(SingleAgentAction(type="fill_text", element_id="c:1", text="654321"))
        self.assertFalse(result["ok"])
        self.assertFalse(result["verified"])

    def test_protected_fields_are_not_read(self):
        edit = _Edit(protected=True)
        self.assertIsNone(WindowsComputer._read_value(edit))
        self.assertEqual(edit.value_reads, 0)

    def test_unknown_target_is_reported_as_not_executed(self):
        computer = self.make_computer(_Edit())
        action = SingleAgentAction(type="click_element", element_id="c:missing")
        result = computer.execute(action)
        feedback = SingleAgentRunner._execution_feedback([action], [result])
        self.assertFalse(result["executed"])
        self.assertEqual(feedback["executed_actions"], [])
        self.assertEqual(len(feedback["not_executed_actions"]), 1)

    def test_input_error_reports_unknown_partial_effects(self):
        edit = _Edit()
        edit.click_input = Mock(side_effect=RuntimeError("OS input error"))
        action = SingleAgentAction(type="click_element", element_id="c:1")
        result = self.make_computer(edit).execute(action)
        feedback = SingleAgentRunner._execution_feedback([action], [result])
        self.assertIsNone(result["executed"])
        self.assertEqual(feedback["executed_actions"], [])
        self.assertEqual(feedback["not_executed_actions"], [])
        self.assertEqual(len(feedback["unverified_actions"]), 1)

    def test_losing_focus_after_select_all_sends_no_text(self):
        edit = _Edit()
        def lose_focus(text, **kwargs):
            edit.focused = False
        with patch("ufo.agents.single_agent.computer.win32gui.GetForegroundWindow", return_value=42), patch(
            "ufo.agents.single_agent.computer.keyboard.send_keys", side_effect=lose_focus
        ) as keyboard:
            result = self.make_computer(edit).execute(SingleAgentAction(type="fill_text", element_id="c:1", text="654321"))
        self.assertFalse(result["ok"])
        self.assertFalse(result["typed"])
        self.assertEqual(keyboard.call_count, 1)
        self.assertEqual(edit.value, "")


class RunnerFormTests(unittest.IsolatedAsyncioTestCase):
    async def test_legacy_click_type_is_filled_before_submission_can_run(self):
        actions = [
            SingleAgentAction(type="click_element", element_id="c:1"),
            SingleAgentAction(type="type_text", text="654321", sensitive=True),
            SingleAgentAction(type="press_keys", keys=["ENTER"], sensitive=True),
        ]
        plan = SingleAgentRunner._prepare_actions(actions, form_observation())
        self.assertEqual([action.type for action in plan], ["fill_text", "press_keys"])
        self.assertTrue(plan[0].sensitive)
        runner = SingleAgentRunner.__new__(SingleAgentRunner)
        runner.cancel_event = threading.Event()
        runner.config = SimpleNamespace(system=SimpleNamespace(safe_guard=False))
        executed = []
        runner.computer = SimpleNamespace(
            BARRIER_ACTIONS=WindowsComputer.BARRIER_ACTIONS,
            execute=lambda action: executed.append(action.type) or {"ok": True, "action": action.type, "verified": True},
        )
        response = SingleAgentResponse(observation="Form", thought="Fill", actions=plan, status="continue")
        results = await runner._execute_actions(response)
        feedback = runner._execution_feedback(plan, results)
        self.assertEqual(executed, ["fill_text"])
        self.assertEqual(feedback["executed_actions"][0]["type"], "fill_text")
        self.assertEqual(feedback["not_executed_actions"][0]["action"]["type"], "press_keys")
        self.assertIn("not executed", feedback["not_executed_actions"][0]["reason"])

    async def test_button_click_is_not_converted_to_form_entry(self):
        actions = [SingleAgentAction(type="click_element", element_id="c:resend"), SingleAgentAction(type="type_text", text="654321")]
        self.assertEqual(SingleAgentRunner._prepare_actions(actions, form_observation()), actions)

    async def test_legacy_append_plan_does_not_overwrite_a_nonempty_field(self):
        actions = [SingleAgentAction(type="click_element", element_id="c:1"), SingleAgentAction(type="type_text", text="extra")]
        self.assertEqual(SingleAgentRunner._prepare_actions(actions, form_observation(token="existing")), actions)

    async def test_user_answer_survives_rolling_history(self):
        with patch("ufo.agents.single_agent.runner.ConsoleWindow"), tempfile.TemporaryDirectory() as temp:
            computer = SimpleNamespace(BARRIER_ACTIONS=WindowsComputer.BARRIER_ACTIONS,
                observe=lambda path: form_observation(), execute=lambda action: {"ok": True, "action": action.type})
            runner = SingleAgentRunner(request="Fill verification", task="form-memory-regression", computer=computer,
                input_callback=lambda question: "654321", max_steps=9)
            runner.event_log = Path(temp) / "events.jsonl"
            runner.log_dir = Path(temp)
            runner.loop_guard = None
            runner.skill_router = None
            decisions = [SingleAgentResponse(observation="Form", thought="Ask", actions=[], status="need_user", question="Code?")]
            decisions += [SingleAgentResponse(observation="Form", thought="Observe", actions=[SingleAgentAction(type="wait", seconds=0.1)], status="continue") for _ in range(7)]
            decisions += [SingleAgentResponse(observation="Form", thought="Finished", actions=[], status="done")]
            captured = []
            def completion(messages, *args):
                captured.append(json.loads(messages[1]["content"][-1]["text"]))
                return decisions[len(captured) - 1].model_dump_json(), 0, {}
            with patch("ufo.agents.single_agent.runner.llm_call.get_completion_with_metrics", side_effect=completion), patch(
                "ufo.agents.single_agent.runner.asyncio.sleep", new=AsyncMock()
            ):
                result = await runner.run()
            self.assertEqual(result["status"], "done")
            self.assertNotIn("answer", json.dumps(captured[-1]["recent_history"]))
            self.assertEqual(captured[-1]["user_answers"], [{"question": "Code?", "answer": "654321"}])


if __name__ == "__main__":
    unittest.main()
