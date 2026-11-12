"""User-requested notes, with bounded evidence and an append-only revision log."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
import uuid
from datetime import date, datetime, time, timedelta, timezone


NOTEBOOK_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS notebook_collections (
        id TEXT PRIMARY KEY,parent_id TEXT REFERENCES notebook_collections(id),
        title TEXT NOT NULL,path TEXT NOT NULL,path_key TEXT NOT NULL,kind TEXT NOT NULL,
        sensitivity TEXT NOT NULL,status TEXT NOT NULL,revision INTEGER NOT NULL,
        created_at INTEGER NOT NULL,updated_at INTEGER NOT NULL)""",
    """CREATE UNIQUE INDEX IF NOT EXISTS notebook_active_path
        ON notebook_collections(path_key) WHERE status='active'""",
    """CREATE INDEX IF NOT EXISTS notebook_collection_parent
        ON notebook_collections(parent_id,status,path_key)""",
    """CREATE TABLE IF NOT EXISTS notebook_entries (
        id TEXT PRIMARY KEY,collection_id TEXT REFERENCES notebook_collections(id),
        type TEXT NOT NULL,title TEXT,body TEXT NOT NULL,tags_json TEXT NOT NULL,props_json TEXT NOT NULL,
        event_time TEXT,event_day TEXT,event_sort INTEGER,time_quality TEXT NOT NULL,recorded_at INTEGER NOT NULL,
        status TEXT NOT NULL,sensitivity TEXT NOT NULL,source_json TEXT NOT NULL,created_by TEXT NOT NULL,
        revision INTEGER NOT NULL,semantic_key TEXT NOT NULL UNIQUE)""",
    """CREATE INDEX IF NOT EXISTS notebook_entry_time
        ON notebook_entries(status,event_sort,recorded_at,id)""",
    """CREATE INDEX IF NOT EXISTS notebook_entry_recorded
        ON notebook_entries(status,recorded_at,id)""",
    """CREATE INDEX IF NOT EXISTS notebook_entry_collection
        ON notebook_entries(collection_id,status,recorded_at,id)""",
    """CREATE TABLE IF NOT EXISTS notebook_revisions (
        entry_id TEXT NOT NULL REFERENCES notebook_entries(id),revision INTEGER NOT NULL,
        operation TEXT NOT NULL,reason TEXT,source_json TEXT NOT NULL,changed_at INTEGER NOT NULL,
        snapshot_json TEXT NOT NULL,PRIMARY KEY(entry_id,revision))""",
    """CREATE TABLE IF NOT EXISTS notebook_collection_revisions (
        collection_id TEXT NOT NULL REFERENCES notebook_collections(id),revision INTEGER NOT NULL,
        operation TEXT NOT NULL,source_json TEXT NOT NULL,changed_at INTEGER NOT NULL,
        snapshot_json TEXT NOT NULL,PRIMARY KEY(collection_id,revision))""",
    """CREATE TABLE IF NOT EXISTS notebook_requests (
        conversation_id TEXT NOT NULL,turn_id TEXT NOT NULL,request_id TEXT NOT NULL,
        request_hash TEXT NOT NULL,result_json TEXT NOT NULL,
        PRIMARY KEY(conversation_id,turn_id,request_id))""",
)

ENTRY_FIELDS = (
    "collection_id", "type", "title", "body", "tags_json", "props_json",
    "event_time", "event_day", "event_sort", "time_quality", "status", "sensitivity",
)
PATCH_FIELDS = {"body", "title", "tags", "event_time", "time_quality", "type", "props", "sensitivity"}
ENTRY_SELECT = """
    SELECT e.*,c.title AS collection_title,c.path AS collection_path,c.kind AS collection_kind
    FROM notebook_entries e LEFT JOIN notebook_collections c ON c.id=e.collection_id
"""
TOKEN = re.compile(r"[a-z][a-z0-9_-]{0,39}\Z")
DAY = re.compile(r"\d{4}-\d{2}-\d{2}\Z")
INSTANT = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?(?:Z|[+-]\d{2}:\d{2})\Z")
CREDENTIAL = re.compile(
    r"-----BEGIN (?:[A-Z]+ )*PRIVATE KEY-----"
    r"|\b(?:sk-(?:proj-)?[a-z0-9_-]{12,}|gh[pousr]_[a-z0-9]{12,}|github_pat_[a-z0-9_]{12,}|AKIA[A-Z0-9]{16})\b"
    r"|(?:密码|口令|验证码|恢复码|助记词|密钥|password|passcode|recovery\s+code|api[ _-]?key|access[ _-]?token)"
    r"[\"']?\s*(?:[:=：]|是|为|\bis\b)\s*[^\s,，。;；]+"
    r"|(?:验证码|恢复码|口令)\s+[a-z0-9-]{4,}",
    re.IGNORECASE,
)
IDENTITY = re.compile(r"(?<!\d)[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx](?!\d)|\b\d{3}-\d{2}-\d{4}\b")
CARD_CANDIDATE = re.compile(r"(?<!\d)\d(?:[ -]?\d){12,18}(?![ -]?\d)")
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
MICROSECOND = timedelta(microseconds=1)


def _error(code, message):
    from .service import FrameworkError
    return FrameworkError(code, message)


def _encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(value):
    return hashlib.sha256(_encode(value).encode("utf-8")).hexdigest()


def _text(value, field, maximum):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or "\x00" in value:
        raise _error("INVALID_PARAMS", f"{field} 必须是非空文本，最多 {maximum} 字。")
    return value.strip()


def _integer(value, field, minimum, maximum):
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise _error("INVALID_PARAMS", f"{field} 必须是 {minimum} 至 {maximum} 之间的整数。")
    return value


def _privacy_flag(value):
    if not isinstance(value, bool):
        raise _error("INVALID_PARAMS", "include_private 必须是布尔值。")
    return value


def _fold(value):
    return unicodedata.normalize("NFKC", value).casefold()


def _tags(values):
    if values is None:
        return []
    if not isinstance(values, list) or len(values) > 20:
        raise _error("INVALID_PARAMS", "最多提供 20 个标签。")
    unique = {}
    for value in values:
        tag = _text(value, "tag", 40)
        unique.setdefault(_fold(tag), tag)
    return list(unique.values())


def _type(value):
    if not isinstance(value, str) or not TOKEN.fullmatch(value):
        raise _error("INVALID_PARAMS", "type 必须是最多 40 字符的小写标识符。")
    return value


def _time(value):
    if value is None:
        return None, None, None, "unknown"
    try:
        if isinstance(value, str) and DAY.fullmatch(value):
            day = date.fromisoformat(value)
            timestamp = datetime.combine(day, time(), timezone.utc)
            return value, value, (timestamp - EPOCH) // MICROSECOND, "day"
        if isinstance(value, str) and INSTANT.fullmatch(value):
            instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return instant.isoformat(), instant.date().isoformat(), (instant - EPOCH) // MICROSECOND, "exact"
    except (ValueError, OverflowError):
        pass
    raise _error("INVALID_PARAMS", "时间须为 YYYY-MM-DD 或带时区偏移的 ISO 日期时间。")


def _looks_like_card(value):
    for match in CARD_CANDIDATE.finditer(value):
        digits = list(map(int, re.sub(r"\D", "", match.group())))
        total = sum(digit if index % 2 == 0 else digit * 2 - 9 * (digit > 4)
                    for index, digit in enumerate(reversed(digits)))
        if total % 10 == 0 and len(set(digits)) > 1:
            return True
    return False


def _privacy(*texts):
    text = "\n".join(unicodedata.normalize("NFKC", part) for part in texts if part)
    credential = bool(CREDENTIAL.search(text))
    return credential or bool(IDENTITY.search(text)) or _looks_like_card(text), credential


def _path(value):
    if not isinstance(value, str) or len(value) > 100:
        raise _error("INVALID_PARAMS", "目录路径最多四级，每级最多 24 字。")
    segments = [unicodedata.normalize("NFC", part.strip()) for part in value.split("/")]
    if len(segments) > 4 or any(
        not 1 <= len(part) <= 24 or _fold(part) in {".", ".."} or "/" in _fold(part) or "\\" in _fold(part)
        or any(unicodedata.category(char).startswith("C") for char in part)
        for part in segments
    ):
        raise _error("INVALID_PARAMS", "目录路径最多四级，每级须为 1 至 24 字的有效名称。")
    return segments


def _collection(row):
    if row is None:
        return None
    return {"collection_id": row["id"], "parent_id": row["parent_id"], "title": row["title"],
            "path": row["path"], "kind": row["kind"], "revision": row["revision"],
            "status": row["status"], "sensitivity": row["sensitivity"]}


def _entry_view(row, *, detail=False):
    collection = None
    if row["collection_id"] is not None:
        collection = {"collection_id": row["collection_id"], "title": row["collection_title"],
                      "path": row["collection_path"], "kind": row["collection_kind"]}
    source = json.loads(row["source_json"])
    if not detail:
        source.pop("utterance")
    view = {"entry_id": row["id"], "title": row["title"], "excerpt": row["body"][:300],
            "tags": json.loads(row["tags_json"]), "type": row["type"], "props": json.loads(row["props_json"]),
            "event_time": row["event_time"], "time_quality": row["time_quality"], "recorded_at": row["recorded_at"],
            "status": row["status"], "sensitivity": row["sensitivity"], "revision": row["revision"],
            "collection": collection, "source": source, "created_by": row["created_by"]}
    if detail:
        view["body"] = row["body"]
    return view


class NotebookOwner:
    def __init__(self, service):
        self.s = service
        self.db = service.db
        self.db.create_function("notebook_fold", 1, _fold, deterministic=True)

    def routes(self):
        return {
            "notebook.entry.add": self.add, "notebook.entry.get": self.get,
            "notebook.entry.revise": self.revise, "notebook.entry.reverse": self.reverse,
            "notebook.entry.delete": self.delete, "notebook.entry.move": self.move,
            "notebook.search": self.search, "notebook.timeline": self.timeline,
            "notebook.collection.create": self.collection_create, "notebook.collection.list": self.collection_list,
            "notebook.collection.rename": self.collection_rename, "notebook.collection.archive": self.collection_archive,
        }

    def _source(self, source, weixin_scope, *, required):
        if source is None and weixin_scope is None and not required:
            return None
        if not isinstance(source, dict) or set(source) != {"conversation_id", "turn_id", "channel", "utterance"}:
            raise _error("INVALID_PARAMS", "记事操作缺少会话来源。")
        conversation_id = _text(source["conversation_id"], "conversation_id", 200)
        turn_id = _text(source["turn_id"], "turn_id", 200)
        channel, utterance = source["channel"], source["utterance"]
        if channel not in ("desktop", "weixin") or not isinstance(utterance, str):
            raise _error("INVALID_PARAMS", "记事操作来源无效。")
        if channel == "weixin":
            if not isinstance(weixin_scope, dict) or turn_id != weixin_scope.get("command_id"):
                raise _error("CONFLICT", "微信会话授权已改变，请重新发起。")
            self.s.channels.check_conversation_scope(conversation_id, weixin_scope)
        elif weixin_scope is not None:
            raise _error("INVALID_PARAMS", "记事操作来源与会话授权不匹配。")
        if not self.s.guard_check(conversation_id=conversation_id, command_id=turn_id)["allow"]:
            raise _error("CONFLICT", "当前记事请求已失效，请重新发起。")
        return {"conversation_id": conversation_id, "turn_id": turn_id, "channel": channel,
                "utterance": utterance[:200]}

    def _write(self, operation, source, request_id, weixin_scope, params, apply):
        request_id = _text(request_id, "request_id", 200)
        with self.s._transaction():
            source = self._source(source, weixin_scope, required=True)
            key = (source["conversation_id"], source["turn_id"], request_id)
            signature = _hash((operation, params))
            receipt = self.db.execute(
                "SELECT request_hash,result_json FROM notebook_requests WHERE conversation_id=? AND turn_id=? AND request_id=?",
                key,
            ).fetchone()
            if receipt:
                if receipt["request_hash"] != signature:
                    raise _error("IDEMPOTENCY_MISMATCH", "同一次记事请求的内容发生变化。")
                return {**json.loads(receipt["result_json"]), "replayed": True}
            result = apply(source)
            self.db.execute("INSERT INTO notebook_requests VALUES(?,?,?,?,?)", (*key, signature, _encode(result)))
            return {"replayed": False, **result}

    def _entry_row(self, entry_id, include_private):
        entry_id = _text(entry_id, "entry_id", 64)
        row = self.db.execute(ENTRY_SELECT + " WHERE e.id=?", (entry_id,)).fetchone()
        if row is None or (row["sensitivity"] == "private" and not include_private):
            raise _error("NOT_FOUND", "记录不存在。")
        return row

    def _collection_row(self, collection_id, include_private):
        collection_id = _text(collection_id, "collection_id", 64)
        row = self.db.execute("SELECT * FROM notebook_collections WHERE id=? AND status='active'", (collection_id,)).fetchone()
        if row is None or (row["sensitivity"] == "private" and not include_private):
            raise _error("NOT_FOUND", "目录不存在。")
        return row

    def _check_revision(self, row, expected_revision):
        if row["revision"] != _integer(expected_revision, "expected_revision", 1, 2**63 - 1):
            raise _error("CONFLICT", "记录已更新，请重新读取后再操作。")

    def _contents(self, body, title, tags, event_time, time_quality, type, props, sensitivity, source,
                  *, previous_sensitivity="public", reason=None):
        body = _text(body, "body", 8000)
        title = _text(title, "title", 160) if title is not None else None
        tags, type = _tags(tags), _type(type)
        event_time, event_day, event_sort, inferred = _time(event_time)
        quality = inferred if time_quality is None else time_quality
        if quality not in (inferred, "approx") or (event_time is None and quality != "unknown"):
            raise _error("INVALID_PARAMS", "time_quality 与事件时间不匹配。")
        props = {} if props is None else props
        if not isinstance(props, dict) or len(_encode(props)) > 2048:
            raise _error("INVALID_PARAMS", "props 必须是最多 2048 字符的对象。")
        if sensitivity not in (None, "public", "private"):
            raise _error("INVALID_PARAMS", "sensitivity 须为 public 或 private。")
        tags_json, props_json = _encode(tags), _encode(props)
        detected, credential = _privacy(body, title, tags_json, props_json, source["utterance"], reason)
        private = detected or sensitivity == "private" or previous_sensitivity == "private"
        return {
            "type": type, "title": title, "body": body, "tags_json": tags_json, "props_json": props_json,
            "event_time": event_time, "event_day": event_day, "event_sort": event_sort, "time_quality": quality,
            "sensitivity": "private" if private else "public",
        }, credential

    def _entry_revision(self, row, operation, source, reason=None):
        snapshot = {field: row[field] for field in ENTRY_FIELDS}
        self.db.execute("INSERT INTO notebook_revisions VALUES(?,?,?,?,?,?,?)",
                        (row["id"], row["revision"], operation, reason, _encode(source), self.s.clock(), _encode(snapshot)))

    def _changed_entry(self, row, changes, operation, source, reason=None):
        record = {field: row[field] for field in ENTRY_FIELDS}
        record.update(changes)
        revision = row["revision"] + 1
        assignments = ",".join(field + "=?" for field in ENTRY_FIELDS)
        self.db.execute(f"UPDATE notebook_entries SET {assignments},revision=? WHERE id=?",
                        (*(record[field] for field in ENTRY_FIELDS), revision, row["id"]))
        updated = {"id": row["id"], "revision": revision, **record}
        self._entry_revision(updated, operation, source, reason)
        return updated

    def _entry_result(self, row, credential=False):
        warnings = ["这条记录可能含凭据。聊天中已出现的凭据应尽快更换。"] if credential else []
        return {"entry_id": row["id"], "revision": row["revision"], "status": row["status"],
                "sensitivity": row["sensitivity"], "warnings": warnings}

    def _collection_revision(self, row, operation, source):
        self.db.execute("INSERT INTO notebook_collection_revisions VALUES(?,?,?,?,?,?)",
                        (row["id"], row["revision"], operation, _encode(source), self.s.clock(), _encode(_collection(row))))

    def _ensure_path(self, segments, kind, source, *, private=False, include_private=False):
        row = None
        path = ""
        created = False
        for index, segment in enumerate(segments):
            path = f"{path}/{segment}" if path else segment
            path_key = _fold(path)
            found = self.db.execute("SELECT * FROM notebook_collections WHERE status='active' AND path_key=?",
                                    (path_key,)).fetchone()
            if found is not None:
                if found["sensitivity"] == "private" and not include_private:
                    raise _error("NOT_FOUND", "目录不存在。")
            else:
                parent_id = row["id"] if row else None
                inherited = row is not None and row["sensitivity"] == "private"
                sensitive, _ = _privacy(path)
                sensitivity = "private" if private or inherited or sensitive else "public"
                directory_kind = kind if index == len(segments) - 1 else "auto_created"
                now = self.s.clock()
                found = {"id": uuid.uuid4().hex, "parent_id": parent_id, "title": segment, "path": path,
                         "path_key": path_key, "kind": directory_kind, "sensitivity": sensitivity, "status": "active",
                         "revision": 1, "created_at": now, "updated_at": now}
                self.db.execute("""INSERT INTO notebook_collections
                    (id,parent_id,title,path,path_key,kind,sensitivity,status,revision,created_at,updated_at)
                    VALUES(:id,:parent_id,:title,:path,:path_key,:kind,:sensitivity,:status,:revision,:created_at,:updated_at)""",
                                found)
                self._collection_revision(found, "create", source)
                created = True
            row = found
            path = row["path"]
        return row, created

    def add(self, body, source, request_id, title=None, tags=None, event_time=None, time_quality=None,
            collection_id=None, collection_path=None, type="note", props=None, sensitivity=None, weixin_scope=None,
            include_private=False):
        _privacy_flag(include_private)
        if collection_id is not None and collection_path is not None:
            raise _error("INVALID_PARAMS", "请选择目录编号或目录路径。")
        params = (body, title, tags, event_time, time_quality, collection_id, collection_path, type, props,
                  sensitivity, include_private)

        def apply(trusted):
            entry, credential = self._contents(body, title, tags, event_time, time_quality, type, props, sensitivity, trusted)
            collection, created = None, False
            if collection_id is not None:
                collection = self._collection_row(collection_id, include_private)
            if collection_path is not None:
                collection, created = self._ensure_path(_path(collection_path), "auto_created", trusted,
                                                        private=entry["sensitivity"] == "private",
                                                        include_private=include_private)
            assigned = collection["id"] if collection else None
            if collection and collection["sensitivity"] == "private":
                entry["sensitivity"] = "private"
            semantic_time = entry["event_sort"] if entry["event_day"] != entry["event_time"] else entry["event_time"]
            semantic_key = _hash((trusted["conversation_id"], trusted["turn_id"], entry["type"],
                                  " ".join(unicodedata.normalize("NFC", entry["body"]).split()), semantic_time, assigned))
            row = self.db.execute("SELECT * FROM notebook_entries WHERE semantic_key=?", (semantic_key,)).fetchone()
            if row is not None:
                created_by = row["created_by"]
                if entry["sensitivity"] == "private" and row["sensitivity"] == "public":
                    row = self._changed_entry(row, {"sensitivity": "private"}, "revise", trusted)
                return {**self._entry_result(row, credential), "created_by": created_by, "replayed": True}
            utterance = unicodedata.normalize("NFC", trusted["utterance"])
            created_by = "user" if unicodedata.normalize("NFC", entry["body"]) in utterance else "model_inferred"
            record = {"id": uuid.uuid4().hex, "collection_id": assigned, **entry,
                      "recorded_at": self.s.clock(), "status": "active", "source_json": _encode(trusted),
                      "created_by": created_by, "revision": 1, "semantic_key": semantic_key}
            columns = ",".join(record)
            placeholders = ",".join("?" for _ in record)
            self.db.execute(f"INSERT INTO notebook_entries({columns}) VALUES({placeholders})", tuple(record.values()))
            self._entry_revision(record, "add", trusted)
            return {**self._entry_result(record, credential), "collection_id": assigned,
                    "collection": _collection(collection), "collection_created": created, "created_by": created_by,
                    "suggested_tags": json.loads(record["tags_json"]) if record["sensitivity"] == "public" else []}

        return self._write("add", source, request_id, weixin_scope, params, apply)

    def get(self, entry_id, include_private=False, source=None, weixin_scope=None):
        _privacy_flag(include_private)
        with self.s._transaction():
            self._source(source, weixin_scope, required=False)
            row = self._entry_row(entry_id, include_private)
            revisions = self.db.execute("""SELECT * FROM notebook_revisions WHERE entry_id=?
                ORDER BY revision DESC LIMIT 10""", (row["id"],)).fetchall()
            history = []
            for revision in revisions:
                snapshot = json.loads(revision["snapshot_json"])
                history.append({"revision": revision["revision"], "operation": revision["operation"],
                                "reason": revision["reason"], "source": json.loads(revision["source_json"]),
                                "changed_at": revision["changed_at"], "status": snapshot["status"],
                                "collection_id": snapshot["collection_id"], "title": snapshot["title"],
                                "event_time": snapshot["event_time"], "excerpt": snapshot["body"][:300]})
            return {"entry": _entry_view(row, detail=True), "revisions": history, "revision_total": row["revision"]}

    def _change(self, operation, entry_id, expected_revision, source, request_id, weixin_scope, include_private,
                *, reason=None, patch=None, collection_id=None):
        _privacy_flag(include_private)
        reason = _text(reason, "reason", 300) if reason is not None else None

        def apply(trusted):
            row = self._entry_row(entry_id, include_private)
            self._check_revision(row, expected_revision)
            if row["status"] != "active":
                raise _error("CONFLICT", "只能修改有效记录。")
            detected, credential = _privacy(trusted["utterance"], reason)
            changes = {"sensitivity": "private" if detected else row["sensitivity"]}
            if operation == "revise":
                values = {field: row[field] for field in ("body", "title", "event_time", "time_quality", "type")}
                values.update(tags=json.loads(row["tags_json"]), props=json.loads(row["props_json"]), sensitivity=None)
                if "event_time" in patch and "time_quality" not in patch:
                    values["time_quality"] = None
                values.update(patch)
                changes, credential = self._contents(**values, source=trusted, reason=reason,
                                                     previous_sensitivity=row["sensitivity"])
            elif operation == "move":
                collection = self._collection_row(collection_id, include_private) if collection_id is not None else None
                changes["collection_id"] = collection["id"] if collection else None
                if collection and collection["sensitivity"] == "private":
                    changes["sensitivity"] = "private"
            else:
                changes["status"] = {"reverse": "reversed", "delete": "deleted"}[operation]
            updated = self._changed_entry(row, changes, operation, trusted, reason)
            return self._entry_result(updated, credential)

        params = (entry_id, expected_revision, reason, patch, collection_id, include_private)
        return self._write(operation, source, request_id, weixin_scope, params, apply)

    def revise(self, entry_id, expected_revision, patch, source, request_id, reason=None, weixin_scope=None, include_private=False):
        if not isinstance(patch, dict) or not patch or set(patch) - PATCH_FIELDS:
            raise _error("INVALID_PARAMS", "修订内容为空或含不支持的字段。")
        return self._change("revise", entry_id, expected_revision, source, request_id, weixin_scope, include_private,
                            reason=reason, patch=patch)

    def reverse(self, entry_id, expected_revision, source, request_id, reason=None, weixin_scope=None, include_private=False):
        return self._change("reverse", entry_id, expected_revision, source, request_id, weixin_scope, include_private,
                            reason=reason)

    def delete(self, entry_id, expected_revision, source, request_id, reason=None, weixin_scope=None, include_private=False):
        return self._change("delete", entry_id, expected_revision, source, request_id, weixin_scope, include_private,
                            reason=reason)

    def move(self, entry_id, expected_revision, collection_id, source, request_id, reason=None, weixin_scope=None, include_private=False):
        return self._change("move", entry_id, expected_revision, source, request_id, weixin_scope, include_private,
                            reason=reason, collection_id=collection_id)

    def _search(self, query, tags, collection_id, type, from_time, to_time, limit, offset, include_private, *, timeline):
        filters = ["e.status='active'"]
        parameters = []
        if not include_private:
            filters.append("e.sensitivity='public'")
        if collection_id is not None:
            collection_id = self._collection_row(collection_id, include_private)["id"]
            filters.append("e.collection_id=?")
            parameters.append(collection_id)
        if type is not None:
            filters.append("e.type=?")
            parameters.append(_type(type))
        for tag in _tags(tags):
            filters.append("EXISTS(SELECT 1 FROM json_each(e.tags_json) WHERE notebook_fold(value)=?)")
            parameters.append(_fold(tag))
        if query is not None:
            terms = list(dict.fromkeys(_fold(_text(query, "query", 240)).split()))
            if len(terms) > 20:
                raise _error("INVALID_PARAMS", "搜索最多支持 20 个关键词。")
            for term in terms:
                filters.append("instr(notebook_fold(coalesce(e.title,'')||char(10)||e.body||char(10)||e.tags_json||char(10)||e.props_json),?)>0")
                parameters.append(term)
        start, end = _time(from_time), _time(to_time)
        if start[0] is not None and end[0] is not None and start[3] == end[3] and start[2] > end[2]:
            raise _error("INVALID_PARAMS", "查询开始时间不得晚于结束时间。")
        for bound, comparison in ((start, ">="), (end, "<=")):
            if bound[3] == "day":
                filters.append(f"e.event_day{comparison}?")
                parameters.append(bound[1])
            if bound[3] == "exact":
                filters.append(f"e.event_sort{comparison}?")
                parameters.append(bound[2])
        order = "e.event_sort DESC,e.recorded_at DESC,e.id DESC" if timeline else "e.recorded_at DESC,e.id DESC"
        return self._page_entries(filters, parameters, order, limit, offset)

    def _page_entries(self, filters, parameters, order, limit, offset):
        where = " WHERE " + " AND ".join(filters)
        total = self.db.execute("SELECT COUNT(*) FROM notebook_entries e" + where, parameters).fetchone()[0]
        rows = self.db.execute(ENTRY_SELECT + where + " ORDER BY " + order + " LIMIT ? OFFSET ?",
                               (*parameters, limit, offset)).fetchall()
        entries = [_entry_view(row) for row in rows]
        evidence = [{key: entry[key] for key in ("entry_id", "event_time", "collection", "excerpt")} for entry in entries]
        return {"entries": entries, "evidence": evidence, "total": total, "limit": limit, "offset": offset}

    def _query(self, query, tags, collection_id, type, from_time, to_time, limit, offset, include_private, source,
               weixin_scope, *, timeline):
        limit = _integer(limit, "limit", 1, 50)
        offset = _integer(offset, "offset", 0, 2**63 - 1)
        _privacy_flag(include_private)
        with self.s._transaction():
            self._source(source, weixin_scope, required=False)
            return self._search(query, tags, collection_id, type, from_time, to_time, limit, offset, include_private,
                                timeline=timeline)

    def search(self, query=None, tags=None, collection_id=None, type=None, from_time=None, to_time=None, limit=20, offset=0,
               include_private=False, source=None, weixin_scope=None):
        return self._query(query, tags, collection_id, type, from_time, to_time, limit, offset, include_private, source,
                           weixin_scope, timeline=False)

    def timeline(self, from_time=None, to_time=None, tags=None, collection_id=None, type=None, limit=20, offset=0,
                 include_private=False, source=None, weixin_scope=None):
        return self._query(None, tags, collection_id, type, from_time, to_time, limit, offset, include_private, source,
                           weixin_scope, timeline=True)

    def collection_create(self, path, source, request_id, kind="user_defined", weixin_scope=None, include_private=False):
        _privacy_flag(include_private)
        if kind not in ("user_defined", "auto_created"):
            raise _error("INVALID_PARAMS", "目录类型无效。")
        segments = _path(path)

        def apply(trusted):
            row, created = self._ensure_path(segments, kind, trusted, include_private=include_private)
            return {**_collection(row), "collection_created": created}

        return self._write("collection.create", source, request_id, weixin_scope, (segments, kind, include_private), apply)

    def collection_list(self, collection_id=None, limit=20, offset=0, include_private=False, source=None, weixin_scope=None):
        limit = _integer(limit, "limit", 1, 50)
        offset = _integer(offset, "offset", 0, 2**63 - 1)
        _privacy_flag(include_private)
        with self.s._transaction():
            self._source(source, weixin_scope, required=False)
            parent = self._collection_row(collection_id, include_private) if collection_id is not None else None
            collection_id = parent["id"] if parent else None
            filters = " WHERE parent_id IS ? AND status='active'"
            if not include_private:
                filters += " AND sensitivity='public'"
            total = self.db.execute("SELECT COUNT(*) FROM notebook_collections" + filters, (collection_id,)).fetchone()[0]
            rows = self.db.execute("SELECT * FROM notebook_collections" + filters + " ORDER BY path_key,id LIMIT ? OFFSET ?",
                                   (collection_id, limit, offset)).fetchall()
            entry_filters = ["e.status='active'", "e.collection_id IS ?"]
            if not include_private:
                entry_filters.append("e.sensitivity='public'")
            page = self._page_entries(entry_filters, (collection_id,), "e.recorded_at DESC,e.id DESC", limit, offset)
            return {"collection": _collection(parent), "collections": [_collection(row) for row in rows],
                    "total": total, "limit": limit, "offset": offset, "entries": page["entries"], "entry_total": page["total"]}

    def _subtree(self, row):
        prefix = row["path_key"] + "/"
        return self.db.execute("""SELECT * FROM notebook_collections WHERE status='active'
            AND (id=? OR substr(path_key,1,?)=?) ORDER BY length(path_key),id""",
                               (row["id"], len(prefix), prefix)).fetchall()

    def collection_rename(self, collection_id, title, source, request_id, weixin_scope=None, expected_revision=None,
                          include_private=False):
        _privacy_flag(include_private)
        segments = _path(title)
        if len(segments) != 1:
            raise _error("INVALID_PARAMS", "改名只接受单级目录名称。")
        title = segments[0]

        def apply(trusted):
            root = self._collection_row(collection_id, include_private)
            if expected_revision is not None:
                self._check_revision(root, expected_revision)
            if root["sensitivity"] == "public" and _privacy(title)[0]:
                raise _error("INVALID_PARAMS", "公开目录名称不能包含敏感内容。")
            prefix = root["path"].rpartition("/")[0]
            path = f"{prefix}/{title}" if prefix else title
            if self.db.execute("SELECT 1 FROM notebook_collections WHERE status='active' AND path_key=? AND id<>?",
                               (_fold(path), root["id"])).fetchone():
                raise _error("CONFLICT", "已有同名目录，请使用其他名称。")
            for row in self._subtree(root):
                current_path = path + row["path"][len(root["path"]):]
                current_title = title if row["id"] == root["id"] else row["title"]
                revision = row["revision"] + 1
                self.db.execute("""UPDATE notebook_collections SET title=?,path=?,path_key=?,revision=?,updated_at=?
                    WHERE id=?""", (current_title, current_path, _fold(current_path), revision, self.s.clock(), row["id"]))
                self._collection_revision({**dict(row), "title": current_title, "path": current_path, "revision": revision},
                                          "rename", trusted)
            return _collection(self._collection_row(collection_id, include_private))

        return self._write("collection.rename", source, request_id, weixin_scope,
                           (collection_id, title, expected_revision, include_private), apply)

    def collection_archive(self, collection_id, source, request_id, weixin_scope=None, expected_revision=None,
                           include_private=False):
        _privacy_flag(include_private)

        def apply(trusted):
            root = self._collection_row(collection_id, include_private)
            if expected_revision is not None:
                self._check_revision(root, expected_revision)
            # Directory audit keeps the origin. Entry history must not relay unrelated private text.
            entry_source = {**trusted, "utterance": ""}
            for row in self._subtree(root):
                entries = self.db.execute("SELECT * FROM notebook_entries WHERE collection_id=?", (row["id"],)).fetchall()
                for entry in entries:
                    self._changed_entry(entry, {"collection_id": None}, "archive_collection", entry_source)
                revision = row["revision"] + 1
                self.db.execute("UPDATE notebook_collections SET status='archived',revision=?,updated_at=? WHERE id=?",
                                (revision, self.s.clock(), row["id"]))
                self._collection_revision({**dict(row), "status": "archived", "revision": revision}, "archive", trusted)
            return {**_collection(root), "status": "archived", "revision": root["revision"] + 1}

        return self._write("collection.archive", source, request_id, weixin_scope,
                           (collection_id, expected_revision, include_private), apply)
