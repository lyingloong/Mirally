"""Opt-in Gateway -> UFO subagent harness test with a synthetic model API.

The fixture uses a native subagent session key and can capture the real
interactive desktop. It tests the harness, not native sessions_spawn admission.
It must not run in the default no-desktop suite.
It never reads the developer's OpenClaw/UFO model credentials.
"""
import json
from contextlib import ExitStack
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from tools.configure_ufo_agent import agent_patch

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(sys.platform == "win32" and (ROOT / ".artifacts/openclaw-runtime.json").is_file()
                     and os.environ.get("MIRALLY_TEST_DESKTOP") == "1",
                     "Real desktop observation requires explicit MIRALLY_TEST_DESKTOP=1")
class GatewayUfoTests(unittest.TestCase):
    def test_real_gateway_loads_ufo_child_harness_and_preserves_reply_history(self):
        prompts = []
        class API(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                prompts.append(data)
                marker = "合成正文：保持段落\n\n第二段全部保留。"
                # The UFO child harness enters the desktop subtask path.
                # Its model contract is HostAgentResponse,
                # not the product conversation AssistantDecision envelope.
                content = json.dumps({
                    "observation": "当前请求只需要文本回复，无需桌面操作。",
                    "thought": "直接结束本轮并返回合成正文。",
                    "status": "FINISH",
                    "message": [marker],
                    "plan": [],
                    "result": marker,
                }, ensure_ascii=False)
                value = {"id": "synthetic-completion", "object": "chat.completion", "created": 1,
                         "model": "vision", "choices": [{"index": 0, "finish_reason": "stop",
                             "message": {"role": "assistant", "content": content}}],
                         "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30}}
                body = json.dumps(value).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        server = ThreadingHTTPServer(("127.0.0.1", 0), API)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        runtime = json.loads((ROOT / ".artifacts/openclaw-runtime.json").read_text())
        node, entry = ROOT / runtime["node"], ROOT / runtime["entry"]
        gateway = None
        try:
            with tempfile.TemporaryDirectory(prefix="mirally-gateway-test-") as directory, ExitStack() as cleanup:
                state = Path(directory)
                with socket.socket() as port_socket:
                    port_socket.bind(("127.0.0.1", 0))
                    port = port_socket.getsockname()[1]
                config = {
                    "gateway": {"mode": "local", "bind": "loopback", "port": port,
                                "auth": {"mode": "token", "token": "synthetic-test-token-only"}},
                    "agents": {"defaults": {"workspace": str(state / "workspace"),
                        "thinkingDefault": "off", "model": "synthetic/vision",
                        "models": {"synthetic/vision": {}}},
                        "entries": {"main": {"default": True, "name": "Synthetic main"}}},
                    "models": {"mode": "merge", "providers": {"synthetic": {
                        "baseUrl": f"http://127.0.0.1:{server.server_port}/v1",
                        "apiKey": "synthetic-key-only", "api": "openai-completions",
                        "models": [{"id": "vision", "name": "Synthetic vision",
                                    "input": ["text", "image"], "reasoning": True,
                                    "compat": {"thinkingFormat": "openai", "supportsReasoningEffort": True,
                                        "supportedReasoningEfforts": ["none", "low", "medium", "xhigh"],
                                        "reasoningEffortMap": {"minimal": "low", "high": "xhigh"}},
                                    "contextWindow": 32000, "maxTokens": 1024}]}}},
                    "plugins": {"allow": ["mirally-ufo"]},
                }
                patch = agent_patch(config, ROOT, "synthetic/vision")
                config["agents"]["entries"].update(patch["agents"]["entries"])
                config["plugins"].update(patch["plugins"])
                config_path = state / "openclaw.json"
                config_path.write_text(json.dumps(config), encoding="utf-8")
                env = dict(os.environ, OPENCLAW_STATE_DIR=str(state), OPENCLAW_CONFIG_PATH=str(config_path),
                           PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
                env["PATH"] = str(node.parent) + os.pathsep + env.get("PATH", "")
                def cli(*arguments):
                    completed = subprocess.run([str(node), str(entry), *arguments], cwd=ROOT, env=env,
                        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90,
                        creationflags=subprocess.CREATE_NO_WINDOW)
                    self.assertEqual(completed.returncode, 0,
                                     completed.stdout[-2000:] + completed.stderr[-4000:])
                    return completed.stdout

                cli("config", "validate")
                with (state / "gateway.log").open("wb") as log:
                    gateway = subprocess.Popen([str(node), str(entry), "gateway", "run"], cwd=ROOT, env=env,
                        stdin=subprocess.DEVNULL, stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW)
                    def stop_gateway(process=gateway):
                        if process.poll() is None:
                            process.terminate()
                        process.wait(timeout=15)
                    cleanup.callback(stop_gateway)
                    deadline = time.monotonic() + 45
                    while time.monotonic() < deadline:
                        self.assertIsNone(gateway.poll(), (state / "gateway.log").read_text(errors="replace")[-6000:])
                        try:
                            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                                break
                        except OSError:
                            time.sleep(0.2)
                    else:
                        self.fail("Isolated Gateway did not start")
                    listed = cli("gateway", "call", "agents.list", "--json")
                    self.assertIn('"ufo"', listed)
                    for text in ("仅在聊天里起草一段合成文字，不操作电脑。", "复述刚才那段文字，不操作电脑。"):
                        answer = cli("agent", "--agent", "ufo", "--session-key", "agent:ufo:subagent:synthetic-child",
                                     "--message", text, "--json", "--timeout", "45")
                        self.assertIn("合成正文", answer)
                    self.assertEqual(len(prompts), 2)
                    self.assertTrue(all(p.get("reasoning_effort") == "none" for p in prompts), prompts)
                    history = json.dumps(prompts[-1]["messages"], ensure_ascii=False)
                    self.assertIn("合成正文", history)
                    self.assertIn("第二段全部保留", history)
                    # Native UFO observation legitimately sends the current
                    # screenshot as OpenAI multimodal content. Historical
                    # text must remain intact, while image parts need only be
                    # structurally valid.
                    for prompt in prompts:
                        for message in prompt["messages"]:
                            content = message.get("content")
                            self.assertTrue(
                                isinstance(content, str) or (
                                    isinstance(content, list)
                                    and all(isinstance(part, dict) for part in content)
                                ),
                                message,
                            )
                    gateway.terminate()
                    gateway.wait(timeout=15)
                    gateway = None
        finally:
            if gateway is not None:
                gateway.terminate()
                gateway.wait(timeout=15)
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
