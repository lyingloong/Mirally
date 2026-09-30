"""Deterministic loop and no-progress detection for the single agent."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from PIL import Image

from ufo.agents.single_agent.schemas import DesktopObservation, SingleAgentAction


@dataclass(frozen=True)
class ObservationFingerprint:
    """Stable UI structure plus a small perceptual screenshot hash."""

    structure_hash: str
    image_hash: Optional[int]

    @property
    def state_id(self) -> str:
        image = "none" if self.image_hash is None else f"{self.image_hash:016x}"
        return f"{self.structure_hash[:12]}:{image}"


@dataclass(frozen=True)
class GuardDecision:
    """Result of checking a proposed action batch."""

    blocked: bool = False
    terminal: bool = False
    reason: Optional[str] = None
    recovery_instruction: Optional[str] = None
    repeat_count: int = 0
    unchanged_observations: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "blocked": self.blocked,
            "terminal": self.terminal,
            "reason": self.reason,
            "recovery_instruction": self.recovery_instruction,
            "repeat_count": self.repeat_count,
            "unchanged_observations": self.unchanged_observations,
        }


@dataclass(frozen=True)
class _ActionRecord:
    state: ObservationFingerprint
    action_hash: str


class LoopGuard:
    """Block repeated actions on an unchanged UI without another model call."""

    def __init__(
        self,
        *,
        repeat_limit: int = 3,
        stagnant_limit: int = 4,
        max_blocked_attempts: int = 2,
        image_distance: int = 4,
        history_limit: int = 16,
    ) -> None:
        self.repeat_limit = max(2, repeat_limit)
        self.stagnant_limit = max(2, stagnant_limit)
        self.max_blocked_attempts = max(1, max_blocked_attempts)
        self.image_distance = max(0, image_distance)
        self.history_limit = max(self.repeat_limit, history_limit)
        self._current: Optional[ObservationFingerprint] = None
        self._previous: Optional[ObservationFingerprint] = None
        self._records: List[_ActionRecord] = []
        self._unchanged_observations = 0
        self._consecutive_blocks = 0
        self._stagnation_alerted = False
        self._last_feedback: Optional[str] = None

    def observe(self, observation: DesktopObservation) -> Dict[str, Any]:
        """Update the UI state before the next decision."""
        fingerprint = self.fingerprint(observation)
        changed = self._previous is not None and not self._same_state(
            self._previous, fingerprint
        )
        if self._previous is None or changed:
            self._unchanged_observations = 0
            self._stagnation_alerted = False
        else:
            self._unchanged_observations += 1
        self._previous = fingerprint
        self._current = fingerprint
        return {
            "state_id": fingerprint.state_id,
            "changed": changed,
            "unchanged_observations": self._unchanged_observations,
        }

    def assess(self, actions: Iterable[SingleAgentAction]) -> GuardDecision:
        """Check a proposed effective action batch before it is executed."""
        action_list = list(actions)
        if self._current is None or not action_list:
            return GuardDecision(
                unchanged_observations=self._unchanged_observations
            )

        action_hash = self._action_hash(action_list)
        matching_records = sum(
            1
            for record in self._records
            if record.action_hash == action_hash
            and self._same_state(record.state, self._current)
        )
        repeat_count = matching_records + 1
        repeat_limit = (
            self.repeat_limit + 1
            if self._only_waits(action_list)
            else self.repeat_limit
        )

        if repeat_count >= repeat_limit:
            return self._blocked(
                reason="repeated_action_without_ui_change",
                instruction=(
                    "The same action has already been tried on this unchanged UI. "
                    "Do not repeat it. Re-observe the available controls and use a "
                    "different interaction strategy; ask the user if no alternative "
                    "is safe."
                ),
                repeat_count=repeat_count,
            )

        if (
            self._unchanged_observations >= self.stagnant_limit
            and not self._stagnation_alerted
        ):
            self._stagnation_alerted = True
            self._unchanged_observations = 0
            return self._blocked(
                reason="no_ui_progress",
                instruction=(
                    "The visible UI has not changed across several decision rounds. "
                    "Discard stale element assumptions and choose a different route."
                ),
                repeat_count=repeat_count,
            )

        self._consecutive_blocks = 0
        self._last_feedback = None
        return GuardDecision(
            repeat_count=repeat_count,
            unchanged_observations=self._unchanged_observations,
        )

    def record(
        self,
        actions: Iterable[SingleAgentAction],
    ) -> None:
        """Remember actions that were actually attempted from the current UI."""
        action_list = list(actions)
        if self._current is None or not action_list:
            return
        self._records.append(
            _ActionRecord(self._current, self._action_hash(action_list))
        )
        self._records = self._records[-self.history_limit :]

    def prompt_context(self) -> Optional[Dict[str, Any]]:
        """Return only actionable guard feedback for the next model call."""
        if not self._last_feedback:
            return None
        return {
            "warning": self._last_feedback,
            "required_response": (
                "Choose a materially different action or ask the user."
            ),
        }

    def _blocked(
        self,
        *,
        reason: str,
        instruction: str,
        repeat_count: int,
    ) -> GuardDecision:
        self._consecutive_blocks += 1
        self._last_feedback = instruction
        return GuardDecision(
            blocked=True,
            terminal=self._consecutive_blocks >= self.max_blocked_attempts,
            reason=reason,
            recovery_instruction=instruction,
            repeat_count=repeat_count,
            unchanged_observations=self._unchanged_observations,
        )

    def _same_state(
        self,
        left: ObservationFingerprint,
        right: ObservationFingerprint,
    ) -> bool:
        if left.structure_hash != right.structure_hash:
            return False
        if left.image_hash is None or right.image_hash is None:
            return True
        return (left.image_hash ^ right.image_hash).bit_count() <= self.image_distance

    @classmethod
    def fingerprint(cls, observation: DesktopObservation) -> ObservationFingerprint:
        active = observation.active_window or {}
        controls = sorted(
            (
                {
                    "name": str(control.get("name", "")).strip().casefold(),
                    "type": str(control.get("type", "")),
                    "rect": cls._normal_rect(control.get("rect")),
                    "selected": control.get("selected"),
                }
                for control in observation.controls
            ),
            key=lambda item: (
                str(item["rect"]),
                item["type"],
                item["name"],
            ),
        )
        structure = {
            "active": {
                "name": str(active.get("name", "")).strip().casefold(),
                "process": str(active.get("process", "")).strip().casefold(),
                "rect": cls._normal_rect(active.get("rect")),
            },
            "controls": controls,
        }
        encoded = json.dumps(
            structure, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return ObservationFingerprint(
            structure_hash=hashlib.sha256(encoded).hexdigest(),
            image_hash=cls._image_dhash(observation.screenshot_path),
        )

    @staticmethod
    def _normal_rect(rect: Any) -> Optional[List[int]]:
        try:
            return [int(value) for value in rect]
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _image_dhash(path: str) -> Optional[int]:
        try:
            with Image.open(Path(path)) as image:
                gray = image.convert("L").resize((9, 8))
                pixels = list(gray.getdata())
        except (OSError, ValueError):
            return None

        value = 0
        for row in range(8):
            offset = row * 9
            for column in range(8):
                value = (value << 1) | int(
                    pixels[offset + column] > pixels[offset + column + 1]
                )
        return value

    @staticmethod
    def _action_hash(actions: List[SingleAgentAction]) -> str:
        payload = [
            action.model_dump(exclude={"description", "sensitive"}, exclude_none=True)
            for action in actions
        ]
        encoded = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _only_waits(actions: List[SingleAgentAction]) -> bool:
        return all(action.type == "wait" for action in actions)
