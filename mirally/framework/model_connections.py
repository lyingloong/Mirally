"""Named model configuration belongs to the product, independently of conversations."""
from __future__ import annotations

from copy import deepcopy
import json
import re
from urllib.parse import urlsplit
import uuid


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
        return {"connections": [{"id": "default", "name": "默认连接", "model": settings["model"]},
                                *[{"id": identity, **connection} for identity, connection in settings["model_connections"].items()]],
                "routes": settings["model_routes"]}

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
        origin = lambda url: (urlsplit(url).scheme, urlsplit(url).hostname, urlsplit(url).port)
        if existing and current.get("api_key") and "api_key" not in model and origin(current.get("base_url", "")) != origin(updated.get("base_url", "")):
            self._fail("INVALID_PARAMS", "更换服务来源时请填写对应的 API Key，或明确清空凭据后保存。")
        with self.service._transaction():
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
        del settings["model_connections"][connection_id]
        with self.service._transaction():
            self._put("model_connections", settings["model_connections"])
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
