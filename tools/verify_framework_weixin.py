"""Offline iLink acceptance against the real built Host, sidecar and DSH Agent.

Uses only a 127.0.0.1 fixture service, a fresh product home and the explicit test
model. It never logs in to a WeChat account or sends an external message.
Run after tools/setup_framework.py. GUI and external model APIs are not used.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import time
from urllib.parse import urlsplit
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from mirally.framework_host import FrameworkHost


def record(checks, text):
    checks.append(text)
    print("PASS " + text, flush=True)


def wait_until(function, *, timeout=35, label="acceptance condition"):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = function()
        if result:
            return result
        time.sleep(.08)
    raise AssertionError(label + " timed out")


class Client:
    def __init__(self, runtime):
        self.runtime = runtime

    def call(self, path, value=None):
        request = urllib.request.Request(self.runtime["url"] + path,
            data=None if value is None else json.dumps(value, ensure_ascii=False).encode(),
            headers={"Authorization": "Bearer " + self.runtime["token"], "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=35) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            error.msg += ": " + error.read(4096).decode("utf-8", "replace")
            raise

    def rpc(self, method, **params):
        return self.call("/api/rpc", {"method": method, "params": params})["result"]

    def status(self):
        return self.rpc("weixin.status")

    def history(self, identity):
        return self.call(f"/api/sessions/{identity}/history")["messages"]


class ILinkFixture:
    TOKEN = "offline-weixin-fixture-token"
    PEER = "owner@im.wechat"
    BOT = "bot@im.bot"

    def __init__(self):
        self.condition = threading.Condition()
        self.batches = []
        self.sent = []
        self.requests = []
        self.confirm = False
        self.sequence = 0
        fixture = self
        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_):
                pass

            def reply(self, value):
                raw = json.dumps(value, ensure_ascii=False).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("Connection", "close")
                self.end_headers()
                try:
                    self.wfile.write(raw)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    pass
                self.close_connection = True

            def do_GET(self):
                assert urlsplit(self.path).path == "/ilink/bot/get_qrcode_status", self.path
                with fixture.condition:
                    confirmed = fixture.confirm
                self.reply({"status": "confirmed", "bot_token": fixture.TOKEN,
                    "ilink_bot_id": fixture.BOT, "ilink_user_id": fixture.PEER,
                    "baseurl": fixture.url} if confirmed else {"status": "wait"})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
                path = urlsplit(self.path).path
                auth = self.headers.get("Authorization")
                with fixture.condition:
                    fixture.requests.append({"path": path, "auth": auth, "body": body})
                if path == "/ilink/bot/get_bot_qrcode":
                    self.reply({"qrcode": "offline-fixture-code", "qrcode_img_content": "https://weixin.qq.com/offline-fixture-qr"})
                elif path == "/ilink/bot/getupdates":
                    assert auth == "Bearer " + fixture.TOKEN
                    index = int(body.get("get_updates_buf") or "0")
                    with fixture.condition:
                        if index >= len(fixture.batches):
                            fixture.condition.wait(.2)
                        msgs = fixture.batches[index] if index < len(fixture.batches) else []
                        cursor = index + 1 if msgs else index
                    self.reply({"ret": 0, "msgs": msgs, "get_updates_buf": str(cursor)})
                elif path == "/ilink/bot/sendmessage":
                    assert auth == "Bearer " + fixture.TOKEN
                    assert body["msg"]["to_user_id"] == fixture.PEER
                    assert body["msg"]["context_token"] == "fixture-context"
                    with fixture.condition:
                        fixture.sent.append(body["msg"])
                    self.reply({"ret": 0, "message_id": "receipt-" + str(len(fixture.sent))})
                else:
                    raise AssertionError("unexpected local protocol endpoint: " + path)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def push(self, text, *, sender=None, identity=None):
        with self.condition:
            self.sequence += 1
            message = {"message_id": identity or f"fixture-{self.sequence}", "message_type": 1, "message_state": 2,
                "from_user_id": sender or self.PEER, "to_user_id": self.BOT, "context_token": "fixture-context",
                "create_time_ms": time.time_ns() // 1_000_000,
                "item_list": [{"type": 1, "text_item": {"text": text}}]}
            self.batches.append([message])
            self.condition.notify_all()
        return message["message_id"]

    def sent_texts(self):
        with self.condition:
            return [item["item_list"][0]["text_item"]["text"] for item in self.sent]

    def wait_reply(self, text, *, after=0):
        return wait_until(lambda: next((value for value in self.sent_texts()[after:] if text in value), None), label="iLink reply " + text[:40])

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(3)


@contextmanager
def test_environment(url):
    key = "MIRALLY_TEST_WEIXIN_URL"
    old = os.environ.get(key)
    os.environ[key] = url
    try:
        yield
    finally:
        if old is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = old


def records(home, table):
    assert table in {"channel_weixin_events", "channel_weixin_outbox", "commands"}
    with sqlite3.connect(home / "framework/product.sqlite3") as connection:
        connection.row_factory = sqlite3.Row
        return [dict(row) for row in connection.execute("SELECT * FROM " + table)]


def verify(root, home):
    checks = []
    fixture = ILinkFixture()
    host = FrameworkHost(root, home, test_mode=True)
    with test_environment(fixture.url):
        try:
            client = Client(host.start())
            initial = client.status()
            assert initial["state"] == "unbound" and not initial["bound"]
            assert client.call("/api/health")["mode"] == "test"
            for method in ("channel.weixin.get", "channel.weixin.save", "channel.weixin.claim"):
                try:
                    client.rpc(method)
                except urllib.error.HTTPError as error:
                    assert error.code == 403
                else:
                    raise AssertionError("internal credentials/transport exposed through " + method)
            record(checks, "authenticated public channel API refuses internal credentials and transport methods")

            session = client.call("/api/sessions", {"title": "微信与桌面共同会话"})
            original = session["id"]
            login = client.rpc("weixin.login.start")
            assert login["state"] == "waiting_scan" and login["login"]["qr_content"]
            fixture.confirm = True
            pending = wait_until(lambda: (value if (value := client.status())["state"] == "awaiting_confirmation" else None), label="local binding confirmation")
            assert not pending["enabled"] and not pending["bound"]
            assert not any(item["path"].endswith("getupdates") for item in fixture.requests)
            bound = client.rpc("weixin.bind", expected_revision=pending["revision"], conversation_id=original)
            assert bound["bound"] and bound["enabled"] and bound["conversation_id"] == original
            wait_until(lambda: client.status()["state"] == "connected")
            for value in (client.status(), client.rpc("settings.get"), client.call("/api/health")):
                assert fixture.TOKEN not in json.dumps(value)
                assert "fixture-context" not in json.dumps(value)
            record(checks, "QR protocol, pending local confirmation, explicit binding and redacted status")

            desktop_text = "来自桌面的实验上下文"
            result = client.call(f"/api/sessions/{original}/messages", {"commandId": "desktop-initial", "text": desktop_text})
            assert result["accepted"]
            wait_until(lambda: any(item["role"] == "assistant" and desktop_text in item["text"] for item in client.history(original)))
            phone_text = "来自微信的实验进度问题"
            event_id = fixture.push(phone_text)
            fixture.wait_reply(phone_text)
            history = client.history(original)
            assert sum(item["role"] == "user" and item["text"] == phone_text for item in history) == 1
            assert any(item["role"] == "user" and item["text"] == desktop_text for item in history)
            receipt = next(item for item in records(home, "channel_weixin_events") if item["event_id"] == event_id)
            wait_until(lambda: any(item["event_id"] == event_id and item["state"] == "completed" for item in records(home, "channel_weixin_events")))
            command = next(item for item in records(home, "commands") if item["command_id"] == receipt["command_id"])
            assert command["conversation_id"] == original and command["state"] == "completed"
            assert any(item["command_id"] == command["command_id"] and item["status"] == "sent" for item in records(home, "channel_weixin_outbox"))
            record(checks, "desktop and WeChat share the same real DSH transcript and durable command/outbox receipts")

            sent_before = len(fixture.sent_texts())
            history_before = len(history)
            fixture.push(phone_text, identity=event_id)
            fixture.push("foreign sender cannot use Mirally", sender="stranger@im.wechat", identity="foreign")
            expected_cursor = str(len(fixture.batches))
            def cursor_reached():
                with sqlite3.connect(home / "framework/product.sqlite3") as connection:
                    return connection.execute("SELECT cursor FROM channel_weixin").fetchone()[0] == expected_cursor
            wait_until(cursor_reached)
            assert len(client.history(original)) == history_before
            assert len(fixture.sent_texts()) == sent_before
            assert not any(item["event_id"] == "foreign" for item in records(home, "channel_weixin_events"))
            assert len([item for item in records(home, "commands") if item["command_id"] == receipt["command_id"]]) == 1
            record(checks, "duplicate platform delivery and unrelated sender cause no second user turn or command")

            fixture.push("/new")
            fixture.wait_reply("已切换到")
            second = wait_until(lambda: (value if (value := client.status())["conversation_id"] != original else None))["conversation_id"]
            fixture.push("第二段手机会话")
            fixture.wait_reply("第二段手机会话")
            assert not any(item["text"] == "第二段手机会话" for item in client.history(original))
            before = len(fixture.sent_texts())
            fixture.push("/sessions")
            listed = fixture.wait_reply("发送 /use", after=before)
            short_original = original.removeprefix("conversation_")[:12]
            short_second = second.removeprefix("conversation_")[:12]
            assert short_original in listed and short_second in listed
            before = len(fixture.sent_texts())
            fixture.push("/use " + short_original)
            fixture.wait_reply("已切换到", after=before)
            wait_until(lambda: client.status()["conversation_id"] == original)
            assert any(item["text"] == phone_text for item in client.history(original))
            record(checks, "/new, /sessions and /use route real conversations without moving history")

            slow_text = "停止验收" + "较长的流式内容" * 180
            slow_id = fixture.push(slow_text)
            wait_until(lambda: any(item["role"] == "user" and item["text"] == slow_text for item in client.history(original)))
            wait_until(lambda: any(item["event_id"] == slow_id and item["state"] == "submitted" for item in records(home, "channel_weixin_events")))
            before = len(fixture.sent_texts())
            fixture.push("/stop")
            fixture.wait_reply("已停止当前会话", after=before)
            slow_event = next(item for item in records(home, "channel_weixin_events") if item["event_id"] == slow_id)
            wait_until(lambda: any(item["command_id"] == slow_event["command_id"] and item["state"] == "cancelled" for item in records(home, "commands")))
            record(checks, "/stop cancels an in-flight native model turn and records the stopped command")

            current = client.status()
            disconnected = client.rpc("weixin.disconnect", expected_revision=current["revision"])
            assert disconnected["bound"] and not disconnected["enabled"]
            before = len(fixture.sent_texts())
            reconnect_id = fixture.push("断线期间消息在重连后收到")
            time.sleep(.5)
            assert not any(item["event_id"] == reconnect_id for item in records(home, "channel_weixin_events"))
            client.rpc("weixin.reconnect", expected_revision=disconnected["revision"])
            fixture.wait_reply("断线期间消息在重连后收到", after=before)
            record(checks, "disconnect retains the route, and reconnect resumes only after explicit local action")

            history_before = len(client.history(original))
            host.close()
            host = FrameworkHost(root, home, test_mode=True)
            client = Client(host.start())
            wait_until(lambda: client.status()["state"] == "connected")
            assert client.status()["conversation_id"] == original
            assert len(client.history(original)) == history_before
            before = len(fixture.sent_texts())
            fixture.push("冷重启后的微信对话")
            fixture.wait_reply("冷重启后的微信对话", after=before)
            record(checks, "cold restart restores binding, cursor and unique native history without replay")

            current = client.status()
            unbound = client.rpc("weixin.unbind", expected_revision=current["revision"])
            assert not unbound["bound"] and not unbound["enabled"] and unbound["peer_id"] is None
            assert fixture.TOKEN not in json.dumps(unbound)
            ignored = fixture.push("解绑后的消息不得进入产品")
            time.sleep(.5)
            assert not any(item["event_id"] == ignored for item in records(home, "channel_weixin_events"))
            assert any(value["id"] == original for value in client.call("/api/sessions")["sessions"])
            assert len(client.history(original)) >= history_before
            with sqlite3.connect(home / "framework/product.sqlite3") as connection:
                assert connection.execute("SELECT token FROM channel_weixin").fetchone()[0] is None
            record(checks, "unbind erases credentials and stops ingress while preserving user conversations")
            return checks
        except BaseException:
            # Retained homes aid local diagnosis without dumping private state.
            print("Acceptance failed; inspect " + str(home / "logs/host.log"), file=sys.stderr)
            raise
        finally:
            host.close()
            fixture.close()


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--home", type=Path, help="Fresh isolated home; retained after the run")
    parser.add_argument("--report", type=Path, help="Write a credential-free JSON acceptance report")
    args = parser.parse_args()
    if args.home:
        home = args.home.resolve()
        if home.exists() and any(home.iterdir()):
            parser.error("--home must be empty and isolated")
        checks = verify(args.root.resolve(), home)
    else:
        with tempfile.TemporaryDirectory(prefix="mirally-weixin-acceptance-") as directory:
            checks = verify(args.root.resolve(), Path(directory) / "个人 home")
    report = {"passed": True, "mode": "built Host + real DSH explicit offline model + loopback iLink",
        "external_wechat_account": False, "external_messages_sent": False, "gui_started": False,
        "checks": checks, "not_covered": ["Real WeChat account/phone scan and service permissions",
            "Real model API or forced malicious model tool calls (explicit offline model emits text only)"]}
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
