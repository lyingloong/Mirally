import unittest
from unittest.mock import patch

from aip.messages import Result, ResultStatus

from ufo.agents.processors.context.app_agent_processing_context import (
    AppAgentProcessorContext,
)
from ufo.agents.processors.context.processing_context import ProcessingContext
from ufo.agents.processors.schemas.actions import ActionCommandInfo
from ufo.agents.processors.schemas.response_schema import AppAgentResponse
# Match the application's import order and initialize the processor framework first.
from ufo.agents.processors import app_agent_processor as _app_agent_processor  # noqa: F401
from ufo.agents.processors.strategies.app_agent_processing_strategy import (
    AppActionExecutionStrategy,
    AppControlInfoStrategy,
    AppLLMInteractionStrategy,
    ufo_config,
)
from ufo.module.context import Context


class _Dispatcher:
    def __init__(self, events=None, result=None):
        self.events = events if events is not None else []
        self.result = result

    async def execute_commands(self, commands):
        self.events.append("execute")
        if self.result is not None:
            return self.result
        return [Result(status=ResultStatus.SUCCESS, result="ok") for _ in commands]


def _confirmation_context(dispatcher):
    action = ActionCommandInfo(
        function="keyboard_input",
        status="CONFIRM",
        arguments={"keys": "{ENTER}"},
    )
    local = AppAgentProcessorContext(
        parsed_response=AppAgentResponse(
            observation="ready",
            thought="send",
            action=action,
        ),
        log_path="logs/test/",
        session_step=1,
        annotation_dict={},
        clean_screenshot_path="screen.png",
        application_process_name="WeChat",
    )
    return ProcessingContext(
        global_context=Context(command_dispatcher=dispatcher),
        local_context=local,
    )


class AppActionConfirmationTests(unittest.IsolatedAsyncioTestCase):
    async def test_sensitive_action_is_confirmed_before_execution(self):
        events = []
        dispatcher = _Dispatcher(events=events)
        context = _confirmation_context(dispatcher)
        strategy = AppActionExecutionStrategy()
        previous_safe_guard = ufo_config.system.safe_guard
        ufo_config.system.safe_guard = True

        def approve(*_args):
            events.append("confirm")
            return True

        try:
            with (
                patch.object(strategy, "_save_annotated_screenshot"),
                patch(
                    "ufo.agents.processors.strategies.app_agent_processing_strategy."
                    "interactor.sensitive_step_asker",
                    side_effect=approve,
                ),
            ):
                result = await strategy.execute(None, context)
        finally:
            ufo_config.system.safe_guard = previous_safe_guard

        self.assertTrue(result.success)
        self.assertEqual(events, ["confirm", "execute"])
        self.assertEqual(result.data["status"], "CONTINUE")

    async def test_rejected_sensitive_action_is_not_executed(self):
        events = []
        dispatcher = _Dispatcher(events=events)
        context = _confirmation_context(dispatcher)
        strategy = AppActionExecutionStrategy()
        previous_safe_guard = ufo_config.system.safe_guard
        ufo_config.system.safe_guard = True

        def reject(*_args):
            events.append("confirm")
            return False

        try:
            with (
                patch.object(strategy, "_save_annotated_screenshot"),
                patch(
                    "ufo.agents.processors.strategies.app_agent_processing_strategy."
                    "interactor.sensitive_step_asker",
                    side_effect=reject,
                ),
            ):
                result = await strategy.execute(None, context)
        finally:
            ufo_config.system.safe_guard = previous_safe_guard

        self.assertTrue(result.success)
        self.assertEqual(events, ["confirm"])
        self.assertEqual(result.data["status"], "FINISH")
        self.assertEqual(
            result.data["execution_result"][0].status,
            ResultStatus.SKIPPED,
        )


class AppPerceptionTests(unittest.IsolatedAsyncioTestCase):
    async def test_none_uia_result_is_an_empty_control_list(self):
        strategy = AppControlInfoStrategy()
        dispatcher = _Dispatcher(result=[Result(status=ResultStatus.SUCCESS, result=None)])

        controls = await strategy._collect_uia_controls(dispatcher)

        self.assertEqual(controls, [])

    def test_no_controls_sends_only_clean_screenshot(self):
        strategy = AppLLMInteractionStrategy()
        previous_include_last = ufo_config.system.include_last_screenshot
        ufo_config.system.include_last_screenshot = False

        try:
            with patch(
                "ufo.agents.processors.strategies.app_agent_processing_strategy."
                "PhotographerFacade"
            ) as photographer_cls:
                photographer = photographer_cls.return_value
                photographer.encode_image_from_path.side_effect = lambda path: path

                images = strategy._collect_image_strings(
                    "last.png",
                    "clean.png",
                    "annotated.png",
                    "concat.png",
                    has_annotated_controls=False,
                )
        finally:
            ufo_config.system.include_last_screenshot = previous_include_last

        self.assertEqual(images, ["clean.png"])
        photographer.concat_screenshots.assert_not_called()


if __name__ == "__main__":
    unittest.main()
