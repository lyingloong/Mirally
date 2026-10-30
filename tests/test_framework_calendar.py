"""Calendar persistence, timezone semantics and multi-channel reminders."""
from pathlib import Path
import tempfile
import unittest

from mirally.framework import FrameworkError, FrameworkService
from mirally.framework.service import SCHEMA_VERSION


class CalendarTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='mirally 日历 ')
        self.addCleanup(self.temp.cleanup)
        self.now = 1793200000000
        self.home = Path(self.temp.name) / 'home'
        self.service = FrameworkService(Path(self.temp.name), self.home, clock=lambda: self.now)
        self.addCleanup(lambda: self.service.close())
        self.conv = self.service.call('conversation.ensure', {'title':'会议'})['conversation_id']

    def call(self, method, **params):
        return self.service.call(method, params)

    def setup_zone(self):
        return self.call('calendar.settings.update', time_zone='Asia/Shanghai')

    def bind_weixin(self):
        saved = self.call('channel.weixin.save',token='fixture',peer_id='peer',bot_id='bot',base_url='https://ilinkai.weixin.qq.com')['state']
        state = self.call('channel.weixin.bind',expected_revision=saved['revision'],conversation_id=self.conv)['state']
        return {'installation_id':state['installation_id'],'revision':state['revision']}

    def create(self, **extra):
        args = {'title':'会议', 'start_at':'2026-10-30T11:00:00+08:00',
                'reminder_offsets_minutes':[60], 'conversation_id':self.conv, 'request_id':'request-1'}
        args.update(extra)
        return self.call('calendar.event.create', **args)['event']

    def test_timezone_requires_confirmation_and_resolves_explicitly(self):
        self.assertFalse(self.call('calendar.settings.get')['confirmed'])
        with self.assertRaises(FrameworkError):
            self.call('calendar.time.resolve', local_time='2026-10-30T11:00')
        self.setup_zone()
        result = self.call('calendar.time.resolve', local_time='2026-10-30T11:00')
        self.assertEqual(result['iso'], '2026-10-30T11:00:00+08:00')
        self.assertEqual(self.call('calendar.settings.get')['time_zone'], 'Asia/Shanghai')
        with self.assertRaises(FrameworkError):
            self.call('calendar.time.resolve', local_time='2026-03-08T02:30', time_zone='America/New_York')
        with self.assertRaises(FrameworkError):
            self.call('calendar.time.resolve', local_time='2026-11-01T01:30', time_zone='America/New_York')

    def test_event_and_reminder_are_atomic_idempotent_and_multichannel(self):
        self.setup_zone()
        destination = self.bind_weixin()
        event = self.create()
        self.assertEqual(self.create()['event_id'], event['event_id'])
        self.assertEqual(event['start_at'], '2026-10-30T11:00:00+08:00')
        self.assertEqual(event['conversation_id'], self.conv)
        with self.assertRaises(FrameworkError):
            self.create(title='不同会议')
        self.assertEqual(len(self.call('calendar.event.list')['events']), 1)
        self.now = event['start_at_utc_ms'] - 60 * 60000
        self.call('delivery.tick')
        self.call('delivery.tick')
        self.assertEqual(len(self.call('inbox.list')['items']), 1)
        self.assertEqual(len(self.call('calendar.delivery.list', channel='desktop')['deliveries']), 1)
        item = self.call('calendar.delivery.list', channel='weixin')['deliveries'][0]
        claimed = self.call('calendar.delivery.claim', delivery_id=item['delivery_id'], destination=destination)
        self.assertTrue(claimed['should_send'])
        self.assertEqual(claimed['delivery']['destination']['revision'], destination['revision'])
        self.assertFalse(self.call('calendar.delivery.claim', delivery_id=item['delivery_id'], destination={'installation_id':'other','revision':4})['should_send'])
        self.call('calendar.delivery.ack', delivery_id=item['delivery_id'],claim_token=claimed['claim_token'],status='unknown')
        self.assertEqual(self.call('calendar.delivery.get',delivery_id=item['delivery_id'])['delivery']['status'],'unknown')
        self.assertEqual(len(self.call('calendar.delivery.list', channel='weixin')['deliveries']), 0)
        self.assertEqual(len(self.call('calendar.delivery.list', channel='desktop')['deliveries']), 1)

    def test_timezone_change_fences_resolved_times_before_mutation(self):
        self.setup_zone()
        resolved = self.call('calendar.time.resolve', local_time='2026-10-30T11:00')
        event = self.create()
        self.call('calendar.settings.update', time_zone='America/New_York')
        with self.assertRaises(FrameworkError):
            self.create(request_id='new', expected_time_zone=resolved['time_zone'])
        with self.assertRaises(FrameworkError):
            self.call('calendar.reminder.create', title='提醒', due_at=resolved['iso'],
                      expected_time_zone=resolved['time_zone'])
        with self.assertRaises(FrameworkError):
            self.call('calendar.event.update', event_id=event['event_id'], expected_version=event['version'],
                      patch={'title':'错误修改'}, expected_time_zone=resolved['time_zone'])
        self.assertEqual(self.call('calendar.event.get', event_id=event['event_id'])['event']['title'], '会议')

    def test_update_and_cancel_fence_previous_unfinished_notifications(self):
        self.setup_zone()
        event = self.create()
        self.now = event['start_at_utc_ms'] - 60 * 60000
        self.call('delivery.tick')
        old = self.call('calendar.delivery.list',channel='desktop')['deliveries'][0]
        claimed = self.call('calendar.delivery.claim', delivery_id=old['delivery_id'])
        updated = self.call('calendar.event.update',event_id=event['event_id'],expected_version=1,
                            patch={'start_at':'2026-10-31T11:00:00+08:00'})['event']
        self.assertEqual(self.call('calendar.delivery.get',delivery_id=old['delivery_id'])['delivery']['status'],'cancelled')
        late = self.call('calendar.delivery.ack',delivery_id=old['delivery_id'],claim_token=claimed['claim_token'],status='sent')
        self.assertEqual(late['delivery']['status'],'cancelled')
        with self.assertRaises(FrameworkError):
            self.call('calendar.event.cancel',event_id=event['event_id'],expected_version=1)
        self.call('calendar.event.cancel',event_id=event['event_id'],expected_version=updated['version'])
        self.now = updated['start_at_utc_ms']
        self.call('delivery.tick')
        self.assertEqual(self.call('calendar.delivery.list',channel='desktop')['deliveries'], [])

    def test_legacy_commitment_fans_out_and_sending_recovers_as_unknown(self):
        reminder = self.call('commitment.create', title='喝水', due_at_utc_ms=self.now)
        self.call('delivery.tick')
        item = self.call('calendar.delivery.list',channel='desktop')['deliveries'][0]
        self.call('calendar.delivery.claim',delivery_id=item['delivery_id'])
        self.service.close()
        self.service = FrameworkService(Path(self.temp.name), self.home, clock=lambda: self.now)
        self.assertEqual(self.call('calendar.delivery.get',delivery_id=item['delivery_id'])['delivery']['status'],'unknown')
        self.assertEqual(self.call('commitment.list')['commitments'][0]['id'],reminder['id'])

    def test_channel_setting_is_independent_and_invalid_dates_rejected(self):
        self.setup_zone()
        self.call('calendar.settings.update',channels={'weixin':False})
        event = self.create()
        self.now = event['start_at_utc_ms'] - 60 * 60000
        self.call('delivery.tick')
        self.assertEqual(self.call('calendar.delivery.list',channel='weixin')['deliveries'],[])
        self.assertEqual(len(self.call('calendar.delivery.list',channel='desktop')['deliveries']),1)
        with self.assertRaises(FrameworkError):
            self.create(request_id='another',start_at='2026-10-30T11:00:00')

    def test_weixin_reminder_scope_and_rebinding_are_fenced(self):
        self.setup_zone()
        destination = self.bind_weixin()
        self.call('settings.update',patch={'weixin_remote_control_enabled':True})
        self.call('conversation.bind',conversation_id=self.conv,session_id='fixture-session')
        self.call('command.admit',conversation_id=self.conv,command_id='remote-1',text='提醒')
        scope = {**destination,'command_id':'remote-1'}
        result = self.call('calendar.reminder.create',title='喝水',due_at='2026-10-30T10:00:00+08:00',
                           conversation_id=self.conv,request_id='remote-reminder',weixin_scope=scope)
        self.assertEqual(result['reminder']['conversation_id'], self.conv)
        self.call('settings.update',patch={'weixin_remote_control_enabled':False})
        with self.assertRaises(FrameworkError):
            self.call('calendar.reminder.cancel',commitment_id=result['reminder']['id'],weixin_scope=scope)
        self.now = result['reminder']['due_at']
        self.call('delivery.tick')
        item = self.call('calendar.delivery.list',channel='weixin')['deliveries'][0]
        other = self.call('conversation.ensure',title='另一个会话')['conversation_id']
        changed = self.call('channel.weixin.select',expected_revision=destination['revision'],conversation_id=other)['state']
        self.assertTrue(self.call('calendar.delivery.get',delivery_id=item['delivery_id'])['delivery']['eligible'])
        claim = self.call('calendar.delivery.claim',delivery_id=item['delivery_id'],
                          destination={'installation_id':changed['installation_id'],'revision':changed['revision']})
        self.assertTrue(claim['should_send'])
        self.assertEqual(claim['delivery']['destination']['revision'], changed['revision'])
        replaced = self.call('channel.weixin.save',token='other',peer_id='other',bot_id='other',base_url='https://ilinkai.weixin.qq.com')['state']
        self.call('channel.weixin.bind',expected_revision=replaced['revision'],conversation_id=other)
        self.assertFalse(self.call('calendar.delivery.get',delivery_id=item['delivery_id'])['delivery']['eligible'])

    def test_unbound_weixin_occurrence_does_not_later_follow_new_binding(self):
        self.setup_zone()
        event = self.create()
        self.now = event['start_at_utc_ms'] - 60 * 60000
        self.call('delivery.tick')
        self.assertEqual(self.call('calendar.delivery.list',channel='weixin')['deliveries'], [])
        failed = self.call('calendar.delivery.list',channel='weixin',include_terminal=True)['deliveries'][0]
        self.assertEqual(failed['status'], 'failed')
        self.assertEqual(failed['receipt']['reason'], 'channel_unavailable')
        self.bind_weixin()
        self.assertEqual(self.call('calendar.delivery.list',channel='weixin')['deliveries'], [])

    def test_upgrade_from_eight_preserves_existing_reminders_and_settings(self):
        old = self.call('commitment.create',title='已有提醒',due_at_utc_ms=self.now+120000)
        self.call('settings.update',patch={'do_not_disturb':True})
        self.service.db.execute('DROP TABLE calendar_event_reminders')
        self.service.db.execute('DROP TABLE calendar_deliveries')
        self.service.db.execute('DROP TABLE calendar_events')
        self.service.db.execute('PRAGMA user_version=8')
        self.service.close()
        self.service = FrameworkService(Path(self.temp.name),self.home,clock=lambda: self.now)
        self.assertEqual(self.call('system.status')['schema_version'],SCHEMA_VERSION)
        self.assertTrue(self.call('settings.get')['do_not_disturb'])
        self.assertEqual(self.call('commitment.list')['commitments'][0]['id'],old['id'])
        self.assertFalse(self.call('calendar.settings.get')['confirmed'])

    def test_notes_are_preserved_and_can_be_updated(self):
        self.setup_zone()
        event = self.create(notes='三楼会议室')
        self.assertEqual(event['notes'], '三楼会议室')
        updated = self.call('calendar.event.update',event_id=event['event_id'],expected_version=event['version'],
                            patch={'notes':'改到四楼'})['event']
        self.assertEqual(updated['notes'],'改到四楼')
        self.assertEqual(self.call('calendar.event.get',event_id=event['event_id'])['event']['notes'],'改到四楼')


if __name__ == '__main__':
    unittest.main()
