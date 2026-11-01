"""Stable value objects for the activity knowledge layer.

The knowledge layer stores projections of immutable observations.  These
objects deliberately contain no platform or host-specific types so they can be
used by any future activity source and by agent adapters.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Tuple


def _json(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False))


@dataclass(frozen=True)
class Evidence:
    evidence_id: str
    source_observation_ids: Tuple[str, ...]
    observed_at_utc_ms: int
    received_at_utc_ms: Optional[int]
    content_type: str
    text: str = ""
    structured: Mapping[str, Any] = field(default_factory=dict)
    asset_refs: Tuple[str, ...] = ()
    content_hash: str = ""
    quality: str = "complete"
    confidence: float = 1.0
    extractor_version: str = "deterministic.v1"

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "source_observation_ids": list(self.source_observation_ids),
            "observed_at_utc_ms": self.observed_at_utc_ms,
            "received_at_utc_ms": self.received_at_utc_ms,
            "content_type": self.content_type,
            "text": self.text,
            "structured": _json(dict(self.structured)),
            "asset_refs": list(self.asset_refs),
            "content_hash": self.content_hash,
            "quality": self.quality,
            "confidence": self.confidence,
            "extractor_version": self.extractor_version,
        }


@dataclass(frozen=True)
class ActivityItem:
    item_id: str
    kind: str
    start_utc_ms: int
    end_utc_ms: int
    app_id: Optional[str] = None
    process_instance_id: Optional[str] = None
    window_instance_id: Optional[str] = None
    title: Optional[str] = None
    url: Optional[str] = None
    document_ref: Optional[str] = None
    text_summary: str = ""
    structured: Mapping[str, Any] = field(default_factory=dict)
    asset_refs: Tuple[str, ...] = ()
    source_observation_ids: Tuple[str, ...] = ()
    actor: str = "UNKNOWN"
    actor_basis: str = "unknown"
    quality: str = "complete"
    missing_reasons: Tuple[str, ...] = ()
    confidence: float = 1.0
    importance: float = 0.5
    sensitivity: str = "normal"
    content_hash: str = ""
    projection_version: str = "activity.v1"
    episode_id: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "kind": self.kind,
            "start_utc_ms": self.start_utc_ms,
            "end_utc_ms": self.end_utc_ms,
            "app_id": self.app_id,
            "process_instance_id": self.process_instance_id,
            "window_instance_id": self.window_instance_id,
            "title": self.title,
            "url": self.url,
            "document_ref": self.document_ref,
            "text_summary": self.text_summary,
            "structured": _json(dict(self.structured)),
            "asset_refs": list(self.asset_refs),
            "source_observation_ids": list(self.source_observation_ids),
            "actor": self.actor,
            "actor_basis": self.actor_basis,
            "quality": self.quality,
            "missing_reasons": list(self.missing_reasons),
            "confidence": self.confidence,
            "importance": self.importance,
            "sensitivity": self.sensitivity,
            "content_hash": self.content_hash,
            "projection_version": self.projection_version,
            "episode_id": self.episode_id,
        }


@dataclass(frozen=True)
class ActivityEpisode:
    episode_id: str
    start_utc_ms: int
    end_utc_ms: int
    title: str = ""
    primary_app: Optional[str] = None
    primary_window_id: Optional[str] = None
    item_ids: Tuple[str, ...] = ()
    summary: Optional[str] = None
    confidence: float = 1.0
    finalized: bool = False
    end_reason: Optional[str] = None
    revision: int = 1
    data_gaps: Tuple[str, ...] = ()
    projection_version: str = "activity.v1"

    def to_dict(self) -> dict[str, Any]:
        return {
            "episode_id": self.episode_id,
            "start_utc_ms": self.start_utc_ms,
            "end_utc_ms": self.end_utc_ms,
            "title": self.title,
            "primary_app": self.primary_app,
            "primary_window_id": self.primary_window_id,
            "item_ids": list(self.item_ids),
            "summary": self.summary,
            "confidence": self.confidence,
            "finalized": self.finalized,
            "end_reason": self.end_reason,
            "revision": self.revision,
            "data_gaps": list(self.data_gaps),
            "projection_version": self.projection_version,
        }


@dataclass(frozen=True)
class ContextQuery:
    query: Optional[str] = None
    start_utc_ms: Optional[int] = None
    end_utc_ms: Optional[int] = None
    apps: Tuple[str, ...] = ()
    windows: Tuple[str, ...] = ()
    kinds: Tuple[str, ...] = ()
    episode_id: Optional[str] = None
    mode: str = "outline"
    purpose: str = "agent_context"
    max_items: int = 30
    max_episodes: int = 10
    token_budget: int = 4000
    include_assets: bool = False
    include_uia: bool = False
    include_ocr: bool = True
    after_item_id: Optional[str] = None

    def validate(self) -> None:
        if any(value is not None and value < 0 for value in (self.start_utc_ms, self.end_utc_ms)):
            raise ValueError("context timestamps must be non-negative")
        if self.start_utc_ms is not None and self.end_utc_ms is not None and self.start_utc_ms >= self.end_utc_ms:
            raise ValueError("context start must be before end")
        if self.mode not in {"outline", "structured", "evidence"}:
            raise ValueError("context mode must be outline, structured or evidence")
        if self.purpose not in {"agent_context", "memory_maintenance", "diagnostics"}:
            raise ValueError("unsupported context purpose")
        if self.max_items < 1 or self.max_items > 500:
            raise ValueError("max_items must be between 1 and 500")
        if self.max_episodes < 1 or self.max_episodes > 100:
            raise ValueError("max_episodes must be between 1 and 100")
        if self.token_budget < 128 or self.token_budget > 100_000:
            raise ValueError("token_budget must be between 128 and 100000")


@dataclass(frozen=True)
class ContextBundle:
    query_id: str
    generated_at_utc_ms: int
    index_watermark: int
    episodes: Tuple[ActivityEpisode, ...] = ()
    items: Tuple[ActivityItem, ...] = ()
    evidence: Tuple[Evidence, ...] = ()
    gaps: Tuple[str, ...] = ()
    truncated: bool = False
    next_cursor: Optional[str] = None
    # ``context_policy`` identifies the consumer contract used to assemble the
    # bundle.  Top-level references make it possible for an MCP client to
    # render provenance without walking every item first.
    context_policy: str = "agent_context"
    source_observation_ids: Tuple[str, ...] = ()
    evidence_ids: Tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "query_id": self.query_id,
            "generated_at_utc_ms": self.generated_at_utc_ms,
            "index_watermark": self.index_watermark,
            "episodes": [value.to_dict() for value in self.episodes],
            "items": [value.to_dict() for value in self.items],
            "evidence": [value.to_dict() for value in self.evidence],
            "gaps": list(self.gaps),
            "truncated": self.truncated,
            "next_cursor": self.next_cursor,
            "context_policy": self.context_policy,
            "source_observation_ids": list(self.source_observation_ids),
            "evidence_ids": list(self.evidence_ids),
        }


@dataclass(frozen=True)
class MemoryCandidate:
    candidate_id: str
    statement: str
    fact_key: Optional[str] = None
    subject: Optional[str] = None
    predicate: Optional[str] = None
    object_value: Optional[str] = None
    valid_from_utc_ms: Optional[int] = None
    valid_to_utc_ms: Optional[int] = None
    source_episode_ids: Tuple[str, ...] = ()
    source_observation_ids: Tuple[str, ...] = ()
    confidence: float = 0.5
    extraction_method: str = "manual"
    extraction_version: str = "memory.v1"
    status: str = "candidate"
    conflicts_with: Tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "statement": self.statement,
            "fact_key": self.fact_key,
            "subject": self.subject,
            "predicate": self.predicate,
            "object_value": self.object_value,
            "valid_from_utc_ms": self.valid_from_utc_ms,
            "valid_to_utc_ms": self.valid_to_utc_ms,
            "source_episode_ids": list(self.source_episode_ids),
            "source_observation_ids": list(self.source_observation_ids),
            "confidence": self.confidence,
            "extraction_method": self.extraction_method,
            "extraction_version": self.extraction_version,
            "status": self.status,
            "conflicts_with": list(self.conflicts_with),
        }


@dataclass(frozen=True)
class MemoryFact:
    """An accepted, versioned fact retained by the memory maintenance layer."""

    fact_id: str
    statement: str
    fact_key: Optional[str] = None
    subject: Optional[str] = None
    predicate: Optional[str] = None
    object_value: Optional[str] = None
    valid_from_utc_ms: Optional[int] = None
    valid_to_utc_ms: Optional[int] = None
    source_episode_ids: Tuple[str, ...] = ()
    source_observation_ids: Tuple[str, ...] = ()
    confidence: float = 0.5
    extraction_method: str = "manual"
    extraction_version: str = "memory.v1"
    status: str = "active"
    supersedes: Tuple[str, ...] = ()
    conflicts_with: Tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "fact_id": self.fact_id,
            "statement": self.statement,
            "fact_key": self.fact_key,
            "subject": self.subject,
            "predicate": self.predicate,
            "object_value": self.object_value,
            "valid_from_utc_ms": self.valid_from_utc_ms,
            "valid_to_utc_ms": self.valid_to_utc_ms,
            "source_episode_ids": list(self.source_episode_ids),
            "source_observation_ids": list(self.source_observation_ids),
            "confidence": self.confidence,
            "extraction_method": self.extraction_method,
            "extraction_version": self.extraction_version,
            "status": self.status,
            "supersedes": list(self.supersedes),
            "conflicts_with": list(self.conflicts_with),
        }
