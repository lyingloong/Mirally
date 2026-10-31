"""Local calendar and per-channel reminder delivery owned by the product."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

CALENDAR_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS calendar_recurrences (id TEXT PRIMARY KEY,request_id TEXT UNIQUE,request_json TEXT NOT NULL,title TEXT NOT NULL,body TEXT NOT NULL,frequency TEXT NOT NULL,weekday INTEGER,local_time TEXT NOT NULL,time_zone TEXT NOT NULL,conversation_id TEXT,status TEXT NOT NULL,current_commitment_id TEXT NOT NULL,created_at INTEGER NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS calendar_events (id TEXT PRIMARY KEY,request_id TEXT UNIQUE,
        request_json TEXT NOT NULL,title TEXT NOT NULL,notes TEXT NOT NULL,start_at INTEGER NOT NULL,end_at INTEGER,
        all_day INTEGER NOT NULL,time_zone TEXT NOT NULL,conversation_id TEXT,status TEXT NOT NULL,
        version INTEGER NOT NULL,reminder_offsets_json TEXT NOT NULL,created_at INTEGER NOT NULL,updated_at INTEGER NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS calendar_events_start ON calendar_events(status,start_at)",
    """CREATE TABLE IF NOT EXISTS calendar_event_reminders (event_id TEXT NOT NULL REFERENCES calendar_events(id),
        commitment_id TEXT NOT NULL REFERENCES commitments(id),event_version INTEGER NOT NULL,offset_minutes INTEGER NOT NULL,
        PRIMARY KEY(event_id,commitment_id))""",
    """CREATE TABLE IF NOT EXISTS calendar_deliveries (id TEXT PRIMARY KEY,commitment_id TEXT NOT NULL REFERENCES commitments(id),
        channel TEXT NOT NULL,status TEXT NOT NULL,claim_token TEXT,destination_json TEXT,receipt_json TEXT,
        created_at INTEGER NOT NULL,updated_at INTEGER NOT NULL,UNIQUE(commitment_id,channel))""",
    "CREATE INDEX IF NOT EXISTS calendar_deliveries_pending ON calendar_deliveries(channel,status,created_at)",
)

def error(code, message):
    from .service import FrameworkError
    return FrameworkError(code, message)

def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)

def zone(name):
    if not isinstance(name, str) or len(name) > 128 or not name or name.startswith('/'):
        raise error('INVALID_PARAMS', '请选择有效的 IANA 时区。')
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise error('INVALID_PARAMS', '时区不可用；请安装 tzdata 并选择有效时区。') from exc

def instant(value, field):
    if not isinstance(value, str) or len(value) > 128:
        raise error('INVALID_PARAMS', f'{field} 需要包含时区的日期和时间。')
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if result.tzinfo is None or result.utcoffset() is None:
            raise ValueError()
        return int(result.timestamp() * 1000)
    except (ValueError, OverflowError, OSError) as exc:
        raise error('INVALID_PARAMS', f'{field} 需要包含时区的日期和时间。') from exc

class CalendarOwner:
    def __init__(self, service):
        self.s = service

    def routes(self):
        return {'calendar.settings.get': self.settings_get, 'calendar.settings.update': self.settings_update,
                'calendar.time.resolve': self.time_resolve,
                'calendar.recurrence.create': self.recurrence_create, 'calendar.recurrence.list': self.recurrence_list, 'calendar.recurrence.cancel': self.recurrence_cancel,
                'calendar.reminder.create': self.reminder_create, 'calendar.reminder.cancel': self.reminder_cancel,
                'calendar.event.create': self.event_create, 'calendar.event.list': self.event_list,
                'calendar.event.get': self.event_get, 'calendar.event.update': self.event_update,
                'calendar.event.cancel': self.event_cancel,
                'calendar.delivery.list': self.delivery_list, 'calendar.delivery.get': self.delivery_get,
                'calendar.delivery.claim': self.delivery_claim, 'calendar.delivery.ack': self.delivery_ack}

    def settings_get(self):
        row = self.s.db.execute("SELECT value_json FROM settings WHERE key='calendar'").fetchone()
        saved = json.loads(row[0]) if row else {}
        detected = None
        try:
            from tzlocal import get_localzone_name
            detected = get_localzone_name()
            zone(detected)
        except Exception:
            detected = None
        return {'time_zone': saved.get('time_zone'), 'confirmed': bool(saved.get('time_zone')),
                'detected_time_zone': detected, 'channels': saved.get('channels', {'desktop': True, 'weixin': True})}

    def settings_update(self, time_zone=None, channels=None):
        if time_zone is not None:
            zone(time_zone)
        if channels is not None:
            if not isinstance(channels, dict) or set(channels) - {'desktop', 'weixin'} or any(not isinstance(v, bool) for v in channels.values()):
                raise error('INVALID_PARAMS', '提醒渠道设置无效。')
        with self.s._transaction():
            current = self.settings_get()
            if time_zone is not None:
                if current['time_zone'] and current['time_zone'] != time_zone:
                    for row in self.s.db.execute("SELECT * FROM calendar_recurrences WHERE status='active'").fetchall():
                        for item in self.s.db.execute('SELECT id FROM commitments WHERE request_id GLOB ?', (row['id'] + ':*',)).fetchall():
                            self.s.commitment_cancel(item['id'])
                        due = self._next_recurrence(row['frequency'], row['weekday'], row['local_time'], time_zone, self.s.clock())
                        item = self.s.commitment_create(title=row['title'], body=row['body'], due_at_utc_ms=due,
                            conversation_id=row['conversation_id'], request_id=f"{row['id']}:{time_zone}:{due}:{uuid.uuid4().hex}")
                        self.s.db.execute('UPDATE calendar_recurrences SET time_zone=?,current_commitment_id=? WHERE id=?',
                            (time_zone, item['id'], row['id']))
                current['time_zone'] = time_zone
            if channels is not None:
                current['channels'].update(channels)
            self.s.db.execute("INSERT INTO settings VALUES('calendar',?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json",
                              (encoded({'time_zone': current['time_zone'], 'channels': current['channels']}),))
        return self.settings_get()

    def _timezone(self, time_zone=None):
        selected = time_zone or self.settings_get()['time_zone']
        if not self.settings_get()['confirmed']:
            raise error('TIME_ZONE_UNCONFIRMED', '请先在设置中确认时区。')
        return zone(selected)

    def time_resolve(self, local_time, time_zone=None):
        tz = self._timezone(time_zone)
        if not isinstance(local_time, str) or len(local_time) > 32:
            raise error('INVALID_PARAMS', '请输入本地日期和时间。')
        try:
            dt = datetime.fromisoformat(local_time)
            if dt.tzinfo is not None:
                raise ValueError()
        except ValueError as exc:
            raise error('INVALID_PARAMS', '请输入不含时区的本地日期和时间。') from exc
        options = {}
        for fold in (0, 1):
            candidate = dt.replace(tzinfo=tz, fold=fold)
            utc = candidate.astimezone(timezone.utc)
            if utc.astimezone(tz).replace(tzinfo=None) == dt:
                options[int(utc.timestamp() * 1000)] = candidate
        if len(options) != 1:
            raise error('AMBIGUOUS_TIME', '该本地时间不存在或出现两次，请选择其他时间。')
        ms, resolved = next(iter(options.items()))
        return {'utc_ms': ms, 'iso': resolved.isoformat(), 'time_zone': tz.key}

    def _view(self, row):
        if row is None:
            raise error('NOT_FOUND', '日程不存在。')
        tz = zone(row['time_zone'])
        def show(ms):
            return datetime.fromtimestamp(ms / 1000, timezone.utc).astimezone(tz).isoformat() if ms is not None else None
        return {'event_id': row['id'], 'title': row['title'], 'notes': row['notes'], 'start_at': show(row['start_at']),
                'end_at': show(row['end_at']), 'start_at_utc_ms': row['start_at'], 'end_at_utc_ms': row['end_at'],
                'all_day': bool(row['all_day']), 'time_zone': row['time_zone'], 'conversation_id': row['conversation_id'],
                'status': row['status'], 'version': row['version'], 'reminder_offsets_minutes': json.loads(row['reminder_offsets_json']),
                'created_at': row['created_at'], 'updated_at': row['updated_at']}

    def _row(self, event_id):
        return self.s.db.execute('SELECT * FROM calendar_events WHERE id=?', (event_id,)).fetchone()

    def event_get(self, event_id):
        return {'event': self._view(self._row(event_id))}

    def event_list(self, start_at=None, end_at=None, limit=200):
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise error('INVALID_PARAMS', '日程列表数量无效。')
        start = instant(start_at, 'start_at') if start_at else 0
        end = instant(end_at, 'end_at') if end_at else 2**63-1
        if start >= end:
            raise error('INVALID_PARAMS', '日程查询时间范围无效。')
        rows = self.s.db.execute("SELECT * FROM calendar_events WHERE status='active' AND start_at<? AND COALESCE(end_at,start_at+1)>? ORDER BY start_at,id LIMIT ?", (end, start, limit)).fetchall()
        return {'events': [self._view(row) for row in rows]}

    def _validate(self, title, start_at, end_at, all_day, time_zone, offsets, conversation_id):
        if not isinstance(title, str) or not title.strip() or len(title) > 1024:
            raise error('INVALID_PARAMS', '日程标题不能为空。')
        tz = self._timezone(time_zone)
        start = instant(start_at, 'start_at')
        end = instant(end_at, 'end_at') if end_at else None
        if end is not None and end <= start:
            raise error('INVALID_PARAMS', '结束时间必须晚于开始时间。')
        if not isinstance(all_day, bool):
            raise error('INVALID_PARAMS', '全天设置无效。')
        if not isinstance(offsets, list) or len(offsets) > 10 or any(isinstance(x, bool) or not isinstance(x, int) or x < 0 or x > 525600 for x in offsets):
            raise error('INVALID_PARAMS', '提醒时间无效。')
        if conversation_id is not None:
            self.s._conversation(conversation_id)
        return title.strip(), start, end, tz.key, sorted(set(offsets))

    def _schedule(self, row):
        for offset in json.loads(row['reminder_offsets_json']):
            due = row['start_at'] - offset * 60000
            # A previous occurrence is never resurrected by an edit.
            if due <= self.s.clock() and row['version'] > 1:
                continue
            notice = f"{row['title']}（{datetime.fromtimestamp(row['start_at']/1000, timezone.utc).astimezone(zone(row['time_zone'])).strftime('%Y-%m-%d %H:%M')}）"
            commitment = self.s.commitment_create(title=row['title'], body=notice, due_at_utc_ms=due,
                conversation_id=row['conversation_id'], request_id=f"calendar:{row['id']}:{row['version']}:{offset}")
            self.s.db.execute('INSERT INTO calendar_event_reminders VALUES(?,?,?,?)', (row['id'], commitment['id'], row['version'], offset))

    def _next_recurrence(self, frequency, weekday, local_time, time_zone, after):
        tz = zone(time_zone)
        hour, minute = (int(part) for part in local_time.split(':'))
        start = datetime.fromtimestamp(after / 1000, timezone.utc).astimezone(tz).date()
        # Nonexistent wall times are skipped; an ambiguous wall time fires once,
        # at its first occurrence. Never duplicate a missed week on recovery.
        for day in range(370):
            date = start + timedelta(days=day)
            if frequency == 'weekly' and date.weekday() != weekday:
                continue
            naive = datetime(date.year, date.month, date.day, hour, minute)
            options = set()
            for fold in (0, 1):
                candidate = naive.replace(tzinfo=tz, fold=fold).astimezone(timezone.utc)
                if candidate.astimezone(tz).replace(tzinfo=None) == naive:
                    options.add(int(candidate.timestamp() * 1000))
            if options and min(options) > after:
                return min(options)
        raise error('INVALID_PARAMS', '无法计算下次提醒时间。')

    def recurrence_create(self, title, frequency, local_time, weekday=None, body=None, conversation_id=None, request_id=None, weixin_scope=None, expected_time_zone=None):
        tz = self._timezone()
        if not isinstance(title, str) or not title.strip() or len(title) > 1024:
            raise error('INVALID_PARAMS', '提醒标题不能为空。')
        if body is not None and (not isinstance(body, str) or len(body) > 16000):
            raise error('INVALID_PARAMS', '提醒内容无效。')
        if frequency not in ('daily', 'weekly') or (frequency == 'weekly' and (isinstance(weekday, bool) or not isinstance(weekday, int) or not 0 <= weekday <= 6)) or (frequency == 'daily' and weekday is not None):
            raise error('INVALID_PARAMS', '重复规则无效。')
        import re
        if not isinstance(local_time, str) or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', local_time):
            raise error('INVALID_PARAMS', '提醒时间需为 HH:MM。')
        if request_id is not None and (not isinstance(request_id, str) or not request_id or len(request_id) > 512):
            raise error('INVALID_PARAMS', '请求标识无效。')
        request = encoded([title.strip(), body or title.strip(), frequency, weekday, local_time, tz.key, conversation_id])
        with self.s._transaction():
            self._scope(conversation_id, weixin_scope)
            self._expect_zone(expected_time_zone)
            if request_id:
                old = self.s.db.execute('SELECT * FROM calendar_recurrences WHERE request_id=?', (request_id,)).fetchone()
                if old:
                    if old['request_json'] != request:
                        raise error('CONFLICT', '请求标识已用于其他提醒。')
                    return {'recurrence': self._recurrence_view(old)}
            identity = 'recurrence_' + uuid.uuid4().hex
            due = self._next_recurrence(frequency, weekday, local_time, tz.key, self.s.clock())
            item = self.s.commitment_create(title=title.strip(), body=body or title.strip(), due_at_utc_ms=due,
                conversation_id=conversation_id, request_id=f'{identity}:{due}')
            self.s.db.execute('INSERT INTO calendar_recurrences VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (identity, request_id, request, title.strip(), body or title.strip(), frequency, weekday, local_time, tz.key, conversation_id, 'active', item['id'], self.s.clock()))
            return {'recurrence': self._recurrence_view(self.s.db.execute('SELECT * FROM calendar_recurrences WHERE id=?', (identity,)).fetchone())}

    def _recurrence_view(self, row):
        item = dict(row)
        item.pop('request_json')
        item.pop('request_id')
        item['next_due_at_utc_ms'] = self.s._record('commitments', item['current_commitment_id'])['due_at']
        return item

    def recurrence_list(self):
        return {'recurrences': [self._recurrence_view(row) for row in self.s.db.execute("SELECT * FROM calendar_recurrences WHERE status='active' ORDER BY created_at DESC")]}

    def _cancel_recurrence_items(self, recurrence_id):
        for item in self.s.db.execute('SELECT id FROM commitments WHERE request_id GLOB ?', (recurrence_id + ":*",)).fetchall():
            self.s.commitment_cancel(item['id'])
        self.s.db.execute("UPDATE calendar_recurrences SET status='cancelled' WHERE id=?", (recurrence_id,))

    def recurrence_cancel(self, recurrence_id, weixin_scope=None):
        with self.s._transaction():
            row = self.s.db.execute('SELECT * FROM calendar_recurrences WHERE id=?', (recurrence_id,)).fetchone()
            if not row:
                raise error('NOT_FOUND', '周期提醒不存在。')
            self._scope(row['conversation_id'], weixin_scope)
            if row['status'] == 'active':
                self._cancel_recurrence_items(recurrence_id)
            return {'recurrence': self._recurrence_view(self.s.db.execute('SELECT * FROM calendar_recurrences WHERE id=?', (recurrence_id,)).fetchone())}

    def advance_recurrences(self, now):
        rows = self.s.db.execute("SELECT r.* FROM calendar_recurrences r JOIN commitments c ON c.id=r.current_commitment_id WHERE r.status='active' AND c.status IN ('fulfilled','queued','delivered')").fetchall()
        for row in rows:
            due = self._next_recurrence(row['frequency'], row['weekday'], row['local_time'], row['time_zone'], now)
            item = self.s.commitment_create(title=row['title'], body=row['body'], due_at_utc_ms=due,
                conversation_id=row['conversation_id'], request_id=f"{row['id']}:{due}")
            self.s.db.execute('UPDATE calendar_recurrences SET current_commitment_id=? WHERE id=?', (item['id'], row['id']))

    def _scope(self, conversation_id, weixin_scope):
        if weixin_scope is not None:
            self.s.channels.check_conversation_scope(conversation_id, weixin_scope)

    def _expect_zone(self, expected_time_zone):
        if expected_time_zone is not None and self.settings_get()['time_zone'] != expected_time_zone:
            raise error('CONFLICT', '时区已改变，请重新安排时间。')

    def reminder_create(self, title, due_at, body=None, conversation_id=None, request_id=None, weixin_scope=None, expected_time_zone=None):
        self._timezone()
        due = instant(due_at, 'due_at')
        with self.s._transaction():
            self._scope(conversation_id, weixin_scope)
            self._expect_zone(expected_time_zone)
            return {'reminder': self.s.commitment_create(title=title, body=body, due_at_utc_ms=due,
                conversation_id=conversation_id, request_id=request_id)}

    def reminder_cancel(self, commitment_id, weixin_scope=None):
        with self.s._transaction():
            record = self.s._record('commitments', commitment_id)
            self._scope(record['conversation_id'], weixin_scope)
            series = self.s.db.execute("SELECT id FROM calendar_recurrences WHERE current_commitment_id=? AND status='active'", (commitment_id,)).fetchone()
            if series:
                self._cancel_recurrence_items(series['id'])
            return {'reminder': self.s.commitment_cancel(commitment_id)}

    def event_create(self, title, start_at, end_at=None, all_day=False, time_zone=None,
                     reminder_offsets_minutes=None, conversation_id=None, request_id=None, weixin_scope=None, notes='', expected_time_zone=None):
        if not isinstance(notes, str) or len(notes) > 16000:
            raise error('INVALID_PARAMS', '日程备注无效。')
        offsets = [] if reminder_offsets_minutes is None else reminder_offsets_minutes
        title, start, end, tz, offsets = self._validate(title, start_at, end_at, all_day, time_zone, offsets, conversation_id)
        if request_id is not None and (not isinstance(request_id, str) or not request_id or len(request_id) > 512):
            raise error('INVALID_PARAMS', '请求标识无效。')
        request = encoded([title, notes, start, end, all_day, tz, offsets, conversation_id])
        with self.s._transaction():
            self._scope(conversation_id, weixin_scope)
            self._expect_zone(expected_time_zone)
            if request_id:
                old = self.s.db.execute('SELECT * FROM calendar_events WHERE request_id=?', (request_id,)).fetchone()
                if old:
                    if old['request_json'] != request:
                        raise error('CONFLICT', '请求标识已用于不同日程。')
                    return {'event': self._view(old)}
            identity, now = 'event_' + uuid.uuid4().hex, self.s.clock()
            self.s.db.execute('INSERT INTO calendar_events(id,request_id,request_json,title,notes,start_at,end_at,all_day,time_zone,conversation_id,status,version,reminder_offsets_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (identity, request_id, request, title, notes, start, end, int(all_day), tz, conversation_id, 'active', 1, encoded(offsets), now, now))
            row = self._row(identity)
            self._schedule(row)
            return {'event': self._view(row)}

    def _cancel_event_reminders(self, event_id):
        rows = self.s.db.execute('SELECT commitment_id FROM calendar_event_reminders WHERE event_id=?', (event_id,)).fetchall()
        for row in rows:
            self.s.commitment_cancel(row['commitment_id'])

    def event_update(self, event_id, expected_version, patch, weixin_scope=None, expected_time_zone=None):
        if not isinstance(patch, dict) or not patch or set(patch) - {'title','notes','start_at','end_at','all_day','time_zone','reminder_offsets_minutes'}:
            raise error('INVALID_PARAMS', '日程修改内容无效。')
        with self.s._transaction():
            self._expect_zone(expected_time_zone)
            old = self._view(self._row(event_id))
            self._scope(old['conversation_id'], weixin_scope)
            if old['status'] != 'active' or old['version'] != expected_version:
                raise error('CONFLICT', '日程已变化，请重新查看。')
            value = {**old, **patch}
            if not isinstance(value['notes'], str) or len(value['notes']) > 16000:
                raise error('INVALID_PARAMS', '日程备注无效。')
            title, start, end, tz, offsets = self._validate(value['title'], value['start_at'], value['end_at'], value['all_day'], value['time_zone'], value['reminder_offsets_minutes'], old['conversation_id'])
            self._cancel_event_reminders(event_id)
            self.s.db.execute('UPDATE calendar_events SET title=?,notes=?,start_at=?,end_at=?,all_day=?,time_zone=?,reminder_offsets_json=?,version=version+1,updated_at=? WHERE id=?',
                (title, value['notes'], start, end, int(value['all_day']), tz, encoded(offsets), self.s.clock(), event_id))
            row = self._row(event_id)
            self._schedule(row)
            return {'event': self._view(row)}

    def event_cancel(self, event_id, expected_version, weixin_scope=None):
        with self.s._transaction():
            old = self._view(self._row(event_id))
            self._scope(old['conversation_id'], weixin_scope)
            if old['status'] != 'active' or old['version'] != expected_version:
                raise error('CONFLICT', '日程已变化，请重新查看。')
            self._cancel_event_reminders(event_id)
            self.s.db.execute("UPDATE calendar_events SET status='cancelled',version=version+1,updated_at=? WHERE id=?", (self.s.clock(),event_id))
            return {'event': self._view(self._row(event_id))}

    def queue_deliveries(self, commitment_id):
        channels = self.settings_get()['channels']
        for channel in ('desktop','weixin'):
            if channels[channel]:
                now = self.s.clock()
                destination = None
                status = 'pending'
                receipt = None
                if channel == 'weixin':
                    state = self.s.channels._state()
                    if state.get('bound') and state.get('enabled') and state.get('token'):
                        destination = encoded({'installation_id': state['installation_id'], 'revision': state['revision']})
                    else:
                        status = 'failed'
                        receipt = encoded({'reason':'channel_unavailable'})
                self.s.db.execute('INSERT OR IGNORE INTO calendar_deliveries VALUES(?,?,?,?,NULL,?,?,?,?)',
                    ('calendar_delivery_'+uuid.uuid4().hex,commitment_id,channel,status,destination,receipt,now,now))

    def cancel_deliveries(self, commitment_id):
        self.s.db.execute("UPDATE calendar_deliveries SET status='cancelled',updated_at=? WHERE commitment_id=? AND status IN ('pending','sending')", (self.s.clock(),commitment_id))

    def _delivery(self, delivery_id):
        row = self.s.db.execute('SELECT d.*,c.title,c.body,c.conversation_id,c.status AS commitment_status,c.sources_json FROM calendar_deliveries d JOIN commitments c ON c.id=d.commitment_id WHERE d.id=?',(delivery_id,)).fetchone()
        if not row:
            raise error('NOT_FOUND', '提醒投递不存在。')
        value = dict(row)
        value['delivery_id'] = value.pop('id')
        destination_json = value.pop('destination_json')
        receipt_json = value.pop('receipt_json')
        value['destination'] = json.loads(destination_json) if destination_json else None
        value['receipt'] = json.loads(receipt_json) if receipt_json else None
        value['sources'] = json.loads(value.pop('sources_json'))
        value['eligible'] = (value['status'] in ('pending', 'sending')
            and value['commitment_status'] not in ('cancelled', 'expired')
            and not self.s._source_reasons(value['sources'])
            and self.settings_get()['channels'][value['channel']])
        if value['eligible'] and value['channel'] == 'weixin':
            destination = value['destination']
            try:
                if not destination:
                    value['eligible'] = False
                elif value['status'] == 'pending':
                    state = self.s.channels._state()
                    value['eligible'] = bool(state.get('bound') and state.get('enabled') and state.get('token')
                                             and state.get('installation_id') == destination['installation_id'])
                else:
                    self.s.channels._current(destination['revision'], destination['installation_id'], enabled=True)
            except Exception:
                value['eligible'] = False
        return value

    def delivery_get(self, delivery_id):
        return {'delivery': self._delivery(delivery_id)}

    def delivery_list(self, channel, include_terminal=False):
        if channel not in ('desktop','weixin'):
            raise error('INVALID_PARAMS', '提醒渠道无效。')
        if not isinstance(include_terminal, bool):
            raise error('INVALID_PARAMS', '提醒列表参数无效。')
        rows = self.s.db.execute("SELECT id FROM calendar_deliveries WHERE channel=? AND (? OR status IN ('pending','sending')) ORDER BY created_at DESC,id LIMIT 100",(channel,include_terminal)).fetchall()
        return {'deliveries': [self._delivery(row['id']) for row in rows]}

    def delivery_claim(self, delivery_id, destination=None):
        with self.s._transaction():
            old = self._delivery(delivery_id)
            if old['status'] != 'pending':
                return {'delivery': old, 'should_send': False, 'claim_token': old['claim_token']}
            if not old['eligible']:
                self.s.db.execute("UPDATE calendar_deliveries SET status='cancelled',updated_at=? WHERE id=?", (self.s.clock(),delivery_id))
                return {'delivery': self._delivery(delivery_id), 'should_send': False, 'claim_token': None}
            if old['channel'] == 'weixin':
                if not isinstance(destination, dict) or set(destination) != {'installation_id','revision'} or not isinstance(destination['installation_id'], str) or type(destination['revision']) is not int:
                    raise error('INVALID_PARAMS', '微信投递目标无效。')
                if old['destination']['installation_id'] != destination['installation_id']:
                    self.s.db.execute("UPDATE calendar_deliveries SET status='cancelled',updated_at=? WHERE id=?", (self.s.clock(),delivery_id))
                    return {'delivery': self._delivery(delivery_id), 'should_send': False, 'claim_token': None}
                self.s.channels._current(destination['revision'], destination['installation_id'], enabled=True)
            elif destination is not None:
                raise error('INVALID_PARAMS', '桌面提醒不需要投递目标。')
            token = uuid.uuid4().hex
            self.s.db.execute("UPDATE calendar_deliveries SET status='sending',claim_token=?,destination_json=?,updated_at=? WHERE id=?", (token,encoded(destination) if destination else None,self.s.clock(),delivery_id))
            return {'delivery': self._delivery(delivery_id), 'should_send': True, 'claim_token': token}

    def delivery_ack(self, delivery_id, claim_token, status, receipt=None):
        if status not in ('sent','failed','unknown') or (receipt is not None and (not isinstance(receipt, dict) or len(encoded(receipt)) > 16384)):
            raise error('INVALID_PARAMS', '提醒回执无效。')
        with self.s._transaction():
            old = self._delivery(delivery_id)
            if not claim_token or old['claim_token'] != claim_token:
                raise error('CONFLICT', '提醒投递凭据不匹配。')
            if old['status'] in ('sent','failed'):
                return {'delivery': old}
            new_status = 'cancelled' if old['status'] == 'cancelled' else status
            self.s.db.execute('UPDATE calendar_deliveries SET status=?,receipt_json=?,updated_at=? WHERE id=?',
                (new_status, encoded({'status':status,'late':new_status == 'cancelled','receipt':receipt or {}}),self.s.clock(),delivery_id))
            return {'delivery': self._delivery(delivery_id)}
