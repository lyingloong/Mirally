"""Legacy event mapping without creating a window or accessing the desktop."""

import queue
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from openassist.core.task_controller import UiCall
from openassist.ui.desktop import OpenAssistWindow


class LegacyControllerBridgeTests(unittest.TestCase):
    def window(self):
        window = object.__new__(OpenAssistWindow)
        window.root = Mock()
        window.session = Mock()
        window.controller = Mock()
        window.controller.task_id = "current"
        window.controller.conversation.snapshot.return_value = {"tasks": [{"result": "真实业务答案\n\n保留段落"}]}
        window.status_var = Mock()
        window._set_live = Mock()
        window._set_busy = Mock()
        window._set_click_through = Mock()
        window._restore_external_window = Mock()
        window._append = Mock()
        return window

    def test_capture_acknowledges_only_after_isolation_finishes(self):
        window = self.window()
        call = UiCall("capture_begin")
        window.session.capture_begin.side_effect = lambda: self.assertFalse(call.done.is_set())
        window._handle_event({"type": "ui", "call": call})
        self.assertTrue(call.done.is_set())
        window.session.capture_begin.assert_called_once()
        self.assertIsNone(call.error)

    def test_failed_capture_is_returned_to_controller(self):
        window = self.window()
        call = UiCall("capture_begin")
        window.session.capture_begin.side_effect = RuntimeError("not isolated")
        window._handle_event({"type": "ui", "call": call})
        self.assertTrue(call.done.is_set())
        self.assertIsInstance(call.error, RuntimeError)

    def test_partial_result_uses_partial_label_and_real_answer(self):
        window = self.window()
        window._handle_event({"type": "state", "phase": "partial", "title": "部分完成"})
        window._append.assert_called_once_with("部分完成", "真实业务答案\n\n保留段落", "agent")
        window._set_busy.assert_called_once_with(False, "部分完成")

    def test_poll_schedules_heartbeat_before_handling_interaction(self):
        window = self.window()
        window._closing = False
        window.events = queue.Queue()
        window.session.poll_hotkeys.return_value = []
        window.events.put({"type": "state", "phase": "waiting_confirmation", "task_id": "current"})
        window._handle_event = lambda event: window.root.after.assert_called_once()
        window._drain_events()
        window.controller.pulse.assert_called_once()


if __name__ == "__main__":
    unittest.main()
