"""Native presentation boundaries and companion geometry remain product-owned."""
import unittest
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from mirally.desktop_surface import _companion_viewport_scale
from mirally.framework_shell import FrameworkShell


class PresentationBoundaryTests(unittest.TestCase):
    def test_companion_uses_actual_css_scale_when_form_dpi_is_unaware(self):
        self.assertAlmostEqual(_companion_viewport_scale((1200, 900), [800, 600], 1), 1.5)
        self.assertAlmostEqual(_companion_viewport_scale((1200, 900), [600, 450], 1), 2)
        self.assertAlmostEqual(_companion_viewport_scale((1280, 860), [853, 573], 1), 1280 / 853)

    def test_invalid_or_stale_viewports_keep_the_native_dpi_fallback(self):
        for viewport in (None, [], [0, 100], [True, 100], [float('nan'), 100],
                         [100, float('inf')], [400, 860], [1, 1], ['800', 600]):
            with self.subTest(viewport=viewport):
                self.assertEqual(_companion_viewport_scale((1200, 900), viewport, 1.25), 1.25)

    def test_window_messages_require_the_product_origin(self):
        shell = FrameworkShell(SimpleNamespace(root=Path.cwd()))
        shell._product_origin = ('http', '127.0.0.1:18789')
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

    def _context_request(self):
        shell = FrameworkShell(SimpleNamespace(root=Path.cwd()))
        shell._workspace = object()
        shell._product_origin = ('http', '127.0.0.1:18789')
        shell.window = Mock(native=Mock(webview=Mock(ClientSize=SimpleNamespace(Width=600, Height=900))))
        view = Mock(Source='http://127.0.0.1:18789/')
        args = SimpleNamespace(Handled=False, Location=SimpleNamespace(X=450, Y=300),
            ContextMenuTarget=SimpleNamespace(IsEditable=False, HasSelection=False))
        return shell, view, args

    def test_native_context_request_routes_all_companion_forms_and_views(self):
        for form in ('rail', 'badge', 'pet'):
            for presentation in ('compact', 'quick', 'menu', 'detail', 'reply'):
                with self.subTest(form=form, presentation=presentation):
                    shell, view, args = self._context_request()
                    shell._surface_mode, shell._surface_view = form, presentation
                    shell._context_menu_requested(view, args)
                    self.assertTrue(args.Handled)
                    view.ExecuteScriptAsync.assert_called_once_with(
                        'window.__mirallyCompanion?.requestContextMenu('
                        '450 * innerWidth / 600, 300 * innerHeight / 900)')

    def test_workspace_closed_and_foreign_pages_keep_the_native_context_menu(self):
        for state in ('workspace', 'closed', 'foreign'):
            with self.subTest(state=state):
                shell, view, args = self._context_request()
                if state == 'workspace':
                    shell._workspace = None
                elif state == 'closed':
                    shell.closed.set()
                else:
                    view.Source = 'https://example.com/'
                shell._context_menu_requested(view, args)
                self.assertFalse(args.Handled)
                view.ExecuteScriptAsync.assert_not_called()

    def test_editable_and_selected_text_keep_native_copy_and_paste(self):
        for attribute in ('IsEditable', 'HasSelection'):
            with self.subTest(attribute=attribute):
                shell, view, args = self._context_request()
                setattr(args.ContextMenuTarget, attribute, True)
                shell._context_menu_requested(view, args)
                self.assertFalse(args.Handled)
                view.ExecuteScriptAsync.assert_not_called()

    def test_first_startup_frame_reveals_only_the_local_opening_page(self):
        shell = FrameworkShell(SimpleNamespace(root=Path.cwd()))
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
        shell = FrameworkShell(SimpleNamespace(root=Path.cwd()))
        shell.window = Mock(native=Mock(Opacity=0))
        shell.closed.set()
        args = Mock(Source='about:blank')
        args.get_WebMessageAsJson.return_value = json.dumps({'type': 'mirally-startup-painted'})
        shell._web_message(None, args)
        self.assertEqual(shell.window.native.Opacity, 0)
        self.assertFalse(shell._startup_visible.is_set())


if __name__ == "__main__":
    unittest.main()
