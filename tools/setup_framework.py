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
    npm_script = node.parent / "node_modules/npm/bin/npm-cli.js"
    if npm_script.is_file():
        npm = [str(node), str(npm_script)]
    else:
        executable = shutil.which("npm.cmd" if os.name == "nt" else "npm")
        if not executable:
            raise RuntimeError("npm is required to bootstrap " + manager + "; install npm with your Node distribution")
        npm = [executable]
    return [*npm, "exec", "--yes", "--package=" + manager, "--", "pnpm"]


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
    kernel = root / "kernel/dsh"
    if args.check:
        print("Node: " + str(node))
        print("Kernel: " + str(kernel))
        print("Built: " + str((kernel / "packages/core/agent/lib/index.js").is_file()))
        print("UI: " + str((root / "mirally/framework_ui/index.html").is_file()))
        return 0
    env = environment(node)
    if not args.skip_install:
        subprocess.run([*package_manager(root, node), "install", "--frozen-lockfile"], cwd=kernel, env=env, check=True)
    if not args.skip_build:
        subprocess.run([*package_manager(root, node), "run", "build"], cwd=kernel, env=env, check=True)
    if not (kernel / "packages/core/agent/lib/index.js").is_file():
        raise RuntimeError("The kernel build did not produce its native Agent entry")
    for path in (kernel / "packages/mirally").glob("*/src/*.mjs"):
        subprocess.run([str(node), "--check", str(path)], check=True, env=env)
    print("Mirally kernel is ready. Run .venv/Scripts/python.exe -m mirally.framework_host.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
