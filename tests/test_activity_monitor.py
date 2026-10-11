from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.request
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

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


class _TrackingLock:
    def __init__(self) -> None:
        self.held = True
        self.release_calls = 0

    def release(self) -> None:
        self.held = False
        self.release_calls += 1


class ActivityMonitorTests(unittest.TestCase):
    def test_close_retains_resources_until_monitor_thread_stops(self) -> None:
        for attribute in ("http_thread", "sensor_thread", "organizer_thread"):
            with self.subTest(thread=attribute), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                config = replace(build_test_config(root, content=False), input_enabled=False, ui_structure_enabled=False)
                runtime = ActivityMonitorRuntime(config, root=root)
                lock = _TrackingLock()
                runtime.lock = lock

                class StoreClosingSensor:
                    close_calls = 0

                    def stop(self):
                        pass

                    def close(self):
                        self.close_calls += 1
                        runtime.store.close()

                runtime.sensor = StoreClosingSensor()
                release = threading.Event()
                worker = threading.Thread(target=release.wait, daemon=True)
                setattr(runtime, attribute, worker)
                worker.start()
                try:
                    with patch("openassist.runtime.activity.monitor._THREAD_JOIN_TIMEOUT_SECONDS", 0.01), \
                            patch("openassist.runtime.activity.monitor._HTTP_THREAD_JOIN_TIMEOUT_SECONDS", 0.01):
                        with self.assertRaisesRegex(RuntimeError, f"{attribute} did not stop"):
                            runtime.close()
                    self.assertIs(getattr(runtime, attribute), worker)
                    self.assertTrue(worker.is_alive())
                    self.assertFalse(runtime._closed)
                    self.assertTrue(lock.held)
                    self.assertEqual(lock.release_calls, 0)
                    self.assertEqual(runtime.sensor.close_calls, 0)
                    self.assertTrue(runtime.knowledge_store.health().available)
                    self.assertIs(runtime.organizer.source, runtime.store)
                    runtime.store.append(Observation.create(
                        kind="window_context", source_id="fixture", source_epoch="epoch", source_seq=0,
                        boot_id="boot", observed_at_utc_ms=1000,
                        payload={"window_title": "Fixture context"},
                    ))
                    self.assertEqual(runtime.knowledge_store.health("activity-knowledge").item_count, 0)
                finally:
                    release.set()
                    worker.join(timeout=2)
                    runtime.close()
                self.assertFalse(worker.is_alive())
                self.assertIsNone(getattr(runtime, attribute))
                self.assertTrue(runtime._closed)
                self.assertFalse(lock.held)
                self.assertEqual(lock.release_calls, 1)
                self.assertEqual(runtime.sensor.close_calls, 1)
                runtime.close()
                self.assertEqual(lock.release_calls, 1)
                from openassist.core.activity.knowledge import KnowledgeStore
                with KnowledgeStore(config.database_path.with_name("activity-knowledge.sqlite3")) as knowledge:
                    health = knowledge.health("activity-knowledge")
                    self.assertEqual(health.item_count, 1)
                    self.assertEqual(health.processed_ingest_seq, 1)

    def test_close_preserves_stores_when_sensor_evidence_worker_cannot_stop(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = replace(build_test_config(root, content=False), input_enabled=False, ui_structure_enabled=False)
            runtime = ActivityMonitorRuntime(config, root=root)
            runtime.sensor = ActivitySensor(
                config, store=runtime.store, source=_FakeForeground(), input_source=None, uia_source=None,
            )
            lock = _TrackingLock()
            runtime.lock = lock
            release = threading.Event()
            worker = threading.Thread(target=release.wait, daemon=True)
            runtime.sensor._evidence_thread = worker
            worker.start()
            join_worker = worker.join
            try:
                with patch.object(worker, "join", side_effect=lambda timeout=None: join_worker(timeout=0.01)):
                    with self.assertRaisesRegex(RuntimeError, "evidence worker did not stop"):
                        runtime.close()
                self.assertIs(runtime.sensor._evidence_thread, worker)
                self.assertTrue(worker.is_alive())
                self.assertFalse(runtime._closed)
                self.assertTrue(lock.held)
                self.assertEqual(lock.release_calls, 0)
                self.assertTrue(runtime.knowledge_store.health().available)
                self.assertIs(runtime.organizer.source, runtime.store)
                runtime.store.append(Observation.create(
                    kind="window_context", source_id="fixture", source_epoch="epoch", source_seq=0,
                    boot_id="boot", observed_at_utc_ms=1000,
                    payload={"window_title": "Fixture context"},
                ))
                self.assertEqual(runtime.knowledge_store.health("activity-knowledge").item_count, 0)
            finally:
                release.set()
                worker.join(timeout=2)
                runtime.close()
            self.assertFalse(worker.is_alive())
            self.assertIsNone(runtime.sensor._evidence_thread)
            self.assertTrue(runtime.sensor._diagnostics_closed)
            self.assertTrue(runtime._closed)
            self.assertFalse(lock.held)
            self.assertEqual(lock.release_calls, 1)
            from openassist.core.activity.knowledge import KnowledgeStore
            with KnowledgeStore(config.database_path.with_name("activity-knowledge.sqlite3")) as knowledge:
                health = knowledge.health("activity-knowledge")
                self.assertEqual(health.item_count, 1)
                self.assertEqual(health.processed_ingest_seq, 1)

    def test_close_projects_final_sensor_flush_without_new_capture(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = replace(build_test_config(root, content=False), input_enabled=False, ui_structure_enabled=False)
            runtime = ActivityMonitorRuntime(config, root=root)

            class FinalFlushSensor:
                def stop(self):
                    pass

                def close(self):
                    runtime.store.append(Observation.create(
                        kind="context_snapshot", source_id="fixture", source_epoch="epoch", source_seq=0,
                        boot_id="boot", observed_at_utc_ms=1000,
                        uia_summary={"nodes": [{"role": "Document", "text": "Final flushed body"}]},
                    ))
                    runtime.store.close()

            runtime.sensor = FinalFlushSensor()
            runtime.close()
            runtime.close()
            from openassist.core.activity.knowledge import KnowledgeStore
            with KnowledgeStore(config.database_path.with_name("activity-knowledge.sqlite3")) as knowledge:
                health = knowledge.health("activity-knowledge")
                self.assertEqual(health.item_count, 1)
                self.assertEqual(health.processed_ingest_seq, 1)

    def test_close_reports_failed_final_projection_and_releases_stopped_resources(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = replace(build_test_config(root, content=False), input_enabled=False, ui_structure_enabled=False)
            runtime = ActivityMonitorRuntime(config, root=root)
            lock = _TrackingLock()
            runtime.lock = lock
            flushed = []

            class FinalFlushSensor:
                def stop(self):
                    pass

                def close(self):
                    flushed.append(runtime.store.append(Observation.create(
                        kind="window_context", source_id="fixture", source_epoch="epoch", source_seq=0,
                        boot_id="boot", observed_at_utc_ms=1000,
                        payload={"window_title": "Fixture context"},
                    )))
                    runtime.store.close()

            class FailingParser:
                def parse(self, item):
                    raise ValueError("fixture final parser failure")

            runtime.sensor = FinalFlushSensor()
            runtime.organizer.parser_registry = FailingParser()
            with self.assertRaisesRegex(RuntimeError, r"final projection incomplete \(errors=1, lag=0\)"):
                runtime.close()
            self.assertTrue(runtime._closed)
            self.assertFalse(runtime.store._writer.is_alive())
            self.assertTrue(runtime.knowledge_store._closed)
            self.assertIs(runtime.organizer.source, runtime.store)
            self.assertFalse(lock.held)
            self.assertEqual(lock.release_calls, 1)
            runtime.close()
            self.assertEqual(lock.release_calls, 1)
            from openassist.core.activity.knowledge import KnowledgeStore
            from openassist.core.activity.store import ObservationStore
            with ObservationStore(config.database_path, read_only=True) as source:
                self.assertIsNotNone(source.get(flushed[0].observation_id))
            with KnowledgeStore(config.database_path.with_name("activity-knowledge.sqlite3")) as knowledge:
                health = knowledge.health("activity-knowledge")
                self.assertEqual(health.item_count, 0)
                self.assertEqual(health.processed_ingest_seq, 1)
                errors = knowledge.list_projection_errors()
                self.assertEqual(len(errors), 1)
                self.assertEqual(errors[0]["observation_id"], flushed[0].observation_id)
                self.assertEqual(errors[0]["attempts"], 1)
                self.assertIn("fixture final parser failure", errors[0]["error"])

    def test_close_does_not_report_old_terminal_projection_error_as_new_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = replace(build_test_config(root, content=False), input_enabled=False, ui_structure_enabled=False)
            runtime = ActivityMonitorRuntime(config, root=root)
            runtime.sensor = ActivitySensor(
                config, store=runtime.store, source=_FakeForeground(), input_source=None, uia_source=None,
            )
            for _ in range(3):
                runtime.knowledge_store.record_projection_error("old-unavailable-observation", 0, "old terminal failure")
            runtime.close()
            self.assertTrue(runtime._closed)
            from openassist.core.activity.knowledge import KnowledgeStore
            with KnowledgeStore(config.database_path.with_name("activity-knowledge.sqlite3")) as knowledge:
                errors = knowledge.list_projection_errors()
                self.assertEqual(len(errors), 1)
                self.assertEqual(errors[0]["attempts"], 3)

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
                self.assertIn("knowledge", payload)
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

                _, browser_page = get("/api/browser/observations?limit=1")
                self.assertTrue(browser_page["observations"])
                self.assertIn("observation_id", browser_page["observations"][0])
                if browser_page["has_more"]:
                    _, next_browser_page = get(
                        f"/api/browser/observations?limit=1&before={browser_page['cursor']}"
                    )
                    self.assertNotEqual(
                        browser_page["observations"][0]["observation_id"],
                        next_browser_page["observations"][0]["observation_id"],
                    )

                _, knowledge_health = get("/api/knowledge/health")
                self.assertTrue(knowledge_health["knowledge"]["available"])
                context_status, context = get("/api/knowledge/context?mode=outline&limit=5")
                self.assertEqual(context_status, 200)
                self.assertIn("index_watermark", context)

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
