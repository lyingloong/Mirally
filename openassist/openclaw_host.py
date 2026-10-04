"""Host the official OpenClaw process, without implementing its client protocol.

CLI output may contain credentials and one-time pairing URLs. It stays in memory;
errors deliberately describe recovery steps instead of echoing native diagnostics.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import subprocess
import threading
import time
from typing import Callable
from urllib.parse import urlsplit


class HostError(RuntimeError):
    """A message safe to display on the local startup page."""


class OpenClawHost:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.state = self.root / "config/openassist/openclaw"
        self.config = self.state / "openclaw.json"
        self.closed = threading.Event()
        self._gateway: subprocess.Popen | None = None
        try:
            runtime = json.loads((self.root / ".artifacts/openclaw-runtime.json").read_text(encoding="utf-8"))
            self.node = (self.root / runtime["node"]).resolve()
            self.entry = (self.root / runtime["entry"]).resolve()
        except (OSError, ValueError, KeyError):
            raise HostError("请先运行 tools/setup_openclaw.py 安装 OpenClaw。") from None
        if not self.node.is_file() or not self.entry.is_file():
            raise HostError("OpenClaw 运行依赖不完整，请重新运行 tools/setup_openclaw.py。")
        if not self.config.is_file():
            raise HostError("尚未完成 OpenClaw 配置。请使用 run-openclaw.ps1 onboard 或已有配置迁移脚本。")
        self.env = dict(os.environ)
        self.env["OPENCLAW_STATE_DIR"] = str(self.state)
        self.env["OPENCLAW_CONFIG_PATH"] = str(self.config)
        self.env["PATH"] = str(self.node.parent) + os.pathsep + self.env.get("PATH", "")

    def _native_json(self, *arguments: str, entry: Path | None = None,
                     fallback: tuple[str, ...] = ()) -> dict:
        try:
            result = subprocess.run(
                [str(self.node), str(entry or self.entry), *arguments], cwd=self.root,
                env=self.env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, encoding="utf-8", timeout=25,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if result.returncode == 78 and fallback:
                return self._native_json(*fallback)
            value = json.loads(result.stdout)
            if result.returncode or not isinstance(value, dict):
                raise ValueError
            return value
        except (OSError, ValueError, subprocess.TimeoutExpired):
            raise HostError("原生连接暂不可用。请用 run-openclaw.ps1 config validate 或 gateway status 检查。") from None

    def dashboard(self) -> str:
        adapter = self.root / "integrations/openclaw/dashboard.mjs"
        value = (self._native_json(entry=adapter, fallback=("dashboard", "--json"))
                 if adapter.is_file() else self._native_json("dashboard", "--json"))
        url = value.get("browserUrl", "")
        try:
            parts = urlsplit(url)
            valid = value.get("ok") is True and parts.scheme in {"http", "https"} and bool(parts.hostname)
        except (ValueError, TypeError):
            valid = False
        if not valid:
            raise HostError("原生界面暂不可用。请检查 Gateway 与 Control UI 配置。")
        # This is the native one-time browser handoff, not a token we invent.
        return url

    @staticmethod
    def _listening(port: int) -> bool:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except OSError:
            return False

    def _start_gateway(self) -> None:
        directory = self.state / "mirally-host"
        directory.mkdir(parents=True, exist_ok=True)
        flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        try:
            with (directory / "gateway.log").open("ab") as log:
                self._gateway = subprocess.Popen(
                    [str(self.node), str(self.entry), "gateway", "run"],
                    cwd=self.root, env=self.env, stdin=subprocess.DEVNULL,
                    stdout=log, stderr=subprocess.STDOUT, creationflags=flags,
                )
            # Local diagnostic metadata only; never credentials or pairing URLs.
            (directory / "process.json").write_text(json.dumps({
                "pid": self._gateway.pid, "startedAt": time.time(),
                "executable": str(self.node),
            }, indent=2), encoding="utf-8")
        except OSError:
            raise HostError("无法启动后台 Gateway，请运行 run-openclaw.ps1 gateway run 查看诊断。") from None

    def connect(self, status: Callable[[str], None]) -> str:
        status("正在连接 OpenClaw")
        try:
            return self.dashboard()
        except HostError:
            pass
        if self.closed.is_set():
            raise HostError("窗口已关闭。")
        # Ask the native config loader: supports includes, environment references,
        # and JSON5. Do not parse or rewrite the user's configuration ourselves.
        config = self._native_json("config", "get", "gateway", "--json")
        if config.get("mode", "local") != "local":
            raise HostError("请先按原生方式连接远程 Gateway；套壳不会更改部署设置。")
        if config.get("controlUi", {}).get("enabled") is False:
            raise HostError("原生 Control UI 当前未启用。套壳保留该设置，请通过原生配置自行启用。")
        try:
            port = int(self.env.get("OPENCLAW_GATEWAY_PORT") or config.get("port", 18789))
            if not 0 < port < 65536:
                raise ValueError
        except (TypeError, ValueError):
            raise HostError("Gateway 端口无效，请运行 run-openclaw.ps1 config validate。") from None
        if not self._listening(port):
            if self._gateway is None or self._gateway.poll() is not None:
                status("正在启动后台服务")
                self._start_gateway()
        else:
            status("正在等待原生界面就绪")
        deadline = time.monotonic() + 65
        while not self.closed.is_set() and time.monotonic() < deadline:
            if self._listening(port):
                status("正在连接原生界面")
                try:
                    return self.dashboard()
                except HostError:
                    pass
            if self._gateway is not None and self._gateway.poll() not in {None, 0}:
                break
            self.closed.wait(0.8)
        raise HostError("Gateway 未能就绪。请运行 run-openclaw.ps1 gateway status 检查；本地启动日志位于私有状态目录 mirally-host/gateway.log。")

    def close(self) -> None:
        # The desktop is a view; closing it must not kill work owned by Gateway.
        self.closed.set()
