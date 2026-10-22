"""Long-lived JSONL memory worker supervised by the official host service.

Parent pipe closure ends maintenance. Stdout carries responses only; all model
requests are leased to the host and use its configured LLM and credentials.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, TextIO

from .service import MemoryService


def serve(service: MemoryService, source: TextIO, sink: TextIO) -> None:
    service.start()
    try:
        while True:
            line = source.readline(2_000_001)
            if not line:
                break
            if not line.strip():
                continue
            request_id: Any = None
            stop = False
            try:
                if len(line) > 2_000_000:
                    raise ValueError("memory request exceeds 2 MB")
                request = json.loads(line)
                if not isinstance(request, dict):
                    raise ValueError("memory request must be an object")
                request_id = request.get("id")
                if request_id is not None and (not isinstance(request_id, (str, int)) or isinstance(request_id, bool)):
                    raise ValueError("memory request id must be a string or integer")
                op = request.get("op")
                args = request.get("args") or {}
                if not isinstance(op, str) or not isinstance(args, dict):
                    raise ValueError("memory request needs string op and object args")
                stop = op == "stop"
                result = {"status": "stopping"} if stop else service.handle(op, args)
                response = {"id": request_id, "ok": True, "result": result}
            except Exception as error:
                response = {"id": request_id, "ok": False,
                            "error": {"type": type(error).__name__, "message": str(error)[:2000]}}
            sink.write(json.dumps(response, ensure_ascii=False, allow_nan=False) + "\n")
            sink.flush()
            if stop:
                break
    finally:
        service.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Mirally background memory worker")
    parser.add_argument("--product-root", "--root", dest="root", type=Path,
                        default=Path(__file__).resolve().parents[3])
    parser.add_argument("--memory-config", "--config", dest="config", type=Path)
    arguments = parser.parse_args(argv)
    reader = sys.stdin
    if sys.platform == "win32" and not sys.stdin.isatty():
        from mirally.platform.stdio_pipe import control_pipe_reader
        reader = control_pipe_reader(sys.stdin)
    try:
        serve(MemoryService(arguments.root, arguments.config), reader, sys.stdout)
    except (BrokenPipeError, KeyboardInterrupt):
        return 0
    finally:
        if reader is not sys.stdin:
            reader.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
