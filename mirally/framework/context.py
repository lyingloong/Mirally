"""On-demand reads of existing owners; never start collectors or maintenance."""

from __future__ import annotations

from pathlib import Path
from dataclasses import replace
import sqlite3
from typing import Any


def config_path(root: Path, settings: dict[str, Any], kind: str) -> Path | None:
    value = settings.get(f"{kind}_config_path")
    if not value:
        return None
    path = Path(value).expanduser()
    path = (root / path).resolve() if not path.is_absolute() else path.resolve()
    # A missing project config must not accidentally read this machine's global
    # legacy data, especially when a test or a new installation has no config.
    return path if path.is_file() else None


def activity_config(root: Path, home: Path, settings: dict[str, Any]):
    from mirally.runtime.activity.config import ActivityConfig, load_activity_config
    path = config_path(root, settings, "activity")
    if path is not None:
        return load_activity_config(root, path)
    directory = home / "data" / "activity"
    return replace(ActivityConfig.disabled(root), enabled=settings["activity_enabled"],
                   database_path=directory / "observations.sqlite3", asset_directory=directory / "assets",
                   knowledge_database_path=directory / "knowledge.sqlite3")


def memory_config(root: Path, home: Path, settings: dict[str, Any]):
    from mirally.runtime.memory.config import MemoryConfig, load_memory_config
    path = config_path(root, settings, "memory")
    if path is not None:
        return load_memory_config(root, path)
    # A clean product home has its own Memory owner, not the machine-global
    # defaults used by the old installation. A read never creates these files.
    return MemoryConfig(enabled=settings["memory_enabled"], database_path=home / "memory" / "memory.sqlite3",
                        activity_config_path=home / "config" / "activity.yaml",
                        projection_database_path=home / "memory" / "activity-projection.sqlite3")


class ContextReader:
    def __init__(self, root: Path, home: Path) -> None:
        self.root = root
        self.home = home

    def read(self, settings: dict[str, Any], *, query: str, limit: int,
             include_memory: bool, include_activity: bool) -> dict[str, Any]:
        memory: dict[str, Any] = {"status": "disabled", "memories": []}
        activity: dict[str, Any] = {"status": "disabled", "items": []}
        gaps: list[str] = []
        if include_memory and settings["memory_enabled"]:
            try:
                from mirally.runtime.memory.service import MemoryReader
                memory = MemoryReader(self.root, config=memory_config(self.root, self.home, settings)).handle("search", {
                    "query": query, "limit": limit, "token_budget": 12000,
                })
            except (OSError, RuntimeError, ValueError, sqlite3.Error) as error:
                memory["status"] = "unavailable"
                gaps.append(f"memory: {type(error).__name__}")
        if include_activity and settings["activity_enabled"]:
            try:
                from mirally.core.activity.knowledge.repository import KnowledgeStore
                config = activity_config(self.root, self.home, settings)
                database = config.knowledge_database_path or config.database_path.with_name("activity-knowledge.sqlite3")
                if not database.is_file():
                    activity["status"] = "not_ready"
                else:
                    with KnowledgeStore(database, read_only=True) as store:
                        items = store.query_items(query=query or None, limit=limit)
                        selected = []
                        remaining = 12000
                        truncated = False
                        for item in items:
                            value = item.to_dict()
                            value["text_summary"] = value["text_summary"][:3000]
                            value["structured"] = {}  # Detailed evidence stays at its owner.
                            cost = len(str(value).encode("utf-8"))
                            if cost > remaining:
                                truncated = True
                                break
                            selected.append(value)
                            remaining -= cost
                        activity = {"status": "ready", "items": selected, "truncated": truncated,
                                    "collector_started": False, "maintenance_on_query": False}
            except (OSError, RuntimeError, ValueError, sqlite3.Error) as error:
                activity["status"] = "unavailable"
                gaps.append(f"activity: {type(error).__name__}")
        return {"memory": memory, "activity": activity, "gaps": gaps}
