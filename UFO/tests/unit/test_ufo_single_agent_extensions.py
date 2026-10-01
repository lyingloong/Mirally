import unittest
from pathlib import Path
from unittest.mock import patch

from ufo.agents.single_agent.loop_guard import LoopGuard
from ufo.agents.single_agent.runner import SingleAgentRunner
from ufo.agents.single_agent.schemas import DesktopObservation, SingleAgentAction
from ufo.agents.single_agent.skill_router import SkillRouter


def _observation(
    *,
    window_name: str = "Editor",
    process: str = "editor.exe",
    control_name: str = "Save",
) -> DesktopObservation:
    active = {
        "id": "w:1",
        "name": window_name,
        "process": process,
        "rect": [0, 0, 1000, 700],
        "active": True,
    }
    return DesktopObservation(
        windows=[active],
        active_window=active,
        controls=[
            {
                "id": "c:1",
                "name": control_name,
                "type": "Button",
                "rect": [10, 10, 80, 40],
                "selected": False,
            }
        ],
        screenshot="data:image/png;base64,",
        screenshot_path="missing-test-screenshot.png",
    )


class LoopGuardTests(unittest.TestCase):
    def test_third_identical_action_on_unchanged_ui_is_blocked(self):
        guard = LoopGuard(
            repeat_limit=3,
            stagnant_limit=10,
            max_blocked_attempts=2,
        )
        action = SingleAgentAction(type="click_element", element_id="c:1")

        guard.observe(_observation())
        self.assertFalse(guard.assess([action]).blocked)
        guard.record([action])

        guard.observe(_observation())
        self.assertFalse(guard.assess([action]).blocked)
        guard.record([action])

        guard.observe(_observation())
        decision = guard.assess([action])

        self.assertTrue(decision.blocked)
        self.assertFalse(decision.terminal)
        self.assertEqual(decision.reason, "repeated_action_without_target_progress")
        self.assertEqual(decision.repeat_count, 3)
        self.assertIsNotNone(guard.prompt_context())

        second_block = guard.assess([action])
        self.assertTrue(second_block.terminal)

    def test_stagnant_ui_requests_a_different_route(self):
        guard = LoopGuard(repeat_limit=10, stagnant_limit=2)
        actions = [
            SingleAgentAction(type="press_keys", keys=["TAB"]),
            SingleAgentAction(type="press_keys", keys=["ENTER"]),
            SingleAgentAction(type="scroll", amount=-2),
        ]

        guard.observe(_observation())
        self.assertFalse(guard.assess([actions[0]]).blocked)
        guard.record([actions[0]])
        guard.observe(_observation())
        self.assertFalse(guard.assess([actions[1]]).blocked)
        guard.record([actions[1]])
        guard.observe(_observation())

        decision = guard.assess([actions[2]])

        self.assertTrue(decision.blocked)
        self.assertEqual(decision.reason, "no_ui_progress")

    def test_ui_change_resets_stagnation(self):
        guard = LoopGuard(repeat_limit=10, stagnant_limit=2)
        guard.observe(_observation())
        guard.observe(_observation())

        state = guard.observe(_observation(window_name="Different page"))

        self.assertTrue(state["changed"])
        self.assertEqual(state["unchanged_observations"], 0)


class SkillRouterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        skill_path = (
            Path(__file__).resolve().parents[2] / "ufo" / "skills" / "single_agent"
        )
        cls.router = SkillRouter(skill_path, top_k=2)

    def test_selects_wechat_skill_using_keywords(self):
        observation = _observation(window_name="微信", process="WeChat.exe")

        matches = self.router.select("给王淇辉发送一条微信测试消息", observation)

        self.assertEqual(matches[0].skill.id, "wechat-messaging")
        self.assertIn("微信", matches[0].matched_keywords)

    def test_selects_web_skill_without_a_model_router(self):
        observation = _observation(
            window_name="Microsoft Edge", process="msedge.exe"
        )

        matches = self.router.select("在浏览器登录测试网站", observation)

        self.assertEqual(matches[0].skill.id, "web-navigation")
        self.assertIn("msedge.exe", matches[0].matched_applications)

    def test_unrelated_request_does_not_inject_a_skill(self):
        matches = self.router.select("打开计算器", _observation())

        self.assertEqual(matches, [])


class SingleAgentExtensionIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_selected_skill_is_added_to_decision_context(self):
        with patch("ufo.agents.single_agent.runner.ConsoleWindow"):
            runner = SingleAgentRunner(
                request="在浏览器登录测试网站",
                task="single-agent-skill-context-test",
                keep_console=True,
                max_steps=1,
                computer=object(),
            )
        observation = _observation(
            window_name="Microsoft Edge", process="msedge.exe"
        )
        matches = runner.skill_router.select(runner.request, observation)
        captured = {}

        def fake_completion(messages, agent_type, use_backup_engine):
            captured["messages"] = messages
            return (
                '{"observation":"Page visible","thought":"Done",'
                '"actions":[],"status":"done","message":"Done",'
                '"question":null}',
                0.0,
                {"duration_seconds": 0.01},
            )

        with patch(
            "ufo.agents.single_agent.runner.llm_call.get_completion_with_metrics",
            side_effect=fake_completion,
        ):
            response, _, _ = await runner._decide(
                1,
                observation,
                selected_skills=matches,
            )

        prompt_text = captured["messages"][1]["content"][-1]["text"]
        self.assertEqual(response.status, "done")
        self.assertIn('"selected_skills"', prompt_text)
        self.assertIn('"id": "web-navigation"', prompt_text)


if __name__ == "__main__":
    unittest.main()
