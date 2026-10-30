"""Versioned, permission-fenced conversation summaries."""
import uuid

SCHEMA = """CREATE TABLE IF NOT EXISTS conversation_recaps (
 conversation_id TEXT PRIMARY KEY REFERENCES conversations(id), generation INTEGER NOT NULL,
 command_rowid INTEGER NOT NULL, summary TEXT NOT NULL, updated_at INTEGER NOT NULL)"""
ATTEMPTS = """CREATE TABLE IF NOT EXISTS recap_attempts (
 id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, generation INTEGER NOT NULL,
 command_rowid INTEGER NOT NULL, created_at INTEGER NOT NULL, status TEXT NOT NULL)"""

class Recaps:
    def __init__(self, service):
        self.s = service
        with service._transaction():
            service.db.execute(SCHEMA)
            service.db.execute(ATTEMPTS)

    def routes(self):
        return {'internal.recap.reserve': self.reserve, 'internal.recap.complete': self.complete}

    def _current(self, conversation_id):
        c = self.s.db.execute('SELECT c.current_generation AS generation,c.archived,g.frozen FROM conversations c JOIN generations g ON g.conversation_id=c.id AND g.generation=c.current_generation WHERE c.id=?', (conversation_id,)).fetchone()
        if c is None:
            from .service import FrameworkError
            raise FrameworkError('NOT_FOUND', 'conversation not found')
        refs = [dict(r) for r in self.s.db.execute('SELECT source_id,version FROM source_uses WHERE conversation_id=? AND generation=?', (conversation_id,c['generation']))]
        valid = not c['frozen'] and not c['archived'] and not self.s._source_reasons(refs)
        row = self.s.db.execute('SELECT rowid,state FROM commands WHERE conversation_id=? AND generation=? ORDER BY rowid DESC LIMIT 1', (conversation_id,c['generation'])).fetchone()
        return c, valid, row

    def get(self, conversation_id):
        c, valid, last = self._current(conversation_id)
        row = self.s.db.execute('SELECT * FROM conversation_recaps WHERE conversation_id=? AND generation=?', (conversation_id,c['generation'])).fetchone()
        if not valid or not row:
            return None
        return {'text': row['summary'], 'updated_at': row['updated_at'], 'stale': bool(last and last['rowid'] != row['command_rowid'])}

    def reserve(self, conversation_id, manual=False):
        from .service import FrameworkError
        if not isinstance(manual, bool):
            raise FrameworkError('INVALID_PARAMS', 'manual must be boolean')
        with self.s._transaction():
            c, valid, last = self._current(conversation_id)
            if not valid or not last or last['state'] != 'completed':
                return {'status': 'unavailable'}
            now = self.s.clock()
            self.s.db.execute("UPDATE recap_attempts SET status='abandoned' WHERE status LIKE 'pending%' AND created_at<?", (now-300000,))
            self.s.db.execute("DELETE FROM recap_attempts WHERE created_at<? AND status NOT LIKE 'pending%'", (now-2592000000,))
            if not manual and not self.s._settings()['background_enabled']:
                return {'status': 'disabled'}
            recent = self.s.db.execute("SELECT COUNT(*) FROM recap_attempts WHERE created_at>?", (now-86400000,)).fetchone()[0]
            if not manual and recent >= 12:
                return {'status': 'budget'}
            recent_conv = self.s.db.execute('SELECT MAX(created_at) FROM recap_attempts WHERE conversation_id=?', (conversation_id,)).fetchone()[0]
            if not manual and recent_conv and now-recent_conv < 600000:
                return {'status': 'deferred'}
            if self.s.db.execute("SELECT 1 FROM recap_attempts WHERE conversation_id=? AND status LIKE 'pending%'", (conversation_id,)).fetchone():
                return {'status': 'pending'}
            attempt = uuid.uuid4().hex
            self.s.db.execute('INSERT INTO recap_attempts VALUES(?,?,?,?,?,?)', (attempt,conversation_id,c['generation'],last['rowid'],now,'pending-manual' if manual else 'pending-auto'))
            return {'status':'reserved','id':attempt,'generation':c['generation'],'command_rowid':last['rowid']}

    def complete(self, attempt_id, summary='', title=''):
        from .service import FrameworkError
        if not isinstance(attempt_id,str) or not isinstance(summary,str) or not isinstance(title,str) or len(summary)>1000 or len(title)>100:
            raise FrameworkError('INVALID_PARAMS','invalid recap')
        with self.s._transaction():
            attempt = self.s.db.execute("SELECT * FROM recap_attempts WHERE id=? AND status LIKE 'pending%'", (attempt_id,)).fetchone()
            if not attempt:
                return {'changed':False}
            self.s.db.execute("UPDATE recap_attempts SET status='completed' WHERE id=?", (attempt_id,))
            c, valid, last = self._current(attempt['conversation_id'])
            if attempt['status'] == 'pending-auto' and not self.s._settings()['background_enabled']:
                return {'changed':False}
            if not summary.strip() or not valid or c['generation'] != attempt['generation'] or not last or last['rowid'] != attempt['command_rowid'] or last['state'] != 'completed':
                return {'changed':False}
            now = self.s.clock()
            self.s.db.execute('INSERT INTO conversation_recaps VALUES(?,?,?,?,?) ON CONFLICT(conversation_id) DO UPDATE SET generation=excluded.generation,command_rowid=excluded.command_rowid,summary=excluded.summary,updated_at=excluded.updated_at', (attempt['conversation_id'],attempt['generation'],attempt['command_rowid'],summary.strip(),now))
            mode = self.s.db.execute('SELECT title_mode FROM conversations WHERE id=?',(attempt['conversation_id'],)).fetchone()[0]
            if mode in ('pending','auto') and title.strip():
                self.s.db.execute("UPDATE conversations SET title=?,title_mode='generated',metadata_version=metadata_version+1,metadata_updated_at=? WHERE id=?", (title.strip()[:32],now,attempt['conversation_id']))
            return {'changed':True,'conversation':self.s._conversation(attempt['conversation_id'])}
