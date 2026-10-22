"""Exercise the native menu adapter on isolated, hidden Windows HWNDs."""
import ctypes
import sys
import threading
import unittest
from unittest.mock import patch


@unittest.skipUnless(sys.platform == 'win32', 'Windows system menus')
class WindowSystemMenuTests(unittest.TestCase):
    def setUp(self):
        from mirally.platform.window_menu import WindowSystemMenu
        self.adapter = WindowSystemMenu
        self.user = ctypes.WinDLL('user32', use_last_error=True)
        self.user.CreateWindowExW.argtypes = [ctypes.c_uint, ctypes.c_wchar_p, ctypes.c_wchar_p,
            ctypes.c_uint, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
        self.user.CreateWindowExW.restype = ctypes.c_void_p
        self.user.DestroyWindow.argtypes = [ctypes.c_void_p]
        self.user.GetSystemMenu.argtypes = [ctypes.c_void_p, ctypes.c_bool]
        self.user.GetSystemMenu.restype = ctypes.c_void_p
        self.user.GetMenuItemCount.argtypes = [ctypes.c_void_p]
        self.user.GetMenuItemID.argtypes = [ctypes.c_void_p, ctypes.c_int]
        self.user.GetMenuItemID.restype = ctypes.c_uint
        self.user.GetMenuStringW.argtypes = [ctypes.c_void_p, ctypes.c_uint,
            ctypes.c_wchar_p, ctypes.c_int, ctypes.c_uint]
        self.user.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
        self.user.SendMessageW.restype = ctypes.c_ssize_t
        self.hwnd = self.user.CreateWindowExW(0, 'STATIC', 'Mirally isolated menu test',
            0x00CF0000, 0, 0, 320, 240, None, None, None, None)
        self.assertTrue(self.hwnd)
        self.addCleanup(lambda: self.user.DestroyWindow(self.hwnd) if self.hwnd else None)

    def items(self):
        menu = self.user.GetSystemMenu(self.hwnd, False)
        result = []
        for position in range(self.user.GetMenuItemCount(menu)):
            label = ctypes.create_unicode_buffer(256)
            self.user.GetMenuStringW(menu, position, label, len(label), 0x400)
            result.append((self.user.GetMenuItemID(menu, position), label.value))
        return result

    def test_install_preserves_windows_commands_and_close_restores_menu(self):
        original = self.items()
        instance = self.adapter(self.hwnd, [('菜单检查', lambda: None), None,
            ('第二项', lambda: None)], dispatch=lambda callback: callback())
        self.addCleanup(instance.close)
        instance.install()
        self.assertTrue(instance.installed)
        installed = self.items()
        self.assertEqual([label for _, label in installed[:3]], ['菜单检查', '', '第二项'])
        self.assertTrue(all(item in installed for item in original))
        self.assertIn(0xF060, [identifier for identifier, _ in installed])
        instance.install()
        self.assertEqual(self.items(), installed, 'Installation must be idempotent')
        instance.close()
        self.assertFalse(instance.installed)
        self.assertEqual(self.items(), original)
        instance.close()

    def test_native_command_dispatches_off_ui_thread_and_survives_frame_changes(self):
        called = threading.Event()
        threads = []
        def callback():
            threads.append(threading.get_ident())
            called.set()
        instance = self.adapter(self.hwnd, [('Dispatch', callback)],
            dispatch=lambda action: threading.Thread(target=action, daemon=True).start())
        self.addCleanup(instance.close)
        instance.install()
        identifier = next(identifier for identifier, label in self.items() if label == 'Dispatch')
        self.assertLess(identifier, 0xF000)
        self.assertEqual(identifier & 15, 0)
        # Windows reserves the low four WM_SYSCOMMAND bits for internal use.
        self.user.SendMessageW(self.hwnd, 0x112, identifier | 3, 0)
        self.assertTrue(called.wait(2))
        self.assertNotEqual(threads[0], threading.get_ident())
        self.user.GetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int]
        self.user.GetWindowLongPtrW.restype = ctypes.c_ssize_t
        self.user.SetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]
        style = self.user.GetWindowLongPtrW(self.hwnd, -16)
        self.user.SetWindowLongPtrW(self.hwnd, -16, style & ~0x00C40000)
        self.user.SetWindowLongPtrW(self.hwnd, -16, style)
        called.clear()
        self.user.SendMessageW(self.hwnd, 0x112, identifier, 0)
        self.assertTrue(called.wait(2))
        self.assertTrue(instance.installed)

    def test_destroyed_window_detaches_without_retaining_callback_hook(self):
        instance = self.adapter(self.hwnd, [('Dispatch', lambda: None)], dispatch=lambda action: action())
        instance.install()
        self.assertTrue(self.user.DestroyWindow(self.hwnd))
        self.hwnd = None
        self.assertFalse(instance.installed)
        instance.close()

    def test_failed_subclass_install_rolls_back_owned_items_only(self):
        original = self.items()
        instance = self.adapter(self.hwnd, [('Failed install', lambda: None), None],
            dispatch=lambda action: action())
        with patch.object(instance._common, 'SetWindowSubclass', return_value=0):
            with self.assertRaises(OSError):
                instance.install()
        self.assertFalse(instance.installed)
        self.assertEqual(self.items(), original)

    def test_dispatch_exception_stays_inside_callback_and_windows_minimize_still_works(self):
        def broken_dispatch(action):
            raise RuntimeError('Synthetic dispatch failure')
        instance = self.adapter(self.hwnd, [('Failure', lambda: None)], dispatch=broken_dispatch)
        self.addCleanup(instance.close)
        instance.install()
        identifier = next(identifier for identifier, label in self.items() if label == 'Failure')
        with self.assertLogs('mirally.platform.window_menu', level='ERROR') as logs:
            self.user.SendMessageW(self.hwnd, 0x112, identifier, 0)
        self.assertIn('Synthetic dispatch failure', '\n'.join(logs.output))
        self.user.IsIconic.argtypes = [ctypes.c_void_p]
        self.user.SendMessageW(self.hwnd, 0x112, 0xF020, 0)  # SC_MINIMIZE
        self.assertTrue(self.user.IsIconic(self.hwnd))
        self.user.SendMessageW(self.hwnd, 0x112, 0xF060, 0)  # SC_CLOSE
        self.user.IsWindow.argtypes = [ctypes.c_void_p]
        self.assertFalse(self.user.IsWindow(self.hwnd))
        self.hwnd = None
        self.assertFalse(instance.installed)


if __name__ == '__main__':
    unittest.main()
