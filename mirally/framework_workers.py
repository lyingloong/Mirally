"""Owned Activity and Memory lifecycles for the local Mirally framework host."""

from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import http.client
import json
import os
from pathlib import Path
import subprocess
import socket
import sys
import threading
from typing import Any, Callable
from urllib.parse import urlparse

import yaml


def _write_yaml(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = yaml.safe_dump(data, allow_unicode=True, sort_keys=False).encode("utf-8")
    if path.exists() and path.read_bytes() == raw:
        return
    pending = path.with_suffix(path.suffix + ".tmp")
    pending.write_bytes(raw)
    os.replace(pending, path)


class _Completion:
    """One bounded transport task. Only the supervisor may touch its owner."""

    def __init__(self, owner: Any, job: dict[str, Any], revision: int, signature: str,
                 complete: Callable, wake: threading.Event) -> None:
        self.owner, self.job, self.revision, self.signature = owner, job, revision, signature
        self.cancelled, self.done = threading.Event(), threading.Event()
        self.result: dict[str, Any] | None = None
        self.error: BaseException | None = None
        self.settled = False
        self._lock = threading.Lock()
        self._connection: http.client.HTTPConnection | None = None
        def run() -> None:
            try:
                self.check()
                self.result = complete(job, self)
            except Exception as error:
                self.error = error
            finally:
                self.done.set()
                wake.set()
        self.thread = threading.Thread(target=run, name="mirally-memory-completion", daemon=True)

    def check(self) -> None:
        if self.cancelled.is_set():
            raise RuntimeError("background completion was revoked")

    def connection(self, connection: http.client.HTTPConnection | None) -> None:
        with self._lock:
            self._connection = connection
        if self.cancelled.is_set():
            self.cancel()
            self.check()

    def cancel(self) -> None:
        self.cancelled.set()
        with self._lock:
            connection = self._connection
        if connection is not None:
            # Closing HTTPConnection alone does not reliably wake a blocking
            # getresponse/read. Shutdown also signals the Host to abort its LLM.
            try:
                if connection.sock is not None:
                    connection.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            connection.close()


class WorkerSupervisor:
    """Start only opted-in workers; each store retains its existing writer owner.

    Settings updates are asynchronous so model or capture shutdown cannot block
    the product RPC reader. ``status`` exposes requested and observed state.
    """

    def __init__(self, root: Path, home: Path, bridge_url: str = "", token: str = "") -> None:
        self.root = Path(root).resolve()
        self.home = Path(home).resolve()
        if bridge_url:
            parsed = urlparse(bridge_url)
            if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
                raise ValueError("framework completion bridge must use loopback HTTP")
        self.bridge_url, self.token = bridge_url, token
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._settings: dict[str, Any] = {}
        self._revision = 0
        self._applied = -1
        self._closed = False
        self._activity: subprocess.Popen | None = None
        self._activity_log = None
        self._activity_signature = ""
        self._memory = None
        self._memory_signature = ""
        self._completion: _Completion | None = None
        self._shutdown_error: str | None = None
        self._state: dict[str, Any] = {"activity": {"state": "disabled"}, "memory": {"state": "disabled"}, "semantic": {"state": "disabled"}}
        self._thread = threading.Thread(target=self._run, name="mirally-framework-workers", daemon=True)
        self._thread.start()

    def update(self, settings: dict[str, Any]) -> None:
        with self._lock:
            if self._closed:
                raise RuntimeError("worker supervisor is closed")
            self._settings = deepcopy(settings)
            self._revision += 1
            completion = self._completion
        if completion is not None:
            # Includes source/policy invalidation even when settings are equal.
            completion.cancel()
        self._wake.set()

    def status(self) -> dict[str, Any]:
        with self._lock:
            result = deepcopy(self._state)
            result["settings_pending"] = self._revision != self._applied
            result["requested"] = {name: bool(self._settings.get(name)) for name in ("activity_enabled", "memory_enabled", "background_enabled")}
            result["running"] = self._thread.is_alive() and not self._closed
            result["completion_pending"] = self._completion is not None and not self._completion.done.is_set()
            return result

    def _state_update(self, name: str, **values: Any) -> None:
        with self._lock:
            self._state[name] = {**self._state.get(name, {}), **values}

    def _profile(self, settings: dict[str, Any], kind: str) -> dict[str, Any]:
        explicit = settings.get(kind + "_config_path")
        if explicit:
            path = Path(explicit).expanduser()
            if not path.is_absolute():
                path = self.root / path
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            if not isinstance(data, dict):
                raise ValueError(f"{kind} profile must contain a YAML mapping")
            return data
        return {}

    @staticmethod
    def _source_blocked(settings: dict[str, Any]) -> bool:
        for source in settings.get("_framework_revoked_sources", []):
            identity = source.get("source_id", "") if isinstance(source, dict) else source
            if isinstance(identity, str) and identity.split(":", 1)[0] in {"memory", "activity", "observation"}:
                return True
        return False

    def _profiles(self, settings: dict[str, Any]) -> tuple[Path, Path]:
        activity_path = self.home / "config/activity.yaml"
        memory_path = self.home / "config/memory.yaml"
        activity = self._profile(settings, "activity")
        activity["enabled"] = bool(settings.get("activity_enabled", False))
        if not settings.get("activity_config_path"):
            activity["storage"] = {"directory": str(self.home / "data/activity"),
                                   "database_path": str(self.home / "data/activity/observations.sqlite3"),
                                   "knowledge_database_path": str(self.home / "data/activity/knowledge.sqlite3")}
        _write_yaml(activity_path, activity)
        memory = self._profile(settings, "memory")
        memory["enabled"] = bool(settings.get("memory_enabled", True))
        memory["activity_config_path"] = str(activity_path)
        if not settings.get("memory_config_path"):
            memory["database_path"] = str(self.home / "memory/memory.sqlite3")
            memory["projection_database_path"] = str(self.home / "memory/activity-projection.sqlite3")
        memory["semantics"] = {**memory.get("semantics", {}), "enabled": bool(settings.get("background_enabled", False)) and not self._source_blocked(settings), "model": settings.get("model", {}).get("model_id") or None, "max_calls_per_hour": 4, "max_tokens_per_day": 24000, "max_output_tokens": 1500}
        _write_yaml(memory_path, memory)
        return activity_path, memory_path

    def _stop_activity(self) -> None:
        process = self._activity
        if process is None:
            return
        if process.poll() is None:
            try:
                if process.stdin is not None and not process.stdin.closed:
                    process.stdin.write(b"stop\n")
                    process.stdin.flush()
                    process.stdin.close()
            except (BrokenPipeError, OSError):
                pass
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                # Retain the unknown state; the sensor's global lock prevents
                # a replacement from starting against a live old process.
                self._state_update("activity", state="stop_unconfirmed", pid=process.pid)
                raise RuntimeError("activity worker did not acknowledge shutdown")
        self._activity = None
        self._activity_signature = ""
        if self._activity_log:
            self._activity_log.close()
            self._activity_log = None
        self._state_update("activity", state="disabled", pid=None)

    def _apply(self, settings: dict[str, Any]) -> None:
        activity_path, memory_path = self._profiles(settings)
        activity_signature = hashlib.sha256(activity_path.read_bytes()).hexdigest()
        if self._activity is not None and (not settings.get("activity_enabled") or activity_signature != self._activity_signature):
            self._stop_activity()
        # Activity profile content also determines the Memory organizer source;
        # a path/policy change must not leave it reading the old store.
        signature = hashlib.sha256(memory_path.read_bytes() + b"\0" + activity_path.read_bytes()).hexdigest()
        if self._memory is not None and (not settings.get("memory_enabled", True) or signature != self._memory_signature):
            self._cancel_completion("memory_owner_changed")
            self._memory.close()
            self._memory = None
        if settings.get("activity_enabled") and self._activity is None:
            if sys.platform != "win32":
                self._state_update("activity", state="unsupported", error="Activity capture requires Windows")
            else:
                logs = self.home / "logs"
                logs.mkdir(parents=True, exist_ok=True)
                self._activity_log = (logs / "activity.log").open("ab")
                environment = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
                self._activity = subprocess.Popen([sys.executable, "-m", "mirally.framework_workers", "activity", "--root", str(self.root), "--config", str(activity_path)], cwd=self.root, env=environment, stdin=subprocess.PIPE, stdout=self._activity_log, stderr=subprocess.STDOUT, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                self._activity_signature = activity_signature
                self._state_update("activity", state="starting", pid=self._activity.pid, log=str(logs / "activity.log"))
        if settings.get("memory_enabled", True) and self._memory is None:
            from mirally.runtime.memory.service import MemoryService
            self._memory = MemoryService(self.root, memory_path)
            self._memory.start()
            self._memory_signature = signature
        if self._memory is None:
            self._state_update("memory", state="disabled")

    def _complete(self, job: dict[str, Any], completion: _Completion) -> dict[str, Any]:
        if not self.bridge_url or not self.token:
            raise RuntimeError("host model bridge is not configured")
        payload = job["payload"]
        body = {"model": payload.get("model"), "messages": [{"role": "system", "content": payload["system_prompt"]}, {"role": "user", "content": payload["prompt"]}], "max_tokens": payload.get("max_tokens", 1500), "temperature": 0, "stream": False, "purpose": "mirally-memory"}
        parsed = urlparse(self.bridge_url)
        connection = http.client.HTTPConnection(parsed.hostname, parsed.port or 80, timeout=45)
        completion.connection(connection)
        try:
            completion.check()
            connection.connect()
            completion.check()
            connection.request("POST", (parsed.path or "/") + ("?" + parsed.query if parsed.query else ""),
                               body=json.dumps(body).encode("utf-8"),
                               headers={"Content-Type": "application/json", "Authorization": "Bearer " + self.token})
            response = connection.getresponse()
            raw = response.read(2_000_001)
            completion.check()
            if response.status >= 400 or len(raw) > 2_000_000:
                raise RuntimeError("host model bridge returned an invalid response")
            result = json.loads(raw)
        finally:
            connection.close()
            completion.connection(None)
        text = result.get("text")
        if text is None:
            text = result["choices"][0]["message"]["content"]
        return {"text": text, "usage": result.get("usage", {}), "provider": result.get("provider"), "model": result.get("model")}

    def _fail_completion(self, completion: _Completion, reason: str) -> None:
        if completion.settled:
            return
        args = {"job_id": completion.job["job_id"], "lease_token": completion.job["lease_token"], "error": reason}
        if completion.result is not None:
            args["usage"] = completion.result.get("usage", {})
        completion.owner.handle("fail_job", args)
        completion.settled = True

    def _cancel_completion(self, reason: str) -> None:
        completion = self._completion
        if completion is None:
            return
        completion.cancel()
        # Fence/release the old job before closing or replacing its owner.
        self._fail_completion(completion, reason)

    def _finish_completion(self, settings: dict[str, Any], revision: int) -> None:
        completion = self._completion
        if completion is None:
            return
        with self._lock:
            allowed = (not self._stop.is_set() and revision == self._revision == completion.revision
                       and settings.get("memory_enabled", True) and settings.get("background_enabled")
                       and not self._source_blocked(settings)
                       and completion.owner is self._memory and completion.signature == self._memory_signature
                       and not completion.cancelled.is_set())
            if not allowed:
                self._cancel_completion("settings_or_source_changed")
            if not completion.done.is_set():
                return
            if not completion.settled:
                if completion.error is not None or completion.result is None:
                    self._fail_completion(completion, type(completion.error).__name__)
                    self._state_update("semantic", state="failed", error=type(completion.error).__name__, job_id=None)
                else:
                    result = completion.result
                    committed = completion.owner.handle("complete_job", {
                        "job_id": completion.job["job_id"], "lease_token": completion.job["lease_token"],
                        "result": result["text"], "usage": result.get("usage", {}),
                        "provider": result.get("provider"), "model": result.get("model")})
                    completion.settled = True
                    self._state_update("semantic", state=committed.get("status", "unknown"), job_id=None)
            self._completion = None

    def _pump(self, settings: dict[str, Any], revision: int) -> None:
        if self._activity is not None:
            code = self._activity.poll()
            if code is not None:
                self._state_update("activity", state="failed", exit_code=code, error="Activity worker exited; inspect its local log before re-enabling")
            else:
                ready = self.home / "activity-worker.ready"
                if ready.exists() and ready.read_text(encoding="ascii").strip() == str(self._activity.pid):
                    self._state_update("activity", state="running")
        if self._memory is None:
            self._state_update("semantic", state="disabled", job_id=None)
            return
        health = self._memory.handle("health")
        self._state_update("memory", state=health.get("maintenance_status", "running"), health=health)
        if self._source_blocked(settings):
            self._state_update("semantic", state="source_blocked", job_id=None)
            return
        if not settings.get("background_enabled") or self._stop.is_set():
            self._state_update("semantic", state="disabled", job_id=None)
            return
        if self._completion is not None:
            self._state_update("semantic", state="cancelling" if self._completion.cancelled.is_set() else "running")
            return
        claim = self._memory.handle("claim_job")
        job = claim.get("job")
        if not job:
            self._state_update("semantic", state=claim.get("status", "idle"))
            return
        self._state_update("semantic", state="running", job_id=job["job_id"])
        with self._lock:
            completion = _Completion(self._memory, job, revision, self._memory_signature, self._complete, self._wake)
            self._completion = completion
            if revision != self._revision:
                completion.cancel()
            completion.thread.start()

    def _run(self) -> None:
        try:
            while not self._stop.is_set():
                with self._lock:
                    settings, revision = deepcopy(self._settings), self._revision
                if revision:
                    try:
                        # Re-reading explicit profile files also catches edits
                        # on disk without requiring an unrelated UI toggle.
                        self._apply(settings)
                        self._applied = revision
                        self._finish_completion(settings, revision)
                        self._pump(settings, revision)
                    except Exception as error:
                        self._state_update("supervisor", state="error", error=f"{type(error).__name__}: {error}")
                self._wake.wait(2)
                self._wake.clear()
        finally:
            try:
                self._cancel_completion("host_stopped")
            except Exception as error:
                self._shutdown_error = type(error).__name__
                self._state_update("supervisor", state="stop_unconfirmed", error=self._shutdown_error)
            try:
                self._stop_activity()
            except Exception as error:
                self._shutdown_error = type(error).__name__
                self._state_update("activity", state="stop_unconfirmed", error=self._shutdown_error)
            finally:
                if self._memory is not None:
                    try:
                        self._memory.close()
                        self._memory = None
                    except Exception as error:
                        self._shutdown_error = type(error).__name__
                        self._state_update("memory", state="stop_unconfirmed", error=self._shutdown_error)

    def close(self) -> None:
        with self._lock:
            self._closed = True
        self._stop.set()
        if self._completion is not None:
            self._completion.cancel()
        self._wake.set()
        self._thread.join(60)
        if self._thread.is_alive():
            raise RuntimeError("background workers are still stopping; do not start another writer")
        if self._shutdown_error is not None:
            raise RuntimeError("background shutdown is unconfirmed; do not start another writer")


def _activity_main(root: Path, config_path: Path) -> int:
    from mirally.runtime.activity.config import load_activity_config
    from mirally.runtime.activity.lock import ActivityProcessLock
    from mirally.runtime.activity.organizer_service import ActivityOrganizerService
    from mirally.runtime.activity.service import ActivitySensor
    config = load_activity_config(root, config_path)
    lock = ActivityProcessLock(fallback_path=config.database_path.with_suffix(".lock"))
    if not config.enabled:
        return 0
    if not lock.acquire():
        raise RuntimeError("another Mirally Activity collector is already running")
    stop = threading.Event()
    def watch_parent() -> None:
        # EOF also closes capture after the owning Host unexpectedly exits.
        sys.stdin.readline()
        stop.set()
    threading.Thread(target=watch_parent, daemon=True).start()
    sensor = organizer = None
    ready = config_path.parent.parent / "activity-worker.ready"
    try:
        sensor = ActivitySensor(config)
        sensor.start()
        organizer = ActivityOrganizerService(config)
        organizer.start()
        ready.write_text(str(os.getpid()), encoding="ascii")
        sensor.run(stop_event=stop)
    finally:
        ready.unlink(missing_ok=True)
        if organizer is not None:
            organizer.close()
        if sensor is not None:
            sensor.close()
        lock.release()
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["activity"])
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    arguments = parser.parse_args()
    raise SystemExit(_activity_main(arguments.root.resolve(), arguments.config.resolve()))
