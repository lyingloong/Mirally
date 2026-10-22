"""Agent-facing read-only context source for activity knowledge."""

from __future__ import annotations

import time
from typing import Any

from mirally.core.activity.knowledge import ActivityContextPolicy, ContextQuery

from .organizer_service import ActivityOrganizerService


class ActivityContextSource:
    """Transport-independent adapter for OpenClaw and Activity consumers."""

    def __init__(self, service: ActivityOrganizerService) -> None:
        self.service = service

    @staticmethod
    def _policy(query: ContextQuery) -> ActivityContextPolicy:
        return ActivityContextPolicy.for_purpose(query.purpose)

    def _apply(self, query: ContextQuery) -> ContextQuery:
        return self._policy(query).apply(query)

    def recent(self, *, seconds: int = 300, mode: str = "outline", token_budget: int = 4000) -> dict[str, Any]:
        now = int(time.time() * 1000)
        query = ContextQuery(
            start_utc_ms=now - max(1, int(seconds)) * 1000,
            end_utc_ms=now + 1,
            mode=mode,
            token_budget=token_budget,
        )
        return self.query(query)

    def recent_activity(self, **kwargs: Any) -> dict[str, Any]:
        return self.recent(**kwargs)

    def query(self, query: ContextQuery) -> dict[str, Any]:
        return self.service.context(self._apply(query)).to_dict()

    def events(self, query: ContextQuery) -> dict[str, Any]:
        """Return deterministic observed-event fragments for an agent.

        Unlike a context bundle, this view carries explicit action/object/
        unknown fields and evidence references without inferring user intent.
        """
        return self.service.organizer.activity_events(self._apply(query))

    def summary(self, query: ContextQuery) -> dict[str, Any]:
        query = self._apply(query)
        return self.service.organizer.activity_summary(query)

    def search_activity(self, query: str, **kwargs: Any) -> dict[str, Any]:
        context = ContextQuery(
            query=str(query), mode=str(kwargs.get("mode", "outline")),
            token_budget=int(kwargs.get("token_budget", 4000)),
            max_items=int(kwargs.get("max_items", 30)),
        )
        return self.query(context)

    def get_activity_around(self, at_utc_ms: int, **kwargs: Any) -> dict[str, Any]:
        center = int(at_utc_ms)
        radius = max(1, int(kwargs.get("window_seconds", 60))) * 1000
        context = ContextQuery(
            start_utc_ms=center - radius, end_utc_ms=center + radius,
            mode=str(kwargs.get("mode", "outline")),
            token_budget=int(kwargs.get("token_budget", 4000)),
        )
        return self.query(context)

    def get_episode(self, episode_id: str) -> dict[str, Any] | None:
        episode = self.service.organizer.get_episode(episode_id)
        return None if episode is None else episode.to_dict()

    def get_evidence(self, evidence_ids: list[str], *, include_assets: bool = False) -> list[dict[str, Any]]:
        return [value.to_dict() for value in self.service.organizer.get_evidence(evidence_ids, include_assets=include_assets)]

    def activity_summary(self, query: ContextQuery) -> dict[str, Any]:
        return self.summary(query)

    def organizer_health(self) -> dict[str, Any]:
        return self.health()

    def health(self) -> dict[str, Any]:
        return self.service.health()


__all__ = ["ActivityContextSource"]
