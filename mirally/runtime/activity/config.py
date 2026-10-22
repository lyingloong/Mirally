"""Activity sensor configuration, kept separate from speech and UFO config."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional

import yaml

from mirally.core.activity.models import CollectionLevel
from mirally.core.activity.policy import ActivityPolicy


@dataclass(frozen=True)
class ActivityConfig:
    enabled: bool
    database_path: Path
    asset_directory: Path
    poll_interval_seconds: float
    policy: ActivityPolicy
    observation_retention_days: int
    image_retention_hours: int
    image_quota_mib: int
    min_free_space_mib: int
    input_enabled: bool = False
    input_quiet_ms: int = 1500
    input_max_burst_ms: int = 10_000
    ui_structure_enabled: bool = False
    uia_timeout_ms: int = 1200
    # Legacy size/depth settings remain loadable; raw workers ignore these
    # values. Only consumer queries may shorten captured text or structure.
    uia_max_depth: int = 16
    uia_max_nodes: int = 180
    visual_enabled: bool = False
    visual_persist_images: bool = False
    visual_max_per_minute: int = 6
    visual_burst: int = 2
    visual_timeout_ms: int = 1500
    visual_max_dimension: int = 1920
    ocr_enabled: bool = False
    ocr_timeout_ms: int = 2500
    ocr_languages: tuple[str, ...] = ()
    window_titles_enabled: bool = True
    browser_urls_enabled: bool = True
    browser_url_timeout_ms: int = 1200
    browser_url_poll_interval_seconds: float = 3.0
    uia_content_enabled: bool = False
    uia_max_text_chars: int = 12000
    input_text_enabled: bool = False
    clipboard_enabled: bool = False
    clipboard_timeout_ms: int = 1500
    clipboard_max_chars: int = 12000
    continuous_enabled: bool = True
    capture_interval_seconds: float = 3.0
    visual_scope: str = "foreground"
    drop_black_frames: bool = True
    monitor_black_fraction: float = 0.90
    window_black_fraction: float = 0.80
    black_pixel_threshold: int = 8
    knowledge_database_path: Optional[Path] = None
    knowledge_poll_interval_seconds: float = 1.0
    knowledge_batch_size: int = 200
    knowledge_idle_timeout_seconds: float = 300.0
    knowledge_allowed_lateness_seconds: float = 60.0

    @classmethod
    def disabled(cls, product_root: Path) -> "ActivityConfig":
        data_root = default_data_directory()
        return cls(
            False,
            data_root / "observations.sqlite3",
            data_root / "assets",
            1.0,
            ActivityPolicy(enabled=False),
            14,
            24,
            1024,
            2048,
            window_titles_enabled=False,
            browser_urls_enabled=False,
        )


def default_data_directory() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "Mirally" / "activity"
    return Path.home() / ".local" / "share" / "mirally" / "activity"


def _path(value: Any, default: Path, root: Path) -> Path:
    if value in (None, ""):
        return default
    result = Path(os.path.expandvars(os.path.expanduser(str(value))))
    return (result if result.is_absolute() else root / result).resolve()


def load_activity_config(product_root: Path, path: Optional[Path] = None) -> ActivityConfig:
    """Load activity settings; relative storage paths use the product root."""

    product_root = Path(product_root).resolve()
    config_path = path or product_root / "config" / "mirally" / "activity.yaml"
    if config_path.is_file():
        with config_path.open("r", encoding="utf-8") as stream:
            data = yaml.safe_load(stream) or {}
    else:
        data = {}
    if not isinstance(data, Mapping):
        raise ValueError("activity config must be a YAML mapping")
    data_root = default_data_directory()
    # This is a preference, not a side effect: only the desktop host invokes
    # the collector lifecycle. Readers and MCP tools remain passive.
    enabled = data.get("enabled", True)
    if type(enabled) is not bool:
        raise ValueError("activity enabled must be a boolean")
    storage = data.get("storage") or {}
    collection = data.get("collection") or {}
    visual = data.get("visual") or {}
    if bool(visual.get("desktop_fallback", False)):
        raise ValueError("activity visual.desktop_fallback must remain false")
    policy_data = dict(data)
    policy_data["enabled"] = enabled
    policy = ActivityPolicy.from_mapping(policy_data)
    if "default_level" not in collection:
        policy = ActivityPolicy(
            **{
                **policy.__dict__,
                "default_level": CollectionLevel.CONTENT
                if enabled
                else CollectionLevel.METADATA,
            }
        )
    input_data = data.get("input") or {}
    structure_data = data.get("ui_structure") or {}
    knowledge_data = data.get("knowledge") or {}
    database_path = _path(storage.get("database_path"), data_root / "observations.sqlite3", product_root)
    window_titles_enabled = bool(
        collection.get("window_titles", data.get("window_titles", True))
    )
    browser_urls_enabled = bool(
        collection.get("browser_urls", data.get("browser_urls", True))
    )
    browser_url_timeout_ms = max(
        100,
        min(10_000, int(collection.get("browser_url_timeout_ms", 1200))),
    )
    visual_enabled = bool(visual.get("enabled", enabled))
    ocr_enabled = bool(visual.get("ocr_enabled", visual_enabled))
    input_enabled = bool(input_data.get("enabled", enabled))
    ui_structure_enabled = bool(
        structure_data.get("enabled", enabled)
    )
    return ActivityConfig(
        enabled=enabled,
        database_path=database_path,
        asset_directory=_path(storage.get("directory"), data_root, product_root) / "assets",
        poll_interval_seconds=max(0.25, min(10.0, float(data.get("poll_interval_seconds", 1.0)))),
        policy=policy,
        observation_retention_days=max(1, int(storage.get("observation_retention_days", 14))),
        image_retention_hours=max(1, int(storage.get("image_retention_hours", 24))),
        image_quota_mib=max(1, int(storage.get("image_quota_mib", 1024))),
        min_free_space_mib=max(128, int(storage.get("min_free_space_mib", 2048))),
        input_enabled=input_enabled,
        input_quiet_ms=max(100, min(60_000, int(input_data.get("quiet_ms", 1500)))),
        input_max_burst_ms=max(1_000, min(300_000, int(input_data.get("max_burst_ms", 10_000)))),
        ui_structure_enabled=ui_structure_enabled,
        uia_timeout_ms=max(100, min(10_000, int(structure_data.get("timeout_ms", 5000)))),
        uia_max_depth=max(0, min(32, int(structure_data.get("max_depth", 16 if structure_data.get("content", True) else 6)))),
        uia_max_nodes=max(1, min(2_000, int(structure_data.get("max_nodes", 180)))),
        visual_enabled=visual_enabled,
        visual_persist_images=bool(visual.get("persist_images", True)),
        visual_max_per_minute=max(1, min(240, int(visual.get("max_per_minute", 120)))),
        visual_burst=max(1, min(20, int(visual.get("burst", 2)))),
        visual_timeout_ms=max(100, min(10_000, int(visual.get("timeout_ms", 2_500)))),
        visual_max_dimension=max(320, min(8_192, int(visual.get("max_dimension", 1_920)))),
        ocr_enabled=ocr_enabled,
        ocr_timeout_ms=max(100, min(30_000, int(visual.get("ocr_timeout_ms", 2_500)))),
        ocr_languages=tuple(str(value) for value in (visual.get("languages") or ())),
        window_titles_enabled=window_titles_enabled,
        browser_urls_enabled=browser_urls_enabled,
        browser_url_timeout_ms=browser_url_timeout_ms,
        browser_url_poll_interval_seconds=max(
            1.0, min(60.0, float(collection.get("browser_url_poll_interval_seconds", 3.0)))
        ),
        uia_content_enabled=bool(structure_data.get("content", True)),
        uia_max_text_chars=max(256, min(24000, int(structure_data.get("max_text_chars", 12000)))),
        input_text_enabled=bool(input_data.get("text", collection.get("input_content", True))),
        clipboard_enabled=bool(input_data.get("clipboard", collection.get("clipboard", True))),
        clipboard_timeout_ms=max(100, min(10000, int(input_data.get("clipboard_timeout_ms", 1500)))),
        clipboard_max_chars=max(256, min(24000, int(input_data.get("clipboard_max_chars", 12000)))),
        continuous_enabled=bool(visual.get("continuous", True)),
        capture_interval_seconds=max(1.0, min(60.0, float(visual.get("interval_seconds", 3.0)))),
        visual_scope=_visual_scope(visual.get("scope", "monitors")),
        drop_black_frames=bool(visual.get("drop_black_frames", True)),
        monitor_black_fraction=max(0.5, min(1.0, float(visual.get("monitor_black_fraction", 0.90)))),
        window_black_fraction=max(0.5, min(1.0, float(visual.get("window_black_fraction", 0.80)))),
        black_pixel_threshold=max(0, min(32, int(visual.get("black_pixel_threshold", 8)))),
        knowledge_database_path=_path(
            storage.get("knowledge_database_path"),
            database_path.with_name("activity-knowledge.sqlite3"),
            product_root,
        ),
        knowledge_poll_interval_seconds=max(
            0.1, min(60.0, float(knowledge_data.get("poll_interval_seconds", 1.0)))
        ),
        knowledge_batch_size=max(1, min(500, int(knowledge_data.get("batch_size", 200)))),
        knowledge_idle_timeout_seconds=max(
            1.0, min(86_400.0, float(knowledge_data.get("idle_timeout_seconds", 300.0)))
        ),
        knowledge_allowed_lateness_seconds=max(
            0.0, min(86_400.0, float(knowledge_data.get("allowed_lateness_seconds", 60.0)))
        ),
    )


def _visual_scope(value: Any) -> str:
    scope = str(value)
    if scope not in {"foreground", "monitors"}:
        raise ValueError("activity visual.scope must be foreground or monitors")
    return scope
