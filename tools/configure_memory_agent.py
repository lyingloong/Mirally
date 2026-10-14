"""Register additive Mirally memory integration through native config patches."""

from __future__ import annotations

import argparse
from fnmatch import fnmatchcase
import hashlib
import json
import os
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ID = "mirally-memory"
TOOLS = ["memory_health", "memory_recall", "memory_read", "memory_propose", "memory_forget"]


def memory_revision(root: Path) -> str:
    revision = hashlib.sha256()
    for directory, pattern in (("openassist/core/memory", "*.py"),
                               ("openassist/runtime/memory", "*.py"),
                               ("integrations/openclaw/memory", "*.mjs")):
        for source in sorted((root / directory).rglob(pattern)):
            revision.update(source.relative_to(root).as_posix().encode("utf-8"))
            revision.update(b"\0")
            revision.update(source.read_bytes())
    return revision.hexdigest()[:16]


def memory_patch(config: dict, root: Path, memory_config: Path | None = None) -> dict:
    """Preserve authored agents, memory slot, credentials and explicit denials."""
    root = root.resolve()
    profile = (memory_config or root / "config/openassist/memory.yaml").resolve()
    plugins = config.get("plugins", {})
    tools = config.get("tools", {})
    denied = tools.get("deny", [])
    if (plugins.get("enabled") is False or PLUGIN_ID in plugins.get("deny", [])
            or any(name in denied for name in ("*", "group:plugins", "bundle-mcp", PLUGIN_ID))
            or all(any(fnmatchcase(f"{PLUGIN_ID}__{name}", pattern) for pattern in denied)
                   for name in TOOLS)):
        raise ValueError("Mirally memory is explicitly denied by existing policy")
    interpreter = root / (".venv/Scripts/python.exe" if os.name == "nt" else ".venv/bin/python")
    if not interpreter.is_file():
        raise ValueError("Repository Python environment is missing")
    paths = list(plugins.get("load", {}).get("paths", []))
    plugin_path = str(root / "integrations/openclaw/memory")
    if plugin_path not in paths:
        paths.append(plugin_path)
    existing = plugins.get("entries", {}).get(PLUGIN_ID, {}).get("config", {})
    plugin_config = {**existing, "root": str(root), "configPath": str(profile)}
    hooks = dict(plugins.get("entries", {}).get(PLUGIN_ID, {}).get("hooks", {}))
    # Native external plugins require an explicit conversation hook grant.
    # This narrowly authorizes the requested current-message recall; authored
    # false settings remain effective, including prompt-injection denials.
    hooks.setdefault("allowConversationAccess", True)
    patch = {"plugins": {"load": {"paths": paths},
                         "entries": {PLUGIN_ID: {"enabled": True, "config": plugin_config,
                                                  "hooks": hooks}}}}
    if "allow" in plugins:
        allowed = list(plugins["allow"])
        if PLUGIN_ID not in allowed:
            allowed.append(PLUGIN_ID)
        patch["plugins"]["allow"] = allowed
    key = "allow" if "allow" in tools else "alsoAllow"
    tool_allow = list(tools.get(key, []))
    if f"{PLUGIN_ID}__*" not in tool_allow:
        tool_allow.append(f"{PLUGIN_ID}__*")
    patch["tools"] = {key: tool_allow}
    extra_dirs = list(config.get("skills", {}).get("load", {}).get("extraDirs", []))
    skill_path = str(root / "integrations/openclaw/skills")
    if skill_path not in extra_dirs:
        extra_dirs.append(skill_path)
    patch["skills"] = {"load": {"extraDirs": extra_dirs}}
    patch["mcp"] = {"servers": {PLUGIN_ID: {
        "enabled": True, "command": str(interpreter),
        "args": ["-m", "openassist.runtime.memory.mcp_server", "--root", str(root), "--config", str(profile)],
        "cwd": str(root), "connectionTimeoutMs": 15000, "requestTimeoutMs": 30000,
        "supportsParallelToolCalls": False, "toolFilter": {"include": TOOLS},
        "env": {"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8",
                "OPENASSIST_MEMORY_CODE_REVISION": memory_revision(root)},
    }}}
    return patch


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--memory-config", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.memory_config is not None and not args.memory_config.is_file():
        raise SystemExit("Memory profile does not exist")
    state = Path(os.environ.get("OPENCLAW_STATE_DIR", ROOT / "config/openassist/openclaw"))
    config_path = Path(os.environ.get("OPENCLAW_CONFIG_PATH", state / "openclaw.json"))
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
        patch = memory_patch(config, ROOT, args.memory_config)
        runtime = json.loads((ROOT / ".artifacts/openclaw-runtime.json").read_text(encoding="utf-8"))
        node = ROOT / runtime["node"]
        env = dict(os.environ, OPENCLAW_STATE_DIR=str(state), OPENCLAW_CONFIG_PATH=str(config_path))
        env["PATH"] = str(node.parent) + os.pathsep + env.get("PATH", "")
        for dry_run in ([True, False] if args.apply else [True]):
            command = [str(node), str(ROOT / runtime["entry"]), "config", "patch", "--stdin"]
            if dry_run:
                command.append("--dry-run")
            completed = subprocess.run(command, input=json.dumps(patch), encoding="utf-8", env=env,
                                       cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                       timeout=90, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if completed.returncode:
                raise ValueError("Native memory config validation failed")
    except (OSError, ValueError, KeyError, subprocess.TimeoutExpired):
        raise SystemExit("Memory configuration failed; check native plugin and tool policy") from None
    print("Mirally memory configured." if args.apply else "Memory configuration validated; use --apply to install.")
    if args.apply:
        print("Gateway services own the worker lifetime. A new plugin may require an idle Gateway restart.")
        print("Collection stays separately configured. Semantic processing uses the host model only when enabled in the memory profile.")


if __name__ == "__main__":
    main()
