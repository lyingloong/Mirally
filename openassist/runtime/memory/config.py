"""Memory configuration contains storage and budgets, never provider secrets."""

from __future__ import annotations

import math
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping

import yaml

from openassist.runtime.activity.config import default_data_directory, load_activity_config


@dataclass(frozen=True)
class SemanticsConfig:
    enabled: bool = False
    model: str | None = None
    agent_id: str = "main"
    interval_seconds: float = 30.0
    max_calls_per_hour: int = 6
    max_tokens_per_day: int = 60_000
    max_output_tokens: int = 1500


@dataclass(frozen=True)
class MemoryConfig:
    enabled: bool
    database_path: Path
    activity_config_path: Path
    projection_database_path: Path
    poll_interval_seconds: float = 2.0
    batch_size: int = 200
    window_seconds: int = 300
    working_ttl_seconds: int = 86_400
    context_token_budget: int = 1500
    semantics: SemanticsConfig = field(default_factory=SemanticsConfig)

    def settings(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "database_path": str(self.database_path),
            "activity_config_path": str(self.activity_config_path),
            "poll_interval_seconds": self.poll_interval_seconds,
            "context_token_budget": self.context_token_budget,
            "semantics": asdict(self.semantics),
            "starts_collector": False,
            "provider_credentials": "host_managed",
        }


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError(f"memory {name} must be a mapping")
    return value


def _bool(value: Any, name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"memory {name} must be a boolean")
    return value


def _number(value: Any, name: str, low: float, high: float) -> float:
    if isinstance(value, bool):
        raise ValueError(f"memory {name} must be a number")
    try:
        result = float(value)
    except (ValueError, TypeError) as error:
        raise ValueError(f"memory {name} must be a number") from error
    if not math.isfinite(result) or not low <= result <= high:
        raise ValueError(f"memory {name} must be between {low} and {high}")
    return result


def _integer(value: Any, name: str, low: int, high: int) -> int:
    result = _number(value, name, low, high)
    if not result.is_integer():
        raise ValueError(f"memory {name} must be an integer")
    return int(result)


def _path(value: Any, default: Path, root: Path) -> Path:
    if value in (None, ""):
        return default.resolve()
    result = Path(os.path.expandvars(os.path.expanduser(str(value))))
    return (result if result.is_absolute() else root / result).resolve()


def load_memory_config(product_root: Path, path: Path | None = None) -> MemoryConfig:
    root = Path(product_root).resolve()
    config_path = Path(path) if path is not None else root / "config/openassist/memory.yaml"
    data: Mapping[str, Any] = {}
    if config_path.is_file():
        with config_path.open(encoding="utf-8") as stream:
            data = _mapping(yaml.safe_load(stream), "config")
    storage = _mapping(data.get("storage"), "storage")
    semantics = _mapping(data.get("semantics"), "semantics")
    memory_root = default_data_directory().parent / "memory"
    database = _path(storage.get("database_path", data.get("database_path")), memory_root / "memory.sqlite3", root)
    projection = _path(storage.get("projection_database_path", data.get("projection_database_path")),
                       database.with_name("memory-activity-projection.sqlite3"), root)
    if database == projection:
        raise ValueError("memory storage and rebuildable projection must use different databases")
    model = semantics.get("model")
    if model is not None and not isinstance(model, str):
        raise ValueError("memory semantics.model must be a string or null")
    agent_id = str(semantics.get("agent_id", "main")).strip()
    if not agent_id or len(agent_id) > 128:
        raise ValueError("memory semantics.agent_id must be nonempty and bounded")
    config = MemoryConfig(
        enabled=_bool(data.get("enabled", False), "enabled"),
        database_path=database,
        activity_config_path=_path(data.get("activity_config_path"), root / "config/openassist/activity.yaml", root),
        projection_database_path=projection,
        poll_interval_seconds=_number(data.get("poll_interval_seconds", 2.0), "poll_interval_seconds", 0.05, 300),
        batch_size=_integer(data.get("batch_size", 200), "batch_size", 1, 500),
        window_seconds=_integer(data.get("window_seconds", 300), "window_seconds", 30, 3600),
        working_ttl_seconds=_integer(data.get("working_ttl_seconds", 86400), "working_ttl_seconds", 60, 604800),
        context_token_budget=_integer(data.get("context_token_budget", 1500), "context_token_budget", 128, 8000),
        semantics=SemanticsConfig(
            enabled=_bool(semantics.get("enabled", False), "semantics.enabled"),
            model=model.strip() or None if model else None,
            agent_id=agent_id,
            interval_seconds=_number(semantics.get("interval_seconds", 30), "semantics.interval_seconds", 1, 3600),
            max_calls_per_hour=_integer(semantics.get("max_calls_per_hour", 6), "semantics.max_calls_per_hour", 0, 10000),
            max_tokens_per_day=_integer(semantics.get("max_tokens_per_day", 60000), "semantics.max_tokens_per_day", 0, 10000000),
            max_output_tokens=_integer(semantics.get("max_output_tokens", 1500), "semantics.max_output_tokens", 128, 16000),
        ),
    )
    validate_storage_paths(root, config)
    return config


def validate_storage_paths(root: Path, config: MemoryConfig) -> None:
    """Reject aliasing before any memory writer can touch the source store."""
    source = load_activity_config(root, config.activity_config_path)
    paths = (config.database_path.resolve(), config.projection_database_path.resolve(),
             source.database_path.resolve())
    if len(set(paths)) != 3:
        raise ValueError("memory/source/projection must use independent databases")
    existing = [path for path in paths if path.is_file()]
    if any(first.samefile(second) for index, first in enumerate(existing)
           for second in existing[index + 1:]):
        raise ValueError("memory/source/projection must use independent databases")
