"""Ensure a presentation script cannot carry a private handoff to another page."""
import unittest
import json
from unittest.mock import Mock
from openassist.control_ui_brand import brand_script, startup_message
from openassist.control_ui_shell import ControlUIShell


class PresentationBoundaryTests(unittest.TestCase):
    def test_script_has_origin_but_no_private_url_components(self):
        source = brand_script('http://127.0.0.1:18789/console?secret=private-query#bootstrap=private-fragment')
        self.assertIn('http://127.0.0.1:18789', source)
        for secret in ('private-query', 'private-fragment', '/console'):
            self.assertNotIn(secret, source)

    def test_non_web_or_credential_origins_are_rejected(self):
        for url in ('file:///private.html', 'javascript:alert(1)', 'https://key:secret@example.com/'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                brand_script(url)

    def test_internal_startup_vocabulary_is_not_product_copy(self):
        for message in ('正在连接 OpenClaw', '正在连接原生界面', '正在启动后台服务', 'unrecognized backend status'):
            display = startup_message(message)
            for word in ('OpenClaw', '原生', '后台服务', 'backend'):
                self.assertNotIn(word, display)

    def test_window_messages_require_the_gateway_origin(self):
        shell = ControlUIShell(Mock())
        shell._gateway_origin = ('http', '127.0.0.1:18789')
        shell._companion_action = Mock()
        payload = {'type': 'mirally-companion', 'action': 'dock'}
        for source in ('https://example.com/', 'file:///preview.html', 'http://127.0.0.1:19000/'):
            args = Mock(Source=source)
            args.get_WebMessageAsJson.return_value = json.dumps(payload)
            shell._web_message(None, args)
        shell._companion_action.assert_not_called()
        args = Mock(Source='http://127.0.0.1:18789/?bootstrap=private')
        args.get_WebMessageAsJson.return_value = json.dumps(payload)
        shell._web_message(None, args)
        shell._companion_action.assert_called_once_with(payload)

    def test_native_pet_context_request_opens_the_existing_menu(self):
        shell = ControlUIShell(Mock())
        shell._workspace = object()
        shell._surface_mode, shell._surface_view = 'pet', 'compact'
        view, args = Mock(), Mock(Handled=False)
        shell._context_menu_requested(view, args)
        self.assertTrue(args.Handled)
        view.ExecuteScriptAsync.assert_called_once_with("window.__mirallyCompanion?.openPetMenu()")

    def test_other_context_requests_keep_the_native_browser_behavior(self):
        shell = ControlUIShell(Mock())
        for workspace, form, presentation in (
            (None, 'pet', 'compact'),
            (object(), 'rail', 'compact'),
            (object(), 'badge', 'compact'),
            (object(), 'pet', 'quick'),
            (object(), 'pet', 'menu'),
            (object(), 'pet', 'detail'),
        ):
            with self.subTest(form=form, presentation=presentation, docked=workspace is not None):
                shell._workspace, shell._surface_mode, shell._surface_view = workspace, form, presentation
                view, args = Mock(), Mock(Handled=False)
                shell._context_menu_requested(view, args)
                self.assertFalse(args.Handled)
                view.ExecuteScriptAsync.assert_not_called()

    def test_first_startup_frame_reveals_only_the_local_opening_page(self):
        shell = ControlUIShell(Mock())
        shell.window = Mock(native=Mock(Opacity=0))
        payload = json.dumps({'type': 'mirally-startup-painted'})
        for source in ('https://example.com/', 'http://127.0.0.1:18789/', 'file:///startup.html'):
            args = Mock(Source=source)
            args.get_WebMessageAsJson.return_value = payload
            shell._web_message(None, args)
            self.assertEqual(shell.window.native.Opacity, 0)
            self.assertFalse(shell._startup_visible.is_set())
        args = Mock(Source='about:blank')
        args.get_WebMessageAsJson.return_value = payload
        shell._web_message(None, args)
        self.assertEqual(shell.window.native.Opacity, 1)
        self.assertTrue(shell._startup_visible.is_set())
        shell.window.native.Opacity = 0.5
        shell._web_message(None, args)
        self.assertEqual(shell.window.native.Opacity, 0.5)

    def test_late_startup_frame_does_not_reveal_a_closing_window(self):
        shell = ControlUIShell(Mock())
        shell.window = Mock(native=Mock(Opacity=0))
        shell.closed.set()
        args = Mock(Source='about:blank')
        args.get_WebMessageAsJson.return_value = json.dumps({'type': 'mirally-startup-painted'})
        shell._web_message(None, args)
        self.assertEqual(shell.window.native.Opacity, 0)
        self.assertFalse(shell._startup_visible.is_set())


if __name__ == '__main__':
    unittest.main()
