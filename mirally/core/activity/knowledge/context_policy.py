"""Boundaries for exposing activity knowledge to an agent.

The organizer is intentionally a general query engine.  Consumers such as an
agent or a diagnostics browser need different result budgets, however.  This
module keeps those limits in one product-owned value object shared by the MCP
adapter and compatibility JSONL bridge.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Mapping, Tuple

from .models import ContextQuery


_DAY_MS = 24 * 60 * 60 * 1000


@dataclass(frozen=True)
class ActivityContextPolicy:
    """Bounded read policy for one activity context consumer.

    The policy only constrains a query; it never starts collection or changes
    the source/knowledge stores.  ``apply`` returns a new query and leaves the
    caller's value object untouched.  Limits are intentionally aligned with
    the existing bridge defaults so adopting this class is backwards
    compatible.
    """

    purpose: str = "agent_context"
    policy_id: str = "activity-agent.v1"
    # ``around`` uses a radius on both sides of its center, so the largest
    # bridge request spans two days even though each side is capped at one.
    max_window_ms: int = 2 * _DAY_MS + 1000
    max_items: int = 200
    max_episodes: int = 50
    max_token_budget: int = 16_000
    allowed_modes: Tuple[str, ...] = ("outline", "structured", "evidence")
    allow_uia: bool = True
    allow_ocr: bool = True
    allow_assets: bool = True

    @classmethod
    def for_purpose(cls, purpose: str) -> "ActivityContextPolicy":
        """Return the bounded profile used by a named context consumer."""

        selected = str(purpose or "agent_context").strip() or "agent_context"
        if selected == "agent_context":
            return cls()
        if selected == "memory_maintenance":
            return cls(
                purpose=selected,
                policy_id="activity-memory.v1",
                max_window_ms=14 * _DAY_MS + 1000,
                max_items=200,
                max_episodes=50,
                max_token_budget=32_000,
                allow_uia=False,
            )
        if selected == "diagnostics":
            return cls(
                purpose=selected,
                policy_id="activity-diagnostics.v1",
                max_window_ms=60 * _DAY_MS + 1000,
                max_items=500,
                max_episodes=100,
                max_token_budget=100_000,
            )
        raise ValueError(f"unsupported context purpose: {selected}")

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any] | None) -> "ActivityContextPolicy":
        """Build a policy from a small transport/config mapping.

        This is deliberately not a general configuration loader.  It is useful
        to MCP adapters that receive a policy profile while keeping the
        hard-coded safety ceilings in this module.
        """

        data = data or {}
        base = cls.for_purpose(str(data.get("purpose", "agent_context")))

        def positive(name: str, value: Any, ceiling: int) -> int:
            if value is None:
                return getattr(base, name)
            parsed = int(value)
            return max(1, min(ceiling, parsed))

        def content_allowed(name: str) -> bool:
            value = data.get(name, getattr(base, name))
            if not isinstance(value, bool):
                raise ValueError(f"{name} must be a boolean")
            return getattr(base, name) and value

        modes = data.get("allowed_modes")
        if modes is None:
            selected_modes = base.allowed_modes
        elif isinstance(modes, str):
            selected_modes = (modes,)
        else:
            selected_modes = tuple(str(value) for value in modes)
        selected_modes = tuple(dict.fromkeys(value for value in selected_modes if value in base.allowed_modes))
        if not selected_modes:
            raise ValueError("allowed_modes must include outline, structured or evidence")
        return replace(
            base,
            max_window_ms=positive("max_window_ms", data.get("max_window_ms"), base.max_window_ms),
            max_items=positive("max_items", data.get("max_items"), base.max_items),
            max_episodes=positive("max_episodes", data.get("max_episodes"), base.max_episodes),
            max_token_budget=positive("max_token_budget", data.get("max_token_budget"), base.max_token_budget),
            allowed_modes=selected_modes,
            allow_uia=content_allowed("allow_uia"),
            allow_ocr=content_allowed("allow_ocr"),
            allow_assets=content_allowed("allow_assets"),
        )

    def apply(self, query: ContextQuery) -> ContextQuery:
        """Validate and constrain ``query`` for this policy profile."""

        query.validate()
        if query.purpose != self.purpose:
            raise ValueError(f"query purpose {query.purpose!r} does not match policy {self.purpose!r}")
        if query.mode not in self.allowed_modes:
            raise ValueError(f"context mode {query.mode!r} is not allowed by {self.policy_id}")
        if (
            query.start_utc_ms is not None
            and query.end_utc_ms is not None
            and query.end_utc_ms - query.start_utc_ms > self.max_window_ms
        ):
            raise ValueError(f"context window exceeds {self.max_window_ms // _DAY_MS} day policy limit")
        return replace(
            query,
            max_items=min(query.max_items, self.max_items),
            max_episodes=min(query.max_episodes, self.max_episodes),
            token_budget=min(query.token_budget, self.max_token_budget),
            include_uia=query.include_uia and self.allow_uia,
            include_ocr=query.include_ocr and self.allow_ocr,
            include_assets=query.include_assets and self.allow_assets,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy_id": self.policy_id,
            "purpose": self.purpose,
            "max_window_ms": self.max_window_ms,
            "max_items": self.max_items,
            "max_episodes": self.max_episodes,
            "max_token_budget": self.max_token_budget,
            "allowed_modes": list(self.allowed_modes),
            "allow_uia": self.allow_uia,
            "allow_ocr": self.allow_ocr,
            "allow_assets": self.allow_assets,
        }


__all__ = ["ActivityContextPolicy"]
