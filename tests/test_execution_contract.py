import ast
import asyncio
import queue
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock

from mirally.core.assistant import AssistantDecision
from mirally.core.execution import (
    ExecutionCancelled, ExecutionCapabilities, ExecutionRequest, Reobserve, TaskResult,
)
from mirally.core.task_controller import TaskController, TaskStopped


class ExecutionContractTests(unittest.TestCase):
    def setUp(self):
        self.controllers = []

    def controller(self, executor):
        value = TaskController(executor=executor, assistant=Mock(
            respond=Mock(return_value=AssistantDecision("desktop", "开始处理"))))
        self.controllers.append(value)
        return value

    def pump(self, controller, until, timeout=2):
        events = []
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            controller.pulse()
            while True:
                try:
                    event = controller.events.get_nowait()
                except queue.Empty:
                    break
                events.append(event)
                if event["type"] == "ui":
                    event["call"].done.set()
            if until():
                return events
            time.sleep(.005)
        self.fail(f"Timed out: {controller.phase}")

    def tearDown(self):
        for controller in self.controllers:
            controller.stop()
            self.pump(controller, lambda: not controller.active)

    def test_native_and_fake_use_request_services_and_structured_result(self):
        received = []

        class Executor:
            capabilities = ExecutionCapabilities(pause=True, supplement=True)

            async def run(self, request, services):
                received.append((request, services))
                services.event({"type": "phase", "phase": "executing", "action": "set_edit_text"})
                return TaskResult("partial", "正文\n\n仅第一份已完成", reason="step_limit")

        controller = self.controller(Executor())
        controller.start("处理两份文件")
        events = self.pump(controller, lambda: not controller.active)
        self.assertIsInstance(received[0][0], ExecutionRequest)
        self.assertEqual(received[0][0].text, "处理两份文件")
        self.assertEqual(controller.phase, "partial")
        self.assertEqual(controller.conversation.snapshot()["tasks"][-1]["result"], "正文\n\n仅第一份已完成")
        self.assertTrue(any(event.get("phase") == "executing" for event in events))

    def test_stop_remains_pending_until_executor_releases_external_work(self):
        waiting, release = threading.Event(), threading.Event()

        class Executor:
            capabilities = ExecutionCapabilities()

            async def run(self, request, services):
                waiting.set()
                await asyncio.to_thread(release.wait, 1)
                return TaskResult("cancelled")

        controller = self.controller(Executor())
        controller.start("test")
        self.pump(controller, waiting.is_set)
        controller.stop()
        self.assertEqual(controller.phase, "stopping")
        self.assertTrue(controller.active)
        with self.assertRaises(RuntimeError):
            controller.start("overlap")
        release.set()
        self.pump(controller, lambda: not controller.active)
        self.assertEqual(controller.phase, "cancelled")

    def test_invalid_executor_result_finishes_as_failure(self):
        class Executor:
            capabilities = ExecutionCapabilities()

            async def run(self, request, services):
                return {"status": "done"}  # Only the legacy wrapper accepts this.

        controller = self.controller(Executor())
        with self.assertLogs("mirally.task_controller", level="ERROR"):
            controller.start("test")
            self.pump(controller, lambda: not controller.active)
        self.assertEqual(controller.phase, "failed")

    def test_confirmation_does_not_invalidate_the_approved_plan(self):
        revisions, decisions = [], []

        class Executor:
            capabilities = ExecutionCapabilities(pause=True, supplement=True)

            async def run(self, request, services):
                revisions.append(services.revision())
                decisions.append(services.confirm("保存", "文档"))
                revisions.append(services.revision())
                return TaskResult("completed")

        controller = self.controller(Executor())
        controller.start("test")
        self.pump(controller, lambda: controller.phase == "waiting_confirmation")
        controller.reply(controller._pending_id, True)
        self.pump(controller, lambda: not controller.active)
        self.assertEqual(decisions, [True])
        self.assertEqual(revisions, [0, 0])

    def test_stale_plan_cannot_acquire_desktop_gate(self):
        controller = self.controller(Mock(capabilities=ExecutionCapabilities()))
        controller._revision = 2
        with self.assertRaises(Reobserve):
            controller.desktop_access("before", expected_revision=1)
        self.assertFalse(controller._desktop_busy)
        self.assertEqual(controller.desktop_access("before", expected_revision=2), 2)
        self.assertTrue(controller._desktop_busy)
        controller.desktop_access("after")
        controller.cancel_event.set()
        with self.assertRaises(ExecutionCancelled):
            controller.desktop_access("before", expected_revision=2)

    def test_receipt_ack_applies_only_explicit_ids(self):
        controller = self.controller(Mock(capabilities=ExecutionCapabilities(supplement=True)))
        controller.phase = "paused"
        controller.supplement("第一条")
        controller.supplement("第二条")
        items = controller.take_supplements()
        controller.on_agent_event({"type": "context_applied", "source": "supplement", "receipt_ids": [items[0].receipt_id]})
        self.assertEqual(controller._receipts[items[0].receipt_id]["status"], "applied")
        self.assertEqual(controller._receipts[items[1].receipt_id]["status"], "queued")
        controller.on_agent_event({"type": "context_applied", "source": "supplement", "receipt_ids": ["foreign", items[0].receipt_id]})
        self.assertEqual(controller._receipts[items[1].receipt_id]["status"], "queued")

    def test_control_signals_bypass_engine_exception_handlers(self):
        self.assertFalse(issubclass(Reobserve, Exception))
        self.assertFalse(issubclass(TaskStopped, Exception))
        self.assertTrue(issubclass(TaskStopped, ExecutionCancelled))

    def test_waiting_progress_does_not_erase_a_stop_or_pause_request(self):
        controller = self.controller(Mock(capabilities=ExecutionCapabilities()))
        controller.phase = "planning"
        controller.on_agent_event({"type": "waiting", "stage": "model", "elapsed_seconds": 2})
        self.assertEqual(controller.phase, "waiting_model")
        controller.phase = "pausing"
        controller.on_agent_event({"type": "waiting", "stage": "model"})
        self.assertEqual(controller.phase, "pausing")
        controller.phase = "stopping"
        controller.cancel_event.set()
        controller.on_agent_event({"type": "waiting", "stage": "cleanup", "in_flight": True})
        self.assertEqual(controller.phase, "stopping")

    def test_foreign_and_late_events_cannot_change_task_state(self):
        controller = self.controller(Mock(capabilities=ExecutionCapabilities()))
        controller.task_id, controller.phase = "old", "planning"
        old_services = controller.execution_services()
        controller.task_id = "new"
        old_services.event({"type": "phase", "phase": "executing"})
        self.assertEqual(controller.phase, "planning")
        self.assertTrue(controller.events.empty())
        controller.phase = "completed"
        controller.on_agent_event({"task_id": "new", "type": "phase", "phase": "executing"})
        self.assertEqual(controller.phase, "completed")
        self.assertTrue(controller.events.empty())

    def test_core_has_no_ui_platform_or_engine_imports(self):
        core = Path(__file__).resolve().parents[1] / "mirally" / "core"
        for path in core.glob("*.py"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                names = [node.module or ""] if isinstance(node, ast.ImportFrom) else (
                    [alias.name for alias in node.names] if isinstance(node, ast.Import) else [])
                for name in names:
                    self.assertFalse(name.startswith(("mirally.ui", "mirally.platform", "mirally.runtime", "ufo", "tkinter")),
                                     f"{path.name} depends on {name}")


if __name__ == "__main__":
    unittest.main()
