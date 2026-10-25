"""Native companion clipping uses physical pixel geometry, not CSS noise."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from mirally.control_ui_shell import ControlUIShell


class CompanionGeometryTests(unittest.TestCase):
    def setUp(self):
        self.shell = ControlUIShell(Path(__file__).resolve().parents[1])
        self.shell._workspace = object()
        self.form = SimpleNamespace(
            Bounds=SimpleNamespace(X=100, Y=200), Region=None,
            webview=SimpleNamespace(
                ClientSize=SimpleNamespace(Width=600, Height=1020),
                PointToScreen=lambda _point: SimpleNamespace(X=100, Y=200)))
        self.shell.window = SimpleNamespace(native=self.form)
        self.regions = []
        self.paths = []

        def region():
            value = MagicMock()
            self.regions.append(value)
            return value

        def path():
            value = MagicMock()
            self.paths.append(value)
            return value

        self.modules = {
            "System.Drawing": SimpleNamespace(
                Color=MagicMock(), Point=lambda x, y: (x, y),
                Rectangle=lambda *values: values, Region=region, Size=MagicMock()),
            "System.Drawing.Drawing2D": SimpleNamespace(GraphicsPath=path),
            "System.Windows.Forms": SimpleNamespace(FormWindowState=MagicMock(), Screen=MagicMock())}
        self.message = {"action": "shape", "viewport": [400, 680],
            "rect": [20, 30, 100, 200], "parts": [[20, 30, 100, 200, 18]], "view": "quick"}

    def apply(self, message=None):
        with patch.dict("sys.modules", self.modules), \
             patch.object(self.shell, "_companion_background"):
            self.shell._companion_action(message or self.message)

    def test_subpixel_noise_reuses_region_and_actual_pixel_change_replaces_once(self):
        self.apply()
        first = self.form.Region
        self.assertEqual(self.shell._shape_parts_key, ((30, 45, 180, 345, 27),))
        noisy = deepcopy(self.message)
        noisy["rect"][0] += .05
        noisy["parts"][0][0] += .05
        noisy["parts"][0][4] += .05
        self.apply(noisy)
        self.assertIs(self.form.Region, first)
        self.assertEqual(len(self.regions), 1)
        first.Dispose.assert_not_called()
        moved = deepcopy(self.message)
        moved["rect"][0] += 1
        moved["parts"][0][0] += 1
        self.apply(moved)
        self.assertEqual(len(self.regions), 2)
        self.assertIsNot(self.form.Region, first)
        first.Dispose.assert_called_once_with()
        self.assertEqual(len(self.paths), 2)
        for path in self.paths:
            path.Dispose.assert_called_once_with()

    def test_radius_and_badge_padding_are_part_of_deduplication(self):
        self.shell._surface_mode = "badge"
        self.message.update(rect=[20, 30, 48, 48], parts=[[20, 30, 48, 48, 24]])
        self.apply()
        self.assertEqual(self.shell._shape_parts_key, ((28, 43, 104, 119, 38),))
        first = self.form.Region
        self.shell._surface_mode = "pet"
        self.apply()
        self.assertEqual(self.shell._shape_parts_key, ((30, 45, 102, 117, 36),))
        self.assertEqual(len(self.regions), 2)
        first.Dispose.assert_called_once_with()
        self.message["parts"][0][4] = 18
        self.apply()
        self.assertEqual(len(self.regions), 3)
        self.assertEqual(self.shell._shape_parts_key[0][-1], 27)

    def test_each_native_part_matters_and_view_background_still_updates(self):
        self.message.update(rect=[20, 30, 160, 200],
            parts=[[20, 30, 100, 200, 18], [128, 60, 48, 48, 24]])
        self.apply()
        self.assertEqual(len(self.paths), 2)
        first = self.form.Region
        self.message["view"] = "detail"
        with patch.dict("sys.modules", self.modules), \
             patch.object(self.shell, "_companion_background") as background:
            self.shell._companion_action(self.message)
        background.assert_called_once_with()
        self.assertEqual(self.shell._surface_view, "detail")
        self.assertIs(self.form.Region, first)
        self.assertEqual(len(self.regions), 1)
        self.message["parts"][1][1] += 2
        self.apply()
        self.assertEqual(len(self.regions), 2)
        self.assertEqual(len(self.paths), 4)

    def test_invalid_or_stale_geometry_cannot_replace_region(self):
        self.apply()
        first = self.form.Region
        for changed in (
            {"parts": [[20, 30, 1010, 200, 18]]},
            {"parts": [[20, 30, 100, 200, float("nan")]]},
            {"parts": [[20, 30, 100, 200, True]]},
            {"viewport": [800, 680]},
            {"rect": [-1, 30, 100, 200]},
        ):
            with self.subTest(changed=changed):
                self.apply({**deepcopy(self.message), **changed})
                self.assertIs(self.form.Region, first)
        self.assertEqual(len(self.regions), 1)
        first.Dispose.assert_not_called()

    def test_initial_quick_clip_reserves_the_actual_quick_composer_height(self):
        self.form.webview.CoreWebView2 = MagicMock()
        self.shell._companion_css_scale = 1.5
        self.shell._surface_mode = "badge"
        with patch.object(self.shell, "_companion_action") as action, \
             patch.object(self.shell, "_companion_background"):
            self.shell._initial_companion_shape({"view": "quick"})
        message = action.call_args.args[0]
        self.assertEqual(message["rect"][3], 188)
        self.assertEqual(message["parts"][1][2:4], [248, 188])
        self.assertEqual(self.shell._surface_view, "quick")


if __name__ == "__main__":
    unittest.main()
