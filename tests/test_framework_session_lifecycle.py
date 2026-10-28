"""Product stop is distinct from DSH transcript invalidation."""

from pathlib import Path
import tempfile
import unittest

from mirally.framework import FrameworkService


class FrameworkSessionLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='mirally sessions ')
        self.root = Path(self.temp.name)
        self.service = FrameworkService(self.root, self.root / '个人 数据')
        self.service.conversation_ensure(conversation_id='chat')
        self.service.conversation_bind('chat', 'native-original')

    def tearDown(self):
        self.service.close()
        self.temp.cleanup()

    def test_stop_keeps_native_session_but_cancels_consumed_and_queued_commands(self):
        for identity in ('active', 'queued'):
            self.service.command_admit('chat', identity, identity)
        stopped = self.service.interaction_stop('chat', 'active')
        self.assertCountEqual(stopped['cancelled_command_ids'], ['active', 'queued'])
        self.assertFalse(stopped['frozen'])
        self.assertFalse(self.service.guard_check('chat', command_id='active')['allow'])
        resumed = self.service.conversation_prepare_input('chat')
        self.assertEqual((resumed['generation'], resumed['session_id']), (1, 'native-original'))
        continued = self.service.command_admit('chat', 'continue', '继续')
        self.assertEqual(continued['session_id'], 'native-original')
        self.assertTrue(self.service.guard_check('chat', command_id='continue')['allow'])
        self.assertEqual(self.service.command_ack('chat', 'active', 'completed')['state'], 'cancelled')

    def test_legacy_user_stop_can_resume_same_durable_transcript_after_restart(self):
        self.service.db.execute("UPDATE generations SET frozen=1,reason='user_stopped' WHERE conversation_id='chat'")
        self.service.close()
        self.service = FrameworkService(self.root, self.root / '个人 数据')
        resumed = self.service.conversation_prepare_input('chat')
        self.assertFalse(resumed['frozen'])
        self.assertEqual((resumed['generation'], resumed['session_id']), (1, 'native-original'))

    def test_legacy_stop_does_not_restore_withdrawn_sources(self):
        self.service.command_admit('chat', 'one', 'private', sources=[{'source_id': 'private', 'version': 1}])
        self.service.db.execute("UPDATE generations SET frozen=1,reason='user_stopped' WHERE conversation_id='chat'")
        self.service.source_set('private', False)
        resumed = self.service.conversation_prepare_input('chat')
        self.assertEqual(resumed['generation'], 2)
        self.assertIsNone(resumed['session_id'])

    def test_stop_does_not_unfreeze_or_relabel_a_privacy_boundary(self):
        self.service.command_admit('chat', 'one', 'private', sources=[{'source_id': 'private', 'version': 1}])
        self.service.source_set('private', False)
        self.service.interaction_stop('chat')
        self.assertTrue(self.service.conversation_get('chat')['frozen'])
        reason = self.service.db.execute("SELECT reason FROM generations WHERE conversation_id='chat' AND generation=1").fetchone()[0]
        self.assertNotEqual(reason, 'user_stopped')
        self.assertEqual(self.service.conversation_prepare_input('chat')['generation'], 2)

    def test_fork_reserves_one_native_prefix_and_inherits_source_withdrawal(self):
        self.service.command_admit('chat', 'one', 'private', sources=[{'source_id':'private','version':1}])
        args=dict(conversation_id='fork',parent_conversation_id='chat',parent_generation=1,
                  parent_session_id='native-original',boundary_seq=10,request_hash='a'*64)
        fork=self.service.conversation_fork(**args)
        self.assertEqual(fork['fork']['boundary_seq'],10)
        self.assertEqual(self.service.conversation_fork(**{**args,'boundary_seq':20})['fork']['boundary_seq'],10)
        self.service.conversation_bind('fork','native-fork')
        self.service.source_set('private',False)
        self.assertTrue(self.service.conversation_get('fork')['frozen'])
        with self.assertRaisesRegex(ValueError,'no longer active'):
            self.service.conversation_fork(**args)

    def test_schema7_migrates_fork_references_without_changing_native_owner(self):
        self.service.db.execute('DROP TABLE conversation_forks')
        self.service.db.execute('PRAGMA user_version=7')
        self.service.close()
        self.service=FrameworkService(self.root,self.root / '个人 数据')
        self.assertEqual(self.service.call('system.status')['schema_version'],8)
        self.assertEqual(self.service.conversation_get('chat')['session_id'],'native-original')
        self.assertEqual(self.service.db.execute('SELECT COUNT(*) FROM conversation_forks').fetchone()[0],0)
