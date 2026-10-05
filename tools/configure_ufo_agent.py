"""Add a UFO agent using OpenClaw's native config patch command."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def agent_patch(config, root, model, agent_id="ufo"):
    if not model or "/" not in model:
        raise ValueError("Supply a configured provider/model reference")
    agents = config.get("agents", {})
    entries = agents.get("entries", {})
    old = entries.get(agent_id) or next((item for item in agents.get("list", [])
                                       if item.get("id") == agent_id), None)
    if old and old.get("models", {}).get(model, {}).get("agentRuntime", {}).get("id") != "mirally-ufo":
        raise ValueError("The agent id already belongs to another configuration; choose --agent-id")
    plugins = config.get("plugins", {})
    paths = list(plugins.get("load", {}).get("paths", []))
    plugin = str((root / "integrations/openclaw/ufo").resolve())
    if plugin not in paths:
        paths.append(plugin)
    result = {"plugins": {"load": {"paths": paths},
                          "entries": {"mirally-ufo": {"enabled": True}}},
              "agents": {"entries": {agent_id: {
                  "name": "桌面助手（UFO）", "model": model,
                  "models": {model: {"agentRuntime": {"id": "mirally-ufo"}}},
              }}}}
    if "allow" in plugins:
        allowed = list(plugins["allow"])
        if "mirally-ufo" not in allowed:
            allowed.append("mirally-ufo")
        result["plugins"]["allow"] = allowed
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True, help="Existing OpenClaw provider/model reference")
    parser.add_argument("--agent-id", default="ufo")
    parser.add_argument("--apply", action="store_true", help="Apply after native validation; default is dry-run")
    args = parser.parse_args()
    state = Path(os.environ.get("OPENCLAW_STATE_DIR", ROOT / "config/openassist/openclaw"))
    config_path = Path(os.environ.get("OPENCLAW_CONFIG_PATH", state / "openclaw.json"))
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
        payload = agent_patch(config, ROOT, args.model, args.agent_id)
        runtime = json.loads((ROOT / ".artifacts/openclaw-runtime.json").read_text(encoding="utf-8"))
        node, entry = ROOT / runtime["node"], ROOT / runtime["entry"]
        env = dict(os.environ, OPENCLAW_STATE_DIR=str(state), OPENCLAW_CONFIG_PATH=str(config_path))
        env["PATH"] = str(node.parent) + os.pathsep + env.get("PATH", "")
        command = [str(node), str(entry), "config", "patch", "--stdin"]
        if not args.apply:
            command.append("--dry-run")
        completed = subprocess.run(command, input=json.dumps(payload), encoding="utf-8", env=env,
                                   cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   timeout=90, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if completed.returncode:
            raise ValueError("Native configuration validation failed; use run-openclaw.ps1 config validate")
    except (OSError, ValueError, KeyError, subprocess.TimeoutExpired) as error:
        # Configuration and native diagnostics may contain private credentials.
        raise SystemExit("UFO agent configuration failed. Check native configuration and the chosen model/id.") from None
    print("UFO agent configuration applied." if args.apply else "UFO agent configuration validated; use --apply to install.")
    if args.apply:
        print("Restart the Gateway when idle, then select the UFO agent in the existing UI.")


if __name__ == "__main__":
    main()
