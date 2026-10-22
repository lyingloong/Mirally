"""The desktop settings bridge cannot grant collection through page browsing."""

import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from mirally.control_ui_shell import ControlUIShell


class ActivitySettingsBridgeTests(unittest.TestCase):
    def setUp(self):
        self.shell = ControlUIShell(Path.cwd())
        self.shell._gateway_origin = ('http', '127.0.0.1:18789')
        self.shell.window = Mock()
        self.shell.window.get_current_url.return_value = 'http://127.0.0.1:18789/settings/appearance?mirally=activity'
        self.backend = Mock()
        self.shell._activity_settings = self.backend

    def message(self, source, action='status', request_id='request-one'):
        args = Mock(Source=source)
        args.get_WebMessageAsJson.return_value = json.dumps({
            'type': 'mirally-activity', 'action': action, 'requestId': request_id,
        })
        return args

    def test_only_the_gateway_origin_can_dispatch_known_actions(self):
        with patch('mirally.control_ui_shell.threading.Thread') as thread:
            for source in ('https://example.com/', 'file:///private.html', 'http://127.0.0.1:19000/'):
                self.shell._web_message(None, self.message(source))
            self.shell._web_message(None, self.message('http://127.0.0.1:18789/', 'arbitrary-command'))
            self.shell._web_message(None, self.message('http://127.0.0.1:18789/', request_id=None))
            thread.assert_not_called()
            self.shell._web_message(None, self.message('http://127.0.0.1:18789/'))
            thread.assert_called_once()
            thread.return_value.start.assert_called_once()

    def test_browsing_and_status_never_start_collection_or_confirm(self):
        self.backend.status.return_value = {'activity': {}, 'memory': {}}
        self.backend.open_browser.return_value = {'opened': True}
        for action in ('status', 'browse', 'monitor'):
            self.shell._activity_action({'action': action, 'requestId': 'read-one'})
        self.backend.start_capture.assert_not_called()
        self.shell.window.create_confirmation_dialog.assert_not_called()
        self.assertEqual(self.backend.open_browser.call_args_list[0].args, ('history',))
        self.assertEqual(self.backend.open_browser.call_args_list[1].args, ('monitor',))

    def test_start_does_not_request_repeated_confirmation(self):
        self.backend.status.return_value = {'activity': {'capture': [{'label': '屏幕截图', 'enabled': True}]}}
        self.shell.window.create_confirmation_dialog.return_value = False
        self.shell._activity_action({'action': 'start', 'requestId': 'start-one'})
        self.backend.start_capture.assert_called_once()
        self.shell.window.create_confirmation_dialog.assert_not_called()

    def test_start_rechecks_the_current_scope(self):
        activity = {'capture': [{'label': '窗口活动', 'enabled': True}], 'config_path': 'synthetic.yaml'}
        self.backend.status.return_value = {'activity': activity}
        self.backend.start_capture.return_value = {'collector': {'status': 'running'}}
        self.shell.window.create_confirmation_dialog.return_value = True
        self.shell._activity_action({'action': 'start', 'requestId': 'start-one'})
        self.backend.start_capture.assert_called_once_with(expected_activity=activity)

    def test_closing_during_status_read_cannot_start_collection(self):
        self.backend.status.return_value = {'activity': {}}

        def close(*_):
            self.shell.closed.set()
            return {'activity': {}}

        self.backend.status.side_effect = close
        self.shell._activity_action({'action': 'start', 'requestId': 'start-one'})
        self.backend.start_capture.assert_not_called()

    def test_persistent_switch_validates_and_forwards_boolean(self):
        for value in (None, 'true', 1, {}, []):
            self.shell._activity_action({'action':'set-enabled','requestId':'switch','enabled':value})
        self.backend.set_enabled.assert_not_called()
        self.backend.set_enabled.return_value = {'saved':True,'enabled':False}
        self.shell._activity_action({'action':'set-enabled','requestId':'switch','enabled':False})
        self.backend.set_enabled.assert_called_once_with(False)
        self.shell.window.create_confirmation_dialog.assert_not_called()

    def test_semantic_settings_bridge_only_accepts_known_preferences(self):
        self.shell._activity_action({'action':'set-semantics','requestId':'semantic','enabled':True,
                                    'model':'','maxCallsPerHour':24,'maxTokensPerDay':60000})
        self.backend.set_semantics.assert_called_once_with(True,model='',model_provided=True,
            max_calls_per_hour=24,max_tokens_per_day=60000)
        self.backend.set_semantics.reset_mock()
        for payload in ({'path':'private.yaml'},{'maxCallsPerHour':None},{'maxTokensPerDay':True}):
            self.shell._activity_action({'action':'set-semantics','requestId':'semantic','enabled':True,**payload})
        self.backend.set_semantics.assert_not_called()

    def test_model_settings_read_has_no_capture_side_effect_and_rejects_extra_fields(self):
        self.backend.model_settings.return_value = {"model": "", "canSave": True}
        self.shell._activity_action({'action': 'model-settings', 'requestId': 'models'})
        self.backend.model_settings.assert_called_once_with()
        self.backend.start_capture.assert_not_called()
        self.shell.window.create_confirmation_dialog.assert_not_called()
        self.backend.model_settings.reset_mock()
        self.shell._activity_action({'action': 'model-settings', 'requestId': 'models', 'path': 'private'})
        self.backend.model_settings.assert_not_called()

    def test_model_only_bridge_strictly_validates_fields_and_output_cap(self):
        self.backend.set_semantic_model.return_value = {'saved': True, 'applied': False}
        self.shell._activity_action({'action': 'set-semantic-model', 'requestId': 'models',
                                    'model': 'provider/utility', 'maxOutputTokens': 2000})
        self.backend.set_semantic_model.assert_called_once_with('provider/utility', max_output_tokens=2000)
        self.backend.set_semantic_model.reset_mock()
        for payload in ({}, {'model': None}, {'model': None, 'maxOutputTokens': 800},
                        {'model': False}, {'model': 'bad model'}, {'model': 'x' * 513},
                        {'model': '', 'maxOutputTokens': None}, {'model': '', 'maxOutputTokens': True},
                        {'maxOutputTokens': None},
                        {'model': '', 'maxOutputTokens': 127}, {'model': '', 'maxOutputTokens': 16001},
                        {'model': '', 'enabled': True}, {'model': '', 'path': 'private'}):
            self.shell._activity_action({'action': 'set-semantic-model', 'requestId': 'models', **payload})
        self.backend.set_semantic_model.assert_not_called()
        self.shell._activity_action({'action': 'set-semantic-model', 'requestId': 'models', 'model': ''})
        self.backend.set_semantic_model.assert_called_once_with('', max_output_tokens=None)
        self.backend.set_semantic_model.reset_mock()
        self.shell._activity_action({'action': 'set-semantic-model', 'requestId': 'models', 'maxOutputTokens': 2500})
        self.backend.set_semantic_model.assert_called_once_with(None, max_output_tokens=2500)
        self.shell.window.create_confirmation_dialog.assert_not_called()

    def test_model_only_write_uses_serialized_control_lane_and_gateway_origin(self):
        self.shell._activity_control_lock.acquire()
        try:
            self.shell._activity_action({'action': 'set-semantic-model', 'requestId': 'models', 'model': ''})
            self.backend.set_semantic_model.assert_not_called()
        finally:
            self.shell._activity_control_lock.release()
        with patch('mirally.control_ui_shell.threading.Thread') as thread:
            self.shell._web_message(None, self.message('https://example.test/', 'model-settings'))
            thread.assert_not_called()
            self.shell._web_message(None, self.message('http://127.0.0.1:18789/', 'model-settings'))
            thread.assert_called_once()

    def test_memory_browser_filters_reach_only_the_read_only_search_method(self):
        filters = {'provenances': ['model_inference'], 'kinds': ['episodic', 'semantic'],
                   'start_utc_ms': 100, 'end_utc_ms': 200}
        self.backend.memory_search.return_value = {'memories': []}
        self.shell._activity_action({'action': 'memory-search', 'requestId': 'filters', 'query': 'project', **filters})
        self.backend.memory_search.assert_called_once_with('project', include_candidates=False, limit=20, offset=0, **filters)
        self.backend.start_capture.assert_not_called()
        self.backend.set_semantics.assert_not_called()
        self.shell.window.create_confirmation_dialog.assert_not_called()

    def test_memory_browser_rejects_null_boolean_unknown_or_unbounded_filters(self):
        for filters in ({'provenances': None}, {'provenances': 'observed'}, {'provenances': [True]},
                        {'provenances': ['unknown']}, {'provenances': ['observed'] * 4}, {'kinds': None},
                        {'kinds': [False]}, {'kinds': ['unknown']}, {'kinds': ['episodic'] * 5},
                        {'start_utc_ms': None}, {'start_utc_ms': True}, {'end_utc_ms': -1},
                        {'end_utc_ms': 2**53}, {'start_utc_ms': 200, 'end_utc_ms': 100},
                        {'start_utc_ms': 100, 'end_utc_ms': 100}, {'scope': 'other'}, {'path': 'private'}):
            with self.subTest(filters=filters):
                self.shell._activity_action({'action': 'memory-search', 'requestId': 'filters', **filters})
        self.backend.memory_search.assert_not_called()

    def test_startup_runs_once_per_window_and_never_after_close(self):
        self.shell._auto_start_activity()
        self.shell._auto_start_activity()
        self.backend.auto_start_capture.assert_called_once()
        other = ControlUIShell(Path.cwd())
        other._activity_settings = Mock()
        other.closed.set()
        other._auto_start_activity()
        other._activity_settings.auto_start_capture.assert_not_called()

    def test_switch_uses_same_serialized_control_lane(self):
        self.shell._activity_control_lock.acquire()
        try:
            self.shell._activity_action({'action':'set-enabled','requestId':'switch','enabled':False})
            self.backend.set_enabled.assert_not_called()
        finally:
            self.shell._activity_control_lock.release()

    def test_overlapping_capture_controls_are_rejected_without_queueing(self):
        self.shell._activity_control_lock.acquire()
        try:
            self.shell._activity_action({'action': 'start', 'requestId': 'second-start'})
            self.shell._activity_action({'action': 'stop', 'requestId': 'old-stop', 'session_id': 'old'})
            self.backend.status.assert_not_called()
            self.backend.start_capture.assert_not_called()
            self.backend.stop_capture.assert_not_called()
            self.shell.window.create_confirmation_dialog.assert_not_called()
        finally:
            self.shell._activity_control_lock.release()

    def test_capture_controls_require_and_forward_the_session_identity(self):
        for action in ('pause', 'resume', 'stop'):
            self.shell._activity_action({'action': action, 'requestId': 'invalid-control'})
            getattr(self.backend, action + '_capture').assert_not_called()
            self.shell._activity_action({'action': action, 'requestId': 'valid-control', 'session_id': 'current'})
            getattr(self.backend, action + '_capture').assert_called_once_with(expected_session_id='current')

    def test_a_response_is_not_delivered_to_a_different_origin(self):
        self.shell.window.get_current_url.return_value = 'https://example.com/'
        self.shell._activity_result('read-one', result={'private_memory': 'synthetic'})
        self.shell.window.evaluate_js.assert_not_called()

    def test_queries_are_bounded_without_accepting_paths_or_commands(self):
        self.shell._activity_action({'action': 'memory-search', 'requestId': 'read-one', 'query': 'x' * 513})
        self.shell._activity_action({'action': 'memory-search', 'requestId': 'read-one', 'include_candidates': 'true'})
        self.shell._activity_action({'action': 'memory-search', 'requestId': 'read-one', 'limit': True})
        self.backend.memory_search.assert_not_called()
        self.backend.memory_search.return_value = {'memories': []}
        self.shell._activity_action({'action': 'memory-search', 'requestId': 'read-one', 'query': '会议', 'limit': 10})
        self.backend.memory_search.assert_called_once_with('会议', include_candidates=False, limit=10, offset=0)

    def test_memory_pagination_is_validated_and_forwarded(self):
        for offset in (-1, 10001, True, '20'):
            self.shell._activity_action({'action': 'memory-search', 'requestId': 'page-one', 'offset': offset})
        self.backend.memory_search.assert_not_called()
        self.shell._activity_action({'action': 'memory-search', 'requestId': 'page-one', 'query': '会议', 'offset': 20})
        self.backend.memory_search.assert_called_once_with('会议', include_candidates=False, limit=20, offset=20)
        for parameters in ({'from_line': 0}, {'from_line': 10001}, {'from_line': True}, {'line_count': 201}, {'line_count': '100'}):
            self.shell._activity_action({'action': 'memory-read', 'requestId': 'detail-one', 'memory_id': 'synthetic', **parameters})
        self.backend.memory_read.assert_not_called()
        self.shell._activity_action({'action': 'memory-read', 'requestId': 'detail-one', 'memory_id': 'synthetic', 'from_line': 101, 'line_count': 100})
        self.backend.memory_read.assert_called_once_with('synthetic', from_line=101, line_count=100)

    def test_close_releases_only_the_window_owned_services(self):
        self.shell.host = Mock()
        self.shell._close()
        self.backend.close.assert_called_once()
        self.shell.host.close.assert_called_once()
        self.shell.host.restart.assert_not_called()
        self.assertIsNone(self.shell._activity_settings)

    def test_a_failed_close_retains_the_backend_for_retry(self):
        self.backend.close.side_effect = [RuntimeError('synthetic shutdown timeout'), None]
        with self.assertRaises(RuntimeError):
            self.shell._close()
        self.assertIs(self.shell._activity_settings, self.backend)
        self.shell._close()
        self.assertIsNone(self.shell._activity_settings)


if __name__ == '__main__':
    unittest.main()
