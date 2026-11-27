"""JSON-lines transport: stdout is protocol only; the Node host owns scheduling."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
import re
import sys
import traceback
from pathlib import Path

from .service import FrameworkError, FrameworkService

DIAGNOSTIC_MAX_CHARS = 2000  # stays well below the host sidecar's 4096-char stderr slice
DIAGNOSTIC_MAX_FRAMES = 5
DIAGNOSTIC_MAX_CAUSES = 3
ROUTE_TOKEN = re.compile(r"[A-Za-z0-9_.\-]+\Z")


class WorkerLockBusy(Exception):
    """A live worker already owns this home, so a second one must not start."""


def worker_lock_message(home: Path) -> str:
    lock = home.resolve() / "framework" / "worker.lock"
    return f"mirally: another worker already holds {lock}; only one worker per home is allowed"


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
        try:
            if sys.platform == "win32":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, PermissionError) as error:
            raise WorkerLockBusy(worker_lock_message(home)) from error
        try:
            yield
        finally:
            handle.seek(0)
            if sys.platform == "win32":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _frame_location(filename: str, root: Path) -> str:
    """Repo-relative posix path for frames under the checkout, bare file name outside it."""
    root_path = str(root)
    if os.path.normcase(filename).startswith(os.path.normcase(root_path) + os.sep):
        return "/".join(Path(filename[len(root_path) + 1:]).parts)
    return os.path.basename(filename)


def rpc_diagnostic(method: object, error: BaseException, root: Path) -> str:
    """One bounded stderr line naming the route, exception types and code frames.

    The reply stays a generic INTERNAL_ERROR, so this line is the only local record
    of where a product call failed. It never carries exception messages, request
    values, locals or absolute paths: stderr flows through the host into host.log.
    """
    route = method if isinstance(method, str) and len(method) <= 128 and ROUTE_TOKEN.match(method) else "-"
    chain: list[str] = []
    seen: list[BaseException] = []
    link: BaseException | None = error
    while link is not None and len(chain) < DIAGNOSTIC_MAX_CAUSES:
        seen.append(link)
        entry = [type(link).__name__]
        for frame in traceback.extract_tb(link.__traceback__)[-DIAGNOSTIC_MAX_FRAMES:]:
            entry.append(f"{_frame_location(frame.filename, root)}:{frame.lineno if frame.lineno is not None else '?'}:{frame.name}")
        chain.append(" ".join(entry))
        next_link = link.__cause__ or (None if link.__suppress_context__ else link.__context__)
        link = None if any(next_link is known for known in seen) else next_link
    return ("framework RPC failed: method=" + route + " " + " <- ".join(chain))[:DIAGNOSTIC_MAX_CHARS]


def main() -> int:
    parser = argparse.ArgumentParser(description="Mirally product worker (JSON-lines stdio)")
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--workers", action="store_true", help="enable explicit product worker lifecycle")
    parser.add_argument("--bridge-url", default="")
    parser.add_argument("--bridge-token", default="")
    args = parser.parse_args()
    root = args.root.resolve()
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    controller = None
    if args.workers:
        from mirally.framework_workers import WorkerSupervisor
        controller = WorkerSupervisor(args.root, args.home, args.bridge_url, args.bridge_token)
    try:
        with worker_lock(args.home), FrameworkService(args.root, args.home, controller=controller) as service:
            while True:
                line = sys.stdin.readline(1_048_577)
                if not line:
                    break
                identity = None
                method: object = None
                try:
                    if len(line) > 1_048_576 or not line.endswith("\n"):
                        raise FrameworkError("INVALID_REQUEST", "request must be a newline-terminated JSON object under 1 MiB")
                    request = json.loads(line)
                    if not isinstance(request, dict):
                        raise FrameworkError("INVALID_REQUEST", "request must be an object")
                    identity = request.get("id")
                    if not isinstance(identity, (str, int)) or isinstance(identity, bool):
                        raise FrameworkError("INVALID_REQUEST", "id must be a string or integer")
                    method = request.get("method")
                    result = service.call(method, request.get("params", {}))
                    response = {"id": identity, "result": result}
                except (json.JSONDecodeError, UnicodeError):
                    response = {"id": identity, "error": {"code": "INVALID_REQUEST", "message": "invalid JSON"}}
                except FrameworkError as error:
                    response = {"id": identity, "error": {"code": error.code, "message": str(error)}}
                except Exception as error:
                    # Never echo request payloads or credentials into an HTTP error.
                    print(rpc_diagnostic(method, error, root), file=sys.stderr, flush=True)
                    response = {"id": identity, "error": {"code": "INTERNAL_ERROR", "message": "product operation failed"}}
                print(json.dumps(response, ensure_ascii=False, allow_nan=False), flush=True)
    except WorkerLockBusy as error:
        print(error, file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
