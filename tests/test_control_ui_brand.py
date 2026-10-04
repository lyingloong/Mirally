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


if __name__ == '__main__':
    unittest.main()
