"""Versioned, privacy-aware observation value objects.

The observation model deliberately contains no Windows or UFO types.  A source
turns platform facts into this model and the store assigns the durable
``ingest_seq``.  That keeps consumers independent from HWNDs and allows the
same protocol to be used by a future OpenClaw or non-Windows source.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field, replace
from typing import Any, Dict, Iterable, Mapping, Optional, Tuple


class Actor:
    USER = "USER"
    AGENT = "AGENT"
    SYSTEM = "SYSTEM"
    BACKGROUND = "BACKGROUND"
    UNKNOWN = "UNKNOWN"


class CollectionLevel:
    METADATA = "metadata"
    STRUCTURE = "structure"
    CONTENT = "content"


class PrivacyStatus:
    ALLOWED = "allowed"
    DENIED = "denied"
    PAUSED = "paused"
    UNKNOWN = "unknown"


class ObservationQuality:
    COMPLETE = "complete"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"
    STALE = "stale"
    GAP = "gap"


def utc_ms() -> int:
    return int(time.time() * 1000)


def monotonic_ns() -> int:
    return time.monotonic_ns()


def opaque_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def stable_opaque_id(prefix: str, value: str) -> str:
    """Return a non-reversible local identifier for an instance key."""

    digest = hashlib.sha256(value.encode("utf-8", "strict")).hexdigest()[:32]
    return f"{prefix}_{digest}"


def json_object(value: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    if value is None:
        return {}
    return json.loads(json.dumps(dict(value), ensure_ascii=False))


@dataclass(frozen=True)
class ObservationInput:
    """An immutable source event before it receives a store sequence."""

    kind: str
    source_id: str
    source_epoch: str
    source_seq: int
    observed_at_utc_ms: int
    monotonic_ns: int
    boot_id: str
    clock_epoch: int = 0
    time_quality: str = "observed"
    session_instance_id: Optional[str] = None
    process_instance_id: Optional[str] = None
    window_instance_id: Optional[str] = None
    app_id: Optional[str] = None
    actor: str = Actor.UNKNOWN
    actor_basis: str = "unclaimed"
    control_owner: str = "unclaimed"
    policy_version: int = 1
    collection_level: str = CollectionLevel.METADATA
    privacy_status: str = PrivacyStatus.ALLOWED
    caused_by: Tuple[str, ...] = ()
    capture_group_id: Optional[str] = None
    capture_started_at: Optional[int] = None
    capture_finished_at: Optional[int] = None
    payload: Mapping[str, Any] = field(default_factory=dict)
    uia_summary: Optional[Mapping[str, Any]] = None
    visual_summary: Optional[Mapping[str, Any]] = None
    ocr_summary: Optional[Mapping[str, Any]] = None
    asset_refs: Tuple[str, ...] = ()
    quality: str = ObservationQuality.COMPLETE
    missing_reasons: Tuple[str, ...] = ()
    truncated: bool = False
    identity_confidence: str = "unknown"

    def validate(self) -> None:
        if not self.kind or len(self.kind) > 80:
            raise ValueError("observation kind must be 1-80 characters")
        if not self.source_id or not self.source_epoch:
            raise ValueError("source_id and source_epoch are required")
        if self.source_seq < 0:
            raise ValueError("source_seq must be non-negative")
        if self.policy_version < 1:
            raise ValueError("policy_version must be positive")
        if self.actor not in {
            Actor.USER,
            Actor.AGENT,
            Actor.SYSTEM,
            Actor.BACKGROUND,
            Actor.UNKNOWN,
        }:
            raise ValueError(f"unknown actor: {self.actor}")
        if self.collection_level not in {
            CollectionLevel.METADATA,
            CollectionLevel.STRUCTURE,
            CollectionLevel.CONTENT,
        }:
            raise ValueError(f"unknown collection level: {self.collection_level}")
        if len(json.dumps(self.payload, ensure_ascii=False)) > 64 * 1024:
            raise ValueError("observation payload exceeds 64 KiB")
        if self.uia_summary is not None and len(json.dumps(self.uia_summary, ensure_ascii=False)) > 64 * 1024:
            raise ValueError("UIA summary exceeds 64 KiB")
        if self.visual_summary is not None and len(json.dumps(self.visual_summary, ensure_ascii=False)) > 64 * 1024:
            raise ValueError("visual summary exceeds 64 KiB")
        if self.ocr_summary is not None and len(json.dumps(self.ocr_summary, ensure_ascii=False)) > 64 * 1024:
            raise ValueError("OCR summary exceeds 64 KiB")


@dataclass(frozen=True)
class Observation(ObservationInput):
    observation_id: str = field(default_factory=lambda: opaque_id("obs"))
    ingest_seq: Optional[int] = None
    received_at_utc_ms: Optional[int] = None

    @classmethod
    def create(cls, **kwargs: Any) -> "Observation":
        now = utc_ms()
        value = cls(
            observed_at_utc_ms=kwargs.pop("observed_at_utc_ms", now),
            monotonic_ns=kwargs.pop("monotonic_ns", monotonic_ns()),
            received_at_utc_ms=kwargs.pop("received_at_utc_ms", now),
            **kwargs,
        )
        value.validate()
        return value

    def as_input(self) -> ObservationInput:
        return ObservationInput(
            **{
                name: getattr(self, name)
                for name in ObservationInput.__dataclass_fields__
            }
        )

    def with_ingest(self, ingest_seq: int, received_at_utc_ms: int) -> "Observation":
        return replace(self, ingest_seq=ingest_seq, received_at_utc_ms=received_at_utc_ms)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": 2,
            "observation_id": self.observation_id,
            "kind": self.kind,
            "source_id": self.source_id,
            "source_epoch": self.source_epoch,
            "source_seq": self.source_seq,
            "ingest_seq": self.ingest_seq,
            "observed_at_utc_ms": self.observed_at_utc_ms,
            "received_at_utc_ms": self.received_at_utc_ms,
            "monotonic_ns": self.monotonic_ns,
            "boot_id": self.boot_id,
            "clock_epoch": self.clock_epoch,
            "time_quality": self.time_quality,
            "session_instance_id": self.session_instance_id,
            "process_instance_id": self.process_instance_id,
            "window_instance_id": self.window_instance_id,
            "app_id": self.app_id,
            "actor": self.actor,
            "actor_basis": self.actor_basis,
            "control_owner": self.control_owner,
            "policy_version": self.policy_version,
            "collection_level": self.collection_level,
            "privacy_status": self.privacy_status,
            "caused_by": list(self.caused_by),
            "capture_group_id": self.capture_group_id,
            "capture_started_at": self.capture_started_at,
            "capture_finished_at": self.capture_finished_at,
            "payload": json_object(self.payload),
            "uia_summary": json_object(self.uia_summary),
            "visual_summary": json_object(self.visual_summary),
            "ocr_summary": json_object(self.ocr_summary),
            "asset_refs": list(self.asset_refs),
            "quality": self.quality,
            "missing_reasons": list(self.missing_reasons),
            "truncated": self.truncated,
            "identity_confidence": self.identity_confidence,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "Observation":
        known = {field_name for field_name in cls.__dataclass_fields__}
        data = {key: value[key] for key in known if key in value}
        data["caused_by"] = tuple(data.get("caused_by") or ())
        data["asset_refs"] = tuple(data.get("asset_refs") or ())
        data["missing_reasons"] = tuple(data.get("missing_reasons") or ())
        result = cls(**data)
        result.validate()
        return result


def normalize_asset_refs(values: Iterable[str]) -> Tuple[str, ...]:
    return tuple(dict.fromkeys(str(value) for value in values if value))
