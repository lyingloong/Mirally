"""WinForms frame bookkeeping must not grow a clipped companion on restore."""
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from mirally.framework_shell import FrameworkShell


STATES = SimpleNamespace(Normal="normal", Minimized="minimized", Maximized="maximized")


class NativeForm:
    def __init__(self, shell):
        self.shell = shell
        self._state = STATES.Normal
        self._bounds = self.RestoreBounds = (2158, 424, 400, 680)
        self.Region = object()
        self.IsDisposed = self.Disposing = self.InvokeRequired = False
        self.callbacks = []
        self.writes = []
        self.Show = MagicMock()
        self.BringToFront = MagicMock()
        self.Activate = MagicMock()
        self.webview = SimpleNamespace(Focus=MagicMock())

    @property
    def Bounds(self):
        return self._bounds

    @Bounds.setter
    def Bounds(self, value):
        self.writes.append(value)
        self._bounds = self.RestoreBounds = value
        self.shell._native_resize(self, None)

    @property
    def WindowState(self):
        return self._state

    @WindowState.setter
    def WindowState(self, value):
        previous = self._state
        if value == STATES.Minimized:
            self.RestoreBounds = self._bounds
        self._state = value
        self.shell._native_resize(self, None)
        if value == STATES.Normal and previous == STATES.Minimized:
            # Form.WmWindowPosChanged raises Resize, then restores cached
            # client dimensions using its old managed Sizable border style.
            x, y, width, height = self.RestoreBounds
            self._bounds = self.RestoreBounds = (x, y, width + 22, height + 56)

    def BeginInvoke(self, callback):
        self.callbacks.append(callback)

    def flush(self):
        while self.callbacks:
            self.callbacks.pop(0)()


class FrameworkCompanionRestoreTests(unittest.TestCase):
    def setUp(self):
        self.shell = FrameworkShell(SimpleNamespace(root=Path(__file__).resolve().parents[1]))
        self.owner = self.shell._workspace = object()
        self.form = NativeForm(self.shell)
        self.region = self.form.Region
        self.shell.window = SimpleNamespace(native=self.form)
        self.modules = {
            "System": SimpleNamespace(Action=lambda callback: callback),
            "System.Windows.Forms": SimpleNamespace(FormWindowState=STATES)}
        self.patches = patch.dict("sys.modules", self.modules)
        self.patches.start()
        self.addCleanup(self.patches.stop)

    def test_tray_finishes_geometry_synchronously_and_queued_callback_is_harmless(self):
        original = self.form.Bounds
        self.form.WindowState = STATES.Minimized
        self.shell._show_window()
        self.assertEqual(self.form.Bounds, original)
        self.assertEqual(self.form.WindowState, STATES.Normal)
        self.assertEqual(self.form.writes, [original])
        self.form.flush()
        self.assertEqual(self.form.writes, [original])
        self.assertIs(self.form.Region, self.region)
        self.assertIs(self.shell._workspace, self.owner)
        self.assertIsNone(self.shell._companion_minimize_geometry)

    def test_native_taskbar_restore_finishes_after_winforms_cached_bounds(self):
        original = self.form.Bounds
        self.form.WindowState = STATES.Minimized
        self.form.WindowState = STATES.Normal
        self.assertNotEqual(self.form.Bounds, original)
        self.assertEqual(len(self.form.callbacks), 1)
        self.form.flush()
        self.assertEqual(self.form.Bounds, original)
        self.assertIs(self.form.Region, self.region)
        self.assertIs(self.shell._workspace, self.owner)
        self.assertEqual(self.form.writes, [original])

    def test_callback_overtaken_by_second_minimize_waits_for_next_restore(self):
        original = self.form.Bounds
        self.form.WindowState = STATES.Minimized
        self.form.WindowState = STATES.Normal
        self.form.WindowState = STATES.Minimized
        self.form.flush()
        self.assertEqual(self.form.WindowState, STATES.Minimized)
        self.assertFalse(self.form.writes)
        self.assertIsNotNone(self.shell._companion_minimize_geometry)
        self.form.WindowState = STATES.Normal
        self.form.flush()
        self.assertEqual(self.form.Bounds, original)
        self.assertEqual(self.form.writes, [original])

    def test_old_owner_callback_cannot_clear_or_apply_new_companion_geometry(self):
        self.form.WindowState = STATES.Minimized
        self.form.WindowState = STATES.Normal
        old_callback = self.form.callbacks.pop()
        self.shell._workspace = object()
        new_bounds = self.form._bounds = (120, 180, 400, 680)
        self.form.WindowState = STATES.Minimized
        current = self.shell._companion_minimize_geometry
        old_callback()
        self.assertIs(self.shell._companion_minimize_geometry, current)
        self.assertFalse(self.form.writes)
        self.form.WindowState = STATES.Normal
        self.form.flush()
        self.assertEqual(self.form.Bounds, new_bounds)

    def test_closed_disposed_or_returned_workspace_does_not_restore_old_rectangle(self):
        for invalidation in ("closed", "disposed", "workspace"):
            with self.subTest(invalidation=invalidation):
                self.shell.closed.clear()
                self.form.IsDisposed = False
                self.shell._workspace = self.owner
                self.form.WindowState = STATES.Minimized
                self.form.WindowState = STATES.Normal
                self.form.writes.clear()
                if invalidation == "closed":
                    self.shell.closed.set()
                elif invalidation == "disposed":
                    self.form.IsDisposed = True
                else:
                    self.shell._workspace = None
                self.form.flush()
                self.assertFalse(self.form.writes)
                self.assertIsNone(self.shell._companion_minimize_geometry)

    def test_normal_resizes_and_maximized_workspace_do_not_schedule_correction(self):
        self.shell._native_resize(None, None)
        self.assertFalse(self.form.callbacks)
        self.shell._workspace = None
        self.form.WindowState = STATES.Maximized
        self.form.WindowState = STATES.Minimized
        self.form.WindowState = STATES.Normal
        self.form.flush()
        self.assertFalse(self.form.writes)
        self.assertIsNone(self.shell._companion_minimize_geometry)


if __name__ == "__main__":
    unittest.main()
