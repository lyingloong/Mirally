"""Privacy-preserving input aggregation primitives.

The aggregator accepts decoded platform events but never stores key values,
characters, pointer coordinates, clipboard data, or raw packets.  A Windows
message-pump adapter can feed it later; keeping this class platform-neutral
makes the policy and timing behavior testable without hooks or a real desktop.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class InputBatch:
    keyboard_count: int
    click_count: int
    scroll_count: int
    motion: bool
    started_monotonic_ns: int
    ended_monotonic_ns: int
    quiet_ms: int
    hwnd: int = 0
    pid: int = 0
    observed_at_utc_ms: int = 0


class InputMetadataAggregator:
    """Bounded counter for one short input burst."""

    def __init__(self, *, max_burst_ms: int = 10_000) -> None:
        self.max_burst_ns = max(1, int(max_burst_ms)) * 1_000_000
        self._started: Optional[int] = None
        self._last: Optional[int] = None
        self._keyboard = 0
        self._clicks = 0
        self._scrolls = 0
        self._motion = False

    def _touch(self, now: Optional[int] = None) -> int:
        value = int(now if now is not None else time.monotonic_ns())
        if self._started is None:
            self._started = value
        self._last = value
        return value

    def keyboard(self, *, now_monotonic_ns: Optional[int] = None) -> None:
        self._touch(now_monotonic_ns)
        self._keyboard = min(self._keyboard + 1, 1_000_000)

    def click(self, *, now_monotonic_ns: Optional[int] = None) -> None:
        self._touch(now_monotonic_ns)
        self._clicks = min(self._clicks + 1, 1_000_000)

    def scroll(self, *, now_monotonic_ns: Optional[int] = None) -> None:
        self._touch(now_monotonic_ns)
        self._scrolls = min(self._scrolls + 1, 1_000_000)

    def motion(self, *, now_monotonic_ns: Optional[int] = None) -> None:
        self._touch(now_monotonic_ns)
        self._motion = True

    def flush(self, *, now_monotonic_ns: Optional[int] = None, quiet_ms: int = 0) -> Optional[InputBatch]:
        if self._started is None or self._last is None:
            return None
        ended = self._last if now_monotonic_ns is None else int(now_monotonic_ns)
        batch = InputBatch(
            self._keyboard,
            self._clicks,
            self._scrolls,
            self._motion,
            self._started,
            ended,
            max(0, int(quiet_ms)),
        )
        self.reset()
        return batch

    def should_flush(self, *, now_monotonic_ns: Optional[int] = None, quiet_ms: int = 1500) -> bool:
        if self._last is None:
            return False
        now = int(now_monotonic_ns if now_monotonic_ns is not None else time.monotonic_ns())
        return now - self._last >= max(0, int(quiet_ms)) * 1_000_000 or now - (self._started if self._started is not None else now) >= self.max_burst_ns

    def reset(self) -> None:
        self._started = None
        self._last = None
        self._keyboard = self._clicks = self._scrolls = 0
        self._motion = False
