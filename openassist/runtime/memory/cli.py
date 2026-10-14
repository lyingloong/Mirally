"""Inspect, process and explicitly maintain Mirally memories."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import signal
import sys
import time


def main() -> int:
    from .config import load_memory_config
    from .service import MemoryReader, MemoryService

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[3])
    parser.add_argument("--config", type=Path)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    sub.add_parser("process", help="Process one bounded batch without calling a model")
    run = sub.add_parser("run", help="Run background deterministic memory maintenance")
    run.add_argument("--duration", type=float)
    search = sub.add_parser("search")
    search.add_argument("query", nargs="?", default="")
    search.add_argument("--limit", type=int, default=10)
    search.add_argument("--candidates", action="store_true")
    read = sub.add_parser("read")
    read.add_argument("id")
    propose = sub.add_parser("propose")
    propose.add_argument("statement")
    propose.add_argument("--kind", choices=("semantic", "task"), default="semantic")
    accept = sub.add_parser("accept", help="Explicitly confirm a reviewed candidate")
    accept.add_argument("id")
    accept.add_argument("--supersedes", action="append", default=[])
    forget = sub.add_parser("forget")
    forget.add_argument("id")
    args = parser.parse_args()
    config = load_memory_config(args.root, args.config)
    result = None
    if args.command in {"process", "run"}:
        service = MemoryService(args.root, args.config)
        try:
            if args.command == "process":
                result = service.process_once()
            elif not config.enabled:
                result = service.handle("health", {})
            else:
                stopped = False

                def stop(*_):
                    nonlocal stopped
                    stopped = True

                previous = {}
                for name in ("SIGINT", "SIGTERM"):
                    number = getattr(signal, name, None)
                    if number is not None:
                        previous[number] = signal.signal(number, stop)
                try:
                    service.start()
                    deadline = time.monotonic() + args.duration if args.duration is not None else None
                    while not stopped and (deadline is None or time.monotonic() < deadline):
                        time.sleep(0.2)
                    result = service.handle("health", {})
                finally:
                    for number, handler in previous.items():
                        signal.signal(number, handler)
        finally:
            service.close()
    elif args.command in {"propose", "forget"}:
        from .mcp_server import forget_memory, propose_memory
        result = (propose_memory(config, args.statement, kind=args.kind) if args.command == "propose"
                  else forget_memory(config, args.id))
    elif args.command == "accept":
        from openassist.core.memory import MemoryStore
        if not config.enabled or not config.database_path.is_file():
            result = {"status": "disabled" if not config.enabled else "not_ready"}
        else:
            with MemoryStore(config.database_path) as store:
                record = store.get_memory(args.id, include_inactive=True)
                if record is None or record["status"] != "candidate":
                    raise ValueError("Only an existing candidate can be confirmed")
                with store.transaction():
                    record.update(status="active", provenance="user_confirmed", confidence=1.0,
                                  supersedes=args.supersedes)
                    record["metadata"] = {**record.get("metadata", {}), "confirmed_at_utc_ms": int(time.time() * 1000)}
                    accepted = store.put_memory(record)
                    result = {"status": "accepted" if accepted["status"] == "active" else
                              "requires_conflict_resolution", "memory": accepted}
    else:
        reader = MemoryReader(args.root, args.config)
        try:
            op = {"status": "health", "search": "search", "read": "read"}[args.command]
            payload = ({"query": args.query, "limit": args.limit, "include_candidates": args.candidates}
                       if args.command == "search" else {"id": args.id} if args.command == "read" else {})
            result = reader.handle(op, payload)
        finally:
            close = getattr(reader, "close", None)
            if close is not None:
                close()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
