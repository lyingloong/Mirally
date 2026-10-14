"""Verify the real worker generates memory while its parent stdin is idle."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from openassist.core.activity import Observation, ObservationStore
from openassist.core.memory import MemoryStore
from openassist.runtime.memory import MemoryConfig, MemoryService
from openassist.runtime.memory.worker import serve


class MemoryWorkerTests(unittest.TestCase):
    def test_disabled_worker_protocol_survives_bad_request_and_stops(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = MemoryConfig(False, root / "memory.sqlite3", root / "activity.yaml", root / "projection.sqlite3")
            source = io.StringIO('bad json\n{"id":"h","op":"health"}\n{"id":"stop","op":"stop"}\n')
            sink = io.StringIO()
            serve(MemoryService(root, config=config), source, sink)
            rows = [json.loads(line) for line in sink.getvalue().splitlines()]
            self.assertFalse(rows[0]["ok"])
            self.assertEqual(rows[1]["result"]["status"], "disabled")
            self.assertEqual(rows[2]["result"]["status"], "stopping")
            self.assertFalse(config.database_path.exists())

    def test_real_worker_maintains_memory_without_requests_and_stops_on_parent_eof(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_path = root / "source.sqlite3"
            with ObservationStore(source_path) as source:
                original = source.append(Observation.create(kind="window_context", source_id="fixture", source_epoch="epoch",
                    source_seq=1, boot_id="boot", observed_at_utc_ms=1000, app_id="code.exe", window_instance_id="editor",
                    payload={"window_title": "worker-startup memory layer"}))
            activity = root / "activity.yaml"
            activity.write_text("enabled: false\nstorage:\n  database_path: " + source_path.as_posix() + "\n", encoding="utf-8")
            config = root / "memory.yaml"
            memory_path = root / "memory.sqlite3"
            config.write_text("enabled: true\nactivity_config_path: " + activity.as_posix() +
                "\npoll_interval_seconds: 0.05\nstorage:\n  database_path: " + memory_path.as_posix() +
                "\n  projection_database_path: " + (root / "projection.sqlite3").as_posix() + "\n", encoding="utf-8")
            product_root = Path(__file__).resolve().parents[1]
            environment = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
            process = subprocess.Popen([sys.executable, "-m", "openassist.runtime.memory.worker", "--root", str(product_root),
                "--config", str(config)], cwd=product_root, env=environment,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
            try:
                ready = False
                deadline = time.monotonic() + 8
                while time.monotonic() < deadline:
                    self.assertIsNone(process.poll(), "worker exited while parent pipe remained open")
                    if memory_path.is_file():
                        try:
                            with MemoryStore(memory_path, read_only=True) as store:
                                rows = store.search(kinds=("episodic",))
                                if rows:
                                    ready = True
                                    self.assertIn(original.observation_id, rows[0]["source_observation_ids"])
                                    self.assertIn("worker-startup", rows[0]["content"])
                                    break
                        except (RuntimeError, OSError):
                            pass
                    time.sleep(0.03)
                self.assertTrue(ready, "background memory was not generated without dialogue")
                assert process.stdin is not None
                process.stdin.close()
                process.stdin = None
                output, errors = process.communicate(timeout=10)
                self.assertEqual(process.returncode, 0, errors)
                self.assertEqual(output, "")
            finally:
                if process.poll() is None:
                    process.kill()
                    process.communicate(timeout=5)


if __name__ == "__main__":
    unittest.main()
