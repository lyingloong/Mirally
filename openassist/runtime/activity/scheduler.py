"""Bounded, coalescing evidence queue; native calls never run on the poller."""

import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any


@dataclass
class CaptureJob:
    key: str
    snapshot: Any
    generation: int
    triggers: set[str] = field(default_factory=set)
    causes: tuple = ()
    data: Any = None
    created: float = field(default_factory=time.monotonic)


class CaptureQueue:
    def __init__(self, capacity: int = 32, deadline_seconds: float = 15.0) -> None:
        self.capacity = capacity
        self.deadline_seconds = deadline_seconds
        self._values: OrderedDict[str, CaptureJob] = OrderedDict()
        self._condition = threading.Condition()
        self.dropped = 0
        self.expired = 0
        self.coalesced = 0

    def put(self, job: CaptureJob) -> None:
        with self._condition:
            old = self._values.get(job.key)
            if old and old.generation == job.generation:
                job.triggers |= old.triggers
                job.causes = tuple(dict.fromkeys(old.causes + job.causes))[-32:]
                job.created = old.created
                self.coalesced += 1
            self._values[job.key] = job
            if len(self._values) > self.capacity:
                self._values.popitem(last=False)
                self.dropped += 1
            self._condition.notify()

    def get(self, stop: threading.Event):
        with self._condition:
            while not stop.is_set():
                if self._values:
                    _, job = self._values.popitem(last=False)
                    if time.monotonic() - job.created > self.deadline_seconds:
                        self.expired += 1
                        continue
                    return job
                self._condition.wait(0.2)
        return None

    def clear(self):
        with self._condition:
            self._values.clear()
            self._condition.notify_all()

    def status(self):
        with self._condition:
            return {"pending": len(self._values), "dropped": self.dropped,
                    "expired": self.expired, "coalesced": self.coalesced}
