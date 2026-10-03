"""Startup lifecycle regressions without creating a Tk interpreter or window."""

from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from openassist.presentation import THEMES
from openassist.startup import StartupReveal


class FakeRoot:
    """Only the Tk scheduling/binding contract used by the reveal."""

    def __init__(self):
        self.bindings = {}
        self.pending = {}
        self.serial = 0

    def _identifier(self):
        self.serial += 1
        return str(self.serial)

    def bind(self, sequence, callback, add="+"):
        identifier = self._identifier()
        self.bindings.setdefault(sequence, {})[identifier] = callback
        return identifier

    def unbind(self, sequence, identifier):
        self.bindings.get(sequence, {}).pop(identifier, None)

    def after(self, milliseconds, callback):
        identifier = self._identifier()
        self.pending[identifier] = callback
        return identifier

    def after_cancel(self, identifier):
        self.pending.pop(identifier, None)

    def dispatch(self, sequence, widget=None):
        event = SimpleNamespace(widget=widget if widget is not None else self)
        results = []
        for callback in list(self.bindings.get(sequence, {}).values()):
            result = callback(event)
            results.append(result)
            if result == "break":
                break
        return results

    def run_next_timer(self):
        identifier = next(iter(self.pending))
        self.pending.pop(identifier)()

    def winfo_width(self):
        return 810

    def winfo_height(self):
        return 1200


class StartupRevealTests(unittest.TestCase):
    def setUp(self):
        self.root = FakeRoot()
        self.completed = Mock()
        self.frame = Mock()
        self.canvas = Mock()
        for name, value in (("tk.Frame", self.frame), ("tk.Canvas", self.canvas), ("brand_photo", object())):
            patcher = patch("openassist.startup." + name, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch("openassist.startup.time.perf_counter", return_value=100.0)
        self.clock = patcher.start()
        self.addCleanup(patcher.stop)

    def reveal(self, **kwargs):
        reveal = StartupReveal(self.root, THEMES["sky"], on_complete=self.completed, **kwargs)
        self.addCleanup(reveal.cancel)
        return reveal

    def assert_clean(self):
        self.assertFalse(self.root.pending)
        self.assertFalse(any(self.root.bindings.values()))
        self.frame.destroy.assert_called_once_with()

    def test_finish_is_idempotent_and_cancels_scheduled_work(self):
        reveal = self.reveal().start()
        self.assertTrue(reveal.active)
        self.assertTrue(self.root.pending)
        reveal.finish()
        reveal.finish()
        reveal.cancel()
        self.assertFalse(reveal.active)
        self.completed.assert_called_once_with()
        self.assert_clean()

    def test_cancel_never_invokes_completion_even_if_finish_follows(self):
        reveal = self.reveal().start()
        reveal.cancel()
        reveal.cancel()
        reveal.finish()
        self.completed.assert_not_called()
        self.assert_clean()

    def test_cancel_before_after_idle_start_does_not_revive_overlay(self):
        reveal = self.reveal()
        reveal.cancel()
        reveal.start()
        self.frame.place.assert_not_called()
        self.completed.assert_not_called()
        self.assert_clean()

    def test_reduced_motion_immediately_completes_without_animation(self):
        reveal = self.reveal(reduced_motion=True).start()
        reveal.start()
        self.assertFalse(reveal.active)
        self.frame.place.assert_not_called()
        self.canvas.coords.assert_not_called()
        self.completed.assert_called_once_with()
        self.assert_clean()

    def test_first_key_dismisses_without_stopping_event_propagation(self):
        reveal = self.reveal().start()
        next_handler = Mock()
        identifier = self.root.bind("<KeyPress>", next_handler)
        results = self.root.dispatch("<KeyPress>")
        self.assertIsNone(results[0])
        next_handler.assert_called_once()
        self.assertFalse(reveal.active)
        self.completed.assert_called_once_with()
        # Removing the reveal's handlers must preserve unrelated app bindings.
        self.assertIn(identifier, self.root.bindings["<KeyPress>"])
        self.root.unbind("<KeyPress>", identifier)
        self.assert_clean()

    def test_parent_destroy_cancels_without_calling_focus_callback(self):
        reveal = self.reveal().start()
        self.root.dispatch("<Destroy>", widget=self.canvas)
        self.assertTrue(reveal.active)
        self.root.dispatch("<Destroy>")
        reveal.finish()
        self.completed.assert_not_called()
        self.assert_clean()

    def test_delayed_frame_finishes_at_wall_clock_deadline(self):
        reveal = self.reveal().start()
        self.clock.return_value = 100.0 + StartupReveal.duration + 0.5
        self.root.run_next_timer()
        self.assertFalse(reveal.active)
        self.completed.assert_called_once_with()
        self.assert_clean()


if __name__ == "__main__":
    unittest.main()
