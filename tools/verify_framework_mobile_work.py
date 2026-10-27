"""Real Host/DSH/sidecar mobile-work acceptance in an isolated home.

No external model, WeChat account, public listener or user's files are used.
The optional browser check runs serially against the same isolated instance.
"""
from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from mirally.framework_host import FrameworkHost


class Client:
    def __init__(self, url, token=""):
        self.url, self.token = url.rstrip("/"), token

    def call(self, path, payload=None):
        request = urllib.request.Request(self.url + path,
            data=None if payload is None else json.dumps(payload, ensure_ascii=False).encode(),
            headers={"Content-Type": "application/json", **({"Authorization": "Bearer " + self.token} if self.token else {})})
        try:
            with urllib.request.urlopen(request, timeout=35) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            error.msg += ": " + error.read(4096).decode("utf-8", "replace")
            raise

    def rpc(self, method, **params):
        return self.call("/api/mobile/rpc" if getattr(self, "mobile", False) else "/api/rpc", {"method": method, "params": params})["result"]


def wait(fn, timeout=35):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = fn()
        if value:
            return value
        time.sleep(.1)
    raise AssertionError("acceptance condition timed out")


def verify(root, home, browser=False):
    checks = []
    def record(message):
        checks.append(message)
        print("PASS " + message, flush=True)

    host = FrameworkHost(root, home, test_mode=True)
    try:
        runtime = host.start()
        desktop = Client(runtime["url"], runtime["token"])
        status = desktop.rpc("mobile.status")
        assert status["ready"] and status["url"].startswith("http://127.0.0.1:")
        base = status["url"].removesuffix("/mobile")
        issued = desktop.rpc("mobile.pair.create", device_name="隔离验收手机")
        pair = Client(base).call("/api/mobile/pair", {"code": issued["code"], "device_name": "隔离验收手机"})["result"]
        phone = Client(base, pair["token"])
        phone.mobile = True
        assert phone.rpc("mobile.status")["ready"]
        record("separate loopback mobile surface pairs without sharing the desktop token")

        workspace = home / "workspace"
        workspace.mkdir(exist_ok=True)
        (workspace / "实验状态.json").write_text(json.dumps({"state": "running", "summary": "第 42 轮，验证正常", "progress": {"current": 42, "total": 100}, "metrics": {"loss": .183}}, ensure_ascii=False), encoding="utf-8")
        observed = phone.rpc("work.create", title="外部实验关注", goal="只读了解现有实验",
            source={"kind": "json", "root_id": "workspace", "path": "实验状态.json"}, request_id="observe-request")["work"]
        observation = phone.rpc("work.observe", work_id=observed["id"])
        assert observation["observation"]["state"] == "running"
        assert observation["observation"]["observed_at"]
        assert len(desktop.rpc("work.list")["works"]) == 1
        record("mobile-created observed work shares desktop state with real source timestamps")

        prefs = phone.rpc("mobile.preferences.get")
        assert prefs["effective"]["home"] == "work"
        prefs = phone.rpc("mobile.preferences.set", scope="user", expected_revision=prefs["revision"], patch={"home": "conversations"})
        prefs = phone.rpc("mobile.preferences.set", scope="device", expected_revision=prefs["revision"], patch={"home": "work", "navigation": ["conversations", "work", "inbox", "settings"], "hidden": ["inbox"]})
        assert prefs["effective"]["home"] == "work" and prefs["user"]["home"] == "conversations"
        record("user defaults, device overrides and navigation remain presentation settings")

        conversation = phone.rpc("conversation.create", title="手机与桌面接续", request_id="same-conversation")["conversation"]
        cid = conversation["conversation_id"]
        assert phone.rpc("conversation.create", title="手机与桌面接续", request_id="same-conversation")["conversation"]["conversation_id"] == cid
        command = phone.rpc("conversation.send", conversation_id=cid, text="验证手机接续同一会话", command_id="mobile-inference")
        history = wait(lambda: (v if (v := phone.rpc("conversation.history", conversation_id=cid)).get("messages") and not v.get("active_command_ids") and any(m["role"] == "assistant" for m in v["messages"]) else None))
        assert any("验证手机接续" in m.get("text", "") for m in history["messages"])
        desktop_history = desktop.call(f"/api/sessions/{cid}/history")
        assert any("验证手机接续" in m.get("text", "") for m in desktop_history["messages"])
        conversation = next(v for v in phone.rpc("conversation.list")["conversations"] if v["conversation_id"] == cid)
        conversation = phone.rpc("conversation.update", conversation_id=cid, expected_version=conversation["metadata_version"], patch={"title": "手机改名", "pinned": True})["conversation"]
        conversation = phone.rpc("conversation.update", conversation_id=cid, expected_version=conversation["metadata_version"], patch={"archived": True})["conversation"]
        phone.rpc("conversation.update", conversation_id=cid, expected_version=conversation["metadata_version"], patch={"archived": False})
        record("real DSH history is shared; phone rename, pin, archive and restore use the original owner")

        capabilities = desktop.rpc("capabilities.get")
        desktop.rpc("capabilities.configure", expected_revision=capabilities["revision"], patch={"workspace_write_enabled": True})
        (workspace / "处理 脚本.py").write_text("from pathlib import Path\nimport time\nprint('working', flush=True)\ntime.sleep(.3)\nPath('结果 文件.txt').write_text('实际成果', encoding='utf-8')\nprint('finished', flush=True)\n", encoding="utf-8")
        work = phone.rpc("work.create", title="手机控制脚本", goal="生成可下载的真实结果", request_id="script-work")["work"]
        work_cid = work["conversation_id"]
        assert desktop.call(f"/api/sessions/{work_cid}/messages", {"commandId":"work-activity-check", "text":"测试真实对话活动" * 80})["accepted"]
        active = wait(lambda: (w if (w := desktop.rpc("work.get", work_id=work["id"])["work"])["conversation_activity"] == "running" else None))
        assert active["status"] == "idle" and active["latest_run"] is None
        desktop.call(f"/api/sessions/{work_cid}/cancel", {"commandId":"work-activity-check"})
        wait(lambda: desktop.rpc("work.get", work_id=work["id"])["work"]["conversation_activity"] == "idle")
        record("work reads real DSH conversation activity separately from script and source status")
        approval = desktop.rpc("work.run.prepare", work_id=work["id"], script="处理 脚本.py", arguments=[], cwd=".", output_paths=["结果 文件.txt"], timeout_seconds=15, request_id="prepared-on-desktop")["approval"]
        approved = phone.rpc("work.run.approve", approval_id=approval["id"], expected_digest=approval["digest"])
        started = phone.rpc("work.run.start", approval_id=approval["id"], approval_token=approved["approval_token"], request_id="single-start")["run"]
        replay = phone.rpc("work.run.start", approval_id=approval["id"], approval_token=approved["approval_token"], request_id="single-start")["run"]
        assert replay["id"] == started["id"]
        run = wait(lambda: (r if (r := phone.rpc("work.run.get", run_id=started["id"])["run"])["status"] not in {"starting", "running", "cancelling"} else None))
        assert run["status"] == "completed", run
        logs = phone.rpc("work.run.logs", run_id=run["id"])
        assert "finished" in logs["text"]
        artifact = phone.rpc("work.run.artifact", run_id=run["id"], path="结果 文件.txt")
        assert base64.b64decode(artifact["data"]).decode() == "实际成果"
        record("desktop plan is precisely approved on phone, runs once, and returns real logs and artifacts")

        (workspace / "慢任务.py").write_text("import time\nprint('alive', flush=True)\ntime.sleep(40)\n", encoding="utf-8")
        slow = phone.rpc("work.run.prepare", work_id=work["id"], script="慢任务.py", arguments=[], cwd=".", output_paths=[], timeout_seconds=60, request_id="slow-plan")["approval"]
        grant = phone.rpc("work.run.approve", approval_id=slow["id"], expected_digest=slow["digest"])
        running = phone.rpc("work.run.start", approval_id=slow["id"], approval_token=grant["approval_token"], request_id="slow-start")["run"]
        phone.rpc("work.run.cancel", run_id=running["id"])
        cancelled = wait(lambda: (r if (r := phone.rpc("work.run.get", run_id=running["id"])["run"])["status"] not in {"running", "starting", "cancelling"} else None))
        assert cancelled["status"] == "cancelled", cancelled
        record("phone cancellation reaches the real supervised process")

        inbox = phone.rpc("inbox.list")
        assert any(item.get("kind") == "work" for item in inbox["items"])
        assert desktop.rpc("work.settings.get")["settings"]["channels"]["weixin"] is False
        record("work notices reach the product inbox; external WeChat notifications remain opt-in")

        saved_url = status["url"]
        host.close()
        host = FrameworkHost(root, home, test_mode=True)
        runtime = host.start()
        desktop = Client(runtime["url"], runtime["token"])
        assert desktop.rpc("mobile.status")["url"] == saved_url
        assert phone.rpc("work.run.get", run_id=run["id"])["run"]["status"] == "completed"
        assert phone.rpc("mobile.preferences.get")["effective"]["navigation"][0] == "conversations"
        record("cold restart preserves the private-proxy port, paired phone, work records and preferences")

        if browser:
            probe = home / "mobile-browser-runtime.json"
            probe.write_text(json.dumps({**runtime, "mobile_url": saved_url, "mobile_token": pair["token"], "work_id": work["id"], "conversation_id": cid}), encoding="utf-8")
            subprocess.run(["node", str(root / "tools/verify_framework_mobile_browser.mjs"), str(probe)], cwd=root, check=True)
            record("real built desktop and phone pages passed browser acceptance")

        desktop.rpc("mobile.devices.revoke", device_id=pair["device"]["id"])
        try:
            phone.rpc("conversation.list")
        except urllib.error.HTTPError as error:
            assert error.code == 401
        else:
            raise AssertionError("revoked phone retained access")
        record("revoking the phone removes its access without deleting conversations or work")
        return checks
    finally:
        host.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, help="Fresh isolated home to retain after verification")
    parser.add_argument("--browser", action="store_true")
    args = parser.parse_args()
    if args.home:
        args.home.mkdir(parents=True, exist_ok=True)
        if (args.home / "runtime.json").exists():
            parser.error("use a fresh isolated home")
        checks = verify(ROOT, args.home.resolve(), args.browser)
    else:
        with tempfile.TemporaryDirectory(prefix="mirally-mobile-work 中文 ") as directory:
            checks = verify(ROOT, Path(directory), args.browser)
    print(json.dumps({"passed": len(checks), "checks": checks}, ensure_ascii=False))


if __name__ == "__main__":
    main()
