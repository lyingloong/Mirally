"""Safe startup heartbeat: code locations only, never source lines or locals."""
from contextlib import contextmanager
from pathlib import Path
import sys
import threading
import time


def stack_locations(thread_id):
    frame = sys._current_frames().get(thread_id)
    entries = []
    while frame is not None:
        entries.append({"file": Path(frame.f_code.co_filename).name,
                        "function": frame.f_code.co_name, "line": frame.f_lineno})
        frame = frame.f_back
    return list(reversed(entries[:12]))


@contextmanager
def startup_diagnostics(send_event, *, interval=10):
    """Keep reporting the main thread when an import blocks before Session."""
    stopped = threading.Event()
    thread_id = threading.get_ident()
    lock = threading.Lock()
    stage, since = "worker_imports", time.monotonic()

    def mark(value):
        nonlocal stage, since
        with lock:
            stage, since = value, time.monotonic()
            send_event({"type": "phase", "phase": "preparing", "stage": stage})

    def heartbeat():
        while not stopped.wait(interval):
            with lock:
                event = {"type": "startup_wait", "stage": stage,
                         "elapsed_seconds": round(time.monotonic() - since, 1),
                         "stack": stack_locations(thread_id)}
            try:
                send_event(event)
            except (OSError, ValueError):
                return

    thread = threading.Thread(target=heartbeat, daemon=True, name="ufo-startup-diagnostics")
    mark(stage)
    thread.start()
    try:
        yield mark
    finally:
        stopped.set()
        thread.join(timeout=1)
