import asyncio
import queue
import threading
import time
import unittest

from openassist.presentation import public_text
from openassist.task_controller import TaskController, TaskStopped
from openassist.assistant import AssistantDecision
from unittest.mock import Mock


class TaskControllerTests(unittest.TestCase):
    def setUp(self):
        self.controllers = []

    def controller(self, **kwargs):
        kwargs.setdefault("assistant", Mock(respond=Mock(return_value=AssistantDecision("desktop", "开始处理"))))
        c = TaskController(**kwargs)
        self.controllers.append(c)
        return c

    def tearDown(self):
        for c in self.controllers:
            c.stop()
            self.pump(c, lambda: not c.active)

    def pump(self, c, condition, *, acknowledge=True, timeout=2):
        deadline = time.monotonic() + timeout
        collected = []
        while time.monotonic() < deadline:
            c.pulse()
            while True:
                try:
                    event = c.events.get_nowait()
                except queue.Empty:
                    break
                collected.append(event)
                if acknowledge and event["type"] == "ui":
                    event["call"].done.set()
            if condition():
                return collected
            time.sleep(0.005)
        self.fail(f"Timed out waiting for state; phase={c.phase}")

    def test_pause_is_acknowledged_and_resume_invalidates_plan(self):
        entered = threading.Event()
        release = threading.Event()
        revisions = []

        class Runner:
            def __init__(self, **kw): self.kw = kw
            async def run(self):
                revisions.append(self.kw["control_callback"]())
                entered.set()
                release.wait(1)
                revisions.append(self.kw["control_callback"]())
                return {"status": "done", "message": "done"}

        c = self.controller(runner_factory=Runner)
        c.start("test")
        self.pump(c, entered.is_set)
        c.pause()
        self.assertEqual(c.phase, "pausing")
        with self.assertRaises(RuntimeError): c.start("overlap")
        release.set()
        self.pump(c, lambda: c.phase == "paused")
        c.pause(takeover=True)
        self.assertEqual(c.phase, "takeover")
        self.assertTrue(c.supplement("new information"))
        self.assertTrue(c.resume())
        self.pump(c, lambda: not c.active)
        self.assertGreater(revisions[1], revisions[0])
        self.assertEqual(c.phase, "completed")

    def test_stop_wins_over_waiting_confirmation_and_stale_reply(self):
        acted = []
        class Runner:
            def __init__(self, **kw): self.kw = kw
            async def run(self):
                if self.kw["confirmation_callback"]("click", "target"):
                    acted.append(True)
                return {"status": "done"}
        c = self.controller(runner_factory=Runner)
        c.start("confirm")
        self.pump(c, lambda: c.phase == "waiting_confirmation")
        pending = c._pending_id
        self.assertFalse(c.reply("old-task-id", True))
        self.assertFalse(c.reply(pending, "yes"))
        c.stop()
        self.assertFalse(c.reply(pending, True))
        self.pump(c, lambda: not c.active)
        self.assertEqual(c.phase, "cancelled")
        self.assertEqual(acted, [])

    def test_confirmation_timeout_pauses_and_never_approves(self):
        answers = []
        class Runner:
            def __init__(self, **kw): self.kw = kw
            async def run(self):
                answers.append(self.kw["confirmation_callback"]("click", "target"))
                return {"status": "done"}
        c = self.controller(runner_factory=Runner, input_timeout=0.05)
        c.start("confirm")
        self.pump(c, lambda: c.phase == "paused")
        c.resume()
        self.pump(c, lambda: not c.active)
        self.assertEqual(answers, [False])

    def test_capture_requires_acknowledgement(self):
        c = self.controller(ui_timeout=0.05)
        with self.assertRaises(RuntimeError): c.capture("before")
        self.assertTrue(c.cancel_event.is_set())
        self.assertTrue(c.events.get_nowait()["call"].expired)

    def test_takeover_invalidates_an_already_queued_approval(self):
        answers = []
        class Runner:
            def __init__(self, **kw): self.kw = kw
            async def run(self):
                answers.append(self.kw["confirmation_callback"]("click", "target"))
                return {"status": "done"}
        c = self.controller(runner_factory=Runner)
        c.start("confirm")
        self.pump(c, lambda: c.phase == "waiting_confirmation")
        with c._condition:
            self.assertTrue(c.reply(c._pending_id, True))
            c.pause(takeover=True)
        self.pump(c, lambda: c.phase == "takeover")
        c.resume()
        self.pump(c, lambda: not c.active)
        self.assertEqual(answers, [False])

    def test_unresponsive_ui_blocks_new_actions(self):
        c = self.controller(ui_timeout=0.1)
        c._last_pulse = time.monotonic() - 1
        with self.assertRaises(RuntimeError): c.checkpoint()
        self.assertTrue(c.cancel_event.is_set())

    def test_model_reasoning_and_raw_logs_are_not_forwarded(self):
        c = self.controller()
        c.on_agent_event({"type": "step", "observation": "private", "thought": "secret", "actions": [{"text": "password"}]})
        c.on_agent_event({"type": "progress", "message": "API_KEY=secret"})
        self.assertTrue(c.events.empty())
        c.phase = "executing"
        c.on_agent_event({"type": "action_result", "action": "type_text", "ok": True, "detail": "private typed contents"})
        event = c.events.get_nowait()
        self.assertNotIn("private", str(event))

    def test_public_copy_redacts_credentials_and_bounds_size(self):
        self.assertNotIn("abcdefgh123", public_text("API_KEY=abcdefgh123"))
        self.assertEqual(len(public_text("长" * 1000)), 240)

    def test_supplement_receipts_wait_for_runner_ack_and_track_each_message(self):
        c = self.controller()
        c.phase = "paused"
        self.assertTrue(c.supplement("Keep the originals"))
        self.assertTrue(c.supplement("Use the Archive folder"))
        queued = [c.events.get_nowait(), c.events.get_nowait()]
        ids = [e["receipt_id"] for e in queued]
        self.assertEqual(len(set(ids)), 2)
        self.assertTrue(all(e["status"] == "queued" for e in queued))
        supplements = c.take_supplements()
        self.assertEqual([item.text for item in supplements], ["Keep the originals", "Use the Archive folder"])
        self.assertEqual([item.receipt_id for item in supplements], ids)
        self.assertTrue(c.events.empty(), "Queue consumption must not claim context was updated")
        # A later addition is not acknowledged by the earlier consumed batch.
        self.assertTrue(c.supplement("Only PDF files"))
        later = c.events.get_nowait()
        c.on_agent_event({"type": "context_applied", "source": "supplement"})
        applied = [c.events.get_nowait(), c.events.get_nowait()]
        self.assertEqual([e["receipt_id"] for e in applied], ids)
        self.assertTrue(all(e["status"] == "applied" for e in applied))
        self.assertEqual(c._receipts[later["receipt_id"]]["status"], "queued")
        c.on_agent_event({"type": "context_applied", "source": "supplement"})
        self.assertTrue(c.events.empty(), "Duplicate acknowledgment must be harmless")

    def test_question_receipt_is_created_once_and_acknowledged_after_input_returns(self):
        answers = []
        class Runner:
            def __init__(self, **kw): self.kw = kw
            async def run(self):
                answers.append(self.kw["input_callback"]("Which folder?"))
                self.kw["event_callback"]({"type": "context_applied", "source": "answer"})
                return {"status": "done"}
        c = self.controller(runner_factory=Runner)
        c.start("test")
        self.pump(c, lambda: c.phase == "waiting_user")
        with c._condition:
            self.assertFalse(c.reply(c._pending_id, " "))
            self.assertTrue(c.reply(c._pending_id, "Archive"))
            self.assertFalse(c.reply(c._pending_id, "Duplicate"))
        events = self.pump(c, lambda: not c.active)
        receipts = [e for e in events if e["type"] == "receipt"]
        self.assertEqual(answers, ["Archive"])
        self.assertEqual([e["status"] for e in receipts], ["queued", "applied"])
        self.assertEqual(receipts[0]["receipt_id"], receipts[1]["receipt_id"])

    def test_stop_preserves_unconsumed_receipt_and_next_task_clears_it(self):
        class Runner:
            def __init__(self, **kw): self.kw = kw
            async def run(self):
                while True:
                    self.kw["control_callback"]()
                    await asyncio.sleep(0.005)
        c = self.controller(runner_factory=Runner)
        c.start("first")
        self.pump(c, lambda: c.mode == "desktop")
        c.pause()
        self.pump(c, lambda: c.phase == "paused")
        self.assertTrue(c.supplement("Not consumed"))
        old_task = c.task_id
        c.stop()
        self.assertFalse(c.supplement("Too late"))
        events = self.pump(c, lambda: not c.active)
        receipts = [e for e in events if e["type"] == "receipt"]
        self.assertEqual([e["status"] for e in receipts], ["queued", "unused"])
        self.assertTrue(all(e["task_id"] == old_task for e in receipts))
        c.start("second")
        self.assertNotEqual(c.task_id, old_task)
        self.assertEqual(c._receipts, {})
        self.assertEqual(c.take_supplements(), [])

    def test_timed_out_question_cannot_accept_and_acknowledge_a_late_reply(self):
        answers, accepted = [], []
        class Runner:
            def __init__(self, **kw): self.kw = kw
            async def run(self):
                answers.append(self.kw["input_callback"]("Which folder?"))
                self.kw["event_callback"]({"type": "context_applied", "source": "answer"})
                return {"status": "done"}
        c = self.controller(runner_factory=Runner, input_timeout=0.03)
        emit = c._emit
        def submit_at_timeout(kind, **fields):
            if kind == "record" and fields.get("title") == "等待已超时":
                accepted.append(c.reply(c._pending_id, "Too late"))
            emit(kind, **fields)
        c._emit = submit_at_timeout
        c.start("test")
        events = self.pump(c, lambda: c.phase == "paused")
        c.resume()
        events += self.pump(c, lambda: not c.active)
        self.assertEqual(accepted, [False])
        self.assertEqual(answers, [""])
        self.assertFalse(any(e["type"] == "receipt" for e in events))

    def test_live_input_during_model_wait_is_available_without_manual_pause(self):
        thinking, model_returned = threading.Event(), threading.Event()
        contexts, additions, revisions = [], [], []
        class Runner:
            def __init__(self, **kw): self.kw = kw; contexts.append(kw["conversation_context"])
            async def run(self):
                revisions.append(self.kw["control_callback"]())
                thinking.set()
                model_returned.wait(1)
                revisions.append(self.kw["control_callback"]())
                additions.extend(self.kw["supplement_callback"]())
                self.kw["event_callback"]({"type": "context_applied", "source": "supplement"})
                return {"status": "done", "message": "Created D:/reports/week.pdf"}
        c = self.controller(runner_factory=Runner)
        c.start("archive files")
        self.pump(c, thinking.is_set)
        self.assertTrue(c.begin_edit())
        self.assertTrue(c.editing_ready)
        self.assertNotIn(c.phase, {"paused", "pausing"})
        self.assertTrue(c.supplement("Only PDF"))
        self.assertTrue(c.supplement("Keep originals"))
        c.end_edit()
        model_returned.set()
        self.pump(c, lambda: not c.active)
        self.assertGreater(revisions[1], revisions[0])
        self.assertEqual(additions, ["Only PDF", "Keep originals"])
        c.start("use the previous result")
        self.pump(c, lambda: not c.active)
        self.assertEqual(contexts[1]["previous_tasks"][0]["inputs"], additions[:2])
        self.assertIn("D:/reports/week.pdf", contexts[1]["previous_tasks"][0]["result"])

    def test_composer_waits_for_inflight_desktop_operation_and_blocks_the_next(self):
        entered, finish_action, second = threading.Event(), threading.Event(), threading.Event()
        class Runner:
            def __init__(self, **kw): self.kw = kw
            async def run(self):
                self.kw["desktop_callback"]("before")
                entered.set()
                finish_action.wait(1)
                self.kw["desktop_callback"]("after")
                self.kw["desktop_callback"]("before")
                second.set()
                self.kw["desktop_callback"]("after")
                return {"status": "done"}
        c = self.controller(runner_factory=Runner)
        c.start("test")
        self.pump(c, entered.is_set)
        self.assertFalse(c.begin_edit(), "Cannot focus composer during a desktop operation")
        finish_action.set()
        events = self.pump(c, lambda: c.editing_ready)
        self.assertFalse(second.is_set())
        self.assertTrue(any(e["type"] == "edit_ready" for e in events))
        c.end_edit()
        self.pump(c, lambda: not c.active)
        self.assertTrue(second.is_set())

    def test_new_information_invalidates_a_pending_sensitive_confirmation(self):
        approvals = []
        class Runner:
            def __init__(self, **kw): self.kw = kw
            async def run(self):
                approvals.append(self.kw["confirmation_callback"]("move", "folder"))
                return {"status": "done"}
        c = self.controller(runner_factory=Runner)
        c.start("test")
        self.pump(c, lambda: c.phase == "waiting_confirmation")
        self.assertTrue(c.supplement("Use a different folder"))
        self.pump(c, lambda: not c.active)
        self.assertEqual(approvals, [False])

    def test_consumed_reply_cannot_be_submitted_twice_before_request_closes(self):
        c = self.controller()
        c._pending = queue.Queue(maxsize=1)
        c._pending_id, c._pending_kind = "one", "question"
        self.assertTrue(c.reply("one", "first"))
        self.assertEqual(c._pending.get_nowait(), "first")
        self.assertFalse(c.reply("one", "duplicate"))
        self.assertEqual(len(c._receipts), 1)


if __name__ == "__main__":
    unittest.main()
