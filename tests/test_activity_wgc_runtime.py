"""Persistent capture regression tests use a synthetic helper, never the desktop."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from mirally.platform.activity.wgc_capture import WindowsWindowCaptureSource, VisualSignatureGate
from mirally.platform.activity.wgc_worker import WGCWorker, WorkerFailure


HELPER = r'''
import json, os, sys, time
from pathlib import Path
if os.environ.get("ACTIVITY_FAKE_OLD_HELPER"):
    print(json.dumps({"captured":False,"error":"invalid_args"}), flush=True)
    sys.exit(2)
print(json.dumps({"event":"ready","protocol":1}), flush=True)
count = 0
for line in sys.stdin:
    r = json.loads(line)
    count += 1
    path = Path(r["output"])
    path.write_bytes(b"synthetic image")
    if r["handle"] in {"timeout", "202"}:
        time.sleep(30)
    if r["handle"] == "exit":
        sys.exit(3)
    if r["handle"] == "malformed":
        print("not json", flush=True)
        continue
    if r["handle"] == "huge":
        print("x"*70000, flush=True)
        continue
    if r["handle"] == "invalid_id":
        r["id"] += 100
    print(json.dumps({"id":r["id"], "captured":True, "method":"wgc", "size":[100,60],
        "runtime":{"helper_pid":os.getpid(),"session_id":1,"session_reused":count>1,
                   "readbacks":count,"session_count":1,"border_status":"disable_requested"}}), flush=True)
'''


class WGCWorkerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.helper = self.root / "synthetic_helper.py"
        self.helper.write_text(HELPER, encoding="utf-8")
        real_popen = subprocess.Popen
        self.processes = []

        def launch(command, **kwargs):
            self.assertEqual(command, [str(self.helper), "--serve"])
            process = real_popen([sys.executable, "-u", str(self.helper)], **kwargs)
            self.processes.append(process)
            return process

        self.mock = patch("mirally.platform.activity.wgc_worker.subprocess.Popen", side_effect=launch)
        self.mock.start()
        self.addCleanup(self.mock.stop)
        self.worker = WGCWorker(self.helper)
        self.addCleanup(self.worker.close)

    def request(self, handle="101", timeout=2000):
        return self.worker.request({"target_type":"hwnd", "handle":handle,
            "output":str(self.root/"frame.png"), "max_dimension":1920, "timeout_ms":timeout}, timeout)

    def test_reuses_process_and_session_and_exposes_runtime(self):
        first, second = self.request(), self.request()
        self.assertEqual(len(self.processes), 1)
        self.assertEqual(first["runtime"]["helper_pid"], second["runtime"]["helper_pid"])
        self.assertTrue(second["runtime"]["session_reused"])
        self.assertEqual(second["runtime"]["readbacks"], 2)
        self.assertEqual(self.worker.status()["border_status"], "disable_requested")
        self.worker.close()
        self.assertIsNotNone(self.processes[0].poll())
        self.assertFalse(self.worker.status()["running"])
        self.assertEqual(self.worker.status()["session_count"], 0)

    def test_deadline_kills_worker_and_next_request_recovers(self):
        self.request()
        with self.assertRaisesRegex(WorkerFailure, "worker_deadline"):
            self.request("timeout", timeout=150)
        self.assertIsNotNone(self.processes[0].poll())
        recovered = self.request()
        self.assertEqual(len(self.processes), 2)
        self.assertFalse(recovered["runtime"]["session_reused"])

    def test_idle_worker_exit_is_recovered_without_a_spurious_capture_failure(self):
        self.request()
        self.processes[0].terminate()
        self.processes[0].wait(timeout=2)
        recovered = self.request()
        self.assertEqual(len(self.processes), 2)
        self.assertTrue(recovered["captured"])
        self.assertFalse(recovered["runtime"]["session_reused"])

    def test_pause_interrupts_inflight_and_resume_is_lazy(self):
        self.request()
        (self.root/"frame.png").unlink()
        errors = []
        def blocked():
            try:
                self.request("timeout", timeout=5000)
            except WorkerFailure as error:
                errors.append(error.reason)
        thread = threading.Thread(target=blocked)
        thread.start()
        deadline = time.monotonic() + 2
        while not (self.root/"frame.png").exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue((self.root/"frame.png").exists())
        self.worker.pause()
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, ["capture_interrupted"])
        with self.assertRaisesRegex(WorkerFailure, "capture_paused"):
            self.request()
        self.worker.resume()
        self.assertEqual(len(self.processes), 1)
        self.request()
        self.assertEqual(len(self.processes), 2)

    def test_invalid_output_exit_and_ids_are_not_accepted(self):
        for handle, reason in (("exit", "worker_exit"), ("malformed", "invalid_helper_output"),
                               ("huge", "invalid_helper_output"), ("invalid_id", "invalid_helper_response_id")):
            with self.subTest(handle=handle):
                with self.assertRaisesRegex(WorkerFailure, reason):
                    self.request(handle)
                self.assertFalse(self.worker.status()["running"])
                self.assertIsNotNone(self.processes[-1].poll())
        self.assertTrue(self.request()["captured"])

    def test_old_binary_requests_rebuild_without_one_shot_fallback(self):
        with patch.dict("os.environ", {"ACTIVITY_FAKE_OLD_HELPER":"1"}):
            with self.assertRaisesRegex(WorkerFailure, "helper_protocol_mismatch_rebuild_required"):
                self.request()
        self.assertFalse(self.worker.status()["running"])

    def test_serial_requests_have_a_bounded_lock_wait(self):
        self.worker._request_lock.acquire()
        try:
            with self.assertRaisesRegex(WorkerFailure, "worker_busy"):
                self.request(timeout=100)
            self.assertEqual(self.processes, [])
        finally:
            self.worker._request_lock.release()

    @unittest.skipUnless(sys.platform == "win32", "Windows capture provider")
    def test_provider_preserves_diagnostics_and_removes_partial_files(self):
        with WindowsWindowCaptureSource(self.helper) as source:
            path = self.root/"output.png"
            first = source.capture(101, path, timeout_ms=2000)
            second = source.capture(101, path, timeout_ms=2000)
            self.assertEqual(first.status, "ok")
            self.assertEqual(first.as_dict()["runtime"]["border_status"], "disable_requested")
            gate = VisualSignatureGate()
            gate.commit("window", first.signature)
            self.assertFalse(gate.changed("window", second.signature))
            failed = source.capture(202, path, timeout_ms=150)
            self.assertEqual(failed.status, "timeout")
            self.assertEqual(failed.missing_reasons, ("worker_deadline",))
            self.assertFalse(path.exists())
            source.pause()
            self.assertEqual(source.capture(101, path).status, "cancelled")
            source.resume()
            self.assertEqual(source.capture(101, path, timeout_ms=2000).status, "ok")

    def test_native_session_implementation_uses_border_request_before_start(self):
        source = (Path(__file__).resolve().parents[1]/"mirally/platform/activity/native/desktop_wgc.cpp").read_text()
        self.assertLess(source.index("border.IsBorderRequired(false)"), source.index("session.StartCapture()"))
        self.assertIn("sender.Recreate(", source)
        self.assertNotIn("RequestAccessAsync", source)  # No unexpected system consent prompt.


if __name__ == "__main__":
    unittest.main()
