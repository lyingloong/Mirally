"""Optional semantic enrichment contracts.

Deterministic projection remains usable without an LLM.  Applications may
register bounded parsers that annotate ActivityItems after they are indexed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional, Protocol

from .models import ActivityItem


@dataclass(frozen=True)
class SemanticAnnotation:
    extractor_name: str
    extractor_version: str
    value: Mapping[str, Any]
    confidence: float = 0.5
    model_name: Optional[str] = None
    status: str = "ok"
    error: Optional[str] = None


class ActivityParser(Protocol):
    name: str
    version: str

    def supports(self, item: ActivityItem) -> bool:
        ...

    def parse(self, item: ActivityItem) -> Optional[SemanticAnnotation]:
        ...


class GenericActivityParser:
    name = "generic"
    version = "generic.v1"

    def supports(self, item: ActivityItem) -> bool:
        return True

    def parse(self, item: ActivityItem) -> Optional[SemanticAnnotation]:
        text = item.text_summary.strip()
        if not text and not item.title and not item.url:
            return None
        entities = tuple(dict.fromkeys(part for part in (item.app_id, item.title, item.url) if part))
        return SemanticAnnotation(
            extractor_name=self.name,
            extractor_version=self.version,
            value={"category": item.kind, "entities": entities, "has_text": bool(text)},
            confidence=item.confidence,
        )


class ParserRegistry:
    """Ordered app parser registry with a generic fallback."""

    def __init__(self, parsers: Iterable[ActivityParser] = ()) -> None:
        self._parsers: list[ActivityParser] = list(parsers)
        self._fallback: ActivityParser = GenericActivityParser()

    def register(self, parser: ActivityParser) -> None:
        if not getattr(parser, "name", None) or not getattr(parser, "version", None):
            raise ValueError("activity parser requires name and version")
        self._parsers.insert(0, parser)

    def select(self, item: ActivityItem) -> ActivityParser:
        for parser in self._parsers:
            if parser.supports(item):
                return parser
        return self._fallback

    def parse(self, item: ActivityItem) -> Optional[SemanticAnnotation]:
        parser = self.select(item)
        return parser.parse(item)


__all__ = ["ActivityParser", "GenericActivityParser", "ParserRegistry", "SemanticAnnotation"]
