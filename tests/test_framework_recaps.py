from pathlib import Path
import tempfile
import unittest
from mirally.framework import FrameworkError, FrameworkService

class RecapTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.now=1000000
        self.s=FrameworkService(Path(self.tmp.name),Path(self.tmp.name)/'home',clock=lambda:self.now)
        self.addCleanup(self.s.close)
        self.s.call('conversation.ensure',{'conversation_id':'a'})
        self.s.call('conversation.bind',{'conversation_id':'a','session_id':'native'})
        self.command('one')
    def command(self, identity):
        self.s.call('command.admit',{'conversation_id':'a','command_id':identity,'text':'hello'})
        self.s.call('command.ack',{'conversation_id':'a','command_id':identity,'state':'completed'})
    def reserve(self, manual=True):
        return self.s.call('internal.recap.reserve',{'conversation_id':'a','manual':manual})
    def finish(self, lease, title='自动标题'):
        return self.s.call('internal.recap.complete',{'attempt_id':lease['id'],'summary':'讨论了实验计划。','title':title})
    def test_disabled_automatic_and_manual_title_protection(self):
        self.assertEqual(self.reserve(False)['status'],'disabled')
        lease=self.reserve()
        c=self.s.call('conversation.get',{'conversation_id':'a'})
        self.s.call('conversation.patch',{'conversation_id':'a','expected_version':c['metadata_version'],'patch':{'title':'用户标题'}})
        self.assertTrue(self.finish(lease)['changed'])
        c=self.s.call('conversation.get',{'conversation_id':'a'})
        self.assertEqual(c['title'],'用户标题'); self.assertEqual(c['recap']['text'],'讨论了实验计划。')
    def test_newer_command_and_generation_invalidate_results(self):
        lease=self.reserve();self.command('two')
        self.assertFalse(self.finish(lease)['changed'])
        lease=self.reserve();self.assertTrue(self.finish(lease)['changed'])
        self.assertFalse(self.s.call('conversation.get',{'conversation_id':'a'})['recap']['stale'])
        self.command('three')
        self.assertTrue(self.s.call('conversation.get',{'conversation_id':'a'})['recap']['stale'])
        self.s.call('conversation.rotate',{'conversation_id':'a','reason':'source changed'})
        self.assertIsNone(self.s.call('conversation.get',{'conversation_id':'a'})['recap'])
    def test_auto_title_only_generated_once(self):
        self.assertTrue(self.finish(self.reserve())['changed'])
        self.command('two')
        self.assertTrue(self.finish(self.reserve(),'不同标题')['changed'])
        self.assertEqual(self.s.call('conversation.get',{'conversation_id':'a'})['title'],'自动标题')

    def test_source_revocation_hides_stored_summary_and_rejects_pending_update(self):
        source = self.s.call('source.set', {'source_id':'document','enabled':True})
        self.s._record_uses('a', 1, [{'source_id':'document','version':source['version']}])
        self.assertTrue(self.finish(self.reserve())['changed'])
        pending = self.reserve()
        self.s.call('source.set', {'source_id':'document','enabled':False})
        self.assertIsNone(self.s.call('conversation.get', {'conversation_id':'a'})['recap'])
        self.assertFalse(self.finish(pending)['changed'])

    def enable_background(self):
        self.s.call('settings.update', {'patch':{'background_enabled':True}})

    def test_auto_reserve_is_deferred_until_cooldown_elapses(self):
        self.enable_background()
        self.assertTrue(self.finish(self.reserve())['changed'])
        self.assertEqual(self.reserve(False)['status'],'deferred')
        self.now += 600000
        self.assertEqual(self.reserve(False)['status'],'reserved')

    def test_auto_daily_budget_blocks_thirteenth_reserve_and_manual_still_reserves(self):
        self.enable_background()
        for _ in range(12):
            self.now += 600000
            self.assertEqual(self.reserve(False)['status'],'reserved')
        self.now += 600000
        self.assertEqual(self.reserve(False)['status'],'budget')
        self.assertEqual(self.reserve()['status'],'reserved')

    def test_expired_pending_is_reclaimed_and_manual_reserve_succeeds(self):
        self.assertEqual(self.reserve()['status'],'reserved')
        self.assertEqual(self.reserve()['status'],'pending')
        self.now += 360000
        self.assertEqual(self.reserve()['status'],'reserved')

    def test_unfinished_command_and_archive_return_unavailable(self):
        self.s.call('command.admit', {'conversation_id':'a','command_id':'two','text':'follow-up'})
        self.assertEqual(self.reserve()['status'],'unavailable')
        self.s.call('command.ack', {'conversation_id':'a','command_id':'two','state':'completed'})
        self.assertTrue(self.finish(self.reserve())['changed'])
        c = self.s.call('conversation.get', {'conversation_id':'a'})
        self.s.call('conversation.patch', {'conversation_id':'a','expected_version':c['metadata_version'],'patch':{'archived':True}})
        self.assertEqual(self.reserve()['status'],'unavailable')
        self.assertIsNone(self.s.call('conversation.get', {'conversation_id':'a'})['recap'])

    def test_complete_rejects_unknown_attempt_and_invalid_params(self):
        self.assertFalse(self.s.call('internal.recap.complete', {'attempt_id':'no-such-attempt','summary':'讨论了实验计划。'})['changed'])
        for params in ({'attempt_id':'no-such-attempt','summary':'摘'*1001},
                       {'attempt_id':'no-such-attempt','summary':123}):
            with self.subTest(summary=params['summary'].__class__.__name__ if not isinstance(params['summary'],str) else 'oversized'), self.assertRaises(FrameworkError) as error:
                self.s.call('internal.recap.complete', params)
            self.assertEqual(error.exception.code,'INVALID_PARAMS')
        with self.assertRaises(FrameworkError) as error:
            self.s.call('internal.recap.reserve', {'conversation_id':'a','manual':'yes'})
        self.assertEqual(error.exception.code,'INVALID_PARAMS')
