"""Real SQLite, bounded files and actual Python process supervision, offline."""
from __future__ import annotations

import base64
import concurrent.futures
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
import uuid
from unittest.mock import patch

from mirally.framework import FrameworkError, FrameworkService


class WorkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="Mirally 工作 中文 ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.now = 1800000000000
        self.service = FrameworkService(self.root, self.root / "product home", clock=lambda: self.now)
        self.addCleanup(lambda: self.service.close())
        self.workspace = self.service.home / "workspace"
        self.workspace.mkdir(exist_ok=True)
        self.service.call("capabilities.configure", {"expected_revision": 1, "patch": {"workspace_write_enabled": True}})

    def call(self, name, **params):
        return self.service.call("work." + name, params)

    def work(self, **params):
        return self.call("create", title="训练实验 A", **params)["work"]

    def script(self, content, name="实验 script.py"):
        (self.workspace / name).write_text(content, encoding="utf-8")
        return name

    def approval(self, work=None, content="print('完成')", **params):
        work = work or self.work()
        script = self.script(content)
        return self.call("run.prepare", work_id=work["id"], script=script, request_id="prepare-" + uuid.uuid4().hex, **params)["approval"]

    def start(self, approval, request_id="start", actor="desktop"):
        confirmed = self.call("run.approve", approval_id=approval["id"], expected_digest=approval["digest"], actor=actor)
        return self.call("run.start", approval_id=approval["id"], approval_token=confirmed["approval_token"], request_id=request_id, actor=actor)["run"], confirmed["approval_token"]

    def wait(self, run):
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            current = self.call("run.get", run_id=run["id"])["run"]
            if current["status"] not in {"starting", "running", "cancelling"}: return current
            time.sleep(.04)
        self.fail("real process did not settle")

    def test_work_references_existing_conversation_and_versioned_updates(self):
        conversation = self.service.call("conversation.ensure", {"title": "原会话"})
        work = self.work(conversation_id=conversation["conversation_id"], request_id="one")
        self.assertEqual(work["conversation_id"], conversation["conversation_id"])
        self.assertEqual(self.work(conversation_id=conversation["conversation_id"], request_id="one")["id"], work["id"])
        with self.assertRaises(FrameworkError): self.call("create", title="different", request_id="one")
        changed = self.call("update", work_id=work["id"], expected_revision=1, patch={"intent": "archived"})["work"]
        self.assertEqual(changed["status"], "archived")
        self.assertFalse(self.call("list")["works"])
        self.assertEqual(len(self.call("list", include_archived=True)["works"]), 1)
        with self.assertRaises(FrameworkError): self.call("update", work_id=work["id"], expected_revision=1, patch={"title": "old"})

    def test_json_observation_has_time_evidence_progress_and_not_control(self):
        (self.workspace / "status.json").write_text(json.dumps({"state": "running", "summary": "第 4 轮", "progress": {"current": 4, "total": 10}, "metrics": {"loss": .3}, "artifacts": ["../secret"]}), encoding="utf-8")
        work = self.work(source={"kind": "json", "path": "status.json"})
        observed = self.call("observe", work_id=work["id"])["observation"]
        self.assertEqual(observed["state"], "running")
        self.assertEqual(observed["observed_at"], self.now)
        self.assertIsInstance(observed["source_updated_at"], int)
        self.assertEqual(observed["progress"]["current"], 4)
        self.assertFalse(observed["controllable"])
        self.assertFalse(observed["artifacts"])
        self.assertEqual(self.call("get", work_id=work["id"])["runs"], [])

    def test_external_read_root_and_revocation(self):
        external = self.root / "外部实验"; external.mkdir()
        (external / "progress.log").write_text("epoch 6\n", encoding="utf-8")
        current = self.service.call("capabilities.get")
        self.service.call("capabilities.configure", {"expected_revision": current["revision"], "patch": {"read_roots": [str(external)]}})
        roots = self.service.call("workspace.roots")["roots"]
        root_id = next(r["id"] for r in roots if r["id"] != "workspace")
        work = self.work(source={"kind": "log", "root_id": root_id, "path": "progress.log"})
        self.assertIn("epoch 6", self.call("observe", work_id=work["id"])["observation"]["summary"])
        current = self.service.call("capabilities.get")
        self.service.call("capabilities.configure", {"expected_revision": current["revision"], "patch": {"read_roots": []}})
        cached = self.call("get", work_id=work["id"])["work"]["observation"]
        self.assertTrue(cached["withdrawn"])
        self.assertNotIn("epoch 6", cached["summary"])
        self.assertEqual(self.call("observe", work_id=work["id"])["observation"]["state"], "unknown")

    def test_observation_bounded_log_and_json_malformed(self):
        (self.workspace / "big.log").write_text("x" * 20000, encoding="utf-8")
        work = self.work(source={"kind": "log", "path": "big.log"})
        observation = self.call("observe", work_id=work["id"])["observation"]
        self.assertTrue(observation["truncated"]); self.assertEqual(len(observation["summary"]), 16000)
        (self.workspace / "bad.json").write_text("[1,2]", encoding="utf-8")
        work = self.work(source={"kind": "json", "path": "bad.json"})
        self.assertEqual(self.call("observe", work_id=work["id"])["observation"]["state"], "unknown")

    def test_observation_completion_event_once_and_hostile_numbers_bounded(self):
        path = self.workspace / "status.json"
        path.write_text(json.dumps({"state": "completed", "metrics": {"huge": 10**400, "nan": float("nan")}, "progress": {"current": 10**400, "total": 10**401}}))
        work = self.work(source={"kind": "json", "path": "status.json"})
        observation = self.call("observe", work_id=work["id"])["observation"]
        self.assertEqual(observation["metrics"], {})
        self.assertNotIn("progress", observation)
        self.call("update", work_id=work["id"], expected_revision=1, patch={"title": "renamed"})
        self.call("observe", work_id=work["id"])
        events = self.call("notifications", channel="inbox")["events"]
        self.assertEqual(len(events), 1)

    def test_invalid_json_state_overwrites_previous_completion_with_unknown(self):
        path = self.workspace / "status.json"
        path.write_text(json.dumps({"state": "completed", "summary": "old completed"}))
        work = self.work(source={"kind": "json", "path": "status.json"})
        self.call("observe", work_id=work["id"])
        for invalid in ([], {}, None, 1, True, "unsupported"):
            with self.subTest(state=invalid):
                path.write_text(json.dumps({"state": invalid, "summary": "invalid"}))
                result = self.call("observe", work_id=work["id"])
                self.assertEqual(result["observation"]["state"], "unknown")
                self.assertIn("state", result["observation"]["summary"])
                self.assertEqual(result["work"]["status"], "unknown")
                self.assertNotIn("old completed", self.call("get", work_id=work["id"])["work"]["observation"]["summary"])

    def test_run_history_paginates_and_list_omits_large_execution_spec(self):
        approval = self.approval()
        run, _ = self.start(approval)
        self.wait(run)
        listing = self.call("list")["works"][0]
        self.assertNotIn("spec", listing["latest_run"])
        detail = self.call("get", work_id=approval["work_id"], run_limit=1)
        self.assertEqual(detail["next_run_offset"], 1)
        self.assertFalse(detail["has_more_runs"])
        self.assertIn("spec", detail["runs"][0])

    def test_source_symlink_and_outside_paths_rejected(self):
        outside = self.root / "private.json"; outside.write_text("{}")
        with self.assertRaises(FrameworkError): self.work(source={"kind": "json", "path": "../../private.json"})
        link = self.workspace / "link.json"
        try: link.symlink_to(outside)
        except OSError: self.skipTest("Windows symlink privilege unavailable")
        with self.assertRaises(FrameworkError): self.work(source={"kind": "json", "path": "link.json"})

    @unittest.skipUnless(os.name == "nt", "Windows junction validation")
    def test_actual_windows_junction_denied_for_source_and_script(self):
        import _winapi
        external = self.root / "junction target"; external.mkdir()
        (external / "status.json").write_text("{}")
        (external / "run.py").write_text("print('should not run')")
        junction = self.workspace / "junction"
        _winapi.CreateJunction(str(external), str(junction))
        try:
            with self.assertRaises(FrameworkError): self.work(source={"kind": "json", "path": "junction/status.json"})
            work = self.work()
            with self.assertRaises(FrameworkError): self.call("run.prepare", work_id=work["id"], script="junction/run.py", request_id="junction")
        finally:
            junction.rmdir()

    def test_explicit_close_stops_active_run_and_does_not_leave_child(self):
        run, _ = self.start(self.approval(content="import time\nprint('started',flush=True)\ntime.sleep(60)"))
        self.service.close()
        self.service = FrameworkService(self.root, self.root / "product home")
        self.assertEqual(self.call("run.get", run_id=run["id"])["run"]["status"], "cancelled")

    def test_start_requires_exact_actor_digest_token_and_current_script(self):
        approval = self.approval(actor="weixin:one:1")
        self.assertNotIn("token", approval)
        with self.assertRaises(FrameworkError): self.call("run.approve", approval_id=approval["id"], expected_digest="bad", actor="weixin:one:1")
        confirmed = self.call("run.approve", approval_id=approval["id"], expected_digest=approval["digest"], actor="weixin:one:1")
        with self.assertRaises(FrameworkError): self.call("run.approve", approval_id=approval["id"], expected_digest=approval["digest"], actor="mobile:other")
        with self.assertRaises(FrameworkError): self.call("run.start", approval_id=approval["id"], approval_token="fake", request_id="start", actor="weixin:one:1")
        self.script("print('changed')")
        with self.assertRaises(FrameworkError): self.call("run.start", approval_id=approval["id"], approval_token=confirmed["approval_token"], request_id="start", actor="weixin:one:1")

    def test_owner_can_review_pending_approval_on_phone_but_token_is_device_bound(self):
        approval = self.approval(actor="desktop")
        confirmed = self.call("run.approve", approval_id=approval["id"], expected_digest=approval["digest"], actor="mobile:paired-owner-phone")
        self.assertEqual(confirmed["approval"]["prepared_actor"], "desktop")
        self.assertEqual(confirmed["approval"]["approved_actor"], "mobile:paired-owner-phone")
        with self.assertRaises(FrameworkError): self.call("run.start", approval_id=approval["id"], approval_token=confirmed["approval_token"], request_id="cross-device", actor="desktop")
        run = self.call("run.start", approval_id=approval["id"], approval_token=confirmed["approval_token"], request_id="phone-start", actor="mobile:paired-owner-phone")["run"]
        self.assertEqual(self.wait(run)["status"], "completed")

    def test_approval_expires_and_work_changes_invalidate(self):
        approval = self.approval()
        self.now += 300001
        with self.assertRaises(FrameworkError): self.start(approval)
        approval = self.approval()
        self.call("update", work_id=approval["work_id"], expected_revision=1, patch={"title": "new"})
        with self.assertRaises(FrameworkError): self.start(approval)

    def test_prepare_idempotence_changed_arguments_conflict(self):
        work = self.work(); script = self.script("print('x')")
        params = dict(work_id=work["id"], script=script, request_id="prepare", arguments=["literal $(command)"])
        approval = self.call("run.prepare", **params)["approval"]
        self.assertEqual(self.call("run.prepare", **params)["approval"]["id"], approval["id"])
        params["arguments"] = ["changed"]
        with self.assertRaises(FrameworkError): self.call("run.prepare", **params)

    def test_real_completion_artifact_log_and_start_idempotence(self):
        approval = self.approval(content="from pathlib import Path\nimport sys\nprint(sys.argv[1], flush=True)\nPath('result.txt').write_text('真实结果',encoding='utf-8')", arguments=["参数 with spaces"], output_paths=["result.txt"])
        run, token = self.start(approval)
        completed = self.wait(run)
        self.assertEqual(completed["status"], "completed", completed)
        self.assertEqual(completed["exit_code"], 0)
        again = self.call("run.start", approval_id=approval["id"], approval_token=token, request_id="start")["run"]
        self.assertEqual(again["id"], run["id"])
        with self.assertRaises(FrameworkError): self.call("run.start", approval_id=approval["id"], approval_token=token, request_id="different")
        self.assertIn("参数 with spaces", self.call("run.logs", run_id=run["id"])["text"])
        artifact = self.call("run.artifact", run_id=run["id"], path="result.txt")
        self.assertEqual(base64.b64decode(artifact["data"]).decode("utf-8"), "真实结果")
        (self.workspace / "result.txt").write_text("changed")
        with self.assertRaises(FrameworkError): self.call("run.artifact", run_id=run["id"], path="result.txt")
        with self.assertRaises(FrameworkError): self.call("run.artifact", run_id=run["id"], path="not-declared.txt")

    def test_approved_bytes_are_immutable_after_launch_path_replacement(self):
        import mirally.framework.work as module
        approval = self.approval(content="from pathlib import Path\nPath('approved-result').write_text('approved')\nprint('APPROVED')")
        original_launch = module.launch
        def replace_before_interpreter(command, cwd, **options):
            # Reproduces the hash/launch race: the original path has different
            # content before the child interpreter is even created.
            self.script("from pathlib import Path\nPath('unapproved-result').write_text('bad')\nprint('UNAPPROVED')")
            return original_launch(command, cwd, **options)
        with patch.object(module, "launch", side_effect=replace_before_interpreter):
            run, _ = self.start(approval)
        self.assertEqual(self.wait(run)["status"], "completed")
        self.assertTrue((self.workspace / "approved-result").is_file())
        self.assertFalse((self.workspace / "unapproved-result").exists())
        self.assertIn("APPROVED", self.call("run.logs", run_id=run["id"])["text"])

    def test_large_snapshot_preserves_script_namespace_imports_arguments_and_stdin(self):
        folder = self.workspace / "子目录 scripts"; folder.mkdir()
        (folder / "sibling.py").write_text("VALUE = '同目录导入'", encoding="utf-8")
        content = ("#" + "x" * 100000 + "\nimport __main__, sys\nfrom pathlib import Path\nimport sibling\n"
                   "VALUE = 'main module'\nassert __main__.VALUE == VALUE\nassert __name__ == '__main__'\n"
                   "assert Path(__file__).name == '运行.py'\nassert sys.argv[0] == __file__\n"
                   "assert sys.argv[1:] == ['参数 空格']\nassert sys.stdin.read() == ''\n"
                   "print(sibling.VALUE, flush=True)\n")
        file = folder / "运行.py"; file.write_text(content, encoding="utf-8")
        work = self.work()
        approval = self.call("run.prepare", work_id=work["id"], script="子目录 scripts/运行.py", request_id="large", arguments=["参数 空格"])["approval"]
        run, _ = self.start(approval)
        finished = self.wait(run)
        self.assertEqual(finished["status"], "completed", self.call("run.logs", run_id=run["id"])["text"])
        self.assertIn("同目录导入", self.call("run.logs", run_id=run["id"])["text"])

    def test_real_cancel_and_close_kill_process_tree(self):
        content = "import subprocess,sys,time\nfrom pathlib import Path\np=subprocess.Popen([sys.executable,'-c',\"import time;from pathlib import Path;time.sleep(3);Path('child-survived').write_text('bad')\"])\nPath('started').write_text(str(p.pid))\nprint('running',flush=True)\ntime.sleep(60)"
        run, _ = self.start(self.approval(content=content))
        deadline = time.monotonic() + 5
        while not (self.workspace / "started").exists() and time.monotonic() < deadline: time.sleep(.03)
        self.assertTrue((self.workspace / "started").exists())
        stopped = self.call("run.cancel", run_id=run["id"])["run"]
        self.assertIn(stopped["status"], {"cancelling", "cancelled"})
        self.assertEqual(self.wait(run)["status"], "cancelled")
        time.sleep(3.1)
        self.assertFalse((self.workspace / "child-survived").exists())

    def test_timeout_failure_and_bounded_output(self):
        run, _ = self.start(self.approval(content="import time\nprint('before timeout',flush=True)\ntime.sleep(10)", timeout_seconds=1))
        self.assertEqual(self.wait(run)["status"], "timed_out")
        run, _ = self.start(self.approval(content="import sys\nsys.stdout.write('x'*1200000)\nsys.exit(2)"), request_id="second")
        result = self.wait(run)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["log_bytes"], 1024*1024); self.assertTrue(result["log_truncated"])

    def test_missing_write_grant_and_script_escape_rejected(self):
        work = self.work(); script = self.script("print('x')")
        with self.assertRaises(FrameworkError): self.call("run.prepare", work_id=work["id"], script="../outside.py", request_id="outside")
        self.service.call("capabilities.configure", {"expected_revision": 2, "patch": {"workspace_write_enabled": False}})
        with self.assertRaises(FrameworkError): self.call("run.prepare", work_id=work["id"], script=script, request_id="no-grant")

    def test_approval_lost_on_restart_and_active_run_is_unknown_no_replay(self):
        approval = self.approval()
        with self.service._transaction():
            self.service.db.execute("INSERT INTO work_runs(id,work_id,approval_id,request_id,status,created_at,spec_json) VALUES('run_crashed',?,?,?,'running',?,?)", (approval["work_id"], approval["id"], "crashed", self.now, json.dumps(approval["spec"])))
        self.service.close()
        self.service = FrameworkService(self.root, self.root / "product home", clock=lambda: self.now)
        self.assertEqual(self.call("run.get", run_id="run_crashed")["run"]["status"], "unknown")
        with self.assertRaises(FrameworkError): self.start(approval)
        self.assertFalse(self.service.work.active)

    def test_schema4_migration_preserves_conversation(self):
        work = self.work()
        conversation_id = work["conversation_id"]
        self.service.close()
        db = sqlite3.connect(self.root / "product home" / "framework" / "product.sqlite3")
        db.execute("PRAGMA foreign_keys=OFF")
        for table in ("work_events", "work_runs", "work_approvals", "works"): db.execute("DROP TABLE " + table)
        db.execute("PRAGMA user_version=4"); db.commit(); db.close()
        self.service = FrameworkService(self.root, self.root / "product home")
        self.assertEqual(self.service.call("system.status")["schema_version"], 5)
        self.assertEqual(self.service.call("conversation.get", {"conversation_id": conversation_id})["title"], "训练实验 A")
        self.assertEqual(self.call("list")["works"], [])

    def test_notifications_opt_in_dedup_and_selection_revision(self):
        approval = self.approval()
        self.assertFalse(self.call("notifications")["events"])
        self.call("settings.update", expected_revision=1, patch={"channels": {"weixin": True}})
        events = self.call("notifications")["events"]
        self.assertEqual(len(events), 1)
        self.call("notifications.ack", event_id=events[0]["event_id"], channel="weixin", status="unknown")
        self.assertFalse(self.call("notifications")["events"])
        self.call("channel.selection.set", channel="weixin", route_id="installation", revision=1, work_id=approval["work_id"])
        self.assertEqual(self.call("channel.selection.get", channel="weixin", route_id="installation", revision=1)["work_id"], approval["work_id"])
        self.assertIsNone(self.call("channel.selection.get", channel="weixin", route_id="installation", revision=2)["work_id"])

    def bound_weixin(self):
        saved = self.service.call("channel.weixin.save", {"token": "offline-test", "peer_id": "peer", "bot_id": "bot", "base_url": "https://ilinkai.weixin.qq.com"})["state"]
        return self.service.call("channel.weixin.bind", {"expected_revision": saved["revision"]})["state"]

    def test_notification_claim_pins_destination_across_restart_rebind_and_terminal_ack(self):
        self.approval()
        self.call("settings.update", expected_revision=1, patch={"channels": {"weixin": True}})
        route = self.bound_weixin()
        dest = {"installation_id": route["installation_id"], "revision": route["revision"]}
        event = self.call("notifications", **dest)["events"][0]
        claim = self.call("notifications.claim", event_id=event["event_id"], channel="weixin", **dest)
        self.assertTrue(claim["should_enqueue"])
        self.assertFalse(self.call("notifications.claim", event_id=event["event_id"], channel="weixin", **dest)["should_enqueue"])
        self.assertEqual(len(self.call("notifications", **dest)["events"]), 1)
        self.service.close()
        self.service = FrameworkService(self.root, self.root / "product home", clock=lambda: self.now)
        self.assertEqual(self.call("notifications.get", event_id=event["event_id"])["event"]["deliveries"]["weixin"], {"status": "queued", **dest})
        newer = self.bound_weixin()
        new_dest = {"installation_id": newer["installation_id"], "revision": newer["revision"]}
        self.assertFalse(self.call("notifications", **new_dest)["events"])
        self.assertFalse(self.call("notifications.claim", event_id=event["event_id"], channel="weixin", **new_dest)["should_enqueue"])
        with self.assertRaises(FrameworkError): self.call("notifications.ack", event_id=event["event_id"], channel="weixin", status="sent", **new_dest)
        receipt = self.call("notifications.ack", event_id=event["event_id"], channel="weixin", status="unknown", **dest)["event"]["deliveries"]["weixin"]
        self.assertEqual(receipt, {"status": "unknown", **dest})
        self.call("notifications.ack", event_id=event["event_id"], channel="weixin", status="sent", **dest)
        self.assertEqual(self.call("notifications.get", event_id=event["event_id"])["event"]["deliveries"]["weixin"]["status"], "unknown")
        self.assertFalse(self.call("notifications", **dest)["events"])

    def test_notification_claim_checks_live_route_and_current_preferences(self):
        self.approval()
        self.call("settings.update", expected_revision=1, patch={"channels": {"weixin": True}})
        event = self.call("notifications")["events"][0]
        with self.assertRaises(FrameworkError): self.call("notifications.claim", event_id=event["event_id"], channel="weixin", installation_id="unbound", revision=1)
        route = self.bound_weixin()
        self.call("settings.update", expected_revision=2, patch={"channels": {"weixin": False}})
        refused = self.call("notifications.claim", event_id=event["event_id"], channel="weixin", installation_id=route["installation_id"], revision=route["revision"])
        self.assertFalse(refused["should_enqueue"])
        self.assertFalse(refused["event"]["eligible_channels"]["weixin"])

    def test_notification_claim_is_atomic_and_source_change_revokes_queued_delivery(self):
        self.approval()
        self.call("settings.update", expected_revision=1, patch={"channels": {"weixin": True}})
        route = self.bound_weixin()
        dest = {"installation_id": route["installation_id"], "revision": route["revision"]}
        event = self.call("notifications", **dest)["events"][0]
        def claim(): return self.call("notifications.claim", event_id=event["event_id"], channel="weixin", **dest)["should_enqueue"]
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool: outcomes = list(pool.map(lambda _: claim(), range(8)))
        self.assertEqual(sum(outcomes), 1)
        self.call("update", work_id=event["work_id"], expected_revision=1, patch={"source": None})
        record = self.call("notifications.get", event_id=event["event_id"])["event"]
        self.assertEqual(record["deliveries"]["weixin"], {"status": "suppressed", **dest})
        self.assertFalse(self.call("notifications", **dest)["events"])

    def test_notification_projection_cached_per_work_and_skips_acknowledged_rows(self):
        work = self.work()
        with self.service._transaction():
            for n in range(10): self.service.work._event(work["id"], "completed", "done", str(n))
        with patch.object(self.service.work, "_work", wraps=self.service.work._work) as reader:
            events = self.call("notifications", channel="inbox")["events"]
            self.assertEqual(reader.call_count, 1)
        for event in events: self.call("notifications.ack", event_id=event["event_id"], channel="inbox", status="sent")
        with patch.object(self.service.work, "_work", wraps=self.service.work._work) as reader:
            self.assertFalse(self.call("notifications", channel="inbox")["events"])
            self.assertEqual(reader.call_count, 0)


if __name__ == "__main__": unittest.main()
