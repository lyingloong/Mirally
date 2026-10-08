"""Bounded JSONL transport for the product-owned persistent WGC helper."""

from __future__ import annotations

import json
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path


class WorkerFailure(Exception):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


class _Process:
    def __init__(self, command: list[str]):
        startupinfo = None
        if sys.platform == "win32":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = 0
        self.process = subprocess.Popen(
            command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, bufsize=0,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            startupinfo=startupinfo,
        )
        self.messages: queue.Queue = queue.Queue(maxsize=4)
        self.reader = threading.Thread(target=self._read, name="activity-wgc-ipc", daemon=True)
        self.reader.start()

    def _read(self):
        stream = None
        try:
            if sys.platform == "win32":
                from openassist.platform.stdio_pipe import control_pipe_reader
                stream = control_pipe_reader(self.process.stdout)
            else:
                import io
                stream = io.TextIOWrapper(self.process.stdout, encoding="utf-8")
            while True:
                line = stream.readline(65_537)
                if not line:
                    raise WorkerFailure("worker_exit")
                if len(line) > 65_536 or not line.endswith("\n"):
                    raise WorkerFailure("invalid_helper_output")
                message = json.loads(line)
                if not isinstance(message, dict):
                    raise WorkerFailure("invalid_helper_output")
                self.messages.put_nowait(message)
        except (OSError, ValueError, WorkerFailure, queue.Full) as error:
            reason = error.reason if isinstance(error, WorkerFailure) else "invalid_helper_output"
            try:
                self.messages.put_nowait(WorkerFailure(reason))
            except queue.Full:
                pass
        finally:
            if stream is not None:
                stream.close()

    def receive(self, deadline: float) -> dict:
        try:
            message = self.messages.get(timeout=max(0, deadline - time.monotonic()))
        except queue.Empty:
            raise WorkerFailure("worker_deadline") from None
        if isinstance(message, WorkerFailure):
            raise message
        return message

    def close(self):
        # Kill is a hard boundary even if a driver or native shutdown is blocked.
        if self.process.poll() is None:
            self.process.terminate()
        try:
            self.process.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=1)
        self.reader.join(timeout=1)
        if self.process.stdin is not None:
            self.process.stdin.close()
        if not self.reader.is_alive() and self.process.stdout is not None:
            self.process.stdout.close()


class WGCWorker:
    """Serialize requests, but allow pause/stop to interrupt an active request."""

    def __init__(self, helper_path: Path):
        self.helper_path = helper_path
        self._request_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._process: _Process | None = None
        self._generation = 0
        self._paused = False
        self._request_id = 0
        self._runtime: dict = {}

    def status(self) -> dict:
        with self._state_lock:
            process = self._process
            running = process is not None and process.process.poll() is None
            return {**self._runtime, "running": running, "paused": self._paused,
                    "helper_pid": process.process.pid if running else None,
                    "session_count": self._runtime.get("session_count", 0) if running else 0}

    def pause(self):
        with self._state_lock:
            self._paused = True
            self._generation += 1
            process, self._process = self._process, None
        if process is not None:
            process.close()

    close = pause

    def resume(self):
        with self._state_lock:
            self._paused = False

    def _discard(self, process):
        with self._state_lock:
            if self._process is not process:
                return
            self._process = None
        process.close()

    def request(self, request: dict, timeout_ms: int) -> dict:
        deadline = time.monotonic() + max(0.1, timeout_ms / 1000)
        if not self._request_lock.acquire(timeout=max(0, deadline - time.monotonic())):
            raise WorkerFailure("worker_busy")
        process = None
        generation = None
        try:
            with self._state_lock:
                if self._paused:
                    raise WorkerFailure("capture_paused")
                generation = self._generation
                process = self._process
            if process is not None and process.process.poll() is not None:
                self._discard(process)
                process = None
            if process is None:
                try:
                    process = _Process([str(self.helper_path), "--serve"])
                except OSError:
                    raise WorkerFailure("spawn_error") from None
                with self._state_lock:
                    if self._paused or generation != self._generation:
                        process.close()
                        raise WorkerFailure("capture_interrupted")
                    self._process = process
                ready = process.receive(deadline)
                if ready.get("event") != "ready" or ready.get("protocol") != 1:
                    raise WorkerFailure("helper_protocol_mismatch_rebuild_required")
            with self._state_lock:
                if self._paused or generation != self._generation:
                    raise WorkerFailure("capture_interrupted")
            self._request_id += 1
            request = {**request, "id": self._request_id}
            encoded = (json.dumps(request, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
            if len(encoded) > 4096:
                raise WorkerFailure("request_too_large")
            if process.process.stdin.write(encoded) != len(encoded):
                raise WorkerFailure("incomplete_helper_request")
            result = process.receive(deadline)
            with self._state_lock:
                if self._paused or generation != self._generation:
                    raise WorkerFailure("capture_interrupted")
                if result.get("id") != self._request_id:
                    raise WorkerFailure("invalid_helper_response_id")
                runtime = result.get("runtime") or {}
                if not isinstance(runtime, dict):
                    raise WorkerFailure("invalid_helper_output")
                self._runtime = dict(runtime)
            return result
        except (OSError, ValueError):
            if process is not None:
                self._discard(process)
            with self._state_lock:
                interrupted = self._paused or (generation is not None and generation != self._generation)
            raise WorkerFailure("capture_interrupted" if interrupted else "worker_exit") from None
        except WorkerFailure as error:
            if process is not None:
                self._discard(process)
            with self._state_lock:
                interrupted = self._paused or (generation is not None and generation != self._generation)
            if interrupted and error.reason != "capture_paused":
                raise WorkerFailure("capture_interrupted") from None
            raise
        finally:
            self._request_lock.release()
