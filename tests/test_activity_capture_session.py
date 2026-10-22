from __future__ import annotations

import copy
import sys
import tempfile
import threading
import unittest
import uuid
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from mirally.core.activity.policy import ActivityPolicy
from mirally.runtime.activity.capture_session import ActivityCaptureSession
from mirally.runtime.activity.config import ActivityConfig
from mirally.runtime.activity.lock import ActivityProcessLock
from mirally.runtime.activity.monitor import ActivityMonitorRuntime


class FakeGuard:
    def __init__(self) -> None:
        self.held = False
        self.acquire_calls = 0
        self.release_calls = 0

    def acquire(self) -> bool:
        self.acquire_calls += 1
        self.held = True
        return True

    def release(self) -> None:
        self.release_calls += int(self.held)
        self.held = False


class FakeSensor:
    def __init__(self, runtime) -> None:
        self.runtime = runtime
        self.pause_calls = 0
        self.resume_calls = 0

    def pause(self) -> None:
        self.pause_calls += 1
        self.runtime.collector = "paused"

    def resume(self) -> None:
        self.resume_calls += 1
        self.runtime.collector = "running"


class FakeRuntime:
    created = []
    guard = None
    constructor_error = None
    start_error = None

    def __init__(self, config, *, root) -> None:
        if self.guard is not None and not self.guard.held:
            raise AssertionError("the guard must be held before runtime construction")
        self.created.append(self)
        self.config = config
        self.root = root
        self.stop_event = threading.Event()
        self.sensor = FakeSensor(self)
        self.collector = "idle"
        self.lock = None
        self._closed = False
        self.start_calls = 0
        self.close_calls = 0
        self.fail_close = 0
        self.url = "http://127.0.0.1:12345/?token=synthetic"
        if self.constructor_error is not None:
            raise self.constructor_error

    def start(self) -> str:
        self.start_calls += 1
        if not self.lock.acquire():
            raise AssertionError("the runtime must reuse the already held guard")
        if self.start_error is not None:
            raise self.start_error
        self.collector = "running"
        return self.url

    def request_stop(self) -> None:
        self.stop_event.set()
        self.collector = "stopped"

    def close(self) -> None:
        if self._closed:
            return
        self.close_calls += 1
        self.request_stop()
        if self.fail_close:
            self.fail_close -= 1
            raise RuntimeError("fixture worker did not stop")
        self.lock.release()
        self._closed = True

    def status(self) -> dict:
        if self._closed:
            raise RuntimeError("closed stores must not be read")
        return {
            "enabled": self.config.enabled,
            "database": str(self.config.database_path),
            "collector": {"status": self.collector},
            "collector_diagnostics": {"fixture": "preserved"},
            "monitor": {"thread_error": None},
            "metrics": {"events_total": 4},
        }


def fixture_config(root: Path) -> ActivityConfig:
    return ActivityConfig(
        enabled=False,
        database_path=root / "space 中文" / "observations.sqlite3",
        asset_directory=root / "space 中文" / "assets",
        poll_interval_seconds=1,
        policy=ActivityPolicy(enabled=False, excluded_apps=("private.exe",)),
        observation_retention_days=14,
        image_retention_hours=24,
        image_quota_mib=1024,
        min_free_space_mib=2048,
    )


class ActivityCaptureSessionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.config = fixture_config(self.root)
        self.guard = FakeGuard()
        FakeRuntime.created = []
        FakeRuntime.guard = self.guard
        FakeRuntime.constructor_error = None
        FakeRuntime.start_error = None
        self.addCleanup(patch.stopall)
        patch("mirally.runtime.activity.capture_session.ActivityProcessLock", return_value=self.guard).start()
        patch("mirally.runtime.activity.capture_session.ActivityMonitorRuntime", FakeRuntime).start()
        self.session = ActivityCaptureSession(self.root)
        self.addCleanup(self.session.close)

    def test_creation_status_and_noop_controls_do_not_create_runtime_or_data(self) -> None:
        self.assertIsNone(self.session.status())
        self.assertIsNone(self.session.pause())
        self.assertIsNone(self.session.resume())
        self.assertIsNone(self.session.stop())
        self.session.close()
        self.assertIsNone(self.session.runtime)
        self.assertEqual(self.session.url, "")
        self.assertEqual(FakeRuntime.created, [])
        self.assertEqual(self.guard.acquire_calls, 0)
        self.assertFalse(self.config.database_path.parent.exists())

    def test_start_enables_only_session_and_acquires_guard_before_construction(self) -> None:
        before = copy.deepcopy(self.config)
        with patch("webbrowser.open") as browser:
            status = self.session.start(self.config)
        self.assertEqual(self.config, before)
        runtime = self.session.runtime
        self.assertTrue(runtime.config.enabled)
        self.assertTrue(runtime.config.policy.enabled)
        self.assertEqual(replace(runtime.config, enabled=False, policy=self.config.policy), self.config)
        self.assertEqual(runtime.root, self.root.resolve())
        self.assertEqual(self.guard.acquire_calls, 1)
        self.assertEqual(runtime.start_calls, 1)
        self.assertEqual(status["collector"]["status"], "running")
        self.assertTrue(status["collector"]["owned"])
        self.assertFalse(status["collector"]["snapshot"])
        self.assertGreater(status["collector"]["updated_at_utc_ms"], 0)
        self.assertEqual(status["controls"], {"start": False, "pause": True, "resume": False, "stop": True})
        self.assertEqual(self.session.url, runtime.url)
        browser.assert_not_called()

    def test_start_twice_reuses_session_and_rejects_a_new_scope(self) -> None:
        first_status = self.session.start(self.config)
        runtime = self.session.runtime
        second_status = self.session.start(self.config)
        self.assertEqual(first_status["session_id"], second_status["session_id"])
        self.assertEqual(len(first_status["session_id"]), 32)
        self.assertEqual(len(FakeRuntime.created), 1)
        self.assertEqual(self.guard.acquire_calls, 1)
        self.assertEqual(runtime.start_calls, 1)
        with self.assertRaisesRegex(RuntimeError, "stop the current"):
            self.session.start(replace(self.config, visual_enabled=True))
        self.assertIs(self.session.runtime, runtime)

    def test_pause_resume_and_stop_control_only_owned_runtime(self) -> None:
        self.session.start(self.config)
        runtime = self.session.runtime
        paused = self.session.pause()
        self.assertEqual(paused["collector"]["status"], "paused")
        self.assertEqual(paused["controls"], {"start": False, "pause": False, "resume": True, "stop": True})
        resumed = self.session.resume()
        self.assertEqual(resumed["collector"]["status"], "running")
        self.assertEqual(runtime.sensor.pause_calls, 1)
        self.assertEqual(runtime.sensor.resume_calls, 2)
        stopped = self.session.stop()
        self.assertEqual(stopped["collector"]["status"], "stopped")
        self.assertEqual(stopped["collector_diagnostics"], {"fixture": "preserved"})
        self.assertEqual(stopped["controls"], {"start": True, "pause": False, "resume": False, "stop": False})
        self.assertIsNone(self.session.runtime)
        self.assertEqual(self.session.url, "")
        self.assertEqual(self.guard.release_calls, 1)
        self.session.pause()
        self.session.resume()
        self.session.stop()
        self.assertEqual(runtime.close_calls, 1)

    def test_same_profile_can_restart_after_stop(self) -> None:
        first = self.session.start(self.config)
        original = self.session.runtime
        stopped = self.session.stop(expected_session_id=first["session_id"])
        self.assertEqual(stopped["session_id"], first["session_id"])
        second = self.session.start(self.config)
        self.assertIsNot(self.session.runtime, original)
        self.assertNotEqual(first["session_id"], second["session_id"])
        self.assertEqual(len(FakeRuntime.created), 2)
        self.assertEqual(self.guard.acquire_calls, 2)

    def test_late_controls_from_previous_session_cannot_affect_new_capture(self) -> None:
        first = self.session.start(self.config)
        self.session.stop(expected_session_id=first["session_id"])
        second = self.session.start(self.config)
        runtime = self.session.runtime
        for action in (self.session.pause, self.session.resume, self.session.stop):
            with self.subTest(action=action.__name__), self.assertRaisesRegex(RuntimeError, "会话已变化"):
                action(expected_session_id=first["session_id"])
        self.assertIs(self.session.runtime, runtime)
        self.assertFalse(runtime.stop_event.is_set())
        self.assertEqual(runtime.close_calls, 0)
        self.assertEqual(runtime.sensor.pause_calls, 0)
        self.assertEqual(runtime.sensor.resume_calls, 1)
        self.assertTrue(self.guard.held)
        self.session.pause(expected_session_id=second["session_id"])
        self.session.resume(expected_session_id=second["session_id"])
        self.session.stop(expected_session_id=second["session_id"])
        self.assertFalse(self.guard.held)

    def test_returned_status_cannot_mutate_retained_diagnostics(self) -> None:
        self.session.start(self.config)
        self.session.stop()
        changed = self.session.status()
        changed["collector"]["status"] = "running"
        changed["collector_diagnostics"]["fixture"] = "changed"
        status = self.session.status()
        self.assertEqual(status["collector"]["status"], "stopped")
        self.assertEqual(status["collector_diagnostics"]["fixture"], "preserved")

    def test_busy_guard_skips_runtime_construction_and_database_creation(self) -> None:
        with patch.object(self.guard, "acquire", return_value=False):
            with self.assertRaisesRegex(RuntimeError, "another Mirally Activity collector"):
                self.session.start(self.config)
        self.assertEqual(FakeRuntime.created, [])
        self.assertIsNone(self.session.status())
        self.assertFalse(self.config.database_path.parent.exists())

    def test_start_failure_closes_runtime_and_releases_guard(self) -> None:
        FakeRuntime.start_error = ValueError("fixture start failed")
        with self.assertRaisesRegex(ValueError, "fixture start failed"):
            self.session.start(self.config)
        runtime = FakeRuntime.created[0]
        self.assertTrue(runtime._closed)
        self.assertEqual(self.guard.release_calls, 1)
        self.assertIsNone(self.session.runtime)
        status = self.session.status()
        self.assertEqual(status["collector"]["status"], "stopped")
        self.assertIn("fixture start failed", status["monitor"]["startup_error"])
        self.assertTrue(status["controls"]["start"])

    def test_constructor_failure_closes_partial_store_before_releasing_guard(self) -> None:
        class PartialRuntime:
            resource = None

            def __init__(runtime, config, *, root):
                class PartialStore:
                    closed = False

                    def close(resource):
                        self.assertTrue(self.guard.held)
                        resource.closed = True

                runtime.store = PartialStore()
                PartialRuntime.resource = runtime.store
                raise ValueError("fixture constructor failed")

        with patch("mirally.runtime.activity.capture_session.ActivityMonitorRuntime", PartialRuntime):
            with self.assertRaisesRegex(ValueError, "fixture constructor failed"):
                self.session.start(self.config)
        self.assertTrue(PartialRuntime.resource.closed)
        self.assertFalse(self.guard.held)
        self.assertIsNone(self.session.runtime)
        self.assertIn("fixture constructor failed", self.session.status()["monitor"]["startup_error"])

    def test_partial_constructor_cleanup_failure_can_be_retried(self) -> None:
        class PartialRuntime:
            resource = None

            def __init__(runtime, config, *, root):
                class PartialStore:
                    calls = 0

                    def close(resource):
                        resource.calls += 1
                        if resource.calls == 1:
                            raise RuntimeError("fixture partial cleanup failed")

                runtime.store = PartialStore()
                PartialRuntime.resource = runtime.store
                raise ValueError("fixture constructor failed")

        with patch("mirally.runtime.activity.capture_session.ActivityMonitorRuntime", PartialRuntime):
            with self.assertRaisesRegex(ValueError, "fixture constructor failed"):
                self.session.start(self.config)
        self.assertTrue(self.guard.held)
        status = self.session.status()
        self.assertFalse(status["controls"]["start"])
        self.assertTrue(status["controls"]["stop"])
        self.assertIn("fixture partial cleanup failed", status["monitor"]["shutdown_error"])
        with self.assertRaisesRegex(RuntimeError, "stop the current"):
            self.session.start(self.config)
        self.session.close()
        self.assertEqual(PartialRuntime.resource.calls, 2)
        self.assertFalse(self.guard.held)
        self.assertTrue(self.session.status()["controls"]["start"])

    def test_close_failure_retains_runtime_and_lock_until_retry_succeeds(self) -> None:
        self.session.start(self.config)
        runtime = self.session.runtime
        runtime.fail_close = 2
        with self.assertRaisesRegex(RuntimeError, "fixture worker did not stop"):
            self.session.close()
        self.assertIs(self.session.runtime, runtime)
        self.assertTrue(self.guard.held)
        self.assertEqual(self.guard.release_calls, 0)
        # A read retries cleanup, reports stop_event accurately even when the
        # persisted status was running, and does not throw away the failure.
        status = self.session.status()
        self.assertEqual(status["collector"]["status"], "stopped")
        self.assertFalse(status["collector"]["running"])
        self.assertFalse(status["collector"]["live_enabled"])
        self.assertIn("fixture worker did not stop", status["monitor"]["shutdown_error"])
        self.assertFalse(status["controls"]["start"])
        self.assertTrue(status["controls"]["stop"])
        self.assertTrue(self.guard.held)
        self.session.close()
        self.assertFalse(self.guard.held)
        self.assertIsNone(self.session.runtime)
        self.assertEqual(self.guard.release_calls, 1)

    def test_web_stop_event_reaps_session_without_stale_running_status(self) -> None:
        self.session.start(self.config)
        runtime = self.session.runtime
        # The monitor's stop endpoint requests shutdown. Its stale status must
        # not make the settings panel show a still-running collector.
        runtime.stop_event.set()
        self.assertEqual(runtime.collector, "running")
        self.assertEqual(self.session.url, "")
        status = self.session.status()
        self.assertEqual(status["collector"]["status"], "stopped")
        self.assertTrue(status["controls"]["start"])
        self.assertEqual(runtime.close_calls, 1)
        self.assertFalse(self.guard.held)
        self.assertIsNone(self.session.runtime)

    def test_cleanup_error_after_resources_closed_keeps_diagnostic_without_reading_stores(self) -> None:
        self.session.start(self.config)
        runtime = self.session.runtime
        close = runtime.close

        def closed_with_error():
            close()
            raise RuntimeError("fixture final projection incomplete")

        with patch.object(runtime, "close", side_effect=closed_with_error):
            with self.assertRaisesRegex(RuntimeError, "fixture final projection incomplete"):
                self.session.close()
        self.assertTrue(runtime._closed)
        self.assertFalse(self.guard.held)
        with patch.object(runtime, "request_stop", side_effect=AssertionError("closed sensor must not be stopped again")), \
                patch.object(runtime, "status", side_effect=AssertionError("closed store must not be read")):
            status = self.session.status()
        self.assertTrue(status["controls"]["start"])
        self.assertIn("fixture final projection incomplete", status["monitor"]["shutdown_error"])
        self.assertIsNone(self.session.runtime)

    def test_concurrent_start_calls_create_one_runtime(self) -> None:
        barrier = threading.Barrier(3)
        results = []
        errors = []

        def start():
            try:
                barrier.wait(timeout=2)
                results.append(self.session.start(self.config))
            except Exception as error:
                errors.append(error)

        workers = [threading.Thread(target=start) for _ in range(2)]
        for worker in workers:
            worker.start()
        barrier.wait(timeout=2)
        for worker in workers:
            worker.join(timeout=3)
            self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 2)
        self.assertEqual(len(FakeRuntime.created), 1)
        self.assertEqual(self.guard.acquire_calls, 1)

    def test_real_monitor_lifecycle_uses_fake_sensor_and_temporary_stores(self) -> None:
        test = self

        class StoreSensor:
            def __init__(self, config, *, store):
                test.assertTrue(test.guard.held)
                self.config = config
                self.store = store
                self.policy = config.policy
                self.source = type("Source", (), {"available": False})()
                self.browser_source = self.input_source = self.uia_source = None
                self.visual_source = self.ocr_source = None
                self.clipboard_source = self.source
                self.started = threading.Event()

            def run(self, *, stop_event):
                self.store.set_state("collector", {"status": "running"})
                self.started.set()
                stop_event.wait(timeout=10)

            def metrics_snapshot(self):
                return {"events_total": 0}

            def pause(self):
                self.store.set_state("collector_pause", {"paused": True})

            def resume(self):
                self.store.set_state("collector_pause", {"paused": False})

            def stop(self):
                self.store.set_state("collector", {"status": "stopped"})

            def close(self):
                self.store.close()

        with patch("mirally.runtime.activity.capture_session.ActivityMonitorRuntime", ActivityMonitorRuntime), \
                patch("mirally.runtime.activity.monitor.ActivitySensor", StoreSensor):
            self.session.start(self.config)
            runtime = self.session.runtime
            self.assertTrue(runtime.sensor.started.wait(timeout=2))
            self.assertEqual(self.guard.acquire_calls, 1)
            self.assertTrue(self.config.database_path.is_file())
            self.assertEqual(self.session.status()["collector"]["status"], "running")
            self.assertEqual(self.session.pause()["collector"]["status"], "paused")
            self.assertEqual(self.session.resume()["collector"]["status"], "running")
            self.assertTrue(self.session.url.startswith("http://127.0.0.1:"))
            first_session_id = self.session.status()["session_id"]
            self.session.pause()
            self.session.stop()
            self.assertTrue(runtime._closed)
            self.assertFalse(runtime.store._writer.is_alive())
            second = self.session.start(self.config)
            runtime = self.session.runtime
            self.assertTrue(runtime.sensor.started.wait(timeout=2))
            self.assertNotEqual(first_session_id, second["session_id"])
            restarted = self.session.status()
            self.assertFalse(runtime.store.get_state("collector_pause")["paused"])
            self.assertEqual(restarted["collector"]["status"], "running")
            self.assertTrue(restarted["controls"]["pause"])
            self.assertFalse(restarted["controls"]["resume"])
            runtime.request_stop()
            status = self.session.status()
            self.assertEqual(status["collector"]["status"], "stopped")
            self.assertTrue(runtime._closed)
            self.assertIsNone(runtime.sensor_thread)
            self.assertIsNone(runtime.http_thread)
            self.assertFalse(runtime.store._writer.is_alive())
            self.assertIsNone(self.session.runtime)
            self.assertEqual(self.guard.release_calls, 2)

    @unittest.skipUnless(sys.platform == "win32", "Windows named-handle guard")
    def test_real_windows_guard_blocks_external_and_releases_across_threads(self) -> None:
        name = "Local\\MirallyCaptureSessionTest-" + uuid.uuid4().hex
        external = ActivityProcessLock(name=name)
        self.assertTrue(external.acquire())
        self.addCleanup(external.release)

        def guard_factory(*, fallback_path):
            return ActivityProcessLock(name=name, fallback_path=fallback_path)

        with patch("mirally.runtime.activity.capture_session.ActivityProcessLock", side_effect=guard_factory):
            with self.assertRaisesRegex(RuntimeError, "another Mirally Activity collector"):
                self.session.start(self.config)
            self.assertEqual(FakeRuntime.created, [])
            external.release()
            FakeRuntime.guard = None
            self.session.start(self.config)
            competitor = ActivityProcessLock(name=name)
            self.assertFalse(competitor.acquire())
            failures = []

            def stop():
                try:
                    self.session.close()
                except Exception as error:
                    failures.append(error)

            worker = threading.Thread(target=stop)
            worker.start()
            worker.join(timeout=3)
            self.assertFalse(worker.is_alive())
            self.assertEqual(failures, [])
            self.assertTrue(competitor.acquire())
            competitor.release()
        self.assertFalse(self.config.database_path.parent.exists())


if __name__ == "__main__":
    unittest.main()
