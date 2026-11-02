"""Offline end-to-end acceptance against the built native Mirally kernel.

Exit codes: 0 = every acceptance check passed; 1 = a real acceptance check failed;
2 = usage error (for example a non-empty --home); 3 = skipped because prerequisites
are missing (nothing was started, no port was bound).
"""

from __future__ import annotations

import argparse
import base64
import io
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
import http.cookiejar

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from mirally.framework_host import FrameworkHost, discover_node, node_candidates, node_version  # noqa: E402

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_USAGE = 2
EXIT_SKIPPED = 3
SETUP_COMMAND = r"run .venv\Scripts\python.exe tools\setup_framework.py first"


def node_supported(root: Path) -> tuple[bool, list[str]]:
    """Read-only Node check with the same version rule and order as discover_node()."""
    problems = []
    for candidate in node_candidates(root):
        path = Path(candidate).expanduser()
        if not path.is_file():
            problems.append(f"{path} does not exist")
            continue
        parts = node_version(path)
        if parts is not None and ((parts[0] == 22 and parts >= (22, 19, 0)) or parts[0] >= 24):
            return True, []
        problems.append(f"{path} is not Node 22.19+ (22.x) or Node 24+")
    return False, problems


def missing_prerequisites(root: Path) -> list[str]:
    """Read-only list of what FrameworkHost.start() needs; never builds or starts anything."""
    root = Path(root)
    missing = []
    kernel_entry = root / "kernel/dsh/packages/mirally/mirally-product/start.mjs"
    if not kernel_entry.is_file():
        missing.append(f"kernel entry {kernel_entry}")
    kernel_module = root / "kernel/dsh/packages/core/agent/lib/index.js"
    if not kernel_module.is_file():
        missing.append(f"kernel build output {kernel_module}")
    frontend_build = root / "frontend/control-ui/dist/.mirally-build.json"
    if not frontend_build.is_file():
        missing.append(f"frontend build output {frontend_build}")
    python = root / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    if not python.is_file():
        missing.append(f"framework virtualenv {python}")
    node_found, problems = node_supported(root)
    if not node_found:
        missing.append("Node 22.19+ (22.x) or Node 24+ (" + "; ".join(problems or ["not found on PATH"]) + ")")
    return missing


class FixtureProvider(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    calls: list[dict] = []

    def log_message(self, *_):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
        self.calls.append(body)
        messages = body.get("messages", [])
        # Native DSH adds runtime and Skill reminders as user-role context.
        # The fixture routes the actual human request, not those injected rows.
        human_inputs = [(index, str(item.get("content", ""))) for index, item in enumerate(messages)
            if item.get("role") == "user" and not str(item.get("content", "")).startswith((
                "Current runtime context.", "Current runtime context: none.", "<system-reminder>"))]
        human_index, last = human_inputs[-1] if human_inputs else (-1, "")
        is_result = any(item.get("role") == "tool" for item in messages[human_index + 1:])
        contact_check = any("Mirally optional personal check-in" in str(item.get("content", "")) for item in messages if item.get("role") == "system")
        names = [tool.get("function", {}).get("name") for tool in body.get("tools", [])]
        tool = args = None
        if not is_result and "remember my preference" in last:
            tool, args = "mirally_set_preference", {"key": "tone", "value": "concise and friendly"}
        elif not is_result and "create a reminder" in last:
            tool, args = "mirally_create_reminder", {"title": "Acceptance reminder", "due_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 2)), "body": "A real durable reminder from the native tool pipeline"}
        elif not is_result and "read fixture MCP tool" in last:
            tool, args = "mcp__acceptance__echo", {"text": "fixture value"}
        elif not is_result and "call native plugin" in last:
            tool, args = "fixture_plugin_echo", {"text": "plugin value"}
        elif not is_result and "create deliverable" in last:
            tool, args = "mirally_create_file", {"name": "迁移成果.md", "content": "# 实际交付\n\n复用原有 Mirally 产品体验。\n"}
        elif not is_result and "Use the mirally_reminders tool" in last:
            tool, args = "mirally_reminders", {}
        if tool and tool not in names:
            raise AssertionError("Expected product tool was not offered by the real native runtime: " + tool)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Connection", "close")
        self.end_headers()
        def chunk(delta, finish=None, usage=None):
            data = {"id": "chatcmpl-local-fixture", "object": "chat.completion.chunk", "created": int(time.time()), "model": "fixture-model", "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
            if usage:
                data["usage"] = usage
            self.wfile.write(("data: " + json.dumps(data) + "\n\n").encode())
            self.wfile.flush()
        try:
            chunk({"role": "assistant"})
            if tool:
                chunk({"tool_calls": [{"index": 0, "id": "call-local-fixture", "type": "function", "function": {"name": tool, "arguments": json.dumps(args)}}]})
                chunk({}, "tool_calls")
            else:
                text = json.dumps({"contact": True, "title": "An optional check-in", "body": "Would a brief planning break help?", "reason": "An explicit preference is available"}) if contact_check else "Done. The product tool returned a durable result." if is_result else "Hello from the real native model adapter."
                if "slow response" in last:
                    text = "slow " * 150
                for word in text.split(" "):
                    chunk({"content": word + " "})
                    if "slow response" in last:
                        time.sleep(.03)
                chunk({}, "stop", {"prompt_tokens": 42, "completion_tokens": 16, "total_tokens": 58})
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        self.close_connection = True


def wait_until(function, *, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = function()
        if result:
            return result
        time.sleep(.1)
    raise AssertionError("Acceptance condition timed out")


class Client:
    def __init__(self, runtime):
        self.runtime = runtime

    def call(self, path, data=None, *, origin=None):
        headers = {"Authorization": "Bearer " + self.runtime["token"], "Content-Type": "application/json"}
        if origin:
            headers["Origin"] = origin
        request = urllib.request.Request(self.runtime["url"] + path, data=None if data is None else json.dumps(data).encode(), headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            error.msg += ": " + error.read(4096).decode("utf-8", "replace")
            raise

    def rpc(self, method, params=None):
        return self.call("/api/rpc", {"method": method, "params": params or {}})["result"]

    def history(self, session):
        return self.call("/api/sessions/" + session["id"] + "/history")["messages"]

    def send(self, session, command, text, **options):
        before = len(self.history(session))
        result = self.call("/api/sessions/" + session["id"] + "/messages", {"commandId": command, "text": text, **options})
        assert result["accepted"]
        wait_until(lambda: len(self.history(session)) >= before + 2 and self.history(session)[-1]["role"] == "assistant")
        return result


def verify(root: Path, home: Path, *, browser_files: bool = False, browser_history: bool = False) -> list[str]:
    checks = []
    provider = ThreadingHTTPServer(("127.0.0.1", 0), FixtureProvider)
    thread = threading.Thread(target=provider.serve_forever, daemon=True)
    thread.start()
    host = FrameworkHost(root, home)
    try:
        client = Client(host.start())
        assert client.call("/api/health")["kernel"] == "dsh"
        for method in ("desktop.start", "desktop.list", "desktop.cancel", "desktop.respond"):
            try:
                client.rpc(method, {})
            except urllib.error.HTTPError as error:
                assert error.code == 403
                assert '"method_not_allowed"' in error.msg
            else:
                raise AssertionError("Deferred UFO operation was exposed: " + method)
        assert not (home / "actions").exists()
        checks.append("default framework has no UFO executor, task API, or action directory")
        client.rpc("settings.update", {"patch": {"model": {"provider": "openai-compatible", "base_url": f"http://127.0.0.1:{provider.server_port}/v1", "api_format": "openai-chat", "model_id": "fixture-model", "api_key": "offline-fixture-key"}}})
        public = client.rpc("settings.get")
        assert "offline-fixture-key" not in json.dumps(public)
        assert public["activity_enabled"] is False and public["background_enabled"] is False
        assert client.rpc("calendar.settings.get")["confirmed"] is False
        client.rpc("calendar.settings.update", {"time_zone": "Asia/Shanghai"})
        checks.append("private model configuration and passive defaults")
        try:
            client.call("/api/health", origin="https://untrusted.invalid")
            raise AssertionError("Cross-origin request accepted")
        except urllib.error.HTTPError as error:
            assert error.code == 403
        try:
            client.rpc("internal.settings.get")
            raise AssertionError("Private settings exposed over HTTP")
        except urllib.error.HTTPError as error:
            assert error.code == 403
        checks.append("authenticated loopback and cross-origin/private-method refusal")
        try:
            client.rpc('settings.update', {'patch': {'activity_enabled': True, 'activity_capture': {'clipboard_enabled': True}}})
            raise AssertionError('Generic settings bypassed capture confirmation')
        except urllib.error.HTTPError as error:
            assert error.code == 403
        cookies = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        auth = urllib.request.Request(host.runtime["url"] + "/api/auth", data=b"{}", headers={"Authorization": "Bearer " + host.runtime["token"]})
        with cookies.open(auth) as response:
            assert "HttpOnly" in response.headers["Set-Cookie"]
        with cookies.open(host.runtime["url"] + "/api/health") as response:
            assert json.load(response)["ready"]
        checks.append("browser refresh authentication through an HttpOnly cookie")
        session = client.call("/api/sessions", {"title": "End-to-end acceptance"})
        client.send(session, "chat-1", "hello")
        assert "native model adapter" in client.history(session)[-1]["text"]
        tool_names = {tool.get("function", {}).get("name", "") for tool in FixtureProvider.calls[-1].get("tools", [])}
        assert not any("ufo" in name.lower() for name in tool_names), tool_names
        # Mirally's own observations are supported; deferred desktop input and
        # the retired UFO executor must not return through another tool alias.
        assert {name for name in tool_names if "desktop" in name.lower()} <= {
            "mirally_desktop_foreground", "mirally_desktop_windows", "mirally_desktop_screenshot"}, tool_names
        duplicate = client.call("/api/sessions/" + session["id"] + "/messages", {"commandId": "chat-1", "text": "hello"})
        assert duplicate["replay"]
        checks.append("real native provider, Agent loop, transcript and command deduplication")
        uploaded = client.call('/api/materials', {'conversation_id': session['id'], 'name': '原始材料.txt', 'media_type': 'text/plain', 'data': base64.b64encode('明确材料：保留海獭桌宠。'.encode()).decode()})
        assert uploaded['readingState'] == 'ready'
        client.send(session, 'material-1', 'read supplied material', materials=[uploaded['id']])
        assert '保留海獭桌宠' in json.dumps(FixtureProvider.calls[-1], ensure_ascii=False)
        client.send(session, 'file-denied', 'create deliverable')
        assert not client.call('/api/artifacts?conversation_id=' + session['id'])['artifacts']
        client.send(session, 'file-allowed', 'create deliverable', allow_file_write=True)
        artifact = client.call('/api/artifacts?conversation_id=' + session['id'])['artifacts'][0]
        assert artifact['name'] == '迁移成果.md'
        preview = client.call('/api/artifacts/' + artifact['id'] + '/preview')
        assert '实际交付' in preview['text']
        assert 'path' not in preview['artifact']
        if browser_files:
            subprocess.run([str(discover_node(root)), str(root / "tools/verify_framework_files.mjs"),
                str(home / "runtime.json"), session["id"], artifact["id"]], cwd=root, check=True)
            checks.append("original Files panel reads real native tool artifact, previews and downloads")
        receipt = client.call('/api/sessions/' + session['id'] + '/messages', {'commandId': 'file-allowed', 'text': 'create deliverable', 'allow_file_write': True})
        assert receipt['replay'] and len(client.call('/api/artifacts?conversation_id=' + session['id'])['artifacts']) == 1
        client.rpc('settings.update', {'patch': {'model': {'image_enabled': True}}})
        from PIL import Image
        image_bytes = io.BytesIO(); Image.new('RGB', (2, 2), 'blue').save(image_bytes, format='PNG')
        image_material = client.call('/api/materials', {'conversation_id': session['id'], 'name': 'fixture.png', 'media_type': 'image/png', 'data': base64.b64encode(image_bytes.getvalue()).decode()})
        client.send(session, 'main-image', 'read supplied image', materials=[image_material['id']])
        assert 'data:image/png;base64' in json.dumps(FixtureProvider.calls[-1])
        checks.append('real material extraction and native multimodal message, file permission refusal, deliverable receipt/preview and replay deduplication')
        if browser_history:
            subprocess.run([str(discover_node(root)), str(root / "tools/verify_framework_history_browser.mjs"),
                str(home / "runtime.json"), str(root / ".artifacts/migration-closeout/history-browser"), "--live"], cwd=root, check=True)
            checks.append("original history shows actual attachment pixels/bytes and tool details after reload and a fresh client")
        client.send(session, "preference-1", "remember my preference")
        assert any(item["key"] == "tone" for item in client.rpc("preference.list")["preferences"])
        client.send(session, "reminder-1", "create a reminder")
        wait_until(lambda: len(client.rpc("inbox.list")["items"]) > 0)
        item = client.rpc("inbox.list")["items"][0]
        client.rpc("inbox.ack", {"delivery_id": item["delivery_id"], "state": "read"})
        checks.append("real model tool calls, persistent personalization and due reminder delivery")
        prior = len(client.history(session))
        client.call("/api/sessions/" + session["id"] + "/messages", {"commandId": "cancel-1", "text": "slow response"})
        time.sleep(.25)
        client.call("/api/sessions/" + session["id"] + "/cancel", {})
        commands = client.rpc("command.list", {"conversation_id": session["id"]})
        assert any(command["state"] == "cancelled" for command in commands["commands"])
        checks.append("actual native cancellation and durable stopped command")
        host.close()
        host = FrameworkHost(root, home)
        client = Client(host.start())
        sessions = client.call("/api/sessions")["sessions"]
        assert any(value["id"] == session["id"] for value in sessions)
        assert len(client.history(session)) >= prior
        assert client.rpc("preference.list")["preferences"]
        assert len(client.rpc("inbox.list")["items"]) == 1
        checks.append("cold restart: native history, preference and delivery survive without duplication")
        skill_root = home / "acceptance-skills"
        skill = skill_root / "acceptance-helper"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("---\nname: acceptance-helper\ndescription: A local acceptance Skill for a controlled artifact.\n---\nCreate the requested artifact using the configured write script.\n", encoding="utf-8")
        (skill / "write.py").write_text("from pathlib import Path\nPath('receipt.txt').write_text('verified controlled script',encoding='utf-8')\nprint('artifact created')\n", encoding="utf-8")
        (skill / "slow.py").write_text("from pathlib import Path\nimport time\nPath('started.txt').write_text('started')\ntime.sleep(10)\nPath('should-not-finish.txt').write_text('finished')\n", encoding="utf-8")
        configured = client.rpc("extensions.configure", {"extension": {"id": "acceptance-skill", "type": "bundle", "config": {"skills": [str(skill_root)], "scripts": [{"id": "write", "path": str(skill / "write.py"), "interpreter": "python"}, {"id": "slow", "path": str(skill / "slow.py"), "interpreter": "python"}]}}})
        assert any(value["status"] == "active" for value in configured["extensions"]), configured
        catalog = client.rpc("skills.list")
        assert any(value["name"] == "acceptance-helper" for value in catalog["skills"]), catalog
        definition = client.rpc("skills.read", {"name": "acceptance-helper"})
        assert "configured write script" in json.dumps(definition)
        try:
            client.rpc("scripts.run", {"extension_id": "acceptance-skill", "id": "write", "confirmed": False, "conversation_id": session["id"], "command_id": "script-1"})
            raise AssertionError("Unconfirmed script ran")
        except urllib.error.HTTPError as error:
            assert error.code == 403
        assert not (skill / "receipt.txt").exists()
        executed = client.rpc("scripts.run", {"extension_id": "acceptance-skill", "id": "write", "confirmed": True, "conversation_id": session["id"], "command_id": "script-1"})
        assert (skill / "receipt.txt").read_text() == "verified controlled script", executed
        repeated = client.rpc("scripts.run", {"extension_id": "acceptance-skill", "id": "write", "confirmed": True, "conversation_id": session["id"], "command_id": "script-1"})
        assert repeated["replay"]
        checks.append("native Skill discovery/body and confirmed, deduplicated real script artifact")
        cancelled = []
        def run_slow_script():
            try:
                cancelled.append(client.rpc("scripts.run", {"extension_id": "acceptance-skill", "id": "slow", "confirmed": True, "conversation_id": session["id"], "command_id": "cancel-script"}))
            except Exception as error:
                cancelled.append(error)
        script_thread = threading.Thread(target=run_slow_script, daemon=True)
        script_thread.start()
        wait_until(lambda: (skill / "started.txt").exists())
        client.call("/api/sessions/" + session["id"] + "/cancel", {})
        script_thread.join(15)
        assert not script_thread.is_alive() and cancelled, cancelled
        assert isinstance(cancelled[0], dict) and cancelled[0]["outcome"] == "unknown", cancelled
        assert not (skill / "should-not-finish.txt").exists()
        checks.append("conversation stop actually terminates its confirmed script process before later file effects")
        client.rpc("extensions.disable", {"id": "acceptance-skill"})
        assert not any(value["name"] == "acceptance-helper" for value in client.rpc("skills.list")["skills"])
        checks.append("extension disposal withdraws the native Skill catalog")
        mcp = client.rpc("extensions.configure", {"extension": {"id": "acceptance-mcp", "type": "mcp", "config": {"serverName": "acceptance", "transport": "stdio", "command": "python", "args": [str(root / "tests/fixtures/framework_mcp.py")], "readOnly": True}}})
        assert any(value["id"] == "acceptance-mcp" and value["status"] == "active" for value in mcp["extensions"]), mcp
        assert "fixture://record" in json.dumps(client.rpc("mcp.resources.list"))
        assert "uriTemplate" in json.dumps(client.rpc("mcp.resources.templates", {"server": "acceptance"}))
        assert "Real MCP resource" in json.dumps(client.rpc("mcp.resources.read", {"server": "acceptance", "uri": "fixture://record"}))
        assert "greeting" in json.dumps(client.rpc("mcp.prompts.list"))
        assert "Hello Mirally" in json.dumps(client.rpc("mcp.prompts.get", {"server": "acceptance", "name": "greeting", "arguments": {"name": "Mirally"}}))
        client.send(session, "mcp-1", "read fixture MCP tool")
        configured = client.rpc("extensions.configure", {"extension": {"id": "acceptance-mcp", "type": "mcp", "config": {"serverName": "acceptance", "transport": "stdio", "command": "python", "args": [str(root / "tests/fixtures/framework_mcp.py")], "readOnly": False}}})
        assert "mcp__acceptance__echo" in json.dumps(client.rpc("mcp.tools.list"))
        explicit = client.rpc("mcp.tools.call", {"server": "acceptance", "name": "mcp__acceptance__echo", "arguments": {"text": "explicitly confirmed"}, "confirmed": True, "conversation_id": session["id"], "command_id": "mcp-explicit"})
        assert not explicit["isError"], explicit
        assert "explicitly confirmed" in json.dumps(explicit)
        checks.append("real MCP stdio SDK: tools, resources, templates and prompts on one supervised connection")
        plugin = home / "fixture-plugin.mjs"
        plugin.write_text("export const mirally={apiVersion:1}; export default {name:'fixture-native',apply(ctx){ctx.tools.register({name:'fixture_plugin_echo',description:'Explicit offline plugin echo',parameters:{type:'object',properties:{text:{type:'string'}},required:['text']},output:{schema:{type:'object',additionalProperties:true},render:(_args,result)=>[{type:'text',text:JSON.stringify(result)}]},execute:async args=>({echo:args.text})});}};", encoding="utf-8")
        result = client.rpc("extensions.configure", {"extension": {"id": "acceptance-plugin", "type": "plugin", "path": str(plugin), "config": {"apiVersion": 1}}})
        assert any(value["id"] == "acceptance-plugin" and value["status"] == "active" for value in result["extensions"]), result
        client.send(session, "plugin-1", "call native plugin")
        client.rpc("extensions.disable", {"id": "acceptance-plugin"})
        client.rpc("extensions.disable", {"id": "acceptance-mcp"})
        client.send(session, "after-disable", "hello after disabling integrations")
        names = [value["function"]["name"] for value in FixtureProvider.calls[-1].get("tools", [])]
        assert "fixture_plugin_echo" not in names and "mcp__acceptance__echo" not in names
        checks.append("versioned native plugin execution and removal of plugin/MCP tools from new generations")
        completion = client.call("/internal/completion", {"messages": [{"role": "user", "content": "Offline auxiliary bridge check"}], "max_tokens": 64})
        assert "native model adapter" in completion["text"]
        client.rpc("settings.update", {"patch": {"model": {"image_enabled": True}}})
        from PIL import Image
        pixels = io.BytesIO()
        Image.new("RGB", (2, 2), "white").save(pixels, format="PNG")
        pixel = "data:image/png;base64," + base64.b64encode(pixels.getvalue()).decode("ascii")
        image = client.call("/internal/completion", {"messages": [{"role": "user", "content": [{"type": "text", "text": "Offline image bridge check"}, {"type": "image_url", "image_url": {"url": pixel}}]}], "max_tokens": 64})
        assert "native model adapter" in image["text"]
        assert "data:image/png;base64" in json.dumps(FixtureProvider.calls[-1])
        client.rpc("settings.update", {"patch": {"background_enabled": True, "general_initiative_enabled": True}})
        wait_until(lambda: any(item["kind"] == "initiative" for item in client.rpc("inbox.list")["items"]))
        client.rpc("settings.update", {"patch": {"background_enabled": False, "general_initiative_enabled": False}})
        checks.append("native auxiliary model bridge and opt-in grounded contact planning to durable inbox")
        import psutil
        sidecars = [process for process in psutil.Process(host.process.pid).children()
                    if "mirally.framework" in process.cmdline()]
        assert len(sidecars) == 1, sidecars
        sidecars[0].terminate()
        sidecars[0].wait(10)
        def unavailable():
            try:
                client.call("/api/health")
                return False
            except urllib.error.HTTPError as error:
                return error.code == 503
        wait_until(unavailable)
        assert client.call("/api/shutdown", {})["stopping"]
        host.process.wait(15)
        assert not (home / "framework-host.lock").exists()
        checks.append("faulted sidecar fences the Host and authenticated shutdown still releases ownership")
        return checks
    finally:
        host.close()
        provider.shutdown()
        provider.server_close()


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog="exit codes: 0 passed, 1 acceptance failure, 2 usage error, 3 skipped (missing prerequisites)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--home", type=Path)
    parser.add_argument("--browser-files", action="store_true", help="Also verify the retained Files UI in headless Edge against the real loopback tool artifact")
    parser.add_argument("--browser-history", action="store_true", help="Also verify actual native material/tool history in Edge using the loopback provider")
    args = parser.parse_args()
    if args.home and args.home.exists() and any(args.home.iterdir()):
        print(f"ERROR: --home must be missing or empty for isolation: {args.home}", file=sys.stderr)
        return EXIT_USAGE
    root = args.root.resolve()
    missing = missing_prerequisites(root)
    if missing:
        print("SKIP: missing prerequisites: " + ", ".join(missing), file=sys.stderr)
        print(SETUP_COMMAND, file=sys.stderr)
        return EXIT_SKIPPED
    try:
        if args.home:
            checks = verify(root, args.home.resolve(), browser_files=args.browser_files, browser_history=args.browser_history)
        else:
            with tempfile.TemporaryDirectory(prefix="mirally-acceptance-") as temporary:
                checks = verify(root, Path(temporary) / "product home", browser_files=args.browser_files, browser_history=args.browser_history)
    except Exception as error:
        print(f"FAIL: {type(error).__name__}: {error}", file=sys.stderr)
        return EXIT_FAILURE
    for check in checks:
        print("PASS " + check)
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
