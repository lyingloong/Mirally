"""OpenAssist product-layer configuration."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional

import yaml


_ENV_PATTERN = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")


@dataclass(frozen=True)
class SpeechConfig:
    """Configuration for the OpenAssist speech-to-text layer."""

    enabled: bool
    provider: str
    base_url: str
    api_key: str
    model: str
    language: str
    timeout_seconds: float
    response_format: str


def configure_ufo_config_root(product_root: Path) -> Path:
    """Select the product-owned UFO configuration directory."""
    config_root = product_root / "config" / "openassist"
    if not (config_root / "ufo").is_dir():
        raise FileNotFoundError(
            f"OpenAssist UFO configuration directory not found at {config_root / 'ufo'}"
        )

    os.environ["OPENASSIST_UFO_CONFIG_ROOT"] = str(config_root.resolve())
    return config_root


def _env_or_value(value: Any, *, field_name: str, required: bool = True) -> str:
    text = str(value or "").strip()
    match = _ENV_PATTERN.fullmatch(text)
    if match:
        text = os.environ.get(match.group(1), "").strip()
    if required and field_name == "api_key" and not text:
        raise ValueError(
            "OpenAssist speech API key is empty; set it in config or "
            "OPENASSIST_SPEECH_API_KEY"
        )
    return text


def load_speech_config(product_root: Path, path: Optional[Path] = None) -> SpeechConfig:
    """Load the local speech config without changing UFO configuration."""

    config_path = path or product_root / "config" / "openassist" / "speech.yaml"
    if not config_path.is_file():
        raise FileNotFoundError(
            f"OpenAssist speech config not found at {config_path}. "
            "Copy config/openassist/speech.yaml.template first."
        )

    with config_path.open("r", encoding="utf-8") as stream:
        data = yaml.safe_load(stream) or {}
    if not isinstance(data, Mapping):
        raise ValueError("OpenAssist speech config must be a YAML mapping")

    provider = str(data.get("provider", "openai_compatible")).strip()
    if provider != "openai_compatible":
        raise ValueError(f"Unsupported OpenAssist speech provider: {provider}")

    enabled = bool(data.get("enabled", False))
    return SpeechConfig(
        enabled=enabled,
        provider=provider,
        base_url=str(data.get("base_url", "")).strip(),
        api_key=_env_or_value(
            data.get("api_key"), field_name="api_key", required=enabled
        ),
        model=str(data.get("model", "")).strip(),
        language=str(data.get("language", "auto")).strip() or "auto",
        timeout_seconds=float(data.get("timeout_seconds", 120)),
        response_format=str(data.get("response_format", "json")).strip() or "json",
    )
