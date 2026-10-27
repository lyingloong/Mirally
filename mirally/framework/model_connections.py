"""Named model configuration belongs to the product, independently of conversations."""
from __future__ import annotations

from copy import deepcopy
import json
import re
from urllib.parse import urlsplit
import uuid

MODEL_FIELDS = frozenset({'model_id', 'image_enabled', 'reasoning_enabled', 'reasoning_budget',
                          'reasoning_effort', 'context_window', 'max_tokens', 'temperature'})


def selected_connection_model(settings: dict, purpose: str = "background") -> dict:
    identity = settings.get("model_routes", {}).get(purpose, "default")
    if identity == "default":
        return settings.get("model", {})
    connection = settings.get("model_connections", {}).get(identity)
    if connection is None:
        raise ValueError("selected model connection no longer exists")
    return connection["model"]


class ModelConnections:
    def __init__(self, service):
        self.service = service

    @staticmethod
    def _fail(code, message):
        from .service import FrameworkError
        raise FrameworkError(code, message)

    def _put(self, key, value):
        self.service.db.execute("INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json",
                                (key, json.dumps(value, ensure_ascii=False, allow_nan=False)))

    def list(self):
        settings = self.service.settings_get()
        connections = [{"id": "default", "name": "默认连接", "model": settings["model"]},
                       *[{"id": identity, **connection} for identity, connection in settings["model_connections"].items()]]
        for connection in connections:
            model = connection['model']
            connection['models'] = ([{key: value for key, value in model.items() if key in MODEL_FIELDS}]
                                    if model.get('model_id') else []) + settings.get('model_catalogs', {}).get(connection['id'], [])
        return {"connections": connections, "routes": settings["model_routes"]}

    def _assert_connection_inactive(self, connection_id):
        for row in self.service.db.execute("SELECT model_selection_json FROM commands WHERE model_selection_json IS NOT NULL AND state IN ('admitted','submitted','applied')"):
            if json.loads(row[0]).get('connection_id') == connection_id:
                self._fail('MODEL_IN_USE', '连接正在被未完成请求使用，请等待请求结束后再更改。')

    def _assert_model_inactive(self, connection_id, model_id):
        for row in self.service.db.execute("SELECT model_selection_json FROM commands WHERE model_selection_json IS NOT NULL AND state IN ('admitted','submitted','applied')"):
            selection = json.loads(row[0])
            if selection.get('connection_id') == connection_id and selection.get('model_id') == model_id:
                self._fail('MODEL_IN_USE', '模型正在被未完成请求使用，请等待请求结束后再更改。')

    def _assert_model_unused(self, connection_id, model_id):
        for table, condition in (("conversations", "model_selection_json IS NOT NULL"),
                                 ("commands", "model_selection_json IS NOT NULL AND state IN ('admitted','submitted','applied')")):
            for row in self.service.db.execute(f"SELECT model_selection_json FROM {table} WHERE {condition}"):
                selection = json.loads(row[0])
                if selection.get('connection_id') == connection_id and selection.get('model_id') == model_id:
                    self._fail('MODEL_IN_USE', '模型仍被会话或未完成的请求使用，请先切换会话并等待请求结束。')

    def save_catalog(self, connection_id, model):
        settings = self.service._settings()
        connection = settings['model'] if connection_id == 'default' else settings['model_connections'].get(connection_id, {}).get('model')
        if connection is None:
            self._fail('NOT_FOUND', '模型连接不存在，请刷新后重试。')
        if not isinstance(model, dict) or set(model) - MODEL_FIELDS or not isinstance(model.get('model_id'), str) or not model['model_id'].strip():
            self._fail('INVALID_PARAMS', '请填写有效模型 ID 和模型参数。')
        model = {**model, 'model_id': model['model_id'].strip()}
        if model['model_id'] == connection.get('model_id'):
            self._fail('INVALID_PARAMS', '该模型已作为连接默认模型保存，请在连接设置中修改。')
        catalogs = settings.setdefault('model_catalogs', {})
        entries = catalogs.setdefault(connection_id, [])
        existing = next((entry for entry in entries if entry['model_id'] == model['model_id']), None)
        if existing is None and len(entries) >= 500:
            self._fail('INVALID_PARAMS', '每个连接最多保存 500 个附加模型。')
        from .service import DEFAULT_SETTINGS
        base = {key: value for key, value in DEFAULT_SETTINGS['model'].items() if key in MODEL_FIELDS}
        updated = self.service._model_update({**base, **(existing or {})}, model)
        item = {key: value for key, value in updated.items() if key in MODEL_FIELDS}
        with self.service._transaction():
            # The first model becomes the existing connection's default without
            # ever moving or duplicating its credential or endpoint.
            current = self.service._settings()
            current_connection = (current['model'] if connection_id == 'default'
                                  else current['model_connections'].get(connection_id, {}).get('model'))
            if current_connection is None:
                self._fail('NOT_FOUND', '模型连接不存在，请刷新后重试。')
            if not current_connection.get('model_id'):
                self._assert_connection_inactive(connection_id)
                current_connection.update(item)
                if connection_id == 'default':
                    self._put('model', current_connection)
                else:
                    self._put('model_connections', current['model_connections'])
                current_catalogs = current.get('model_catalogs', {})
                if connection_id in current_catalogs:
                    current_catalogs[connection_id] = [entry for entry in current_catalogs[connection_id] if entry['model_id'] != item['model_id']]
                    if not current_catalogs[connection_id]:
                        del current_catalogs[connection_id]
                    self._put('model_catalogs', current_catalogs)
            else:
                self._assert_model_inactive(connection_id, item['model_id'])
                catalogs[connection_id] = [entry for entry in entries if entry['model_id'] != item['model_id']] + [item]
                self._put('model_catalogs', catalogs)
        self.service._update_controller()
        return self.list()

    def remove_catalog(self, connection_id, model_id):
        settings = self.service._settings()
        catalogs = settings.get('model_catalogs', {})
        entries = catalogs.get(connection_id, [])
        if not any(entry['model_id'] == model_id for entry in entries):
            self._fail('NOT_FOUND', '模型不存在，请刷新后重试。')
        with self.service._transaction():
            self._assert_model_unused(connection_id, model_id)
            catalogs[connection_id] = [entry for entry in entries if entry['model_id'] != model_id]
            if not catalogs[connection_id]:
                del catalogs[connection_id]
            self._put('model_catalogs', catalogs)
        self.service._update_controller()
        return self.list()

    def save(self, model, name=None, connection_id=None):
        from .service import DEFAULT_SETTINGS
        if connection_id is not None and (not isinstance(connection_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", connection_id)):
            self._fail("INVALID_PARAMS", "invalid model connection identity")
        settings = self.service._settings()
        identity = connection_id or uuid.uuid4().hex
        existing = ({"name": "默认连接", "model": settings["model"]} if identity == "default"
                    else settings["model_connections"].get(identity))
        if connection_id and existing is None:
            self._fail("NOT_FOUND", "模型连接不存在，请刷新后重试。")
        title = name if name is not None else existing["name"] if existing else ""
        if not isinstance(title, str) or not title.strip() or len(title) > 128:
            self._fail("INVALID_PARAMS", "请填写不超过 128 字的连接名称。")
        if not existing and len(settings["model_connections"]) >= 32:
            self._fail("INVALID_PARAMS", "最多保存 32 个自定义模型连接。")
        current = existing["model"] if existing else deepcopy(DEFAULT_SETTINGS["model"])
        updated = self.service._model_update(current, model)
        if updated.get('model_id') != current.get('model_id'):
            if any(entry['model_id'] == updated.get('model_id') for entry in settings.get('model_catalogs', {}).get(identity, [])):
                self._fail('INVALID_PARAMS', '该模型已在模型列表中，请先从列表移除。')
        origin = lambda url: (urlsplit(url).scheme, urlsplit(url).hostname, urlsplit(url).port)
        if existing and current.get("api_key") and "api_key" not in model and origin(current.get("base_url", "")) != origin(updated.get("base_url", "")):
            self._fail("INVALID_PARAMS", "更换服务来源时请填写对应的 API Key，或明确清空凭据后保存。")
        with self.service._transaction():
            if existing and updated != current:
                self._assert_connection_inactive(identity)
            if current.get('model_id') and updated.get('model_id') != current.get('model_id'):
                self._assert_model_unused(identity, current['model_id'])
            if identity == "default":
                self._put("model", updated)
            else:
                settings["model_connections"][identity] = {"name": title.strip(), "model": updated}
                self._put("model_connections", settings["model_connections"])
        self.service._update_controller()
        return {"connection_id": identity, **self.list()}

    def remove(self, connection_id):
        settings = self.service._settings()
        if connection_id == "default":
            self._fail("INVALID_PARAMS", "默认连接保留兼容入口，可编辑或切换用途，不可删除。")
        if connection_id not in settings["model_connections"]:
            self._fail("NOT_FOUND", "模型连接不存在。")
        used = [purpose for purpose, identity in settings["model_routes"].items() if identity == connection_id]
        from .capabilities import get as capability_settings
        capabilities = capability_settings(self.service)
        if capabilities["web_enabled"] and capabilities["search_connection"] == connection_id:
            used.append("网页研究")
        if used:
            self._fail("MODEL_IN_USE", "连接正在用于 " + "、".join(used) + "，请先切换这些用途后再删除。")
        with self.service._transaction():
            self._assert_connection_inactive(connection_id)
            for table, condition in (("conversations", "model_selection_json IS NOT NULL"),
                                     ("commands", "model_selection_json IS NOT NULL AND state IN ('admitted','submitted','applied')")):
                for row in self.service.db.execute(f"SELECT model_selection_json FROM {table} WHERE {condition}"):
                    if json.loads(row[0]).get("connection_id") == connection_id:
                        self._fail("MODEL_IN_USE", "连接仍被会话或未完成的请求使用，请先切换会话并等待请求结束。")
            del settings["model_connections"][connection_id]
            self._put("model_connections", settings["model_connections"])
            catalogs = settings.get('model_catalogs', {})
            if connection_id in catalogs:
                del catalogs[connection_id]
                self._put('model_catalogs', catalogs)
        self.service._update_controller()
        return self.list()

    def set_routes(self, routes):
        if not isinstance(routes, dict) or not routes or set(routes) - {"chat", "background"}:
            self._fail("INVALID_PARAMS", "unsupported model purpose")
        settings = self.service._settings()
        for identity in routes.values():
            if not isinstance(identity, str):
                self._fail("INVALID_PARAMS", "invalid model connection identity")
            model = settings["model"] if identity == "default" else settings["model_connections"].get(identity, {}).get("model")
            if model is None:
                self._fail("NOT_FOUND", "指定的模型连接不存在。")
            if not model.get("base_url") or not model.get("model_id"):
                self._fail("INVALID_PARAMS", "请先为该连接保存服务地址和模型 ID。")
        settings["model_routes"].update(routes)
        with self.service._transaction():
            self._put("model_routes", settings["model_routes"])
        self.service._update_controller()
        return self.list()
