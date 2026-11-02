"""Worker startup keeps one writer per home and a clean JSON-lines stream."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STARTUP_TIMEOUT = 60


def worker_command(home: Path) -> list[str]:
    return [sys.executable, "-m", "mirally.framework", "--root", str(ROOT), "--home", str(home)]


def start_worker(home: Path) -> subprocess.Popen[str]:
    return subprocess.Popen(
        worker_command(home),
        cwd=ROOT,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )


def read_line(process: subprocess.Popen[str], timeout: float = STARTUP_TIMEOUT) -> str:
    lines: list[str] = []

    def read() -> None:
        assert process.stdout is not None
        lines.append(process.stdout.readline())

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    reader.join(timeout)
    if reader.is_alive():
        process.kill()
        raise AssertionError(f"worker did not answer within {timeout}s")
    line = lines[0] if lines else ""
    if not line:
        stderr = process.stderr.read() if process.stderr is not None else ""
        raise AssertionError(f"worker produced no response line; stderr={stderr!r}")
    return line


def exchange(process: subprocess.Popen[str], request: dict) -> dict:
    assert process.stdin is not None
    process.stdin.write(json.dumps(request) + "\n")
    process.stdin.flush()
    return json.loads(read_line(process))


def stop_worker(process: subprocess.Popen[str]) -> tuple[int, str, str]:
    if process.poll() is None:
        assert process.stdin is not None
        process.stdin.close()
        process.stdin = None
    try:
        stdout, stderr = process.communicate(timeout=STARTUP_TIMEOUT)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate(timeout=10)
        raise
    return process.returncode, stdout, stderr


class WorkerLockTests(unittest.TestCase):
    def test_second_worker_on_same_home_reports_readable_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "home"
            home.mkdir()
            first = start_worker(home)
            try:
                ready = exchange(first, {"id": "ready", "method": "system.status"})
                self.assertEqual(ready["id"], "ready")
                self.assertIsInstance(ready["result"], dict)
                second = subprocess.run(
                    worker_command(home),
                    cwd=ROOT,
                    input="",
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    timeout=STARTUP_TIMEOUT,
                )
            finally:
                first_code, first_stdout, first_stderr = stop_worker(first)

            self.assertEqual(first_code, 0, first_stderr)
            self.assertEqual(first_stdout, "")
            self.assertEqual(second.returncode, 1)
            self.assertEqual(second.stdout, "")
            lines = second.stderr.splitlines()
            self.assertEqual(len(lines), 1, second.stderr)
            lock_path = home.resolve() / "framework" / "worker.lock"
            self.assertIn("another worker", lines[0])
            self.assertIn("worker.lock", lines[0])
            self.assertIn(str(lock_path), lines[0])
            self.assertNotIn("Traceback", second.stderr)
            self.assertNotIn("PermissionError", second.stderr)

    def test_lock_is_released_after_the_first_worker_exits(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "home"
            home.mkdir()
            first = start_worker(home)
            self.assertEqual(exchange(first, {"id": "first", "method": "system.status"})["id"], "first")
            first_code, first_stdout, first_stderr = stop_worker(first)
            self.assertEqual(first_code, 0, first_stderr)
            self.assertEqual(first_stdout, "")

            second = start_worker(home)
            try:
                self.assertEqual(exchange(second, {"id": "second", "method": "system.status"})["id"], "second")
            finally:
                second_code, second_stdout, second_stderr = stop_worker(second)
            self.assertEqual(second_code, 0, second_stderr)
            self.assertEqual(second_stdout, "")

    def test_workers_with_different_homes_run_independently(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            homes = [Path(directory) / "a", Path(directory) / "b"]
            for home in homes:
                home.mkdir()
            processes = [start_worker(home) for home in homes]
            try:
                for index, process in enumerate(processes):
                    response = exchange(process, {"id": f"worker-{index}", "method": "system.status"})
                    self.assertEqual(response["id"], f"worker-{index}")
                    self.assertIsInstance(response["result"], dict)
            finally:
                results = [stop_worker(process) for process in processes]
            for code, stdout, stderr in results:
                self.assertEqual(code, 0, stderr)
                self.assertEqual(stdout, "")

    def test_invalid_json_keeps_the_protocol_response(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "home"
            home.mkdir()
            process = start_worker(home)
            try:
                assert process.stdin is not None
                process.stdin.write("this is not json\n")
                process.stdin.flush()
                response = json.loads(read_line(process))
                self.assertIsNone(response["id"])
                self.assertEqual(response["error"], {"code": "INVALID_REQUEST", "message": "invalid JSON"})

                # A parse failure does not change how a valid id is echoed back.
                response = exchange(process, {"id": "after-error", "method": "system.status"})
                self.assertEqual(response["id"], "after-error")
                self.assertIsInstance(response["result"], dict)
            finally:
                code, stdout, stderr = stop_worker(process)
            self.assertEqual(code, 0, stderr)
            self.assertEqual(stdout, "")

    def test_other_startup_errors_keep_their_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "home"
            (home / "framework" / "product.sqlite3").mkdir(parents=True)
            result = subprocess.run(
                worker_command(home),
                cwd=ROOT,
                input="",
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=STARTUP_TIMEOUT,
            )
            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stdout, "")
            self.assertIn("Traceback (most recent call last)", result.stderr)
            self.assertNotIn("another worker", result.stderr)


if __name__ == "__main__":
    unittest.main()
