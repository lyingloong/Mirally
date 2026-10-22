"""An Activity collector owned by one Mirally window's lifecycle."""

from __future__ import annotations

import copy
import threading
import time
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any, Optional

from .config import ActivityConfig
from .lock import ActivityProcessLock
from .monitor import ActivityMonitorRuntime


class _HeldActivityLock:
    """Let the monitor use a guard acquired before its stores are opened.

    On Windows ActivityProcessLock is a named-handle single-instance guard,
    not a thread-owned mutex. Keeping that handle alive is sufficient.
    """

    def __init__(self, guard: ActivityProcessLock) -> None:
        self.guard = guard
        self.held = True

    def acquire(self) -> bool:
        return self.held

    def release(self) -> None:
        if self.held:
            self.guard.release()
            self.held = False


class ActivityCaptureSession:
    """Own a temporary capture session without changing the saved profile.

    Construction and an initial status read do not open databases, acquire
    capture sources, or affect an independently running Activity collector.
    Only a desktop host invokes start, at startup when the saved preference is
    enabled or after a user control. Reading status never starts collection.
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()
        self._mutex = threading.RLock()
        self._runtime: Optional[ActivityMonitorRuntime] = None
        self._runtime_complete = False
        self._guard: Optional[_HeldActivityLock] = None
        self._config: Optional[ActivityConfig] = None
        self._last_status: Optional[dict[str, Any]] = None
        self._session_id: Optional[str] = None

    @property
    def runtime(self) -> Optional[ActivityMonitorRuntime]:
        """The monitor owned by this window, if any; never an external one."""
        with self._mutex:
            return self._runtime if self._runtime_complete else None

    @property
    def url(self) -> str:
        with self._mutex:
            runtime = self._runtime
            if not self._runtime_complete or runtime is None or runtime.stop_event.is_set():
                return ""
            return runtime.url

    def start(self, config: ActivityConfig) -> dict[str, Any]:
        """Start once with the profile's scope, leaving its on-disk value alone."""
        with self._mutex:
            self._reap_stopped_locked()
            # Policy is immutable and has its own enabled bit. Enable both for
            # this session, without changing levels, exclusions, or sources.
            active_config = replace(config, enabled=True, policy=replace(config.policy, enabled=True))
            if self._runtime is not None:
                if self._runtime_complete and not self._runtime.stop_event.is_set() and self._config == active_config:
                    return self._snapshot_locked()
                raise RuntimeError("stop the current Activity capture session before starting another")
            self._session_id = uuid.uuid4().hex
            guard = ActivityProcessLock(fallback_path=active_config.database_path.with_suffix(".lock"))
            if not guard.acquire():
                guard.release()
                raise RuntimeError("another Mirally Activity collector is already running; this window cannot control it")
            self._guard = _HeldActivityLock(guard)
            self._config = active_config
            self._last_status = {
                "enabled": True,
                "database": str(active_config.database_path),
                "asset_directory": str(active_config.asset_directory),
                "collection_level": active_config.policy.default_level,
                "collector": {"status": "starting"},
            }
            try:
                # Keep the object even if __init__ raises after opening a store,
                # so partial construction can be cleaned up or retried safely.
                runtime = ActivityMonitorRuntime.__new__(ActivityMonitorRuntime)
                self._runtime = runtime
                ActivityMonitorRuntime.__init__(runtime, active_config, root=self.root)
                runtime.lock = self._guard
                self._runtime_complete = True
                # Starting a new session is an explicit request to collect.
                # Do not inherit a previous session's durable pause marker.
                runtime.sensor.resume()
                runtime.start()
                return self._snapshot_locked()
            except Exception as error:
                self._diagnostic_locked("startup_error", error)
                self._shutdown_locked(raise_errors=False)
                raise

    def status(self) -> Optional[dict[str, Any]]:
        with self._mutex:
            self._reap_stopped_locked()
            return self._snapshot_locked() if self._last_status is not None else None

    def pause(self, expected_session_id: Optional[str] = None) -> Optional[dict[str, Any]]:
        with self._mutex:
            self._check_session_locked(expected_session_id)
            self._reap_stopped_locked()
            if self._runtime_complete and self._runtime is not None and not self._runtime.stop_event.is_set():
                self._runtime.sensor.pause()
            return self._snapshot_locked() if self._last_status is not None else None

    def resume(self, expected_session_id: Optional[str] = None) -> Optional[dict[str, Any]]:
        with self._mutex:
            self._check_session_locked(expected_session_id)
            self._reap_stopped_locked()
            if self._runtime_complete and self._runtime is not None and not self._runtime.stop_event.is_set():
                self._runtime.sensor.resume()
            return self._snapshot_locked() if self._last_status is not None else None

    def stop(self, expected_session_id: Optional[str] = None) -> Optional[dict[str, Any]]:
        with self._mutex:
            self._check_session_locked(expected_session_id)
            self._shutdown_locked(raise_errors=True)
            return self._snapshot_locked() if self._last_status is not None else None

    def close(self) -> None:
        with self._mutex:
            self._shutdown_locked(raise_errors=True)

    def _diagnostic_locked(self, field: str, error: Exception) -> None:
        if self._last_status is None:
            self._last_status = {}
        self._last_status.setdefault("monitor", {})[field] = f"{type(error).__name__}: {error}"

    def _check_session_locked(self, expected_session_id: Optional[str]) -> None:
        if expected_session_id is not None and expected_session_id != self._session_id:
            raise RuntimeError("活动采集会话已变化；请刷新状态后再操作。")

    def _snapshot_locked(self) -> dict[str, Any]:
        previous = self._last_status or {}
        result = copy.deepcopy(previous)
        runtime = self._runtime
        if self._runtime_complete and runtime is not None and not getattr(runtime, "_closed", False):
            try:
                result = copy.deepcopy(runtime.status())
                # Retain lifecycle diagnostics after shutdown retry or a later
                # successful status read replaces the monitor's live fields.
                result.setdefault("monitor", {}).update({
                    key: value for key, value in previous.get("monitor", {}).items()
                    if key in {"startup_error", "shutdown_error", "status_error"}
                })
            except Exception as error:
                result.setdefault("monitor", {})["status_error"] = f"{type(error).__name__}: {error}"
        collector = result.setdefault("collector", {})
        result["session_id"] = self._session_id
        stopped = runtime is None or not self._runtime_complete or runtime.stop_event.is_set()
        if stopped:
            collector["status"] = "stopped"
        else:
            collector.setdefault("status", "starting")
        collector.update(
            snapshot=False,
            owned=True,
            running=not stopped,
            live_enabled=not stopped,
            updated_at_utc_ms=int(time.time() * 1000),
        )
        paused = collector["status"] == "paused"
        controllable = not stopped and collector["status"] not in {"error", "disabled"}
        result["controls"] = {
            "start": runtime is None,
            "pause": controllable and not paused,
            "resume": controllable and paused,
            "stop": runtime is not None,
        }
        self._last_status = result
        return copy.deepcopy(result)

    def _reap_stopped_locked(self) -> None:
        runtime = self._runtime
        if self._runtime_complete and runtime is not None and (runtime.stop_event.is_set() or getattr(runtime, "_closed", False)):
            self._shutdown_locked(raise_errors=False)

    def _shutdown_locked(self, *, raise_errors: bool) -> None:
        runtime = self._runtime
        if runtime is None:
            if self._guard is not None:
                self._guard.release()
                self._guard = None
            return
        self._snapshot_locked()
        try:
            if self._runtime_complete:
                if not getattr(runtime, "_closed", False):
                    runtime.request_stop()
                    self._snapshot_locked()
                runtime.close()
            else:
                # No start was invoked, so only constructed resources need
                # closing. In particular, do not run an incomplete monitor's
                # close method or touch an external collector's state.
                for name in ("sensor", "knowledge_store", "store"):
                    resource = getattr(runtime, name, None)
                    if resource is not None:
                        resource.close()
                        setattr(runtime, name, None)
            if self._guard is not None:
                self._guard.release()
        except Exception as error:
            self._diagnostic_locked("shutdown_error", error)
            self._snapshot_locked()
            if raise_errors:
                raise
            return
        self._runtime = None
        self._runtime_complete = False
        self._guard = None
        self._snapshot_locked()
