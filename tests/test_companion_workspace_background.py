"""Workspace exposure must never use the companion's transparent clear plate."""
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from mirally.control_ui_shell import ControlUIShell, _opaque_page_background


class PageColor:
    Black = (255, 0, 0, 0)
    Transparent = (0, 0, 0, 0)

    @staticmethod
    def FromArgb(*channels):
        return tuple(channels)


class WorkspaceBackgroundTests(unittest.TestCase):
    def setUp(self):
        self.shell = ControlUIShell(Path(__file__).resolve().parents[1])
        self.saved = (255, 247, 249, 252)
        self.events = []
        test = self

        class NativeForm:
            def __init__(self):
                self.webview = SimpleNamespace(DefaultBackgroundColor=PageColor.Transparent,
                    ClientSize=SimpleNamespace(Width=1280, Height=860))
                self.DeviceDpi = 96
                self.Region = MagicMock()
                self.BackColor = PageColor.Black
                self.Bounds = self.RestoreBounds = (1600, 200, 400, 680)
                self.WindowState = "normal"
                self.MinimumSize = (0, 0)
                self.TopMost = True
                self.PerformLayout = MagicMock(side_effect=lambda: test.record("layout"))
                self.recording = True

            def __setattr__(self, name, value):
                object.__setattr__(self, name, value)
                if getattr(self, "recording", False) and name in {"Region", "Bounds", "MinimumSize", "WindowState"}:
                    test.record(name)

        self.form = NativeForm()
        self.shell.window = SimpleNamespace(native=self.form, shadow=True)
        self.shell._workspace = ((100, 120, 1280, 860), "normal", (640, 480), False, self.saved, 0x16cf0000, 2)
        self.shell._workspace_backcolor = self.saved
        self.shell._workspace_glass_margin = 1
        self.shell._glass_active = True
        self.glass = -1
        self.modules = {
            "System.Drawing": SimpleNamespace(Color=PageColor, Point=MagicMock(), Region=MagicMock(),
                Rectangle=lambda *values: tuple(values), Size=lambda *values: tuple(values)),
            "System.Drawing.Drawing2D": SimpleNamespace(GraphicsPath=MagicMock()),
            "System.Windows.Forms": SimpleNamespace(FormWindowState=SimpleNamespace(Normal="normal"),
                Screen=SimpleNamespace(FromControl=lambda _form: SimpleNamespace(
                    WorkingArea=SimpleNamespace(Width=1920, Height=1080, Right=1920, Top=0))))}

    def record(self, action):
        self.events.append((action, self.form.BackColor, self.form.webview.DefaultBackgroundColor,
            self.glass, self.form.Region))

    def set_glass(self, _form, margin):
        self.glass = margin

    def apply(self, message):
        with patch.dict("sys.modules", self.modules), \
             patch("mirally.control_ui_shell._frame_style", side_effect=lambda *_: self.record("frame") or 0x16cf0000), \
             patch("mirally.control_ui_shell._frame_composition", side_effect=lambda *_: self.record("composition") or 2), \
             patch("mirally.control_ui_shell._client_glass", side_effect=self.set_glass), \
             patch("mirally.control_ui_shell._capture_affinity"), \
             patch.object(self.shell, "_initial_companion_shape"):
            self.shell._companion_action(message)

    def assert_exposure(self, expected):
        self.assertTrue(self.events)
        for action, back, web, glass, region in self.events:
            with self.subTest(action=action):
                self.assertEqual(back, expected)
                self.assertEqual(web, expected)
                self.assertEqual(glass, 1)
                if action != "Region":
                    self.assertIsNotNone(region, "Keep clipping until geometry and opaque backing are restored")
        self.assertEqual(self.events[-1][0], "Region")
        self.assertIsNone(self.form.Region)

    def test_light_and_dark_pages_have_opaque_backing_before_frame_resize_and_unclip(self):
        for rgb in ([247, 249, 252], [17, 25, 35]):
            with self.subTest(rgb=rgb):
                self.setUp()
                region = self.form.Region
                self.apply({"action": "workspace", "background": rgb})
                self.assert_exposure((255, *rgb))
                region.Dispose.assert_called_once_with()
                self.assertEqual(self.form.Bounds, (100, 120, 1280, 860))
                self.assertIsNone(self.shell._workspace)

    def test_missing_or_malformed_color_uses_the_saved_opaque_page_color(self):
        for bad in (None, [255, 255], [False, 0, 0], [0, 0, 256], [1.5, 2, 3], "#000000"):
            with self.subTest(background=bad):
                self.setUp()
                self.apply({"action": "workspace", "background": bad})
                self.assert_exposure(self.saved)

    def test_dock_caches_the_actual_page_color_for_native_retry_without_javascript(self):
        self.form.recording = False
        self.form.BackColor = self.form.webview.DefaultBackgroundColor = (255, 13, 21, 32)
        self.shell._workspace = None
        self.apply({"action": "dock", "viewport": [1280, 860], "background": [247, 249, 252]})
        self.assertEqual(self.shell._workspace[4], self.saved)
        self.assertEqual(self.shell._workspace_backcolor, self.saved)
        self.form.recording = True
        self.events.clear()
        self.apply({"action": "workspace"})
        self.assert_exposure(self.saved)

    def test_rgb_validation_forces_opaque_alpha_and_rejects_non_integer_channels(self):
        with patch.dict("sys.modules", self.modules):
            self.assertEqual(_opaque_page_background([0, 128, 255]), (255, 0, 128, 255))
            for value in (None, True, [0, 0, 0, 0], [-1, 0, 0], [True, 0, 0], [float("nan"), 0, 0]):
                self.assertIsNone(_opaque_page_background(value))


if __name__ == "__main__":
    unittest.main()
