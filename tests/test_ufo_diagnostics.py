import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import unittest

import psutil

from mirally.runtime.ufo.diagnostics import startup_diagnostics


class StartupDiagnosticsTests(unittest.TestCase):
    def test_blocked_startup_reports_stage_and_code_locations_without_locals(self):
        events, heartbeat = [], threading.Event()
        private_value = "PRIVATE_LOCAL_MUST_NOT_APPEAR"

        def receive(event):
            events.append(event)
            if event["type"] == "startup_wait":
                heartbeat.set()

        with startup_diagnostics(receive, interval=0.01) as mark:
            mark("session_import")
            self.assertTrue(heartbeat.wait(1))
        count = len(events)
        time.sleep(0.03)
        self.assertEqual(len(events), count)
        wait = next(event for event in events if event["type"] == "startup_wait")
        self.assertEqual(wait["stage"], "session_import")
        self.assertTrue(wait["stack"])
        self.assertNotIn(private_value, json.dumps(events))
        for frame in wait["stack"]:
            self.assertEqual(set(frame), {"file", "function", "line"})
            self.assertEqual(frame["file"], Path(frame["file"]).name)

    def test_heartbeat_is_removed_when_initialization_raises(self):
        events = []
        with self.assertRaises(RuntimeError):
            with startup_diagnostics(events.append, interval=0.01):
                raise RuntimeError("synthetic initialization failure")
        time.sleep(0.03)
        self.assertEqual(len(events), 1)

    @unittest.skipUnless(sys.platform == "win32", "Windows hidden-process initialization")
    def test_real_ufo_imports_with_temporary_config_and_no_desktop_or_model_call(self):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run([str(root / ".venv/Scripts/python.exe"), "-m",
                                 "tests.ufo_startup_probe", str(root)], cwd=root,
            env=dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8"),
            capture_output=True, text=True, encoding="utf-8", timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW)
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        events = [json.loads(line) for line in result.stdout.splitlines()]
        stages = [event.get("stage") for event in events if event["type"] == "phase"]
        self.assertEqual(stages[-1], "initialized")
        self.assertTrue({"configuring", "model_binding", "native_compatibility", "session_import"} <= set(stages))

    @unittest.skipUnless(sys.platform == "win32", "Windows inherited pipe/native extension loading")
    def test_real_worker_imports_with_idle_control_pipe_and_receives_cancel(self):
        root = Path(__file__).resolve().parents[1]
        for executable in ("python.exe", "pythonw.exe"):
            with self.subTest(executable=executable):
                events, errors = [], []
                initialized = threading.Event()
                process = subprocess.Popen([str(root / ".venv/Scripts" / executable), "-m",
                    "tests.ufo_startup_probe", str(root), "--worker-shape"], cwd=root,
                    env=dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8"),
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, encoding="utf-8", creationflags=subprocess.CREATE_NO_WINDOW)

                def receive():
                    for line in process.stdout:
                        event = json.loads(line)
                        events.append(event)
                        if event.get("stage") == "initialized":
                            initialized.set()

                def receive_errors():
                    errors.append(process.stderr.read())

                readers = [threading.Thread(target=receive, daemon=True),
                           threading.Thread(target=receive_errors, daemon=True)]
                for reader in readers:
                    reader.start()
                try:
                    # Crucially, keep stdin open and idle during DLL imports.
                    # subprocess.run/communicate would close it and mask the bug.
                    self.assertTrue(initialized.wait(20),
                        "Native imports did not initialize with an idle control pipe; "
                        f"executable={executable}, exit={process.poll()}, "
                        f"last_events={json.dumps(events[-4:], ensure_ascii=False)}, "
                        f"stderr={''.join(errors)[-2000:]}")
                    process.stdin.write('{"type":"cancel"}\n')
                    process.stdin.flush()
                    self.assertEqual(process.wait(timeout=10), 0, "".join(errors)[-2000:])
                    for reader in readers:
                        reader.join(timeout=3)
                    self.assertIn({"type": "cancel_received"}, events)
                finally:
                    if process.poll() is None:
                        parent = psutil.Process(process.pid)
                        targets = [parent, *parent.children(recursive=True)]
                        for target in targets:
                            try:
                                target.kill()
                            except psutil.NoSuchProcess:
                                pass
                        psutil.wait_procs(targets, timeout=3)
                        process.wait(timeout=3)
                    for reader in readers:
                        reader.join(timeout=3)
                    process.stdin.close()
                    process.stdout.close()
                    process.stderr.close()


if __name__ == "__main__":
    unittest.main()
