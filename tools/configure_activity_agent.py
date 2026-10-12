"""Configure the shared Activity knowledge MCP server in OpenClaw."""
from __future__ import annotations

import argparse
from fnmatch import fnmatchcase
import hashlib
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ID = "mirally-activity"
MCP_SERVER_ID = "mirally-activity"
ACTIVITY_TOOL_NAMES = [
    "activity_health",
    "activity_recent",
    "activity_search",
    "activity_around",
    "activity_summary",
    "activity_query",
    "activity_events",
    "activity_episode",
    "activity_evidence",
]


def _python_command(root: Path) -> str:
    """Return the repository virtualenv interpreter used by the MCP child."""

    if os.name == "nt":
        candidate = root / ".venv" / "Scripts" / "python.exe"
    else:
        candidate = root / ".venv" / "bin" / "python"
    if not candidate.is_file():
        raise ValueError("Repository Python environment is missing")
    return str(candidate.resolve())


def _mcp_code_revision(root: Path) -> str:
    """Let native config reload retire children after product code updates."""

    revision = hashlib.sha256()
    for package in ("openassist/core/activity", "openassist/runtime/activity"):
        for source in sorted((root / package).rglob("*.py")):
            revision.update(source.relative_to(root).as_posix().encode("utf-8"))
            revision.update(b"\0")
            revision.update(source.read_bytes())
    return revision.hexdigest()[:16]


def activity_mcp_server(root: Path, activity_config: Path | None = None, timeout_ms: int = 30000) -> dict:
    root = root.resolve()
    args = ["-m", "openassist.runtime.activity.mcp_server", "--root", str(root)]
    if activity_config is not None:
        args.extend(["--config", str(activity_config.resolve())])
    return {
        "enabled": True,
        "command": _python_command(root),
        "args": args,
        "cwd": str(root),
        "env": {"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8",
                "OPENASSIST_ACTIVITY_MCP_CODE_REVISION": _mcp_code_revision(root)},
        "connectionTimeoutMs": min(timeout_ms, 15000),
        "requestTimeoutMs": timeout_ms,
        "supportsParallelToolCalls": False,
        "toolFilter": {"include": ACTIVITY_TOOL_NAMES},
    }


def _legacy_plugin_patch(config: dict, root: Path, activity_config: Path | None = None) -> dict:
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
    if MCP_SERVER_ID in config.get("mcp", {}).get("servers", {}):
        patch["mcp"] = {"servers": {MCP_SERVER_ID: {"enabled": False}}}
    return patch


def activity_patch(
    config: dict,
    root: Path,
    activity_config: Path | None = None,
    *,
    legacy_plugin: bool = False,
    timeout_ms: int = 30000,
) -> dict:
    """Return a non-mutating patch for the canonical local MCP transport.

    ``legacy_plugin`` remains available for older OpenClaw installations that
    do not have managed MCP support.  New configurations use MCP so the same
    Activity contract can be consumed by any MCP-capable OpenClaw runtime.
    """

    if legacy_plugin:
        return _legacy_plugin_patch(config, root, activity_config)
    tools = config.get("tools", {})
    denied = tools.get("deny", [])
    if (
        PLUGIN_ID in config.get("plugins", {}).get("deny", [])
        or any(name in denied for name in (PLUGIN_ID, "bundle-mcp", "group:plugins", "*"))
        or all(any(fnmatchcase(f"{MCP_SERVER_ID}__{name}", pattern) for pattern in denied)
               for name in ACTIVITY_TOOL_NAMES)
    ):
        raise ValueError("Activity MCP tools are explicitly denied by existing policy")
    patch = {"mcp": {"servers": {MCP_SERVER_ID: activity_mcp_server(root, activity_config, timeout_ms)}}}
    key = "allow" if "allow" in tools else "alsoAllow"
    allowed_tools = list(tools.get(key, []))
    server_tools = f"{MCP_SERVER_ID}__*"
    if server_tools not in allowed_tools:
        allowed_tools.append(server_tools)
    patch["tools"] = {key: allowed_tools}
    # Migrate an existing installation without registering both surfaces.  The
    # plugin files and user policies remain intact so --legacy-plugin can still
    # be used explicitly on an older OpenClaw release.
    if PLUGIN_ID in config.get("plugins", {}).get("entries", {}):
        patch.setdefault("plugins", {}).setdefault("entries", {})[PLUGIN_ID] = {"enabled": False}
    # A native skill advertises the capability even when OpenClaw's compact
    # tool directory hides MCP schemas. Keep user-authored skills and policy.
    skill_roots = list(config.get("skills", {}).get("load", {}).get("extraDirs", []))
    skill_root = str((root / "integrations/openclaw/skills").resolve())
    if skill_root not in skill_roots:
        skill_roots.append(skill_root)
    patch["skills"] = {"load": {"extraDirs": skill_roots}}
    return patch


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--activity-config", type=Path, help="Existing activity YAML, including a historical-data-only profile")
    parser.add_argument("--legacy-plugin", action="store_true", help="Use the pre-MCP native plugin compatibility path")
    parser.add_argument("--apply", action="store_true", help="Apply after native validation; default validates only")
    parser.add_argument("--timeout-ms", type=int, choices=range(1000, 120001), metavar="1000..120000", help="Bridge request timeout")
    args = parser.parse_args()
    state = Path(os.environ.get("OPENCLAW_STATE_DIR", ROOT / "config/openassist/openclaw"))
    config_path = Path(os.environ.get("OPENCLAW_CONFIG_PATH", state / "openclaw.json"))
    if args.activity_config is not None and not args.activity_config.is_file():
        raise SystemExit("Activity config file does not exist.")
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
        payload = activity_patch(
            config, ROOT, args.activity_config, legacy_plugin=args.legacy_plugin,
            timeout_ms=args.timeout_ms or 30000,
        )
        if args.timeout_ms is not None and args.legacy_plugin:
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
        print("Probe the saved server with openclaw mcp doctor mirally-activity --probe, then verify calls with --mcp."
              if not args.legacy_plugin else "Verify activity_health through the Gateway legacy plugin.")
        if not args.legacy_plugin:
            print("Check the native mirally-activity skill is eligible; per-agent skill allowlists still apply.")


if __name__ == "__main__":
    main()
