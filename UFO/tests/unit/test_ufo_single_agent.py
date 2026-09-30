import unittest
from unittest.mock import patch

from pydantic import ValidationError

from ufo.agents.single_agent.computer import ConsoleWindow, WindowsComputer
from ufo.agents.single_agent.runner import SingleAgentRunner
from ufo.agents.single_agent.schemas import (
    SingleAgentAction,
    SingleAgentResponse,
)
from ufo.llm import AgentType
from ufo.llm.config_helper import get_agent_config


class _FakeComputer:
    BARRIER_ACTIONS = {"focus_window", "click_element", "click", "scroll"}

    def __init__(self):
        self.executed = []

    def execute(self, action):
        self.executed.append(action.type)
        return {"ok": True, "action": action.type}


class SingleAgentSchemaTests(unittest.TestCase):
    def test_continue_requires_actions(self):
        with self.assertRaises(ValidationError):
            SingleAgentResponse(
                observation="Desktop visible",
                thought="Need to act",
                actions=[],
                status="continue",
            )

    def test_terminal_status_rejects_actions(self):
        with self.assertRaises(ValidationError):
            SingleAgentResponse(
                observation="Done",
                thought="Finished",
                actions=[SingleAgentAction(type="press_keys", keys=["ENTER"])],
                status="done",
            )

    def test_action_requires_its_arguments(self):
        with self.assertRaises(ValidationError):
            SingleAgentAction(type="click_element")


class SingleAgentKeyboardTests(unittest.TestCase):
    def test_key_strokes_are_converted_from_restricted_notation(self):
        self.assertEqual(WindowsComputer._key_stroke("ENTER"), "{ENTER}")
        self.assertEqual(
            WindowsComputer._key_stroke("CTRL+L"),
            "{VK_CONTROL down}l{VK_CONTROL up}",
        )
        self.assertEqual(WindowsComputer._key_stroke("WIN"), "{VK_LWIN}")

    def test_arbitrary_send_keys_syntax_is_rejected(self):
        with self.assertRaises(ValueError):
            WindowsComputer._key_stroke("{VK_CONTROL down}")

    def test_type_text_escapes_key_command_syntax(self):
        escaped = WindowsComputer._literal_text("show {ENTER} + ^")

        self.assertEqual(escaped, "show {{}ENTER{}} {+} {^}")


class SingleAgentPerceptionTests(unittest.TestCase):
    def test_empty_container_and_offscreen_controls_are_filtered(self):
        self.assertFalse(
            WindowsComputer._is_relevant_control(
                {"control_text": "", "control_type": "Group", "control_rect": (1, 1, 20, 20)},
                1920,
                1080,
            )
        )
        self.assertFalse(
            WindowsComputer._is_relevant_control(
                {"control_text": "Hidden", "control_type": "Button", "control_rect": (2000, 1, 2100, 20)},
                1920,
                1080,
            )
        )
        self.assertTrue(
            WindowsComputer._is_relevant_control(
                {"control_text": "", "control_type": "Button", "control_rect": (1, 1, 20, 20)},
                1920,
                1080,
            )
        )


class ConsoleWindowTests(unittest.TestCase):
    def test_console_handle_comes_from_win32console(self):
        with patch(
            "ufo.agents.single_agent.computer.win32console.GetConsoleWindow",
            return_value=123,
        ) as get_console_window:
            window = ConsoleWindow(enabled=True)

        self.assertEqual(window.handle, 123)
        get_console_window.assert_called_once_with()


class SingleAgentExecutionTests(unittest.IsolatedAsyncioTestCase):
    def make_runner(self, computer):
        with patch(
            "ufo.agents.single_agent.runner.ConsoleWindow"
        ) as console_window:
            console_window.return_value.hide.return_value = None
            console_window.return_value.show.return_value = None
            return SingleAgentRunner(
                request="test",
                task="single-agent-test",
                keep_console=True,
                max_steps=2,
                computer=computer,
            )

    async def test_keyboard_sequence_executes_as_one_batch(self):
        computer = _FakeComputer()
        runner = self.make_runner(computer)
        response = SingleAgentResponse(
            observation="App is closed",
            thought="Use Windows search",
            actions=[
                SingleAgentAction(type="press_keys", keys=["WIN"]),
                SingleAgentAction(type="type_text", text="WeChat"),
                SingleAgentAction(type="press_keys", keys=["ENTER"]),
            ],
            status="continue",
        )

        results = await runner._execute_actions(response)

        self.assertEqual(computer.executed, ["press_keys", "type_text", "press_keys"])
        self.assertEqual(len(results), 3)

    async def test_click_is_an_observation_barrier(self):
        computer = _FakeComputer()
        runner = self.make_runner(computer)
        response = SingleAgentResponse(
            observation="Button visible",
            thought="Click it and observe again",
            actions=[
                SingleAgentAction(type="click_element", element_id="c:1"),
                SingleAgentAction(type="type_text", text="must not execute yet"),
            ],
            status="continue",
        )

        results = await runner._execute_actions(response)

        self.assertEqual(computer.executed, ["click_element"])
        self.assertEqual(len(results), 1)


class SingleAgentConfigurationTests(unittest.TestCase):
    def test_single_agent_reuses_app_agent_model_configuration(self):
        single = get_agent_config(AgentType.SINGLE)
        app = get_agent_config(AgentType.APP)

        self.assertEqual(single["API_TYPE"], app["API_TYPE"])
        self.assertEqual(single["API_BASE"], app["API_BASE"])
        self.assertEqual(single["API_MODEL"], app["API_MODEL"])


if __name__ == "__main__":
    unittest.main()
