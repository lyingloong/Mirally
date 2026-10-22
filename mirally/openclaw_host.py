"""Host the official OpenClaw process, without implementing its client protocol.

CLI output may contain credentials and one-time pairing URLs. It stays in memory;
errors deliberately describe recovery steps instead of echoing native diagnostics.
"""
from __future__ import annotations

import json
import os
import queue
from pathlib import Path
import socket
import subprocess
import threading
import time
from typing import Callable
from urllib.parse import urlsplit

import psutil


class HostError(RuntimeError):
    """A message safe to display on the local startup page."""


class _SettingsCommandUnavailable(ValueError):
    """The adapter answered, but the official read could not complete."""


class _SettingsReader:
    """One product-owned JSONL adapter; native code owns config/auth/protocol."""

    def __init__(self, host):
        self.process = subprocess.Popen(
            [str(host.node), str(host.root / "integrations/openclaw/settings-read.mjs")],
            cwd=host.root, env=host.env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, encoding="utf-8", bufsize=1,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self.replies = queue.Queue()
        self.sequence = 0
        self.reader = threading.Thread(target=self._read, name="mirally-settings-reader", daemon=True)
        self.reader.start()

    def _read(self):
        try:
            while True:
                line = self.process.stdout.readline(4_000_001)
                if not line or len(line) > 4_000_000:
                    break
                self.replies.put(json.loads(line))
        except (OSError, ValueError):
            pass
        finally:
            self.replies.put(None)

    def request(self, action):
        self.sequence += 1
        self.process.stdin.write(json.dumps({"id": self.sequence, "action": action}) + "\n")
        self.process.stdin.flush()
        result = self.replies.get(timeout=25)
        if not isinstance(result, dict) or result.get("id") != self.sequence:
            raise ValueError("Native settings read unavailable")
        if result.get("ok") is False:
            raise _SettingsCommandUnavailable("Native settings read unavailable")
        if result.get("ok") is not True or not isinstance(result.get("result"), dict):
            raise ValueError("Native settings read unavailable")
        return result["result"]

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=2)
        for stream in (self.process.stdin, self.process.stdout):
            if stream is not None:
                stream.close()
        self.reader.join(timeout=1)


class OpenClawHost:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.state = self.root / "config/mirally/openclaw"
        self.config = self.state / "openclaw.json"
        self.closed = threading.Event()
        self._gateway: subprocess.Popen | None = None
        self._settings_reader = None
        self._settings_read_lock = threading.RLock()
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

    def _settings_read(self, action: str) -> dict:
        if action not in {"profiles", "memory-status"}:
            raise HostError("不支持的设置读取操作。")
        with self._settings_read_lock:
            if self.closed.is_set():
                raise HostError("Mirally 窗口已关闭。")
            try:
                if self._settings_reader is None:
                    self._settings_reader = _SettingsReader(self)
                return self._settings_reader.request(action)
            except _SettingsCommandUnavailable:
                # A disconnected Gateway or invalid configuration does not
                # require restarting the healthy read-only adapter process.
                raise HostError("暂时无法读取原生设置；请检查本机配置和 Gateway 状态。") from None
            except (OSError, ValueError, queue.Empty, subprocess.SubprocessError):
                reader, self._settings_reader = self._settings_reader, None
                if reader is not None:
                    reader.close()
                raise HostError("暂时无法读取原生设置；请检查本机配置和 Gateway 状态。") from None

    def activity_profiles(self) -> dict:
        """Fresh official config read; includes/env/validation stay native."""
        return self._settings_read("profiles")

    def memory_runtime_status(self) -> dict:
        """Official read-only RPC; never starts or reloads the Gateway."""
        return self._settings_read("memory-status")

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
        # Keep the background Gateway independent from the desktop host while
        # preventing Node's helper processes from opening transient consoles.
        # CREATE_NO_WINDOW is the relevant Windows flag for console-subsystem
        # descendants such as the SQLite read-only worker.
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
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

    def _restart_target(self, port: int):
        """Identify this project's foreground/background Gateway, never by PID alone."""
        try:
            listeners = {connection.pid for connection in psutil.net_connections(kind="tcp")
                         if connection.status == psutil.CONN_LISTEN and connection.laddr.port == port}
            if len(listeners) != 1 or None in listeners:
                raise ValueError
            process = psutil.Process(listeners.pop())
            command = process.cmdline()
            environment = process.environ()
            # Verify the executable, exact CLI entry/mode and configuration.
            # Other OpenClaw installs or unrelated listeners are not ours.
            if (Path(process.exe()).resolve() != self.node
                    or len(command) != 4 or Path(command[1]).resolve() != self.entry
                    or command[2:] != ["gateway", "run"]
                    or Path(environment.get("OPENCLAW_STATE_DIR", "")).resolve() != self.state
                    or Path(environment.get("OPENCLAW_CONFIG_PATH", "")).resolve() != self.config):
                raise ValueError
            # Cache creation identity before any termination (psutil checks it
            # again for mutating operations to guard against PID reuse).
            process.create_time()
            return process
        except (OSError, ValueError, psutil.Error):
            raise HostError("无法确认当前 Gateway 属于本项目，未执行重启。请在原启动终端或服务管理器中重启。") from None

    def _stop_gateway(self, process) -> None:
        """Explicit restart owns the Gateway's workers as well as its listener."""
        try:
            children = process.children(recursive=True)
            # Stop the parent first, so it cannot start more workers while its
            # children are being collected. Windows terminate ends the process;
            # callers must warn that in-flight work/window cleanup can be lost.
            targets = [process, *reversed(children)]
            for target in targets:
                try:
                    target.terminate()
                except psutil.NoSuchProcess:
                    pass
            _, alive = psutil.wait_procs(targets, timeout=3)
            for target in alive:
                try:
                    target.kill()
                except psutil.NoSuchProcess:
                    pass
            _, alive = psutil.wait_procs(alive, timeout=3)
            if alive:
                raise HostError("Gateway 未能停止，未启动第二个实例。请通过原启动终端检查。")
            if self._gateway is not None:
                self._gateway.poll()  # Retire our own Popen handle after external termination.
            self._gateway = None
        except psutil.Error:
            raise HostError("无法停止本机 Gateway，未启动第二个实例。请通过原启动终端检查。") from None

    def restart(self, status: Callable[[str], None]) -> str:
        """Restart verified local gateway run with a fresh process/plugin cache."""
        if self.closed.is_set():
            raise HostError("窗口已关闭。")
        config = self._native_json("config", "get", "gateway", "--json")
        if config.get("mode", "local") != "local":
            raise HostError("此按钮只重启本项目的本机 Gateway；远程服务请在部署端重启。")
        if config.get("controlUi", {}).get("enabled") is False:
            raise HostError("Control UI 当前未启用，未执行重启。请通过原生配置检查。")
        try:
            port = int(self.env.get("OPENCLAW_GATEWAY_PORT") or config.get("port", 18789))
            if not 0 < port < 65536:
                raise ValueError
        except (TypeError, ValueError):
            raise HostError("Gateway 端口无效，请运行 run-openclaw.ps1 config validate。") from None
        if self._listening(port):
            process = self._restart_target(port)
            status("正在重启后台服务")
            self._stop_gateway(process)
            deadline = time.monotonic() + 5
            while self._listening(port) and time.monotonic() < deadline and not self.closed.is_set():
                self.closed.wait(0.2)
            if self._listening(port):
                raise HostError("Gateway 端口仍被占用，未启动第二个实例。请检查原启动终端。")
        return self.connect(status)

    def close(self) -> None:
        # The desktop is a view; closing it must not kill work owned by Gateway.
        self.closed.set()
        with self._settings_read_lock:
            reader, self._settings_reader = self._settings_reader, None
            if reader is not None:
                reader.close()
