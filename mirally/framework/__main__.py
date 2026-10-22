"""JSON-lines transport: stdout is protocol only; the Node host owns scheduling."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import sys
from pathlib import Path

from .service import FrameworkError, FrameworkService


@contextmanager
def worker_lock(home: Path):
    """Do not let a second worker mark a live sender unknown on startup."""
    directory = home.resolve() / "framework"
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "worker.lock").open("a+b") as handle:
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        if sys.platform == "win32":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            handle.seek(0)
            if sys.platform == "win32":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def main() -> int:
    parser = argparse.ArgumentParser(description="Mirally product worker (JSON-lines stdio)")
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--workers", action="store_true", help="enable explicit product worker lifecycle")
    parser.add_argument("--bridge-url", default="")
    parser.add_argument("--bridge-token", default="")
    args = parser.parse_args()
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    controller = None
    if args.workers:
        from mirally.framework_workers import WorkerSupervisor
        controller = WorkerSupervisor(args.root, args.home, args.bridge_url, args.bridge_token)
    with worker_lock(args.home), FrameworkService(args.root, args.home, controller=controller) as service:
        while True:
            line = sys.stdin.readline(1_048_577)
            if not line:
                break
            identity = None
            try:
                if len(line) > 1_048_576 or not line.endswith("\n"):
                    raise FrameworkError("INVALID_REQUEST", "request must be a newline-terminated JSON object under 1 MiB")
                request = json.loads(line)
                if not isinstance(request, dict):
                    raise FrameworkError("INVALID_REQUEST", "request must be an object")
                identity = request.get("id")
                if not isinstance(identity, (str, int)) or isinstance(identity, bool):
                    raise FrameworkError("INVALID_REQUEST", "id must be a string or integer")
                result = service.call(request.get("method"), request.get("params", {}))
                response = {"id": identity, "result": result}
            except (json.JSONDecodeError, UnicodeError):
                response = {"id": identity, "error": {"code": "INVALID_REQUEST", "message": "invalid JSON"}}
            except FrameworkError as error:
                response = {"id": identity, "error": {"code": error.code, "message": str(error)}}
            except Exception as error:
                # Never echo request payloads or credentials into an HTTP error.
                print(f"framework RPC failed: {type(error).__name__}", file=sys.stderr, flush=True)
                response = {"id": identity, "error": {"code": "INTERNAL_ERROR", "message": "product operation failed"}}
            print(json.dumps(response, ensure_ascii=False, allow_nan=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
