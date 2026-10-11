"""CLI for the local activity store and sensor lifecycle."""

from __future__ import annotations

import argparse
import json
import signal
import sys
import time
from pathlib import Path
from typing import Any, Iterable, Optional

from openassist.core.activity.knowledge import ActivityOrganizer, ContextQuery, KnowledgeStore
from openassist.core.activity.store import CursorExpiredError, ObservationStore

from .config import ActivityConfig, load_activity_config
from .lock import ActivityProcessLock
from .monitor import ActivityMonitorRuntime, build_test_config
from .organizer_service import ActivityOrganizerService
from .service import ActivitySensor


def _root() -> Path:
    return Path(__file__).resolve().parents[3]


def _config(args: argparse.Namespace) -> ActivityConfig:
    configured_path = getattr(args, "config", None) or getattr(args, "monitor_config", None)
    path = Path(configured_path).resolve() if configured_path else None
    return load_activity_config(_root(), path)


def _store(config: ActivityConfig, *, read_only: bool = False) -> ObservationStore:
    return ObservationStore(config.database_path, asset_directory=config.asset_directory, read_only=read_only)


def _knowledge_path(config: ActivityConfig) -> Path:
    return config.knowledge_database_path or config.database_path.with_name("activity-knowledge.sqlite3")


def _print(value: Any) -> None:
    # Redirected stdout uses the Windows locale encoding by default. Activity
    # content can contain arbitrary OCR/UIA Unicode; keep JSON exports UTF-8.
    if callable(getattr(sys.stdout, "reconfigure", None)):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def command_status(args: argparse.Namespace) -> int:
    config = _config(args)
    if not config.database_path.is_file():
        _print({
            "status": "not_ready",
            "enabled": config.enabled,
            "database": str(config.database_path),
            "knowledge_database": str(_knowledge_path(config)),
            "policy_version": config.policy.policy_version,
            "collection_level": config.policy.default_level,
            "health": None,
            "collector": {},
            "collector_diagnostics": {},
        })
        return 0
    with _store(config, read_only=True) as store:
        health = store.health()
        state = store.get_state("collector", {})
        _print(
            {
                "enabled": config.enabled,
                "database": str(config.database_path),
                "knowledge_database": str(_knowledge_path(config)),
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
                    "raw_content_policy": "complete_within_declared_scope",
                    "legacy_uia_size_limits_applied": False,
                },
                "collector": state,
                "collector_diagnostics": store.get_state("collector_diagnostics", {}),
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
    with _store(config, read_only=True) as store:
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
    with _store(config, read_only=True) as store:
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


def _knowledge_context(args: argparse.Namespace) -> ContextQuery:
    mode = getattr(args, "mode", "outline")
    return ContextQuery(
        query=getattr(args, "query", None), start_utc_ms=getattr(args, "start", None),
        end_utc_ms=getattr(args, "end", None), apps=tuple(getattr(args, "app", None) or ()),
        windows=tuple(getattr(args, "window", None) or ()), kinds=tuple(getattr(args, "kind", None) or ()),
        episode_id=getattr(args, "episode", None), mode=mode, purpose=getattr(args, "purpose", "agent_context"),
        max_items=getattr(args, "limit", 30), max_episodes=getattr(args, "episodes", 10),
        token_budget=getattr(args, "tokens", 4000), include_assets=bool(getattr(args, "include_assets", False)),
        include_uia=bool(getattr(args, "include_uia", False)), include_ocr=not bool(getattr(args, "no_ocr", False)),
        after_item_id=getattr(args, "after_item", None),
    )


def command_knowledge_status(args: argparse.Namespace) -> int:
    config = _config(args)
    knowledge_path = _knowledge_path(config)
    if not config.database_path.is_file() or not knowledge_path.is_file():
        _print({
            "status": "not_ready",
            "source_database": str(config.database_path),
            "knowledge_database": str(knowledge_path),
            "health": {"available": False, "last_error": "knowledge-process has not produced a projection"},
        })
        return 0
    with _store(config, read_only=True) as source, KnowledgeStore(knowledge_path, read_only=True) as knowledge:
        organizer = ActivityOrganizer(source, knowledge)
        _print(organizer.health())
    return 0


def command_knowledge_process(args: argparse.Namespace) -> int:
    config = _config(args)
    with _store(config, read_only=True) as source, KnowledgeStore(_knowledge_path(config)) as knowledge:
        organizer = ActivityOrganizer(source, knowledge)
        if args.rebuild:
            result = organizer.rebuild(batch_size=args.limit).__dict__
        else:
            result = organizer.catch_up(batch_size=args.limit, max_batches=args.batches).__dict__
        _print({"result": result, "health": organizer.health()})
    return 0


def command_knowledge_query(args: argparse.Namespace) -> int:
    config = _config(args)
    query = _knowledge_context(args)
    query.validate()
    with _store(config, read_only=True) as source, KnowledgeStore(_knowledge_path(config), read_only=not args.sync) as knowledge:
        organizer = ActivityOrganizer(source, knowledge)
        if args.sync:
            organizer.catch_up(batch_size=args.sync_limit, max_batches=args.sync_batches)
        _print(organizer.context(query).to_dict())
    return 0


def command_knowledge_timeline(args: argparse.Namespace) -> int:
    config = _config(args)
    query = _knowledge_context(args)
    query.validate()
    with _store(config, read_only=True) as source, KnowledgeStore(_knowledge_path(config), read_only=not args.sync) as knowledge:
        organizer = ActivityOrganizer(source, knowledge)
        if args.sync:
            organizer.catch_up(batch_size=args.sync_limit, max_batches=args.sync_batches)
        _print(organizer.activity_timeline(query))
    return 0


def command_knowledge_events(args: argparse.Namespace) -> int:
    config = _config(args)
    query = _knowledge_context(args)
    query.validate()
    with _store(config, read_only=True) as source, KnowledgeStore(_knowledge_path(config), read_only=not args.sync) as knowledge:
        organizer = ActivityOrganizer(source, knowledge)
        if args.sync:
            organizer.catch_up(batch_size=args.sync_limit, max_batches=args.sync_batches)
        _print(organizer.activity_events(query))
    return 0


def command_knowledge_candidates(args: argparse.Namespace) -> int:
    config = _config(args)
    with KnowledgeStore(_knowledge_path(config), read_only=True) as knowledge:
        _print([candidate.to_dict() for candidate in knowledge.list_memory_candidates(status=args.status, limit=args.limit)])
    return 0


def command_knowledge_facts(args: argparse.Namespace) -> int:
    config = _config(args)
    with KnowledgeStore(_knowledge_path(config), read_only=True) as knowledge:
        _print([fact.to_dict() for fact in knowledge.list_memory_facts(status=args.status, limit=args.limit)])
    return 0


def command_knowledge_summary(args: argparse.Namespace) -> int:
    config = _config(args)
    query = _knowledge_context(args)
    query.validate()
    with _store(config, read_only=True) as source, KnowledgeStore(_knowledge_path(config), read_only=not args.sync) as knowledge:
        organizer = ActivityOrganizer(source, knowledge)
        if args.sync:
            organizer.catch_up(batch_size=args.sync_limit, max_batches=args.sync_batches)
        _print(organizer.activity_summary(query))
    return 0


def command_run(args: argparse.Namespace) -> int:
    config = _config(args)
    if not config.enabled:
        print("Activity Sensor is disabled. Copy activity.yaml.template to activity.yaml and enable it.", file=sys.stderr)
        return 2
    sensor = ActivitySensor(config)
    organizer_service = ActivityOrganizerService(config)
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
        organizer_service.close()
        sensor.close()
        return 4
    try:
        sensor.start()
        organizer_service.start()
        if args.once:
            sensor.run_once()
            organizer_service.process_once(limit=config.knowledge_batch_size)
            return 0
        sensor.start_evidence_worker()
        deadline = time.monotonic() + args.duration if args.duration else None
        while not stop and (deadline is None or time.monotonic() < deadline):
            sensor.run_once()
            time.sleep(config.poll_interval_seconds)
        return 0
    finally:
        organizer_service.close()
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

def command_browse(args: argparse.Namespace) -> int:
    from dataclasses import replace
    from .browser import ActivityBrowserRuntime

    configured_path = getattr(args, "config", None) or getattr(args, "browser_config", None)
    if args.test and configured_path:
        print("browse --test cannot be combined with --config", file=sys.stderr)
        return 2
    if args.test:
        config = build_test_config(_root())
    else:
        config = load_activity_config(_root(), Path(configured_path).resolve() if configured_path else None)
    if args.database:
        database = Path(args.database).resolve()
        config = replace(
            config, database_path=database,
            asset_directory=Path(args.assets).resolve() if args.assets else database.parent / "assets",
            knowledge_database_path=Path(args.knowledge_database).resolve() if args.knowledge_database else database.with_name("activity-knowledge.sqlite3"),
        )
    if not config.database_path.is_file():
        print(f"Activity database not found: {config.database_path}", file=sys.stderr)
        return 2
    try:
        runtime = ActivityBrowserRuntime(config, root=_root(), sync=not args.no_sync)
        return runtime.serve(port=args.port, open_browser=not args.no_open, duration=args.duration)
    except (OSError, ValueError, RuntimeError) as error:
        print(f"Activity browser: {error}", file=sys.stderr)
        return 2


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
    knowledge = sub.add_parser("knowledge-status", help="show activity knowledge projection health")
    knowledge.set_defaults(handler=command_knowledge_status)
    process = sub.add_parser("knowledge-process", help="project raw observations into activity knowledge")
    process.add_argument("--limit", type=int, default=200)
    process.add_argument("--batches", type=int, default=100)
    process.add_argument("--rebuild", action="store_true")
    process.set_defaults(handler=command_knowledge_process)
    context = sub.add_parser("knowledge-query", help="query activity context and evidence")
    context.add_argument("--query")
    context.add_argument("--start", type=int)
    context.add_argument("--end", type=int)
    context.add_argument("--app", action="append")
    context.add_argument("--window", action="append")
    context.add_argument("--kind", action="append")
    context.add_argument("--episode")
    context.add_argument("--after-item")
    context.add_argument("--mode", choices=("outline", "structured", "evidence"), default="outline")
    context.add_argument("--purpose", choices=("agent_context", "memory_maintenance", "diagnostics"), default="agent_context")
    context.add_argument("--limit", type=int, default=30)
    context.add_argument("--episodes", type=int, default=10)
    context.add_argument("--tokens", type=int, default=4000)
    context.add_argument("--include-assets", action="store_true")
    context.add_argument("--include-uia", action="store_true")
    context.add_argument("--no-ocr", action="store_true")
    context.add_argument("--sync", action="store_true", help="process pending observations before querying")
    context.add_argument("--sync-limit", type=int, default=200)
    context.add_argument("--sync-batches", type=int, default=10)
    context.set_defaults(handler=command_knowledge_query)
    timeline = sub.add_parser("knowledge-timeline", help="show observed actions, changes, uncertainty and evidence references")
    timeline.add_argument("--query")
    timeline.add_argument("--start", type=int)
    timeline.add_argument("--end", type=int)
    timeline.add_argument("--app", action="append")
    timeline.add_argument("--window", action="append")
    timeline.add_argument("--kind", action="append")
    timeline.add_argument("--episode")
    timeline.add_argument("--after-item")
    timeline.add_argument("--limit", type=int, default=30)
    timeline.add_argument("--tokens", type=int, default=4000)
    timeline.add_argument("--include-uia", action="store_true")
    timeline.add_argument("--no-ocr", action="store_true")
    timeline.add_argument("--sync", action="store_true")
    timeline.add_argument("--sync-limit", type=int, default=200)
    timeline.add_argument("--sync-batches", type=int, default=10)
    timeline.set_defaults(handler=command_knowledge_timeline, purpose="diagnostics")
    events = sub.add_parser("knowledge-events", help="group observed actions, window state and text into activity events")
    events.add_argument("--query")
    events.add_argument("--start", type=int)
    events.add_argument("--end", type=int)
    events.add_argument("--app", action="append")
    events.add_argument("--window", action="append")
    events.add_argument("--kind", action="append")
    events.add_argument("--episode")
    events.add_argument("--after-item")
    events.add_argument("--limit", type=int, default=100, help="maximum raw activity items per page (1-500)")
    events.add_argument("--episodes", type=int, default=30, help="maximum events per page (1-100)")
    events.add_argument("--tokens", type=int, default=8000)
    events.add_argument("--include-assets", action="store_true")
    events.add_argument("--no-uia", dest="include_uia", action="store_false", help="exclude accessible window text")
    events.add_argument("--no-ocr", action="store_true")
    events.add_argument("--sync", action="store_true", help="process pending observations before querying")
    events.add_argument("--sync-limit", type=int, default=200)
    events.add_argument("--sync-batches", type=int, default=10)
    events.set_defaults(handler=command_knowledge_events, purpose="diagnostics", include_uia=True)
    summary = sub.add_parser("knowledge-summary", help="summarize activity by app and episode")
    summary.add_argument("--query")
    summary.add_argument("--start", type=int)
    summary.add_argument("--end", type=int)
    summary.add_argument("--app", action="append")
    summary.add_argument("--window", action="append")
    summary.add_argument("--kind", action="append")
    summary.add_argument("--episode")
    summary.add_argument("--limit", type=int, default=100)
    summary.add_argument("--episodes", type=int, default=100)
    summary.add_argument("--tokens", type=int, default=4000)
    summary.add_argument("--sync", action="store_true")
    summary.add_argument("--sync-limit", type=int, default=200)
    summary.add_argument("--sync-batches", type=int, default=10)
    summary.set_defaults(handler=command_knowledge_summary)
    candidates = sub.add_parser("knowledge-candidates", help="list memory candidates")
    candidates.add_argument("--status")
    candidates.add_argument("--limit", type=int, default=100)
    candidates.set_defaults(handler=command_knowledge_candidates)
    facts = sub.add_parser("knowledge-facts", help="list accepted activity memory facts")
    facts.add_argument("--status", choices=("active", "superseded", "retracted"))
    facts.add_argument("--limit", type=int, default=100)
    facts.set_defaults(handler=command_knowledge_facts)
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
    browse = sub.add_parser("browse", help="browse all layers of existing activity data without collection")
    browse.add_argument("--config", dest="browser_config", help="activity.yaml path (may follow browse)")
    browse.add_argument("--test", action="store_true", help="browse existing .artifacts/activity-monitor data")
    browse.add_argument("--database", help="source observations.sqlite3 path")
    browse.add_argument("--knowledge-database", help="derived knowledge database (with --database)")
    browse.add_argument("--assets", help="asset directory (with --database)")
    browse.add_argument("--no-sync", action="store_true", help="read the existing derived index without catching up")
    browse.add_argument("--port", type=int, default=0, help="loopback port (0 chooses a free port)")
    browse.add_argument("--duration", type=float)
    browse.add_argument("--no-open", action="store_true", help="print the URL without opening a browser")
    browse.set_defaults(handler=command_browse)
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
