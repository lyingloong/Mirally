"""Real queues and worker lifetimes at the native control boundary."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from mirally.core.execution import ExecutionCancelled, ExecutionRequest, Reobserve, Supplement
from mirally.runtime.ufo.controls import ExecutionBudgetExceeded, GuardedExecutor, NativeRuntime


class Events:
    def __init__(self):
        self.items = []

    def emit(self, kind, **fields):
        self.items.append({"type": kind, **fields})


class Harness:
    def __init__(self):
        self.stopped = False
        self.revision = 0
        self.updates = []
        self.leases = []
        self.events = Events()
        self.services = SimpleNamespace(
            cancelled=lambda: self.stopped,
            revision=lambda: self.revision,
            checkpoint=lambda: self.revision,
            take_supplements=self.take_updates,
            desktop_access=self.desktop_access,
            authorize_tool=None, authorize_model=None,
        )
        self.runtime = NativeRuntime(
            ExecutionRequest("fixture-task", "Work only in the fixture",
                             {"previous_tasks": [{"result": "Exact previous answer"}]}),
            self.services,
            SimpleNamespace(total_seconds=10, tool_seconds=1, model_seconds=1,
                            model_attempts=2, context_chars=100000),
            self.events,
        )
        self.tool = SimpleNamespace(tool_name="fixture_write", tool_type="action",
                                    parameters={"text": "fixture text"})

    def take_updates(self):
        updates, self.updates = self.updates, []
        return updates

    def desktop_access(self, stage, expected_revision=None):
        if stage == "before" and expected_revision != self.revision:
            raise Reobserve()
        self.leases.append(stage)
        return self.revision

    def close(self):
        self.runtime.pool.shutdown(wait=True, cancel_futures=True)


class GuardedQueueTests(unittest.TestCase):
    def setUp(self):
        self.h = Harness()
        self.addCleanup(self.h.close)

    def run_queued_change(self, change, expected_error):
        entered, release = threading.Event(), threading.Event()
        operation = Mock(return_value=SimpleNamespace(is_error=False))
        with ThreadPoolExecutor(max_workers=1) as pool:
            def occupy():
                entered.set()
                if not release.wait(2):
                    raise AssertionError("test queue was not released")
            blocker = pool.submit(occupy)
            try:
                self.assertTrue(entered.wait(1))
                executor = GuardedExecutor(pool, self.h.runtime, self.h.tool, 0)
                future = executor.submit(operation)
                self.assertFalse(future.running())
                change()
                release.set()
                blocker.result(1)
                with self.assertRaises(expected_error):
                    future.result(1)
            finally:
                release.set()
        operation.assert_not_called()
        self.assertEqual(self.h.leases, [])
        self.assertEqual(self.h.runtime.actions, [])

    def test_stop_revokes_a_tool_already_queued_in_the_real_executor(self):
        self.run_queued_change(lambda: setattr(self.h, "stopped", True), ExecutionCancelled)

    def test_changed_revision_revokes_a_queued_action_before_its_lease(self):
        self.run_queued_change(lambda: setattr(self.h, "revision", 1), Reobserve)

    def test_revision_change_after_lease_admission_prevents_side_effect_and_releases(self):
        def access(stage, expected_revision=None):
            self.h.leases.append(stage)
            if stage == "before":
                self.h.revision += 1
            return self.h.revision
        self.h.services.desktop_access = access
        operation = Mock()
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = GuardedExecutor(pool, self.h.runtime, self.h.tool, 0).submit(operation)
            with self.assertRaises(Reobserve):
                future.result(1)
        operation.assert_not_called()
        self.assertEqual(self.h.leases, ["before", "after"])

    def test_failed_admission_does_not_release_a_lease_it_never_owned(self):
        self.h.services.desktop_access = Mock(side_effect=Reobserve())
        operation = Mock()
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = GuardedExecutor(pool, self.h.runtime, self.h.tool, 0).submit(operation)
            with self.assertRaises(Reobserve):
                future.result(1)
        operation.assert_not_called()
        self.h.services.desktop_access.assert_called_once_with("before", expected_revision=0)

    def test_worker_exception_releases_owned_lease(self):
        operation = Mock(side_effect=ValueError("fixture tool failed"))
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = GuardedExecutor(pool, self.h.runtime, self.h.tool, 0).submit(operation)
            with self.assertRaisesRegex(ValueError, "fixture tool failed"):
                future.result(1)
        self.assertEqual(self.h.leases, ["before", "after"])
        self.assertEqual(len(self.h.runtime.actions), 1)
        self.assertIsNone(self.h.runtime.actions[0]["ok"])
        self.assertEqual(self.h.runtime.actions[0]["outcome"], "unknown")


class NativeRuntimeWorkerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.h = Harness()
        self.addCleanup(self.h.close)

    async def test_tool_timeout_retains_execution_ownership_until_true_worker_finishes(self):
        entered, release = threading.Event(), threading.Event()
        with ThreadPoolExecutor(max_workers=1) as pool:
            def operation():
                entered.set()
                if not release.wait(2):
                    raise AssertionError("test worker was not released")
                return SimpleNamespace(is_error=False)
            future = GuardedExecutor(pool, self.h.runtime, self.h.tool, 0).submit(operation)
            wait = asyncio.create_task(self.h.runtime.wait_worker(future, "tool", .01))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 1))
                await asyncio.sleep(.09)
                self.assertFalse(wait.done(), "The timeout must not pretend a running tool has stopped")
                self.assertFalse(future.done())
                self.assertEqual(self.h.runtime.expired, "tool_timeout")
                self.assertEqual(self.h.leases, ["before"])
                with self.assertRaises(ExecutionBudgetExceeded):
                    self.h.runtime.check(wait=False)
                release.set()
                with self.assertRaises(ExecutionBudgetExceeded):
                    await asyncio.wait_for(wait, 1)
            finally:
                release.set()
                if not wait.done():
                    try:
                        await wait
                    except BaseException:
                        pass
        self.assertEqual(self.h.leases, ["before", "after"])
        self.assertEqual(len(self.h.runtime.actions), 1)
        self.assertTrue(any(e["type"] == "timing" and e.get("timed_out") for e in self.h.events.items))

    async def test_stop_waits_for_inflight_tool_and_never_returns_its_late_success(self):
        entered, release = threading.Event(), threading.Event()
        with ThreadPoolExecutor(max_workers=1) as pool:
            def operation():
                entered.set()
                release.wait(2)
                return SimpleNamespace(is_error=False)
            future = GuardedExecutor(pool, self.h.runtime, self.h.tool, 0).submit(operation)
            wait = asyncio.create_task(self.h.runtime.wait_worker(future, "tool", 1))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 1))
                self.h.stopped = True
                await asyncio.sleep(.07)
                self.assertFalse(wait.done())
                self.assertEqual(self.h.leases, ["before"])
                release.set()
                # asyncio may normalize a CancelledError subclass at a Task
                # boundary; cancellation remains the observable contract.
                with self.assertRaises(asyncio.CancelledError):
                    await asyncio.wait_for(wait, 1)
            finally:
                release.set()
                if not wait.done():
                    try:
                        await wait
                    except BaseException:
                        pass
        self.assertEqual(self.h.leases, ["before", "after"])

    async def test_drain_cancels_queued_work_but_joins_running_work(self):
        entered, release = threading.Event(), threading.Event()
        def running():
            entered.set()
            release.wait(2)
        first = self.h.runtime.pool.submit(running)
        self.h.runtime.register_worker(first)
        self.assertTrue(await asyncio.to_thread(entered.wait, 1))
        side_effect = Mock()
        queued = self.h.runtime.pool.submit(side_effect)
        self.h.runtime.register_worker(queued)
        drain = asyncio.create_task(self.h.runtime.drain())
        try:
            await asyncio.sleep(.06)
            self.assertTrue(queued.cancelled())
            self.assertFalse(drain.done())
            self.assertFalse(first.done())
            release.set()
            await asyncio.wait_for(drain, 1)
        finally:
            release.set()
            if not drain.done():
                await drain
        side_effect.assert_not_called()
        self.assertEqual(self.h.runtime.workers, set())

    async def test_supplement_receipt_acknowledges_the_exact_prompt_batch(self):
        self.h.updates = [Supplement("A", "keep the original")]
        self.h.runtime.collect_inputs()
        self.assertEqual(self.h.events.items, [], "Queue consumption is not prompt delivery")
        prompts = []
        def respond(prompt, agent_type, backup):
            prompts.append(prompt)
            if len(prompts) == 1:
                self.h.updates = [Supplement("B", "only the fixture")]
                self.h.runtime.collect_inputs()
            return '{"status":"FINISH"}', 0
        agent = SimpleNamespace(get_response=respond, response_to_dict=json.loads)
        await self.h.runtime.model_response(agent, [{"role": "system", "content": "fixture"}], "AppAgent")
        receipts = [e for e in self.h.events.items if e["type"] == "context_applied"]
        self.assertEqual([e["receipt_ids"] for e in receipts], [["A"]])
        self.assertEqual(self.h.runtime.pending_receipts, ["B"])
        self.assertIn("keep the original", prompts[0][-1]["content"])
        self.assertNotIn("only the fixture", prompts[0][-1]["content"])
        self.assertIn("Exact previous answer", prompts[0][-1]["content"])
        await self.h.runtime.model_response(agent, [], "AppAgent")
        receipts = [e for e in self.h.events.items if e["type"] == "context_applied"]
        self.assertEqual([e["receipt_ids"] for e in receipts], [["A"], ["B"]])
        self.assertIn("only the fixture", prompts[1][-1]["content"])
        self.assertEqual(self.h.runtime.pending_receipts, [])

    async def test_cancel_before_model_invocation_keeps_supplement_unacknowledged(self):
        self.h.updates = [Supplement("A", "do not lose this")]
        self.h.runtime.collect_inputs()
        self.h.stopped = True
        agent = SimpleNamespace(get_response=Mock(), response_to_dict=Mock())
        with self.assertRaises(ExecutionCancelled):
            await self.h.runtime.model_response(agent, [], "AppAgent")
        agent.get_response.assert_not_called()
        self.assertFalse(any(e["type"] == "context_applied" for e in self.h.events.items))
        self.assertEqual(self.h.runtime.pending_receipts, ["A"])

    async def test_model_format_retry_does_not_duplicate_input_receipts(self):
        self.h.updates = [Supplement("A", "keep the original")]
        self.h.runtime.collect_inputs()
        self.h.runtime.answer_receipts.append("answer-1")
        self.h.runtime.answers.append({"question": "which?", "answer": "fixture"})
        agent = SimpleNamespace(
            get_response=Mock(side_effect=[("invalid JSON", 0), ('{"status":"FINISH"}', 0)]),
            response_to_dict=json.loads,
        )
        await self.h.runtime.model_response(agent, [], "AppAgent")
        self.assertEqual(agent.get_response.call_count, 2)
        receipts = [e for e in self.h.events.items if e["type"] == "context_applied"]
        self.assertEqual([(e["source"], e["receipt_ids"]) for e in receipts],
                         [("supplement", ["A"]), ("answer", ["answer-1"])])
        self.assertEqual(self.h.runtime.pending_receipts, [])
        self.assertEqual(self.h.runtime.answer_receipts, [])

    async def test_late_model_reply_is_discarded_when_revision_changes(self):
        entered, release = threading.Event(), threading.Event()
        def respond(*_):
            entered.set()
            release.wait(2)
            return '{"status":"FINISH"}', 0
        agent = SimpleNamespace(get_response=respond, response_to_dict=Mock())
        run = asyncio.create_task(self.h.runtime.model_response(agent, [], "AppAgent"))
        try:
            self.assertTrue(await asyncio.to_thread(entered.wait, 1))
            self.h.revision = 1
            release.set()
            with self.assertRaises(Reobserve):
                await asyncio.wait_for(run, 1)
        finally:
            release.set()
            if not run.done():
                try:
                    await run
                except BaseException:
                    pass
        agent.response_to_dict.assert_not_called()


if __name__ == "__main__":
    unittest.main()
