"""Durable product owners and explicit operations, without a second Agent loop.

Only references, command hashes and business state live here. DSH owns model
history, Activity owns observations and MemoryStore owns durable memories.
All timestamps on the wire are integer UTC milliseconds.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterator
from urllib.parse import urlsplit

from .context import ContextReader, memory_config
from .channels import CHANNEL_SCHEMA, WeixinChannels
from .calendar import CALENDAR_SCHEMA, CalendarOwner


SCHEMA_VERSION = 11
CONVERSATION_FORKS_SCHEMA = """CREATE TABLE IF NOT EXISTS conversation_forks (
    conversation_id TEXT PRIMARY KEY REFERENCES conversations(id),
    parent_conversation_id TEXT NOT NULL,parent_generation INTEGER NOT NULL,
    parent_session_id TEXT NOT NULL,boundary_seq INTEGER NOT NULL,request_hash TEXT NOT NULL,
    FOREIGN KEY(parent_conversation_id,parent_generation) REFERENCES generations(conversation_id,generation))"""
INITIATIVE_RUNS_SCHEMA = """CREATE TABLE initiative_runs (id TEXT PRIMARY KEY,lease_token TEXT NOT NULL,
    created_at INTEGER NOT NULL,expires_at INTEGER NOT NULL,status TEXT NOT NULL,sources_json TEXT NOT NULL)"""
DEFAULT_SETTINGS = {
    "activity_enabled": False, "background_enabled": False,
    "activity_paused": False, "activity_capture": {},
    "memory_semantics": {"max_calls_per_hour": 4, "max_tokens_per_day": 24000, "max_output_tokens": 1500},
    "general_initiative_enabled": False, "memory_enabled": True,
    "weixin_remote_control_enabled": False, "weixin_auto_approve_enabled": False, "weixin_typing_enabled": True,
    "action_approval_mode": "ask",
    "do_not_disturb": False, "memory_config_path": None, "activity_config_path": None, "extensions": [],
    "model_connections": {}, "model_catalogs": {}, "model_routes": {"chat": "default", "background": "default"},
    "model": {"provider": "openai-compatible", "base_url": "", "api_format": "openai-chat",
              "api_key": "", "model_id": "", "image_enabled": False, "reasoning_enabled": False,
              "reasoning_budget": 0, "reasoning_effort": "", "context_window": 128000,
              "max_tokens": 2048, "temperature": 0.7},
}
TERMINAL_COMMANDS = {"completed", "cancelled", "rejected"}


class FrameworkError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _text(value: Any, name: str, limit: int = 512, *, empty: bool = False) -> str:
    if not isinstance(value, str) or (not value.strip() and not empty) or len(value) > limit:
        raise FrameworkError("INVALID_PARAMS", f"{name} must be bounded text")
    return value


def _integer(value: Any, name: str, low: int = 0, high: int = 2**63 - 1) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise FrameworkError("INVALID_PARAMS", f"{name} must be an integer in range")
    return value


class FrameworkService:
    def __init__(self, root: Path | str, home: Path | str, *,
                 clock: Callable[[], int] | None = None, controller: Any = None) -> None:
        self.root = Path(root).resolve()
        self.home = Path(home).resolve()
        self.path = self.home / "framework" / "product.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.clock = clock or (lambda: time.time_ns() // 1_000_000)
        self.controller = controller
        self._controller_error: str | None = None
        self._lock = threading.RLock()
        self.db = sqlite3.connect(self.path, isolation_level=None, timeout=10, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA busy_timeout=10000")
        try:
            if self.db.execute("PRAGMA user_version").fetchone()[0] not in (0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, SCHEMA_VERSION):
                raise FrameworkError("SCHEMA_VERSION", "unsupported product database schema")
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA synchronous=FULL")
            self._initialize()
            self.reader = ContextReader(self.root, self.home)
            from .activity_experience import ActivityExperience
            self.experience = ActivityExperience(self)
            from .model_connections import ModelConnections
            self.model_connections = ModelConnections(self)
            self.channels = WeixinChannels(self)
            self.calendar = CalendarOwner(self)
            from .recaps import Recaps
            self.recaps = Recaps(self)
            with self._transaction():
                # A process died after reserving an external effect. Reconcile
                # the provider; never turn this back into pending automatically.
                self.db.execute("UPDATE deliveries SET status='unknown',updated_at=? WHERE status='sending'", (self.clock(),))
                self.db.execute("UPDATE calendar_deliveries SET status='unknown',updated_at=? WHERE status='sending'", (self.clock(),))
                self.db.execute("UPDATE commands SET state='unknown',updated_at=? WHERE state='submitted'", (self.clock(),))
            self._update_controller()
        except BaseException:
            self.db.close()
            raise

    def _initialize(self) -> None:
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, SCHEMA_VERSION):
            raise FrameworkError("SCHEMA_VERSION", "unsupported product database schema")
        if version == SCHEMA_VERSION:
            # Development builds of schema 9 before notes were added can be
            # opened without losing their local events.
            if 'notes' not in {row[1] for row in self.db.execute('PRAGMA table_info(calendar_events)')}:
                with self._transaction():
                    self.db.execute("ALTER TABLE calendar_events ADD COLUMN notes TEXT NOT NULL DEFAULT ''")
            return
        if version in (1, 2, 3, 4, 5, 6, 7, 8, 9, 10):
            with self._transaction():
                if version == 1:
                    self.db.execute(INITIATIVE_RUNS_SCHEMA)
                if version in (1, 2):
                    for column in ("pinned INTEGER NOT NULL DEFAULT 0", "archived INTEGER NOT NULL DEFAULT 0",
                                   "metadata_version INTEGER NOT NULL DEFAULT 1", "metadata_updated_at INTEGER NOT NULL DEFAULT 0",
                                   "pinned_at INTEGER", "archived_at INTEGER"):
                        self.db.execute("ALTER TABLE conversations ADD COLUMN " + column)
                    self.db.execute("UPDATE conversations SET metadata_updated_at=created_at")
                if version < 5:
                    for statement in CHANNEL_SCHEMA:
                        self.db.execute(statement)
                if version < 6:
                    self.db.execute("ALTER TABLE conversations ADD COLUMN title_mode TEXT NOT NULL DEFAULT 'explicit'")
                # Older migration fixtures may already contain newer columns.
                for table in ('conversations', 'commands'):
                    columns = {row[1] for row in self.db.execute(f'PRAGMA table_info({table})')}
                    if 'model_selection_json' not in columns:
                        self.db.execute(f'ALTER TABLE {table} ADD COLUMN model_selection_json TEXT')
                self.db.execute(CONVERSATION_FORKS_SCHEMA)
                for statement in CALENDAR_SCHEMA:
                    self.db.execute(statement)
                if 'notes' not in {row[1] for row in self.db.execute('PRAGMA table_info(calendar_events)')}:
                    self.db.execute("ALTER TABLE calendar_events ADD COLUMN notes TEXT NOT NULL DEFAULT ''")
                for statement in CHANNEL_SCHEMA:
                    self.db.execute(statement)
                self.db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            return
        with self._transaction():
            statements = (
                "CREATE TABLE settings (key TEXT PRIMARY KEY,value_json TEXT NOT NULL)",
                """CREATE TABLE conversations (id TEXT PRIMARY KEY,title TEXT NOT NULL,current_generation INTEGER NOT NULL,created_at INTEGER NOT NULL,
                    pinned INTEGER NOT NULL DEFAULT 0,archived INTEGER NOT NULL DEFAULT 0,metadata_version INTEGER NOT NULL DEFAULT 1,
                    metadata_updated_at INTEGER NOT NULL DEFAULT 0,pinned_at INTEGER,archived_at INTEGER,
                    title_mode TEXT NOT NULL DEFAULT 'explicit',model_selection_json TEXT)""",
                """CREATE TABLE generations (conversation_id TEXT NOT NULL REFERENCES conversations(id),generation INTEGER NOT NULL,
                    session_id TEXT UNIQUE,frozen INTEGER NOT NULL DEFAULT 0,reason TEXT NOT NULL,created_at INTEGER NOT NULL,
                    PRIMARY KEY(conversation_id,generation))""",
                """CREATE TABLE commands (conversation_id TEXT NOT NULL,command_id TEXT NOT NULL,generation INTEGER NOT NULL,
                    session_id TEXT NOT NULL,content_hash TEXT NOT NULL,state TEXT NOT NULL,runtime_request_id TEXT,
                    sources_json TEXT NOT NULL,created_at INTEGER NOT NULL,updated_at INTEGER NOT NULL,model_selection_json TEXT,
                    PRIMARY KEY(conversation_id,command_id),FOREIGN KEY(conversation_id,generation) REFERENCES generations(conversation_id,generation))""",
                "CREATE TABLE sources (source_id TEXT PRIMARY KEY,enabled INTEGER NOT NULL,version INTEGER NOT NULL,reason TEXT NOT NULL)",
                """CREATE TABLE source_uses (conversation_id TEXT NOT NULL,generation INTEGER NOT NULL,source_id TEXT NOT NULL,
                    version INTEGER NOT NULL,PRIMARY KEY(conversation_id,generation,source_id,version),
                    FOREIGN KEY(conversation_id,generation) REFERENCES generations(conversation_id,generation))""",
                "CREATE INDEX source_use_lookup ON source_uses(source_id,version)",
                """CREATE TABLE preferences (scope TEXT NOT NULL,key TEXT NOT NULL,value_json TEXT NOT NULL,
                    revision INTEGER NOT NULL,updated_at INTEGER NOT NULL,PRIMARY KEY(scope,key))""",
                """CREATE TABLE commitments (id TEXT PRIMARY KEY,request_id TEXT UNIQUE,title TEXT NOT NULL,body TEXT NOT NULL,
                    due_at INTEGER NOT NULL,expires_at INTEGER,conversation_id TEXT,channel TEXT NOT NULL,status TEXT NOT NULL,
                    sources_json TEXT NOT NULL,created_at INTEGER NOT NULL)""",
                "CREATE INDEX commitment_due ON commitments(status,due_at)",
                """CREATE TABLE initiatives (id TEXT PRIMARY KEY,dedupe_key TEXT UNIQUE,title TEXT NOT NULL,body TEXT NOT NULL,
                    reason TEXT NOT NULL,expires_at INTEGER NOT NULL,not_before INTEGER NOT NULL,conversation_id TEXT,
                    channel TEXT NOT NULL,status TEXT NOT NULL,sources_json TEXT NOT NULL,created_at INTEGER NOT NULL)""",
                """CREATE TABLE deliveries (id TEXT PRIMARY KEY,kind TEXT NOT NULL,reference_id TEXT NOT NULL,channel TEXT NOT NULL,
                    status TEXT NOT NULL,claim_token TEXT,receipt_json TEXT,sources_json TEXT NOT NULL,created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,UNIQUE(kind,reference_id))""",
                "CREATE TABLE inbox (delivery_id TEXT PRIMARY KEY REFERENCES deliveries(id),state TEXT NOT NULL,updated_at INTEGER NOT NULL)",
                INITIATIVE_RUNS_SCHEMA,
            )
            for statement in (*statements, *CHANNEL_SCHEMA, CONVERSATION_FORKS_SCHEMA, *CALENDAR_SCHEMA):
                self.db.execute(statement)
            self.db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        with self._lock:
            # Domain methods call each other; only the outer owner commits.
            nested = self.db.in_transaction
            if not nested:
                self.db.execute("BEGIN IMMEDIATE")
            try:
                yield
                if not nested:
                    self.db.execute("COMMIT")
            except BaseException:
                if not nested:
                    self.db.execute("ROLLBACK")
                raise

    def call(self, method: Any, params: Any = None) -> dict[str, Any]:
        if params is None:
            params = {}
        if not isinstance(method, str) or not isinstance(params, dict):
            raise FrameworkError("INVALID_REQUEST", "method must be text and params an object")
        routes = {
            **self.channels.routes(),
            **self.calendar.routes(),
            **self.recaps.routes(),
            "system.status": self.status, "settings.get": self.settings_get,
            "settings.update": self.settings_update, "internal.settings.get": self.settings_internal,
            "model.connections.list": self.model_connections.list, "model.connection.save": self.model_connections.save,
            "model.connection.remove": self.model_connections.remove, "model.routes.set": self.model_connections.set_routes,
            "model.catalog.save": self.model_connections.save_catalog, "model.catalog.remove": self.model_connections.remove_catalog,
            "conversation.ensure": self.conversation_ensure, "conversation.bind": self.conversation_bind,
            "conversation.rotate": self.conversation_rotate, "conversation.list": self.conversation_list,
            "conversation.patch": self.conversation_patch,
            "internal.conversation.autotitle": self.conversation_autotitle,
            "internal.conversation.weixin_receipts": self.conversation_weixin_receipts,
            "conversation.get": self.conversation_get,
            "internal.conversation.prepare_input": self.conversation_prepare_input,
            "internal.conversation.fork": self.conversation_fork,
            "internal.conversation.continuation": self.conversation_continuation,
            "internal.conversation.activity_batch": self.conversation_activity_batch,
            "internal.conversation.resolve": self.conversation_resolve,
            "conversation.history_refs": self.conversation_history_refs,
            "command.admit": self.command_admit, "command.ack": self.command_ack, "command.list": self.command_list,
            "interaction.stop": self.interaction_stop, "source.set": self.source_set, "source.list": self.source_list,
            "guard.check": self.guard_check, "context.read": self.context_read,
            "memory.search": self.memory_search, "memory.list": self.memory_search, "memory.read": self.memory_read,
            "memory.status": self.experience.memory_status, "memory.configure": self.experience.memory_configure,
            "activity.status": self.experience.status, "activity.configure": self.experience.configure,
            "activity.start": self.experience.start, "activity.pause": self.experience.pause,
            "activity.resume": self.experience.resume, "activity.stop": self.experience.stop,
            "activity.browse": self.experience.browse,
            "preference.set": self.preference_set, "preference.list": self.preference_list, "memory.correct": self.memory_correct,
            "commitment.create": self.commitment_create, "commitment.cancel": self.commitment_cancel, "commitment.list": self.commitment_list,
            "internal.job_notification.create": self.job_notification_create,
            "reminder.create": self.reminder_create,
            "initiative.offer": self.initiative_offer, "initiative.list": self.initiative_list, "initiative.dismiss": self.initiative_dismiss,
            "initiative.acquire": self.initiative_acquire, "initiative.complete": self.initiative_complete,
            "delivery.tick": self.delivery_tick, "delivery.list": self.delivery_list,
            "delivery.claim": self.delivery_claim, "delivery.ack": self.delivery_ack,
            "inbox.list": self.inbox_list, "inbox.ack": self.inbox_ack,
            "capabilities.get": self.capabilities_get, "capabilities.configure": self.capabilities_configure,
            "workspace.roots": self.workspace_roots, "workspace.list": self.workspace_list,
            "workspace.read": self.workspace_read, "workspace.search": self.workspace_search,
            "internal.workspace.write": self.workspace_write,
        }
        if method not in routes:
            raise FrameworkError("METHOD_NOT_FOUND", "unknown product method")
        with self._lock:
            try:
                return routes[method](**params)
            except FrameworkError:
                raise
            except TypeError as error:
                raise FrameworkError("INVALID_PARAMS", "unexpected or missing parameters") from error
            except ValueError as error:
                raise FrameworkError("INVALID_PARAMS", str(error)[:1000]) from error
            except sqlite3.IntegrityError as error:
                raise FrameworkError("CONFLICT", "product identity or binding conflict") from error

    def capabilities_get(self):
        from .capabilities import get
        return get(self)

    def capabilities_configure(self, expected_revision, patch):
        from .capabilities import configure
        return configure(self, expected_revision, patch)

    def workspace_roots(self):
        from .capabilities import roots
        return {"roots":roots(self)}

    def workspace_list(self, root_id="workspace", path="."):
        from .capabilities import list_files
        return list_files(self,root_id,path)

    def workspace_read(self, root_id="workspace", path=""):
        from .capabilities import read_file
        return read_file(self,root_id,path)

    def workspace_search(self, query, root_id="workspace", path="."):
        from .capabilities import search_files
        return search_files(self,query,root_id,path)

    def workspace_write(self, path, content, expected_sha256=None, conversation_id=None, weixin_scope=None):
        from .capabilities import write_file
        if weixin_scope is None:
            return write_file(self,path,content,expected_sha256)
        with self._transaction():
            self.channels.check_conversation_scope(conversation_id, weixin_scope)
            return write_file(self,path,content,expected_sha256)

    def status(self) -> dict[str, Any]:
        workers = self._controller_status()
        observed = workers.get("status") if isinstance(workers.get("status"), dict) else workers
        return {"schema_version": SCHEMA_VERSION, "ready": True, "home": str(self.home),
                "database": str(self.path), "collector_started": isinstance(observed.get("activity"), dict) and observed["activity"].get("state") == "running",
                "background_model_started": isinstance(observed.get("semantic"), dict) and observed["semantic"].get("state") == "running",
                "scheduler": "host_tick", "model_bridge": "host_managed", "transcript_owner": "dsh",
                "now_utc_ms": self.clock(), "workers": workers}

    def _controller_status(self) -> dict[str, Any]:
        if self.controller is None:
            return {"enabled": False, "status": "not_started"}
        try:
            return {"enabled": True, "status": self.controller.status(), "error": self._controller_error}
        except Exception as error:
            return {"enabled": True, "error": type(error).__name__}

    def _update_controller(self) -> None:
        if self.controller is not None:
            try:
                settings = self._settings()
                settings["_framework_revoked_sources"] = [row[0] for row in self.db.execute("SELECT source_id FROM sources WHERE enabled=0")]
                for kind in ("activity", "memory"):
                    settings[kind + "_enabled"] &= self._source(kind)["enabled"]
                self.controller.update(settings)
                self._controller_error = None
            except Exception as error:
                # Persist the user's chosen setting, but report failure. A flag
                # is never evidence that a collector actually started.
                self._controller_error = type(error).__name__

    def _settings(self) -> dict[str, Any]:
        value = copy.deepcopy(DEFAULT_SETTINGS)
        for row in self.db.execute("SELECT key,value_json FROM settings"):
            saved = json.loads(row["value_json"])
            if row["key"] in {"model", "activity_capture", "memory_semantics", "model_routes"}:
                value[row["key"]].update(saved)
            else:
                value[row["key"]] = saved
        return value

    @staticmethod
    def _public_settings(value: dict[str, Any]) -> dict[str, Any]:
        result = copy.deepcopy(value)
        key = result["model"].pop("api_key", "")
        result["model"]["api_key_configured"] = bool(key)
        for connection in result.get("model_connections", {}).values():
            model = connection["model"]
            model["api_key_configured"] = bool(model.pop("api_key", ""))
        def redact(item: Any) -> Any:
            if isinstance(item, list):
                return [redact(child) for child in item]
            if not isinstance(item, dict):
                return item
            output = {}
            for name, child in item.items():
                normalized = str(name).replace("_", "").replace("-", "").lower()
                if normalized in {"env", "headers"} and isinstance(child, dict):
                    output[name] = {field: "<configured>" if content else "" for field, content in child.items()}
                elif any(part in normalized for part in ("apikey", "token", "password", "secret", "authorization", "credential")):
                    output[name] = "<configured>" if child else ""
                else:
                    output[name] = redact(child)
            return output
        result["extensions"] = redact(result["extensions"])
        return result

    def settings_get(self) -> dict[str, Any]:
        return self._public_settings(self._settings())

    def settings_internal(self) -> dict[str, Any]:
        """Private stdio only. HTTP must never forward internal.* methods."""
        return self._settings()

    def _model_update(self, current: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
        updated = copy.deepcopy(current)
        if not isinstance(item, dict) or set(item) - set(DEFAULT_SETTINGS["model"]):
            raise FrameworkError("INVALID_PARAMS", "unsupported model settings field")
        for field, setting in item.items():
            default = DEFAULT_SETTINGS["model"][field]
            if isinstance(default, bool):
                if not isinstance(setting, bool):
                    raise FrameworkError("INVALID_PARAMS", f"model.{field} must be boolean")
            elif field == "context_window":
                _integer(setting, "model.context_window", 128, 10000000)
            elif field in {"max_tokens", "reasoning_budget"}:
                _integer(setting, f"model.{field}", 128 if field == "max_tokens" else 0, 262144)
            elif field == "temperature":
                if isinstance(setting, bool) or not isinstance(setting, (int, float)) or not math.isfinite(setting) or not 0 <= setting <= 2:
                    raise FrameworkError("INVALID_PARAMS", "model.temperature must be between zero and two")
            else:
                _text(setting, f"model.{field}", 16384 if field == "api_key" else 2048, empty=field in {"api_key", "base_url", "model_id", "reasoning_effort"})
            if field == "base_url" and setting:
                parsed = urlsplit(setting)
                if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
                    raise FrameworkError("INVALID_PARAMS", "model.base_url must be an HTTP(S) URL without credentials")
            if field == "api_format" and setting not in {"openai-chat", "openai-completions", "openai-responses", "anthropic", "anthropic-messages"}:
                raise FrameworkError("INVALID_PARAMS", "unsupported model API format")
            if field == "reasoning_effort" and setting not in {"", "default", "off", "low", "medium", "high", "max"}:
                raise FrameworkError("INVALID_PARAMS", "unsupported model reasoning effort")
            updated[field] = setting
        return updated

    def settings_update(self, patch: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(patch, dict) or set(patch) - set(DEFAULT_SETTINGS) or set(patch) & {"model_connections", "model_catalogs", "model_routes"}:
            raise FrameworkError("INVALID_PARAMS", "unsupported settings field")
        value = self._settings()
        previous = copy.deepcopy(value)
        for key, item in patch.items():
            if key == "model":
                value["model"] = self._model_update(value["model"], item)
            elif key == "action_approval_mode":
                if item not in ("ask", "trusted") or not isinstance(item, str):
                    raise FrameworkError("INVALID_PARAMS", "action_approval_mode must be ask or trusted")
                value[key] = item
            elif key == "activity_capture":
                from .activity_experience import validate_capture_patch
                try:
                    value[key].update(validate_capture_patch(item))
                except ValueError as error:
                    raise FrameworkError("INVALID_PARAMS", str(error)) from error
            elif key == "memory_semantics":
                if not isinstance(item, dict) or set(item) - set(DEFAULT_SETTINGS[key]):
                    raise FrameworkError("INVALID_PARAMS", "unsupported semantic budget field")
                for field, setting in item.items():
                    _integer(setting, field, 0 if field != "max_output_tokens" else 128,
                             {"max_calls_per_hour": 1000, "max_tokens_per_day": 10000000, "max_output_tokens": 262144}[field])
                value[key].update(item)
            elif key == "extensions":
                if not isinstance(item, list) or len(item) > 100 or any(not isinstance(entry, dict) for entry in item):
                    raise FrameworkError("INVALID_PARAMS", "extensions must be at most 100 configuration objects")
                if len(_json(item).encode("utf-8")) > 262144:
                    raise FrameworkError("INVALID_PARAMS", "extensions configuration exceeds size limit")
                for entry in item:
                    if "enabled" in entry and not isinstance(entry["enabled"], bool):
                        raise FrameworkError("INVALID_PARAMS", "extension.enabled must be boolean")
                    for name in ("id", "path", "kind", "version"):
                        if name in entry:
                            _text(entry[name], f"extension.{name}", 4096 if name == "path" else 512)
                value[key] = item
            elif key.endswith("_config_path"):
                if item is not None:
                    _text(item, key, 4096)
                value[key] = item
            else:
                if not isinstance(item, bool):
                    raise FrameworkError("INVALID_PARAMS", f"{key} must be boolean")
                value[key] = item
        with self._transaction():
            if patch.get('weixin_remote_control_enabled') is True:
                route = self.db.execute("SELECT bound FROM channel_weixin WHERE singleton=1").fetchone()
                if not route or not route['bound']:
                    raise FrameworkError('CHANNEL_UNCONFIGURED', '请先绑定微信，再启用远程控制。')
            if 'model' in patch and value['model'] != previous['model']:
                self.model_connections._assert_connection_inactive('default')
            if 'model' in patch and value['model']['model_id'] != previous['model']['model_id']:
                if any(entry['model_id'] == value['model']['model_id'] for entry in value['model_catalogs'].get('default', [])):
                    raise FrameworkError('INVALID_PARAMS', '该模型已在模型列表中，请先从列表移除。')
                if previous['model']['model_id']:
                    self.model_connections._assert_model_unused('default', previous['model']['model_id'])
            for key in patch:
                self.db.execute("INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json", (key, _json(value[key])))
            for kind in ("memory", "activity"):
                fields = [kind + "_enabled", kind + "_config_path"]
                if kind == "activity": fields.extend(["activity_capture", "activity_paused"])
                if any(value[key] != previous[key] for key in fields):
                    self._invalidate(kind, self._source(kind)["enabled"], "source_policy_changed", force=True)
        self._update_controller()
        result = self._public_settings(value)
        if self.controller is not None:
            result["workers"] = self._controller_status()
        return result

    def _conversation(self, conversation_id: str) -> dict[str, Any]:
        row = self.db.execute("""SELECT c.id AS conversation_id,c.title,c.created_at,c.pinned,c.archived,
            c.metadata_version,c.metadata_updated_at,c.pinned_at,c.archived_at,c.model_selection_json,
            COALESCE((SELECT MAX(updated_at) FROM commands WHERE conversation_id=c.id),c.created_at) AS updated_at,
            c.current_generation AS generation,g.session_id,g.frozen
            FROM conversations c JOIN generations g ON g.conversation_id=c.id AND g.generation=c.current_generation WHERE c.id=?""", (conversation_id,)).fetchone()
        if row is None:
            raise FrameworkError("NOT_FOUND", "conversation not found")
        result = dict(row)
        selection = result.pop('model_selection_json')
        result['model_selection'] = json.loads(selection) if selection else None
        result['recap'] = self.recaps.get(conversation_id) if hasattr(self, 'recaps') else None
        result["frozen"] = bool(result["frozen"])
        result["pinned"], result["archived"] = bool(result["pinned"]), bool(result["archived"])
        result["needs_session"] = result["session_id"] is None or result["frozen"]
        fork = self.db.execute('SELECT * FROM conversation_forks WHERE conversation_id=?', (conversation_id,)).fetchone()
        if fork:
            result['fork'] = dict(fork)
        return result

    def _validate_model_selection(self, selection: Any) -> None:
        if selection is None:
            return
        if not isinstance(selection, dict) or set(selection) != {'connection_id', 'model_id'}:
            raise FrameworkError('INVALID_PARAMS', 'invalid model selection')
        connection_id = _text(selection['connection_id'], 'connection_id', 64)
        _text(selection['model_id'], 'model_id', 2048)
        settings = self._settings()
        model = settings['model'] if connection_id == 'default' else settings['model_connections'].get(connection_id, {}).get('model')
        if model is None:
            raise FrameworkError('NOT_FOUND', '模型连接不存在，请刷新后重试。')
        if not model.get('base_url'):
            raise FrameworkError('INVALID_PARAMS', '请先配置模型服务地址。')
        available = {entry['model_id'] for entry in settings['model_catalogs'].get(connection_id, [])}
        available.add(model.get('model_id'))
        if selection['model_id'] not in available:
            raise FrameworkError('INVALID_PARAMS', '模型不在该连接的模型列表中，请刷新后重试。')

    def conversation_ensure(self, conversation_id: str | None = None, title: str | None = None, model_selection: dict[str, str] | None = None) -> dict[str, Any]:
        identity = _text(conversation_id, "conversation_id") if conversation_id is not None else "conversation_" + uuid.uuid4().hex
        if title is not None:
            _text(title, "title", 512)
        with self._transaction():
            if not self.db.execute("SELECT 1 FROM conversations WHERE id=?", (identity,)).fetchone():
                self._validate_model_selection(model_selection)
                now = self.clock()
                placeholder = "新对话-" + datetime.fromtimestamp(now / 1000).strftime("%Y-%m-%d")
                self.db.execute("INSERT INTO conversations(id,title,current_generation,created_at,metadata_updated_at,title_mode,model_selection_json) VALUES(?,?,1,?,?,?,?)", (identity, title if title is not None else placeholder, now, now, 'explicit' if title is not None else 'pending', _json(model_selection) if model_selection is not None else None))
                self.db.execute("INSERT INTO generations VALUES(?,1,NULL,0,'initial',?)", (identity, self.clock()))
        return self._conversation(identity)

    def conversation_get(self, conversation_id: str) -> dict[str, Any]:
        _text(conversation_id, "conversation_id")
        return self._conversation(conversation_id)

    def conversation_fork(self, conversation_id: str, parent_conversation_id: str, parent_generation: int,
                          parent_session_id: str, boundary_seq: int, request_hash: str,
                          title: str | None = None) -> dict[str, Any]:
        """Reserve a native prefix and its source dependencies, never a second transcript."""
        _text(conversation_id, 'conversation_id')
        _text(parent_conversation_id, 'parent_conversation_id')
        _text(parent_session_id, 'parent_session_id')
        _text(request_hash, 'request_hash', 64)
        _integer(parent_generation, 'parent_generation', 1)
        _integer(boundary_seq, 'boundary_seq', 0)
        if title is not None:
            _text(title, 'title', 512)
        with self._transaction():
            old = self.db.execute('SELECT * FROM conversation_forks WHERE conversation_id=?', (conversation_id,)).fetchone()
            if old:
                if old['request_hash'] != request_hash or old['parent_conversation_id'] != parent_conversation_id:
                    raise FrameworkError('CONFLICT', 'fork identity already refers to a different request')
                target = self._conversation(conversation_id)
                if target['frozen'] or target['archived']:
                    raise FrameworkError('CONFLICT', 'fork is no longer active')
                return target
            parent = self._conversation(parent_conversation_id)
            refs = [dict(row) for row in self.db.execute('SELECT source_id,version FROM source_uses WHERE conversation_id=? AND generation=?',
                                                        (parent_conversation_id, parent_generation))]
            if parent['frozen'] or parent['archived'] or parent['generation'] != parent_generation or parent['session_id'] != parent_session_id or self._source_reasons(refs):
                raise FrameworkError('CONFLICT', 'parent session or its sources changed before fork')
            if self.db.execute('SELECT 1 FROM conversations WHERE id=?', (conversation_id,)).fetchone():
                raise FrameworkError('CONFLICT', 'fork target already exists')
            self.conversation_ensure(conversation_id=conversation_id, title=title or (parent['title'][:500] + ' · 分叉'), model_selection=parent['model_selection'])
            self.db.execute('INSERT INTO conversation_forks VALUES(?,?,?,?,?,?)',
                            (conversation_id, parent_conversation_id, parent_generation, parent_session_id, boundary_seq, request_hash))
            self._record_uses(conversation_id, 1, refs)
        return self._conversation(conversation_id)

    def conversation_activity_batch(self, conversation_ids: list[str]) -> dict[str, Any]:
        if not isinstance(conversation_ids, list) or len(conversation_ids) > 200:
            raise FrameworkError("INVALID_PARAMS", "conversation_ids must be a list of at most 200 identities")
        ids = list(dict.fromkeys(_text(identity, "conversation_id") for identity in conversation_ids))
        if not ids:
            return {"conversations": []}
        placeholders = ",".join("?" for _ in ids)
        rows = self.db.execute(f"""SELECT c.id AS conversation_id,g.session_id,g.frozen,c.archived
            FROM conversations c JOIN generations g ON g.conversation_id=c.id AND g.generation=c.current_generation
            WHERE c.id IN ({placeholders})""", ids).fetchall()
        return {"conversations": [{**dict(row), "frozen": bool(row["frozen"]),
                                  "archived": bool(row["archived"])} for row in rows]}

    def conversation_bind(self, conversation_id: str, session_id: str, generation: int | None = None) -> dict[str, Any]:
        _text(session_id, "session_id")
        with self._transaction():
            current = self._conversation(conversation_id)
            if current["frozen"] or generation is not None and generation != current["generation"]:
                raise FrameworkError("CONFLICT", "cannot bind a frozen or stale conversation generation")
            if current["session_id"] not in (None, session_id):
                raise FrameworkError("CONFLICT", "generation already has a different session")
            self.db.execute("UPDATE generations SET session_id=? WHERE conversation_id=? AND generation=?", (session_id, conversation_id, current["generation"]))
        return self._conversation(conversation_id)

    def conversation_rotate(self, conversation_id: str, reason: str, session_id: str | None = None) -> dict[str, Any]:
        _text(reason, "reason", 2048)
        if session_id is not None:
            _text(session_id, "session_id")
        with self._transaction():
            current = self._conversation(conversation_id)
            self._freeze(conversation_id, current["generation"], reason)
            generation = current["generation"] + 1
            self.db.execute("INSERT INTO generations VALUES(?,?,?,0,?,?)", (conversation_id, generation, session_id, reason, self.clock()))
            self.db.execute("UPDATE conversations SET current_generation=? WHERE id=?", (generation, conversation_id))
        return self._conversation(conversation_id)

    def conversation_prepare_input(self, conversation_id: str) -> dict[str, Any]:
        """Keep native transcripts across ordinary stops, including legacy ones.

        Older hosts froze every user stop. Reuse that native session only when
        all recorded sources are still current; privacy/configuration fences
        continue to require a fresh generation.
        """
        with self._transaction():
            current = self._conversation(conversation_id)
            if current['archived']:
                raise FrameworkError('CONFLICT', 'restore the archived conversation before sending')
            if not current['frozen']:
                return current
            reason = self.db.execute('SELECT reason FROM generations WHERE conversation_id=? AND generation=?',
                                     (conversation_id, current['generation'])).fetchone()[0]
            refs = [dict(row) for row in self.db.execute('SELECT source_id,version FROM source_uses WHERE conversation_id=? AND generation=?',
                                                        (conversation_id, current['generation']))]
            if reason == 'user_stopped' and not self._source_reasons(refs):
                self.db.execute('UPDATE generations SET frozen=0,reason=? WHERE conversation_id=? AND generation=?',
                                ('user_resumed', conversation_id, current['generation']))
                return self._conversation(conversation_id)
            return self.conversation_rotate(conversation_id, reason='explicit_user_input')

    def conversation_patch(self, conversation_id: str, expected_version: int, patch: dict[str, Any]) -> dict[str, Any]:
        _integer(expected_version, "expected_version", 1)
        if not isinstance(patch, dict) or not patch or set(patch) - {"title", "pinned", "archived", "model_selection"}:
            raise FrameworkError("INVALID_PARAMS", "unsupported conversation patch")
        if "title" in patch:
            _text(patch["title"], "title", 512)
        for field in ("pinned", "archived"):
            if field in patch and not isinstance(patch[field], bool):
                raise FrameworkError("INVALID_PARAMS", field + " must be boolean")
        with self._transaction():
            current = self._conversation(conversation_id)
            if current["metadata_version"] != expected_version:
                raise FrameworkError("CONFLICT", "conversation changed; refresh before editing again")
            if 'model_selection' in patch:
                self._validate_model_selection(patch['model_selection'])
            if patch.get("archived") and not current["archived"] and self.db.execute(
                    "SELECT 1 FROM commands WHERE conversation_id=? AND state IN ('admitted','submitted','applied') LIMIT 1", (conversation_id,)).fetchone():
                raise FrameworkError("CONFLICT", "stop the active conversation before archiving")
            changed = {field: value for field, value in patch.items() if value != current[field]}
            if 'model_selection' in changed:
                value = changed.pop('model_selection')
                changed['model_selection_json'] = _json(value) if value is not None else None
            # A deliberate rename, including to the current placeholder, must
            # always defeat a later automatic title.
            if "title" in patch:
                self.db.execute("UPDATE conversations SET title_mode='explicit' WHERE id=?", (conversation_id,))
            if changed:
                now = self.clock()
                if "pinned" in changed:
                    changed["pinned_at"] = now if changed["pinned"] else None
                if "archived" in changed:
                    changed["archived_at"] = now if changed["archived"] else None
                fields = ",".join(field + "=?" for field in changed)
                self.db.execute(f"UPDATE conversations SET {fields},metadata_version=metadata_version+1,metadata_updated_at=? WHERE id=?",
                                [*changed.values(), now, conversation_id])
        return self._conversation(conversation_id)

    def conversation_autotitle(self, conversation_id: str, command_id: str, text: str) -> dict[str, Any]:
        """Name a pending conversation from its first durable input once.

        This is a deterministic excerpt, not an extra model request. The host
        invokes it only after flushing the native user message.
        """
        _text(command_id, "command_id")
        _text(text, "text", 100000)
        title = " ".join(text.split())
        if len(title) > 32:
            title = title[:32].rstrip() + "…"
        with self._transaction():
            current = self._conversation(conversation_id)
            # A definitively rejected/cancelled admission with no native
            # receipt never became a message. Unknown or in-flight earlier
            # commands remain a barrier until their outcome is resolved.
            first = self.db.execute("""SELECT command_id,generation,state,runtime_request_id FROM commands
                WHERE conversation_id=? AND NOT (state IN ('rejected','cancelled') AND runtime_request_id IS NULL)
                ORDER BY created_at,rowid LIMIT 1""", (conversation_id,)).fetchone()
            mode = self.db.execute("SELECT title_mode FROM conversations WHERE id=?", (conversation_id,)).fetchone()[0]
            if mode != 'pending' or not first or first['command_id'] != command_id or not first['runtime_request_id'] or first['state'] not in ('applied', 'completed', 'cancelled') or first['generation'] != current['generation']:
                return {"conversation": current, "changed": False}
            now = self.clock()
            self.db.execute("UPDATE conversations SET title=?,title_mode='auto',metadata_version=metadata_version+1,metadata_updated_at=? WHERE id=?", (title, now, conversation_id))
            return {"conversation": self._conversation(conversation_id), "changed": True}

    def conversation_list(self, archived_filter: str = "all", limit: int = 200, offset: int = 0) -> dict[str, Any]:
        if archived_filter not in {"all", "active", "archived"}:
            raise FrameworkError("INVALID_PARAMS", "unsupported conversation archive filter")
        _integer(limit, "limit", 1, 1000)
        _integer(offset, "offset", 0, 2**31 - 1)
        clause = {"all": "1=1", "active": "c.archived=0", "archived": "c.archived=1"}[archived_filter]
        return {"conversations": [self._conversation(row[0]) for row in self.db.execute("""SELECT c.id FROM conversations c
            WHERE """ + clause + """ ORDER BY c.pinned DESC,
            COALESCE((SELECT MAX(updated_at) FROM commands WHERE conversation_id=c.id),c.created_at) DESC,c.id DESC LIMIT ? OFFSET ?""", (limit, offset))]}

    def conversation_resolve(self, selector: str) -> dict[str, Any]:
        """Resolve one phone route from a single SQLite snapshot, never list offsets.

        Full identities are authoritative. Otherwise exact titles and literal
        short identities share one namespace and must be disambiguated together.
        Nine rows are enough to distinguish a unique target from a bounded list
        of eight alternatives without materializing every product conversation.
        """
        _text(selector, "selector", 512)
        if "\r" in selector or "\n" in selector:
            raise FrameworkError("INVALID_PARAMS", "selector must be single-line text")
        params = {"selector": selector, "prefix": bool(re.fullmatch(r"[A-Za-z0-9_-]{8,128}", selector))}
        rows = self.db.execute("""WITH exact AS (SELECT id FROM conversations WHERE id=:selector)
            SELECT c.id AS conversation_id,c.title FROM conversations c WHERE c.archived=0 AND
            CASE WHEN EXISTS(SELECT 1 FROM exact) THEN c.id IN (SELECT id FROM exact)
            ELSE c.title=:selector OR (:prefix AND substr(
                CASE WHEN substr(c.id,1,13)='conversation_' THEN substr(c.id,14) ELSE c.id END,
                1,length(:selector))=:selector) END
            ORDER BY c.id LIMIT 9""", params)
        return {"conversations": [dict(row) for row in rows]}

    def conversation_weixin_receipts(self, conversation_id: str, limit: int = 200, offset: int = 0) -> dict[str, Any]:
        """Return only evidence needed to project old channel input, never credentials or text."""
        self._conversation(conversation_id)
        _integer(limit, "limit", 1, 1000)
        _integer(offset, "offset", 0, 2**31 - 1)
        rows = self.db.execute("""SELECT c.command_id,c.runtime_request_id,c.session_id,c.content_hash
            FROM commands c JOIN channel_weixin_events e
              ON e.conversation_id=c.conversation_id AND e.command_id=c.command_id AND e.content_hash=c.content_hash
            WHERE c.conversation_id=? AND c.runtime_request_id IS NOT NULL
            ORDER BY c.command_id,e.installation_id,e.event_id LIMIT ? OFFSET ?""", (conversation_id, limit, offset))
        return {"receipts": [dict(row) for row in rows]}

    def conversation_history_refs(self, conversation_id: str) -> dict[str, Any]:
        self._conversation(conversation_id)
        return {"conversation_id": conversation_id, "generations": [
            {**dict(row), "frozen": bool(row["frozen"])} for row in self.db.execute(
                "SELECT generation,session_id,frozen,reason,created_at FROM generations WHERE conversation_id=? ORDER BY generation", (conversation_id,))]}

    def conversation_continuation(self, conversation_id: str, generation: int) -> dict[str, Any]:
        """Authorize prior generations for a new prompt and retain source dependencies."""
        _integer(generation, "generation", 1)
        with self._transaction():
            current = self._conversation(conversation_id)
            if current['generation'] != generation or current['frozen'] or current['archived']:
                raise FrameworkError('CONFLICT', 'conversation generation is no longer active')
            rows = self.db.execute("SELECT generation,session_id FROM generations WHERE conversation_id=? AND generation<? ORDER BY generation DESC LIMIT 101", (conversation_id, generation)).fetchall()
            eligible, omitted = [], len(rows) > 100
            for row in rows[:100]:
                refs = [dict(item) for item in self.db.execute("SELECT source_id,version FROM source_uses WHERE conversation_id=? AND generation=?", (conversation_id, row['generation']))]
                if not row['session_id'] or self._source_reasons(refs):
                    omitted = True
                    continue
                self._record_uses(conversation_id, generation, refs)
                eligible.append(row['generation'])
        return {'generations': eligible, 'omitted': omitted}

    def _source(self, source_id: str) -> dict[str, Any]:
        row = self.db.execute("SELECT * FROM sources WHERE source_id=?", (source_id,)).fetchone()
        return {"source_id": source_id, "enabled": bool(row["enabled"]) if row else True,
                "version": row["version"] if row else 1, "reason": row["reason"] if row else ""}

    def _refs(self, sources: Any = None, *, max_refs: int = 200) -> list[dict[str, Any]]:
        if sources is None:
            return []
        if not isinstance(sources, list) or len(sources) > max_refs:
            raise FrameworkError("INVALID_PARAMS", f"sources must be at most {max_refs} references")
        values: dict[str, dict[str, Any]] = {}
        for item in sources:
            if not isinstance(item, dict):
                raise FrameworkError("INVALID_PARAMS", "source reference must be an object")
            identity = _text(item.get("source_id"), "source_id", 1024)
            version = item.get("version", self._source(identity)["version"])
            values[identity] = {"source_id": identity, "version": _integer(version, "version", 1)}
        return [values[key] for key in sorted(values)]

    def _source_reasons(self, refs: list[dict[str, Any]]) -> list[str]:
        reasons = []
        for ref in refs:
            current = self._source(ref["source_id"])
            if not current["enabled"]:
                reasons.append("source_disabled:" + ref["source_id"])
            elif current["version"] != ref["version"]:
                reasons.append("source_changed:" + ref["source_id"])
        return reasons

    def _record_uses(self, conversation_id: str, generation: int, sources: list[dict[str, Any]]) -> None:
        self.db.executemany("INSERT OR IGNORE INTO source_uses VALUES(?,?,?,?)", [(conversation_id, generation, item["source_id"], item["version"]) for item in sources])

    def _command(self, conversation_id: str, command_id: str) -> dict[str, Any]:
        row = self.db.execute("SELECT * FROM commands WHERE conversation_id=? AND command_id=?", (conversation_id, command_id)).fetchone()
        if row is None:
            raise FrameworkError("NOT_FOUND", "command not found")
        result = dict(row)
        selection = result.pop('model_selection_json')
        result['model_selection'] = json.loads(selection) if selection else None
        result["sources"] = json.loads(result.pop("sources_json"))
        result.update(replay=True, should_dispatch=False, needs_reconciliation=result["state"] in {"submitted", "unknown"})
        return result

    def command_admit(self, conversation_id: str, command_id: str, text: str, sources: Any = None) -> dict[str, Any]:
        _text(command_id, "command_id")
        _text(text, "text", 262144)
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        with self._transaction():
            old = self.db.execute("SELECT content_hash FROM commands WHERE conversation_id=? AND command_id=?", (conversation_id, command_id)).fetchone()
            if old:
                if old[0] != digest:
                    raise FrameworkError("CONFLICT", "command_id is already bound to different content")
                return self._command(conversation_id, command_id)
            current = self._conversation(conversation_id)
            if current["archived"]:
                raise FrameworkError("CONFLICT", "restore the archived conversation before sending")
            refs = self._refs(sources)
            if current["needs_session"]:
                raise FrameworkError("CONFLICT", "conversation requires a bound active session")
            reasons = self._source_reasons(refs)
            if reasons:
                raise FrameworkError("FORBIDDEN", "command contains unavailable source references")
            selection = current['model_selection']
            if selection is None:
                settings = self._settings()
                identity = settings.get('model_routes', {}).get('chat', 'default')
                model = settings['model'] if identity == 'default' else settings['model_connections'].get(identity, {}).get('model')
                if model is None:
                    raise FrameworkError('NOT_FOUND', '对话模型连接不存在，请刷新设置。')
                selection = {'connection_id': identity, 'model_id': model.get('model_id', '')}
            self.db.execute("""INSERT INTO commands(conversation_id,command_id,generation,session_id,content_hash,state,runtime_request_id,
                sources_json,created_at,updated_at,model_selection_json) VALUES(?,?,?,?,?,'admitted',NULL,?,?,?,?)""",
                (conversation_id, command_id, current["generation"], current["session_id"], digest, _json(refs), self.clock(), self.clock(), _json(selection)))
            self._record_uses(conversation_id, current["generation"], refs)
        return {**self._command(conversation_id, command_id), "replay": False, "should_dispatch": True}

    def command_ack(self, conversation_id: str, command_id: str, state: str, runtime_request_id: str | None = None) -> dict[str, Any]:
        if state not in {"submitted", "applied", "completed", "cancelled", "rejected", "unknown"}:
            raise FrameworkError("INVALID_PARAMS", "unsupported command state")
        if runtime_request_id is not None:
            _text(runtime_request_id, "runtime_request_id")
        with self._transaction():
            old = self._command(conversation_id, command_id)
            if old["runtime_request_id"] is not None and runtime_request_id not in (None, old["runtime_request_id"]):
                raise FrameworkError("CONFLICT", "command runtime identity cannot change")
            # Cancellation wins over late flush/completion acknowledgements.
            if old["state"] in TERMINAL_COMMANDS and old["state"] != state:
                return old
            if old["state"] == "applied" and state == "submitted":
                return old
            self.db.execute("UPDATE commands SET state=?,runtime_request_id=COALESCE(runtime_request_id,?),updated_at=? WHERE conversation_id=? AND command_id=?", (state, runtime_request_id, self.clock(), conversation_id, command_id))
        return self._command(conversation_id, command_id)

    def command_list(self, conversation_id: str, command_id: str | None = None, limit: int = 200, offset: int = 0) -> dict[str, Any]:
        self._conversation(conversation_id)
        _integer(limit, "limit", 1, 1000)
        _integer(offset, "offset", 0, 2**31 - 1)
        if command_id is not None:
            _text(command_id, "command_id")
            exists = self.db.execute("SELECT 1 FROM commands WHERE conversation_id=? AND command_id=?", (conversation_id, command_id)).fetchone()
            return {"commands": [self._command(conversation_id, command_id)] if exists else []}
        return {"commands": [self._command(conversation_id, row[0]) for row in self.db.execute("SELECT command_id FROM commands WHERE conversation_id=? ORDER BY created_at DESC,command_id DESC LIMIT ? OFFSET ?", (conversation_id, limit, offset))]}

    def interaction_stop(self, conversation_id: str, command_id: str | None = None) -> dict[str, Any]:
        with self._transaction():
            current = self._conversation(conversation_id)
            clause, args = "conversation_id=? AND generation=?", [conversation_id, current["generation"]]
            if command_id is not None:
                command = self._command(conversation_id, command_id)
                # Native cancel clears this session's queued input too; all
                # matching receipts must settle with the same stopped driver.
                clause, args = "conversation_id=? AND generation=?", [conversation_id, command["generation"]]
                current = {**current, "generation": command["generation"], "session_id": command["session_id"]}
            ids = [row[0] for row in self.db.execute(f"SELECT command_id FROM commands WHERE {clause} AND state NOT IN ('completed','cancelled','rejected')", args)]
            self.db.execute(f"UPDATE commands SET state='cancelled',updated_at=? WHERE {clause} AND state NOT IN ('completed','cancelled','rejected')", [self.clock(), *args])
            # Stop is a turn operation, not a transcript/privacy boundary. The
            # native Agent cancels its driver and inbox while retaining session
            # messages, tool receipts and attachments for explicit later input.
            # Source/configuration invalidation remains the owner of freezing.
        return {"conversation_id": conversation_id, "generation": current["generation"], "session_id": current["session_id"],
                "frozen": current["frozen"], "cancelled_command_ids": ids}

    def _freeze(self, conversation_id: str, generation: int, reason: str) -> None:
        self.db.execute("UPDATE generations SET frozen=1,reason=? WHERE conversation_id=? AND generation=?", (reason, conversation_id, generation))
        self.db.execute("UPDATE commands SET state='cancelled',updated_at=? WHERE conversation_id=? AND generation=? AND state NOT IN ('completed','cancelled','rejected')", (self.clock(), conversation_id, generation))

    def _invalidate(self, source_id: str, enabled: bool, reason: str, *, force: bool = False) -> dict[str, Any]:
        current = self._source(source_id)
        if current["enabled"] == enabled and not force:
            return {**current, "affected": []}
        version = current["version"] + 1
        self.db.execute("INSERT INTO sources VALUES(?,?,?,?) ON CONFLICT(source_id) DO UPDATE SET enabled=excluded.enabled,version=excluded.version,reason=excluded.reason", (source_id, int(enabled), version, reason))
        affected = []
        for row in self.db.execute("SELECT DISTINCT u.conversation_id,u.generation,g.session_id FROM source_uses u JOIN generations g ON g.conversation_id=u.conversation_id AND g.generation=u.generation WHERE u.source_id=? AND g.frozen=0", (source_id,)).fetchall():
            self._freeze(row["conversation_id"], row["generation"], reason)
            affected.append(dict(row))
        for table in ("initiatives", "deliveries"):
            state = "status"
            for row in self.db.execute(f"SELECT id,sources_json FROM {table} WHERE {state} IN ('pending','sending')").fetchall():
                if any(ref["source_id"] == source_id for ref in json.loads(row["sources_json"])):
                    self.db.execute(f"UPDATE {table} SET status='cancelled' WHERE id=?", (row["id"],))
        return {**self._source(source_id), "affected": affected}

    def source_set(self, source_id: str, enabled: bool, reason: str = "user_changed_source") -> dict[str, Any]:
        _text(source_id, "source_id", 1024)
        _text(reason, "reason", 2048)
        if not isinstance(enabled, bool):
            raise FrameworkError("INVALID_PARAMS", "enabled must be boolean")
        with self._transaction():
            result = self._invalidate(source_id, enabled, reason)
        self._update_controller()
        return result

    def source_list(self) -> dict[str, Any]:
        return {"sources": [{**dict(row), "enabled": bool(row["enabled"])} for row in self.db.execute("SELECT * FROM sources ORDER BY source_id")]}

    def guard_check(self, conversation_id: str | None = None, generation: int | None = None,
                    command_id: str | None = None, sources: Any = None, effect: str = "query",
                    confirmed: bool = False) -> dict[str, Any]:
        reasons = self._source_reasons(self._refs(sources))
        if effect not in {"query", "file", "script", "desktop", "delivery"}:
            reasons.append("unsupported_effect")
        if not isinstance(confirmed, bool):
            raise FrameworkError("INVALID_PARAMS", "confirmed must be boolean")
        command = None
        if command_id is not None:
            if conversation_id is None:
                reasons.append("conversation_required")
            else:
                try:
                    command = self._command(conversation_id, command_id)
                    if command["state"] not in {"admitted", "submitted", "applied"}:
                        reasons.append("command_not_active")
                    reasons.extend(self._source_reasons(command["sources"]))
                    if generation is not None and generation != command["generation"]:
                        reasons.append("command_generation_mismatch")
                    generation = command["generation"]
                except FrameworkError:
                    reasons.append("command_not_found")
        if conversation_id is not None:
            try:
                current = self._conversation(conversation_id)
                if current["archived"]:
                    reasons.append("conversation_archived")
                target = current["generation"] if generation is None else generation
                row = self.db.execute("SELECT frozen FROM generations WHERE conversation_id=? AND generation=?", (conversation_id, target)).fetchone()
                if row is None or row[0] or target != current["generation"]:
                    reasons.append("generation_frozen_or_stale")
            except FrameworkError:
                reasons.append("conversation_not_found")
        if effect != "query":
            if command is None:
                reasons.append("command_scope_required")
            if not confirmed:
                reasons.append("confirmation_required")
        return {"allow": not reasons, "reasons": sorted(set(reasons))}

    def preference_list(self) -> dict[str, Any]:
        return {"preferences": [{"scope": row["scope"], "key": row["key"], "value": json.loads(row["value_json"]), "revision": row["revision"]} for row in self.db.execute("SELECT * FROM preferences ORDER BY scope,key")
                                if self._source(f"preference:{row['scope']}:{row['key']}")["enabled"]]}

    def preference_set(self, key: str, value: Any, scope: str = "user/local", conversation_id: str | None = None, weixin_scope: dict | None = None) -> dict[str, Any]:
        _text(key, "key")
        _text(scope, "scope")
        encoded = _json(value)
        if len(encoded.encode("utf-8")) > 16384:
            raise FrameworkError("INVALID_PARAMS", "preference exceeds size limit")
        with self._transaction():
            if weixin_scope is not None:
                self.channels.check_conversation_scope(conversation_id, weixin_scope)
            old = self.db.execute("SELECT * FROM preferences WHERE scope=? AND key=?", (scope, key)).fetchone()
            if old and old["value_json"] == encoded:
                return {"scope": scope, "key": key, "value": value, "revision": old["revision"], "affected": []}
            revision = old["revision"] + 1 if old else 1
            invalidated = self._invalidate(f"preference:{scope}:{key}", True, "preference_corrected", force=bool(old))
            self.db.execute("INSERT INTO preferences VALUES(?,?,?,?,?) ON CONFLICT(scope,key) DO UPDATE SET value_json=excluded.value_json,revision=excluded.revision,updated_at=excluded.updated_at", (scope, key, encoded, revision, self.clock()))
        self._update_controller()
        return {"scope": scope, "key": key, "value": value, "revision": revision, "affected": invalidated["affected"]}

    def context_read(self, conversation_id: str | None = None, generation: int | None = None, query: str = "",
                     include_activity: bool = True, include_memory: bool = True, limit: int = 10) -> dict[str, Any]:
        _text(query, "query", 2048, empty=True)
        _integer(limit, "limit", 1, 50)
        if not isinstance(include_activity, bool) or not isinstance(include_memory, bool):
            raise FrameworkError("INVALID_PARAMS", "include flags must be boolean")
        if conversation_id is not None:
            guard = self.guard_check(conversation_id, generation)
            if not guard["allow"]:
                raise FrameworkError("FORBIDDEN", "context requested for an inactive generation")
            generation = self._conversation(conversation_id)["generation"] if generation is None else generation
        settings = self._settings()
        settings["memory_enabled"] &= self._source("memory")["enabled"]
        settings["activity_enabled"] &= self._source("activity")["enabled"]
        result = self.reader.read(settings, query=query, limit=limit, include_memory=include_memory, include_activity=include_activity)
        refs: list[dict[str, Any]] = []
        preferences = []
        for preference in self.preference_list()["preferences"]:
            source = self._source(f"preference:{preference['scope']}:{preference['key']}")
            if source["enabled"]:
                preferences.append(preference)
                refs.append({"source_id": source["source_id"], "version": source["version"]})
        for kind, field, identity_field in (("memory", "memories", "id"), ("activity", "items", "item_id")):
            kept = []
            for item in result[kind].get(field, []):
                if kind == "memory":
                    try:
                        item_refs = self._memory_source_refs(item)
                    except FrameworkError:
                        result["gaps"].append("memory lineage unavailable or inactive")
                        continue
                else:
                    identities = [kind, f"{kind}:{item[identity_field]}"]
                    identities.extend(f"observation:{value}" for value in item.get("source_observation_ids", []))
                    item_refs = [{"source_id": value, "version": self._source(value)["version"]} for value in identities]
                if self._source_reasons(item_refs):
                    continue
                kept.append(item)
                refs.extend(item_refs)
            result[kind][field] = kept
        refs = self._refs(refs, max_refs=10000)
        if conversation_id is not None:
            with self._transaction():
                self._record_uses(conversation_id, generation, refs)
        if len(refs) > 200:
            result["gaps"].append("public_source_list_bounded; complete known use receipts retained by product owner")
        return {**result, "preferences": preferences, "sources": refs[:200]}

    def _memory_source_refs(self, record: dict[str, Any], *, allow_candidate: bool = False) -> list[dict[str, Any]]:
        """Include the owner's known derivation chain on every memory read."""
        from mirally.core.memory.store import MemoryStore
        identities, seen = {"memory"}, set()
        config = memory_config(self.root, self.home, self._settings())
        store = MemoryStore(config.database_path, read_only=True)
        current = store.get_memory(record["id"], include_inactive=True)
        if current is None:
            store.close()
            raise FrameworkError("FORBIDDEN", "memory is unavailable")
        try:
            for _depth in range(64):
                identity = current["id"]
                allowed = (None, "active", "candidate") if allow_candidate and identity == record["id"] else (None, "active")
                if identity in seen or current.get("status") not in allowed:
                    raise FrameworkError("FORBIDDEN", "memory lineage is inactive or cyclic")
                seen.add(identity)
                identities.add("memory:" + identity)
                identities.update("observation:" + value for value in current.get("source_observation_ids", []))
                identities.update("activity:" + value for value in current.get("source_item_ids", []))
                identities.update("activity_episode:" + value for value in current.get("source_episode_ids", []))
                identities.update("evidence:" + value for value in current.get("evidence_ids", []))
                parent = (current.get("metadata") or {}).get("derived_from_memory_id")
                if not parent:
                    return [{"source_id": value, "version": self._source(value)["version"]} for value in sorted(identities)]
                current = store.get_memory(parent, include_inactive=True)
                if current is None:
                    raise FrameworkError("FORBIDDEN", "memory ancestor is unavailable")
            raise FrameworkError("FORBIDDEN", "memory lineage exceeds its depth limit")
        finally:
            if store is not None:
                store.close()

    def memory_correct(self, content: str, memory_id: str | None = None, title: str | None = None,
                       request_id: str | None = None) -> dict[str, Any]:
        """Explicit correction through Memory's owner API, never SQL into its DB.

        Fence stale use first. If the separate owner write fails, the source
        stays blocked and this call can be retried; no atomic cross-DB claim.
        Optional request_id gives creation a stable owner ID across retries;
        a replay never rewrites a memory or restores withdrawn source use.
        """
        creating = memory_id is None
        if request_id is not None:
            _text(request_id, "request_id")
        if creating:
            identity = uuid.uuid5(uuid.NAMESPACE_URL, "mirally:manual-memory:" + request_id) if request_id is not None else uuid.uuid4()
            memory_id = "manual_" + identity.hex
        else:
            memory_id = _text(memory_id, "memory_id")
        _text(content, "content", 100000)
        if title is not None:
            _text(title, "title", 1024, empty=True)
        from mirally.core.memory.store import MemoryStore
        config = memory_config(self.root, self.home, self._settings())
        if not config.enabled or not self._settings()["memory_enabled"]:
            raise FrameworkError("FORBIDDEN", "memory is disabled")
        old = None
        if config.database_path.is_file():
            with MemoryStore(config.database_path, read_only=True) as reader:
                old = reader.get_memory(memory_id, include_inactive=True)
        if old is None and not creating:
            raise FrameworkError("NOT_FOUND", "memory not found")
        if old is not None and creating and request_id is not None:
            if (old.get("content") != content or old.get("title") != (title or content.splitlines()[0][:100])
                    or old.get("provenance") != "user_confirmed"):
                raise FrameworkError("CONFLICT", "request_id already identifies a different memory")
            return {"memory": old, "affected": [], "source": self._source("memory:" + memory_id)}
        if old is None:
            record = {"id": memory_id, "kind": "semantic", "scope": "user/local", "status": "active",
                      "provenance": "user_confirmed", "title": title or content.splitlines()[0][:100], "content": content}
            with MemoryStore(config.database_path) as writer:
                updated = writer.put_memory(record)
            return {"memory": updated, "affected": [], "source": self._source("memory:" + memory_id)}
        if old.get("content") == content and (title is None or old.get("title") == title) and old.get("provenance") == "user_confirmed":
            return {"memory": old, "affected": [], "source": self._source("memory:" + memory_id)}
        with self._transaction():
            invalidated = self._invalidate("memory:" + memory_id, False, "memory_correction_pending", force=True)
        self._update_controller()
        record = {**old, "content": content, "provenance": "user_confirmed", "status": "active"}
        if title is not None:
            record["title"] = title
        with MemoryStore(config.database_path) as writer:
            updated = writer.put_memory(record)
        if updated.get("blocked_by_tombstone"):
            raise FrameworkError("FORBIDDEN", "forgotten memory cannot be restored by correction")
        with self._transaction():
            source = self._invalidate("memory:" + memory_id, True, "memory_corrected")
        self._update_controller()
        return {"memory": updated, "affected": invalidated["affected"], "source": source}

    def memory_search(self, query: str = "", limit: int = 10, include_candidates: bool = False, offset: int = 0, **filters) -> dict[str, Any]:
        return self.experience.memory_search(query=query, limit=limit, include_candidates=include_candidates, offset=offset, **filters)

    def memory_read(self, memory_id: str, from_line: int = 1, line_count: int = 100, include_candidates: bool = False) -> dict[str, Any]:
        return self.experience.memory_read(memory_id, from_line=from_line, line_count=line_count, include_candidates=include_candidates)

    def reminder_create(self, text: str, due_at: str, channel: str = "inbox", request_id: str | None = None,
                        conversation_id: str | None = None) -> dict[str, Any]:
        _text(due_at, "due_at", 128)
        try:
            moment = datetime.fromisoformat(due_at.replace("Z", "+00:00"))
            if moment.tzinfo is None:
                raise ValueError("timezone required")
            timestamp = int(moment.timestamp() * 1000)
        except (ValueError, OverflowError) as error:
            raise FrameworkError("INVALID_PARAMS", "due_at must be ISO 8601 with an explicit timezone") from error
        return self.commitment_create(text, timestamp, request_id=request_id, conversation_id=conversation_id, channel=channel)

    def _record(self, table: str, identity: str) -> dict[str, Any]:
        row = self.db.execute(f"SELECT * FROM {table} WHERE id=?", (identity,)).fetchone()
        if row is None:
            raise FrameworkError("NOT_FOUND", "record not found")
        result = dict(row)
        if "sources_json" in result:
            result["sources"] = json.loads(result.pop("sources_json"))
        if "receipt_json" in result:
            result["receipt"] = json.loads(result.pop("receipt_json")) if row["receipt_json"] else None
        return result

    def job_notification_create(self, conversation_id: str, generation: int, title: str, body: str,
                                request_id: str, due_at_utc_ms: int, sources: Any = None,
                                installation_id: str | None = None) -> dict[str, Any]:
        """Atomically fence a completed job notification to its original owner."""
        with self._transaction():
            check = self.guard_check(conversation_id=conversation_id, generation=generation, sources=sources)
            if not check['allow']:
                raise FrameworkError('CONFLICT', '会话或来源已变化，未发送作业通知。')
            if installation_id is not None:
                state = self.channels._state()
                if not state.get('bound') or not state.get('enabled') or state.get('installation_id') != installation_id:
                    raise FrameworkError('CONFLICT', '微信绑定已变化，未发送作业通知。')
            existing = self.db.execute('SELECT id FROM commitments WHERE request_id=?', (request_id,)).fetchone()
            result = self.commitment_create(title=title, body=body, due_at_utc_ms=due_at_utc_ms,
                request_id=request_id, conversation_id=conversation_id, sources=sources)
            # Capture the channel destination within the same transaction as
            # the binding check. A later rebind cannot adopt this notification.
            if not existing:
                self.calendar.queue_deliveries(result['id'])
                now = self.clock()
                for channel in ('desktop', 'weixin'):
                    self.db.execute('INSERT OR IGNORE INTO calendar_deliveries VALUES(?,?,?,?,NULL,NULL,?,?,?)',
                        ('calendar_delivery_'+uuid.uuid4().hex, result['id'], channel, 'cancelled',
                         _json({'reason':'channel_disabled'}), now, now))
            return result

    def commitment_create(self, title: str, due_at_utc_ms: int, body: str | None = None, request_id: str | None = None,
                          conversation_id: str | None = None, channel: str = "inbox", expires_at_utc_ms: int | None = None,
                          sources: Any = None) -> dict[str, Any]:
        _text(title, "title", 1024)
        body = title if body is None else _text(body, "body", 24000)
        _integer(due_at_utc_ms, "due_at_utc_ms")
        if expires_at_utc_ms is not None:
            _integer(expires_at_utc_ms, "expires_at_utc_ms", due_at_utc_ms)
        _text(channel, "channel", 128)
        if request_id is not None:
            _text(request_id, "request_id")
        if conversation_id is not None:
            self._conversation(conversation_id)
        refs = self._refs(sources)
        if self._source_reasons(refs):
            raise FrameworkError("FORBIDDEN", "reminder contains unavailable sources")
        with self._transaction():
            if request_id:
                row = self.db.execute("SELECT id FROM commitments WHERE request_id=?", (request_id,)).fetchone()
                if row:
                    old = self._record("commitments", row[0])
                    if any(old[key] != value for key, value in (("title", title), ("body", body), ("due_at", due_at_utc_ms), ("channel", channel), ("conversation_id", conversation_id), ("expires_at", expires_at_utc_ms), ("sources", refs))):
                        raise FrameworkError("CONFLICT", "request_id already identifies a different reminder")
                    return old
            identity = "reminder_" + uuid.uuid4().hex
            self.db.execute("INSERT INTO commitments VALUES(?,?,?,?,?,?,?,?,'pending',?,?)", (identity, request_id, title, body, due_at_utc_ms, expires_at_utc_ms, conversation_id, channel, _json(refs), self.clock()))
        return self._record("commitments", identity)

    def commitment_list(self) -> dict[str, Any]:
        return {"commitments": [self._record("commitments", row[0]) for row in self.db.execute("SELECT id FROM commitments ORDER BY due_at DESC LIMIT 200")]}

    def commitment_cancel(self, commitment_id: str) -> dict[str, Any]:
        with self._transaction():
            self._record("commitments", commitment_id)
            self.db.execute("UPDATE commitments SET status='cancelled' WHERE id=? AND status<>'fulfilled'", (commitment_id,))
            self.db.execute("UPDATE deliveries SET status='cancelled',updated_at=? WHERE kind='commitment' AND reference_id=? AND status NOT IN ('delivered','failed')", (self.clock(), commitment_id))
            self.calendar.cancel_deliveries(commitment_id)
        return self._record("commitments", commitment_id)

    def initiative_offer(self, title: str, body: str, reason: str, expires_at_utc_ms: int,
                         dedupe_key: str, not_before_utc_ms: int | None = None,
                         conversation_id: str | None = None, channel: str = "inbox", sources: Any = None) -> dict[str, Any]:
        for name, value, size in (("title", title, 1024), ("body", body, 24000), ("reason", reason, 2048), ("dedupe_key", dedupe_key, 512), ("channel", channel, 128)):
            _text(value, name, size)
        _integer(expires_at_utc_ms, "expires_at_utc_ms")
        not_before = self.clock() if not_before_utc_ms is None else _integer(not_before_utc_ms, "not_before_utc_ms")
        if expires_at_utc_ms <= not_before:
            raise FrameworkError("INVALID_PARAMS", "initiative must expire after not_before")
        if conversation_id is not None:
            self._conversation(conversation_id)
        refs = self._refs(sources)
        if self._source_reasons(refs):
            raise FrameworkError("FORBIDDEN", "initiative contains unavailable sources")
        with self._transaction():
            old = self.db.execute("SELECT id FROM initiatives WHERE dedupe_key=?", (dedupe_key,)).fetchone()
            if old:
                return self._record("initiatives", old[0])
            identity = "initiative_" + uuid.uuid4().hex
            self.db.execute("INSERT INTO initiatives VALUES(?,?,?,?,?,?,?,?,?,'pending',?,?)", (identity, dedupe_key, title, body, reason, expires_at_utc_ms, not_before, conversation_id, channel, _json(refs), self.clock()))
        return self._record("initiatives", identity)

    def initiative_list(self) -> dict[str, Any]:
        return {"initiatives": [self._record("initiatives", row[0]) for row in self.db.execute("SELECT id FROM initiatives ORDER BY created_at DESC LIMIT 100")]}

    def initiative_acquire(self) -> dict[str, Any]:
        """Reserve at most one optional model check per four hours, across restarts."""
        settings, now = self._settings(), self.clock()
        if not (settings["general_initiative_enabled"] and settings["background_enabled"]) or settings["do_not_disturb"]:
            return {"status": "disabled"}
        with self._transaction():
            if self.db.execute("SELECT 1 FROM initiative_runs WHERE created_at>? LIMIT 1", (now - 4 * 3600000,)).fetchone():
                return {"status": "cooldown"}
            if self.db.execute("SELECT 1 FROM inbox i JOIN deliveries d ON i.delivery_id=d.id WHERE i.state='unread' AND d.kind='initiative' LIMIT 1").fetchone():
                return {"status": "unread_contact"}
            context = self.context_read(query="", limit=8)
            if not (context["preferences"] or context["memory"].get("memories") or context["activity"].get("items")):
                return {"status": "no_evidence"}
            identity, token = "initiative_run_" + uuid.uuid4().hex, uuid.uuid4().hex
            self.db.execute("INSERT INTO initiative_runs VALUES(?,?,?,?,?,?)", (identity, token, now, now + 120000, "leased", _json(context["sources"])))
            return {"status": "leased", "job_id": identity, "lease_token": token, "context": context, "now_utc_ms": now}

    def initiative_complete(self, job_id: str, lease_token: str, proposal: dict[str, Any]) -> dict[str, Any]:
        _text(job_id, "job_id")
        _text(lease_token, "lease_token")
        if not isinstance(proposal, dict) or not isinstance(proposal.get("contact"), bool):
            raise FrameworkError("INVALID_PARAMS", "proposal must contain an explicit contact boolean")
        with self._transaction():
            row = self.db.execute("SELECT * FROM initiative_runs WHERE id=?", (job_id,)).fetchone()
            if row is None or row["lease_token"] != lease_token or row["status"] != "leased":
                raise FrameworkError("CONFLICT", "initiative lease is no longer active")
            settings, refs = self._settings(), json.loads(row["sources_json"])
            allowed = (row["expires_at"] > self.clock() and settings["general_initiative_enabled"] and settings["background_enabled"]
                       and not settings["do_not_disturb"] and not self._source_reasons(refs))
            self.db.execute("UPDATE initiative_runs SET status=? WHERE id=?", ("completed" if allowed else "discarded", job_id))
            if not allowed or not proposal["contact"]:
                return {"status": "quiet" if allowed else "discarded"}
            result = self.initiative_offer(title=proposal.get("title"), body=proposal.get("body"), reason=proposal.get("reason"),
                                           dedupe_key=job_id, expires_at_utc_ms=self.clock() + 3600000, sources=refs)
            return {"status": "offered", "initiative": result}

    def initiative_dismiss(self, initiative_id: str) -> dict[str, Any]:
        with self._transaction():
            self._record("initiatives", initiative_id)
            self.db.execute("UPDATE initiatives SET status='dismissed' WHERE id=?", (initiative_id,))
            self.db.execute("UPDATE deliveries SET status='cancelled',updated_at=? WHERE kind='initiative' AND reference_id=? AND status IN ('pending','sending','unknown')", (self.clock(), initiative_id))
        return self._record("initiatives", initiative_id)

    def _queue_delivery(self, kind: str, record: dict[str, Any]) -> str | None:
        if self._source_reasons(record["sources"]):
            self.db.execute(f"UPDATE {'commitments' if kind == 'commitment' else 'initiatives'} SET status='cancelled' WHERE id=?", (record["id"],))
            return None
        identity = "delivery_" + uuid.uuid4().hex
        self.db.execute("INSERT OR IGNORE INTO deliveries VALUES(?,?,?,?,'pending',NULL,NULL,?,?,?)", (identity, kind, record["id"], record["channel"], _json(record["sources"]), self.clock(), self.clock()))
        row = self.db.execute("SELECT id,status FROM deliveries WHERE kind=? AND reference_id=?", (kind, record["id"])).fetchone()
        identity = row["id"]
        table = "commitments" if kind == "commitment" else "initiatives"
        self.db.execute(f"UPDATE {table} SET status='queued' WHERE id=?", (record["id"],))
        if record["channel"] == "inbox" and row["status"] == "pending":
            self.db.execute("INSERT OR IGNORE INTO inbox VALUES(?,'unread',?)", (identity, self.clock()))
            self.db.execute("UPDATE deliveries SET status='delivered',receipt_json=?,updated_at=? WHERE id=?", (_json({"channel": "inbox", "durable": True}), self.clock(), identity))
            self.db.execute(f"UPDATE {table} SET status=? WHERE id=?", ("fulfilled" if kind == "commitment" else "delivered", record["id"]))
        return identity

    def delivery_tick(self) -> dict[str, Any]:
        generated = []
        now = self.clock()
        with self._transaction():
            self.db.execute("UPDATE commitments SET status='expired' WHERE status='pending' AND expires_at IS NOT NULL AND expires_at<?", (now,))
            self.db.execute("UPDATE initiatives SET status='expired' WHERE status='pending' AND expires_at<=?", (now,))
            for row in self.db.execute("SELECT id FROM commitments WHERE status='pending' AND due_at<=? ORDER BY due_at LIMIT 100", (now,)).fetchall():
                identity = self._queue_delivery("commitment", self._record("commitments", row[0]))
                if identity:
                    self.calendar.queue_deliveries(row[0])
                    generated.append(identity)
            self.calendar.advance_recurrences(now)
            settings = self._settings()
            if settings["general_initiative_enabled"] and not settings["do_not_disturb"]:
                for row in self.db.execute("SELECT id FROM initiatives WHERE status='pending' AND not_before<=? AND expires_at>? ORDER BY created_at LIMIT 20", (now, now)).fetchall():
                    identity = self._queue_delivery("initiative", self._record("initiatives", row[0]))
                    if identity:
                        generated.append(identity)
        return {"delivery_ids": generated, "now_utc_ms": now}

    def delivery_list(self) -> dict[str, Any]:
        return {"deliveries": [self._record("deliveries", row[0]) for row in self.db.execute("SELECT id FROM deliveries ORDER BY created_at DESC LIMIT 200")]}

    def delivery_claim(self, delivery_id: str) -> dict[str, Any]:
        with self._transaction():
            record = self._record("deliveries", delivery_id)
            if record["channel"] == "inbox":
                raise FrameworkError("CONFLICT", "inbox delivery is committed by tick")
            if record["status"] != "pending":
                return {**record, "should_send": False}
            if self._source_reasons(record["sources"]):
                self.db.execute("UPDATE deliveries SET status='cancelled',updated_at=? WHERE id=?", (self.clock(), delivery_id))
                return {**self._record("deliveries", delivery_id), "should_send": False}
            token = uuid.uuid4().hex
            self.db.execute("UPDATE deliveries SET status='sending',claim_token=?,updated_at=? WHERE id=?", (token, self.clock(), delivery_id))
            owner = self._record("commitments" if record["kind"] == "commitment" else "initiatives", record["reference_id"])
        return {**self._record("deliveries", delivery_id), "should_send": True, "title": owner["title"], "body": owner["body"], "conversation_id": owner["conversation_id"]}

    def delivery_ack(self, delivery_id: str, claim_token: str, state: str, receipt: dict[str, Any] | None = None) -> dict[str, Any]:
        if state not in {"delivered", "failed", "unknown"}:
            raise FrameworkError("INVALID_PARAMS", "unsupported delivery acknowledgement")
        if receipt is not None and (not isinstance(receipt, dict) or len(_json(receipt)) > 16384):
            raise FrameworkError("INVALID_PARAMS", "receipt must be a bounded object")
        with self._transaction():
            old = self._record("deliveries", delivery_id)
            if old["claim_token"] != claim_token or not claim_token:
                raise FrameworkError("CONFLICT", "delivery claim token mismatch")
            if old["status"] in {"delivered", "failed"}:
                return old
            # Late evidence is retained, but never resurrects a cancelled plan.
            result_state = "cancelled" if old["status"] == "cancelled" else state
            facts = {"state": state, "late": old["status"] == "cancelled", "receipt": receipt or {}}
            self.db.execute("UPDATE deliveries SET status=?,receipt_json=?,updated_at=? WHERE id=?", (result_state, _json(facts), self.clock(), delivery_id))
            if result_state == "delivered":
                table = "commitments" if old["kind"] == "commitment" else "initiatives"
                self.db.execute(f"UPDATE {table} SET status=? WHERE id=? AND status NOT IN ('cancelled','dismissed')", ("fulfilled" if old["kind"] == "commitment" else "delivered", old["reference_id"]))
        return self._record("deliveries", delivery_id)

    def inbox_list(self) -> dict[str, Any]:
        self.delivery_tick()
        items = []
        for row in self.db.execute("SELECT i.delivery_id,i.state,i.updated_at,d.kind,d.reference_id FROM inbox i JOIN deliveries d ON d.id=i.delivery_id ORDER BY d.created_at DESC LIMIT 200"):
            owner = self._record("commitments" if row["kind"] == "commitment" else "initiatives", row["reference_id"])
            withdrawn = bool(self._source_reasons(owner["sources"]))
            items.append({**dict(row), "title": "已撤回的联系" if withdrawn else owner["title"],
                          "body": "" if withdrawn else owner["body"], "withdrawn": withdrawn,
                          "conversation_id": owner["conversation_id"]})
        return {"items": items}

    def inbox_ack(self, delivery_id: str, state: str = "read") -> dict[str, Any]:
        if state not in {"read", "dismissed", "replied"}:
            raise FrameworkError("INVALID_PARAMS", "unsupported inbox state")
        with self._transaction():
            old = self.db.execute("SELECT state FROM inbox WHERE delivery_id=?", (delivery_id,)).fetchone()
            if old is None:
                raise FrameworkError("NOT_FOUND", "inbox item not found")
            if old[0] == "replied" and state != "replied":
                state = old[0]
            self.db.execute("UPDATE inbox SET state=?,updated_at=? WHERE delivery_id=?", (state, self.clock(), delivery_id))
        return {"delivery_id": delivery_id, "state": state}

    def close(self) -> None:
        with self._lock:
            try:
                self.experience.close()
                if self.controller is not None:
                    self.controller.close()
            finally:
                self.db.close()

    def __enter__(self) -> "FrameworkService":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()
