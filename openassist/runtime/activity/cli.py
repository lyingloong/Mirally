"""CLI for the local activity store and sensor lifecycle."""

from __future__ import annotations

import argparse
import json
import signal
import sys
import time
from pathlib import Path
from typing import Any, Iterable, Optional

from openassist.core.activity.store import CursorExpiredError, ObservationStore

from .config import ActivityConfig, load_activity_config
from .lock import ActivityProcessLock
from .monitor import ActivityMonitorRuntime, build_test_config
from .service import ActivitySensor


def _root() -> Path:
    return Path(__file__).resolve().parents[3]


def _config(args: argparse.Namespace) -> ActivityConfig:
    configured_path = getattr(args, "config", None) or getattr(args, "monitor_config", None)
    path = Path(configured_path).resolve() if configured_path else None
    return load_activity_config(_root(), path)


def _store(config: ActivityConfig) -> ObservationStore:
    return ObservationStore(config.database_path, asset_directory=config.asset_directory)


def _print(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def command_status(args: argparse.Namespace) -> int:
    config = _config(args)
    with _store(config) as store:
        health = store.health()
        state = store.get_state("collector", {})
        _print(
            {
                "enabled": config.enabled,
                "database": str(config.database_path),
                "policy_version": config.policy.policy_version,
                "collection_level": config.policy.default_level,
                "window_context": {
                    "window_titles": config.window_titles_enabled,
                    "browser_urls": config.browser_urls_enabled,
                    "browser_url_timeout_ms": config.browser_url_timeout_ms,
                    "browser_url_poll_interval_seconds": config.browser_url_poll_interval_seconds,
                },
                "baseline_requirements": {
                    "foreground_metadata": True,
                    "input_metadata": config.input_enabled,
                    "uia_structure": config.ui_structure_enabled,
                    "input_quiet_ms": config.input_quiet_ms,
                    "input_max_burst_ms": config.input_max_burst_ms,
                    "uia_timeout_ms": config.uia_timeout_ms,
                    "uia_max_depth": config.uia_max_depth,
                    "uia_max_nodes": config.uia_max_nodes,
                },
                "collector": state,
                "health": health.__dict__,
            }
        )
    return 0


def command_doctor(args: argparse.Namespace) -> int:
    config = _config(args)
    errors = []
    try:
        with _store(config) as store:
            health = store.health()
            recovery = store.recover_assets()
    except Exception as error:
        errors.append(f"store: {type(error).__name__}: {error}")
        health = None
        recovery = {}
    _print(
        {
            "config_exists": bool(args.config and Path(args.config).is_file())
            if args.config
            else (Path(args.config or _root() / "config/openassist/activity.yaml").is_file()),
            "enabled": config.enabled,
            "health": health.__dict__ if health else None,
            "asset_recovery": recovery,
            "errors": errors,
        }
    )
    return 1 if errors else 0


def command_query(args: argparse.Namespace) -> int:
    config = _config(args)
    with _store(config) as store:
        rows = store.query(
            start_utc_ms=args.start,
            end_utc_ms=args.end,
            app_ids=args.app,
            actors=args.actor,
            kinds=args.kind,
            after_ingest_seq=args.after,
            limit=args.limit,
        )
        _print([row.to_dict() for row in rows])
    return 0


def command_tail(args: argparse.Namespace) -> int:
    config = _config(args)
    with _store(config) as store:
        cursor = args.after
        deadline = time.monotonic() + args.duration if args.duration else None
        while True:
            rows, cursor, _ = store.read_since(cursor, limit=args.limit)
            for row in rows:
                _print(row.to_dict())
            if args.once or (deadline is not None and time.monotonic() >= deadline):
                return 0
            time.sleep(max(0.05, args.interval))


def command_control(args: argparse.Namespace) -> int:
    config = _config(args)
    with _store(config) as store:
        if args.action == "pause":
            store.set_state("collector_pause", {"paused": True, "reason": args.reason, "at": int(time.time() * 1000)})
        elif args.action == "resume":
            store.set_state("collector_pause", {"paused": False, "at": int(time.time() * 1000)})
        else:
            store.set_state("collector", {"status": "stop_requested", "at": int(time.time() * 1000)})
        _print(store.get_state("collector_pause", store.get_state("collector", {})))
    return 0


def command_run(args: argparse.Namespace) -> int:
    config = _config(args)
    if not config.enabled:
        print("Activity Sensor is disabled. Copy activity.yaml.template to activity.yaml and enable it.", file=sys.stderr)
        return 2
    sensor = ActivitySensor(config)
    stop = False

    def request_stop(*_: Any) -> None:
        nonlocal stop
        stop = True

    previous = []
    for name in ("SIGINT", "SIGTERM"):
        signal_value = getattr(signal, name, None)
        if signal_value is not None:
            previous.append((signal_value, signal.getsignal(signal_value)))
            signal.signal(signal_value, request_stop)
    lock = ActivityProcessLock(fallback_path=config.database_path.with_suffix(".lock"))
    if not lock.acquire():
        print("another Mirally activity sensor is already running", file=sys.stderr)
        sensor.close()
        return 4
    try:
        sensor.start()
        if args.once:
            sensor.run_once()
            return 0
        sensor.start_evidence_worker()
        deadline = time.monotonic() + args.duration if args.duration else None
        while not stop and (deadline is None or time.monotonic() < deadline):
            sensor.run_once()
            time.sleep(config.poll_interval_seconds)
        return 0
    finally:
        sensor.close()
        lock.release()
        for signal_value, handler in previous:
            signal.signal(signal_value, handler)


def command_monitor(args: argparse.Namespace) -> int:
    configured_path = getattr(args, "config", None) or getattr(args, "monitor_config", None)
    if args.test and configured_path:
        print("monitor --test cannot be combined with --config", file=sys.stderr)
        return 2
    if not args.test and (args.content or args.persist_images):
        print("--content and --persist-images are only available with --test", file=sys.stderr)
        return 2
    if args.test:
        if args.metadata_only and (args.content or args.persist_images):
            print("--metadata-only cannot be combined with --content or --persist-images", file=sys.stderr)
            return 2
        config = build_test_config(_root(), content=not args.metadata_only, persist_images=not args.metadata_only)
    else:
        config = _config(args)
        if not config.enabled:
            print(
                "Activity Sensor is disabled. Use monitor --test for an isolated diagnostic run, "
                "or enable config/openassist/activity.yaml.",
                file=sys.stderr,
            )
            return 2
    runtime = ActivityMonitorRuntime(config, root=_root())
    return runtime.serve(port=args.port, open_browser=not args.no_open, duration=args.duration)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Mirally local activity observation service")
    parser.add_argument("--config", help="activity.yaml path")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status").set_defaults(handler=command_status)
    sub.add_parser("doctor").set_defaults(handler=command_doctor)
    query = sub.add_parser("query")
    query.add_argument("--start", type=int)
    query.add_argument("--end", type=int)
    query.add_argument("--after", type=int)
    query.add_argument("--app", action="append")
    query.add_argument("--actor", action="append")
    query.add_argument("--kind", action="append")
    query.add_argument("--limit", type=int, default=100)
    query.set_defaults(handler=command_query)
    tail = sub.add_parser("tail")
    tail.add_argument("--after", type=int, default=0)
    tail.add_argument("--limit", type=int, default=100)
    tail.add_argument("--interval", type=float, default=0.5)
    tail.add_argument("--duration", type=float)
    tail.add_argument("--once", action="store_true")
    tail.set_defaults(handler=command_tail)
    run = sub.add_parser("run")
    run.add_argument("--duration", type=float)
    run.add_argument("--once", action="store_true")
    run.set_defaults(handler=command_run)
    monitor = sub.add_parser("monitor", help="run the local Activity data-layer diagnostics UI")
    monitor.add_argument("--config", dest="monitor_config", help="activity.yaml path (may follow monitor)")
    monitor.add_argument("--test", action="store_true", help="use an isolated enabled config under .artifacts/")
    monitor.add_argument("--content", action="store_true", help="compatibility flag; rich content is now the test default")
    monitor.add_argument("--persist-images", action="store_true", help="compatibility flag; images are retained by default in test mode")
    monitor.add_argument("--metadata-only", action="store_true", help="test only metadata and UIA structure")
    monitor.add_argument("--port", type=int, default=0, help="loopback port (0 chooses a free port)")
    monitor.add_argument("--duration", type=float)
    monitor.add_argument("--no-open", action="store_true", help="print the URL without opening a browser")
    monitor.set_defaults(handler=command_monitor)
    for action in ("pause", "resume", "stop"):
        control = sub.add_parser(action)
        control.add_argument("--reason", default="cli")
        control.set_defaults(handler=command_control, action=action)
    return parser


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        return int(args.handler(args))
    except CursorExpiredError as error:
        print(f"cursor expired: {error}", file=sys.stderr)
        return 3
    except Exception as error:
        print(f"activity command failed: {type(error).__name__}: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
