"""Cross-session WeChat decisions must retain both source and target ownership."""
import tempfile
import unittest
from pathlib import Path

from mirally.framework import FrameworkError, FrameworkService


class AssistantDelegationSecurityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='mirally assistant security ')
        self.addCleanup(self.temp.cleanup)
        root=Path(self.temp.name)
        self.s=FrameworkService(root,root/'home')
        self.addCleanup(self.s.close)
        self.target=self.call('conversation.ensure',title='实验')['conversation_id']
        saved=self.call('channel.weixin.save',token='fixture',peer_id='user',bot_id='bot',base_url='https://ilinkai.weixin.qq.com')['state']
        bound=self.call('channel.weixin.bind',expected_revision=saved['revision'],conversation_id=self.target)['state']
        self.call('settings.update',patch={'weixin_remote_control_enabled':True})
        self.state=self.call('channel.weixin.mode',expected_revision=bound['revision'],mode='assistant')['state']
        self.source=self.state['conversation_id']
        self.call('conversation.bind',conversation_id=self.source,session_id='native-assistant')
        self.call('conversation.bind',conversation_id=self.target,session_id='native-target')
        self.call('command.admit',conversation_id=self.source,command_id='weixin-source',text='继续实验')
        self.args={'installation_id':self.state['installation_id'],'revision':self.state['revision'],
                   'source_conversation_id':self.source,'source_command_id':'weixin-source',
                   'target_conversation_id':self.target,'target_generation':1,
                   'target_command_id':'weixin-delegate-target','content_hash':'a'*64}
        self.call('channel.weixin.delegate',**self.args)
        self.call('command.admit',conversation_id=self.target,command_id=self.args['target_command_id'],text='来自微信的委托：继续')
        self.scope={'installation_id':self.args['installation_id'],'revision':self.args['revision'],
                    'command_id':self.args['target_command_id'],'source_conversation_id':self.source,'source_command_id':'weixin-source'}

    def call(self, method, **params):
        return self.s.call(method,params)

    def check(self):
        params={key:value for key,value in self.args.items() if key not in ('target_generation','content_hash')}
        return self.call('channel.weixin.delegation_check',**params)

    def test_target_mutation_requires_exact_live_delegation_and_remote_permission(self):
        self.check()
        work=self.call('internal.work.ensure_conversation',conversation_id=self.target,title='实验',weixin_scope=self.scope)['work']
        forged={**self.scope,'source_command_id':'another'}
        with self.assertRaises(FrameworkError):
            self.call('internal.work.update_conversation',conversation_id=self.target,work_id=work['id'],patch={'goal':'伪造'},weixin_scope=forged)
        self.call('settings.update',patch={'weixin_remote_control_enabled':False})
        with self.assertRaises(FrameworkError):
            self.check()
        with self.assertRaises(FrameworkError):
            self.call('internal.work.update_conversation',conversation_id=self.target,work_id=work['id'],patch={'goal':'撤权后'},weixin_scope=self.scope)
        self.assertNotEqual(self.call('work.get',work_id=work['id'])['work']['goal'],'撤权后')

    def test_source_cancellation_and_route_change_revoke_delegation(self):
        self.call('command.ack',conversation_id=self.source,command_id='weixin-source',state='cancelled')
        with self.assertRaises(FrameworkError): self.check()
        with self.assertRaises(FrameworkError):
            self.call('internal.work.ensure_conversation',conversation_id=self.target,title='不能建立',weixin_scope=self.scope)
        state=self.call('channel.weixin.mode',expected_revision=self.state['revision'],mode='direct')['state']
        self.assertEqual(state['conversation_id'],self.target)
        with self.assertRaises(FrameworkError): self.check()

    def test_changed_target_and_request_collision_fail_closed(self):
        with self.assertRaises(FrameworkError):
            self.call('channel.weixin.delegate',**{**self.args,'content_hash':'b'*64})
        self.call('command.ack',conversation_id=self.target,command_id=self.args['target_command_id'],state='completed')
        meta=self.call('conversation.get',conversation_id=self.target)
        self.call('conversation.patch',conversation_id=self.target,expected_version=meta['metadata_version'],patch={'archived':True})
        with self.assertRaises(FrameworkError): self.check()

    def test_delivery_allows_completed_source_but_never_revoked_or_forged_scope(self):
        params={key:value for key,value in self.args.items() if key not in ('target_generation','content_hash')}
        self.call('command.ack',conversation_id=self.source,command_id='weixin-source',state='completed')
        with self.assertRaises(FrameworkError): self.check()
        self.call('channel.weixin.delegation_check',**params,delivery=True)
        with self.assertRaises(FrameworkError):
            self.call('channel.weixin.delegation_check',**{**params,'source_command_id':'forged'},delivery=True)
        self.call('settings.update',patch={'weixin_remote_control_enabled':False})
        with self.assertRaises(FrameworkError):
            self.call('channel.weixin.delegation_check',**params,delivery=True)

    def test_media_identity_checks_full_payload_even_with_same_visible_text(self):
        first=self.call('channel.weixin.admit',installation_id=self.state['installation_id'],revision=self.state['revision'],
                        event_id='voice-1',text='语音',content_identity='1'*64)
        self.assertFalse(first['event']['duplicate'])
        self.assertTrue(self.call('channel.weixin.admit',installation_id=self.state['installation_id'],revision=self.state['revision'],
                                  event_id='voice-1',text='语音',content_identity='1'*64)['event']['duplicate'])
        with self.assertRaises(FrameworkError):
            self.call('channel.weixin.admit',installation_id=self.state['installation_id'],revision=self.state['revision'],
                      event_id='voice-1',text='语音',content_identity='2'*64)


if __name__ == '__main__': unittest.main()
