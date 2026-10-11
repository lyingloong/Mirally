"""Privacy decisions made before platform APIs are called."""

from __future__ import annotations

import fnmatch
import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Optional, Tuple

from .models import Actor, CollectionLevel, PrivacyStatus


_LEVEL_ORDER = {
    CollectionLevel.METADATA: 0,
    CollectionLevel.STRUCTURE: 1,
    CollectionLevel.CONTENT: 2,
}


@dataclass(frozen=True)
class PrivacyDecision:
    allowed: bool
    level: str
    policy_version: int
    status: str
    reason: str = "allowed"


@dataclass(frozen=True)
class ActivityPolicy:
    """Immutable policy snapshot used by one collector generation.

    ``enabled`` controls collection, while the level controls how much of an
    already-authorized observation may be collected.  A denied content request
    must be rejected before UIA or WGC is called.
    """

    enabled: bool = False
    default_level: str = CollectionLevel.METADATA
    policy_version: int = 1
    exclude_self: bool = True
    deny_content_on_unknown_desktop: bool = True
    excluded_apps: Tuple[str, ...] = ()
    excluded_windows: Tuple[str, ...] = ()
    allowed_apps: Tuple[str, ...] = ()
    agent_pause_content: bool = True
    # Legacy configuration value retained for compatibility. Raw collection
    # never crops payloads to this value; query budgets belong to consumers.
    max_payload_bytes: int = 64 * 1024
    _self_app_ids: Tuple[str, ...] = field(default_factory=tuple, repr=False)

    @classmethod
    def from_mapping(cls, data: Optional[Mapping[str, Any]]) -> "ActivityPolicy":
        data = data or {}
        collection = data.get("collection") or {}
        actor = data.get("actor") or {}
        privacy = data.get("privacy") or {}
        default_level = str(collection.get("default_level", CollectionLevel.METADATA)).lower()
        if default_level not in _LEVEL_ORDER:
            raise ValueError(f"Unsupported activity collection level: {default_level}")
        def patterns(value: Any) -> Tuple[str, ...]:
            if not value:
                return ()
            if isinstance(value, str):
                return (value.lower(),)
            return tuple(str(item).lower() for item in value if str(item).strip())
        return cls(
            enabled=bool(data.get("enabled", False)),
            default_level=default_level,
            policy_version=max(1, int(data.get("policy_version", 1))),
            exclude_self=bool(privacy.get("exclude_self", True)),
            deny_content_on_unknown_desktop=bool(
                privacy.get("deny_content_on_unknown_desktop", True)
            ),
            excluded_apps=patterns(privacy.get("excluded_apps") or privacy.get("rules")),
            excluded_windows=patterns(privacy.get("excluded_windows")),
            allowed_apps=patterns(privacy.get("allowed_apps")),
            agent_pause_content=bool(actor.get("pause_content_during_agent_control", True)),
            max_payload_bytes=max(1024, int(data.get("max_payload_bytes", 64 * 1024))),
        )

    def with_self_apps(self, app_ids: Iterable[str]) -> "ActivityPolicy":
        return ActivityPolicy(**{**self.__dict__, "_self_app_ids": tuple(app_ids)})

    def fingerprint(self) -> str:
        raw = json.dumps(
            {
                "enabled": self.enabled,
                "default_level": self.default_level,
                "policy_version": self.policy_version,
                "exclude_self": self.exclude_self,
                "deny_content_on_unknown_desktop": self.deny_content_on_unknown_desktop,
                "excluded_apps": self.excluded_apps,
                "excluded_windows": self.excluded_windows,
                "allowed_apps": self.allowed_apps,
                "agent_pause_content": self.agent_pause_content,
            },
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()[:16]

    def decide(
        self,
        requested_level: str = CollectionLevel.METADATA,
        *,
        app_id: Optional[str] = None,
        window_name: Optional[str] = None,
        desktop_state: str = "default",
        control_owner: str = "unclaimed",
        paused: bool = False,
    ) -> PrivacyDecision:
        level = str(requested_level or CollectionLevel.METADATA).lower()
        if level not in _LEVEL_ORDER:
            raise ValueError(f"Unsupported activity collection level: {level}")
        if not self.enabled:
            return PrivacyDecision(False, level, self.policy_version, PrivacyStatus.DENIED, "disabled")
        if paused:
            return PrivacyDecision(False, level, self.policy_version, PrivacyStatus.PAUSED, "paused")

        app = str(app_id or "").lower()
        window = str(window_name or "").lower()
        if self.exclude_self and app and app in self._self_app_ids:
            return PrivacyDecision(False, level, self.policy_version, PrivacyStatus.DENIED, "self_excluded")
        if any(fnmatch.fnmatch(app, pattern) for pattern in self.excluded_apps):
            return PrivacyDecision(False, level, self.policy_version, PrivacyStatus.DENIED, "app_excluded")
        if window and any(fnmatch.fnmatch(window, pattern) for pattern in self.excluded_windows):
            return PrivacyDecision(False, level, self.policy_version, PrivacyStatus.DENIED, "window_excluded")
        if self.allowed_apps and not any(fnmatch.fnmatch(app, pattern) for pattern in self.allowed_apps):
            return PrivacyDecision(False, level, self.policy_version, PrivacyStatus.DENIED, "app_not_allowed")
        if desktop_state != "default" and level != CollectionLevel.METADATA:
            return PrivacyDecision(False, level, self.policy_version, PrivacyStatus.UNKNOWN, "desktop_unavailable")
        if self.deny_content_on_unknown_desktop and desktop_state == "unknown" and level != CollectionLevel.METADATA:
            return PrivacyDecision(False, level, self.policy_version, PrivacyStatus.UNKNOWN, "desktop_unknown")
        if self.agent_pause_content and control_owner == "agent" and level == CollectionLevel.CONTENT:
            return PrivacyDecision(False, level, self.policy_version, PrivacyStatus.PAUSED, "agent_control")
        if _LEVEL_ORDER[level] > _LEVEL_ORDER[self.default_level]:
            return PrivacyDecision(False, level, self.policy_version, PrivacyStatus.DENIED, "level_not_enabled")
        return PrivacyDecision(True, level, self.policy_version, PrivacyStatus.ALLOWED)
