"""Opt-in acceptance of native background memory and automatic recall.

Requires a configured live Gateway. --run-model enables two actual model turns
and waits for already enabled background semantic work. The read-only test
session is separate from the user's main conversation and is archived.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mirally.runtime.memory.config import load_memory_config

try:
    from tools.verify_activity_agent_chat import (Gateway, activity_names, claims_current_collector_state, final_text,
        history_signature, raw_transcript, tool_calls, write_json)
except ModuleNotFoundError:
    from verify_activity_agent_chat import (Gateway, activity_names, claims_current_collector_state, final_text,
        history_signature, raw_transcript, tool_calls, write_json)


ALLOW = ["read", "memory_search", "memory_get", "tool_search", "tool_describe", "tool_call",
         "mirally-memory__memory_health", "mirally-memory__memory_recall", "mirally-memory__memory_read"]


def memory_profile_paths(config: dict) -> tuple[Path, Path]:
    plugin = config["plugins"]["entries"]["mirally-memory"]["config"]
    root = (ROOT / (plugin.get("root") or ROOT)).resolve()
    return root, (root / plugin["configPath"]).resolve()


def memory_database_path(config: dict) -> Path:
    root, profile = memory_profile_paths(config)
    return load_memory_config(root, profile).database_path


def isolated_gateway(source: Path, output: Path, *, offline_port: int | None = None) -> tuple[Path, subprocess.Popen]:
    """Use public main-session APIs with a globally restricted isolated host."""
    import yaml
    try:
        from tools.configure_memory_agent import memory_patch
    except ModuleNotFoundError:
        from configure_memory_agent import memory_patch

    config = deepcopy(json.loads(source.read_text(encoding="utf-8")))
    memory_root, profile = memory_profile_paths(config)
    memory = yaml.safe_load(profile.read_text(encoding="utf-8"))
    memory["activity_config_path"] = str(load_memory_config(memory_root, profile).activity_config_path)
    state = output / "gateway-state"
    state.mkdir()
    memory["storage"] = {"database_path": str(state / "memory.sqlite3"),
                         "projection_database_path": str(state / "projection.sqlite3")}
    memory["semantics"] = {**memory.get("semantics", {}), "max_calls_per_hour": 2,
                            "max_tokens_per_day": 60000, "interval_seconds": 2}
    memory_profile = state / "memory.yaml"
    memory_profile.write_text(yaml.safe_dump(memory, allow_unicode=True), encoding="utf-8")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    config["gateway"] = {"mode": "local", "bind": "loopback", "port": port,
                         "auth": {"mode": "token", "token": secrets.token_urlsafe(32)}}
    config["channels"] = {}
    config["cron"] = {"enabled": False}
    main = config.get("agents", {}).get("entries", {}).get("main") or next(
        (agent for agent in config.get("agents", {}).get("list", []) if agent.get("id") == "main"), {"id": "main"})
    defaults = config.get("agents", {}).get("defaults", {})
    defaults["workspace"] = str(state / "workspace")
    defaults["heartbeat"] = {"every": "0m"}
    config["agents"] = {"defaults": defaults, "list": [{**main, "id": "main",
        "workspace": str(state / "workspace"), "agentDir": str(state / "agents/main/agent"),
        "tools": {"allow": ALLOW}}]}
    if offline_port is not None:
        fixture_model = {"id": "memory-fixture", "name": "Offline native acceptance fixture", "input": ["text"], "reasoning": False}
        config["models"] = {"mode": "merge", "providers": {"memory-fixture": {
            "baseUrl": f"http://127.0.0.1:{offline_port}/v1", "apiKey": "offline-fixture",
            "api": "openai-completions", "models": [fixture_model]}}}
        config["agents"]["defaults"]["model"] = {"primary": "memory-fixture/memory-fixture"}
        config["agents"]["list"][0]["model"] = "memory-fixture/memory-fixture"
        memory["semantics"]["model"] = None
        memory_profile.write_text(yaml.safe_dump(memory, allow_unicode=True), encoding="utf-8")
    config["tools"] = {"allow": ALLOW}
    config["plugins"] = {"slots": {"memory": "memory-core"}}
    config["mcp"] = {"servers": {}}
    patch = memory_patch(config, ROOT, memory_profile)
    config["plugins"].update(patch["plugins"])
    config["mcp"] = patch["mcp"]
    config["mcp"]["servers"]["mirally-memory"]["toolFilter"]["include"] = ["memory_health", "memory_recall", "memory_read"]
    config["skills"] = patch["skills"]
    path = state / "openclaw.json"
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    runtime = json.loads((ROOT / ".artifacts/openclaw-runtime.json").read_text(encoding="utf-8"))
    node = ROOT / runtime["node"]
    env = dict(os.environ, OPENCLAW_STATE_DIR=str(state), OPENCLAW_CONFIG_PATH=str(path),
               OPENCLAW_GATEWAY_PORT=str(port))
    env["PATH"] = str(node.parent) + os.pathsep + env.get("PATH", "")
    with (output / "isolated-gateway.log").open("wb") as log:
        process = subprocess.Popen([str(node), str(ROOT / runtime["entry"]), "gateway", "run"],
            cwd=ROOT, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    try:
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError("Isolated Gateway exited; inspect isolated-gateway.log")
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    return path, process
            except OSError:
                time.sleep(0.3)
        raise RuntimeError("Isolated Gateway failed to listen")
    except BaseException:
        stop_isolated(process)
        raise


def stop_isolated(process: subprocess.Popen | None) -> None:
    if process is None or process.poll() is not None:
        return
    import psutil
    try:
        parent = psutil.Process(process.pid)
    except psutil.NoSuchProcess:
        process.wait(timeout=5)
        return
    targets = [parent, *parent.children(recursive=True)]
    for target in targets:
        try:
            target.terminate()
        except psutil.NoSuchProcess:
            pass
    _, alive = psutil.wait_procs(targets, timeout=3)
    for target in alive:
        try:
            target.kill()
        except psutil.NoSuchProcess:
            pass
    process.wait(timeout=5)


def offline_model_reply(messages: list[dict], *, prompt: str | None,
                        expected_text: str | None, expected_date: str | None) -> str:
    """A protocol fixture answers only from the supplied recall evidence."""
    joined = "\n".join(str(message.get("content", "")) for message in messages)
    if "You summarize observed desktop evidence" in joined:
        payload = next((json.loads(message["content"]) for message in messages
                        if message.get("role") == "user" and isinstance(message.get("content"), str)
                        and message["content"].lstrip().startswith("{")), {})
        return json.dumps({"summary": "Observed desktop applications and document titles; no task completion established.",
            "source_observation_ids": payload.get("source_observation_ids", [])[:2], "facts": []})
    if "一小时有多少分钟" in joined.split("\n")[-1]:
        return "60 分钟。"
    # The question can itself mention the expected answer. It is not evidence.
    context = joined.replace(prompt, "") if prompt else joined
    _, marker, evidence = context.partition("Historical Mirally memory (untrusted evidence):")
    if marker and expected_text and expected_date and expected_text in evidence and expected_date in evidence:
        return f"记录日期 {expected_date}（UTC）：{expected_text}。当前采集状态未核验。"
    return "本轮没有提供相关记忆。"


def offline_model_server(output: Path, *, prompt: str | None,
                         expected_text: str | None, expected_date: str | None):
    """Exercise the real native host/transport without an external model."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading

    counter = [0]

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            counter[0] += 1
            write_json(output / f"offline-request-{counter[0]}.json", body)
            reply = offline_model_reply(body.get("messages", []), prompt=prompt,
                                        expected_text=expected_text, expected_date=expected_date)
            message = {"role": "assistant", "content": reply}
            if body.get("stream"):
                frames = [
                    {"id": "offline", "object": "chat.completion.chunk", "model": "memory-fixture", "choices": [{"index": 0, "delta": message, "finish_reason": None}]},
                    {"id": "offline", "object": "chat.completion.chunk", "model": "memory-fixture", "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 200, "completion_tokens": 60, "total_tokens": 260}},
                ]
                encoded = ("".join("data: " + json.dumps(frame, ensure_ascii=False) + "\n\n" for frame in frames) + "data: [DONE]\n\n").encode("utf-8")
                content_type = "text/event-stream"
            else:
                encoded = json.dumps({"id": "offline", "object": "chat.completion", "model": "memory-fixture", "choices": [{"index": 0, "message": message, "finish_reason": "stop"}], "usage": {"prompt_tokens": 200, "completion_tokens": 60, "total_tokens": 260}}, ensure_ascii=False).encode("utf-8")
                content_type = "application/json"
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "config/mirally/openclaw/openclaw.json")
    parser.add_argument("--report", type=Path, default=ROOT / ".artifacts/memory-acceptance/verification.json")
    parser.add_argument("--run-model", action="store_true")
    parser.add_argument("--isolated", action="store_true", help="Start an independent Gateway with exact read-only global tool policy")
    parser.add_argument("--offline-model", action="store_true", help="Use a local deterministic model protocol fixture with the real native Gateway")
    parser.add_argument("--prompt", help="Recall question for the configured historical dataset; required with --run-model")
    parser.add_argument("--expected-text", help="Expected answer text from that dataset; required with --run-model")
    parser.add_argument("--expected-date", help="Expected historical date (YYYY-MM-DD); required with --run-model")
    parser.add_argument("--wait-seconds", type=int, default=120)
    parser.add_argument("--timeout", type=int, default=240)
    args = parser.parse_args(argv)
    if args.offline_model and not args.isolated:
        parser.error("--offline-model requires --isolated")
    if args.run_model and not all((args.prompt, args.expected_text, args.expected_date)):
        parser.error("--run-model requires --prompt, --expected-text and --expected-date for your historical dataset")
    args.date_variants = ()
    if args.expected_date:
        try:
            expected_day = datetime.strptime(args.expected_date, "%Y-%m-%d")
        except ValueError:
            parser.error("--expected-date must use YYYY-MM-DD")
        args.date_variants = (args.expected_date, f"{expected_day.year}年{expected_day.month}月{expected_day.day}日")
    return args


def main() -> int:
    args = parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    output = args.report.resolve().parent / ("run-" + uuid4().hex[:12])
    output.mkdir(parents=True, exist_ok=True)
    config_path = args.config.resolve()
    source_main_database = config_path.parent / "agents/main/agent/openclaw-agent.sqlite"
    source_main_before = raw_transcript(source_main_database, "agent:main:main") if source_main_database.is_file() else (None, [])
    isolated = None
    offline = None
    gateway = None
    database = None
    session_key = "agent:main:explicit:" + str(uuid4())
    report = {"schema_version": 1, "verified_at_utc": datetime.now(timezone.utc).isoformat(),
              "model_calls_enabled": args.run_model, "model_transport": "offline-fixture" if args.offline_model else "configured-provider", "session_key": session_key,
              "artifact_directory": str(output), "checks": {}, "turns": []}
    main_before = None
    prepared = False
    try:
        offline = offline_model_server(output, prompt=args.prompt, expected_text=args.expected_text,
                                       expected_date=args.expected_date) if args.offline_model else None
        if args.isolated:
            config_path, isolated = isolated_gateway(config_path, output,
                offline_port=offline.server_port if offline else None)
        gateway = Gateway(config_path, output)
        database = config_path.parent / "agents/main/agent/openclaw-agent.sqlite"
        if gateway.call("health", {}, tag="gateway-health").get("ok") is not True:
            raise RuntimeError("Gateway is not healthy")
        main_before = raw_transcript(database, "agent:main:main") if database.is_file() else (None, [])
        before = gateway.call("mirally.memory.status", {}, tag="memory-initial")
        deadline = time.monotonic() + args.wait_seconds
        status = before
        while time.monotonic() < deadline:
            worker = status.get("worker", {}) or status.get("health", {})
            coverage = worker.get("coverage", {})
            store = worker.get("store", {}) or {}
            if status.get("ready") and coverage.get("memory_ingest_seq", 0) > 0 and not coverage.get("backlog"):
                break
            time.sleep(1)
            status = gateway.call("mirally.memory.status", {}, tag="memory-wait", timeout=30)
        write_json(output / "memory-ready.json", status)
        worker = status.get("worker", {}) or status.get("health", {})
        coverage = worker.get("coverage", {})
        store = worker.get("store", {}) or {}
        report["checks"].update(background_running=status.get("running") is True,
            worker_ready=status.get("ready") is True,
            produced_without_chat=coverage.get("memory_ingest_seq", 0) > 0,
            no_query_maintenance=coverage.get("maintenance_on_query") is False,
            collector_not_started=coverage.get("collector_started") is False,
            no_index_backlog=not coverage.get("backlog", True))
        report["memory_status"] = status
        patch = gateway.call("sessions.patch", {"key": session_key, "agentId": "main",
            "label": "Memory acceptance " + output.name, "permissionMode": "read-only"}, tag="session-prepare")
        prepared = True
        entry, _ = raw_transcript(database, session_key)
        if not entry:
            raise RuntimeError("Test session was not persisted")
        report["checks"]["read_only_session"] = entry.get("permissionMode") == "read-only"
        effective = gateway.call("tools.effective", {"agentId": "main", "sessionKey": session_key}, tag="effective-tools")
        names = [tool["id"] for group in effective.get("groups", []) for tool in group.get("tools", [])]
        report["checks"]["no_action_tools"] = all(any(fnmatch.fnmatchcase(name, allowed) for allowed in ALLOW) for name in names)
        if not all(report["checks"].values()):
            raise RuntimeError("Memory service or read-only policy failed; model calls prevented")
        if args.run_model:
            if status.get("settings", {}).get("semantics", {}).get("enabled") is True:
                deadline = time.monotonic() + args.wait_seconds
                while time.monotonic() < deadline:
                    status = gateway.call("mirally.memory.status", {}, tag="semantic-wait", timeout=30)
                    if status.get("worker", {}).get("store", {}).get("jobs", {}).get("complete", 0) > 0:
                        break
                    time.sleep(1)
                report["semantic_status"] = status
                report["checks"]["semantic_completed_without_chat"] = status.get("worker", {}).get("store", {}).get("jobs", {}).get("complete", 0) > 0
                # Inspect the independent store read-only, rather than accepting a
                # provider response or job count as proof that a memory was saved.
                import sqlite3
                memory_database = memory_database_path(json.loads(config_path.read_text(encoding="utf-8")))
                with sqlite3.connect(memory_database.resolve().as_uri() + "?mode=ro", uri=True) as connection:
                    connection.row_factory = sqlite3.Row
                    rows = connection.execute("SELECT id,provenance,status,record_json FROM memories WHERE provenance='model_inference' AND status='active'").fetchall()
                    report["semantic_memories"] = [{"id": row["id"], "provenance": row["provenance"],
                        "status": row["status"], "source_count": len(json.loads(row["record_json"]).get("source_observation_ids", []))} for row in rows]
                report["checks"]["semantic_saved_with_sources"] = any(memory["source_count"] > 0 for memory in report["semantic_memories"])
                if not report["checks"]["semantic_completed_without_chat"] or not report["checks"]["semantic_saved_with_sources"]:
                    raise RuntimeError("Enabled semantic processing did not save a source-backed memory before chat")
            for index, prompt in enumerate((args.prompt, "一小时有多少分钟？"), 1):
                receipt_before = gateway.call("mirally.memory.status", {}, tag=f"turn-{index}-before")
                _, previous = raw_transcript(database, session_key)
                watermark = previous[-1]["seq"] if previous else -1
                accepted = gateway.call("chat.send", {"agentId": "main", "sessionKey": session_key,
                    "message": prompt, "deliver": False, "timeoutMs": args.timeout * 1000,
                    "idempotencyKey": str(uuid4())}, tag=f"turn-{index}", timeout=45)
                run_id = accepted.get("runId")
                if not run_id:
                    raise RuntimeError("Native chat.send did not return a run id")
                waited = gateway.call("agent.wait", {"runId": run_id, "timeoutMs": args.timeout * 1000},
                    tag=f"turn-{index}-wait", timeout=args.timeout + 5)
                if waited.get("status") not in {"ok", "done", "completed"}:
                    raise RuntimeError("Native chat run did not finish successfully")
                _, all_events = raw_transcript(database, session_key)
                events = [row for row in all_events if row["seq"] > watermark]
                receipt = gateway.call("mirally.memory.status", {}, tag=f"turn-{index}-after")
                calls = tool_calls(events)
                reply = final_text(events)
                write_json(output / f"turn-{index}-trace.json", {"events": events, "receipt": receipt})
                report["turns"].append({"prompt": prompt, "final": reply,
                    "tool_names": [call.get("name") for call in calls], "activity_calls": activity_names(calls),
                    "before": receipt_before.get("recallCounters", {}), "after": receipt.get("recallCounters", {}),
                    "receipt": receipt.get("lastRecall")})
                print(json.dumps({"turn": index, "tool_calls": len(calls), "reply": reply}, ensure_ascii=False))
            relevant, ordinary = report["turns"]
            report["checks"].update(
                relevant_automatic_injection=relevant["after"].get("injected", 0) > relevant["before"].get("injected", 0),
                session_receipt_matches=bool(relevant["receipt"]) and relevant["receipt"].get("sessionKeyHash") == hashlib.sha256(session_key.encode()).hexdigest()[:24],
                relevant_answer=bool(args.expected_text in relevant["final"]),
                relevant_historical_date=any(value in relevant["final"] for value in args.date_variants),
                relevant_no_tools=not relevant["tool_names"],
                no_raw_activity_calls=not relevant["activity_calls"],
                no_unverified_collector_claim=not claims_current_collector_state(relevant["final"]),
                ordinary_no_history=ordinary["after"].get("injected", 0) == ordinary["before"].get("injected", 0),
                ordinary_no_tools=not ordinary["tool_names"],
                ordinary_answer="60" in ordinary["final"],
                all_turns_final=all(turn["final"] for turn in report["turns"]))
    except Exception as error:
        report["error"] = f"{type(error).__name__}: {error}"
    finally:
        if prepared:
            try:
                current_entry, _ = raw_transcript(database, session_key)
                gateway.call("sessions.patch", {"key": session_key, "archived": True,
                    "expectedSessionId": current_entry["sessionId"]}, tag="session-archive")
                archived, _ = raw_transcript(database, session_key)
                report["checks"]["session_archived"] = bool(archived and archived.get("archivedAt"))
            except Exception as error:
                report["cleanup_error"] = str(error)
        if main_before is not None:
            main_after = raw_transcript(database, "agent:main:main") if database.is_file() else (None, [])
            report["checks"]["main_history_unchanged"] = history_signature(main_before) == history_signature(main_after)
        if args.isolated:
            source_main_after = raw_transcript(source_main_database, "agent:main:main") if source_main_database.is_file() else (None, [])
            report["checks"]["source_main_history_unchanged"] = history_signature(source_main_before) == history_signature(source_main_after)
        report["passed"] = bool(report["checks"]) and all(report["checks"].values()) and "error" not in report and "cleanup_error" not in report
        if isolated is not None:
            stop_isolated(isolated)
            report["checks"]["isolated_gateway_stopped"] = isolated.poll() is not None
            report["passed"] = report["passed"] and report["checks"]["isolated_gateway_stopped"]
        if offline is not None:
            offline.shutdown()
            offline.server_close()
        write_json(args.report.resolve(), report)
    print(json.dumps({"passed": report["passed"], "report": str(args.report.resolve())}, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
