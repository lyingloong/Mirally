"""UFO configuration bridge and product execution budgets."""

from __future__ import annotations

import math
import os
import copy
from contextlib import contextmanager
from dataclasses import dataclass, fields
from pathlib import Path

import yaml


@dataclass(frozen=True)
class ExecutionLimits:
    total_seconds: float = 600
    initialization_seconds: float = 60
    model_seconds: float = 90
    tool_seconds: float = 30
    model_attempts: int = 2
    context_chars: int = 32000

    def __post_init__(self):
        for field in fields(self):
            value = getattr(self, field.name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"execution.{field.name} must be a positive finite number")
        for name in ("model_attempts", "context_chars"):
            if not isinstance(getattr(self, name), int):
                raise ValueError(f"execution.{name} must be an integer")


def load_execution_limits(product_root: Path) -> ExecutionLimits:
    path = product_root / "config" / "openassist" / "execution.yaml"
    if not path.is_file():
        return ExecutionLimits()
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError("execution.yaml must contain a mapping")
    unknown = set(data) - {field.name for field in fields(ExecutionLimits)}
    if unknown:
        raise ValueError(f"Unknown execution settings: {', '.join(sorted(unknown))}")
    return ExecutionLimits(**data)


def configure_ufo_config_root(product_root: Path, *, ufo_root: Path | None = None) -> Path:
    """Configure the upstream loader before importing any runtime modules."""
    config_root = product_root / "config" / "openassist"
    if not (config_root / "ufo").is_dir():
        raise FileNotFoundError(f"OpenAssist UFO configuration directory not found at {config_root / 'ufo'}")
    os.environ["OPENASSIST_UFO_CONFIG_ROOT"] = str(config_root.resolve())
    if ufo_root is not None:
        import sys
        resolved = str(ufo_root.resolve())
        if resolved not in sys.path:
            sys.path.insert(0, resolved)
        from config import config_loader
        config_loader.ConfigLoader.reset()
        config_loader.ConfigLoader.get_instance(str(config_root.resolve()))
        config_loader._global_ufo_config = None
        config_loader._global_galaxy_config = None
    return config_root


@contextmanager
def bounded_model_services(limits):
    """Limit SDK retries, including cached clients and startup probes."""
    from config.config_loader import get_ufo_config
    from ufo.llm.base import BaseService

    system = get_ufo_config().system
    previous = {name: getattr(system, name) for name in ("max_retry", "timeout")}
    original = vars(BaseService)["get_service"]
    factory = BaseService.get_service
    system.max_retry = 0
    system.timeout = min(float(system.timeout), limits.model_seconds)

    def get_service(name, agent_type, model_name=None):
        service = factory(name, agent_type, model_name)
        if service is None:
            return service
        bounded = copy.copy(service)
        # Providers with range(max_retry) need one attempt, SDK clients zero retries.
        bounded.max_retry = 1
        if hasattr(service, "config"):
            bounded.config = dict(service.config, MAX_RETRY=1, TIMEOUT=system.timeout)
        client = getattr(service, "client", None)
        if client is not None and hasattr(client, "with_options"):
            bounded.client = client.with_options(max_retries=0, timeout=system.timeout)
        return bounded

    BaseService.get_service = staticmethod(get_service)
    try:
        yield
    finally:
        BaseService.get_service = original
        for name, value in previous.items():
            setattr(system, name, value)
