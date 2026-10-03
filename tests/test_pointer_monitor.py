import ctypes
import sys
import time
import unittest
from ctypes import wintypes
from unittest.mock import Mock, patch

from openassist.raw_pointer import (
    RID_INPUT, RIDEV_INPUTSINK, RIDEV_REMOVE, WM_INPUT, RawInputDevice,
    RawInputHeader, RawMouse, RawMouseInput, RawPointerMonitor,
)
from openassist.window_session import WindowSession


@unittest.skipUnless(sys.platform == "win32", "Windows Raw Input ABI")
class RawPointerTests(unittest.TestCase):
    def setUp(self):
        # Intentionally lacks any synchronous mouse-hook APIs.
        self.user32 = Mock(spec=["CreateWindowExW", "DestroyWindow", "RegisterRawInputDevices",
                                 "GetRawInputData", "DefWindowProcW"])
        self.user32.CreateWindowExW.return_value = 123
        self.user32.RegisterRawInputDevices.return_value = True
        self.monitor = RawPointerMonitor(self.user32)
        self.packet = RawMouseInput()
        self.packet.header.kind = 0
        self.packet.header.size = ctypes.sizeof(self.packet)
        self.packet.header.device = 456
        self.packet.mouse.x = 12

        def read(handle, command, output, size, header_size):
            self.assertEqual(command, RID_INPUT)
            self.assertEqual(header_size, ctypes.sizeof(RawInputHeader))
            ctypes.memmove(output, ctypes.byref(self.packet), ctypes.sizeof(self.packet))
            return ctypes.sizeof(self.packet)

        self.user32.GetRawInputData.side_effect = read

    def test_mouse_layout_matches_windows_abi(self):
        self.assertEqual(ctypes.sizeof(RawMouse), 24)
        self.assertEqual(RawMouse.buttons.offset, 4)
        self.assertEqual(RawMouse.x.offset, 12)
        self.assertEqual(ctypes.sizeof(RawInputHeader), 24 if ctypes.sizeof(ctypes.c_void_p) == 8 else 16)

    def test_listens_in_background_without_suppressing_mouse_messages(self):
        registrations = []

        def register(pointer, count, size):
            self.assertEqual(count, 1)
            self.assertEqual(size, ctypes.sizeof(RawInputDevice))
            device = ctypes.cast(pointer, ctypes.POINTER(RawInputDevice)).contents
            registrations.append((device.usage_page, device.usage, device.flags, device.target))
            return True

        self.user32.RegisterRawInputDevices.side_effect = register
        self.assertTrue(self.monitor.start())
        self.assertEqual(self.user32.CreateWindowExW.call_args.args[8], -3)  # HWND_MESSAGE
        self.assertEqual(registrations, [(1, 2, RIDEV_INPUTSINK, 123)])
        self.monitor.close()
        self.assertEqual(registrations[-1], (1, 2, RIDEV_REMOVE, None))
        self.user32.DestroyWindow.assert_called_once_with(123)
        self.monitor.close()  # Idempotent release.
        self.user32.DestroyWindow.assert_called_once()

    def test_failed_registration_releases_window(self):
        self.user32.RegisterRawInputDevices.return_value = False
        self.assertFalse(self.monitor.start())
        self.assertIsNone(self.monitor.hwnd)
        self.user32.DestroyWindow.assert_called_once_with(123)

    def test_failed_window_creation_does_not_register_devices(self):
        self.user32.CreateWindowExW.return_value = None
        self.assertFalse(self.monitor.start())
        self.user32.RegisterRawInputDevices.assert_not_called()

    def test_hardware_motion_including_absolute_origin_is_observed(self):
        self.assertTrue(self.monitor.physical_motion(1))
        self.packet.mouse.x = 0
        self.packet.mouse.y = -8
        self.assertTrue(self.monitor.physical_motion(1))
        self.packet.mouse.y = 0
        self.packet.mouse.flags = 1
        self.assertTrue(self.monitor.physical_motion(1))

    def test_buttons_and_wheel_alone_do_not_start_hover(self):
        self.packet.mouse.x = 0
        self.packet.mouse.buttons = 0x00780400  # Wheel data and wheel flag.
        self.assertFalse(self.monitor.physical_motion(1))

    def test_device_less_and_non_mouse_events_are_ignored(self):
        self.packet.header.device = None
        self.assertFalse(self.monitor.physical_motion(1))
        self.packet.header.device = 456
        self.packet.header.kind = 1
        self.assertFalse(self.monitor.physical_motion(1))

    def test_invalid_packet_never_reuses_previous_motion(self):
        self.assertTrue(self.monitor.physical_motion(1))
        self.user32.GetRawInputData.side_effect = None
        for returned in (0xFFFFFFFF, 0, ctypes.sizeof(RawInputHeader)):
            self.user32.GetRawInputData.return_value = returned
            self.assertFalse(self.monitor.physical_motion(1))


@unittest.skipUnless(sys.platform == "win32", "Windows message-only window")
class NativeInputPumpTests(unittest.TestCase):
    def test_native_registration_hotkey_delivery_and_cleanup_without_hooks(self):
        session = WindowSession(None, demo=True)
        u = session.user32
        u.GetRegisteredRawInputDevices.argtypes = [ctypes.POINTER(RawInputDevice),
                                                   ctypes.POINTER(wintypes.UINT), wintypes.UINT]
        u.GetRegisteredRawInputDevices.restype = wintypes.UINT
        u.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        u.PostMessageW.restype = wintypes.BOOL
        thread_errors = []
        # Do not reserve the user's real global shortcuts during this check.
        with patch.object(u, "RegisterHotKey", return_value=True), \
                patch.object(u, "UnregisterHotKey", return_value=True) as unregister, \
                patch("threading.excepthook", side_effect=thread_errors.append), \
                patch.object(u, "SetWindowsHookExW", side_effect=AssertionError("Blocking input hook")) as hook:
            try:
                self.assertTrue(session.register_hotkeys())
                self.assertTrue(session.pointer_monitor_ready)
                window = session._pointer_monitor.hwnd
                count = wintypes.UINT()
                self.assertEqual(u.GetRegisteredRawInputDevices(None, ctypes.byref(count), ctypes.sizeof(RawInputDevice)), 0)
                devices = (RawInputDevice * count.value)()
                self.assertNotEqual(u.GetRegisteredRawInputDevices(devices, ctypes.byref(count), ctypes.sizeof(RawInputDevice)), 0xFFFFFFFF)
                mouse = next(d for d in devices if d.usage_page == 1 and d.usage == 2)
                self.assertEqual(mouse.target, window)
                self.assertEqual(mouse.flags, RIDEV_INPUTSINK)
                self.assertFalse(u.IsWindowVisible(window))
                # An expired/invalid Raw Input handle must be harmless and must
                # not kill the pump before later shortcut messages are handled.
                self.assertTrue(u.PostMessageW(window, WM_INPUT, 1, 0))
                for key in (1, 2, 3):
                    self.assertTrue(u.PostMessageW(window, 0x0312, key, 0))
                keys = []
                deadline = time.monotonic() + 1
                while len(keys) < 3 and time.monotonic() < deadline:
                    keys.extend(session.poll_hotkeys())
                    time.sleep(0.005)
                self.assertEqual(keys, [1, 2, 3])
                self.assertTrue(session._key_thread.is_alive())
                hook.assert_not_called()
            finally:
                session.close()
            self.assertFalse(session._key_thread.is_alive())
            self.assertFalse(session.pointer_monitor_ready)
            self.assertFalse(u.IsWindow(window))
            self.assertEqual(unregister.call_count, 3)
            self.assertEqual(thread_errors, [])


if __name__ == "__main__":
    unittest.main()
