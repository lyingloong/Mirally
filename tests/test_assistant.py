import json
import queue
import sys
import tempfile
import threading
import time
import unittest
from types import ModuleType, SimpleNamespace
from pathlib import Path
from unittest.mock import Mock, patch

from openassist.assistant import Assistant, AssistantDecision
from openassist.conversation import ConversationStore
from openassist.task_controller import TaskController
from openassist.runtime.completion import ufo_completion


class AssistantProtocolTests(unittest.TestCase):
    def test_runtime_completion_uses_supported_ufo_api_and_app_model(self):
        llm_call = SimpleNamespace(get_completion=Mock(return_value=("response", 0.0)))
        agent_type = SimpleNamespace(APP="APP_AGENT")
        llm = ModuleType("ufo.llm")
        llm.AgentType = agent_type
        llm.llm_call = llm_call
        ufo = ModuleType("ufo")
        ufo.llm = llm

        with patch.dict(sys.modules, {"ufo": ufo, "ufo.llm": llm}), patch(
            "openassist.runtime.completion.bounded_model_services"
        ) as bounded:
            result = ufo_completion([{"role": "user", "content": "hello"}])

        self.assertEqual(result, "response")
        llm_call.get_completion.assert_called_once_with(
            [{"role": "user", "content": "hello"}], "APP_AGENT", False
        )
        bounded.assert_called_once()

    def test_answer_preserves_paragraphs_and_code(self):
        answer = "例子：\n\n```python\nif True:\n    print('你好')\n```"
        assistant = Assistant(lambda _: json.dumps({"mode": "reply", "message": answer}))
        self.assertEqual(assistant.respond("举个例子", {}).message, answer)

    def test_messages_preserve_conversation_roles_and_latest_additions(self):
        context = {"previous_tasks": [{"request": "写一封邀请函", "inputs": ["口语一些"],
                                        "result": "欢迎来参加！", "status": "completed"}]}
        messages = Assistant.messages("再短一点", context, ["20 字以内"])
        messages[3]["content"] = json.loads(messages[3]["content"])["message"]
        self.assertEqual([(m["role"], m["content"]) for m in messages[1:]], [
            ("user", "写一封邀请函"), ("user", "口语一些"), ("assistant", "欢迎来参加！"),
            ("user", "再短一点"), ("user", "20 字以内")])

    def test_invalid_output_retries_once_and_never_becomes_desktop(self):
        completion = Mock(side_effect=['{"mode":"click","message":"go"}', "not JSON"])
        with self.assertRaises(ValueError):
            Assistant(completion).respond("你好", {})
        self.assertEqual(completion.call_count, 2)

    def test_fenced_json_is_accepted_but_empty_messages_are_not(self):
        completion = Mock(return_value='```json\n{"mode":"reply","message":"你好！"}\n```')
        self.assertEqual(Assistant(completion).respond("你好", {}).mode, "reply")
        completion.return_value = '{"mode":"reply","message":""}'
        with self.assertRaises(ValueError):
            Assistant(completion).respond("你好", {})

    def test_format_retry_keeps_the_real_followup_as_last_user_message(self):
        calls = []
        def completion(messages):
            calls.append([dict(m) for m in messages])
            return "invalid" if len(calls) == 1 else '{"mode":"reply","message":"像俄罗斯套娃一样。"}'
        answer = Assistant(completion).respond("换一个生活例子", {})
        self.assertIn("套娃", answer.message)
        self.assertEqual(calls[1][-1], {"role": "user", "content": "换一个生活例子"})


class AssistantLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.controllers = []

    def controller(self, assistant, **kwargs):
        c = TaskController(assistant=assistant, **kwargs)
        self.controllers.append(c)
        return c

    def pump(self, c, condition, timeout=2):
        events = []
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            c.pulse()
            while True:
                try:
                    event = c.events.get_nowait()
                except queue.Empty:
                    break
                events.append(event)
                if event["type"] == "ui":
                    event["call"].done.set()
            if condition():
                return events
            time.sleep(0.005)
        self.fail(f"Timed out: {c.phase}")

    def tearDown(self):
        for c in self.controllers:
            c.stop()
            self.pump(c, lambda: not c.active)

    def test_greeting_and_question_never_construct_runner_or_touch_desktop(self):
        factory = Mock(side_effect=AssertionError("Desktop must not be constructed"))
        assistant = Mock(respond=Mock(return_value=AssistantDecision("reply", "你好！\n\n有什么想聊的？")))
        c = self.controller(assistant, runner_factory=factory)
        c.desktop_available = False  # Occupied hotkeys do not block conversation.
        for request in ("你好", "解释一下什么是递归"):
            c.start(request)
            events = self.pump(c, lambda: not c.active)
            self.assertEqual(c.phase, "completed")
            self.assertFalse(any(e["type"] == "ui" for e in events))
            self.assertEqual(c.conversation.snapshot()["tasks"][-1]["kind"], "conversation")
        factory.assert_not_called()
        self.assertEqual(assistant.respond.call_args.args[1]["previous_tasks"][0]["request"], "你好")

    def test_new_message_invalidates_old_reply_and_receipt_is_applied(self):
        waiting, release = threading.Event(), threading.Event()
        seen = []

        def respond(request, context, additions):
            seen.append(list(additions))
            if len(seen) == 1:
                waiting.set()
                release.wait(1)
                return AssistantDecision("desktop", "过期的执行请求")
            return AssistantDecision("reply", "好的，只解释步骤。")

        factory = Mock(side_effect=AssertionError("Stale handoff must not execute"))
        c = self.controller(Mock(respond=respond), runner_factory=factory)
        c.start("帮我整理文件")
        self.pump(c, waiting.is_set)
        self.assertTrue(c.begin_edit())
        self.assertFalse(c._editing, "Conversation must not acquire desktop ownership")
        self.assertTrue(c.supplement("先不要操作，只解释步骤"))
        release.set()
        events = self.pump(c, lambda: not c.active)
        self.assertEqual(seen, [[], ["先不要操作，只解释步骤"]])
        self.assertEqual([e["status"] for e in events if e["type"] == "receipt"], ["queued", "applied"])
        self.assertFalse(any(e["type"] == "ui" for e in events))
        factory.assert_not_called()

    def test_stop_during_response_does_not_start_late_desktop_handoff(self):
        waiting, release = threading.Event(), threading.Event()

        def respond(*args):
            waiting.set()
            release.wait(1)
            return AssistantDecision("desktop", "开始工作")

        factory = Mock(side_effect=AssertionError("Cancelled handoff"))
        c = self.controller(Mock(respond=respond), runner_factory=factory)
        c.start("打开记事本")
        self.pump(c, waiting.is_set)
        c.stop()
        release.set()
        self.pump(c, lambda: not c.active)
        self.assertEqual(c.phase, "cancelled")
        self.assertNotIn("开始工作", c.conversation.snapshot()["tasks"][-1]["result"])
        factory.assert_not_called()

    def test_desktop_handoff_inherits_prior_chat_and_original_request(self):
        received = []
        class Runner:
            def __init__(self, **kwargs): received.append(kwargs)
            async def run(self): return {"status": "done", "message": "已打开记事本"}

        assistant = Mock(respond=Mock(side_effect=[AssistantDecision("reply", "草稿内容"),
                                                   AssistantDecision("desktop", "我来打开记事本")]))
        c = self.controller(assistant, runner_factory=Runner)
        c.start("写一段介绍")
        self.pump(c, lambda: not c.active)
        c.start("把它写进记事本")
        events = self.pump(c, lambda: not c.active)
        self.assertEqual(received[0]["request"], "把它写进记事本")
        self.assertEqual(received[0]["conversation_context"]["previous_tasks"][0]["result"], "草稿内容")
        self.assertEqual([e["call"].operation for e in events if e["type"] == "ui"], ["working", "interactive"])

    def test_malformed_model_output_fails_without_desktop_fallback(self):
        factory = Mock(side_effect=AssertionError("No fallback to desktop"))
        c = self.controller(Assistant(lambda _: "invalid"), runner_factory=factory)
        with self.assertLogs("openassist.task_controller", level="ERROR"):
            c.start("你好")
            events = self.pump(c, lambda: not c.active)
        self.assertEqual(c.phase, "failed")
        self.assertFalse(any(e["type"] == "ui" for e in events))
        factory.assert_not_called()

    def test_chat_is_restored_with_paragraphs_and_full_visible_answer(self):
        with tempfile.TemporaryDirectory() as directory:
            answer = "第一段\n\n```python\nif True:\n    print('你好')\n```\n" + "后续解释。" * 600
            memory = ConversationStore(Path(directory))
            c = self.controller(Mock(respond=Mock(return_value=AssistantDecision("reply", answer))), conversation=memory)
            c.start("举一个例子")
            self.pump(c, lambda: not c.active)
            restored = ConversationStore(Path(directory))
            self.assertEqual(restored.snapshot()["tasks"][0]["result"], answer)
            self.assertIn("\n    print", restored.context()["previous_tasks"][0]["result"])


if __name__ == "__main__":
    unittest.main()
