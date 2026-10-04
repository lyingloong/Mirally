"""Product-owned execution contracts; no UI, Windows, or engine imports."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from openassist.core.state import TERMINAL_STATUSES


class Reobserve(BaseException):
    """The observation/plan is stale; discard it and collect fresh evidence."""


class ExecutionCancelled(asyncio.CancelledError):
    """Product cancellation, deliberately outside ordinary Exception handlers."""


@dataclass(frozen=True)
class ExecutionRequest:
    task_id: str
    text: str
    conversation_context: dict[str, Any] = field(default_factory=dict)
    initial_inputs: tuple[str, ...] = ()


@dataclass(frozen=True)
class Supplement:
    receipt_id: str
    text: str


@dataclass(frozen=True)
class ExecutionCapabilities:
    desktop: bool = True
    pause: bool = False
    cancel: bool = True
    supplement: bool = False
    capture_isolation: bool = False
    cancellation_mode: str = "cooperative"


@dataclass(frozen=True)
class ExecutionServices:
    event: Callable[[dict[str, Any]], None]
    ask: Callable[[str], str]
    confirm: Callable[[str, str], bool]
    checkpoint: Callable[[], int]
    capture: Callable[[str], None]
    take_supplements: Callable[[], list[Supplement]]
    desktop_access: Callable[..., int]
    cancelled: Callable[[], bool]
    revision: Callable[[], int]
    # IDs remain queued until the executor confirms that answers entered its context.
    answer_receipt_ids: Callable[[], tuple[str, ...]] = lambda: ()


@dataclass(frozen=True)
class TaskResult:
    status: str
    message: str = ""
    artifacts: tuple[Any, ...] = ()
    reason: str = ""
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if self.status not in TERMINAL_STATUSES:
            raise ValueError(f"Unknown execution result status: {self.status}")

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status, "message": self.message,
            "artifacts": list(self.artifacts), "reason": self.reason,
            "diagnostics": dict(self.diagnostics),
        }

    @classmethod
    def from_legacy(cls, value: dict[str, Any]) -> "TaskResult":
        """Compatibility for old injected runners; not the native execution API."""
        status = {"done": "completed", "error": "failed"}.get(value.get("status"), value.get("status"))
        if status not in TERMINAL_STATUSES:
            status = "failed"
        return cls(status, str(value.get("message") or ""),
                   tuple(value.get("artifacts") or ()), str(value.get("reason") or ""),
                   dict(value.get("diagnostics") or {}))


class Executor(Protocol):
    capabilities: ExecutionCapabilities

    async def run(self, request: ExecutionRequest, services: ExecutionServices) -> TaskResult:
        ...
