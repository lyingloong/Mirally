"""Agent-facing read-only context source for activity knowledge."""

from __future__ import annotations

from typing import Any

from openassist.core.activity.knowledge import ContextQuery

from .organizer_service import ActivityOrganizerService


class ActivityContextSource:
    """Adapter shared by OpenClaw, UFO and future agent harnesses."""

    def __init__(self, service: ActivityOrganizerService) -> None:
        self.service = service

    def recent(self, *, seconds: int = 300, mode: str = "outline", token_budget: int = 4000) -> dict[str, Any]:
        return self.service.organizer.recent_activity(seconds=seconds, mode=mode, token_budget=token_budget).to_dict()

    def recent_activity(self, **kwargs: Any) -> dict[str, Any]:
        return self.recent(**kwargs)

    def query(self, query: ContextQuery) -> dict[str, Any]:
        return self.service.context(query).to_dict()

    def summary(self, query: ContextQuery) -> dict[str, Any]:
        return self.service.organizer.activity_summary(query)

    def search_activity(self, query: str, **kwargs: Any) -> dict[str, Any]:
        return self.service.organizer.search_activity(query, **kwargs).to_dict()

    def get_activity_around(self, at_utc_ms: int, **kwargs: Any) -> dict[str, Any]:
        return self.service.organizer.activity_around(at_utc_ms, **kwargs).to_dict()

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
