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

    def test_job_notice_survives_route_change_but_not_unbinding(self):
        params=dict(conversation_id=self.target,generation=1,title='后台作业完成',body='查看原会话',
                    request_id='job-notification-fixture',due_at_utc_ms=self.s.clock(),
                    installation_id=self.state['installation_id'])
        self.call('channel.weixin.mode',expected_revision=self.state['revision'],mode='direct')
        first=self.call('internal.job_notification.create',**params)
        self.assertEqual(first['id'],self.call('internal.job_notification.create',**params)['id'])
        state=self.call('channel.weixin.get')['state']
        self.call('channel.weixin.unbind',expected_revision=state['revision'])
        with self.assertRaises(FrameworkError):
            self.call('internal.job_notification.create',**{**params,'request_id':'after-unbind'})

    def test_job_notice_never_moves_to_new_wechat_binding_before_tick(self):
        params=dict(conversation_id=self.target,generation=1,title='后台作业完成',body='查看原会话',
                    request_id='job-notice-rebind',due_at_utc_ms=self.s.clock(),installation_id=self.state['installation_id'])
        notice=self.call('internal.job_notification.create',**params)
        old=self.s.db.execute("SELECT destination_json FROM calendar_deliveries WHERE commitment_id=? AND channel='weixin'",(notice['id'],)).fetchone()[0]
        state=self.call('channel.weixin.get')['state']
        self.call('channel.weixin.unbind',expected_revision=state['revision'])
        saved=self.call('channel.weixin.save',token='another',peer_id='another',bot_id='bot',base_url='https://ilinkai.weixin.qq.com')['state']
        self.call('channel.weixin.bind',expected_revision=saved['revision'],conversation_id=self.target)
        self.call('delivery.tick')
        rows=self.s.db.execute("SELECT destination_json FROM calendar_deliveries WHERE commitment_id=? AND channel='weixin'",(notice['id'],)).fetchall()
        self.assertEqual([r[0] for r in rows],[old])
        delivery=self.call('calendar.delivery.list',channel='weixin',include_terminal=True)['deliveries']
        self.assertFalse(next(item for item in delivery if item['commitment_id']==notice['id'])['eligible'])
        self.assertTrue(any(item['reference_id']==notice['id'] for item in self.call('delivery.list')['deliveries']))

    def test_disabled_wechat_is_not_retroactively_added_to_job_notice(self):
        self.call('calendar.settings.update',channels={'weixin':False,'desktop':True})
        params=dict(conversation_id=self.target,generation=1,title='后台作业完成',body='查看原会话',
                    request_id='job-notice-disabled',due_at_utc_ms=self.s.clock(),installation_id=self.state['installation_id'])
        notice=self.call('internal.job_notification.create',**params)
        self.call('calendar.settings.update',channels={'weixin':True})
        self.call('delivery.tick')
        entries=self.s.db.execute('SELECT channel,status,destination_json FROM calendar_deliveries WHERE commitment_id=?',(notice['id'],)).fetchall()
        by_channel={row['channel']:row for row in entries}
        self.assertEqual(by_channel['weixin']['status'],'cancelled')
        self.assertIsNone(by_channel['weixin']['destination_json'])
        self.assertEqual(by_channel['desktop']['status'],'pending')
        self.assertTrue(any(item['reference_id']==notice['id'] for item in self.call('delivery.list')['deliveries']))

    def test_target_mutation_requires_exact_live_delegation_and_remote_permission(self):
        self.check()
        self.call('preference.set',key='experiment',value='ok',conversation_id=self.target,weixin_scope=self.scope)
        forged={**self.scope,'source_command_id':'another'}
        with self.assertRaises(FrameworkError):
            self.call('preference.set',key='forged',value='no',conversation_id=self.target,weixin_scope=forged)
        self.call('channel.weixin.enable',expected_revision=self.state['revision'],enabled=False)
        with self.assertRaises(FrameworkError):
            self.check()
        with self.assertRaises(FrameworkError):
            self.call('preference.set',key='revoked',value='no',conversation_id=self.target,weixin_scope=self.scope)
        self.assertFalse(any(item['key']=='revoked' for item in self.call('preference.list')['preferences']))

    def test_source_cancellation_and_route_change_revoke_delegation(self):
        self.call('command.ack',conversation_id=self.source,command_id='weixin-source',state='cancelled')
        with self.assertRaises(FrameworkError): self.check()
        with self.assertRaises(FrameworkError):
            self.call('preference.set',key='cancelled',value='no',conversation_id=self.target,weixin_scope=self.scope)
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
        self.call('channel.weixin.enable',expected_revision=self.state['revision'],enabled=False)
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

    def test_delegated_preference_and_workspace_writes_recheck_scope_atomically(self):
        revision=self.call('capabilities.get')['revision']
        self.call('capabilities.configure',expected_revision=revision,patch={'workspace_write_enabled':True})
        self.call('preference.set',key='voice',value='short',conversation_id=self.target,weixin_scope=self.scope)
        self.call('internal.workspace.write',path='remote.txt',content='ok',conversation_id=self.target,weixin_scope=self.scope)
        self.call('channel.weixin.enable',expected_revision=self.state['revision'],enabled=False)
        with self.assertRaises(FrameworkError):
            self.call('preference.set',key='tone',value='revoked',conversation_id=self.target,weixin_scope=self.scope)
        with self.assertRaises(FrameworkError):
            self.call('internal.workspace.write',path='revoked.txt',content='no',conversation_id=self.target,weixin_scope=self.scope)
        self.assertFalse((self.s.home/'workspace'/'revoked.txt').exists())
        self.assertFalse(any(item['key']=='tone' for item in self.call('preference.list')['preferences']))

    def test_calendar_and_product_writes_use_channel_scope_without_work_owner(self):
        revision=self.call('capabilities.get')['revision']
        self.call('capabilities.configure',expected_revision=revision,patch={'workspace_write_enabled':True})
        self.call('calendar.settings.update',time_zone='Asia/Shanghai')
        self.assertFalse(hasattr(self.s, 'work'))
        self.call('preference.set',key='channel-check',value='ok',conversation_id=self.target,weixin_scope=self.scope)
        self.call('internal.workspace.write',path='channel-scope.txt',content='ok',conversation_id=self.target,weixin_scope=self.scope)
        reminder=self.call('calendar.reminder.create',title='渠道提醒',due_at='2030-01-01T09:00:00+08:00',conversation_id=self.target,weixin_scope=self.scope)
        self.assertEqual(reminder['reminder']['title'], '渠道提醒')
        self.call('channel.weixin.enable',expected_revision=self.state['revision'],enabled=False)
        with self.assertRaises(FrameworkError):
            self.call('preference.set',key='stale-channel',value='no',conversation_id=self.target,weixin_scope=self.scope)

    def test_direct_weixin_preference_and_workspace_reject_stale_revision(self):
        state=self.call('channel.weixin.mode',expected_revision=self.state['revision'],mode='direct')['state']
        self.call('command.admit',conversation_id=self.target,command_id='weixin-direct',text='保存偏好')
        scope={'installation_id':state['installation_id'],'revision':state['revision'],'command_id':'weixin-direct'}
        self.call('preference.set',key='direct',value='yes',conversation_id=self.target,weixin_scope=scope)
        self.call('channel.weixin.select',expected_revision=state['revision'],conversation_id=self.source)
        with self.assertRaises(FrameworkError):
            self.call('preference.set',key='stale',value='no',conversation_id=self.target,weixin_scope=scope)


if __name__ == '__main__': unittest.main()
