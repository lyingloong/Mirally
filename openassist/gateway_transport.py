"""Threaded stdio transport; no agent logic, desktop automation, or local context."""
from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import subprocess
import threading
from uuid import uuid4


class GatewayTransport:
    def __init__(self, product_root: Path):
        self.root = product_root
        self.events = queue.SimpleQueue()
        self.process = None
        self._write_lock = threading.Lock()
        self._closing = False

    def start(self):
        try:
            runtime = json.loads((self.root / '.artifacts/openclaw-runtime.json').read_text(encoding='utf-8'))
            node = self.root / runtime['node']
            self.process = subprocess.Popen(
                [str(node), str(self.root / 'integrations/openclaw/desktop-bridge.mjs')],
                cwd=self.root, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, encoding='utf-8', errors='replace', bufsize=1,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
            )
        except (OSError, ValueError, KeyError):
            self.events.put({'type': 'connection', 'connected': False,
                             'message': 'OpenClaw 客户端未能启动，请先完成原生安装与配置。'})
            return
        threading.Thread(target=self._read, daemon=True, name='mirally-gateway-reader').start()

    def _read(self):
        try:
            for line in self.process.stdout:
                try:
                    event = json.loads(line)
                except ValueError:
                    # Third-party diagnostics never become product messages.
                    continue
                if isinstance(event, dict):
                    self.events.put(event)
        finally:
            if not self._closing:
                self.events.put({'type': 'connection', 'connected': False,
                                 'message': '客户端连接已结束。关闭窗口不会停止 Gateway 中的任务。'})

    def request(self, op, **params):
        request_id = uuid4().hex
        payload = json.dumps({'id': request_id, 'op': op, 'params': params}, ensure_ascii=False)
        with self._write_lock:
            if not self.process or self.process.poll() is not None or self._closing:
                raise RuntimeError('OpenClaw 客户端未连接，草稿仍然保留。')
            try:
                self.process.stdin.write(payload + '\n')
                self.process.stdin.flush()
            except (OSError, ValueError) as error:
                raise RuntimeError('客户端连接中断，草稿仍然保留。') from error
        return request_id

    def close(self):
        if self._closing:
            return
        if self.process and self.process.poll() is None:
            try:
                self.request('shutdown')
            except RuntimeError:
                pass
        self._closing = True
        # Disconnect this client only. Never stop the Gateway or abort its runs.
        def finish():
            if not self.process:
                return
            try:
                self.process.wait(timeout=4)
            except subprocess.TimeoutExpired:
                self.process.terminate()
        threading.Thread(target=finish, daemon=True, name='mirally-gateway-close').start()
