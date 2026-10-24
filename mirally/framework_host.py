"""Portable supervision and desktop entry for the Mirally-owned DSH kernel."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import urllib.request
import webbrowser


def product_home(explicit: str | Path | None = None) -> Path:
    value = explicit or os.environ.get("MIRALLY_HOME")
    if value:
        return Path(value).expanduser().resolve()
    local = os.environ.get("LOCALAPPDATA")
    return (Path(local) / "Mirally/framework" if local else Path.home() / ".local/share/mirally/framework").resolve()


def discover_node(root: Path, explicit: str | None = None) -> Path:
    candidates = [explicit, os.environ.get("MIRALLY_NODE"), shutil.which("node")]
    candidates.extend(str(path) for path in sorted((root / ".artifacts/node").glob("*/node.exe"), reverse=True))
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate).expanduser()
        if not path.is_file():
            if candidate in (explicit, os.environ.get("MIRALLY_NODE")):
                raise RuntimeError("Configured Node executable does not exist")
            continue
        result = subprocess.run([str(path), "--version"], capture_output=True, text=True, timeout=15)
        try:
            parts = tuple(int(part) for part in result.stdout.strip().lstrip("v").split(".")[:3])
        except ValueError:
            continue
        if result.returncode == 0 and ((parts[0] == 22 and parts >= (22, 19, 0)) or parts[0] >= 24):
            return path.resolve()
    raise RuntimeError("Prepare Node 22.19+ (22.x) or Node 24+, or set MIRALLY_NODE. Then run tools/setup_framework.py.")


class FrameworkHost:
    def __init__(self, root: Path, home: Path | None = None, *, node: str | None = None, test_mode: bool = False, port: int = 0) -> None:
        self.root = Path(root).resolve()
        self.home = product_home(home)
        self.node_option, self.test_mode, self.port = node, test_mode, port
        self.process: subprocess.Popen | None = None
        self.log = None
        self.runtime: dict = {}
        self.owned = False

    def _existing(self) -> dict | None:
        path = self.home / "runtime.json"
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not str(data.get("url", "")).startswith("http://127.0.0.1:"):
                return None
            request = urllib.request.Request(data["url"] + "/api/health", headers={"Authorization": "Bearer " + data["token"]})
            with urllib.request.urlopen(request, timeout=2) as response:
                if json.loads(response.read()).get("ready") and data.get("mode") == ("test" if self.test_mode else "normal"):
                    return data
        except (OSError, ValueError, KeyError):
            return None
        return None

    def start(self, *, timeout: float = 90) -> dict:
        existing = self._existing()
        if existing:
            self.runtime = existing
            return existing
        kernel = self.root / "kernel/dsh"
        required = kernel / "packages/core/agent/lib/index.js"
        if not required.is_file():
            raise RuntimeError("Mirally kernel is not built. Run .venv/Scripts/python.exe tools/setup_framework.py first.")
        if not (self.root / "frontend/control-ui/dist/.mirally-build.json").is_file():
            raise RuntimeError("Mirally's retained frontend is not built. Run .venv/Scripts/python.exe tools/setup_framework.py first.")
        node = discover_node(self.root, self.node_option)
        python = self.root / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
        if not python.is_file():
            raise RuntimeError("Create the repository's .venv and install requirements-framework.txt first.")
        self.home.mkdir(parents=True, exist_ok=True)
        logs = self.home / "logs"
        logs.mkdir(exist_ok=True)
        self.log = (logs / "host.log").open("ab")
        command = [str(node), str(kernel / "packages/mirally/mirally-product/start.mjs"), "--root", str(self.root), "--home", str(self.home), "--python", str(python), "--port", str(self.port)]
        if self.test_mode:
            command.append("--test-mode")
        env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
        self.process = subprocess.Popen(command, cwd=self.root, env=env, stdin=subprocess.PIPE, stdout=self.log, stderr=subprocess.STDOUT, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.owned = True
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                self.close()
                raise RuntimeError(f"Mirally kernel exited during startup. Inspect {logs / 'host.log'}")
            runtime = self._existing()
            if runtime and runtime.get("pid") == self.process.pid:
                self.runtime = runtime
                return runtime
            time.sleep(0.15)
        self.close()
        raise RuntimeError(f"Mirally startup timed out. Inspect {logs / 'host.log'}")

    @property
    def url(self) -> str:
        return self.runtime["url"] + "/#token=" + self.runtime["token"]

    def close(self) -> None:
        if self.process and self.owned:
            if self.process.poll() is None:
                if self.process.stdin:
                    self.process.stdin.close()
                try:
                    self.process.wait(timeout=90)
                except subprocess.TimeoutExpired:
                    self.process.terminate()
                    self.process.wait(timeout=5)
            self.process = None
        if self.log:
            self.log.close()
            self.log = None


def desktop(host: FrameworkHost) -> None:
    from mirally.framework_shell import FrameworkShell
    FrameworkShell(host).run()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Mirally local personal agent")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--home", type=Path)
    parser.add_argument("--node")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--browser", action="store_true")
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--test-mode", action="store_true", help="use an isolated deterministic model; never a production fallback")
    args = parser.parse_args(argv)
    if args.test_mode and args.home is None:
        parser.error("--test-mode requires an explicit isolated --home")
    host = FrameworkHost(args.root, args.home, node=args.node, test_mode=args.test_mode, port=args.port)
    try:
        if args.headless or args.browser:
            host.start()
            if args.browser:
                webbrowser.open(host.url)
            print("Mirally is running at " + host.runtime["url"], flush=True)
            print("Private runtime credentials: " + str(host.home / "runtime.json"), flush=True)
            while host.process is None or host.process.poll() is None:
                time.sleep(0.5)
        else:
            desktop(host)
    except KeyboardInterrupt:
        pass
    finally:
        host.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
