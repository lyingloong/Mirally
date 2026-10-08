"""Activity sensor lifecycle and platform-neutral ingestion facade."""

from __future__ import annotations

import os
import hashlib
import json
import sys
import shutil
import threading
import time
from collections import deque
from dataclasses import dataclass, replace
from typing import Any, Mapping, Optional

from openassist.core.activity.models import (
    Actor,
    CollectionLevel,
    Observation,
    opaque_id,
)
from openassist.core.activity.policy import ActivityPolicy
from openassist.core.activity.store import ObservationStore
from openassist.core.activity.input_text import InputTextTracker
from openassist.platform.activity.clipboard import WindowsClipboardSource
from openassist.platform.activity.monitors import (
    MonitorSnapshot,
    WindowsMonitorSource,
    image_black_fraction,
    mask_image,
)
from openassist.platform.activity.foreground import ForegroundSnapshot, WindowsForegroundSource
from openassist.platform.activity.browser_url import BrowserURLResult, WindowsBrowserURLSource
from openassist.platform.activity.input_metadata import InputBatch
from openassist.platform.activity.input_pump import WindowsInputMetadataSource
from openassist.platform.activity.uia_structure import UIAStructureResult, WindowsUIASource
from openassist.platform.activity.ocr import OCRResult, WindowsNativeOCRSource
from openassist.platform.activity.wgc_capture import (
    CaptureResult,
    VisualSignatureGate,
    WindowsWindowCaptureSource,
)

from .config import ActivityConfig
from .scheduler import CaptureJob, CaptureQueue


@dataclass(frozen=True)
class AgentControlLease:
    lease_id: str
    task_id: str
    session_id: Optional[str]
    pid: int
    process_start_time: Optional[float]
    started_at_monotonic: float
    expires_at_monotonic: float

    @property
    def active(self) -> bool:
        return time.monotonic() < self.expires_at_monotonic


class ActivitySensor:
    """Safe ingestion facade used by Windows sources and future UFO bridges.

    The sensor itself does not start automatically.  Product UI or a future
    host lifecycle explicitly creates and runs it after the user enables the
    activity feature.
    """

    def __init__(
        self,
        config: ActivityConfig,
        *,
        store: Optional[ObservationStore] = None,
        source: Optional[WindowsForegroundSource] = None,
        input_source: Optional[WindowsInputMetadataSource] = None,
        uia_source: Optional[WindowsUIASource] = None,
        visual_source: Optional[WindowsWindowCaptureSource] = None,
        ocr_source: Optional[WindowsNativeOCRSource] = None,
        browser_source: Optional[WindowsBrowserURLSource] = None,
        clipboard_source=None,
        monitor_source=None,
        source_id: str = "windows-desktop",
    ) -> None:
        self.config = config
        # The product must never record its own companion/WebView windows as
        # user activity.  FrontendRegistry can add verified descendants when
        # the host is available; these stable executable names cover the
        # standalone sensor before that bridge is attached.
        self.policy: ActivityPolicy = config.policy.with_self_apps(
            {
                os.path.basename(sys.executable).lower(),
                "openassist.exe",
                "mirally.exe",
                "msedgewebview2.exe",
            }
        )
        if self.policy.exclude_self:
            self.policy = replace(self.policy, excluded_windows=self.policy.excluded_windows +
                                  ("*mirally activity monitor*",))
        self.store = store or ObservationStore(config.database_path, asset_directory=config.asset_directory)
        self.source = source or WindowsForegroundSource(
            include_window_title=config.window_titles_enabled
        )
        self.browser_source = browser_source
        if self.browser_source is None and config.browser_urls_enabled:
            self.browser_source = WindowsBrowserURLSource()
        self.input_source = input_source
        if self.input_source is None and config.input_enabled:
            self.input_source = WindowsInputMetadataSource(
                quiet_ms=config.input_quiet_ms,
                max_burst_ms=config.input_max_burst_ms,
                clipboard=config.clipboard_enabled,
            )
        self.uia_source = uia_source
        if self.uia_source is None and config.ui_structure_enabled:
            self.uia_source = WindowsUIASource()
        self.visual_source = visual_source
        if self.visual_source is None and config.visual_enabled:
            self.visual_source = WindowsWindowCaptureSource()
        self.ocr_source = ocr_source
        if self.ocr_source is None and config.visual_enabled and config.ocr_enabled:
            self.ocr_source = WindowsNativeOCRSource()
        self._visual_gate = VisualSignatureGate()
        self._ocr_retries: dict[str, tuple[Observation, int, float]] = {}
        self.clipboard_source = clipboard_source or WindowsClipboardSource()
        self.monitor_source = monitor_source or WindowsMonitorSource()
        self._input_text = InputTextTracker()
        self._clipboard_sequences: deque[int] = deque(maxlen=128)
        self._queue = CaptureQueue()
        self._evidence_thread = None
        self._generation = 0
        self._job_local = threading.local()
        self._last_evidence_at = 0.0
        self._last_monitors_at = 0.0
        self._last_cleanup_at = 0.0
        self._uia_signatures: dict[str, str] = {}
        self._seq_lock = threading.Lock()
        self._state_lock = threading.RLock()
        self._visual_times: deque[float] = deque()
        self.source_id = source_id
        self.source_epoch = opaque_id("epoch")
        self._source_seq = 0
        self._previous_foreground: Optional[ForegroundSnapshot] = None
        self._last_window_context_key: Optional[tuple[object, ...]] = None
        self._browser_window_id: Optional[str] = None
        self._browser_window_title: Optional[str] = None
        self._browser_context: Optional[BrowserURLResult] = None
        self._browser_next_poll_at = 0.0
        self._browser_failures = 0
        self._lease: Optional[AgentControlLease] = None
        self._stop = threading.Event()
        self._started = False
        self._paused = False
        self._metrics_lock = threading.Lock()
        self._metrics: dict[str, Any] = {
            "started_count": 0,
            "events_total": 0,
            "events_by_kind": {},
            "foreground_events": 0,
            "uia_calls": 0,
            "uia_by_status": {},
            "capture_calls": 0,
            "capture_by_status": {},
            "black_frame_skips": 0,
            "black_filter_errors": 0,
            "ocr_calls": 0,
            "ocr_by_status": {},
            "browser_url_calls": 0,
            "browser_url_by_status": {},
            "input_batches": 0,
            "last_event_at_utc_ms": None,
        }

    def _metric_inc(self, name: str, amount: int = 1) -> None:
        with self._metrics_lock:
            self._metrics[name] = int(self._metrics.get(name, 0)) + int(amount)

    def _metric_status(self, name: str, status: str) -> None:
        with self._metrics_lock:
            values = self._metrics.setdefault(name, {})
            values[str(status)] = int(values.get(str(status), 0)) + 1

    def metrics_snapshot(self) -> dict[str, Any]:
        """Return a JSON-safe snapshot for the local diagnostics monitor."""

        with self._metrics_lock:
            result = dict(self._metrics)
            for name in (
                "events_by_kind",
                "uia_by_status",
                "capture_by_status",
                "ocr_by_status",
                "browser_url_by_status",
            ):
                result[name] = dict(self._metrics.get(name, {}))
            result["started"] = bool(self._started)
            result["paused"] = bool(self._paused)
            result["stop_requested"] = self._stop.is_set()
            result["scheduler"] = self._queue.status()
            status = getattr(self.visual_source, "status", None)
            result["capture_runtime"] = status() if status is not None else {}
            return result

    def _visual_lifecycle(self, action: str) -> None:
        callback = getattr(self.visual_source, action, None)
        if callback is not None:
            callback()

    def _next_seq(self) -> int:
        with self._seq_lock:
            value = self._source_seq
            self._source_seq += 1
            return value

    def _content_allowed(self, snapshot) -> bool:
        return self._current(snapshot) and self.policy.decide(
            CollectionLevel.CONTENT, app_id=snapshot.app_id, window_name=snapshot.window_title,
            control_owner=self._control_owner(), paused=self._paused).allowed

    def _current(self, snapshot) -> bool:
        if self._stop.is_set() or self._paused:
            return False
        job = getattr(self._job_local, "job", None)
        if job is not None and job.generation != self._generation:
            return False
        if snapshot.hwnd == 0 and self.config.visual_scope == "monitors":
            return self.monitor_source.desktop_available()
        matches = getattr(self.source, "matches", None)
        return bool(matches(snapshot)) if matches is not None else True

    def start_evidence_worker(self) -> None:
        if self._evidence_thread is not None and self._evidence_thread.is_alive():
            return
        self._evidence_thread = threading.Thread(target=self._evidence_loop, name="activity-evidence", daemon=True)
        self._evidence_thread.start()

    def _submit(self, snapshot, trigger, causes=(), *, key=None, data=None) -> None:
        job = CaptureJob(key or snapshot.window_instance_id, snapshot, self._generation,
                         {trigger}, tuple(causes), data)
        if self._evidence_thread is not None:
            self._queue.put(job)
        else:
            self._execute_job(job)

    def _evidence_loop(self) -> None:
        while not self._stop.is_set():
            job = self._queue.get(self._stop)
            if job is not None:
                try:
                    self._execute_job(job)
                except Exception as error:
                    self._metric_inc("evidence_errors")
                    reason = str(error).splitlines()[0][:160] or type(error).__name__
                    self.emit("collector_diagnostic", payload={"stage": "evidence", "error": type(error).__name__,
                                                                 "reason": reason},
                              quality="unavailable", missing_reasons=("evidence_error",))

    def _execute_job(self, job: CaptureJob) -> None:
        self._job_local.job = job
        try:
            if not self._current(job.snapshot):
                self._metric_inc("stale_jobs")
                return
            if self._evidence_thread is threading.current_thread() and time.monotonic() - self._last_cleanup_at >= 60:
                self.store.cleanup(observation_retention_days=self.config.observation_retention_days,
                                   max_asset_bytes=self.config.image_quota_mib * 1024 * 1024)
                self._last_cleanup_at = time.monotonic()
            with self._metrics_lock:
                self._metrics["last_triggers"] = sorted(job.triggers)
            if "clipboard" in job.triggers:
                self._record_clipboard(job)
            elif isinstance(job.data, MonitorSnapshot):
                self._record_content(job.snapshot, None, None, opaque_id("capture"),
                                     trigger="+".join(sorted(job.triggers)), monitor=job.data, causes=job.causes)
            else:
                self._capture_evidence(job.snapshot, job.causes, "+".join(sorted(job.triggers)))
        finally:
            self._job_local.job = None

    def _control_owner(self) -> str:
        if self._lease is not None and self._lease.active:
            return "agent"
        self._lease = None
        return "unclaimed"

    def _visual_budget_available(self) -> bool:
        now = time.monotonic()
        while self._visual_times and now - self._visual_times[0] >= 60.0:
            self._visual_times.popleft()
        if len(self._visual_times) >= self.config.visual_max_per_minute:
            return False
        self._visual_times.append(now)
        return True

    def _refresh_external_state(self) -> None:
        pause_state = self.store.get_state("collector_pause", {})
        paused = bool(isinstance(pause_state, Mapping) and pause_state.get("paused"))
        with self._state_lock:
            if paused != self._paused:
                self._invalidate_evidence()
                self._visual_lifecycle("pause" if paused else "resume")
            self._paused = paused
        collector_state = self.store.get_state("collector", {})
        if isinstance(collector_state, Mapping) and collector_state.get("status") == "stop_requested":
            self._stop.set()
            self._visual_lifecycle("pause")

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._stop.clear()
        if not self._paused:
            self._visual_lifecycle("resume")
        self._metric_inc("started_count")
        input_started = True
        if self.config.enabled and self.config.input_enabled and self.input_source is not None:
            if getattr(self.input_source, "supports_content_events", False):
                input_started = self.input_source.start(self.record_input_metadata, event_callback=self.record_input_event)
            else:
                input_started = self.input_source.start(self.record_input_metadata)
        baseline_degraded = bool(
            self.config.enabled
            and (
                not self.config.input_enabled
                or not input_started
                or self.policy.default_level == CollectionLevel.METADATA
                or not self.config.ui_structure_enabled
                or self.uia_source is None
                or not bool(getattr(self.uia_source, "available", False))
            )
        )
        self.store.set_state(
            "collector",
            {
                "status": (
                    "disabled"
                    if not self.config.enabled
                    else "degraded"
                    if baseline_degraded
                    else "running"
                ),
                "source_id": self.source_id,
                "source_epoch": self.source_epoch,
                "policy_version": self.policy.policy_version,
                "baseline": {
                    "foreground_metadata": True,
                    "input_metadata": bool(self.config.input_enabled and input_started),
                    "uia_structure": bool(
                        self.policy.default_level != CollectionLevel.METADATA
                        and self.config.ui_structure_enabled
                        and self.uia_source is not None
                        and getattr(self.uia_source, "available", False)
                    ),
                },
                "started_at_utc_ms": int(time.time() * 1000),
            },
        )

    def pause(self, reason: str = "user") -> None:
        with self._state_lock:
            self._paused = True
            self._invalidate_evidence()
            self._visual_lifecycle("pause")
        self.store.set_state("collector_pause", {"paused": True, "reason": reason, "at": int(time.time() * 1000)})

    def resume(self) -> None:
        with self._state_lock:
            self._paused = False
            self._invalidate_evidence()
            self._visual_lifecycle("resume")
        self._previous_foreground = None
        self._last_window_context_key = None
        self._browser_window_id = None
        self.store.set_state("collector_pause", {"paused": False, "at": int(time.time() * 1000)})

    def _invalidate_evidence(self) -> None:
        self._generation += 1
        self._queue.clear()
        self._input_text.clear()
        self._uia_signatures.clear()
        self._visual_gate = VisualSignatureGate()
        self._ocr_retries.clear()
        self._last_evidence_at = self._last_monitors_at = 0.0

    def stop(self) -> None:
        with self._state_lock:
            self._stop.set()
            self._queue.clear()
            self._visual_lifecycle("pause")
        if self.input_source is not None:
            stop_input = getattr(self.input_source, "stop", None)
            if stop_input is not None:
                stop_input()
        if self._started:
            self.store.set_state("collector", {"status": "stopped", "at": int(time.time() * 1000)})
        self._started = False

    def close(self) -> None:
        self.stop()
        worker = self._evidence_thread
        if worker is not None and worker is not threading.current_thread():
            # Each platform call has its own killable-process deadline.
            worker.join(timeout=(self.config.uia_timeout_ms + self.config.visual_timeout_ms +
                                self.config.ocr_timeout_ms + self.config.browser_url_timeout_ms +
                                self.config.clipboard_timeout_ms) / 1000 + 3)
            if worker.is_alive():
                raise RuntimeError("activity evidence worker did not stop; store remains open")
        self._evidence_thread = None
        self._visual_lifecycle("close")
        self.store.close()

    def emit(
        self, kind: str, **kwargs,
    ) -> Optional[Observation]:
        with self._state_lock:
            return self._emit(kind, **kwargs)

    def _emit(
        self,
        kind: str,
        *,
        requested_level: str = CollectionLevel.METADATA,
        app_id: Optional[str] = None,
        process_instance_id: Optional[str] = None,
        window_instance_id: Optional[str] = None,
        session_instance_id: Optional[str] = None,
        payload: Optional[Mapping[str, Any]] = None,
        actor: str = Actor.UNKNOWN,
        actor_basis: str = "unclaimed",
        quality: str = "complete",
        missing_reasons: tuple[str, ...] = (),
        identity_confidence: str = "unknown",
        uia_summary: Optional[Mapping[str, Any]] = None,
        visual_summary: Optional[Mapping[str, Any]] = None,
        ocr_summary: Optional[Mapping[str, Any]] = None,
        asset_refs: tuple[str, ...] = (),
        caused_by: tuple[str, ...] = (),
        capture_group_id: Optional[str] = None,
        observed_at_utc_ms: Optional[int] = None,
        monotonic_value: Optional[int] = None,
        capture_started_at: Optional[int] = None,
        capture_finished_at: Optional[int] = None,
        truncated: bool = False,
    ) -> Optional[Observation]:
        job = getattr(self._job_local, "job", None)
        if self._stop.is_set() or (job is not None and not self._current(job.snapshot)):
            return None
        decision = self.policy.decide(
            requested_level,
            app_id=app_id,
            window_name=job.snapshot.window_title if job is not None else None,
            control_owner=self._control_owner(),
            paused=self._paused,
        )
        if not decision.allowed:
            return None
        safe_payload = dict(payload or {})
        if len(str(safe_payload).encode("utf-8")) > self.policy.max_payload_bytes:
            safe_payload = {"truncated": True, "original_kind": kind}
            quality = "partial"
            missing_reasons = tuple(missing_reasons) + ("payload_limit",)
        now = int(time.time() * 1000)
        observation = Observation.create(
            kind=kind,
            source_id=self.source_id,
            source_epoch=self.source_epoch,
            source_seq=self._next_seq(),
            observed_at_utc_ms=observed_at_utc_ms or now,
            monotonic_ns=monotonic_value or time.monotonic_ns(),
            boot_id=getattr(self.source, "_boot", "unknown"),
            session_instance_id=session_instance_id,
            process_instance_id=process_instance_id,
            window_instance_id=window_instance_id,
            app_id=app_id,
            actor=actor,
            actor_basis=actor_basis,
            control_owner=self._control_owner(),
            policy_version=decision.policy_version,
            collection_level=decision.level,
            privacy_status=decision.status,
            payload=safe_payload,
            uia_summary=uia_summary,
            visual_summary=visual_summary,
            ocr_summary=ocr_summary,
            asset_refs=asset_refs,
            quality=quality,
            missing_reasons=missing_reasons,
            identity_confidence=identity_confidence,
            caused_by=caused_by,
            capture_group_id=capture_group_id,
            capture_started_at=capture_started_at,
            capture_finished_at=capture_finished_at,
            truncated=truncated,
        )
        stored = self.store.append(observation)
        with self._metrics_lock:
            self._metrics["events_total"] = int(self._metrics.get("events_total", 0)) + 1
            by_kind = self._metrics.setdefault("events_by_kind", {})
            by_kind[kind] = int(by_kind.get(kind, 0)) + 1
            self._metrics["last_event_at_utc_ms"] = stored.received_at_utc_ms or now
        return stored

    def record_foreground(self, snapshot: ForegroundSnapshot) -> Optional[Observation]:
        self._metric_inc("foreground_events")
        if not self.config.enabled or not self.policy.decide(
            CollectionLevel.METADATA,
            app_id=snapshot.app_id,
            window_name=snapshot.window_title,
            control_owner=self._control_owner(),
            paused=self._paused,
        ).allowed:
            self._previous_foreground = None
            self._last_window_context_key = None
            self._browser_window_id = None
            return None
        previous = self._previous_foreground
        same_window = previous is not None and previous.window_instance_id == snapshot.window_instance_id
        self._previous_foreground = snapshot
        if same_window:
            now = time.monotonic()
            if self._evidence_thread is None:
                self._record_window_context(snapshot, None, opaque_id("capture"))
            if self.config.continuous_enabled and (previous.window_title != snapshot.window_title
                    or now - self._last_evidence_at >= self.config.capture_interval_seconds):
                self._last_evidence_at = now
                self._submit(snapshot, "periodic_visual_check")
            return None
        capture_group_id = opaque_id("capture")
        metadata = self.emit(
            "foreground_changed",
            app_id=snapshot.app_id,
            process_instance_id=snapshot.process_instance_id,
            window_instance_id=snapshot.window_instance_id,
            payload={"transition": "foreground_settled", "title_available": snapshot.title_available},
            actor=Actor.UNKNOWN,
            actor_basis="foreground_only",
            identity_confidence=snapshot.identity_confidence,
            capture_group_id=capture_group_id,
            observed_at_utc_ms=snapshot.observed_at_utc_ms,
            monotonic_value=snapshot.monotonic_ns,
        )
        if metadata is None:
            return metadata
        self._last_evidence_at = time.monotonic()
        self._submit(snapshot, "foreground_settled", (metadata.observation_id,))
        return metadata

    def _capture_evidence(self, snapshot, causes: tuple[str, ...], trigger: str) -> None:
        capture_group_id = opaque_id("capture")
        started_at = int(time.time() * 1000)
        if not self._current(snapshot):
            return
        self._record_window_context(snapshot, None, capture_group_id)
        structure = None
        if self.config.ui_structure_enabled and self.uia_source is not None and self.policy.decide(
                CollectionLevel.STRUCTURE, app_id=snapshot.app_id, window_name=snapshot.window_title,
                control_owner=self._control_owner(), paused=self._paused).allowed:
            self._metric_inc("uia_calls")
            uia_started = time.monotonic()
            content_enabled = self.config.uia_content_enabled and self._content_allowed(snapshot)
            try:
                content_args = {"include_content": True, "max_text_chars": self.config.uia_max_text_chars} if content_enabled else {}
                result = self.uia_source.inspect(
                    snapshot.hwnd,
                    timeout_ms=self.config.uia_timeout_ms,
                    max_depth=self.config.uia_max_depth,
                    max_nodes=self.config.uia_max_nodes,
                    **content_args,
                )
            except Exception as error:
                self._metric_inc("uia_errors")
                result = UIAStructureResult(
                    "unavailable",
                    missing_reasons=("source_error", type(error).__name__),
                )
            self._metric_status("uia_by_status", result.status)
            with self._metrics_lock:
                self._metrics["uia_last_duration_ms"] = round((time.monotonic() - uia_started) * 1000, 2)
            if result.status == "ok":
                quality = "partial" if result.truncated else "complete"
            else:
                quality = "unavailable"
            missing_reasons = tuple(result.missing_reasons)
            if result.status != "ok" and not missing_reasons:
                missing_reasons = ("uia_unavailable",)
            if not self._current(snapshot):
                self._metric_inc("stale_results")
                return
            if content_enabled and self.config.input_text_enabled:
                trigger_parts = set(trigger.split("+"))
                # Periodic visual checks also sample UIA text so a changed
                # editable control is captured, but unsupported input metadata
                # should only produce an unavailable event for an actual input
                # signal.
                input_trigger = trigger != "foreground_settled"
                input_signal = bool({"typing_pause", "paste", "click", "scroll_stop"}
                                    & trigger_parts)
                delta = self._input_text.observe(snapshot.window_instance_id, result.focused_control,
                                                 input_trigger=input_trigger)
                if delta is not None:
                    self.emit("input_text", requested_level=CollectionLevel.CONTENT, app_id=snapshot.app_id,
                              process_instance_id=snapshot.process_instance_id, window_instance_id=snapshot.window_instance_id,
                              payload=delta, quality="partial" if delta.get("truncated") or not delta["baseline_known"] else "complete",
                              missing_reasons=("initial_baseline_unknown",) if not delta["baseline_known"] else (),
                              caused_by=causes, capture_group_id=capture_group_id, actor_basis="observed_committed_text")
                elif input_signal and result.focused_control.get("text_status") != "ok":
                    self.emit("input_text", requested_level=CollectionLevel.CONTENT, app_id=snapshot.app_id,
                              window_instance_id=snapshot.window_instance_id, payload={"status": "unavailable"},
                              quality="unavailable", missing_reasons=(str(result.focused_control.get("text_status", "no_editable_focus")),),
                              caused_by=causes, capture_group_id=capture_group_id)
            summary = result.as_dict()
            fingerprint = hashlib.sha256(json.dumps({key: value for key, value in summary.items()
                if key != "duration_ms"}, sort_keys=True).encode()).hexdigest()
            if self._uia_signatures.get(snapshot.window_instance_id) != fingerprint:
                structure = self.emit(
                    "context_snapshot",
                    requested_level=CollectionLevel.CONTENT if content_enabled else CollectionLevel.STRUCTURE,
                    app_id=snapshot.app_id,
                    process_instance_id=snapshot.process_instance_id,
                    window_instance_id=snapshot.window_instance_id,
                    payload={"trigger": trigger, "uia_status": result.status},
                    uia_summary=summary,
                    actor=Actor.UNKNOWN,
                    actor_basis="foreground_only",
                    quality=quality,
                    missing_reasons=missing_reasons,
                    identity_confidence=snapshot.identity_confidence,
                    caused_by=causes,
                    capture_group_id=capture_group_id,
                    observed_at_utc_ms=int(time.time() * 1000),
                    monotonic_value=time.monotonic_ns(),
                    capture_started_at=started_at,
                    capture_finished_at=int(time.time() * 1000),
                    truncated=result.truncated,
                )
                if structure is not None and result.status == "ok":
                    if len(self._uia_signatures) >= 128:
                        self._uia_signatures.pop(next(iter(self._uia_signatures)))
                    self._uia_signatures[snapshot.window_instance_id] = fingerprint
        if self.config.visual_scope == "foreground":
            self._record_content(snapshot, None, structure, capture_group_id, trigger=trigger, causes=causes)
        else:
            for monitor in self.monitor_source.enumerate():
                self._submit_monitor(snapshot, monitor, causes, trigger=trigger)

    def _record_window_context(
        self,
        snapshot: ForegroundSnapshot,
        caused_by: Optional[Observation],
        capture_group_id: str,
    ) -> Optional[Observation]:
        """Persist title and browser URL without changing the UIA schema."""

        if not self.config.enabled or not self.policy.decide(
            CollectionLevel.METADATA,
            app_id=snapshot.app_id,
            window_name=snapshot.window_title,
            control_owner=self._control_owner(),
            paused=self._paused,
        ).allowed:
            return None
        browser_result: Optional[BrowserURLResult] = None
        if (
            self.config.browser_urls_enabled
            and self.browser_source is not None
            and self.browser_source.supports_app(snapshot.app_id)
        ):
            started = time.monotonic()
            changed_window = self._browser_window_id != snapshot.window_instance_id
            if changed_window:
                self._browser_failures = 0
            if (
                changed_window
                or self._browser_window_title != snapshot.window_title
                or started >= self._browser_next_poll_at
            ):
                self._metric_inc("browser_url_calls")
                try:
                    browser_result = self.browser_source.read(
                        snapshot.hwnd,
                        app_id=snapshot.app_id,
                        timeout_ms=self.config.browser_url_timeout_ms,
                    )
                except Exception as error:
                    self._metric_inc("browser_url_errors")
                    browser_result = BrowserURLResult(
                        "error", missing_reasons=("source_error", type(error).__name__)
                    )
                self._browser_failures = 0 if browser_result.status == "ok" else min(4, self._browser_failures + 1)
                self._browser_next_poll_at = time.monotonic() + min(
                    max(30.0, self.config.browser_url_poll_interval_seconds),
                    self.config.browser_url_poll_interval_seconds * (2 ** self._browser_failures),
                )
                self._browser_window_id = snapshot.window_instance_id
                self._browser_window_title = snapshot.window_title
                self._browser_context = browser_result
                self._metric_status("browser_url_by_status", browser_result.status)
                with self._metrics_lock:
                    self._metrics["browser_url_last_duration_ms"] = round(
                        (time.monotonic() - started) * 1000, 2
                    )
            else:
                browser_result = self._browser_context
        else:
            self._browser_window_id = None

        title = snapshot.window_title if self.config.window_titles_enabled else None
        url = browser_result.url if browser_result is not None else None
        status = browser_result.status if browser_result is not None else None
        reasons = browser_result.missing_reasons if browser_result is not None else ()
        if title is None and browser_result is None:
            return None
        context_key = (snapshot.window_instance_id, title, url, status)
        if context_key == self._last_window_context_key:
            return None
        quality = "complete"
        missing_reasons = tuple(reasons)
        if browser_result is not None and browser_result.status not in {"ok", "not_browser"}:
            quality = "partial"
        observation = self.emit(
            "window_context",
            app_id=snapshot.app_id,
            process_instance_id=snapshot.process_instance_id,
            window_instance_id=snapshot.window_instance_id,
            payload={
                "window_title": title,
                "browser_url": url,
                "browser_url_status": status,
            },
            actor=Actor.UNKNOWN,
            actor_basis="foreground_only",
            quality=quality,
            missing_reasons=missing_reasons,
            identity_confidence=snapshot.identity_confidence,
            caused_by=(caused_by.observation_id,) if caused_by is not None else (),
            capture_group_id=capture_group_id,
            observed_at_utc_ms=snapshot.observed_at_utc_ms,
            monotonic_value=snapshot.monotonic_ns,
        )
        if observation is not None:
            self._last_window_context_key = context_key
        return observation

    def _recognize_capture(self, path) -> OCRResult:
        if not self.config.ocr_enabled or self.ocr_source is None:
            return OCRResult("disabled", missing_reasons=("ocr_disabled",))
        self._metric_inc("ocr_calls")
        started = time.monotonic()
        try:
            result = self.ocr_source.recognize(path, languages=self.config.ocr_languages,
                timeout_ms=self.config.ocr_timeout_ms, max_dimension=self.config.visual_max_dimension)
        except Exception as error:
            self._metric_inc("ocr_errors")
            result = OCRResult("error", missing_reasons=("source_error", type(error).__name__))
        self._metric_status("ocr_by_status", result.status)
        with self._metrics_lock:
            self._metrics["ocr_last_duration_ms"] = round((time.monotonic() - started) * 1000, 2)
        return result.bounded()

    def _retry_ocr(self, key, snapshot, capture) -> Optional[Observation]:
        retry = self._ocr_retries.get(key)
        if retry is None or not self.config.ocr_enabled:
            return None
        original, attempts, next_at = retry
        if attempts >= 3 or time.monotonic() < next_at:
            return None
        generation = self._generation
        self._metric_inc("ocr_retries")
        result = self._recognize_capture(capture.path)
        with self._state_lock:
            if generation != self._generation or not self._content_allowed(snapshot):
                self._metric_inc("stale_results")
                return None
            observation = self.emit("ocr_result", requested_level=CollectionLevel.CONTENT,
                app_id=snapshot.app_id, process_instance_id=snapshot.process_instance_id,
                window_instance_id=snapshot.window_instance_id,
                payload={"trigger": "ocr_retry", "attempt": attempts + 1,
                         "original_observation_id": original.observation_id},
                ocr_summary=result.as_dict(), asset_refs=original.asset_refs,
                caused_by=(original.observation_id,), capture_group_id=original.capture_group_id,
                actor=original.actor, actor_basis=original.actor_basis,
                quality="complete" if result.status == "ok" else "partial",
                missing_reasons=result.missing_reasons)
            if observation is not None:
                if result.status == "ok":
                    self._ocr_retries.pop(key, None)
                else:
                    self._ocr_retries[key] = (original, attempts + 1, time.monotonic() + 5 * 2 ** attempts)
            return observation

    def _record_content(
        self,
        snapshot: ForegroundSnapshot,
        metadata: Optional[Observation],
        structure: Optional[Observation],
        capture_group_id: str,
        *,
        trigger: str = "foreground_settled",
        monitor: Optional[MonitorSnapshot] = None,
        causes: tuple[str, ...] = (),
    ) -> Optional[Observation]:
        """Capture and OCR only after the policy has authorized content."""

        if not self.config.visual_enabled or self.visual_source is None:
            return None
        if not self._content_allowed(snapshot):
            return None
        generation = self._generation
        decision = self.policy.decide(
            CollectionLevel.CONTENT,
            app_id=snapshot.app_id,
            window_name=snapshot.window_title,
            control_owner=self._control_owner(),
            paused=self._paused,
        )
        if not decision.allowed:
            return None
        if not self._visual_budget_available():
            return self.emit(
                "content_snapshot",
                requested_level=CollectionLevel.CONTENT,
                app_id=snapshot.app_id,
                process_instance_id=snapshot.process_instance_id,
                window_instance_id=snapshot.window_instance_id,
                payload={"trigger": trigger, "capture_status": "budget_exhausted"},
                visual_summary={"status": "skipped", "missing_reasons": ["visual_budget"]},
                actor=Actor.UNKNOWN,
                actor_basis="foreground_only",
                quality="unavailable",
                missing_reasons=("visual_budget",),
                identity_confidence=snapshot.identity_confidence,
                caused_by=causes + tuple(value.observation_id for value in (metadata, structure) if value),
                capture_group_id=capture_group_id,
                observed_at_utc_ms=snapshot.observed_at_utc_ms,
            )
        staging = self.config.asset_directory / ".staging" / f"{opaque_id('capture')}.png"
        staging.parent.mkdir(parents=True, exist_ok=True)
        if shutil.disk_usage(staging.parent).free < self.config.min_free_space_mib * 1024 * 1024:
            self._metric_inc("low_disk_skips")
            return self.emit("content_snapshot", requested_level=CollectionLevel.CONTENT, app_id=snapshot.app_id,
                             payload={"trigger": trigger, "capture_status": "low_disk_space"},
                             quality="unavailable", missing_reasons=("low_disk_space",))
        self._metric_inc("capture_calls")
        capture_started = time.monotonic()
        capture_started_at = int(time.time() * 1000)
        masks_before = self.monitor_source.masks(self.policy) if monitor is not None else []
        try:
            capture_method = self.visual_source.capture_monitor if monitor is not None else self.visual_source.capture
            capture = capture_method(
                monitor.handle if monitor is not None else snapshot.hwnd,
                staging,
                timeout_ms=self.config.visual_timeout_ms,
                max_dimension=self.config.visual_max_dimension,
            )
        except Exception as error:
            self._metric_inc("capture_errors")
            capture = CaptureResult("error", missing_reasons=("source_error", type(error).__name__))
        self._metric_status("capture_by_status", capture.status)
        with self._metrics_lock:
            self._metrics["capture_last_duration_ms"] = round((time.monotonic() - capture_started) * 1000, 2)
        if capture.status != "ok" or capture.path is None:
            return self.emit(
                "content_snapshot",
                requested_level=CollectionLevel.CONTENT,
                app_id=snapshot.app_id,
                process_instance_id=snapshot.process_instance_id,
                window_instance_id=snapshot.window_instance_id,
                payload={"trigger": trigger, "capture_status": capture.status,
                         "monitor_id": monitor.monitor_id if monitor else None},
                visual_summary=capture.as_dict(),
                actor=Actor.UNKNOWN,
                actor_basis="foreground_only",
                quality="unavailable",
                missing_reasons=tuple(capture.missing_reasons) or ("capture_unavailable",),
                identity_confidence=snapshot.identity_confidence,
                caused_by=causes + tuple(value.observation_id for value in (metadata, structure) if value),
                capture_group_id=capture_group_id,
                observed_at_utc_ms=snapshot.observed_at_utc_ms,
                capture_started_at=capture_started_at,
                capture_finished_at=int(time.time() * 1000),
            )
        if not self._content_allowed(snapshot):
            capture.path.unlink(missing_ok=True)
            self._metric_inc("stale_results")
            return None
        if monitor is not None:
            try:
                if monitor not in self.monitor_source.enumerate():
                    capture.path.unlink(missing_ok=True)
                    self._metric_inc("stale_results")
                    return None
                masks = masks_before + self.monitor_source.masks(self.policy)
                mask_count = mask_image(capture.path, monitor, masks)
                monitor_area = max(1, (monitor.bounds[2] - monitor.bounds[0]) *
                                   (monitor.bounds[3] - monitor.bounds[1]))
                masked_area = sum(max(0, min(monitor.bounds[2], right) - max(monitor.bounds[0], left)) *
                                  max(0, min(monitor.bounds[3], bottom) - max(monitor.bounds[1], top))
                                  for left, top, right, bottom in masks)
                capture = replace(capture, signature={**dict(capture.signature or {}),
                    "sha256": hashlib.sha256(capture.path.read_bytes()).hexdigest(),
                    "masked_windows": mask_count,
                    "masked_area_fraction": round(min(1.0, masked_area / monitor_area), 6)})
            except Exception:
                capture.path.unlink(missing_ok=True)
                raise
        capture_finished_at = int(time.time() * 1000)
        key = monitor.monitor_id if monitor else snapshot.window_instance_id or str(snapshot.hwnd)
        if not self._visual_gate.changed(key, capture.signature):
            try:
                self._metric_inc("duplicate_frames")
                return self._retry_ocr(key, snapshot, capture)
            finally:
                capture.path.unlink(missing_ok=True)
        black_fraction = None
        black_filter_error = None
        try:
            black_fraction = image_black_fraction(capture.path, threshold=self.config.black_pixel_threshold)
        except Exception as error:
            # A capture provider may return a non-image diagnostic artifact.
            # Preserve the frame and its OCR/asset path instead of dropping the
            # whole evidence job; the error remains visible in the signature and
            # metrics for the monitor UI.
            black_filter_error = type(error).__name__
            self._metric_inc("black_filter_errors")
        black_limit = (self.config.monitor_black_fraction if monitor is not None
                       else self.config.window_black_fraction)
        capture = replace(capture, signature={**dict(capture.signature or {}),
            **({"black_fraction": round(black_fraction, 6)} if black_fraction is not None else {}),
            "black_filter_threshold": black_limit,
            **({"black_filter_error": black_filter_error} if black_filter_error else {})})
        if (self.config.drop_black_frames and black_fraction is not None
                and black_fraction > black_limit):
            self._metric_inc("black_frame_skips")
            filtered = replace(capture, status="filtered",
                               missing_reasons=("mostly_black",))
            finished_at = int(time.time() * 1000)
            try:
                result = self.emit(
                    "content_snapshot",
                    requested_level=CollectionLevel.CONTENT,
                    app_id=snapshot.app_id,
                    process_instance_id=snapshot.process_instance_id,
                    window_instance_id=snapshot.window_instance_id,
                    payload={"trigger": trigger, "capture_status": "black_frame_filtered",
                             "scope": "monitor" if monitor else "foreground",
                             "monitor_id": monitor.monitor_id if monitor else None,
                             "monitor_bounds": list(monitor.bounds) if monitor else None},
                    visual_summary=filtered.as_dict(),
                    actor=Actor.BACKGROUND if monitor else Actor.UNKNOWN,
                    actor_basis="monitor_observation" if monitor else "foreground_only",
                    quality="unavailable",
                    missing_reasons=("mostly_black",),
                    identity_confidence=snapshot.identity_confidence,
                    caused_by=causes + tuple(value.observation_id for value in (metadata, structure) if value),
                    capture_group_id=capture_group_id,
                    observed_at_utc_ms=finished_at,
                    capture_started_at=capture_started_at,
                    capture_finished_at=finished_at,
                )
                if result is not None:
                    with self._state_lock:
                        if generation == self._generation:
                            self._visual_gate.commit(key, filtered.signature)
                return result
            finally:
                capture.path.unlink(missing_ok=True)
        ocr = self._recognize_capture(capture.path)
        black_filter_missing = ("black_filter_unavailable",) if black_filter_error else ()
        quality = ("complete" if (not self.config.ocr_enabled or ocr.status == "ok")
                   and not black_filter_error else "partial")
        missing = black_filter_missing + (tuple(ocr.missing_reasons)
                                          if ocr.status not in {"ok", "disabled"} else ())
        reservation = None
        asset_refs: tuple[str, ...] = ()
        observation = None
        try:
            if generation != self._generation or not self._content_allowed(snapshot):
                self._metric_inc("stale_results")
                return None
            content = capture.path.read_bytes()
            persist_image = self.config.visual_persist_images
            if persist_image:
                quota = self.config.image_quota_mib * 1024 * 1024
                if len(content) > quota:
                    persist_image = False
                    quality = "partial"
                    missing += ("image_exceeds_quota",)
                elif self.store.asset_bytes() + len(content) > quota:
                    self.store.cleanup(observation_retention_days=self.config.observation_retention_days,
                                       max_asset_bytes=quota - len(content))
            if persist_image:
                reservation = self.store.reserve_asset(
                    mime_type="image/png",
                    scope="activity-content",
                    policy_version=decision.policy_version,
                    expires_at_utc_ms=int(time.time() * 1000) + self.config.image_retention_hours * 3_600_000,
                )
                asset_refs = (reservation.asset_id,)
            observation = self.emit(
                "content_snapshot",
                requested_level=CollectionLevel.CONTENT,
                app_id=snapshot.app_id,
                process_instance_id=snapshot.process_instance_id,
                window_instance_id=snapshot.window_instance_id,
                payload={"trigger": trigger, "capture_status": capture.status,
                         "scope": "monitor" if monitor else "foreground",
                         "monitor_id": monitor.monitor_id if monitor else None,
                         "monitor_bounds": list(monitor.bounds) if monitor else None},
                visual_summary=capture.as_dict(),
                ocr_summary=ocr.as_dict(),
                asset_refs=asset_refs,
                actor=Actor.BACKGROUND if monitor else Actor.UNKNOWN,
                actor_basis="monitor_observation" if monitor else "foreground_only",
                quality=quality,
                missing_reasons=missing,
                identity_confidence=snapshot.identity_confidence,
                caused_by=causes + tuple(value.observation_id for value in (metadata, structure) if value),
                capture_group_id=capture_group_id,
                observed_at_utc_ms=capture_finished_at,
                capture_started_at=capture_started_at,
                capture_finished_at=capture_finished_at,
                truncated=bool(getattr(ocr, "truncated", False)),
            )
            if observation is not None and reservation is not None:
                self.store.commit_asset_bytes(reservation, content, observation)
            elif reservation is not None:
                self.store.fail_asset(reservation.asset_id)
        except Exception:
            if reservation is not None and observation is None:
                try:
                    self.store.fail_asset(reservation.asset_id)
                except Exception:
                    pass
            raise
        finally:
            try:
                capture.path.unlink()
            except FileNotFoundError:
                pass
        with self._state_lock:
            if observation is not None and generation == self._generation:
                self._visual_gate.commit(key, capture.signature)
                self._ocr_retries.pop(key, None)
                if self.config.ocr_enabled and ocr.status not in {"ok", "disabled"}:
                    self._ocr_retries[key] = (observation, 1, time.monotonic() + 5)
                    if len(self._ocr_retries) > 128:
                        self._ocr_retries.pop(next(iter(self._ocr_retries)))
        return observation

    def _submit_monitor(self, snapshot, monitor, causes=(), trigger="periodic_monitor") -> None:
        background = replace(snapshot, hwnd=0, pid=0, app_id=None, window_title=None,
                             process_instance_id="", window_instance_id=monitor.monitor_id,
                             observed_at_utc_ms=int(time.time() * 1000))
        self._submit(background, trigger, causes, key=monitor.monitor_id, data=monitor)

    def _periodic_monitors(self, snapshot) -> None:
        if (self.config.visual_scope != "monitors" or not self.config.visual_enabled
                or not self.config.continuous_enabled or not self.config.enabled or self._paused):
            return
        now = time.monotonic()
        if now - self._last_monitors_at < self.config.capture_interval_seconds:
            return
        self._last_monitors_at = now
        for monitor in self.monitor_source.enumerate():
            self._submit_monitor(snapshot, monitor)

    def record_input_event(self, event: Mapping[str, Any]) -> None:
        if event.get("type") == "source_diagnostic":
            self.emit("collector_diagnostic", payload={"stage": "input", "reason": event.get("reason")})
            return
        if event.get("type") not in {"clipboard_changed", "clipboard_operation"}:
            return
        snapshot = self._previous_foreground
        if (snapshot is None or event.get("hwnd") != snapshot.hwnd or event.get("pid") != snapshot.pid
                or not self.config.clipboard_enabled or not self._current(snapshot)):
            self._metric_inc("unattributed_clipboard_events")
            return
        observation = self.emit(str(event["type"]), app_id=snapshot.app_id,
                                window_instance_id=snapshot.window_instance_id,
                                payload={"operation": event.get("operation"), "sequence": event.get("sequence")},
                                observed_at_utc_ms=event.get("observed_at_utc_ms"))
        if observation and event.get("operation") not in {"copy", "cut"} and self._content_allowed(snapshot):
            self._submit(snapshot, "clipboard", (observation.observation_id,),
                         key="clipboard:" + str(event.get("sequence")) + ":" + str(event.get("operation")), data=dict(event))
        if observation and event.get("operation") == "paste":
            self._submit(snapshot, "paste", (observation.observation_id,))

    def _record_clipboard(self, job: CaptureJob) -> None:
        snapshot, event = job.snapshot, job.data
        if not self._content_allowed(snapshot):
            return
        operation = event.get("operation")
        sequence = int(event.get("sequence", 0))
        if operation is None and sequence in self._clipboard_sequences:
            return
        self._metric_inc("clipboard_calls")
        result = self.clipboard_source.read(sequence=0 if operation in {"copy", "cut"} else sequence,
            after_sequence=sequence if operation in {"copy", "cut"} else 0,
            timeout_ms=self.config.clipboard_timeout_ms, max_chars=self.config.clipboard_max_chars)
        self._metric_status("clipboard_by_status", str(result.get("status")))
        if not self._content_allowed(snapshot):
            self._metric_inc("stale_results")
            return
        stored = self.emit("clipboard_content", requested_level=CollectionLevel.CONTENT, app_id=snapshot.app_id,
            window_instance_id=snapshot.window_instance_id, process_instance_id=snapshot.process_instance_id,
            payload={**result, "operation": operation or "changed"}, caused_by=job.causes,
            quality="complete" if result.get("status") == "ok" and not result.get("truncated") else "partial"
                if result.get("status") in {"ok", "non_text", "unchanged"} else "unavailable",
            missing_reasons=tuple(result.get("missing_reasons") or ()), truncated=bool(result.get("truncated")),
            actor_basis="clipboard_observation")
        if stored and result.get("status") == "ok":
            self._clipboard_sequences.append(int(result.get("sequence", sequence)))

    def record_input_batch(
        self,
        *,
        keyboard_count: int = 0,
        click_count: int = 0,
        scroll_count: int = 0,
        motion: bool = False,
        quiet_ms: Optional[int] = None,
        snapshot: Optional[ForegroundSnapshot] = None,
    ) -> Optional[Observation]:
        self._metric_inc("input_batches")
        owner = self._control_owner()
        actor = Actor.USER if owner == "unclaimed" else Actor.UNKNOWN
        basis = "raw_input_metadata" if owner == "unclaimed" else "mixed_control"
        foreground = snapshot or self._previous_foreground
        if foreground is not None and (not self._current(foreground) or not self.policy.decide(
                CollectionLevel.METADATA, app_id=foreground.app_id, window_name=foreground.window_title,
                paused=self._paused).allowed):
            return None
        return self.emit(
            "input_batch",
            app_id=foreground.app_id if foreground is not None else None,
            process_instance_id=(foreground.process_instance_id if foreground is not None else None),
            window_instance_id=(foreground.window_instance_id if foreground is not None else None),
            payload={
                "keyboard_count": max(0, int(keyboard_count)),
                "click_count": max(0, int(click_count)),
                "scroll_count": max(0, int(scroll_count)),
                "motion": bool(motion),
                "quiet_ms": max(0, int(quiet_ms)) if quiet_ms is not None else None,
            },
            actor=actor,
            actor_basis=basis,
        )

    def record_input_metadata(self, batch: InputBatch) -> Optional[Observation]:
        """Persist an already aggregated batch without exposing raw packets."""

        snapshot = self._previous_foreground
        if batch.hwnd and (snapshot is None or batch.hwnd != snapshot.hwnd or batch.pid != snapshot.pid):
            self._metric_inc("unattributed_input_batches")
            return None
        observation = self.record_input_batch(
            keyboard_count=batch.keyboard_count,
            click_count=batch.click_count,
            scroll_count=batch.scroll_count,
            motion=batch.motion,
            quiet_ms=batch.quiet_ms,
            snapshot=snapshot,
        )
        if observation and snapshot and (batch.keyboard_count or batch.click_count or batch.scroll_count):
            trigger = "typing_pause" if batch.keyboard_count else "scroll_stop" if batch.scroll_count else "click"
            self._submit(snapshot, trigger, (observation.observation_id,))
        return observation

    def begin_agent_control(
        self,
        task_id: str,
        *,
        session_id: Optional[str] = None,
        ttl_seconds: float = 30.0,
        pid: Optional[int] = None,
        process_start_time: Optional[float] = None,
    ) -> AgentControlLease:
        now = time.monotonic()
        lease = AgentControlLease(
            lease_id=opaque_id("lease"),
            task_id=str(task_id),
            session_id=session_id,
            pid=int(pid or os.getpid()),
            process_start_time=process_start_time,
            started_at_monotonic=now,
            expires_at_monotonic=now + max(1.0, min(300.0, float(ttl_seconds))),
        )
        with self._state_lock:
            self._lease = lease
            if self.policy.agent_pause_content:
                self._visual_lifecycle("pause")
        self.emit(
            "agent_control_changed",
            payload={"state": "begin", "lease_id": lease.lease_id, "task_id": lease.task_id},
            actor=Actor.AGENT,
            actor_basis="verified_product_lease",
        )
        return lease

    def heartbeat_agent_control(self, lease_id: str, *, ttl_seconds: float = 30.0) -> bool:
        if self._lease is None or self._lease.lease_id != lease_id or not self._lease.active:
            return False
        current = self._lease
        self._lease = AgentControlLease(
            current.lease_id,
            current.task_id,
            current.session_id,
            current.pid,
            current.process_start_time,
            current.started_at_monotonic,
            time.monotonic() + max(1.0, min(300.0, float(ttl_seconds))),
        )
        return True

    def end_agent_control(self, lease_id: str, *, reason: str = "completed") -> bool:
        if self._lease is None or self._lease.lease_id != lease_id:
            return False
        current = self._lease
        self.emit(
            "agent_control_changed",
            payload={"state": "end", "lease_id": current.lease_id, "reason": reason},
            actor=Actor.AGENT,
            actor_basis="verified_product_lease",
        )
        self._lease = None
        return True

    def run_once(self) -> Optional[Observation]:
        if not self._started:
            self.start()
        self._refresh_external_state()
        if not self.config.enabled or self._paused or self._stop.is_set():
            return None
        snapshot = self.source.poll()
        with self._state_lock:
            permitted = self.config.visual_enabled and self.policy.decide(CollectionLevel.CONTENT,
                app_id=snapshot.app_id if snapshot and self.config.visual_scope != "monitors" else None,
                window_name=snapshot.window_title if snapshot and self.config.visual_scope != "monitors" else None,
                control_owner=self._control_owner(), paused=self._paused).allowed
            if (not permitted or self._stop.is_set() or not self.monitor_source.desktop_available()
                    or (snapshot is None and self.config.visual_scope != "monitors")):
                self._visual_lifecycle("pause")
            else:
                self._visual_lifecycle("resume")
        result = self.record_foreground(snapshot) if snapshot is not None else None
        if snapshot is not None:
            self._periodic_monitors(snapshot)
        return result

    def run(self, *, stop_event: Optional[threading.Event] = None) -> None:
        self.start()
        self.start_evidence_worker()
        event = stop_event or self._stop
        try:
            while not event.is_set() and not self._stop.is_set():
                self.run_once()
                event.wait(self.config.poll_interval_seconds)
        finally:
            self.stop()
