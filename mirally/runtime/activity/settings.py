"""Activity preferences and Memory controls for the Mirally desktop shell."""

from __future__ import annotations

import hashlib
import json
import os
import threading
import tempfile
import time
import urllib.parse
import webbrowser
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml

from mirally.core.activity.store import ObservationStore
from mirally.core.memory.store import KINDS, PROVENANCES
from mirally.runtime.memory.config import load_memory_config
from mirally.runtime.memory.service import MemoryReader

from .browser import ActivityBrowserRuntime
from .capture_session import ActivityCaptureSession
from .config import load_activity_config


def validate_memory_search_filters(filters: Mapping[str, Any]) -> dict[str, Any]:
    """Validate optional browser filters before any profile/database access."""
    if set(filters) - {"provenances", "kinds", "start_utc_ms", "end_utc_ms"}:
        raise ValueError("不支持的记忆筛选字段。")
    for field, allowed in (("provenances", PROVENANCES), ("kinds", KINDS)):
        if field not in filters:
            continue
        values = filters[field]
        if (not isinstance(values, list) or len(values) > len(allowed)
                or any(not isinstance(value, str) or value not in allowed for value in values)):
            raise ValueError("记忆来源或类型筛选无效。")
    for field in ("start_utc_ms", "end_utc_ms"):
        if field in filters and (type(filters[field]) is not int or not 0 <= filters[field] <= 2**53 - 1):
            raise ValueError("记忆筛选时间必须是有效的非负整数时间戳。")
    if ("start_utc_ms" in filters and "end_utc_ms" in filters
            and filters["start_utc_ms"] >= filters["end_utc_ms"]):
        raise ValueError("记忆筛选开始时间必须早于结束时间。")
    return dict(filters)


@dataclass(frozen=True)
class _Profile:
    root: Path
    path: Path
    explicit: bool = False


def _path(value: Any, root: Path) -> Path:
    if not isinstance(value, (str, os.PathLike)) or not str(value).strip():
        raise ValueError("配置路径必须是非空字符串")
    result = Path(os.path.expandvars(os.path.expanduser(str(value))))
    return (result if result.is_absolute() else root / result).resolve()


def _argument(args: Any, name: str) -> str | None:
    if not isinstance(args, list):
        return None
    result = None
    for index, value in enumerate(args):
        if value == name:
            if (index + 1 == len(args) or not isinstance(args[index + 1], str)
                    or args[index + 1].startswith("--")):
                raise ValueError(f"MCP 配置缺少 {name} 参数")
            result = args[index + 1]
        elif isinstance(value, str) and value.startswith(name + "="):
            result = value[len(name) + 1:]
    return result


def _mcp_profile(server: Mapping[str, Any], root: Path, kind: str) -> _Profile:
    cwd = _path(server.get("cwd", root), root)
    args = server.get("args", [])
    product_root = _argument(args, "--root")
    if product_root is None:
        product_root = _argument(args, "--product-root")
    effective_root = _path(product_root, cwd) if product_root is not None else root
    configured = _argument(args, "--config")
    if configured is None:
        configured = _argument(args, f"--{kind}-config")
    target = _path(configured, cwd) if configured is not None else effective_root / f"config/mirally/{kind}.yaml"
    return _Profile(effective_root, target, configured is not None)


def _error(error: Exception) -> str:
    if isinstance(error, yaml.YAMLError):
        return "配置 YAML 无法解析，请检查本机配置文件。"
    return str(error)[:500] or type(error).__name__


def _capture_scope(config: Any) -> list[dict[str, Any]]:
    content = config.policy.default_level == "content"
    structure = config.policy.default_level in {"structure", "content"}
    return [{"label": label, "enabled": bool(enabled)} for label, enabled in (
        ("前台窗口元数据", True), ("窗口标题", config.window_titles_enabled),
        ("浏览器地址", config.browser_urls_enabled), ("输入操作元数据", config.input_enabled),
        ("UIA 结构", config.ui_structure_enabled and structure),
        ("屏幕截图（所有显示器）" if config.visual_scope == "monitors" else "屏幕截图（前台窗口）",
         config.visual_enabled and content),
        ("OCR 正文", config.visual_enabled and config.ocr_enabled and content),
        ("UIA 正文", config.ui_structure_enabled and config.uia_content_enabled and content),
        ("输入文本", config.ui_structure_enabled and config.uia_content_enabled and config.input_text_enabled and content),
        ("剪贴板正文", config.input_enabled and config.clipboard_enabled and content))]


def _config_fingerprint(config: Any) -> str:
    """The complete immutable capture config contains no provider secrets."""
    encoded = json.dumps(asdict(config), sort_keys=True, ensure_ascii=False,
                         default=str, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _scope_note(config: Any) -> str:
    policy = config.policy
    return "；".join((
        "截图范围：" + ("所有显示器" if config.visual_scope == "monitors" else "前台窗口"),
        "仅限应用：" + ("、".join(policy.allowed_apps) or "未限制"),
        "排除应用：" + ("、".join(policy.excluded_apps) or "无"),
        "排除窗口：" + ("、".join(policy.excluded_windows) or "无"),
        "排除 Mirally 自身：" + ("是" if policy.exclude_self else "否"),
        "智能体控制时暂停正文：" + ("是" if policy.agent_pause_content else "否"),
        "保存截图文件：" + ("是" if config.visual_persist_images and config.visual_enabled
                          and policy.default_level == "content" else "否")))


class ActivitySettings:
    """Inspect committed data and manage the desktop's capture preference.

    Neither construction nor opening this panel starts collection, maintenance,
    model calls, or a Gateway. The host is used only for official config reads
    and the independent read-only memory status method.
    """

    def __init__(self, root: Path, host: Any = None) -> None:
        self.root = Path(root).resolve()
        self.host = host
        self._lock = threading.RLock()
        self._profiles: tuple[_Profile, _Profile] | None = None
        self._profile_stamp: Any = None
        self._profile_checked = 0.0
        self._profile_info: dict[str, Any] = {}
        self._runtime: ActivityBrowserRuntime | None = None
        self._runtime_key: Any = None
        self._capture = ActivityCaptureSession(self.root)
        self._capture_profile: _Profile | None = None
        self._capture_config: Any = None
        self._auto_start_error: str | None = None
        self._closed = False

    def _config_stamp(self) -> Any:
        path = getattr(self.host, "config", None)
        if not isinstance(path, (str, os.PathLike)):
            path = self.root / "config/mirally/openclaw/openclaw.json"
        try:
            stat = Path(path).stat()
            return (str(path), stat.st_mtime_ns, stat.st_size, id(self.host))
        except OSError:
            return (str(path), None, id(self.host))

    def refresh_profiles(self) -> None:
        """Invalidate cached effective config, including changes in includes."""
        with self._lock:
            self._profiles = None

    def _resolve_profiles(self) -> tuple[_Profile, _Profile]:
        with self._lock:
            if self._closed:
                raise RuntimeError("活动与记忆页面已关闭")
            stamp = self._config_stamp()
            now = time.monotonic()
            if self._profiles and stamp == self._profile_stamp and now - self._profile_checked < 30:
                return self._profiles
            activity = _Profile(self.root, self.root / "config/mirally/activity.yaml")
            memory = _Profile(self.root, self.root / "config/mirally/memory.yaml")
            servers: Mapping[str, Any] = {}
            plugin: Mapping[str, Any] = {}
            warnings = []
            source = "defaults"
            activity_verified = self.host is None
            memory_verified = self.host is None
            effective_model = None
            if self.host is None:
                warnings.append("未连接本机配置宿主；当前仅检查默认配置与已有本地数据。")
            else:
                combined_read = getattr(self.host, "activity_profiles", None)
                combined, combined_error = None, None
                if callable(combined_read):
                    try:
                        combined = combined_read()
                        if not isinstance(combined, Mapping):
                            raise ValueError("有效设置快照无法读取")
                    except Exception as error:
                        combined_error = error

                def read_config(key: str, path: str):
                    if callable(combined_read):
                        if combined_error is not None:
                            raise combined_error
                        return combined[key]
                    return self.host._native_json("config", "get", path, "--json")

                try:
                    servers = read_config("servers", "mcp.servers")
                    if not isinstance(servers, Mapping):
                        raise ValueError("有效 MCP 配置无法读取")
                    activity_verified = True
                    source = "openclaw"
                except Exception:
                    servers = {}
                    warnings.append("无法读取有效 MCP 配置；Activity 当前检查默认配置。")
                try:
                    plugin = read_config("plugin", "plugins.entries.mirally-memory.config")
                    if not isinstance(plugin, Mapping):
                        raise ValueError("有效 Memory 配置无法读取")
                    memory_verified = True
                    source = "openclaw"
                except Exception:
                    plugin = {}
                    warnings.append("未能读取 Memory 插件配置；使用已知 MCP profile 或默认配置。")
                try:
                    defaults = read_config("defaults", "agents.defaults")
                    model = defaults.get("model")
                    effective_model = model if isinstance(model, str) else model.get("primary") if isinstance(model, Mapping) else None
                except Exception:
                    pass
            activity_error = memory_error = None
            try:
                server = servers.get("mirally-activity")
                if isinstance(server, Mapping):
                    activity = _mcp_profile(server, self.root, "activity")
            except (TypeError, ValueError) as error:
                activity_error = _error(error)
            try:
                server = servers.get("mirally-memory")
                if isinstance(server, Mapping):
                    memory = _mcp_profile(server, self.root, "memory")
                if plugin:
                    plugin_root = _path(plugin.get("root", self.root), self.root)
                    configured = plugin.get("configPath")
                    memory = _Profile(plugin_root,
                        _path(configured, plugin_root) if configured is not None else
                        plugin_root / "config/mirally/memory.yaml", configured is not None)
            except (TypeError, ValueError) as error:
                memory_error = _error(error)
            self._profiles = (activity, memory)
            self._profile_stamp = stamp
            self._profile_checked = now
            self._profile_info = {"source": source, "warning": " ".join(warnings) or None,
                                  "activity_error": activity_error, "memory_error": memory_error,
                                  "activity_profile_verified": activity_verified}
            self._profile_info["memory_profile_verified"] = memory_verified
            self._profile_info["effective_model"] = effective_model
            return self._profiles

    def _require_profile(self, profile: _Profile, kind: str) -> None:
        failure = self._profile_info.get(kind + "_error")
        if failure:
            raise ValueError(failure)
        default_path = (profile.root / f"config/mirally/{kind}.yaml").resolve()
        if profile.explicit and profile.path.resolve() != default_path and not profile.path.is_file():
            raise FileNotFoundError(f"指定的 {kind} 配置文件不存在：{profile.path}")

    def _require_capture_profile(self) -> None:
        if self.host is not None and not self._profile_info.get("activity_profile_verified", False):
            raise RuntimeError("无法确认有效活动配置；请恢复本机配置连接后重试，不会使用默认范围开始采集。")

    @staticmethod
    def _pause_reason(collector: Mapping[str, Any], diagnostics: Mapping[str, Any], pause: Mapping[str, Any]) -> str | None:
        if collector.get("status") != "paused":
            return None
        intervals = diagnostics.get("pause_intervals") or []
        if intervals and isinstance(intervals[-1], Mapping) and intervals[-1].get("ended_at_utc_ms") is None:
            reason = intervals[-1].get("reason")
        else:
            reason = pause.get("reason") if pause.get("paused") is True else None
        return reason if isinstance(reason, str) else None

    def _activity_status(self, profile: _Profile, *, read_state: bool = True) -> dict[str, Any]:
        result: dict[str, Any] = {"enabled": False, "configured": profile.path.is_file(),
            "config_path": str(profile.path), "database": None, "database_exists": False,
            "collection_level": None, "capture": [], "collector": {"status": "unknown",
            "snapshot": True, "updated_at_utc_ms": None, "pause_reason": None}, "health": None,
            "diagnostics": {}, "knowledge_database": None, "knowledge_exists": False,
            "config_fingerprint": None, "scope_note": None, "error": None}
        try:
            self._require_profile(profile, "activity")
            config = load_activity_config(profile.root, profile.path)
            knowledge = config.knowledge_database_path or config.database_path.with_name("activity-knowledge.sqlite3")
            result.update(enabled=config.enabled, database=str(config.database_path),
                database_exists=config.database_path.is_file(), collection_level=config.policy.default_level,
                knowledge_database=str(knowledge), knowledge_exists=knowledge.is_file(),
                capture=_capture_scope(config), config_fingerprint=_config_fingerprint(config),
                scope_note=_scope_note(config))
            if read_state and config.database_path.is_file():
                with ObservationStore(config.database_path, asset_directory=config.asset_directory, read_only=True) as store:
                    collector = store.get_state("collector", {})
                    diagnostics = store.get_state("collector_diagnostics", {})
                    pause = store.get_state("collector_pause", {})
                    status = diagnostics.get("status") or collector.get("status") or "unknown"
                    if pause.get("paused") and status not in {"stopped", "disabled", "unknown"}:
                        status = "paused"
                    result.update(health=store.health().__dict__, diagnostics=diagnostics,
                        collector={**collector, "status": status, "snapshot": True,
                            "pause_reason": self._pause_reason({**collector, "status": status}, diagnostics, pause),
                            "updated_at_utc_ms": diagnostics.get("updated_at_utc_ms", collector.get("at"))})
        except Exception as error:
            result["error"] = _error(error)
        return result

    def _memory_runtime(self) -> dict[str, Any]:
        result: dict[str, Any] = {"verified": False, "status": "unknown", "running": None,
            "ready": None, "maintenance_running": None, "maintenance_status": None, "semantic": None}
        if self.host is None:
            return result
        try:
            read_runtime = getattr(self.host, "memory_runtime_status", None)
            status = (read_runtime() if callable(read_runtime) else
                self.host._native_json("gateway", "call", "mirally.memory.status",
                                       "--params", "{}", "--timeout", "3000", "--json"))
            if type(status.get("running")) is not bool or type(status.get("ready")) is not bool:
                raise ValueError("Memory runtime status is unavailable")
            worker = status.get("worker") or {}
            state = "running" if status["running"] and status["ready"] else "starting" if status["running"] else "stopped"
            result.update(verified=True, status=state,
                running=status["running"], ready=status["ready"],
                maintenance_running=worker.get("maintenanceRunning"),
                maintenance_status=worker.get("maintenanceStatus"), semantic=status.get("semantic"),
                semantic_maintenance=worker.get("semanticMaintenance"), settings=status.get("settings"),
                error=worker.get("lastError"))
        except Exception:
            result["error"] = "尚未确认后台 Memory 服务状态；本地存储状态仍可单独查看。"
        return result

    def _memory_status(self, profile: _Profile, activity: _Profile) -> dict[str, Any]:
        result: dict[str, Any] = {"enabled": False, "configured": profile.path.is_file(),
            "config_path": str(profile.path), "database": None, "activity_config_path": None, "activity_database": None,
            "source_matches_activity": None, "health": None, "semantics": None, "raw_retention": None,
            "runtime": self._memory_runtime(), "error": None}
        try:
            self._require_profile(profile, "memory")
            config = load_memory_config(profile.root, profile.path)
            source = load_activity_config(profile.root, config.activity_config_path)
            displayed_source = load_activity_config(activity.root, activity.path)
            result.update(enabled=config.enabled, database=str(config.database_path), activity_config_path=str(config.activity_config_path),
                activity_database=str(source.database_path),
                source_matches_activity=source.database_path.resolve() == displayed_source.database_path.resolve(),
                health=MemoryReader(profile.root, config=config).handle("health"),
                semantics=config.settings()["semantics"])
            result["semantics"]["effective_model"] = config.semantics.model or self._profile_info.get("effective_model")
            result["raw_retention"] = {**config.settings().get("raw_retention", {}),
                                       **result["health"].get("raw_retention", {}),
                                       "configured_enabled": config.raw_retention.enabled}
        except Exception as error:
            result["error"] = _error(error)
        result["controls"] = {"set-semantics": not result["error"] and (
            self.host is None or self._profile_info.get("memory_profile_verified", False))}
        return result

    def status(self) -> dict[str, Any]:
        with self._lock:
            activity, memory = self._resolve_profiles()
            owned = self._capture.status()
            owned_live = owned is not None and owned.get("controls", {}).get("stop")
            activity_status = self._activity_status(activity, read_state=not owned_live)
            can_control = not bool(activity_status["error"]) and (
                self.host is None or self._profile_info.get("activity_profile_verified", False))
            activity_status.update(live_enabled=False, config_changed=False, warning=None, session_id=None,
                auto_start=activity_status["enabled"], auto_start_error=self._auto_start_error,
                controls={"start": can_control, "set-enabled": can_control, "pause": False,
                          "resume": False, "stop": False})
            runtime = self._capture.runtime
            if owned_live:
                active_config = runtime.config if runtime is not None else self._capture_config
                pause = {}
                if active_config.database_path.is_file():
                    try:
                        with ObservationStore(active_config.database_path, asset_directory=active_config.asset_directory, read_only=True) as store:
                            pause = store.get_state("collector_pause", {})
                    except Exception:
                        pass
                live_collector = {**owned["collector"], "pause_reason": self._pause_reason(
                    owned["collector"], owned.get("collector_diagnostics", {}), pause)}
                changed = True
                try:
                    current = load_activity_config(activity.root, activity.path)
                    changed = self._capture_profile != activity or self._capture_config != current
                except Exception:
                    pass
                activity_status.update(collector=live_collector, controls={**owned["controls"],
                    "set-enabled": can_control},
                    session_id=owned.get("session_id"),
                    live_enabled=bool(owned["collector"].get("live_enabled", self._capture.url)),
                    configured_config_path=str(activity.path), config_path=str(self._capture_profile.path),
                    config_changed=changed, database=owned["database"],
                    database_exists=active_config.database_path.is_file(),
                    collection_level=active_config.policy.default_level, capture=_capture_scope(active_config),
                    config_fingerprint=_config_fingerprint(self._capture_config), scope_note=_scope_note(active_config),
                    health=owned.get("health"), diagnostics=owned.get("collector_diagnostics", {}),
                    warning="配置已变化；本次采集仍沿用开始时的范围。请先停止，再重新开始以应用新配置。" if changed else None)
                monitor = owned.get("monitor", {})
                if monitor.get("shutdown_error") or monitor.get("status_error"):
                    activity_status["error"] = monitor.get("shutdown_error") or monitor.get("status_error")
            memory_status = self._memory_status(memory, activity)
            if memory_status["activity_database"] is not None and activity_status["database"] is not None:
                memory_status["source_matches_activity"] = (
                    Path(memory_status["activity_database"]).resolve() == Path(activity_status["database"]).resolve())
            return {"activity": activity_status, "memory": memory_status, "profiles": dict(self._profile_info)}

    def open_browser(self, mode: str = "history") -> dict[str, Any]:
        if mode not in {"history", "monitor"}:
            raise ValueError("不支持的活动页面")
        with self._lock:
            if self._closed:
                raise RuntimeError("活动与记忆页面已关闭")
            self._capture.status()
            if mode == "monitor" and self._capture.url:
                if not webbrowser.open(self._capture.url):
                    raise RuntimeError("默认浏览器未能打开本次采集监测页；请检查系统浏览器设置。")
                return {"opened": True, "mode": mode}
            profile, _ = self._resolve_profiles()
            self._require_profile(profile, "activity")
            config = load_activity_config(profile.root, profile.path)
            if not config.database_path.is_file():
                raise FileNotFoundError("尚无 Activity 历史数据库；打开页面不会启动采集。")
            knowledge = config.knowledge_database_path or config.database_path.with_name("activity-knowledge.sqlite3")
            key = (profile.root, profile.path, config, knowledge.is_file())
            if self._runtime and (key != self._runtime_key or not self._runtime.url):
                self._runtime.close()
                self._runtime = None
            if self._runtime is None:
                runtime = ActivityBrowserRuntime(config, root=profile.root, sync=False, require_knowledge=False)
                try:
                    runtime.start()
                except BaseException:
                    runtime.close()
                    raise
                self._runtime, self._runtime_key = runtime, key
            url = self._runtime.url
            if mode == "monitor":
                parsed = urllib.parse.urlsplit(url)
                url = urllib.parse.urlunsplit(parsed._replace(path="/monitor"))
            if not webbrowser.open(url):
                raise RuntimeError("本机 Activity 页面已准备，但默认浏览器未能打开；请检查系统浏览器设置。")
        return {"opened": True, "mode": mode}

    def start_capture(self, expected_activity: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Start this window's capture, preserving the saved preference."""
        with self._lock:
            self.refresh_profiles()
            profile, _ = self._resolve_profiles()
            self._require_capture_profile()
            self._require_profile(profile, "activity")
            config = load_activity_config(profile.root, profile.path)
            actual = {"config_path": str(profile.path), "database": str(config.database_path),
                      "collection_level": config.policy.default_level, "capture": _capture_scope(config),
                      "config_fingerprint": _config_fingerprint(config)}
            if expected_activity is not None and any(expected_activity.get(key) != value
                                                    for key, value in actual.items()):
                raise RuntimeError("活动配置或采集范围已变化；请刷新页面并重新开始。")
            self._capture.status()
            if (self._capture.runtime is not None and
                    (self._capture_profile != profile or self._capture_config != config)):
                raise RuntimeError("请先停止本次采集，再应用新的活动配置。")
            self._capture_profile, self._capture_config = profile, config
            result = self._capture.start(config)
            self._auto_start_error = None
            return result

    def auto_start_capture(self) -> dict[str, Any] | None:
        """Desktop startup only; reading configuration never invokes this."""
        with self._lock:
            if self._closed:
                return None
            try:
                profile, _ = self._resolve_profiles()
                self._require_capture_profile()
                self._require_profile(profile, "activity")
                if not load_activity_config(profile.root, profile.path).enabled:
                    self._auto_start_error = None
                    return None
                return self.start_capture()
            except Exception as error:
                self._auto_start_error = _error(error)
                return None

    def set_enabled(self, enabled: bool) -> dict[str, Any]:
        """Save only the enabled preference, then apply it to this window.

        The file replacement is atomic. Existing configuration values (including
        custom scope, paths, and unknown extensions) are retained. A failed
        collector start or stop does not erase the successfully saved preference.
        """
        if type(enabled) is not bool:
            raise ValueError("活动记录开关必须是布尔值")
        with self._lock:
            self.refresh_profiles()
            profile, _ = self._resolve_profiles()
            self._require_capture_profile()
            self._require_profile(profile, "activity")
            # Validate before writing so an invalid private profile is never
            # replaced with defaults or partially repaired by a UI toggle.
            load_activity_config(profile.root, profile.path)
            path = profile.path
            original = path.read_text(encoding="utf-8") if path.is_file() else None
            data = yaml.safe_load(original) if original is not None else {}
            data = dict(data or {})
            data["enabled"] = enabled
            serialized = yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
            if original is not None:
                # Preserve the user's formatting/comments for the normal
                # scalar switch. Exotic aliases/flow documents use the parsed
                # mapping fallback above, retaining every other value.
                document = yaml.compose(original)
                fields = [(key, value) for key, value in getattr(document, "value", ())
                          if getattr(key, "value", None) == "enabled"]
                if len(fields) == 1:
                    key, value = fields[0]
                    token = original[value.start_mark.index:value.end_mark.index]
                    if (value.start_mark.index >= key.end_mark.index and
                            token.lower() in {"true", "false", "yes", "no", "on", "off"}):
                        serialized = (original[:value.start_mark.index] + str(enabled).lower()
                                      + original[value.end_mark.index:])
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                        prefix=path.name + ".", suffix=".tmp", delete=False) as stream:
                    temporary = Path(stream.name)
                    stream.write(serialized)
                    stream.flush()
                    os.fsync(stream.fileno())
                load_activity_config(profile.root, temporary)
                current = path.read_text(encoding="utf-8") if path.is_file() else None
                if current != original:
                    raise RuntimeError("活动配置已被其他操作修改，请刷新后重试。")
                os.replace(temporary, path)
                temporary = None
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
            result = {"saved": True, "enabled": enabled, "capture": None, "capture_error": None}
            try:
                # Retire a prior scope before starting the freshly read profile.
                if self._capture.runtime is not None:
                    self._capture.stop()
                if enabled:
                    result["capture"] = self.start_capture()
                self._auto_start_error = None
            except Exception as error:
                self._auto_start_error = result["capture_error"] = _error(error)
            return result

    def model_settings(self) -> dict[str, Any]:
        """Read model-routing preferences without querying captured data."""
        with self._lock:
            _, profile = self._resolve_profiles()
            result = {"enabled": False, "model": "", "effective_model": None,
                      "max_output_tokens": 1500, "max_calls_per_hour": None,
                      "max_tokens_per_day": None, "canSave": False, "runtime": None, "error": None}
            try:
                self._require_profile(profile, "memory")
                config = load_memory_config(profile.root, profile.path)
                semantics = config.semantics
                result.update(enabled=semantics.enabled, model=semantics.model or "",
                    effective_model=semantics.model or self._profile_info.get("effective_model"),
                    max_output_tokens=semantics.max_output_tokens,
                    max_calls_per_hour=semantics.max_calls_per_hour,
                    max_tokens_per_day=semantics.max_tokens_per_day,
                    canSave=self.host is None or self._profile_info.get("memory_profile_verified", False),
                    runtime=self._memory_runtime())
                if not result["canSave"]:
                    result["error"] = "无法确认有效 Memory 配置；请恢复本机配置连接后重试。"
            except Exception as error:
                result["error"] = _error(error)
            return result

    def set_semantic_model(self, model: str | None = None, max_output_tokens: int | None = None) -> dict[str, Any]:
        """Change only the semantic model/output limit, preserving enablement."""
        if model is None and max_output_tokens is None:
            raise ValueError("请至少提供整理模型或模型输出上限。")
        return self._save_semantics(None, model=model, model_provided=model is not None,
                                    max_output_tokens=max_output_tokens)

    def set_semantics(self, enabled: bool, *, model: str | None = None,
                      max_calls_per_hour: int | None = None,
                      max_tokens_per_day: int | None = None,
                      max_output_tokens: int | None = None,
                      model_provided: bool = False) -> dict[str, Any]:
        if type(enabled) is not bool:
            raise ValueError("模型语义整理开关必须是布尔值")
        return self._save_semantics(enabled, model=model, max_calls_per_hour=max_calls_per_hour,
            max_tokens_per_day=max_tokens_per_day, max_output_tokens=max_output_tokens,
            model_provided=model_provided)

    def _save_semantics(self, enabled: bool | None, *, model: str | None = None,
                        max_calls_per_hour: int | None = None,
                        max_tokens_per_day: int | None = None,
                        max_output_tokens: int | None = None,
                        model_provided: bool = False) -> dict[str, Any]:
        """Save explicit semantic preferences and reload the existing worker.

        No worker is constructed here and no model is invoked by this bridge.
        The already running Gateway service owns scheduling and completions.
        """
        if model_provided and (not isinstance(model, str) or len(model) > 512 or any(char.isspace() for char in model.strip())):
            raise ValueError("整理模型须填写完整模型标识，留空沿用宿主模型。")
        for value, name, maximum in ((max_calls_per_hour, "每小时调用预算", 10000),
                                     (max_tokens_per_day, "每日 Token 预算", 10000000)):
            if value is not None and (type(value) is not int or not 0 <= value <= maximum):
                raise ValueError(f"{name}无效")
        if max_output_tokens is not None and (type(max_output_tokens) is not int or not 128 <= max_output_tokens <= 16000):
            raise ValueError("模型输出上限必须是 128 到 16000 之间的整数")
        with self._lock:
            self.refresh_profiles()
            _, profile = self._resolve_profiles()
            if self.host is not None and not self._profile_info.get("memory_profile_verified", False):
                raise RuntimeError("无法确认有效 Memory 配置；请恢复本机配置连接后重试。")
            self._require_profile(profile, "memory")
            load_memory_config(profile.root, profile.path)
            original = profile.path.read_text(encoding="utf-8") if profile.path.is_file() else None
            data = dict(yaml.safe_load(original) or {}) if original is not None else {}
            semantics = dict(data.get("semantics") or {})
            if enabled is not None:
                semantics["enabled"] = enabled
            if model_provided:
                semantics["model"] = model.strip() or None
            if max_calls_per_hour is not None:
                semantics["max_calls_per_hour"] = max_calls_per_hour
            if max_tokens_per_day is not None:
                semantics["max_tokens_per_day"] = max_tokens_per_day
            if max_output_tokens is not None:
                semantics["max_output_tokens"] = max_output_tokens
            data["semantics"] = semantics
            profile.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=profile.path.parent,
                        prefix=profile.path.name + ".", suffix=".tmp", delete=False) as stream:
                    temporary = Path(stream.name)
                    stream.write(yaml.safe_dump(data, allow_unicode=True, sort_keys=False))
                    stream.flush()
                    os.fsync(stream.fileno())
                validated = load_memory_config(profile.root, temporary)
                current = profile.path.read_text(encoding="utf-8") if profile.path.is_file() else None
                if current != original:
                    raise RuntimeError("Memory 配置已被其他操作修改，请刷新后重试。")
                os.replace(temporary, profile.path)
                temporary = None
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
            result = {"saved": True, "semantics": validated.settings()["semantics"],
                      "applied": False, "reload_error": None}
            try:
                if self.host is None:
                    raise RuntimeError("未连接本机 Gateway；设置已保存，后台运行时尚未应用。")
                reply = self.host._native_json("gateway", "call", "mirally.memory.reloadSettings",
                    "--params", "{}", "--timeout", "5000", "--json")
                if reply.get("applied") is not True:
                    raise RuntimeError("后台未确认应用新设置，请重新连接后重试。")
                if reply.get("settings", {}).get("semantics") != result["semantics"]:
                    raise RuntimeError("后台整理设置与已保存配置不一致，请检查 Memory 配置路径后重试。")
                result["applied"] = True
            except Exception as error:
                result["reload_error"] = _error(error)
            return result

    def _capture_action(self, action: str, expected_session_id: str | None = None) -> dict[str, Any] | None:
        with self._lock:
            if self._closed:
                raise RuntimeError("活动与记忆页面已关闭")
            return getattr(self._capture, action)(expected_session_id=expected_session_id)

    def pause_capture(self, expected_session_id: str | None = None) -> dict[str, Any] | None:
        return self._capture_action("pause", expected_session_id)

    def resume_capture(self, expected_session_id: str | None = None) -> dict[str, Any] | None:
        return self._capture_action("resume", expected_session_id)

    def stop_capture(self, expected_session_id: str | None = None) -> dict[str, Any] | None:
        return self._capture_action("stop", expected_session_id)

    def _memory_reader(self) -> MemoryReader:
        _, profile = self._resolve_profiles()
        self._require_profile(profile, "memory")
        return MemoryReader(profile.root, profile.path)

    def memory_search(self, query: str = "", include_candidates: bool = False, limit: int = 10,
                      offset: int = 0, **filters: Any) -> dict[str, Any]:
        if not isinstance(query, str):
            raise ValueError("记忆查询必须是文字")
        validated = validate_memory_search_filters(filters)
        return self._memory_reader().handle("search", {"query": query,
            "include_candidates": include_candidates, "limit": limit, "offset": offset, **validated})

    def memory_read(self, memory_id: str, from_line: int = 1, line_count: int = 100) -> dict[str, Any]:
        if not isinstance(memory_id, str):
            raise ValueError("记忆 ID 必须是文字")
        return self._memory_reader().handle("read", {"id": memory_id,
            "from_line": from_line, "line_count": line_count})

    def close(self) -> None:
        with self._lock:
            self._closed = True
            try:
                self._capture.close()
            finally:
                if self._runtime is not None:
                    self._runtime.close()
                    self._runtime = None


__all__ = ["ActivitySettings"]
