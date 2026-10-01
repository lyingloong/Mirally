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
        self._observation: Optional[DesktopObservation] = None
        self._pending_targets: List[Dict[str, Any]] = []
        self._target_failures: Dict[str, int] = {}
        self._target_baselines: Dict[str, Dict[str, Any]] = {}
        self._target_feedback: List[Dict[str, Any]] = []

    def observe(self, observation: DesktopObservation) -> Dict[str, Any]:
        """Update the UI state before the next decision."""
        fingerprint = self.fingerprint(observation)
        self._evaluate_targets(observation)
        self._observation = observation
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
            "target_progress": self._target_feedback,
        }

    def assess(self, actions: Iterable[SingleAgentAction]) -> GuardDecision:
        """Check a proposed effective action batch before it is executed."""
        action_list = list(actions)
        if self._current is None or not action_list:
            return GuardDecision(
                unchanged_observations=self._unchanged_observations
            )

        has_target = False
        target_repeat_count = 0
        for action in action_list:
            target = self._target_snapshot(action, self._observation)
            if target is None:
                continue
            has_target = True
            if target["state"].get("enabled") is False:
                return self._blocked(
                    reason="target_disabled",
                    instruction=(
                        "The target is disabled, possibly while a submission is loading. "
                        "Wait and re-observe the result; do not click or submit again."
                    ),
                    repeat_count=self._target_failures.get(
                        self._target_action_key(action, target), 0
                    ),
                )
            key = self._target_action_key(action, target)
            baseline = self._target_baselines.get(key)
            if baseline is not None and baseline != target["state"]:
                self._target_failures[key] = 0
            failures = self._target_failures.get(key, 0)
            target_repeat_count = max(target_repeat_count, failures + 1)
            if failures + 1 >= self.repeat_limit:
                if (
                    action.type == "click_element" and target["kind"] == "Edit"
                    and target["state"].get("focused") is True
                ):
                    instruction = (
                        "The input field is already focused. Clicking again does not fill it. "
                        "Use fill_text with its current element ID and the supplied text, "
                        "then re-observe before submitting."
                    )
                else:
                    instruction = (
                        "This same action repeatedly failed to verify its expected target effect. "
                        "Unrelated countdowns are not progress. Check the target and result; "
                        "choose a different strategy or ask the user. Do not blindly resubmit."
                    )
                return self._blocked(
                    reason="repeated_action_without_target_progress",
                    instruction=instruction,
                    repeat_count=failures + 1,
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

        if not has_target and repeat_count >= repeat_limit:
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
            not has_target
            and self._unchanged_observations >= self.stagnant_limit
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
            repeat_count=target_repeat_count if has_target else repeat_count,
            unchanged_observations=self._unchanged_observations,
        )

    def record(
        self,
        actions: Iterable[SingleAgentAction],
        *,
        results: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        """Remember actions that were actually attempted from the current UI."""
        action_list = list(actions)
        if self._current is None or not action_list:
            return
        attempted = []
        for index, action in enumerate(action_list):
            if results is not None and index >= len(results):
                break
            result = results[index] if results is not None else {}
            # OS failures with unknown partial effects are still real attempts.
            dispatched = result.get("executed", True) is not False
            if dispatched:
                attempted.append(action)
            if not dispatched and result.get("verified") is not True:
                continue
            target = self._target_snapshot(action, self._observation)
            if target is not None:
                self._pending_targets.append(
                    {
                        "target": target,
                        "key": self._target_action_key(action, target),
                        "action_type": action.type,
                        "verified": result.get("verified"),
                    }
                )
        if attempted:
            self._records.append(
                _ActionRecord(self._current, self._action_hash(attempted))
            )
        self._records = self._records[-self.history_limit :]

    def prompt_context(self) -> Optional[Dict[str, Any]]:
        """Return only actionable guard feedback for the next model call."""
        if not self._last_feedback and not self._target_feedback:
            return None
        return {
            "warning": self._last_feedback,
            "target_progress": self._target_feedback,
            "required_response": (
                "Choose a materially different action or ask the user."
                if self._last_feedback
                else "Continue from the observed target state; verify unknown outcomes."
            ),
        }

    def _evaluate_targets(self, observation: DesktopObservation) -> None:
        self._target_feedback = []
        for pending in self._pending_targets:
            before = pending["target"]
            key = pending["key"]
            after = self._resolve_target(before, observation)
            if after is None:
                # A missing target or changed page requires fresh evidence; it is
                # not proof that the original action succeeded.
                self._target_failures.pop(key, None)
                self._target_baselines.pop(key, None)
                outcome = "unknown_target_changed"
            else:
                self._target_baselines[key] = after["state"]
                if pending["action_type"] == "fill_text":
                    progressed = pending["verified"]
                elif pending["action_type"] == "focus_window":
                    progressed = (
                        before["state"].get("active") is not True
                        and after["state"].get("active") is True
                    )
                elif before["kind"] == "Edit":
                    progressed = (
                        before["state"].get("focused") is not True
                        and after["state"].get("focused") is True
                    ) or (
                        before["state"].get("value_token")
                        != after["state"].get("value_token")
                    )
                else:
                    progressed = before["state"] != after["state"]
                if progressed is True:
                    self._target_failures[key] = 0
                    outcome = (
                        "verified" if pending["action_type"] == "fill_text"
                        else "target_changed"
                    )
                else:
                    self._target_failures[key] = self._target_failures.get(key, 0) + 1
                    outcome = "unverified" if progressed is None else "no_target_progress"
            next_step = "Re-observe and verify the result before further actions or submission."
            if (
                after is not None and before["kind"] == "Edit"
                and pending["action_type"] == "click_element"
                and after["state"].get("focused") is True
            ):
                next_step = "Use fill_text instead of clicking the focused field again."
            self._target_feedback.append(
                {
                    "action": pending["action_type"],
                    "target_type": before["kind"],
                    "outcome": outcome,
                    "no_progress_attempts": self._target_failures.get(key, 0),
                    "next_step": next_step,
                }
            )
        self._pending_targets.clear()

    @classmethod
    def _control_snapshot(
        cls, control: Dict[str, Any], observation: DesktopObservation
    ) -> Dict[str, Any]:
        active = observation.active_window or {}
        stable_id = control.get("stable_id")
        identity = [stable_id, control.get("type")]
        if not stable_id or str(stable_id).startswith("auto:"):
            identity += [control.get("name"), cls._normal_rect(control.get("rect"))]
        window = [active.get("id"), active.get("process"), active.get("name")]
        state = {
            "enabled": control.get("enabled"), "selected": control.get("selected")
        }
        if control.get("type") == "Edit":
            state["focused"] = control.get("focused")
            state.update(observation.target_states.get(control.get("id"), {}))
        if control.get("type") in {"Button", "Hyperlink"}:
            # Editing the form permits a fresh submission attempt. A resend
            # countdown and other Text/Button labels never enter this state.
            state["form_state"] = sorted(
                (
                    str(item.get("stable_id") or (
                        item.get("type"), item.get("name"), item.get("rect")
                    )),
                    str(observation.target_states.get(item.get("id"), {}).get("value_token")),
                    str(item.get("selected")),
                )
                for item in observation.controls
                if item.get("type") in {"Edit", "CheckBox", "RadioButton"}
            )
        return {
            "key": json.dumps([window, identity], sort_keys=True),
            "kind": control.get("type"), "state": state,
        }

    @classmethod
    def _target_snapshot(
        cls, action: SingleAgentAction, observation: Optional[DesktopObservation]
    ) -> Optional[Dict[str, Any]]:
        if observation is None:
            return None
        if action.type == "focus_window":
            window = next(
                (item for item in observation.windows if item.get("id") == action.window_id),
                None,
            )
            if window is not None:
                return {
                    "key": json.dumps([
                        "Window", window.get("id"), window.get("process"), window.get("name")
                    ]),
                    "kind": "Window", "state": {"active": window.get("active")},
                }
        if action.type in {"click_element", "fill_text"}:
            control = next(
                (item for item in observation.controls if item.get("id") == action.element_id),
                None,
            )
            if control is not None:
                return cls._control_snapshot(control, observation)
        return None

    @classmethod
    def _resolve_target(
        cls, target: Dict[str, Any], observation: DesktopObservation
    ) -> Optional[Dict[str, Any]]:
        if target["kind"] == "Window":
            for window in observation.windows:
                candidate = cls._target_snapshot(
                    SingleAgentAction(type="focus_window", window_id=window["id"]),
                    observation,
                )
                if candidate is not None and candidate["key"] == target["key"]:
                    return candidate
        else:
            for control in observation.controls:
                candidate = cls._control_snapshot(control, observation)
                if candidate["key"] == target["key"]:
                    return candidate
        return None

    @staticmethod
    def _target_action_key(action: SingleAgentAction, target: Dict[str, Any]) -> str:
        intent = action.model_dump(
            exclude={"element_id", "window_id", "description", "sensitive"},
            exclude_none=True,
        )
        encoded = json.dumps([target["key"], intent], sort_keys=True).encode()
        return hashlib.sha256(encoded).hexdigest()

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
