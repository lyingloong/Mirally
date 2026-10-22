"""Verify Activity discovery in an isolated live Gateway conversation.

The default only creates a temporary read-only session and verifies its tool
policy. --run-model explicitly enables four configured-model turns, which can
incur API charges and send historical Activity evidence to that provider.
The existing main conversation, model selection and collector stay unchanged.
Full acceptance traces are saved locally; the test session is archived, never
deleted. This is an opt-in acceptance check, not part of the offline test suite.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
ALLOW = ["read", "memory_search", "memory_get", "sessions_search", "sessions_history",
         "tool_search", "tool_describe", "tool_call", "mirally-activity__*"]
PROMPTS = ["你能看到我之前做了什么吗", "能看到我使用电脑的activity吗",
           "帮我回顾最近一次有记录的电脑活动，按时间说明主要做了什么。如果记录不是今天的，请说明。",
           "一小时有多少分钟？"]


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_cli_json(text: str) -> dict:
    decoder = json.JSONDecoder()
    for offset, char in enumerate(text):
        if char != "{":
            continue
        try:
            value, end = decoder.raw_decode(text[offset:])
        except ValueError:
            continue
        if isinstance(value, dict) and not text[offset + end:].strip():
            return value
    raise ValueError("Gateway CLI did not return a JSON object")


class Gateway:
    def __init__(self, config: Path, output: Path):
        runtime = json.loads((ROOT / ".artifacts/openclaw-runtime.json").read_text(encoding="utf-8"))
        self.node = ROOT / runtime["node"]
        self.entry = ROOT / runtime["entry"]
        self.output = output
        self.env = dict(os.environ, OPENCLAW_STATE_DIR=str(config.parent), OPENCLAW_CONFIG_PATH=str(config))
        self.env["PATH"] = str(self.node.parent) + os.pathsep + self.env.get("PATH", "")

    def call(self, method: str, params: dict, *, tag: str, timeout: int = 45,
             expect_final: bool = False) -> dict:
        command = [str(self.node), str(self.entry), "gateway", "call", method,
                   "--json", "--params", json.dumps(params, ensure_ascii=False),
                   "--timeout", str(timeout * 1000)]
        if expect_final:
            command.append("--expect-final")
        result = subprocess.run(command, cwd=ROOT, env=self.env, capture_output=True,
                                text=True, encoding="utf-8", errors="replace", timeout=timeout + 15,
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        (self.output / f"{tag}.stdout.txt").write_text(result.stdout, encoding="utf-8")
        (self.output / f"{tag}.stderr.txt").write_text(result.stderr, encoding="utf-8")
        if result.returncode:
            raise RuntimeError(f"Gateway {method} failed; see {tag}.stderr.txt")
        value = parse_cli_json(result.stdout)
        write_json(self.output / f"{tag}.json", value)
        return value


def raw_transcript(database: Path, session_key: str) -> tuple[dict | None, list[dict]]:
    """Read the installed release's acceptance trace without mutating SQLite."""
    import zstandard  # Already used by the installed product dependencies.

    with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True) as connection:
        row = connection.execute("SELECT entry_json FROM session_nodes WHERE session_key = ?",
                                 (session_key,)).fetchone()
        if not row:
            return None, []
        entry = json.loads(row[0])
        events = []
        for seq, plain, compressed, size in connection.execute(
                "SELECT seq, event_json, event_zstd, event_utf8_bytes FROM transcript_events "
                "WHERE session_id = ? ORDER BY seq", (entry["sessionId"],)):
            encoded = plain if plain is not None else zstandard.ZstdDecompressor().decompress(
                compressed, max_output_size=size).decode("utf-8")
            events.append({"seq": seq, "event": json.loads(encoded)})
        return entry, events


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def tool_calls(events: list[dict]) -> list[dict]:
    calls = []
    for row in events:
        event = row["event"]
        message = event.get("message", {})
        for block in message.get("content", []) if isinstance(message.get("content"), list) else []:
            if isinstance(block, dict) and block.get("type") == "toolCall":
                calls.append({"seq": row["seq"], "timestamp": event.get("timestamp"), **block})
    return calls


def tool_results(events: list[dict]) -> list[dict]:
    return [{"seq": row["seq"], "timestamp": row["event"].get("timestamp"), **row["event"]["message"]}
            for row in events if row["event"].get("message", {}).get("role") == "toolResult"]


def final_text(events: list[dict]) -> str:
    for row in reversed(events):
        message = row["event"].get("message", {})
        if message.get("role") == "assistant":
            texts = [block.get("text", "") for block in message.get("content", [])
                     if isinstance(block, dict) and block.get("type") == "text"]
            if texts:
                return "\n".join(texts)
    return ""


def activity_names(calls: list[dict]) -> list[str]:
    names = []
    for call in calls:
        name = call.get("name", "")
        if name == "tool_call" and isinstance(call.get("arguments"), dict):
            name = call["arguments"].get("id", "")
            if isinstance(name, str) and name.startswith("mcp:mirally-activity:"):
                name = name.removeprefix("mcp:mirally-activity:")
        if isinstance(name, str) and name.startswith("mirally-activity__"):
            names.append(name)
    return names


def objects(value: object):
    if isinstance(value, dict):
        yield value
        for nested in value.values():
            yield from objects(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from objects(nested)
    elif isinstance(value, str):
        text = value.removeprefix("structuredContent:\n").strip()
        # OpenClaw wraps persisted MCP content in an external-evidence boundary;
        # decode only that boundary's JSON payload, not the surrounding prose.
        if "<<<EXTERNAL_UNTRUSTED_CONTENT " in text and "\n---\n" in text:
            text = text.split("\n---\n", 1)[1].lstrip()
        try:
            parsed, _ = json.JSONDecoder().raw_decode(text)
        except ValueError:
            # The persisted wrapper can truncate a duplicated details object
            # while retaining a complete MCP content.text JSON value. Decode
            # only complete encoded text fields; never fill in truncated JSON.
            for match in re.finditer(r'"text"\s*:\s*', text):
                try:
                    field, _ = json.JSONDecoder().raw_decode(text[match.end():])
                except ValueError:
                    continue
                if isinstance(field, str):
                    yield from objects(field)
            return
        if isinstance(parsed, (dict, list)):
            yield from objects(parsed)


def activity_responses(events: list[dict]) -> list[tuple[str, dict]]:
    calls = {call.get("id"): activity_names([call]) for call in tool_calls(events)}
    responses = []
    for result in tool_results(events):
        names = calls.get(result.get("toolCallId"), [])
        if not names or result.get("isError"):
            continue
        for value in objects(result):
            if value.get("status") == "ready" and isinstance(value.get("result"), dict):
                responses.append((names[0], value))
                break
    return responses


def history_signature(snapshot: tuple[dict | None, list[dict]]) -> str:
    entry, events = snapshot
    return digest({"session_id": entry.get("sessionId") if entry else None, "events": events})


def has_source_refs(value: dict) -> bool:
    keys = {"observation_id", "observation_ids", "source_observation_ids", "source_observation_id"}
    return any(bool(item.get(key)) for item in objects(value) for key in keys)


def claims_current_collector_state(text: str) -> bool:
    plain = text.replace("*", "").replace("_", "")
    return bool(re.search(
        r"采集器(?:现在|目前)(?:处于\s*stopped\s*状态|没有在跑|没在运行|没有在运行|未运行|已停止|正在运行)",
        plain,
    ))


def date_mentioned(text: str, timestamp: int) -> bool:
    utc = datetime.fromtimestamp(timestamp / 1000, tz=timezone.utc)
    for recorded in (utc, utc.astimezone()):
        variants = [recorded.strftime("%Y-%m-%d"), f"{recorded.year}年{recorded.month}月{recorded.day}日",
                    f"{recorded.month}月{recorded.day}日", f"{recorded.month}/{recorded.day}"]
        offset = recorded.strftime("%z")
        hour_offset = int(offset[:3])
        zone_names = [offset, offset[:3] + ":" + offset[3:], f"UTC{hour_offset:+}", f"GMT{hour_offset:+}"]
        if not hour_offset:
            zone_names.extend(["UTC", "GMT", "Z"])
        elif hour_offset == 8:
            zone_names.extend(["北京时间", "中国标准时间", "Asia/Shanghai", "GMT+08"])
        if (any(variant in text for variant in variants) and recorded.strftime("%H:%M") in text and
                any(zone in text for zone in zone_names)):
            return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-model", action="store_true", help="Enable four real configured-model API turns")
    parser.add_argument("--config", type=Path, default=ROOT / "config/mirally/openclaw/openclaw.json")
    parser.add_argument("--report", type=Path, default=ROOT / ".artifacts/activity-agent-chat/verification.json")
    parser.add_argument("--timeout", type=int, default=240, help="Per-turn Gateway deadline in seconds")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    config_path = args.config.resolve()
    config = json.loads(config_path.read_text(encoding="utf-8"))
    report_path = args.report.resolve()
    output = report_path.parent / ("run-" + uuid4().hex[:12])
    output.mkdir(parents=True, exist_ok=True)
    gateway = Gateway(config_path, output)
    session_key = "agent:main:subagent:" + str(uuid4())
    database = config_path.parent / "agents/main/agent/openclaw-agent.sqlite"
    main_before = raw_transcript(database, "agent:main:main")
    report = {"schema_version": 1, "verified_at_utc": datetime.now(timezone.utc).isoformat(),
              "model_calls_enabled": args.run_model, "session_key": session_key,
              "artifact_directory": str(output), "allow": ALLOW, "turns": [], "checks": {}}
    prepared = False
    session_id = None
    try:
        health = gateway.call("health", {}, tag="gateway-health")
        if health.get("ok") is not True:
            raise RuntimeError("Gateway is not healthy")
        patch = gateway.call("sessions.patch", {"key": session_key, "agentId": "main",
                            "label": "Activity chat acceptance " + output.name,
                            "permissionMode": "read-only", "inheritedToolPolicyVersion": 1,
                            "inheritedToolAllow": ALLOW}, tag="session-prepare")
        prepared = True
        entry, _ = raw_transcript(database, session_key)
        session_id = entry["sessionId"] if entry else patch.get("entry", {}).get("sessionId")
        if not session_id:
            raise RuntimeError("Independent test session was not persisted")
        effective = gateway.call("tools.effective", {"agentId": "main", "sessionKey": session_key}, tag="effective-tools")
        names = [tool["id"] for group in effective.get("groups", []) for tool in group.get("tools", [])]
        unsafe = [name for name in names if not any(fnmatch.fnmatchcase(name, pattern) for pattern in ALLOW)]
        mcp_names = [name for name in names if name.startswith("mirally-activity__")]
        server = config.get("mcp", {}).get("servers", {}).get("mirally-activity", {})
        configured_names = server.get("toolFilter", {}).get("include", [])
        cold = any(notice.get("id") == "mcp-not-yet-connected" and
                   "mirally-activity" in notice.get("servers", []) for notice in effective.get("notices", []))
        report["checks"].update(read_only_permission=entry.get("permissionMode") == "read-only",
                                 no_action_tools=not unsafe,
                                 activity_catalog_or_cold_notice=len(mcp_names) == 9 or cold,
                                 activity_server_configured=server.get("enabled") is True and len(configured_names) == 9,
                                 stored_allowlist_matches=entry.get("inheritedToolAllow") == ALLOW)
        report["effective_tool_names"] = names
        skills = gateway.call("skills.status", {"agentId": "main", "sessionKey": session_key}, tag="skills-status")
        eligible = [skill for skill in skills.get("skills", []) if skill.get("name") == "mirally-activity"]
        report["checks"]["activity_skill_model_visible"] = bool(eligible and eligible[0].get("eligible") and eligible[0].get("modelVisible"))
        if not all(report["checks"].values()):
            raise RuntimeError("Test session tool policy is unsuitable; real model calls were prevented")
        if args.run_model:
            sys.path.insert(0, str(ROOT))
            from mirally.runtime.activity.agent_bridge import ActivityAgentBridge
            server_args = server.get("args", [])
            activity_config = Path(server_args[server_args.index("--config") + 1]) if "--config" in server_args else None
            bridge = ActivityAgentBridge(ROOT, config_path=activity_config)
            try:
                expected_health = bridge.handle({"command": "health"})["result"]["history"]
            finally:
                bridge.close()
            report["expected_history"] = expected_health
            for index, prompt in enumerate(PROMPTS, 1):
                _, before = raw_transcript(database, session_key)
                watermark = before[-1]["seq"] if before else -1
                gateway.call("agent", {"agentId": "main", "sessionKey": session_key,
                             "message": prompt, "deliver": False, "timeout": args.timeout,
                             "idempotencyKey": str(uuid4())},
                             tag=f"turn-{index}", timeout=args.timeout, expect_final=True)
                _, all_events = raw_transcript(database, session_key)
                events = [row for row in all_events if row["seq"] > watermark]
                calls = tool_calls(events)
                responses = tool_results(events)
                final = final_text(events)
                trace = {"prompt": prompt, "events": events, "tool_calls": calls,
                         "tool_results": responses, "final": final}
                write_json(output / f"turn-{index}-trace.json", trace)
                report["turns"].append({"prompt": prompt, "tool_names": [call.get("name") for call in calls],
                                         "activity_calls": activity_names(calls), "final": final,
                                         "trace": str(output / f"turn-{index}-trace.json")})
                print(json.dumps({"turn": index, "tool_calls": len(calls), "activity_calls": len(activity_names(calls))}, ensure_ascii=False))
            turns = report["turns"]
            _, events = raw_transcript(database, session_key)
            responses = activity_responses(events)
            health_responses = [value["result"].get("history", {}) for name, value in responses if name.endswith("__activity_health")]
            recorded_keys = ("observation_count", "oldest_observed_utc_ms", "newest_observed_utc_ms")
            valid_health = any(history.get("available") is True and
                               all(history.get(key) == expected_health.get(key) for key in recorded_keys) for history in health_responses)
            retrievals = [value for name, value in responses if name.endswith(("__activity_events", "__activity_query", "__activity_summary"))]
            valid_retrieval = any((value["result"].get("events") or value["result"].get("items") or value["result"].get("snippets")) and
                                  has_source_refs(value) for value in retrievals)
            finals = "\n".join(turn["final"] for turn in turns[:3])
            report["checks"].update(activity_tool_invoked=bool(responses),
                                     history_health_matches_source=valid_health,
                                     historical_events_with_sources=valid_retrieval,
                                     recorded_date_in_final=date_mentioned(finals, expected_health["newest_observed_utc_ms"]),
                                     capability_questions_no_content_fetch=all(
                                         name.endswith("__activity_health")
                                         for turn in turns[:2] for name in turn["activity_calls"]),
                                     no_unverified_current_collector_claim=not claims_current_collector_state(finals),
                                     ordinary_question_no_activity=not turns[3]["activity_calls"],
                                     all_turns_have_final=all(turn["final"] for turn in turns))
            report["actual_activity_tools"] = [name for name, _ in responses]
    except Exception as error:
        report["error"] = str(error)
    finally:
        if prepared and session_id:
            try:
                gateway.call("sessions.patch", {"key": session_key, "agentId": "main",
                             "expectedSessionId": session_id, "archived": True}, tag="session-archive")
                archived, events = raw_transcript(database, session_key)
                write_json(output / "complete-transcript.json", events)
                report["checks"]["session_archived"] = bool(archived and archived.get("archivedAt"))
            except Exception as error:
                report["archive_error"] = str(error)
                report["checks"]["session_archived"] = False
        report["checks"]["main_history_unchanged"] = history_signature(main_before) == history_signature(raw_transcript(database, "agent:main:main"))
        report["passed"] = not report.get("error") and all(report["checks"].values())
        report["acceptance_scope"] = "Model-driven discovery" if args.run_model else "Tool policy only; no model turn"
        write_json(report_path, report)
        print(json.dumps({"passed": report["passed"], "model_calls_enabled": args.run_model,
                          "report": str(report_path), "checks": report["checks"],
                          **({"error": report["error"]} if report.get("error") else {})}, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
