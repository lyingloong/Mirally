"""Deterministic tests for the product-owned activity data layer."""

from __future__ import annotations

import tempfile
import time
import unittest
import sqlite3
from dataclasses import replace
from pathlib import Path

from mirally.core.activity import (
    ActivityPolicy,
    Actor,
    CollectionLevel,
    Observation,
    ObservationStore,
)
from mirally.core.activity.store import CursorExpiredError
from mirally.platform.activity.foreground import ForegroundSnapshot
from mirally.platform.activity.browser_url import BrowserURLResult, normalize_browser_url
from mirally.platform.activity.input_metadata import InputMetadataAggregator
from mirally.platform.activity.input_pump import InputPumpStatus, WindowsInputMetadataSource
from mirally.platform.activity.uia_structure import UIAStructureResult
from mirally.platform.activity.ocr import OCRResult
from mirally.platform.activity.wgc_capture import CaptureResult, VisualSignatureGate
from mirally.runtime.activity.config import ActivityConfig, load_activity_config
from mirally.runtime.activity.lock import ActivityProcessLock
from mirally.runtime.activity.service import ActivitySensor


class _FakeSource:
    _boot = "boot_test"

    def __init__(self):
        self.value = None

    def poll(self):
        value, self.value = self.value, None
        return value


class _FakeUIA:
    available = True

    def __init__(self, result):
        self.result = result
        self.calls = []

    def inspect(self, hwnd, **kwargs):
        self.calls.append((hwnd, kwargs))
        return self.result


class _FakeBrowser:
    available = True

    def __init__(self, url="https://example.test/inbox"):
        self.url = url
        self.calls = []

    @staticmethod
    def supports_app(app_id):
        return app_id == "msedge.exe"

    def read(self, hwnd, **kwargs):
        self.calls.append((hwnd, kwargs))
        return BrowserURLResult("ok", url=self.url)


class _FakeCapture:
    available = True

    def __init__(self, signature=None):
        self.signature = signature or {"dhash": "capture-1", "mean": 120.0}
        self.calls = []

    def capture(self, hwnd, output_path, **kwargs):
        self.calls.append((hwnd, kwargs))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"fake-png")
        return CaptureResult(
            "ok", path=output_path, width=320, height=200, signature=self.signature
        )


class _FakeOCR:
    available = True

    def __init__(self, result=None):
        self.result = result or OCRResult(
            "ok", text="Mirally test", lines=({"text": "Mirally test", "words": []},), language="en-US"
        )
        self.calls = []

    def recognize(self, image_path, **kwargs):
        self.calls.append((image_path, kwargs))
        return self.result


def _observation(source_seq: int, *, kind: str = "test") -> Observation:
    return Observation.create(
        kind=kind,
        source_id="test-source",
        source_epoch="test-epoch",
        source_seq=source_seq,
        boot_id="boot-test",
        payload={"sequence": source_seq},
    )


class ActivityStoreTests(unittest.TestCase):
    def test_append_is_idempotent_and_cursor_order_is_durable(self):
        with tempfile.TemporaryDirectory() as directory:
            with ObservationStore(Path(directory) / "observations.sqlite3") as store:
                first = store.append(_observation(0))
                duplicate = store.append(_observation(0))
                second = store.append(_observation(1))
                self.assertEqual(first.ingest_seq, duplicate.ingest_seq)
                self.assertEqual((first.ingest_seq, second.ingest_seq), (1, 2))
                self.assertEqual(store.get(first.observation_id).observation_id, first.observation_id)
                self.assertEqual(store.get_recent(3600)[-1].observation_id, second.observation_id)
                rows, cursor, has_more = store.read_since(0, limit=1)
                self.assertEqual([row.ingest_seq for row in rows], [1])
                self.assertEqual(cursor, 1)
                self.assertTrue(has_more)
                store.ack("consumer", "all", cursor)
                self.assertEqual(store.cursor("consumer", "all")[1], 1)

    def test_observation_round_trip_preserves_quality_and_privacy_fields(self):
        value = Observation.create(
            kind="context_snapshot",
            source_id="test-source",
            source_epoch="epoch",
            source_seq=3,
            boot_id="boot",
            actor=Actor.UNKNOWN,
            collection_level=CollectionLevel.STRUCTURE,
            payload={"trigger": "foreground_settled"},
            missing_reasons=("pixels_not_authorized",),
            quality="partial",
            truncated=True,
        )
        restored = Observation.from_dict(value.to_dict())
        self.assertEqual(restored.payload, value.payload)
        self.assertEqual(restored.missing_reasons, value.missing_reasons)
        self.assertTrue(restored.truncated)

    def test_asset_publish_is_atomic_from_reader_perspective(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with ObservationStore(root / "observations.sqlite3", asset_directory=root / "assets") as store:
                reservation = store.reserve_asset(
                    mime_type="application/octet-stream", scope="test", policy_version=1
                )
                self.assertEqual(store.asset(reservation.asset_id)["status"], "pending")
                stored = store.commit_asset_bytes(
                    reservation,
                    b"private fixture bytes",
                    Observation.create(
                        kind="context_snapshot",
                        source_id="test-source",
                        source_epoch="epoch",
                        source_seq=0,
                        boot_id="boot",
                        collection_level=CollectionLevel.CONTENT,
                        asset_refs=(reservation.asset_id,),
                    ),
                )
                asset = store.asset(reservation.asset_id)
                self.assertEqual(asset["status"], "ready")
                self.assertEqual(asset["observation_id"], stored.observation_id)
                self.assertEqual(store.resolve_asset(reservation.asset_id, scope="other")["status"], "denied")
                self.assertEqual(store.resolve_asset(reservation.asset_id, scope="test")["status"], "available")
                self.assertEqual(reservation.final_path.read_bytes(), b"private fixture bytes")
                self.assertFalse(reservation.temp_path.exists())

    def test_cleanup_uses_tombstones_and_does_not_publish_missing_assets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with ObservationStore(root / "observations.sqlite3", asset_directory=root / "assets") as store:
                reservation = store.reserve_asset(
                    mime_type="application/octet-stream",
                    scope="test",
                    policy_version=1,
                    expires_at_utc_ms=10,
                )
                store.commit_asset_bytes(
                    reservation,
                    b"expire me",
                    Observation.create(
                        kind="context_snapshot",
                        source_id="test-source",
                        source_epoch="epoch",
                        source_seq=9,
                        boot_id="boot",
                        asset_refs=(reservation.asset_id,),
                        observed_at_utc_ms=1,
                    ),
                )
                cleanup_now = 86_400_020
                result = store.cleanup(now_utc_ms=cleanup_now, observation_retention_days=1)
                self.assertEqual(result["assets_finalized"], 1)
                self.assertEqual(store.asset(reservation.asset_id)["status"], "expired")
                self.assertFalse(reservation.final_path.exists())
                # The observation is retained for the first pass and becomes
                # eligible only after the asset tombstone is finalized.
                self.assertEqual(store.cleanup(now_utc_ms=cleanup_now, observation_retention_days=1)["observations_deleted"], 1)

    def test_old_cursor_is_explicitly_expired_after_deletion(self):
        with tempfile.TemporaryDirectory() as directory:
            with ObservationStore(Path(directory) / "observations.sqlite3") as store:
                store.append(_observation(0))
                connection = store._connect()
                try:
                    connection.execute("DELETE FROM observations WHERE ingest_seq = 1")
                finally:
                    connection.close()
                store.append(_observation(1))
                with self.assertRaises(CursorExpiredError):
                    store.read_since(0)

    def test_schema_v1_is_migrated_for_ocr_summary(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "observations.sqlite3"
            with ObservationStore(path) as store:
                store.append(_observation(0))
            connection = sqlite3.connect(path)
            try:
                connection.execute("ALTER TABLE observations DROP COLUMN ocr_summary_json")
                connection.execute(
                    "UPDATE schema_meta SET value='1' WHERE key='schema_version'"
                )
                connection.commit()
            finally:
                connection.close()
            with ObservationStore(path) as store:
                restored = store.get_recent(3600)
                self.assertEqual(len(restored), 1)
                self.assertEqual(restored[0].ocr_summary, {})


class ActivityPolicyTests(unittest.TestCase):
    def test_policy_denies_expensive_platform_calls_before_collection(self):
        policy = ActivityPolicy.from_mapping(
            {
                "enabled": True,
                "collection": {"default_level": "structure"},
                "privacy": {"excluded_apps": ["password*"]},
            }
        )
        self.assertFalse(policy.decide("content", app_id="editor").allowed)
        self.assertEqual(policy.decide("content", app_id="editor").reason, "level_not_enabled")
        self.assertFalse(policy.decide("metadata", app_id="password-manager").allowed)
        self.assertFalse(policy.decide("structure", app_id="editor", desktop_state="secure").allowed)

    def test_product_processes_are_excluded_before_foreground_collection(self):
        policy = ActivityPolicy(enabled=True).with_self_apps(("mirally.exe",))
        decision = policy.decide("metadata", app_id="mirally.exe")
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "self_excluded")


class ActivityConfigTests(unittest.TestCase):
    def test_enabled_config_reports_missing_baseline_at_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            valid = root / "activity.yaml"
            valid.write_text(
                """
enabled: true
collection:
  default_level: structure
input:
  enabled: true
ui_structure:
  enabled: true
""",
                encoding="utf-8",
            )
            config = load_activity_config(root, valid)
            self.assertTrue(config.input_enabled)
            self.assertTrue(config.ui_structure_enabled)
            self.assertEqual(config.policy.default_level, CollectionLevel.STRUCTURE)

            invalid = root / "invalid.yaml"
            invalid.write_text(
                """
enabled: true
collection:
  default_level: metadata
""",
                encoding="utf-8",
            )
            config = load_activity_config(root, invalid)
            self.assertTrue(config.enabled)
            self.assertEqual(config.policy.default_level, CollectionLevel.METADATA)


class InputMetadataTests(unittest.TestCase):
    def test_aggregator_exposes_counts_but_no_raw_input_content(self):
        aggregator = InputMetadataAggregator()
        aggregator.keyboard(now_monotonic_ns=1)
        aggregator.keyboard(now_monotonic_ns=2)
        aggregator.click(now_monotonic_ns=3)
        aggregator.scroll(now_monotonic_ns=4)
        aggregator.motion(now_monotonic_ns=5)
        batch = aggregator.flush(now_monotonic_ns=6, quiet_ms=1500)
        self.assertEqual((batch.keyboard_count, batch.click_count, batch.scroll_count), (2, 1, 1))
        self.assertTrue(batch.motion)
        self.assertFalse(hasattr(batch, "key_values"))
        self.assertIsNone(aggregator.flush())

    def test_aggregator_flushes_a_long_burst_with_a_bound(self):
        aggregator = InputMetadataAggregator(max_burst_ms=10)
        aggregator.motion(now_monotonic_ns=100)
        self.assertTrue(aggregator.should_flush(now_monotonic_ns=10_100_000, quiet_ms=999_999))

    def test_input_source_is_disabled_without_windows(self):
        source = WindowsInputMetadataSource()
        if source.available:
            self.skipTest("non-Windows lifecycle assertion")
        self.assertEqual(source.status, InputPumpStatus(False, False, "not_windows"))
        self.assertFalse(source.start(lambda batch: None))

    def test_visual_signature_gate_only_commits_successful_baselines(self):
        gate = VisualSignatureGate(max_entries=8)
        signature = {"dhash": "abc", "mean": 10}
        self.assertTrue(gate.changed("window", signature))
        gate.commit("window", signature)
        self.assertFalse(gate.changed("window", signature))
        self.assertTrue(gate.changed("window", {"dhash": "def", "mean": 10}))
        self.assertTrue(gate.changed("other", signature))


class ActivityLockTests(unittest.TestCase):
    def test_two_process_locks_cannot_share_the_same_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sensor.lock"
            # Windows uses a named mutex instead of the fallback file. Isolate
            # this pair from any real collector already running on the machine.
            name = "Local\\MirallyActivitySensorTest-" + Path(directory).name
            first = ActivityProcessLock(name=name, fallback_path=path)
            second = ActivityProcessLock(name=name, fallback_path=path)
            try:
                self.assertTrue(first.acquire())
                self.assertFalse(second.acquire())
                first.release()
                self.assertTrue(second.acquire())
            finally:
                second.release()
                first.release()


class ActivitySensorTests(unittest.TestCase):
    def _config(self, root: Path, level: str = "metadata") -> ActivityConfig:
        policy = ActivityPolicy(enabled=True, default_level=level)
        return ActivityConfig(
            True,
            root / "observations.sqlite3",
            root / "assets",
            0.01,
            policy,
            14,
            24,
            1024,
            128,
        )

    def test_foreground_is_coalesced_but_input_is_not_dropped_by_visual_dedup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = _FakeSource()
            sensor = ActivitySensor(self._config(root), source=source)
            try:
                sensor.start()
                snapshot = ForegroundSnapshot(
                    hwnd=12,
                    pid=22,
                    process_instance_id="proc_a",
                    window_instance_id="window_a",
                    app_id="editor",
                    observed_at_utc_ms=100,
                    monotonic_ns=100,
                    identity_confidence="verified_process_start",
                    boot_id="boot_test",
                )
                first = sensor.record_foreground(snapshot)
                self.assertIsNotNone(first)
                self.assertEqual(first.identity_confidence, "verified_process_start")
                self.assertIsNone(sensor.record_foreground(snapshot))
                self.assertIsNotNone(sensor.record_input_batch(keyboard_count=2, quiet_ms=1500))
                rows = sensor.store.query()
                self.assertEqual([row.kind for row in rows], ["foreground_changed", "input_batch"])
                self.assertEqual(rows[1].app_id, "editor")
            finally:
                sensor.close()

    def test_window_context_collects_title_and_browser_url_and_tracks_navigation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            browser = _FakeBrowser()
            config = replace(self._config(root), browser_url_poll_interval_seconds=0)
            sensor = ActivitySensor(config, source=_FakeSource(), browser_source=browser)
            try:
                sensor.start()
                snapshot = ForegroundSnapshot(
                    hwnd=12,
                    pid=22,
                    process_instance_id="proc_a",
                    window_instance_id="window_a",
                    app_id="msedge.exe",
                    observed_at_utc_ms=100,
                    monotonic_ns=100,
                    identity_confidence="verified_process_start",
                    boot_id="boot_test",
                    title_available=True,
                    window_title="Inbox - Microsoft Edge",
                )
                sensor.record_foreground(snapshot)
                browser.url = "https://example.test/settings"
                sensor.record_foreground(snapshot)
                rows = sensor.store.query()
                contexts = [row for row in rows if row.kind == "window_context"]
                self.assertEqual(len(contexts), 2)
                self.assertEqual(contexts[0].payload["window_title"], "Inbox - Microsoft Edge")
                self.assertEqual(contexts[0].payload["browser_url"], "https://example.test/inbox")
                self.assertEqual(contexts[1].payload["browser_url"], "https://example.test/settings")
                self.assertEqual(len(browser.calls), 2)
            finally:
                sensor.close()

    def test_browser_url_normalizer_rejects_search_text(self):
        self.assertEqual(normalize_browser_url(" https://example.test/a "), "https://example.test/a")
        self.assertIsNone(normalize_browser_url("search terms"))

    def test_window_context_respects_opt_out_and_policy_before_browser_calls(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            browser = _FakeBrowser()
            config = replace(self._config(root), window_titles_enabled=False, browser_urls_enabled=False)
            sensor = ActivitySensor(config, source=_FakeSource(), browser_source=browser)
            snapshot = ForegroundSnapshot(
                12, 22, "proc_a", "window_a", "msedge.exe", 100, 100,
                "verified_process_start", "boot_test", True, "Inbox",
            )
            try:
                sensor.record_foreground(snapshot)
                self.assertEqual([row.kind for row in sensor.store.query()], ["foreground_changed"])
                self.assertEqual(browser.calls, [])
                sensor.config = replace(config, window_titles_enabled=True, browser_urls_enabled=True)
                sensor.policy = replace(sensor.policy, excluded_apps=("msedge.exe",))
                sensor.record_foreground(snapshot)
                self.assertEqual(browser.calls, [])
                sensor.policy = replace(sensor.policy, excluded_apps=())
                sensor.pause()
                sensor.record_foreground(snapshot)
                self.assertEqual(browser.calls, [])
                sensor.resume()
                sensor.record_foreground(snapshot)
                self.assertEqual(len(browser.calls), 1)
            finally:
                sensor.close()

    def test_browser_url_polling_is_bounded_and_unchanged_context_is_deduplicated(self):
        with tempfile.TemporaryDirectory() as directory:
            browser = _FakeBrowser()
            sensor = ActivitySensor(self._config(Path(directory)), source=_FakeSource(), browser_source=browser)
            snapshot = ForegroundSnapshot(
                12, 22, "proc_a", "window_a", "msedge.exe", 100, 100,
                "verified_process_start", "boot_test", True, "Inbox",
            )
            try:
                sensor.record_foreground(snapshot)
                sensor.record_foreground(snapshot)
                self.assertEqual(len(browser.calls), 1)
                self.assertEqual(len(sensor.store.query(kinds=["window_context"])), 1)
                sensor.record_foreground(replace(snapshot, window_title="Settings"))
                self.assertEqual(len(browser.calls), 2)
                self.assertEqual(len(sensor.store.query(kinds=["window_context"])), 2)
            finally:
                sensor.close()

    def test_agent_lease_makes_mixed_input_unknown(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sensor = ActivitySensor(self._config(root), source=_FakeSource())
            try:
                sensor.start()
                lease = sensor.begin_agent_control("task-1")
                event = sensor.record_input_batch(click_count=1)
                self.assertEqual(event.actor, Actor.UNKNOWN)
                self.assertEqual(event.control_owner, "agent")
                self.assertTrue(sensor.heartbeat_agent_control(lease.lease_id))
                self.assertTrue(sensor.end_agent_control(lease.lease_id))
            finally:
                sensor.close()

    def test_foreground_emits_bounded_uia_structure_after_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake_uia = _FakeUIA(
                UIAStructureResult(
                    "ok",
                    node_count=4,
                    role_counts={"Window": 1, "Button": 3},
                    max_depth=2,
                    nodes=({"role": "Window", "depth": 0, "offscreen": False, "bounds": {}},),
                )
            )
            config = ActivityConfig(
                **{
                    **self._config(root, level="structure").__dict__,
                    "ui_structure_enabled": True,
                }
            )
            sensor = ActivitySensor(config, source=_FakeSource(), uia_source=fake_uia)
            try:
                sensor.start()
                snapshot = ForegroundSnapshot(
                    hwnd=34,
                    pid=22,
                    process_instance_id="proc_a",
                    window_instance_id="window_a",
                    app_id="editor",
                    observed_at_utc_ms=100,
                    monotonic_ns=100,
                    identity_confidence="verified_process_start",
                    boot_id="boot_test",
                )
                metadata = sensor.record_foreground(snapshot)
                rows = sensor.store.query()
                self.assertEqual([row.kind for row in rows], ["foreground_changed", "context_snapshot"])
                self.assertEqual(rows[1].caused_by, (metadata.observation_id,))
                self.assertEqual(rows[1].uia_summary["node_count"], 4)
                self.assertEqual(rows[1].uia_summary["nodes"][0]["role"], "Window")
                self.assertEqual(fake_uia.calls[0][0], 34)
                self.assertNotIn("name", rows[1].uia_summary)
            finally:
                sensor.close()

    def test_uia_timeout_keeps_foreground_fact_and_marks_snapshot_unavailable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake_uia = _FakeUIA(UIAStructureResult("timeout", missing_reasons=("worker_deadline",)))
            config = ActivityConfig(
                **{
                    **self._config(root, level="structure").__dict__,
                    "ui_structure_enabled": True,
                }
            )
            sensor = ActivitySensor(config, source=_FakeSource(), uia_source=fake_uia)
            try:
                sensor.start()
                sensor.record_foreground(
                    ForegroundSnapshot(
                        hwnd=35,
                        pid=22,
                        process_instance_id="proc_a",
                        window_instance_id="window_a",
                        app_id="editor",
                        observed_at_utc_ms=100,
                        monotonic_ns=100,
                        identity_confidence="pid_only",
                        boot_id="boot_test",
                    )
                )
                rows = sensor.store.query()
                self.assertEqual(rows[0].kind, "foreground_changed")
                self.assertEqual(rows[1].quality, "unavailable")
                self.assertEqual(rows[1].missing_reasons, ("worker_deadline",))
            finally:
                sensor.close()

    def test_disabled_sensor_does_not_write_desktop_facts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self._config(root)
            config = ActivityConfig(False, config.database_path, config.asset_directory, 1, ActivityPolicy(False), 14, 24, 1, 128)
            sensor = ActivitySensor(config, source=_FakeSource())
            try:
                sensor.start()
                self.assertIsNone(sensor.record_input_batch(click_count=1))
                self.assertEqual(sensor.store.query(), [])
            finally:
                sensor.close()

    def test_content_policy_captures_and_persists_ocr_without_defaulting_to_pixels(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = self._config(root, level=CollectionLevel.CONTENT)
            config = ActivityConfig(
                **{
                    **base.__dict__,
                    "visual_enabled": True,
                    "visual_persist_images": False,
                    "ocr_enabled": True,
                    "ocr_languages": ("en-US",),
                }
            )
            capture = _FakeCapture()
            ocr = _FakeOCR()
            sensor = ActivitySensor(
                config,
                source=_FakeSource(),
                visual_source=capture,
                ocr_source=ocr,
            )
            try:
                sensor.start()
                metadata = sensor.record_foreground(
                    ForegroundSnapshot(
                        hwnd=44,
                        pid=22,
                        process_instance_id="proc_a",
                        window_instance_id="window_a",
                        app_id="editor",
                        observed_at_utc_ms=100,
                        monotonic_ns=100,
                        identity_confidence="verified_process_start",
                        boot_id="boot_test",
                    )
                )
                self.assertIsNotNone(metadata)
                rows = sensor.store.query()
                content = rows[-1]
                self.assertEqual(content.kind, "content_snapshot")
                self.assertEqual(content.ocr_summary["text"], "Mirally test")
                self.assertEqual(content.visual_summary["status"], "ok")
                self.assertEqual(len(capture.calls), 1)
                self.assertEqual(len(ocr.calls), 1)
                self.assertEqual(content.asset_refs, ())
            finally:
                sensor.close()

    def test_content_policy_denies_before_capture(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = self._config(root, level=CollectionLevel.STRUCTURE)
            config = ActivityConfig(**{**base.__dict__, "visual_enabled": True, "ocr_enabled": True})
            capture = _FakeCapture()
            sensor = ActivitySensor(config, source=_FakeSource(), visual_source=capture, ocr_source=_FakeOCR())
            try:
                sensor.start()
                sensor.record_foreground(
                    ForegroundSnapshot(
                        hwnd=45,
                        pid=22,
                        process_instance_id="proc_a",
                        window_instance_id="window_a",
                        app_id="editor",
                        observed_at_utc_ms=100,
                        monotonic_ns=100,
                        identity_confidence="pid_only",
                        boot_id="boot_test",
                    )
                )
                self.assertEqual(capture.calls, [])
                self.assertNotIn("content_snapshot", [row.kind for row in sensor.store.query()])
            finally:
                sensor.close()

    def test_content_asset_is_committed_with_the_same_observation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = self._config(root, level=CollectionLevel.CONTENT)
            config = ActivityConfig(
                **{
                    **base.__dict__,
                    "visual_enabled": True,
                    "visual_persist_images": True,
                    "ocr_enabled": False,
                }
            )
            capture = _FakeCapture()
            sensor = ActivitySensor(config, source=_FakeSource(), visual_source=capture)
            try:
                sensor.start()
                sensor.record_foreground(
                    ForegroundSnapshot(
                        hwnd=46,
                        pid=22,
                        process_instance_id="proc_a",
                        window_instance_id="window_a",
                        app_id="editor",
                        observed_at_utc_ms=100,
                        monotonic_ns=100,
                        identity_confidence="pid_only",
                        boot_id="boot_test",
                    )
                )
                content = sensor.store.query()[-1]
                self.assertEqual(content.kind, "content_snapshot")
                self.assertEqual(len(content.asset_refs), 1)
                asset = sensor.store.asset(content.asset_refs[0])
                self.assertEqual(asset["status"], "ready")
                self.assertTrue(sensor.store.resolve_asset(content.asset_refs[0], scope="activity-content")["status"] == "available")
            finally:
                sensor.close()


if __name__ == "__main__":
    unittest.main()
