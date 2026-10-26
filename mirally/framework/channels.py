"""Internal, durable channel state. Networking and public redaction belong to Host.

This is a message receipt/route owner, not a second transcript or Agent loop.
Credentials are DPAPI protected for the current Windows user. Other platforms
use an owner-only directory/database (the supported desktop target is Windows).
"""
from __future__ import annotations

import base64
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import uuid
from urllib.parse import urlsplit


CHANNEL_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS channel_weixin (
        singleton INTEGER PRIMARY KEY CHECK(singleton=1),installation_id TEXT,
        revision INTEGER NOT NULL DEFAULT 0,enabled INTEGER NOT NULL DEFAULT 0,
        bound INTEGER NOT NULL DEFAULT 0,peer_id TEXT,bot_id TEXT,base_url TEXT,
        conversation_id TEXT REFERENCES conversations(id),cursor TEXT,token TEXT,context_token TEXT)""",
    """CREATE TABLE IF NOT EXISTS channel_weixin_events (
        installation_id TEXT NOT NULL,revision INTEGER NOT NULL,event_id TEXT NOT NULL,
        command_id TEXT NOT NULL,conversation_id TEXT NOT NULL REFERENCES conversations(id),
        text TEXT NOT NULL,content_hash TEXT NOT NULL,context_token TEXT,state TEXT NOT NULL,
        result_ref TEXT,created_at INTEGER NOT NULL,updated_at INTEGER NOT NULL,
        PRIMARY KEY(installation_id,event_id))""",
    """CREATE TABLE IF NOT EXISTS channel_weixin_outbox (
        delivery_id TEXT PRIMARY KEY,installation_id TEXT NOT NULL,revision INTEGER NOT NULL,
        dedupe_key TEXT NOT NULL,conversation_id TEXT REFERENCES conversations(id),command_id TEXT,
        text TEXT NOT NULL,content_hash TEXT NOT NULL,context_token TEXT,status TEXT NOT NULL,
        receipt TEXT,created_at INTEGER NOT NULL,updated_at INTEGER NOT NULL,
        UNIQUE(installation_id,dedupe_key))""",
    "CREATE INDEX IF NOT EXISTS channel_weixin_outbox_pending ON channel_weixin_outbox(installation_id,status,created_at)",
)


def _fail(code, message):
    from .service import FrameworkError
    raise FrameworkError(code, message)


def _text(value, name, limit=512, *, empty=False):
    if not isinstance(value, str) or len(value) > limit or (not empty and not value.strip()):
        _fail("INVALID_PARAMS", name + " must be bounded text")
    return value


def _integer(value, name, low=0, high=2**63 - 1):
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        _fail("INVALID_PARAMS", name + " must be an integer in range")
    return value


def _json(value, name):
    try:
        result = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError, RecursionError):
        _fail("INVALID_PARAMS", name + " must be JSON")
    if len(result) > 16000:
        _fail("INVALID_PARAMS", name + " is too large")
    return result


class CredentialProtector:
    """No machine/user paths are embedded in the stored value."""
    def protect(self, text):
        if os.name != "nt":
            return "owner-only:" + base64.b64encode(text.encode("utf-8")).decode("ascii")
        return "dpapi:" + base64.b64encode(self._crypt(text.encode("utf-8"), False)).decode("ascii")

    def unprotect(self, value):
        prefix, _, encoded = value.partition(":")
        try:
            raw = base64.b64decode(encoded, validate=True)
            if prefix == "dpapi" and os.name == "nt":
                raw = self._crypt(raw, True)
            elif prefix != "owner-only" or os.name == "nt":
                _fail("CREDENTIAL_UNAVAILABLE", "WeChat credentials belong to another user or platform; reconnect")
            return raw.decode("utf-8")
        except (ValueError, UnicodeError):
            _fail("CREDENTIAL_UNAVAILABLE", "WeChat credentials cannot be read; reconnect")

    @staticmethod
    def _crypt(data, decrypt):
        from ctypes import wintypes
        class Blob(ctypes.Structure):
            _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]
        buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
        source = Blob(len(data), buffer)
        target = Blob()
        crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        # CRYPTPROTECT_UI_FORBIDDEN; no LOCAL_MACHINE flag, so current-user only.
        if decrypt:
            okay = crypt32.CryptUnprotectData(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target))
        else:
            okay = crypt32.CryptProtectData(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target))
        if not okay:
            _fail("CREDENTIAL_UNAVAILABLE", "Windows credential protection failed; reconnect")
        try:
            return ctypes.string_at(target.data, target.size)
        finally:
            kernel32.LocalFree.argtypes = [ctypes.c_void_p]
            kernel32.LocalFree(ctypes.cast(target.data, ctypes.c_void_p))


class WeixinChannels:
    MAX_PENDING = 1000
    EVENT_STATES = {"queued", "submitted", "completed", "rejected", "unknown"}
    EVENT_FINAL = {"completed", "rejected", "unknown"}
    DELIVERY_FINAL = {"sent", "failed", "unknown", "cancelled", "blocked"}

    def __init__(self, service, *, protector=None):
        self.service = service
        self.protector = protector or CredentialProtector()
        if os.name != "nt":
            # Parent directory access also protects SQLite WAL/SHM created later.
            Path(service.path).parent.chmod(0o700)
            Path(service.path).chmod(0o600)
            for suffix in ("-wal", "-shm"):
                sibling = Path(str(service.path) + suffix)
                if sibling.exists():
                    sibling.chmod(0o600)
        with service._transaction():
            service.db.execute("INSERT OR IGNORE INTO channel_weixin(singleton) VALUES(1)")
            service.db.execute("UPDATE channel_weixin_events SET state='unknown',updated_at=? WHERE state='submitted'", (service.clock(),))
            service.db.execute("UPDATE channel_weixin_outbox SET status='unknown',updated_at=? WHERE status='sending'", (service.clock(),))

    def routes(self):
        return {"channel.weixin." + key: getattr(self, key) for key in (
            "get", "save", "bind", "select", "enable", "unbind", "cursor", "admit", "event", "events",
            "enqueue", "outbox", "claim", "ack")}

    @property
    def db(self):
        return self.service.db

    def _protected(self, value):
        return None if value is None else self.protector.protect(value)

    def _revealed(self, value):
        return None if value is None else self.protector.unprotect(value)

    def _state(self):
        result = dict(self.db.execute("SELECT * FROM channel_weixin WHERE singleton=1").fetchone())
        del result["singleton"]
        result["enabled"] = bool(result["enabled"])
        result["bound"] = bool(result["bound"])
        from .service import FrameworkError
        for field in ("token", "context_token"):
            try:
                result[field] = self._revealed(result[field])
            except FrameworkError as error:
                if error.code != "CREDENTIAL_UNAVAILABLE":
                    raise
                # A copied Windows home cannot decrypt another user's DPAPI
                # values. Preserve metadata/ciphertext so settings and recovery
                # remain available; never turn this into a product boot failure.
                result[field] = None
                result["credential_error"] = True
        result["outbox_counts"] = {status: 0 for status in ("pending", "unknown", "failed")}
        for row in self.db.execute("SELECT status,COUNT(*) AS count FROM channel_weixin_outbox WHERE installation_id=? GROUP BY status", (result["installation_id"],)):
            if row["status"] in result["outbox_counts"]:
                result["outbox_counts"][row["status"]] = row["count"]
        return result

    def _current(self, revision, installation_id=None, *, enabled=False):
        _integer(revision, "revision")
        state = self._state()
        if revision != state["revision"] or installation_id is not None and installation_id != state["installation_id"]:
            _fail("CONFLICT", "WeChat binding changed; refresh before continuing")
        if enabled and (not state["enabled"] or not state["bound"] or not state["token"]):
            _fail("CHANNEL_DISABLED", "WeChat is not connected and confirmed")
        return state

    def _cancel_pending(self):
        self.db.execute("UPDATE channel_weixin_outbox SET status='cancelled',updated_at=? WHERE status='pending'", (self.service.clock(),))

    def _forget_context(self):
        # Historical message IDs and send receipts remain useful for deduplication;
        # expiring send-window credentials do not need to survive an account change.
        self.db.execute("UPDATE channel_weixin_events SET context_token=NULL WHERE context_token IS NOT NULL")
        self.db.execute("UPDATE channel_weixin_outbox SET context_token=NULL WHERE context_token IS NOT NULL")

    def get(self):
        return {"state": self._state()}

    def save(self, token, peer_id, bot_id, base_url):
        _text(token, "token", 16000)
        _text(peer_id, "peer_id", 512)
        _text(bot_id, "bot_id", 512)
        _text(base_url, "base_url", 2048)
        try:
            url = urlsplit(base_url)
            official = url.hostname == "weixin.qq.com" or bool(url.hostname and url.hostname.endswith(".weixin.qq.com"))
            valid = (url.scheme == "https" and official and url.port in (None, 443)
                and url.path in ("", "/") and not url.username and not url.password and not url.query and not url.fragment
                and not re.search(r"[\s\\]", base_url))
            if (os.environ.get("MIRALLY_TEST_MODE") == "1"
                    and os.environ.get("MIRALLY_TEST_WEIXIN_URL") == base_url
                    and re.fullmatch(r"http://127\.0\.0\.1:[0-9]{1,5}", base_url)
                    and url.port is not None and 1 <= url.port <= 65535):
                valid = True
        except ValueError:
            valid = False
        if not valid:
            _fail("INVALID_PARAMS", "base_url must be an official HTTPS WeChat service origin")
        protected = self._protected(token)
        with self.service._transaction():
            self._cancel_pending()
            self._forget_context()
            self.db.execute("""UPDATE channel_weixin SET installation_id=?,revision=revision+1,enabled=0,bound=0,
                peer_id=?,bot_id=?,base_url=?,token=?,conversation_id=NULL,cursor=NULL,context_token=NULL WHERE singleton=1""",
                (uuid.uuid4().hex, peer_id, bot_id, base_url.rstrip("/"), protected))
        return self.get()

    def _conversation(self, conversation_id):
        if conversation_id is None:
            return self.service.conversation_ensure(title="微信对话")["conversation_id"]
        _text(conversation_id, "conversation_id")
        current = self.service.conversation_get(conversation_id)
        if current["archived"]:
            _fail("CONFLICT", "Restore the archived conversation before routing WeChat to it")
        return conversation_id

    def bind(self, expected_revision, conversation_id=None):
        with self.service._transaction():
            current = self._current(expected_revision)
            if not current["token"] or not current["peer_id"]:
                _fail("CHANNEL_UNCONFIGURED", "Scan and confirm WeChat login first")
            conversation_id = self._conversation(conversation_id)
            self._cancel_pending()
            self.db.execute("UPDATE channel_weixin SET revision=revision+1,bound=1,enabled=1,conversation_id=? WHERE singleton=1", (conversation_id,))
        return self.get()

    def select(self, expected_revision, conversation_id=None):
        with self.service._transaction():
            self._current(expected_revision, enabled=True)
            conversation_id = self._conversation(conversation_id)
            self._cancel_pending()
            self.db.execute("UPDATE channel_weixin SET revision=revision+1,conversation_id=? WHERE singleton=1", (conversation_id,))
        return self.get()

    def enable(self, expected_revision, enabled):
        if not isinstance(enabled, bool):
            _fail("INVALID_PARAMS", "enabled must be boolean")
        with self.service._transaction():
            current = self._current(expected_revision)
            if not current["bound"] or not current["token"]:
                _fail("CHANNEL_UNCONFIGURED", "Confirm the local WeChat binding first")
            if enabled:
                self._conversation(current["conversation_id"])
            self._cancel_pending()
            self.db.execute("UPDATE channel_weixin SET revision=revision+1,enabled=? WHERE singleton=1", (enabled,))
        return self.get()

    def unbind(self, expected_revision):
        with self.service._transaction():
            self._current(expected_revision)
            self._cancel_pending()
            self._forget_context()
            self.db.execute("""UPDATE channel_weixin SET revision=revision+1,enabled=0,bound=0,installation_id=NULL,
                peer_id=NULL,bot_id=NULL,base_url=NULL,token=NULL,conversation_id=NULL,cursor=NULL,context_token=NULL WHERE singleton=1""")
        return self.get()

    def cursor(self, installation_id, revision, cursor):
        _text(installation_id, "installation_id", 64)
        _text(cursor, "cursor", 64000, empty=True)
        with self.service._transaction():
            self._current(revision, installation_id, enabled=True)
            self.db.execute("UPDATE channel_weixin SET cursor=? WHERE singleton=1", (cursor,))
        return self.get()

    def _event(self, row, *, duplicate=False):
        result = dict(row)
        del result["content_hash"]
        result["context_token"] = self._revealed(result["context_token"])
        result["result_ref"] = json.loads(result["result_ref"]) if result["result_ref"] is not None else None
        result["duplicate"] = duplicate
        return result

    def admit(self, installation_id, revision, event_id, text, context_token=None, created_at=None):
        _text(installation_id, "installation_id", 64)
        _text(event_id, "event_id", 200)
        _text(text, "text", 100000)
        if context_token is not None:
            _text(context_token, "context_token", 16000)
        if created_at is not None:
            _integer(created_at, "created_at")
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        with self.service._transaction():
            current = self._current(revision, installation_id, enabled=True)
            old = self.db.execute("SELECT * FROM channel_weixin_events WHERE installation_id=? AND event_id=?", (installation_id, event_id)).fetchone()
            if old is not None:
                if old["content_hash"] != digest:
                    _fail("CONFLICT", "WeChat event identity already has different content")
                return {"event": self._event(old, duplicate=True)}
            self._conversation(current["conversation_id"])
            count = self.db.execute("SELECT COUNT(*) FROM channel_weixin_events WHERE installation_id=? AND state IN ('queued','submitted')", (installation_id,)).fetchone()[0]
            if count >= self.MAX_PENDING:
                _fail("CHANNEL_QUEUE_FULL", "WeChat pending message limit reached")
            command_id = "weixin-" + hashlib.sha256((installation_id + "\0" + event_id).encode("utf-8")).hexdigest()
            now = self.service.clock()
            protected = self._protected(context_token)
            self.db.execute("""INSERT INTO channel_weixin_events VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (installation_id, revision, event_id, command_id, current["conversation_id"], text, digest,
                 protected, "queued", None, created_at if created_at is not None else now, now))
            if context_token is not None:
                self.db.execute("UPDATE channel_weixin SET context_token=? WHERE singleton=1", (protected,))
            row = self.db.execute("SELECT * FROM channel_weixin_events WHERE installation_id=? AND event_id=?", (installation_id, event_id)).fetchone()
        return {"event": self._event(row)}

    def event(self, installation_id, event_id, state, result_ref=None):
        _text(installation_id, "installation_id", 64)
        _text(event_id, "event_id", 200)
        if not isinstance(state, str) or state not in self.EVENT_STATES:
            _fail("INVALID_PARAMS", "unsupported WeChat event state")
        encoded = _json(result_ref, "result_ref") if result_ref is not None else None
        with self.service._transaction():
            row = self.db.execute("SELECT * FROM channel_weixin_events WHERE installation_id=? AND event_id=?", (installation_id, event_id)).fetchone()
            if row is None:
                _fail("NOT_FOUND", "WeChat event not found")
            if row["state"] in self.EVENT_FINAL:
                if row["state"] != state or encoded is not None and encoded != row["result_ref"]:
                    _fail("CONFLICT", "WeChat event already has a final result")
            elif state == "queued" and row["state"] != "queued":
                _fail("CONFLICT", "WeChat event cannot be replayed")
            else:
                self.db.execute("UPDATE channel_weixin_events SET state=?,result_ref=COALESCE(?,result_ref),updated_at=? WHERE installation_id=? AND event_id=?",
                    (state, encoded, self.service.clock(), installation_id, event_id))
            row = self.db.execute("SELECT * FROM channel_weixin_events WHERE installation_id=? AND event_id=?", (installation_id, event_id)).fetchone()
        return {"event": self._event(row)}

    def events(self, installation_id, states=None, limit=100):
        _text(installation_id, "installation_id", 64)
        _integer(limit, "limit", 1, 1000)
        if states is not None and (not isinstance(states, list) or not states or any(not isinstance(item, str) or item not in self.EVENT_STATES for item in states)):
            _fail("INVALID_PARAMS", "unsupported WeChat event filter")
        where = " AND state IN (" + ",".join("?" for _ in states) + ")" if states else ""
        rows = self.db.execute("SELECT * FROM channel_weixin_events WHERE installation_id=?" + where + " ORDER BY created_at,event_id LIMIT ?", (installation_id, *(states or []), limit))
        return {"events": [self._event(row) for row in rows]}

    def _delivery(self, row):
        result = dict(row)
        del result["content_hash"]
        result["key"] = result.pop("dedupe_key")
        result["context_token"] = self._revealed(result["context_token"])
        result["receipt"] = json.loads(result["receipt"]) if result["receipt"] is not None else None
        return result

    def enqueue(self, installation_id, revision, key, text, conversation_id=None, command_id=None, context_token=None):
        _text(installation_id, "installation_id", 64)
        _text(key, "key", 512)
        _text(text, "text", 100000)
        if conversation_id is not None:
            _text(conversation_id, "conversation_id")
        if command_id is not None:
            _text(command_id, "command_id")
        if context_token is not None:
            _text(context_token, "context_token", 16000)
        digest = hashlib.sha256(json.dumps([text, conversation_id, command_id, context_token],
            ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()
        with self.service._transaction():
            current = self._current(revision, installation_id, enabled=True)
            if conversation_id is not None:
                self._conversation(conversation_id)
            old = self.db.execute("SELECT * FROM channel_weixin_outbox WHERE installation_id=? AND dedupe_key=?", (installation_id, key)).fetchone()
            if old is not None:
                if old["content_hash"] != digest:
                    _fail("CONFLICT", "WeChat delivery identity already has different content")
                return {"delivery": self._delivery(old)}
            count = self.db.execute("SELECT COUNT(*) FROM channel_weixin_outbox WHERE installation_id=? AND status IN ('pending','sending')", (installation_id,)).fetchone()[0]
            if count >= self.MAX_PENDING:
                _fail("CHANNEL_QUEUE_FULL", "WeChat pending delivery limit reached")
            identity = "weixin-delivery-" + hashlib.sha256((installation_id + "\0" + key).encode("utf-8")).hexdigest()
            now = self.service.clock()
            self.db.execute("INSERT INTO channel_weixin_outbox VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (identity, installation_id, revision, key, conversation_id, command_id, text, digest,
                 self._protected(context_token if context_token is not None else current["context_token"]), "pending", None, now, now))
            row = self.db.execute("SELECT * FROM channel_weixin_outbox WHERE delivery_id=?", (identity,)).fetchone()
        return {"delivery": self._delivery(row)}

    def outbox(self, installation_id, limit=100):
        _text(installation_id, "installation_id", 64)
        _integer(limit, "limit", 1, 1000)
        rows = self.db.execute("SELECT * FROM channel_weixin_outbox WHERE installation_id=? AND status='pending' ORDER BY rowid LIMIT ?", (installation_id, limit))
        return {"deliveries": [self._delivery(row) for row in rows]}

    def claim(self, delivery_id):
        _text(delivery_id, "delivery_id", 128)
        with self.service._transaction():
            row = self.db.execute("SELECT * FROM channel_weixin_outbox WHERE delivery_id=?", (delivery_id,)).fetchone()
            if row is None:
                _fail("NOT_FOUND", "WeChat delivery not found")
            if row["status"] != "pending":
                return {"delivery": self._delivery(row)}
            current = self._state()
            valid = current["enabled"] and current["bound"] and current["token"] and current["installation_id"] == row["installation_id"] and current["revision"] == row["revision"]
            status = "sending" if valid else "cancelled"
            self.db.execute("UPDATE channel_weixin_outbox SET status=?,updated_at=? WHERE delivery_id=?", (status, self.service.clock(), delivery_id))
            result = self._delivery(self.db.execute("SELECT * FROM channel_weixin_outbox WHERE delivery_id=?", (delivery_id,)).fetchone())
            if valid:
                result["state"] = current
        return {"delivery": result}

    def ack(self, delivery_id, status, receipt=None):
        _text(delivery_id, "delivery_id", 128)
        if not isinstance(status, str) or status not in self.DELIVERY_FINAL:
            _fail("INVALID_PARAMS", "unsupported WeChat delivery status")
        encoded = _json(receipt, "receipt") if receipt is not None else None
        with self.service._transaction():
            row = self.db.execute("SELECT * FROM channel_weixin_outbox WHERE delivery_id=?", (delivery_id,)).fetchone()
            if row is None:
                _fail("NOT_FOUND", "WeChat delivery not found")
            if row["status"] in self.DELIVERY_FINAL:
                if row["status"] != status or encoded is not None and row["receipt"] != encoded:
                    _fail("CONFLICT", "WeChat delivery already has a final result")
            elif row["status"] != "sending" and status in {"sent", "unknown"}:
                _fail("CONFLICT", "Claim WeChat delivery before acknowledging a send")
            else:
                self.db.execute("UPDATE channel_weixin_outbox SET status=?,receipt=?,updated_at=? WHERE delivery_id=?", (status, encoded, self.service.clock(), delivery_id))
            row = self.db.execute("SELECT * FROM channel_weixin_outbox WHERE delivery_id=?", (delivery_id,)).fetchone()
        return {"delivery": self._delivery(row)}
