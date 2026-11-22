from io import BytesIO
import base64
import unittest
from unittest.mock import Mock, patch

from PIL import Image

from mirally.framework import desktop_snapshot_image as snapshot_image


class DesktopSnapshotImageTests(unittest.TestCase):
    def setUp(self):
        self.frame = (-800, 100, -400, 300)
        self.window = {"hwnd": 10, "pid": 42}
        self.snapshot = {
            "window": self.window,
            "image_safe": True,
            "targets": [
                {"id": 1, "bounds": {"left": -820, "top": 130, "right": -700, "bottom": 190}},
                {"id": 2, "bounds": {"left": -390, "top": 130, "right": -380, "bottom": 190}},
            ],
        }

    def test_annotation_uses_physical_window_offset_scaling_and_clip(self):
        image = Image.new("RGB", (200, 100), "white")
        ids = snapshot_image._annotate(image, self.frame, self.snapshot["targets"])
        self.assertEqual(ids, [1])
        self.assertEqual(image.getpixel((49, 25)), snapshot_image.LABEL_COLOR)
        self.assertEqual(image.getpixel((51, 25)), (255, 255, 255))

    def test_equal_sized_frames_with_distinct_origins_are_ambiguous(self):
        different_origin = (-790, 100, -390, 300)
        self.assertIsNone(snapshot_image._matching_frame({self.frame, different_origin}, (400, 200)))
        self.assertEqual(snapshot_image._matching_frame({self.frame}, (400, 200)), self.frame)

    def test_incomplete_privacy_scan_avoids_capture(self):
        self.snapshot["image_safe"] = False
        read_window = Mock()
        self.assertEqual(snapshot_image.capture_annotated_window(self.snapshot, read_window),
                         {"status": "unavailable", "reason": "control_privacy_undetermined"})
        read_window.assert_not_called()

    def test_password_mask_uses_the_same_window_projection(self):
        image = Image.new("RGB", (200, 100), "white")
        snapshot_image._mask_controls(image, self.frame, [self.snapshot["targets"][0]["bounds"]])
        self.assertEqual(image.getpixel((25, 25)), (10, 10, 10))
        self.assertEqual(image.getpixel((51, 25)), (255, 255, 255))

    @patch.object(snapshot_image.ctypes, "WinDLL", create=True)
    @patch.object(snapshot_image, "_window_frames")
    @patch.object(snapshot_image, "_capture_wgc", return_value=None)
    @patch.object(snapshot_image, "_client_frame")
    @patch.object(snapshot_image, "print_client_window")
    def test_client_fallback_uses_only_the_requested_window(self, print_window, client_frame,
                                                             wgc, frames, win_dll):
        client_frame.return_value = self.frame
        print_window.return_value = Image.new("RGB", (400, 200), "white")
        result = snapshot_image.capture_annotated_window(self.snapshot, lambda: self.window)
        print_window.assert_called_once_with(win_dll.return_value, 10, 400, 200)
        self.assertEqual(result["source"], "window")
        self.assertEqual(result["annotated_targets"], [1])
        with Image.open(BytesIO(base64.b64decode(result["data"]))) as image:
            self.assertEqual(image.format, "JPEG")
            self.assertEqual(image.size, (400, 200))

    @patch.object(snapshot_image.ctypes, "WinDLL", create=True)
    @patch.object(snapshot_image, "_window_frames")
    @patch.object(snapshot_image, "_capture_wgc")
    @patch.object(snapshot_image, "print_client_window")
    def test_window_replacement_drops_frame_without_capturing_again(self, print_window, wgc,
                                                                   frames, win_dll):
        wgc.return_value = (Image.new("RGB", (200, 100), "white"), self.frame)
        reread = Mock(side_effect=[self.window, {"hwnd": 10, "pid": 99}])
        result = snapshot_image.capture_annotated_window(self.snapshot, reread)
        self.assertEqual(result, {"status": "unavailable", "reason": "window_changed"})
        print_window.assert_not_called()
