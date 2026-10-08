from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import time
import unittest
import urllib.request
from dataclasses import replace
from pathlib import Path

from openassist.core.activity.models import Actor, CollectionLevel, Observation
from openassist.core.activity.policy import ActivityPolicy
from openassist.platform.activity.foreground import ForegroundSnapshot
from openassist.platform.activity.ocr import _normalized_worker_reasons
from openassist.runtime.activity.monitor import (
    ActivityMonitorRuntime,
    MONITOR_HTML,
    _safe_observation,
    build_test_config,
)
from openassist.runtime.activity.service import ActivitySensor


class _FakeForeground:
    available = True
    _boot = "test-boot"

    def __init__(self) -> None:
        self.calls = 0

    def poll(self):
        self.calls += 1
        return ForegroundSnapshot(
            hwnd=100,
            pid=200,
            process_instance_id="proc_test",
            window_instance_id="window_test",
            app_id="test-app",
            observed_at_utc_ms=int(time.time() * 1000),
            monotonic_ns=time.monotonic_ns(),
            identity_confidence="test",
            boot_id=self._boot,
        )


class ActivityMonitorTests(unittest.TestCase):
    def test_safe_observation_redacts_ocr_text(self) -> None:
        value = Observation.create(
            kind="content_snapshot",
            source_id="test",
            source_epoch="epoch",
            source_seq=0,
            boot_id="boot",
            collection_level=CollectionLevel.CONTENT,
            actor=Actor.UNKNOWN,
            ocr_summary={"status": "ok", "text": "private text", "lines": [{"text": "private text"}]},
            visual_summary={"status": "ok", "path": "C:/private.png"},
        )
        safe = _safe_observation(value)
        self.assertNotIn("text", safe["ocr_summary"])
        self.assertTrue(safe["ocr_summary"]["text_available"])
        self.assertEqual(safe["ocr_summary"]["line_count"], 1)
        self.assertNotIn("path", safe["visual_summary"])
        revealed = _safe_observation(value, include_content=True)
        self.assertEqual(revealed["ocr_summary"]["text"], "private text")

    def test_test_config_is_isolated_and_content_is_explicit(self) -> None:
        root = Path(tempfile.mkdtemp())
        baseline = build_test_config(root, content=False)
        content = build_test_config(root, content=True, persist_images=True)
        self.assertTrue(baseline.enabled)
        self.assertEqual(baseline.database_path, (root / ".artifacts" / "activity-monitor" / "observations.sqlite3").resolve())
        self.assertEqual(baseline.policy.default_level, CollectionLevel.STRUCTURE)
        self.assertFalse(baseline.visual_enabled)
        self.assertEqual(content.policy.default_level, CollectionLevel.CONTENT)
        self.assertTrue(content.visual_enabled)
        self.assertTrue(content.visual_persist_images)

    def test_loopback_api_status_events_and_controls(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = build_test_config(root, content=False)
            config = replace(
                base,
                database_path=root / "db" / "observations.sqlite3",
                asset_directory=root / "db" / "assets",
                input_enabled=False,
                ui_structure_enabled=False,
            )
            runtime = ActivityMonitorRuntime(config, root=root)
            runtime.sensor = ActivitySensor(config, store=runtime.store, source=_FakeForeground(), input_source=None, uia_source=None)
            try:
                url = runtime.start(port=0)
                token = runtime.token

                with urllib.request.urlopen(url, timeout=3) as response:
                    page = response.read().decode("utf-8")
                    self.assertEqual(response.status, 200)
                    self.assertIn("Activity Monitor", page)
                    self.assertNotIn("__TOKEN__", page)
                    self.assertIn("events.slice().reverse()", page)
                    self.assertIn("body.insertBefore(tr,body.firstChild)", page)

                def get(path: str):
                    request = urllib.request.Request(
                        f"{url.split('/?')[0]}{path}",
                        headers={"Authorization": f"Bearer {token}"},
                    )
                    with urllib.request.urlopen(request, timeout=3) as response:
                        return response.status, json.loads(response.read().decode("utf-8"))

                status, payload = get("/api/status")
                self.assertEqual(status, 200)
                self.assertTrue(payload["health"]["available"])
                self.assertEqual(payload["collection_level"], CollectionLevel.STRUCTURE)
                self.assertEqual(len(payload["coverage"]["baseline"]), 5)
                self.assertIn("原始逐键字符流", payload["coverage"]["not_collected"])

                deadline = time.time() + 3
                events = {}
                while time.time() < deadline and not events.get("events"):
                    _, events = get("/api/events?after=0&limit=20")
                    time.sleep(0.05)
                self.assertTrue(events["events"])
                self.assertEqual(events["events"][0]["kind"], "foreground_changed")

                request = urllib.request.Request(
                    f"{url.split('/?')[0]}/api/control/pause",
                    method="POST",
                    headers={"Authorization": f"Bearer {token}"},
                )
                with urllib.request.urlopen(request, timeout=3) as response:
                    self.assertEqual(response.status, 200)
                self.assertTrue(runtime.sensor.metrics_snapshot()["paused"])

                request = urllib.request.Request(
                    f"{url.split('/?')[0]}/api/control/resume",
                    method="POST",
                    headers={"Authorization": f"Bearer {token}"},
                )
                with urllib.request.urlopen(request, timeout=3) as response:
                    self.assertEqual(response.status, 200)
                self.assertFalse(runtime.sensor.metrics_snapshot()["paused"])
            finally:
                runtime.close()

    def test_monitor_page_keeps_diagnostic_sections(self) -> None:
        self.assertIn("采集源诊断", MONITOR_HTML)
        self.assertIn("冻结视图", MONITOR_HTML)
        self.assertIn("最新优先", MONITOR_HTML)
        self.assertIn("grid-template-columns:repeat(3,minmax(0,1fr))", MONITOR_HTML)
        self.assertIn("height:clamp(300px,62vh,590px)", MONITOR_HTML)
        self.assertIn("black_frame_skips", MONITOR_HTML)
        self.assertIn("black_filter_errors", MONITOR_HTML)

    def test_monitor_timeline_preserves_newest_first_across_batches(self) -> None:
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node is not available for the monitor DOM regression")
        script = r'''
const vm = require("node:vm");
const assert = require("node:assert/strict");
const page = require("node:fs").readFileSync(0, "utf8");
function element() {
  return {children:[], _text:"", append(...items){this.children.push(...items);},
    get textContent(){return this._text;}, set textContent(value){this._text=value;this.children=[];},
    get firstChild(){return this.children[0];}, get lastChild(){return this.children.at(-1);},
    insertBefore(item,before){this.children.splice(before?this.children.indexOf(before):0,0,item);},
    removeChild(item){this.children.splice(this.children.indexOf(item),1);}};
}
const body = element();
const context = {$:()=>body, document:{createElement:element}, when:String, esc:String, showEvent:()=>{}};
vm.createContext(context);
for(const name of ["cell", "addRows"]) {
  const code = page.match(new RegExp("function " + name + "[^\\n]+"))[0];
  vm.runInContext(code, context);
}
function event(seq) {return {observed_at_utc_ms:seq,kind:"window_context",app_id:"test",payload:{window_title:"<title>"},quality:"complete"};}
context.addRows([event(1),event(2)],true);
context.addRows([event(3),event(4)],false);
assert.deepEqual(body.children.map(row=>row.children[0].textContent),["4","3","2","1"]);
assert.equal(body.children[0].children[2].children[0].textContent,"<title>");
'''
        completed = subprocess.run(
            [node, "-e", script], input=MONITOR_HTML, encoding="utf-8",
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_ocr_worker_error_without_reason_gets_a_diagnostic(self) -> None:
        self.assertEqual(_normalized_worker_reasons("error", {"missing_reasons": []}, 0), ("worker_error",))
        self.assertEqual(_normalized_worker_reasons("error", {"missing_reasons": []}, 1), ("worker_exit",))
        self.assertEqual(_normalized_worker_reasons("unavailable", {"missing_reasons": ["language_pack_missing"]}, 0), ("language_pack_missing",))


if __name__ == "__main__":
    unittest.main()
