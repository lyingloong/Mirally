"""Verify existing activity evidence and optional loopback Gateway tools.

Never starts collection, desktop automation or a model request. Full content
is written only to an explicitly requested local export; the report and stdout
contain aggregate diagnostics, not titles, URLs, text or Gateway credentials.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from openassist.core.activity.knowledge.repository import deterministic_id
from openassist.runtime.activity.agent_bridge import ActivityAgentBridge


def source_fingerprint(database: Path) -> dict[str, str | None]:
    return {suffix or "database": hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
            for suffix in ("", "-wal") for path in (Path(str(database) + suffix),)}


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def gateway_caller(config_path: Path):
    config = json.loads(config_path.read_text(encoding="utf-8"))
    gateway = config.get("gateway", {})
    auth = gateway.get("auth", {})
    mode = auth.get("mode", "token")
    headers = {"Content-Type": "application/json"}
    if mode in {"token", "password"}:
        secret = os.environ.get(f"OPENCLAW_GATEWAY_{mode.upper()}") or auth.get(mode)
        if not isinstance(secret, str) or not secret:
            raise ValueError("Gateway credential unavailable")
        headers["Authorization"] = "Bearer " + secret
    elif mode != "none":
        raise ValueError("Verifier requires direct loopback Gateway authentication")
    url = f"http://127.0.0.1:{int(gateway.get('port', 18789))}/tools/invoke"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def call(tool: str, args: dict) -> dict:
        payload = {"tool": tool, "args": args, "sessionKey": "main"}
        request = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
        try:
            with opener.open(request, timeout=45) as response:
                body = json.load(response)
        except urllib.error.HTTPError as error:
            raise RuntimeError(f"Gateway HTTP {error.code}") from None
        except urllib.error.URLError:
            raise RuntimeError("Loopback Gateway unavailable") from None
        if not body.get("ok") or body.get("result", {}).get("isError"):
            raise RuntimeError("Gateway tool invocation failed")
        result = body.get("result", {})
        details = result.get("details")
        if isinstance(details, dict):
            return details
        for content in result.get("content", []):
            if content.get("type") == "text":
                return json.loads(content["text"])
        raise RuntimeError("Gateway tool result has no structured response")

    return call


def summarize_response(response: dict) -> dict:
    value = response.get("result")
    value = value if isinstance(value, dict) else {}
    return {"status": response.get("status"), "data_status": response.get("data_status"),
            "item_count": len(value.get("items", [])), "evidence_count": len(value.get("evidence", [])),
            "truncated": value.get("truncated", False), "index_watermark": value.get("index_watermark")}


def verify(args: argparse.Namespace) -> dict:
    bridge = ActivityAgentBridge(ROOT, args.activity_config)
    before = source_fingerprint(bridge.config.database_path)
    try:
        response = bridge.handle({"command": "query", "args": {"max_items": 200, "token_budget": 16000}})
        if response.get("status") != "ready":
            raise RuntimeError("Historical activity source is not ready")
        service = bridge._service
        assert service is not None
        health = service.health()
        if health["organizer_lag"]:
            raise RuntimeError("Index catch-up budget exhausted; run organizer process first")
        items = []
        cursor = None
        while True:
            page = service.knowledge.query_items(limit=500, after_item_id=cursor)
            items.extend(page)
            if len(page) < 500:
                break
            cursor = page[-1].item_id
        if not items:
            raise RuntimeError("Historical dataset has no projected items")
        episodes = [service.knowledge.get_episode(episode_id)
                    for episode_id in dict.fromkeys(item.episode_id for item in items if item.episode_id)]
        episode_ids = {episode.episode_id for episode in episodes if episode is not None}
        item_ids = {item.item_id for item in items}
        references = {oid for item in items for oid in item.source_observation_ids}
        missing_sources = sum(service.source.get(oid) is None for oid in references)

        # Prove the bounded query surface can traverse the whole sample rather
        # than mistaking one budget-limited response for the database total.
        seen = set()
        cursor = None
        pages = 0
        while True:
            result = bridge.handle({"command": "query", "sync": False,
                                    "args": {"max_items": 30, "token_budget": 16000, "after_item_id": cursor}})["result"]
            current = {item["item_id"] for item in result["items"]}
            if current & seen:
                raise RuntimeError("Query pagination repeated items")
            seen.update(current)
            pages += 1
            cursor = result["next_cursor"]
            if cursor is None:
                if result["truncated"]:
                    raise RuntimeError("Query budget prevents pagination; increase the budget")
                break
            if not current or pages > len(items) + 1:
                raise RuntimeError("Query pagination did not advance")

        first = items[0]
        probes = {
            "activity_health": {},
            "activity_recent": {"seconds": 300},
            "activity_search": {"query": first.app_id or first.title or "会议", "max_items": 5},
            "activity_around": {"at_utc_ms": first.start_utc_ms, "window_seconds": 60},
            "activity_summary": {"seconds": 300},
            "activity_query": {"max_items": 5},
            "activity_episode": {"episode_id": first.episode_id},
            "activity_evidence": {"evidence_ids": [deterministic_id("evidence", first.source_observation_ids[0], first.projection_version)]},
        }
        local = {tool: summarize_response(bridge.handle({"command": tool.removeprefix("activity_"), "args": params}))
                 for tool, params in probes.items()}
        gateway_results = {}
        if args.gateway:
            call = gateway_caller(args.gateway_config)
            for tool, params in probes.items():
                result = call(tool, params)
                gateway_results[tool] = summarize_response(result)
                if result.get("status") != "ready":
                    raise RuntimeError("Gateway activity tool is not ready")
                if gateway_results[tool] != local[tool]:
                    raise RuntimeError("Gateway response differs from the current local activity bridge")
                if tool == "activity_health":
                    actual = result.get("result", {})
                    if actual.get("knowledge", {}).get("item_count") != len(items) or actual.get("organizer_lag") != 0:
                        raise RuntimeError("Gateway is not reading the expected indexed history")

        checks = {
            "all_items_paginated": seen == item_ids,
            "all_items_have_source_references": all(item.source_observation_ids for item in items),
            "source_references_resolve": missing_sources == 0,
            "item_episode_links_resolve": all(item.episode_id in episode_ids for item in items),
            "episode_member_links_resolve": all(episode and set(episode.item_ids) <= item_ids for episode in episodes),
            "source_database_unchanged": before == source_fingerprint(bridge.config.database_path),
        }
        report = {"verified_at_utc": datetime.now(timezone.utc).isoformat(), "passed": all(checks.values()),
                  "checks": checks, "health": health, "pagination": {"pages": pages, "unique_items": len(seen)},
                  "gap_counts": dict(Counter(gap for item in items for gap in item.missing_reasons)),
                  "local_tools": local, "gateway_tools": gateway_results,
                  "limits": ["capture quality is not semantic accuracy", "episode summaries are deterministic counts",
                             "direct UFO native loop does not consume shared tools", "no model or desktop task exercised"]}
        if args.export is not None:
            write_json(args.export, {"schema_version": 1, "health": health,
                                     "items": [item.to_dict() for item in items],
                                     "episodes": [episode.to_dict() for episode in episodes if episode]})
        return report
    finally:
        bridge.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--activity-config", type=Path, required=True)
    parser.add_argument("--report", type=Path, default=ROOT / ".artifacts/activity-agent-access/verification.json")
    parser.add_argument("--export", type=Path, help="Save full local Item/Episode content for inspection")
    parser.add_argument("--gateway", action="store_true", help="Invoke read-only tools through the running loopback Gateway")
    parser.add_argument("--gateway-config", type=Path, default=ROOT / "config/openassist/openclaw/openclaw.json")
    args = parser.parse_args()
    try:
        report = verify(args)
    except Exception as error:
        # Only known verifier errors are suitable for stdout. Other exceptions
        # can embed captured text or private configuration, so report their type.
        message = str(error) if type(error) is RuntimeError else type(error).__name__
        print(f"Activity verification failed: {message}")
        return 1
    write_json(args.report, report)
    print(json.dumps({"passed": report["passed"], "items": report["health"]["knowledge"]["item_count"],
                      "episodes": report["health"]["knowledge"]["episode_count"],
                      "gateway_tools_verified": len(report["gateway_tools"]), "report": str(args.report)}, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
