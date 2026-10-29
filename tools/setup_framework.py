"""Install and build Mirally's pinned, repository-owned DSH workspace."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from mirally.framework_host import discover_node


def package_manager(root: Path, node: Path) -> list[str]:
    manifest = json.loads((root / "kernel/dsh/package.json").read_text(encoding="utf-8"))
    manager = manifest["packageManager"]
    local = root / "kernel/dsh/node_modules/pnpm/bin/pnpm.cjs"
    if local.is_file():
        installed = json.loads((local.parent.parent / "package.json").read_text(encoding="utf-8"))["version"]
        if manager == "pnpm@" + installed:
            return [str(node), str(local)]
    return [*npm_command(node), "exec", "--yes", "--package=" + manager, "--", "pnpm"]


def npm_command(node: Path) -> list[str]:
    """Use the selected Node distribution's npm, with PATH discovery fallback."""
    npm_script = node.parent / "node_modules/npm/bin/npm-cli.js"
    if npm_script.is_file():
        npm = [str(node), str(npm_script)]
    else:
        executable = shutil.which("npm.cmd" if os.name == "nt" else "npm")
        if not executable:
            raise RuntimeError("npm is required to prepare Mirally; install npm with your Node distribution")
        npm = [executable]
    return npm


def require_frontend_node(node: Path) -> str:
    """Check the selected executable before installing or building the UI."""
    preparation = "Prepare Node 24+ with npm, then select it with --node or MIRALLY_NODE and rerun tools/setup_framework.py."
    try:
        result = subprocess.run([str(node), "--version"], capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("Could not inspect the selected Node executable. " + preparation) from exc
    version = result.stdout.strip()
    try:
        major = int(version.lstrip("v").split(".", 1)[0])
    except ValueError:
        major = 0
    if result.returncode != 0 or major < 24:
        raise RuntimeError(f"Mirally's retained Control UI requires Node 24+; selected {node} reports {version or 'an unknown version'}. " + preparation)
    return version


def environment(node: Path) -> dict[str, str]:
    env = dict(os.environ)
    env["PATH"] = str(node.parent) + os.pathsep + env.get("PATH", "")
    # The dependency's own postinstall runs before our guarded workspace hook.
    env["LEFTHOOK"] = "0"
    env["npm_config_update_notifier"] = "false"
    return env


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--node")
    parser.add_argument("--skip-install", action="store_true")
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--check", action="store_true", help="inspect tools and artifacts without installation")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    node = discover_node(root, args.node)
    if args.check or not args.skip_install or not args.skip_build:
        require_frontend_node(node)
    kernel = root / "kernel/dsh"
    frontend = root / "frontend/control-ui"
    frontend_entry = frontend / "dist/index.html"
    frontend_marker = frontend / "dist/.mirally-build.json"
    if args.check:
        print("Node: " + str(node))
        print("Kernel: " + str(kernel))
        print("Built: " + str((kernel / "packages/core/agent/lib/index.js").is_file()))
        print("Schedule built: " + str((kernel / "packages/schedule/schedule/lib/index.js").is_file()))
        print("Control UI source: " + str((frontend / "ui/src/app/app-host.ts").is_file()))
        print("Control UI built: " + str(frontend_entry.is_file() and frontend_marker.is_file()))
        return 0
    env = environment(node)
    if not args.skip_install:
        subprocess.run([*package_manager(root, node), "install", "--frozen-lockfile"], cwd=kernel, env=env, check=True)
        subprocess.run([*npm_command(node), "ci", "--ignore-scripts", "--no-audit", "--no-fund"], cwd=frontend, env=env, check=True)
    if not args.skip_build:
        subprocess.run([*package_manager(root, node), "run", "build"], cwd=kernel, env=env, check=True)
        subprocess.run([*npm_command(node), "run", "build"], cwd=frontend, env=env, check=True)
    if not (kernel / "packages/core/agent/lib/index.js").is_file():
        raise RuntimeError("The kernel build did not produce its native Agent entry")
    if not (kernel / "packages/schedule/schedule/lib/index.js").is_file():
        raise RuntimeError("The kernel schedule package has not been built. Run tools/setup_framework.py without --skip-build.")
    if not frontend_entry.is_file() or not frontend_marker.is_file():
        raise RuntimeError("The original Mirally Control UI has not been built. Run tools/setup_framework.py without --skip-build; no replacement page will be selected.")
    for path in (kernel / "packages/mirally").glob("*/src/*.mjs"):
        subprocess.run([str(node), "--check", str(path)], check=True, env=env)
    print("Mirally kernel and original Control UI are ready. Run .venv/Scripts/python.exe -m mirally.framework_host.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
