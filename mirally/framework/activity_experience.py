"""Owned Activity/Memory controls which reuse the existing read-only viewers."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import tempfile
from typing import Any

import yaml

from mirally.runtime.activity.config import load_activity_config
from .activity_views import capture_scope, scope_note, validate_memory_search_filters


CAPTURE_FIELDS = {
    "visual_enabled": ("visual", "enabled"), "visual_scope": ("visual", "scope"),
    "persist_images": ("visual", "persist_images"), "ocr_enabled": ("visual", "ocr_enabled"),
    "input_enabled": ("input", "enabled"), "input_text_enabled": ("input", "text"),
    "clipboard_enabled": ("input", "clipboard"), "ui_structure_enabled": ("ui_structure", "enabled"),
    "uia_content_enabled": ("ui_structure", "content"),
    "window_titles_enabled": ("collection", "window_titles"), "browser_urls_enabled": ("collection", "browser_urls"),
    "capture_level": ("collection", "default_level"), "exclude_apps": ("privacy", "excluded_apps"),
    "exclude_window_titles": ("privacy", "excluded_windows"), "allowed_apps": ("privacy", "allowed_apps"),
    "capture_interval_seconds": ("visual", "interval_seconds"),
}


def validate_capture_patch(patch: Any) -> dict[str, Any]:
    if not isinstance(patch, dict) or set(patch) - (set(CAPTURE_FIELDS) | {"poll_interval_seconds"}):
        raise ValueError("Unsupported Activity capture field")
    for key, value in patch.items():
        if key == "visual_scope":
            if value not in {"foreground", "monitors"}: raise ValueError("Choose foreground or monitors")
        elif key == "capture_level":
            if value not in {"metadata", "structure", "content"}: raise ValueError("Choose metadata, structure or content")
        elif key in {"exclude_apps", "exclude_window_titles", "allowed_apps"}:
            if not isinstance(value, list) or len(value) > 100 or any(not isinstance(item, str) or not item.strip() or len(item) > 500 for item in value):
                raise ValueError("Activity application/window patterns must be bounded strings")
        elif key in {"poll_interval_seconds", "capture_interval_seconds"}:
            low, high = (0.25, 10) if key == "poll_interval_seconds" else (1, 60)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= value <= high:
                raise ValueError(f"{key} is outside the supported range")
        elif not isinstance(value, bool):
            raise ValueError(f"{key} must be boolean")
    return deepcopy(patch)


def activity_profile(root: Path, home: Path, settings: dict[str, Any], *, capture: bool = False) -> dict[str, Any]:
    explicit = settings.get("activity_config_path")
    if explicit:
        path = Path(explicit).expanduser()
        path = path if path.is_absolute() else root / path
        value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(value, dict): raise ValueError("Activity profile must be a YAML mapping")
    else:
        value = {"storage": {"directory": str(home / "data/activity"),
                             "database_path": str(home / "data/activity/observations.sqlite3"),
                             "knowledge_database_path": str(home / "data/activity/knowledge.sqlite3")}}
    value = deepcopy(value)
    for key, setting in settings.get("activity_capture", {}).items():
        if key == "poll_interval_seconds": value[key] = setting
        else:
            section, field = CAPTURE_FIELDS[key]
            value.setdefault(section, {})[field] = deepcopy(setting)
    value["enabled"] = bool(settings.get("activity_enabled", False)) and not (capture and settings.get("activity_paused", False))
    return value


def configured_activity(root: Path, home: Path, settings: dict[str, Any], *, prospective: bool = False):
    """Use the actual profile parser without competing for the owned YAML writer."""
    data = activity_profile(root, home, settings)
    if prospective: data["enabled"] = True
    with tempfile.TemporaryDirectory(prefix="mirally-capture-profile-") as temporary:
        path = Path(temporary) / "activity.yaml"
        path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
        return load_activity_config(root, path)


class ActivityExperience:
    def __init__(self, product) -> None:
        self.product = product
        self.browser = None
        self.browser_fingerprint = None

    def config(self, *, prospective: bool = False):
        product = self.product
        return configured_activity(product.root, product.home, product._settings(), prospective=prospective)

    def status(self) -> dict[str, Any]:
        product = self.product
        config = self.config(prospective=True)
        fingerprint = hashlib.sha256(json.dumps(asdict(config), sort_keys=True, default=str).encode()).hexdigest()
        snapshot = {}
        if config.database_path.is_file():
            from mirally.core.activity.store import ObservationStore
            with ObservationStore(config.database_path, asset_directory=config.asset_directory, read_only=True) as store:
                snapshot = {"health": asdict(store.health()), "diagnostics": store.get_state("collector_diagnostics", {}),
                            "collector": store.get_state("collector", {}), "pause": store.get_state("collector_pause", {})}
        settings = product._settings()
        controller = product._controller_status()
        workers = controller.get("status") if isinstance(controller.get("status"), dict) else controller
        return {"enabled": settings["activity_enabled"], "paused": settings.get("activity_paused", False),
                "fingerprint": fingerprint, "scope": capture_scope(config), "scope_note": scope_note(config),
                "capture": self.capture_values(config), "workers": workers,
                "snapshot": snapshot, "snapshot_is_live_status": False,
                "profiles": {"activity": str(product.home / "config/activity.yaml"), "database": str(config.database_path),
                             "knowledge_database": str(config.knowledge_database_path)}}

    @staticmethod
    def capture_values(config) -> dict[str, Any]:
        return {"visual_enabled": config.visual_enabled, "visual_scope": config.visual_scope,
                "persist_images": config.visual_persist_images, "ocr_enabled": config.ocr_enabled,
                "input_enabled": config.input_enabled, "input_text_enabled": config.input_text_enabled,
                "clipboard_enabled": config.clipboard_enabled, "ui_structure_enabled": config.ui_structure_enabled,
                "uia_content_enabled": config.uia_content_enabled, "window_titles_enabled": config.window_titles_enabled,
                "browser_urls_enabled": config.browser_urls_enabled, "capture_level": config.policy.default_level,
                "exclude_apps": list(config.policy.excluded_apps), "exclude_window_titles": list(config.policy.excluded_windows),
                "allowed_apps": list(config.policy.allowed_apps), "poll_interval_seconds": config.poll_interval_seconds,
                "capture_interval_seconds": config.capture_interval_seconds}

    def configure(self, patch: dict[str, Any]) -> dict[str, Any]:
        product = self.product
        values = {**product._settings().get("activity_capture", {}), **validate_capture_patch(patch)}
        # Expanding capture must never silently restart a running collector.
        product.settings_update({"activity_capture": values, "activity_enabled": False, "activity_paused": False})
        return {**self.status(), "requires_confirmation": True}

    def start(self, confirmed: bool = False, expected_fingerprint: str | None = None) -> dict[str, Any]:
        from .service import FrameworkError
        status = self.status()
        if confirmed is not True or expected_fingerprint != status["fingerprint"]:
            raise FrameworkError("CONFIRMATION_REQUIRED", "Review the current capture scope and confirm it before starting")
        if not self.product._source("activity")["enabled"]:
            raise FrameworkError("FORBIDDEN", "Activity source is withdrawn")
        self.product.settings_update({"activity_enabled": True, "activity_paused": False})
        return {**self.status(), "requested": "start"}

    def pause(self) -> dict[str, Any]:
        self.product.settings_update({"activity_paused": True})
        return {**self.status(), "requested": "pause", "note": "Pausing releases the collector; resume starts the same approved scope again"}

    def resume(self, confirmed: bool = False, expected_fingerprint: str | None = None) -> dict[str, Any]:
        return self.start(confirmed=confirmed, expected_fingerprint=expected_fingerprint)

    def stop(self) -> dict[str, Any]:
        self.product.settings_update({"activity_enabled": False, "activity_paused": False})
        return {**self.status(), "requested": "stop"}

    def browse(self, mode: str = "history") -> dict[str, Any]:
        if mode not in {"history", "monitor"}: raise ValueError("Choose history or monitor")
        from mirally.runtime.activity.browser import ActivityBrowserRuntime
        config = self.config()
        fingerprint = (str(config.database_path), str(config.knowledge_database_path), str(config.asset_directory))
        if not config.database_path.is_file(): raise ValueError("No recorded Activity database is available yet")
        if self.browser is not None and self.browser_fingerprint != fingerprint:
            self.browser.close()
            self.browser = None
        if self.browser is None:
            self.browser = ActivityBrowserRuntime(config, root=self.product.root, sync=False, require_knowledge=False)
            self.browser_fingerprint = fingerprint
            self.browser.start()
        return {"url": self.browser.url + ("#diagnostics" if mode == "monitor" else ""), "mode": mode,
                "readonly": True, "collector_started": False, "controls_enabled": False}

    def close(self) -> None:
        if self.browser is not None:
            self.browser.close()
            self.browser = None

    def memory_search(self, query: str = "", include_candidates: bool = False, limit: int = 10,
                      offset: int = 0, **filters) -> dict[str, Any]:
        from .service import FrameworkError, _integer, _text
        from .context import memory_config
        from mirally.runtime.memory.service import MemoryReader
        _text(query, "query", 2048, empty=True)
        _integer(limit, "limit", 1, 50)
        _integer(offset, "offset", 0, 100000)
        if not isinstance(include_candidates, bool): raise ValueError("include_candidates must be boolean")
        valid = validate_memory_search_filters(filters)
        settings = self.product._settings()
        if not settings["memory_enabled"] or not self.product._source("memory")["enabled"]:
            return {"status": "disabled", "memories": [], "next_offset": None}
        result = MemoryReader(self.product.root, config=memory_config(self.product.root, self.product.home, settings)).handle("search", {
            "query": query, "include_candidates": include_candidates, "limit": limit, "offset": offset, **valid})
        kept = []
        for memory in result.get("memories", []):
            try:
                refs = self.product._memory_source_refs(memory, allow_candidate=include_candidates)
            except FrameworkError:
                continue
            if not self.product._source_reasons(refs): kept.append(memory)
        return {**result, "memories": kept, "candidate_promotion": False}

    def memory_read(self, memory_id: str, from_line: int = 1, line_count: int = 100,
                    include_candidates: bool = False) -> dict[str, Any]:
        from .service import FrameworkError, _integer, _text
        from .context import memory_config
        from mirally.core.memory.store import MemoryStore
        from mirally.runtime.memory.service import MemoryReader
        _text(memory_id, "memory_id")
        _integer(from_line, "from_line", 1, 100000)
        _integer(line_count, "line_count", 1, 200)
        if not isinstance(include_candidates, bool): raise ValueError("include_candidates must be boolean")
        product, settings = self.product, self.product._settings()
        if not settings["memory_enabled"] or not product._source("memory")["enabled"]:
            raise FrameworkError("FORBIDDEN", "Memory is disabled")
        config = memory_config(product.root, product.home, settings)
        if not config.database_path.is_file(): return {"status": "not_ready", "memory": None, "text": ""}
        with MemoryStore(config.database_path, read_only=True) as store:
            record = store.get_memory(memory_id, include_inactive=include_candidates)
        if record is None or record.get("status") not in ({"active", "candidate"} if include_candidates else {"active"}):
            return {"status": "not_found", "memory": None, "text": ""}
        refs = product._memory_source_refs(record, allow_candidate=include_candidates)
        if product._source_reasons(refs): raise FrameworkError("FORBIDDEN", "Memory contains withdrawn sources")
        if record.get("status") != "candidate":
            return MemoryReader(product.root, config=config).handle("read", {"memory_id": memory_id, "from_line": from_line, "line_count": line_count})
        lines = [chunk for line in str(record.get("content", "")).splitlines()
                 for chunk in ([line[index:index + 4096] for index in range(0, len(line), 4096)] or [""])]
        selected = []
        for line in lines[from_line - 1:from_line - 1 + line_count]:
            if len("\n".join([*selected, line])) > 24000: break
            selected.append(line)
        bounded = MemoryReader._bounded_record({**record, "content": ""}, 8000)
        return {"status": "ready", "memory": bounded, "text": "\n".join(selected), "from_line": from_line,
                "line_count": len(selected), "total_lines": len(lines), "truncated": from_line - 1 + len(selected) < len(lines),
                "candidate_promotion": False}

    def memory_status(self) -> dict[str, Any]:
        from .context import memory_config
        from mirally.runtime.memory.service import MemoryReader
        product = self.product
        result = MemoryReader(product.root, config=memory_config(product.root, product.home, product._settings())).handle("health")
        controller = product._controller_status()
        workers = controller.get("status") if isinstance(controller.get("status"), dict) else controller
        return {**result, "workers": workers, "budgets": product._settings()["memory_semantics"]}

    def memory_configure(self, patch: dict[str, Any]) -> dict[str, Any]:
        self.product.settings_update({"memory_semantics": patch})
        return self.memory_status()
