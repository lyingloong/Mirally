"""Product work, bounded observations and supervised, explicitly approved runs.

This owner stores references rather than a second transcript. Python scripts are
trusted local code after an exact approval, not an operating-system sandbox.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import mimetypes
import os
from pathlib import Path
import signal
import struct
import subprocess
import sys
import threading
import time
import uuid

from . import capabilities

WORK_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS works (id TEXT PRIMARY KEY,request_id TEXT UNIQUE,title TEXT NOT NULL,
        goal TEXT NOT NULL,conversation_id TEXT NOT NULL REFERENCES conversations(id),intent TEXT NOT NULL,
        source_json TEXT,notifications_json TEXT NOT NULL,revision INTEGER NOT NULL,created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL,observation_json TEXT)""",
    """CREATE TABLE IF NOT EXISTS work_approvals (id TEXT PRIMARY KEY,work_id TEXT NOT NULL REFERENCES works(id),
        request_id TEXT UNIQUE NOT NULL,actor TEXT NOT NULL,work_revision INTEGER NOT NULL,spec_json TEXT NOT NULL,
        digest TEXT NOT NULL,status TEXT NOT NULL,token TEXT,created_at INTEGER NOT NULL,expires_at INTEGER NOT NULL,
        approved_actor TEXT)""",
    """CREATE TABLE IF NOT EXISTS work_runs (id TEXT PRIMARY KEY,work_id TEXT NOT NULL REFERENCES works(id),
        approval_id TEXT UNIQUE NOT NULL REFERENCES work_approvals(id),request_id TEXT UNIQUE NOT NULL,
        status TEXT NOT NULL,pid INTEGER,created_at INTEGER NOT NULL,started_at INTEGER,finished_at INTEGER,
        exit_code INTEGER,log_bytes INTEGER NOT NULL DEFAULT 0,log_truncated INTEGER NOT NULL DEFAULT 0,
        spec_json TEXT NOT NULL,artifacts_json TEXT NOT NULL DEFAULT '[]',error TEXT)""",
    """CREATE TABLE IF NOT EXISTS work_events (event_id TEXT PRIMARY KEY,work_id TEXT NOT NULL REFERENCES works(id),
        kind TEXT NOT NULL,title TEXT NOT NULL,text TEXT NOT NULL,created_at INTEGER NOT NULL,deliveries_json TEXT NOT NULL DEFAULT '{}')""",
    "CREATE INDEX IF NOT EXISTS work_runs_latest ON work_runs(work_id,created_at)",
)
ACTIVE = {"starting", "running", "cancelling"}
LOG_LIMIT = 1024 * 1024
DEFAULT_NOTIFICATIONS = {"completed": True, "failed": True, "approval": True}

# Only this fixed program is placed on the command line. The approved source is
# supplied as a length-framed byte snapshot over an anonymous pipe, so neither
# Windows command-line limits nor changes to the original file affect execution.
PYTHON_BOOTSTRAP = """def _mirally_bootstrap():
    import builtins, os, struct, sys
    stream = sys.stdin.buffer
    header = stream.read(8)
    if len(header) != 8:
        raise RuntimeError('Missing approved script frame')
    length = struct.unpack('>Q', header)[0]
    if not 0 <= length <= 1048576:
        raise RuntimeError('Approved script frame exceeds limit')
    source = stream.read(length)
    if len(source) != length:
        raise RuntimeError('Incomplete approved script frame')
    original = sys.argv[1]
    sys.argv = sys.argv[1:]
    sys.path[0] = os.path.dirname(original)
    sys.stdin = open(os.devnull, 'r', encoding='utf-8')
    sys.__stdin__ = sys.stdin
    code = compile(source, original, 'exec')
    namespace = sys.modules['__main__'].__dict__
    namespace.clear()
    namespace.update(__name__='__main__', __file__=original, __package__=None,
                     __spec__=None, __loader__=None, __cached__=None,
                     __builtins__=builtins.__dict__)
    exec(code, namespace)
_mirally_bootstrap()
"""


def fail(code, message):
    from .service import FrameworkError
    raise FrameworkError(code, message)


def text(value, name, limit=512, empty=False):
    if not isinstance(value, str) or len(value) > limit or "\x00" in value or (not empty and not value.strip()):
        fail("INVALID_PARAMS", name + " 必须为有限长度的文字")
    return value


def integer(value, name, low=0, high=2**63-1):
    if type(value) is not int or not low <= value <= high:
        fail("INVALID_PARAMS", name + " 超出允许范围")
    return value


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def switches(value, allowed):
    if not isinstance(value, dict) or set(value) - set(allowed) or any(type(v) is not bool for v in value.values()):
        fail("INVALID_PARAMS", "通知设置无效")
    return value


def finite_number(value):
    return type(value) in {int, float} and abs(value) <= 1e100 and math.isfinite(value)


class WorkOwner:
    def __init__(self, service):
        self.s = service
        self.active = {}
        self.closing = False
        self.log_home = service.home / "framework" / "work-logs"
        self.log_home.mkdir(parents=True, exist_ok=True)
        with service._transaction():
            # Schema 5 was introduced with Work. Accept an earlier development
            # database without dropping pending history or increasing privileges.
            columns = {r[1] for r in service.db.execute("PRAGMA table_info(work_approvals)")}
            if "approved_actor" not in columns:
                service.db.execute("ALTER TABLE work_approvals ADD COLUMN approved_actor TEXT")
            service.db.execute("UPDATE work_runs SET status='unknown',error='宿主中断，未重放；请核对已有结果',finished_at=? WHERE status IN ('starting','running','cancelling')", (service.clock(),))
            service.db.execute("UPDATE work_approvals SET status='expired',token=NULL WHERE status IN ('pending','approved')")

    def routes(self):
        return {"work.create": self.create, "work.list": self.list, "work.get": self.get,
                "work.update": self.update, "work.observe": self.observe,
                "work.by_conversation": self.by_conversation,
                "internal.work.ensure_conversation": self.ensure_conversation,
                "internal.work.update_conversation": self.update_conversation,
                "work.run.prepare": self.prepare, "work.run.approve": self.approve,
                "work.run.start": self.start, "work.run.cancel": self.cancel,
                "work.run.get": self.run_get, "work.run.logs": self.logs, "work.run.artifact": self.artifact,
                "work.settings.get": self.settings_get, "work.settings.update": self.settings_update,
                "work.notifications": self.notifications, "work.notifications.ack": self.notification_ack,
                "work.notifications.claim": self.notification_claim, "work.notifications.get": self.notification_get,
                "work.channel.selection.get": self.selection_get, "work.channel.selection.set": self.selection_set}

    def _row(self, table, identity):
        text(identity, "id")
        column = "event_id" if table == "work_events" else "id"
        row = self.s.db.execute(f"SELECT * FROM {table} WHERE {column}=?", (identity,)).fetchone()
        if row is None:
            fail("NOT_FOUND", "工作、运行或审批不存在")
        return dict(row)

    def _approval(self, row):
        result = {k: v for k, v in row.items() if k not in {"token", "spec_json"}}
        result["spec"] = json.loads(row["spec_json"])
        result.update(result["spec"])
        result["approval_id"] = result["id"]
        result["prepared_actor"] = result["actor"]
        if result["status"] in {"pending", "approved"} and result["expires_at"] <= self.s.clock():
            result["status"] = "expired"
        return result

    def _run(self, row):
        result = {k: v for k, v in row.items() if k not in {"spec_json", "artifacts_json"}}
        result["spec"] = json.loads(row["spec_json"])
        result["artifacts"] = json.loads(row["artifacts_json"])
        result["log_truncated"] = bool(result["log_truncated"])
        result["run_id"] = result["id"]
        return result

    def _work(self, row, compact=False):
        result = {k: v for k, v in row.items() if k not in {"source_json", "notifications_json", "observation_json"}}
        result["source"] = json.loads(row["source_json"]) if row["source_json"] else None
        result["notifications"] = json.loads(row["notifications_json"])
        result["observation"] = json.loads(row["observation_json"]) if row["observation_json"] else None
        if result["source"]:
            try:
                capabilities.target(self.s, result["source"]["root_id"], result["source"]["path"])
            except (ValueError, OSError):
                old_time = (result["observation"] or {}).get("observed_at", self.s.clock())
                result["observation"] = {"state": "unknown", "summary": "来源授权已撤回或路径已改变，请重新核对", "withdrawn": True, "observed_at": old_time, "controllable": False}
        latest = self.s.db.execute("SELECT * FROM work_runs WHERE work_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1", (row["id"],)).fetchone()
        result["latest_run"] = self._run(dict(latest)) if latest else None
        pending = self.s.db.execute("SELECT * FROM work_approvals WHERE work_id=? AND status IN ('pending','approved') AND expires_at>? ORDER BY created_at DESC LIMIT 16", (row["id"], self.s.clock())).fetchall()
        result["pending_approvals"] = [self._approval(dict(r)) for r in pending]
        result["status"] = (row["intent"] if row["intent"] != "watching" else
                            "cancelling" if latest and latest["status"] == "cancelling" else
                            "running" if latest and latest["status"] in ACTIVE else
                            "needs_approval" if pending else
                            latest["status"] if latest else
                            (result["observation"] or {}).get("state", "observing" if result["source"] else "idle"))
        if result["observation"] and result["observation"].get("source_updated_at"):
            # Reading a cached log again is not a new change in the work.
            result["updated_at"] = max(result["updated_at"], min(self.s.clock(), result["observation"]["source_updated_at"]))
        if latest:
            result["updated_at"] = max(result["updated_at"], latest["finished_at"] or latest["started_at"] or latest["created_at"])
        if compact:
            result["goal"] = result["goal"][:1000]
            if result["observation"]:
                result["observation"] = {k: v for k, v in result["observation"].items() if k not in {"summary", "metrics", "artifacts"}} | {"summary": result["observation"].get("summary", "")[:1000]}
            if result["latest_run"]:
                result["latest_run"] = {k: v for k, v in result["latest_run"].items() if k not in {"spec", "artifacts"}}
            result["pending_approvals"] = [{k: v for k, v in a.items() if k in {"id", "approval_id", "work_id", "digest", "status", "expires_at", "created_at", "actor"}} for a in result["pending_approvals"]]
        return result

    def _source(self, value):
        if value is None:
            return None
        if not isinstance(value, dict) or set(value) - {"kind", "root_id", "path"} or not isinstance(value.get("kind"), str) or value["kind"] not in {"json", "log"}:
            fail("INVALID_PARAMS", "来源仅支持已授权文件夹中的 json 或 log 文件")
        source = {"kind": value["kind"], "root_id": text(value.get("root_id", "workspace"), "root_id"), "path": text(value.get("path"), "path", 2048)}
        file = capabilities.target(self.s, source["root_id"], source["path"])
        if not file.is_file():
            fail("INVALID_PARAMS", "观察来源必须是存在的文件")
        return source

    def create(self, title, goal="", conversation_id=None, source=None, request_id=None):
        text(title, "title", 512); text(goal, "goal", 16000, True)
        source = self._source(source)
        if request_id is not None:
            text(request_id, "request_id")
            old = self.s.db.execute("SELECT * FROM works WHERE request_id=?", (request_id,)).fetchone()
            if old:
                if old["title"] != title or old["goal"] != goal or (conversation_id and old["conversation_id"] != conversation_id) or json.loads(old["source_json"] or "null") != source:
                    fail("CONFLICT", "同一请求编号已有不同工作")
                return {"work": self._work(dict(old))}
        with self.s._transaction():
            conversation = self.s._conversation(conversation_id) if conversation_id else self.s.conversation_ensure(title=title)
            if conversation["archived"]:
                fail("CONFLICT", "请先恢复已归档会话")
            identity = "work_" + uuid.uuid4().hex
            now = self.s.clock()
            self.s.db.execute("INSERT INTO works VALUES(?,?,?,?,?,'watching',?,?,1,?,?,NULL)",
                              (identity, request_id, title, goal, conversation["conversation_id"], encoded(source) if source else None, encoded(DEFAULT_NOTIFICATIONS), now, now))
        return {"work": self._work(self._row("works", identity))}

    def by_conversation(self, conversation_id, include_archived=False):
        self.s._conversation(conversation_id)
        if type(include_archived) is not bool:
            fail("INVALID_PARAMS", "include_archived 必须为布尔值")
        rows = self.s.db.execute("SELECT * FROM works WHERE conversation_id=? AND (? OR intent!='archived') ORDER BY updated_at DESC,rowid DESC LIMIT 201", (conversation_id, include_archived)).fetchall()
        return {"works": [self._work(dict(row), compact=True) for row in rows[:200]], "has_more": len(rows) > 200}

    def _check_weixin_work_scope(self, conversation_id, weixin_scope):
        if weixin_scope is None:
            return
        if not isinstance(weixin_scope, dict) or set(weixin_scope) != {"installation_id", "revision", "command_id"}:
            fail("INVALID_PARAMS", "微信工作授权无效")
        text(weixin_scope["installation_id"], "installation_id", 64)
        text(weixin_scope["command_id"], "command_id", 200)
        state = self.s.channels._current(weixin_scope["revision"], weixin_scope["installation_id"], enabled=True)
        command = self.s._command(conversation_id, weixin_scope["command_id"])
        if (state["conversation_id"] != conversation_id or not self.s._settings().get("weixin_remote_control_enabled")
                or command["state"] not in {"admitted", "submitted", "applied"}):
            fail("CONFLICT", "微信工作授权已改变，请重新发起")

    def ensure_conversation(self, conversation_id, title, goal="", weixin_scope=None):
        """One model-created work projection per conversation; never merge legacy works."""
        text(title, "title", 512); text(goal, "goal", 16000, True)
        with self.s._transaction():
            self._check_weixin_work_scope(conversation_id, weixin_scope)
            conversation = self.s._conversation(conversation_id)
            if conversation["archived"]:
                fail("CONFLICT", "请先恢复已归档会话")
            rows = self.s.db.execute("SELECT * FROM works WHERE conversation_id=? AND intent!='archived' ORDER BY rowid LIMIT 2", (conversation_id,)).fetchall()
            if len(rows) > 1:
                fail("CONFLICT", "此会话关联了多项既有工作，请先明确要维护哪一项")
            if rows:
                return {"work": self._work(dict(rows[0])), "created": False}
            if self.s.db.execute("SELECT 1 FROM works WHERE conversation_id=? AND intent='archived' LIMIT 1", (conversation_id,)).fetchone():
                fail("CONFLICT", "此会话的工作已归档，请先恢复原工作")
            mode = self.s.db.execute("SELECT title_mode FROM conversations WHERE id=?", (conversation_id,)).fetchone()[0]
            changed = mode != "explicit" and conversation["title"] != title
            if changed:
                self.s.db.execute("UPDATE conversations SET title=?,title_mode='auto',metadata_version=metadata_version+1,metadata_updated_at=? WHERE id=?", (title, self.s.clock(), conversation_id))
                conversation = self.s._conversation(conversation_id)
            elif mode == "pending":
                # A later first-input autotitle must not override the work name.
                self.s.db.execute("UPDATE conversations SET title_mode='auto' WHERE id=?", (conversation_id,))
            if mode == "explicit":
                title = conversation["title"]
            result = self.create(title=title, goal=goal, conversation_id=conversation_id)
            return {**result, "created": True, "conversation": conversation, "conversation_title_changed": changed}

    def update_conversation(self, conversation_id, work_id, patch, weixin_scope=None):
        """Model may update descriptive fields only on a work in its own conversation."""
        if not isinstance(patch, dict) or not patch or set(patch) - {"title", "goal"}:
            fail("INVALID_PARAMS", "只能修改本会话工作的名称和目标")
        with self.s._transaction():
            self._check_weixin_work_scope(conversation_id, weixin_scope)
            conversation = self.s._conversation(conversation_id)
            work = self._row("works", work_id)
            if conversation["archived"] or work["conversation_id"] != conversation_id or work["intent"] == "archived":
                fail("CONFLICT", "工作不属于当前可用会话或已归档")
            return self.update(work_id=work_id, expected_revision=work["revision"], patch=patch)

    def list(self, include_archived=False, limit=100, offset=0):
        if type(include_archived) is not bool:
            fail("INVALID_PARAMS", "include_archived 必须为布尔值")
        integer(limit, "limit", 1, 200)
        integer(offset, "offset", 0, 2**31 - 1)
        rows = self.s.db.execute("SELECT * FROM works WHERE (? OR intent!='archived') ORDER BY updated_at DESC,rowid DESC LIMIT ? OFFSET ?", (include_archived, limit + 1, offset)).fetchall()
        page = rows[:limit]
        return {"works": [self._work(dict(r), compact=True) for r in page],
                "has_more": len(rows) > limit, "next_offset": offset + len(page)}

    def get(self, work_id, run_offset=0, run_limit=20):
        integer(run_offset, "run_offset", 0, 1000000); integer(run_limit, "run_limit", 1, 20)
        work = self._work(self._row("works", work_id))
        runs = self.s.db.execute("SELECT * FROM work_runs WHERE work_id=? ORDER BY created_at DESC,rowid DESC LIMIT ? OFFSET ?", (work_id, run_limit + 1, run_offset)).fetchall()
        return {"work": work, "runs": [self._run(dict(r)) for r in runs[:run_limit]], "has_more_runs": len(runs) > run_limit, "next_run_offset": run_offset + min(run_limit, len(runs))}

    def update(self, work_id, expected_revision, patch):
        work = self._row("works", work_id)
        integer(expected_revision, "expected_revision", 1)
        if work["revision"] != expected_revision:
            fail("CONFLICT", "工作已改变，请刷新后重试")
        if not isinstance(patch, dict) or not patch or set(patch) - {"title", "goal", "intent", "source", "notifications"}:
            fail("INVALID_PARAMS", "工作更新字段无效")
        if "title" in patch: work["title"] = text(patch["title"], "title")
        if "goal" in patch: work["goal"] = text(patch["goal"], "goal", 16000, True)
        if "intent" in patch:
            if not isinstance(patch["intent"], str) or patch["intent"] not in {"watching", "ended", "archived"}: fail("INVALID_PARAMS", "工作关注状态无效")
            if patch["intent"] != "watching" and self.s.db.execute("SELECT 1 FROM work_runs WHERE work_id=? AND status IN ('starting','running','cancelling')", (work_id,)).fetchone():
                fail("CONFLICT", "请先停止活动运行，再结束或归档工作")
            work["intent"] = patch["intent"]
        if "source" in patch:
            work["source_json"] = encoded(self._source(patch["source"])) if patch["source"] else None
            work["observation_json"] = None
        if "notifications" in patch:
            work["notifications_json"] = encoded({**json.loads(work["notifications_json"]), **switches(patch["notifications"], DEFAULT_NOTIFICATIONS)})
        with self.s._transaction():
            if self._row("works", work_id)["revision"] != expected_revision:
                fail("CONFLICT", "工作已改变，请刷新后重试")
            if "intent" in patch and patch["intent"] != "watching" and self.s.db.execute("SELECT 1 FROM work_runs WHERE work_id=? AND status IN ('starting','running','cancelling')", (work_id,)).fetchone():
                fail("CONFLICT", "请先停止活动运行，再结束或归档工作")
            self.s.db.execute("UPDATE works SET title=?,goal=?,intent=?,source_json=?,notifications_json=?,revision=revision+1,updated_at=?,observation_json=? WHERE id=?", (work["title"], work["goal"], work["intent"], work["source_json"], work["notifications_json"], self.s.clock(), work["observation_json"], work_id))
            self.s.db.execute("UPDATE work_approvals SET status='expired',token=NULL WHERE work_id=? AND status IN ('pending','approved')", (work_id,))
            if "source" in patch:
                for event in self.s.db.execute("SELECT event_id,deliveries_json FROM work_events WHERE work_id=?", (work_id,)).fetchall():
                    receipts = json.loads(event["deliveries_json"])
                    for channel in ("inbox", "weixin"):
                        receipt = receipts.get(channel)
                        if receipt is None: receipts[channel] = "suppressed"
                        elif isinstance(receipt, dict) and receipt.get("status") == "queued": receipts[channel] = {**receipt, "status": "suppressed"}
                    self.s.db.execute("UPDATE work_events SET deliveries_json=? WHERE event_id=?", (encoded(receipts), event["event_id"]))
            conversation = self.s._conversation(work["conversation_id"])
            title_changed = ("title" in patch and work["intent"] != "archived" and not conversation["archived"]
                and conversation["title"] != work["title"] and len(self.s.db.execute("SELECT id FROM works WHERE conversation_id=? LIMIT 2", (work["conversation_id"],)).fetchall()) == 1)
            if title_changed:
                conversation = self.s.conversation_patch(work["conversation_id"], conversation["metadata_version"], {"title": work["title"]})
        return {"work": self._work(self._row("works", work_id)), "conversation": conversation, "conversation_title_changed": title_changed}

    def observe(self, work_id):
        work = self._row("works", work_id)
        source = json.loads(work["source_json"]) if work["source_json"] else None
        now = self.s.clock()
        result = {"observed_at": now, "state": "unknown", "summary": "尚未关联观察来源", "source": source, "controllable": False}
        if source:
            try:
                file = capabilities.target(self.s, source["root_id"], source["path"])
                stat = file.stat()
                result.update(source_updated_at=int(stat.st_mtime_ns // 1000000), bytes=stat.st_size)
                with file.open("rb") as stream:
                    if source["kind"] == "log":
                        stream.seek(max(0, stat.st_size - 16000)); data = stream.read(16000)
                        result.update(state="observing", summary=data.decode("utf-8", errors="replace"), truncated=stat.st_size > len(data))
                    else:
                        raw = stream.read(65537)
                        if len(raw) > 65536: raise ValueError("状态 JSON 超过 64 KiB")
                        data = json.loads(raw.decode("utf-8-sig"))
                        if not isinstance(data, dict): raise ValueError("状态 JSON 必须是对象")
                        state = data.get("state", "observing")
                        if not isinstance(state, str) or state not in {"idle", "running", "completed", "failed", "blocked", "observing", "unknown"}:
                            raise ValueError("状态 state 必须是受支持的文字值")
                        result.update(state=state, summary=text(data.get("summary", ""), "summary", 4000, True), metrics={})
                        metrics = data.get("metrics", {})
                        if isinstance(metrics, dict):
                            for key, value in list(metrics.items())[:50]:
                                if isinstance(key, str) and len(key) <= 100 and (finite_number(value) or isinstance(value, str) and len(value) <= 500): result["metrics"][key] = value
                        if isinstance(data.get("progress"), dict):
                            p = data["progress"]
                            if all(finite_number(p.get(k)) for k in ("current", "total")) and 0 <= p["current"] <= p["total"] and p["total"] > 0:
                                result["progress"] = {"current": p["current"], "total": p["total"]}
                        result["artifacts"] = []
                        for ref in data.get("artifacts", [])[:30] if isinstance(data.get("artifacts"), list) else []:
                            if isinstance(ref, str) and len(ref) <= 2048:
                                try:
                                    artifact = capabilities.target(self.s, source["root_id"], ref)
                                    if artifact.is_file(): result["artifacts"].append({"root_id": source["root_id"], "path": ref, "name": artifact.name, "bytes": artifact.stat().st_size})
                                except (ValueError, OSError): pass
            except (ValueError, OSError, UnicodeError) as error:
                result.update(state="unknown", summary="无法读取来源：" + str(error)[:500])
        with self.s._transaction():
            self.s.db.execute("UPDATE works SET observation_json=? WHERE id=?", (encoded(result), work_id))
            previous = json.loads(work["observation_json"]) if work["observation_json"] else {}
            if source and result["state"] in {"completed", "failed"} and previous.get("state") != result["state"]:
                self._event(work_id, result["state"], work["title"], result["summary"] or ("来源状态：" + result["state"]))
        return {"work": self._work(self._row("works", work_id)), "observation": result}

    def _spec(self, script, arguments, cwd, output_paths, timeout_seconds):
        text(script, "script", 2048); text(cwd, "cwd", 2048)
        if Path(script).is_absolute() or Path(cwd).is_absolute(): fail("INVALID_PARAMS", "脚本和工作目录须为 Mirally 工作区相对路径")
        if not isinstance(arguments, list) or len(arguments) > 50: fail("INVALID_PARAMS", "脚本参数最多 50 项")
        for argument in arguments: text(argument, "argument", 4096, True)
        if sum(len(a) for a in arguments) > 16000: fail("INVALID_PARAMS", "脚本参数过长")
        if not isinstance(output_paths, list) or len(output_paths) > 30: fail("INVALID_PARAMS", "成果路径最多 30 项")
        integer(timeout_seconds, "timeout_seconds", 1, 86400)
        file = capabilities.target(self.s, "workspace", script, write=True)
        directory = capabilities.target(self.s, "workspace", cwd, write=True)
        if file.suffix.lower() != ".py" or not file.is_file() or not directory.is_dir(): fail("INVALID_PARAMS", "请选择存在的 Python 脚本和工作目录")
        for path in output_paths:
            text(path, "output_path", 2048)
            if Path(path).is_absolute(): fail("INVALID_PARAMS", "成果路径须为工作区相对路径")
            capabilities.target(self.s, "workspace", path, write=True)
        if sum(len(p) for p in output_paths) > 8192: fail("INVALID_PARAMS", "成果路径合计超过 8192 字符")
        with file.open("rb") as stream: content = stream.read(1024 * 1024 + 1)
        if len(content) > 1024 * 1024: fail("INVALID_PARAMS", "脚本超过 1 MiB")
        return {"script": script, "script_sha256": hashlib.sha256(content).hexdigest(), "arguments": arguments,
                "cwd": cwd, "output_paths": output_paths, "timeout_seconds": timeout_seconds,
                "capabilities_revision": capabilities.get(self.s)["revision"], "executor": "python", "sandboxed": False}

    def prepare(self, work_id, script, request_id, arguments=None, cwd=".", output_paths=None, timeout_seconds=300, actor="desktop"):
        text(request_id, "request_id"); text(actor, "actor")
        work = self._row("works", work_id)
        if work["intent"] != "watching": fail("CONFLICT", "请先恢复关注工作")
        spec = self._spec(script, [] if arguments is None else arguments, cwd, [] if output_paths is None else output_paths, timeout_seconds)
        digest = hashlib.sha256(encoded({"work_id": work_id, "work_revision": work["revision"], "actor": actor, "spec": spec}).encode()).hexdigest()
        old = self.s.db.execute("SELECT * FROM work_approvals WHERE request_id=?", (request_id,)).fetchone()
        if old:
            if old["digest"] != digest: fail("CONFLICT", "同一请求编号已有不同动作")
            return {"approval": self._approval(dict(old))}
        if self.s.db.execute("SELECT COUNT(*) FROM work_approvals WHERE work_id=? AND status IN ('pending','approved') AND expires_at>?", (work_id, self.s.clock())).fetchone()[0] >= 16:
            fail("APPROVAL_LIMIT", "此工作待确认动作过多，请等待已有审批过期或先处理")
        identity = "approval_" + uuid.uuid4().hex
        now = self.s.clock()
        with self.s._transaction():
            self.s.db.execute("INSERT INTO work_approvals(id,work_id,request_id,actor,work_revision,spec_json,digest,status,created_at,expires_at) VALUES(?,?,?,?,?,?,?,'pending',?,?)", (identity, work_id, request_id, actor, work["revision"], encoded(spec), digest, now, now + 300000))
            self._event(work_id, "approval", "等待确认：" + work["title"], "运行 Python 脚本 " + script + "；本机代码可产生脚本声明之外的影响，请审阅完整脚本后确认。")
        return {"approval": self._approval(self._row("work_approvals", identity))}

    def _valid_approval(self, approval_id, actor):
        text(actor, "actor")
        row = self._row("work_approvals", approval_id)
        if row["status"] == "approved" and row["approved_actor"] != actor:
            fail("FORBIDDEN", "此动作已由另一入口确认，请在原确认入口继续")
        if row["status"] in {"expired", "consumed"} or row["expires_at"] <= self.s.clock(): fail("APPROVAL_EXPIRED", "审批已失效，请重新准备")
        work = self._row("works", row["work_id"])
        if work["revision"] != row["work_revision"] or work["intent"] != "watching": fail("APPROVAL_EXPIRED", "工作已改变，请重新准备")
        spec = json.loads(row["spec_json"])
        latest = self._spec(spec["script"], spec["arguments"], spec["cwd"], spec["output_paths"], spec["timeout_seconds"])
        if latest != spec: fail("APPROVAL_EXPIRED", "脚本、目录授权或参数已改变，请重新准备")
        return row

    def approve(self, approval_id, expected_digest, actor="desktop"):
        row = self._valid_approval(approval_id, actor)
        if not isinstance(expected_digest, str) or not hmac.compare_digest(row["digest"], expected_digest): fail("CONFLICT", "确认内容与动作不一致")
        token = row["token"] or uuid.uuid4().hex + uuid.uuid4().hex
        with self.s._transaction():
            self.s.db.execute("UPDATE work_approvals SET status='approved',token=?,approved_actor=? WHERE id=?", (token, actor, approval_id))
        return {"approval": self._approval(self._row("work_approvals", approval_id)), "approval_token": token}

    def start(self, approval_id, approval_token, request_id, actor="desktop"):
        text(request_id, "request_id"); text(approval_token, "approval_token")
        old = self.s.db.execute("SELECT * FROM work_runs WHERE request_id=?", (request_id,)).fetchone()
        if old:
            approval = self._row("work_approvals", old["approval_id"])
            if approval["approved_actor"] != actor or old["approval_id"] != approval_id or not hmac.compare_digest(approval["token"] or "", approval_token): fail("CONFLICT", "同一请求编号不匹配原审批")
            return {"run": self._run(dict(old))}
        if self.closing: fail("CLOSED", "Mirally 正在退出")
        if len(self.active) >= 4: fail("RUN_LIMIT", "同时最多运行 4 项后台脚本，请稍后重试")
        row = self._valid_approval(approval_id, actor)
        if row["status"] != "approved" or not hmac.compare_digest(row["token"] or "", approval_token): fail("FORBIDDEN", "需要本人明确确认此动作")
        if self.s.db.execute("SELECT 1 FROM work_runs WHERE work_id=? AND status IN ('starting','running','cancelling')", (row["work_id"],)).fetchone(): fail("CONFLICT", "此工作已有活动运行")
        spec = json.loads(row["spec_json"])
        # Read and validate the exact bytes which will be sent to the child.
        # The interpreter never opens the original path to execute the script.
        script_path = capabilities.target(self.s, "workspace", spec["script"], write=True)
        with script_path.open("rb") as stream: script_bytes = stream.read(1024 * 1024 + 1)
        if len(script_bytes) > 1024 * 1024 or hashlib.sha256(script_bytes).hexdigest() != spec["script_sha256"]:
            fail("APPROVAL_EXPIRED", "脚本在启动前已改变，请重新准备")
        identity = "run_" + uuid.uuid4().hex
        now = self.s.clock()
        with self.s._transaction():
            self.s.db.execute("INSERT INTO work_runs(id,work_id,approval_id,request_id,status,created_at,spec_json) VALUES(?,?,?,?,'starting',?,?)", (identity, row["work_id"], approval_id, request_id, now, row["spec_json"]))
            self.s.db.execute("UPDATE work_approvals SET status='consumed' WHERE id=?", (approval_id,))
        try:
            process, job = launch([sys.executable, "-u", "-c", PYTHON_BOOTSTRAP, str(script_path), *spec["arguments"]],
                                  capabilities.target(self.s, "workspace", spec["cwd"], write=True), script_bytes=script_bytes)
            item = {"process": process, "job": job, "cancel": threading.Event()}
            self.active[identity] = item
            with self.s._transaction():
                self.s.db.execute("UPDATE work_runs SET status='running',pid=?,started_at=? WHERE id=?", (process.pid, self.s.clock(), identity))
                self.s.db.execute("UPDATE works SET updated_at=? WHERE id=?", (self.s.clock(), row["work_id"]))
            thread = threading.Thread(target=self._supervise, args=(identity, item, spec), daemon=True, name="mirally-work-" + identity[-8:])
            item["thread"] = thread
            thread.start()
        except Exception as error:
            item = self.active.pop(identity, None)
            if item:
                stop_tree(item["process"], item["job"])
                item["process"].wait(timeout=5)
                if item["process"].stdout: item["process"].stdout.close()
            with self.s._transaction():
                self.s.db.execute("UPDATE work_runs SET status='failed',finished_at=?,error=? WHERE id=?", (self.s.clock(), str(error)[:500], identity))
                self._event(row["work_id"], "failed", "启动失败", str(error)[:500])
        return self.run_get(identity)

    def run_get(self, run_id):
        return {"run": self._run(self._row("work_runs", run_id))}

    def cancel(self, run_id):
        row = self._row("work_runs", run_id)
        item = self.active.get(run_id)
        if row["status"] in ACTIVE:
            if not item: fail("UNKNOWN_RUN", "此运行未由当前宿主管理，请核对已有结果")
            item["cancel"].set()
            with self.s._transaction(): self.s.db.execute("UPDATE work_runs SET status='cancelling' WHERE id=?", (run_id,))
        return self.run_get(run_id)

    def logs(self, run_id, offset=0, limit=16000):
        row = self._row("work_runs", run_id)
        integer(offset, "offset", 0, LOG_LIMIT); integer(limit, "limit", 1, 65536)
        file = self.log_home / (run_id + ".log")
        data = b""
        if file.is_file():
            with file.open("rb") as stream: stream.seek(offset); data = stream.read(limit)
        return {"text": data.decode("utf-8", errors="replace"), "next_offset": offset + len(data), "truncated": bool(row["log_truncated"]), "run": self._run(row)}

    def artifact(self, run_id, path):
        row = self._row("work_runs", run_id)
        text(path, "path", 2048)
        ref = next((a for a in json.loads(row["artifacts_json"]) if a["path"] == path), None)
        if not ref: fail("FORBIDDEN", "该文件不是此运行已登记的成果")
        file = capabilities.target(self.s, "workspace", path)
        if not file.is_file() or file.stat().st_size > 2 * 1024 * 1024: fail("ARTIFACT_LIMIT", "手机成果下载上限为 2 MiB，请在电脑查看较大文件")
        with file.open("rb") as stream: data = stream.read(2 * 1024 * 1024 + 1)
        if len(data) > 2 * 1024 * 1024: fail("ARTIFACT_LIMIT", "成果超过 2 MiB")
        if not ref.get("sha256") or hashlib.sha256(data).hexdigest() != ref["sha256"]: fail("CONFLICT", "成果已改变，请在电脑核对")
        return {"name": file.name, "mime_type": mimetypes.guess_type(file.name)[0] or "application/octet-stream", "encoding": "base64", "data": base64.b64encode(data).decode("ascii"), "bytes": len(data)}

    def _supervise(self, identity, item, spec):
        process = item["process"]
        count = 0
        truncated = False
        def drain():
            nonlocal count, truncated
            with (self.log_home / (identity + ".log")).open("wb") as stream:
                while True:
                    data = process.stdout.read1(4096)
                    if not data: break
                    keep = data[:max(0, LOG_LIMIT-count)]
                    if len(keep) < len(data): truncated = True
                    stream.write(keep); stream.flush(); count += len(keep)
                    with self.s._transaction(): self.s.db.execute("UPDATE work_runs SET log_bytes=?,log_truncated=? WHERE id=?", (count, truncated, identity))
        reader = threading.Thread(target=drain, daemon=True)
        reader.start()
        deadline = time.monotonic() + spec["timeout_seconds"]
        reason = None
        while process.poll() is None:
            if item["cancel"].wait(.05): reason = "cancelled"; break
            if time.monotonic() >= deadline: reason = "timed_out"; break
        stop_tree(process, item["job"])
        try: process.wait(timeout=5)
        except subprocess.TimeoutExpired: pass
        reader.join(timeout=5)
        process.stdout.close()
        artifacts = []
        for path in spec["output_paths"]:
            try:
                file = capabilities.target(self.s, "workspace", path)
                if file.is_file():
                    info = file.stat()
                    digest = None
                    if info.st_size <= 2 * 1024 * 1024:
                        with file.open("rb") as stream: data = stream.read(2 * 1024 * 1024 + 1)
                        if len(data) <= 2 * 1024 * 1024: digest = hashlib.sha256(data).hexdigest()
                    artifacts.append({"root_id": "workspace", "path": path, "name": file.name, "bytes": info.st_size, "updated_at": info.st_mtime_ns // 1000000, "sha256": digest})
            except (ValueError, OSError): pass
        with self.s._transaction():
            row = self._row("work_runs", identity)
            status = reason or ("completed" if process.returncode == 0 else "failed")
            self.s.db.execute("UPDATE work_runs SET status=?,finished_at=?,exit_code=?,log_bytes=?,log_truncated=?,artifacts_json=?,error=? WHERE id=?", (status, self.s.clock(), process.returncode, count, truncated, encoded(artifacts), "执行超时，已停止进程树" if reason == "timed_out" else None, identity))
            title = self._row("works", row["work_id"])["title"]
            self._event(row["work_id"], "completed" if status == "completed" else "failed", title, "运行 " + identity[-12:] + "：" + status + "。程序退出不等于目标已验收。")
            self.active.pop(identity, None)

    def _event(self, work_id, kind, title, body):
        self.s.db.execute("INSERT INTO work_events(event_id,work_id,kind,title,text,created_at) VALUES(?,?,?,?,?,?)", ("workevent_" + uuid.uuid4().hex, work_id, kind, title, body, self.s.clock()))
        self.s.db.execute("UPDATE works SET updated_at=? WHERE id=?", (self.s.clock(), work_id))

    def settings_get(self):
        row = self.s.db.execute("SELECT value_json FROM settings WHERE key='work' ").fetchone()
        value = json.loads(row[0]) if row else {"revision": 1, "notifications": DEFAULT_NOTIFICATIONS.copy(), "channels": {"inbox": True, "weixin": False}}
        return {"settings": value}

    def settings_update(self, expected_revision, patch):
        value = self.settings_get()["settings"]
        integer(expected_revision, "expected_revision", 1)
        if value["revision"] != expected_revision: fail("CONFLICT", "通知设置已改变")
        if not isinstance(patch, dict) or not patch or set(patch)-{"notifications", "channels"}: fail("INVALID_PARAMS", "通知设置字段无效")
        for key in patch:
            value[key].update(switches(patch[key], value[key]))
        value["revision"] += 1
        with self.s._transaction(): self.s.db.execute("INSERT OR REPLACE INTO settings VALUES('work',?)", (encoded(value),))
        return {"settings": value}

    def _event_view(self, row, settings=None, work_cache=None):
        event = dict(row)
        deliveries = json.loads(event.pop("deliveries_json"))
        settings = settings or self.settings_get()["settings"]
        cache = work_cache if work_cache is not None else {}
        work_id = event["work_id"]
        if work_id not in cache: cache[work_id] = self._work(self._row("works", work_id), compact=True)
        work = cache[work_id]
        allowed = (work["intent"] == "watching" and not (work.get("observation") or {}).get("withdrawn")
                   and settings["notifications"].get(event["kind"], False) and work["notifications"].get(event["kind"], False))
        return {**event, "deliveries": deliveries, "work": work,
                "eligible_channels": {channel: bool(allowed and settings["channels"][channel]) for channel in ("inbox", "weixin")}}

    def notifications(self, limit=100, channel="weixin", installation_id=None, revision=None):
        integer(limit, "limit", 1, 200)
        if not isinstance(channel, str) or channel not in {"inbox", "weixin"}: fail("INVALID_PARAMS", "通知渠道无效")
        if installation_id is not None:
            text(installation_id, "installation_id"); integer(revision, "revision", 0)
        settings = self.settings_get()["settings"]
        if not settings["channels"][channel]: return {"events": []}
        events = []
        work_cache = {}
        for row in self.s.db.execute("SELECT * FROM work_events ORDER BY created_at DESC,rowid DESC LIMIT 1000"):
            receipts = json.loads(row["deliveries_json"])
            receipt = receipts.get(channel)
            if receipt is not None:
                if not isinstance(receipt, dict) or receipt.get("status") != "queued": continue
                if installation_id is None or receipt.get("installation_id") != installation_id or receipt.get("revision") != revision: continue
            event = self._event_view(row, settings, work_cache)
            if (event["work"].get("observation") or {}).get("withdrawn"):
                self.notification_ack(event["event_id"], channel, "suppressed", installation_id=installation_id, revision=revision)
                continue
            if not event["eligible_channels"][channel]: continue
            events.append(event)
            if len(events) >= limit: break
        return {"events": events}

    def notification_get(self, event_id):
        return {"event": self._event_view(self._row("work_events", event_id))}

    def notification_claim(self, event_id, channel, installation_id, revision):
        if channel != "weixin": fail("INVALID_PARAMS", "只有微信外部投递需要目的地占用")
        text(installation_id, "installation_id"); integer(revision, "revision", 0)
        with self.s._transaction():
            row = self._row("work_events", event_id)
            event = self._event_view(row)
            receipts = event["deliveries"]
            existing = receipts.get(channel)
            if existing is not None or not event["eligible_channels"][channel]:
                return {"event": event, "should_enqueue": False}
            route = self.s.db.execute("SELECT installation_id,revision,enabled,bound FROM channel_weixin WHERE singleton=1").fetchone()
            if not route or route["installation_id"] != installation_id or route["revision"] != revision or not route["enabled"] or not route["bound"]:
                fail("CONFLICT", "微信绑定已改变，未占用通知")
            receipts[channel] = {"status": "queued", "installation_id": installation_id, "revision": revision}
            self.s.db.execute("UPDATE work_events SET deliveries_json=? WHERE event_id=?", (encoded(receipts), event_id))
        return {"event": {**event, "deliveries": receipts}, "should_enqueue": True}

    def notification_ack(self, event_id, channel, status, installation_id=None, revision=None):
        if not isinstance(channel, str) or channel not in {"inbox", "weixin"} or not isinstance(status, str) or status not in {"sent", "failed", "unknown", "suppressed"}: fail("INVALID_PARAMS", "通知回执无效")
        with self.s._transaction():
            row = self._row("work_events", event_id)
            deliveries = json.loads(row["deliveries_json"])
            existing = deliveries.get(channel)
            if isinstance(existing, dict) and existing.get("status") == "queued":
                if existing.get("installation_id") != installation_id or existing.get("revision") != revision:
                    fail("CONFLICT", "通知回执属于另一微信绑定")
                deliveries[channel] = {**existing, "status": status}
            elif existing is None: deliveries[channel] = status
            self.s.db.execute("UPDATE work_events SET deliveries_json=? WHERE event_id=?", (encoded(deliveries), event_id))
        return {"event": self._event_view(self._row("work_events", event_id))}

    def _selection_key(self, channel, route_id):
        if channel != "weixin": fail("INVALID_PARAMS", "未知工作选择入口")
        text(route_id, "route_id")
        return "work_selection_" + hashlib.sha256((channel + ":" + route_id).encode()).hexdigest()

    def selection_get(self, channel, route_id, revision):
        key = self._selection_key(channel, route_id)
        integer(revision, "revision", 0)
        row = self.s.db.execute("SELECT value_json FROM settings WHERE key=?", (key,)).fetchone()
        value = json.loads(row[0]) if row else None
        return {"work_id": value["work_id"] if value and value["revision"] == revision else None}

    def selection_set(self, channel, route_id, revision, work_id=None):
        key = self._selection_key(channel, route_id)
        integer(revision, "revision", 0)
        if work_id is not None: self._row("works", work_id)
        with self.s._transaction(): self.s.db.execute("INSERT OR REPLACE INTO settings VALUES(?,?)", (key, encoded({"work_id": work_id, "revision": revision})))
        return {"work_id": work_id}

    def close(self):
        self.closing = True
        items = list(self.active.values())
        for item in items: item["cancel"].set()
        for item in items:
            stop_tree(item["process"], item["job"])
        for item in items:
            if item.get("thread"): item["thread"].join(timeout=12)


def launch(command, cwd, *, script_bytes=None):
    """Assign suspended Windows processes before user code can spawn children."""
    flags = 0x08000000 | 0x00000004 if os.name == "nt" else 0
    job = WindowsJob() if os.name == "nt" else None
    process = None
    try:
        environment = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
        process = subprocess.Popen(command, cwd=str(cwd), stdin=subprocess.PIPE if script_bytes is not None else subprocess.DEVNULL, stdout=subprocess.PIPE, env=environment,
                                   stderr=subprocess.STDOUT, creationflags=flags, start_new_session=os.name != "nt")
        if job: job.assign_and_resume(process)
        if script_bytes is not None:
            process.stdin.write(struct.pack(">Q", len(script_bytes)) + script_bytes)
            process.stdin.close()
            process.stdin = None
        return process, job
    except BaseException:
        if process:
            process.kill(); process.wait(timeout=5)
            if process.stdin: process.stdin.close()
            if process.stdout: process.stdout.close()
        if job: job.close()
        raise


def stop_tree(process, job):
    if job:
        job.close()
    else:
        try: os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError: pass


class WindowsJob:
    def __init__(self):
        import ctypes
        from ctypes import wintypes as w
        self.c = ctypes
        self.lock = threading.Lock()
        self.k = ctypes.WinDLL("kernel32", use_last_error=True)
        class Basic(ctypes.Structure):
            _fields_ = [("process_time", ctypes.c_int64), ("job_time", ctypes.c_int64), ("flags", w.DWORD),
                        ("min_ws", ctypes.c_size_t), ("max_ws", ctypes.c_size_t), ("active", w.DWORD),
                        ("affinity", ctypes.c_size_t), ("priority", w.DWORD), ("scheduling", w.DWORD)]
        class IO(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint64) for name in ("read_op", "write_op", "other_op", "read_bytes", "write_bytes", "other_bytes")]
        class Extended(ctypes.Structure):
            _fields_ = [("basic", Basic), ("io", IO), ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t), ("peak_process", ctypes.c_size_t), ("peak_job", ctypes.c_size_t)]
        self.k.CreateJobObjectW.argtypes = [ctypes.c_void_p, w.LPCWSTR]; self.k.CreateJobObjectW.restype = w.HANDLE
        self.k.SetInformationJobObject.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD]
        self.k.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
        self.k.CloseHandle.argtypes = [w.HANDLE]
        self.handle = self.k.CreateJobObjectW(None, None)
        if not self.handle: raise ctypes.WinError(ctypes.get_last_error())
        info = Extended(); info.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.k.SetInformationJobObject(self.handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
            self.close(); raise ctypes.WinError(ctypes.get_last_error())

    def assign_and_resume(self, process):
        c = self.c
        from ctypes import wintypes as w
        if not self.k.AssignProcessToJobObject(self.handle, w.HANDLE(int(process._handle))): raise c.WinError(c.get_last_error())
        class ThreadEntry(c.Structure):
            _fields_ = [("size", w.DWORD), ("usage", w.DWORD), ("thread", w.DWORD), ("owner", w.DWORD), ("base_priority", w.LONG), ("delta_priority", w.LONG), ("flags", w.DWORD)]
        self.k.CreateToolhelp32Snapshot.argtypes = [w.DWORD, w.DWORD]; self.k.CreateToolhelp32Snapshot.restype = w.HANDLE
        self.k.Thread32First.argtypes = [w.HANDLE, c.POINTER(ThreadEntry)]; self.k.Thread32Next.argtypes = self.k.Thread32First.argtypes
        self.k.OpenThread.argtypes = [w.DWORD, w.BOOL, w.DWORD]; self.k.OpenThread.restype = w.HANDLE
        self.k.ResumeThread.argtypes = [w.HANDLE]; self.k.ResumeThread.restype = w.DWORD
        snapshot = self.k.CreateToolhelp32Snapshot(4, 0)
        if snapshot == c.c_void_p(-1).value: raise c.WinError(c.get_last_error())
        resumed = False
        try:
            entry = ThreadEntry(); entry.size = c.sizeof(entry)
            more = self.k.Thread32First(snapshot, c.byref(entry))
            while more:
                if entry.owner == process.pid:
                    thread = self.k.OpenThread(2, False, entry.thread)
                    if not thread: raise c.WinError(c.get_last_error())
                    try:
                        if self.k.ResumeThread(thread) == 0xFFFFFFFF: raise c.WinError(c.get_last_error())
                        resumed = True
                    finally: self.k.CloseHandle(thread)
                more = self.k.Thread32Next(snapshot, c.byref(entry))
        finally: self.k.CloseHandle(snapshot)
        if not resumed: raise RuntimeError("无法恢复已受监督的工作进程")

    def close(self):
        with self.lock:
            if self.handle:
                self.k.CloseHandle(self.handle)
                self.handle = None
