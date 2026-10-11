"""Enable shared activity tools using OpenClaw's official targeted config patch."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ID = "mirally-activity"


def activity_patch(config: dict, root: Path, activity_config: Path | None = None) -> dict:
    plugins = config.get("plugins", {})
    plugin_entry = plugins.get("entries", {}).get(PLUGIN_ID, {})
    tools = config.get("tools", {})
    denied_tools = set(tools.get("deny", []))
    if (plugins.get("enabled") is False or PLUGIN_ID in plugins.get("deny", [])
            or PLUGIN_ID in denied_tools or "group:plugins" in denied_tools or "*" in denied_tools):
        raise ValueError("Activity plugin is explicitly denied by existing policy")
    paths = list(plugins.get("load", {}).get("paths", []))
    plugin_path = str((root / "integrations/openclaw/activity").resolve())
    if plugin_path not in paths:
        paths.append(plugin_path)
    entry = dict(plugin_entry)
    entry["enabled"] = True
    if activity_config is not None:
        plugin_config = dict(entry.get("config", {}))
        plugin_config.update(root=str(root.resolve()), configPath=str(activity_config.resolve()))
        entry["config"] = plugin_config
    patch = {"plugins": {"load": {"paths": paths}, "entries": {PLUGIN_ID: entry}}}
    # An absent plugin allowlist must stay absent: introducing an exclusive
    # list would disable unrelated bundled/user-installed plugins.
    if "allow" in plugins:
        allowed = list(plugins["allow"])
        if PLUGIN_ID not in allowed:
            allowed.append(PLUGIN_ID)
        patch["plugins"]["allow"] = allowed
    key = "allow" if "allow" in tools else "alsoAllow"
    allowed_tools = list(tools.get(key, []))
    if PLUGIN_ID not in allowed_tools:
        allowed_tools.append(PLUGIN_ID)
    patch["tools"] = {key: allowed_tools}
    return patch


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--activity-config", type=Path, help="Existing activity YAML, including a historical-data-only profile")
    parser.add_argument("--apply", action="store_true", help="Apply after native validation; default validates only")
    parser.add_argument("--timeout-ms", type=int, choices=range(1000, 120001), metavar="1000..120000", help="Bridge request timeout")
    args = parser.parse_args()
    state = Path(os.environ.get("OPENCLAW_STATE_DIR", ROOT / "config/openassist/openclaw"))
    config_path = Path(os.environ.get("OPENCLAW_CONFIG_PATH", state / "openclaw.json"))
    if args.activity_config is not None and not args.activity_config.is_file():
        raise SystemExit("Activity config file does not exist.")
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
        payload = activity_patch(config, ROOT, args.activity_config)
        if args.timeout_ms is not None:
            payload["plugins"]["entries"][PLUGIN_ID].setdefault("config", {})["timeoutMs"] = args.timeout_ms
        runtime = json.loads((ROOT / ".artifacts/openclaw-runtime.json").read_text(encoding="utf-8"))
        node = ROOT / runtime["node"]
        env = dict(os.environ, OPENCLAW_STATE_DIR=str(state), OPENCLAW_CONFIG_PATH=str(config_path))
        env["PATH"] = str(node.parent) + os.pathsep + env.get("PATH", "")
        for dry_run in ([True, False] if args.apply else [True]):
            command = [str(node), str(ROOT / runtime["entry"]), "config", "patch", "--stdin"]
            if dry_run:
                command.append("--dry-run")
            result = subprocess.run(command, input=json.dumps(payload), encoding="utf-8", env=env,
                                    cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                    timeout=90, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if result.returncode:
                raise ValueError("Native config patch failed")
    except (OSError, ValueError, KeyError, subprocess.TimeoutExpired):
        # Never echo native output: it can contain model/Gateway credentials.
        raise SystemExit("Activity configuration failed; check native config validation and plugin policy.") from None
    print("Shared activity tools configured." if args.apply else "Activity configuration validated; use --apply to install.")
    if args.apply:
        print("Verify activity_health through the Gateway; saved config alone does not prove live registration.")


if __name__ == "__main__":
    main()
