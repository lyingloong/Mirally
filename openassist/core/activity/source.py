"""Consumer-facing observation source protocol.

Consumers depend on this protocol rather than on SQLite or Windows handles.
The concrete ``ObservationStore`` is one implementation; a future IPC client
can implement the same methods without changing UFO/OpenClaw integrations.
"""

from __future__ import annotations

from typing import Any, List, Mapping, Optional, Protocol, Sequence, Tuple

from .models import Observation
from .store import StoreHealth


class ObservationSource(Protocol):
    def query(
        self,
        *,
        start_utc_ms: Optional[int] = None,
        end_utc_ms: Optional[int] = None,
        app_ids: Optional[Sequence[str]] = None,
        actors: Optional[Sequence[str]] = None,
        kinds: Optional[Sequence[str]] = None,
        after_ingest_seq: Optional[int] = None,
        limit: int = 100,
    ) -> List[Observation]: ...

    def get_recent(self, window_seconds: int = 300, *, limit: int = 100) -> List[Observation]: ...

    def get(self, observation_id: str) -> Optional[Observation]: ...

    def read_since(self, after_ingest_seq: int = 0, *, limit: int = 100) -> Tuple[List[Observation], int, bool]: ...

    def ack(self, consumer_id: str, scope_hash: str, cursor: int) -> str: ...

    def resolve_asset(self, asset_id: str, *, scope: Optional[str] = None) -> Mapping[str, Any]: ...

    def health(self) -> StoreHealth: ...
