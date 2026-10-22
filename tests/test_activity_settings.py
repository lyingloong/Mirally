"""Activity settings inspect real local stores without starting any services."""

from __future__ import annotations

import hashlib
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import yaml

from mirally.core.activity.store import ObservationStore
from mirally.core.memory import MemoryStore
from mirally.runtime.activity.settings import ActivitySettings
from mirally.runtime.memory.config import load_memory_config


class _Host:
    def __init__(self, root: Path, servers=None, plugin=None, runtime=None) -> None:
        self.config = root / "openclaw.json"
        self.servers = servers or {}
        self.plugin = plugin or {}
        self.runtime = runtime
        self.calls = []

    def _native_json(self, *args):
        self.calls.append(args)
        if args[:3] == ("config", "get", "mcp.servers"):
            return self.servers
        if args[:3] == ("config", "get", "plugins.entries.mirally-memory.config"):
            return self.plugin
        if args[:3] == ("gateway", "call", "mirally.memory.status") and self.runtime is not None:
            return self.runtime
        raise RuntimeError("unavailable")


class _Capture:
    """Deterministic stand-in; real collector lifecycle has separate tests."""

    def __init__(self):
        self.runtime = None
        self.calls = []
        self.paused = False
        self.session_id = None
        self.session_seq = 0

    @property
    def url(self):
        return "http://127.0.0.1:32100/?token=fixture" if self.runtime else ""

    def start(self, config):
        self.calls.append(("start", config))
        self.session_seq += 1
        self.session_id = f"fixture-{self.session_seq}"
        self.runtime = SimpleNamespace(config=replace(config, enabled=True, policy=replace(config.policy, enabled=True)))
        return self.status()

    def status(self):
        if self.runtime is None:
            return None
        return {"database": str(self.runtime.config.database_path), "session_id": self.session_id,
            "collector": {"status": "paused" if self.paused else "running", "snapshot": False,
                          "owned": True, "live_enabled": True},
            "controls": {"start": False, "pause": not self.paused, "resume": self.paused, "stop": True}}

    def check(self, expected_session_id):
        if expected_session_id is not None and expected_session_id != self.session_id:
            raise RuntimeError("采集会话已变化，请刷新页面")

    def pause(self, expected_session_id=None):
        self.check(expected_session_id)
        self.calls.append(("pause",))
        self.paused = True
        return self.status()

    def resume(self, expected_session_id=None):
        self.check(expected_session_id)
        self.calls.append(("resume",))
        self.paused = False
        return self.status()

    def stop(self, expected_session_id=None):
        self.check(expected_session_id)
        self.calls.append(("stop",))
        self.runtime = None
        return None

    def close(self):
        self.stop()


class ActivitySettingsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory(prefix="mirally 设置 ")
        self.root = Path(self.directory.name).resolve()
        self.backends = []
        self.patches = [patch("mirally.runtime.activity.config.default_data_directory",
                              return_value=self.root / "user/activity"),
                        patch("mirally.runtime.memory.config.default_data_directory",
                              return_value=self.root / "user/activity")]
        for item in self.patches:
            item.start()

    def tearDown(self) -> None:
        for backend in reversed(self.backends):
            backend.close()
        for item in reversed(self.patches):
            item.stop()
        self.directory.cleanup()

    def backend(self, host=None):
        backend = ActivitySettings(self.root, host)
        self.backends.append(backend)
        return backend

    @staticmethod
    def write(path: Path, content: dict) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(content, allow_unicode=True), encoding="utf-8")
        return path

    def activity(self, path: Path | None = None, database="data/observations.sqlite3") -> Path:
        return self.write(path or self.root / "config/mirally/activity.yaml",
                          {"enabled": False, "storage": {"database_path": database}})

    def seed_activity(self, database: Path) -> None:
        with ObservationStore(database) as store:
            store.set_state("collector", {"status": "running", "started_at_utc_ms": 100})
            store.set_state("collector_diagnostics", {"status": "degraded", "updated_at_utc_ms": 123,
                "metrics": {"capture_calls": 2}, "sources": {"uia": {"available": False}}})

    @staticmethod
    def digest(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def test_combined_official_profile_read_and_reused_runtime_path(self) -> None:
        self.activity()
        host = _Host(self.root)
        profile_calls, runtime_calls = [], []
        def profiles():
            profile_calls.append(True)
            return {"servers": {}, "plugin": {}, "defaults": {"model": {"primary": "portable/model"}}}
        def runtime():
            runtime_calls.append(True)
            return {"running": True, "ready": True, "worker": {"maintenanceRunning": True}}
        host.activity_profiles = profiles
        host.memory_runtime_status = runtime
        backend = self.backend(host)
        first, second = backend.status(), backend.status()
        self.assertEqual(len(profile_calls), 1)
        self.assertEqual(len(runtime_calls), 2)
        self.assertEqual(host.calls, [])
        self.assertEqual(first["profiles"]["effective_model"], "portable/model")
        self.assertTrue(second["memory"]["runtime"]["verified"])
        backend.refresh_profiles()
        backend.status()
        self.assertEqual(len(profile_calls), 2)
        backend.set_enabled(False)
        self.assertEqual(len(profile_calls), 3, "saving must force a fresh official profile verification")

    def test_combined_read_failure_never_falls_back_or_writes_guessed_profile(self) -> None:
        path = self.activity()
        original = path.read_bytes()
        host = _Host(self.root)
        reads = []
        def profiles():
            reads.append(True)
            raise RuntimeError("official read unavailable")
        host.activity_profiles = profiles
        backend = self.backend(host)
        backend._capture = capture = _Capture()
        status = backend.status()
        self.assertFalse(status["profiles"]["activity_profile_verified"])
        self.assertFalse(status["profiles"]["memory_profile_verified"])
        with self.assertRaisesRegex(RuntimeError, "无法确认有效活动配置"):
            backend.set_enabled(True)
        self.assertEqual(len(reads), 2)
        self.assertFalse(any(args[:2] == ("config", "get") for args in host.calls))
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(capture.calls, [])

    def test_snapshot_pause_reason_is_visible_and_clears_on_resume(self) -> None:
        self.activity()
        database = self.root / "data/observations.sqlite3"
        self.seed_activity(database)
        backend = self.backend()
        with ObservationStore(database) as store:
            store.set_state("collector_pause", {"paused": True, "reason": "semantic_memory_pending_storage_quota"})
        self.assertEqual(backend.status()["activity"]["collector"]["pause_reason"], "semantic_memory_pending_storage_quota")
        with ObservationStore(database) as store:
            store.set_state("collector_pause", {"paused": False, "reason": "user"})
        self.assertIsNone(backend.status()["activity"]["collector"]["pause_reason"])

    def test_memory_browser_filters_are_validated_before_profile_or_database_reads(self) -> None:
        backend = self.backend()
        for filters in ({"provenances": None}, {"provenances": "observed"}, {"provenances": [True]},
                        {"provenances": ["unknown"]}, {"provenances": ["observed"] * 4},
                        {"kinds": None}, {"kinds": ["unknown"]}, {"kinds": [False]},
                        {"start_utc_ms": None}, {"start_utc_ms": True}, {"start_utc_ms": -1},
                        {"end_utc_ms": 2**53}, {"start_utc_ms": 100, "end_utc_ms": 100},
                        {"start_utc_ms": 200, "end_utc_ms": 100}, {"path": "private"}):
            with self.subTest(filters=filters), patch.object(backend, "_memory_reader") as reader:
                with self.assertRaises(ValueError):
                    backend.memory_search(**filters)
                reader.assert_not_called()
        self.assertEqual(list(self.root.iterdir()), [])
        result = backend.memory_search(provenances=["model_inference"], kinds=["episodic"],
                                       start_utc_ms=100, end_utc_ms=200)
        self.assertEqual(result["memories"], [])
        self.assertEqual(list(self.root.iterdir()), [])

    def test_memory_browser_filter_arguments_reach_reader_without_implied_source_filter(self) -> None:
        backend = self.backend()
        filters = {"provenances": ["model_inference", "user_confirmed"], "kinds": ["episodic", "task"],
                   "start_utc_ms": 100, "end_utc_ms": 200}
        with patch.object(backend, "_memory_reader") as reader:
            backend.memory_search("project", include_candidates=True, limit=7, offset=14, **filters)
            reader.return_value.handle.assert_called_once_with("search", {"query": "project", "include_candidates": True,
                "limit": 7, "offset": 14, **filters})
            reader.return_value.handle.reset_mock()
            backend.memory_search()
            self.assertNotIn("provenances", reader.return_value.handle.call_args.args[1])

    def test_owned_pause_reads_active_database_after_profile_changes(self) -> None:
        self.activity()
        backend = self.backend()
        backend._capture = capture = _Capture()
        backend.start_capture()
        database = self.root / "data/observations.sqlite3"
        self.seed_activity(database)
        with ObservationStore(database) as store:
            store.set_state("collector_pause", {"paused": True, "reason": "semantic_memory_pending_storage_quota"})
        capture.paused = True
        self.activity(database="other.sqlite3")
        self.assertEqual(backend.status()["activity"]["collector"]["pause_reason"], "semantic_memory_pending_storage_quota")
        capture.paused = False
        self.assertIsNone(backend.status()["activity"]["collector"]["pause_reason"])

    def test_owned_live_pause_interval_overrides_stale_user_pause(self) -> None:
        self.activity()
        backend = self.backend()
        backend._capture = capture = _Capture()
        backend.start_capture()
        self.seed_activity(self.root / "data/observations.sqlite3")
        with ObservationStore(self.root / "data/observations.sqlite3") as store:
            store.set_state("collector_pause", {"paused": True, "reason": "user"})
        capture.paused = True
        original = capture.status
        capture.status = lambda: {**original(), "collector_diagnostics": {"pause_intervals": [
            {"reason": "semantic_memory_pending_storage_quota", "ended_at_utc_ms": None}]}}
        self.assertEqual(backend.status()["activity"]["collector"]["pause_reason"], "semantic_memory_pending_storage_quota")

    def test_unconfigured_panel_and_queries_create_nothing(self) -> None:
        backend = self.backend()
        before = sorted(self.root.rglob("*"))
        status = backend.status()
        self.assertFalse(status["activity"]["configured"])
        self.assertFalse(status["activity"]["database_exists"])
        self.assertEqual(status["memory"]["health"]["status"], "not_ready")
        self.assertTrue(status["memory"]["raw_retention"]["configured_enabled"])
        self.assertTrue(status["memory"]["raw_retention"]["enabled"])
        self.assertFalse(status["memory"]["runtime"]["verified"])
        self.assertTrue(status["profiles"]["warning"])
        self.assertEqual(backend.memory_search()["memories"], [])
        self.assertIsNone(backend.memory_read("missing")["memory"])
        with patch("mirally.runtime.activity.settings.webbrowser.open") as browser:
            with self.assertRaisesRegex(FileNotFoundError, "历史数据库"):
                backend.open_browser()
            browser.assert_not_called()
        self.assertEqual(sorted(self.root.rglob("*")), before)

    def test_missing_official_default_profiles_are_readable_without_writes(self) -> None:
        host = _Host(self.root, {"mirally-activity": {"args": ["--config", str(self.root / "config/mirally/activity.yaml")]},
            "mirally-memory": {"args": ["--config", str(self.root / "config/mirally/memory.yaml")]}})
        backend = self.backend(host)
        status = backend.status()
        self.assertIsNone(status['activity']['error'])
        self.assertTrue(status['activity']['enabled'])
        self.assertFalse(status['activity']['configured'])
        self.assertTrue(status['activity']['controls']['set-enabled'])
        self.assertIsNone(status['memory']['error'])
        self.assertFalse(status['memory']['configured'])
        self.assertEqual(list(self.root.iterdir()), [])

    def test_default_auto_start_is_only_an_explicit_desktop_lifecycle_call(self) -> None:
        backend = self.backend()
        backend._capture = capture = _Capture()
        backend.status()
        backend.memory_search()
        self.assertEqual(capture.calls, [])
        self.assertEqual(list(self.root.iterdir()), [])
        backend.auto_start_capture()
        self.assertEqual(capture.calls[0][0], 'start')
        self.assertTrue(capture.calls[0][1].enabled)
        self.assertEqual(capture.calls[0][1].policy.default_level,'content')
        self.assertEqual(list(self.root.iterdir()), [])

    def test_saved_disabled_preference_survives_new_desktop_window(self) -> None:
        backend = self.backend()
        backend._capture = capture = _Capture()
        backend.auto_start_capture()
        result = backend.set_enabled(False)
        self.assertTrue(result['saved'])
        self.assertFalse(result['enabled'])
        self.assertIsNone(capture.runtime)
        other = self.backend()
        other._capture = next_capture = _Capture()
        other.auto_start_capture()
        self.assertEqual(next_capture.calls, [])
        self.assertFalse(other.status()['activity']['enabled'])

    def test_pause_stop_close_do_not_change_enabled_and_reopen_starts(self) -> None:
        path = self.write(self.root / 'config/mirally/activity.yaml', {'enabled':True})
        backend = self.backend()
        backend._capture = capture = _Capture()
        backend.auto_start_capture()
        before = path.read_bytes()
        backend.pause_capture()
        backend.resume_capture()
        backend.stop_capture()
        backend.close()
        self.assertEqual(path.read_bytes(), before)
        backend.auto_start_capture()
        self.assertIsNone(capture.runtime)
        other = self.backend()
        other._capture = next_capture = _Capture()
        other.auto_start_capture()
        self.assertIsNotNone(next_capture.runtime)

    def test_persistent_switch_preserves_custom_profile_paths_scope_and_credentials(self) -> None:
        product = self.root / 'external product'
        path = product / 'profiles/custom.yaml'
        original = {'enabled':False, 'collection':{'default_level':'structure'},
            'visual':{'scope':'foreground','enabled':False}, 'privacy':{'allowed_apps':['editor.exe']},
            'storage':{'database_path':'data/custom.sqlite3','directory':'custom data'},
            'unknown_extension':{'credential':'synthetic-preserve-key'}}
        self.write(path, original)
        host = _Host(self.root, {'mirally-activity':{'cwd':str(product),'args':['--root',str(product),'--config',str(path)]}})
        backend = self.backend(host)
        backend._capture = capture = _Capture()
        result = backend.set_enabled(True)
        self.assertIsNone(result['capture_error'])
        saved = yaml.safe_load(path.read_text(encoding='utf-8'))
        self.assertEqual(saved, {**original, 'enabled':True})
        self.assertEqual(capture.runtime.config.policy.default_level,'structure')
        self.assertEqual(capture.runtime.config.visual_scope,'foreground')
        self.assertFalse(capture.runtime.config.visual_enabled)
        self.assertEqual(capture.runtime.config.database_path, product / 'data/custom.sqlite3')
        self.assertFalse((self.root / 'config/mirally/activity.yaml').exists())

    def test_capture_failure_keeps_saved_preference_and_reports_failure(self) -> None:
        backend = self.backend()
        backend._capture = capture = _Capture()
        with patch.object(capture, 'start', side_effect=RuntimeError('synthetic source unavailable')):
            result = backend.set_enabled(True)
        self.assertTrue(result['saved'])
        self.assertIn('source unavailable', result['capture_error'])
        state = backend.status()['activity']
        self.assertTrue(state['enabled'])
        self.assertFalse(state['live_enabled'])
        self.assertIn('source unavailable', state['auto_start_error'])

    def test_failed_preference_write_does_not_stop_or_start_capture(self) -> None:
        path = self.write(self.root / 'config/mirally/activity.yaml', {'enabled':True})
        backend = self.backend()
        backend._capture = capture = _Capture()
        backend.auto_start_capture()
        before = path.read_bytes()
        capture.calls.clear()
        with patch('mirally.runtime.activity.settings.os.replace', side_effect=OSError('synthetic write failure')):
            with self.assertRaises(OSError):
                backend.set_enabled(False)
        self.assertEqual(path.read_bytes(),before)
        self.assertEqual(capture.calls,[])
        self.assertIsNotNone(capture.runtime)
        self.assertEqual(list(path.parent.glob('*.tmp')),[])

    def test_switch_preserves_yaml_comments_and_other_scalar_text(self) -> None:
        path = self.root / 'config/mirally/activity.yaml'
        path.parent.mkdir(parents=True)
        original = '# private profile\nenabled: true # launch preference\nvisual:\n  scope: foreground # scoped on purpose\nunknown: "synthetic-secret"\n'
        path.write_text(original,encoding='utf-8')
        backend = self.backend()
        backend._capture = _Capture()
        backend.set_enabled(False)
        self.assertEqual(path.read_text(encoding='utf-8'),original.replace('enabled: true','enabled: false'))

    def test_shutdown_failure_keeps_disabled_preference_and_truthful_live_status(self) -> None:
        backend = self.backend()
        backend._capture = capture = _Capture()
        backend.auto_start_capture()
        with patch.object(capture,'stop',side_effect=RuntimeError('synthetic shutdown pending')):
            result = backend.set_enabled(False)
        self.assertTrue(result['saved'])
        self.assertIn('shutdown pending',result['capture_error'])
        state = backend.status()['activity']
        self.assertFalse(state['enabled'])
        self.assertTrue(state['live_enabled'])
        other = self.backend()
        other._capture = next_capture = _Capture()
        other.auto_start_capture()
        self.assertEqual(next_capture.calls,[])

    def test_close_during_auto_start_retires_the_just_started_capture(self) -> None:
        backend = self.backend()
        backend._capture = capture = _Capture()
        entered, release = threading.Event(), threading.Event()
        original_start = capture.start
        def delayed_start(config):
            entered.set()
            if not release.wait(3):
                raise RuntimeError('synthetic test timeout')
            return original_start(config)
        with patch.object(capture,'start',side_effect=delayed_start):
            start = threading.Thread(target=backend.auto_start_capture)
            start.start()
            self.assertTrue(entered.wait(3))
            close = threading.Thread(target=backend.close)
            close.start()
            release.set()
            start.join(3)
            close.join(3)
            self.assertFalse(start.is_alive() or close.is_alive())
        self.assertIsNone(capture.runtime)
        backend.auto_start_capture()
        self.assertIsNone(capture.runtime)

    def test_string_disabled_profile_is_reported_instead_of_starting(self) -> None:
        path = self.write(self.root / 'config/mirally/activity.yaml', {'enabled':'false'})
        backend = self.backend()
        backend._capture = capture = _Capture()
        backend.auto_start_capture()
        self.assertEqual(capture.calls,[])
        self.assertIn('must be a boolean',backend.status()['activity']['auto_start_error'])
        self.assertEqual(yaml.safe_load(path.read_text(encoding='utf-8')),{'enabled':'false'})

    def test_failed_effective_activity_read_never_starts_or_writes_fallback(self) -> None:
        path = self.write(self.root / 'config/mirally/activity.yaml', {'enabled':True})
        host = _Host(self.root)
        native = host._native_json
        def failing_activity(*args):
            if args[:3] == ('config','get','mcp.servers'):
                raise RuntimeError('synthetic effective config unavailable')
            return native(*args)
        backend = self.backend(host)
        backend._capture = capture = _Capture()
        before = path.read_bytes()
        with patch.object(host,'_native_json',side_effect=failing_activity):
            # The Memory plugin read succeeds, which must not authorize fallback
            # Activity scope or allow writing its guessed configuration file.
            status = backend.status()
            self.assertEqual(status['profiles']['source'],'openclaw')
            self.assertFalse(status['profiles']['activity_profile_verified'])
            self.assertFalse(status['activity']['controls']['start'])
            self.assertFalse(status['activity']['controls']['set-enabled'])
            backend.auto_start_capture()
            self.assertIn('无法确认有效活动配置',backend.status()['activity']['auto_start_error'])
            with self.assertRaisesRegex(RuntimeError,'无法确认有效活动配置'):
                backend.start_capture()
            with self.assertRaisesRegex(RuntimeError,'无法确认有效活动配置'):
                backend.set_enabled(False)
        self.assertEqual(capture.calls,[])
        self.assertEqual(path.read_bytes(),before)

    def test_invalid_effective_activity_config_response_is_not_verified(self) -> None:
        host = _Host(self.root)
        backend = self.backend(host)
        backend._capture = capture = _Capture()
        with patch.object(host,'_native_json',return_value=[]):
            backend.auto_start_capture()
            self.assertFalse(backend.status()['profiles']['activity_profile_verified'])
        self.assertEqual(capture.calls,[])
        self.assertEqual(list(self.root.iterdir()),[])

    def test_semantics_save_preserves_private_settings_and_reloads_only_existing_host(self) -> None:
        path = self.root / 'custom memory.yaml'
        original = {'enabled':True,'semantics':{'enabled':False,'model':'custom/old','max_calls_per_hour':7,'max_output_tokens':800},
            'storage':{'database_path':'data/custom-memory.sqlite3','projection_database_path':'data/projection.sqlite3'},
            'unknown':{'credential':'synthetic-preserve'}}
        self.write(path,original)
        host = _Host(self.root,plugin={'root':str(self.root),'configPath':str(path)})
        native = host._native_json
        def respond(*args):
            if args[:3] == ('gateway','call','mirally.memory.reloadSettings'):
                host.calls.append(args)
                return {'applied':True,'settings':load_memory_config(self.root,path).settings()}
            return native(*args)
        backend = self.backend(host)
        backend._capture = capture = _Capture()
        with patch.object(host,'_native_json',side_effect=respond):
            result = backend.set_semantics(True)
        self.assertTrue(result['saved'] and result['applied'])
        self.assertIsNone(result['reload_error'])
        self.assertEqual(yaml.safe_load(path.read_text(encoding='utf-8')),{
            **original,'semantics':{**original['semantics'],'enabled':True}})
        self.assertEqual(capture.calls,[])
        rpc = next(call for call in host.calls if call[:3] == ('gateway','call','mirally.memory.reloadSettings'))
        self.assertEqual(rpc[3:5],('--params','{}'))
        self.assertFalse((self.root / 'config/mirally/memory.yaml').exists())

    def test_model_settings_is_a_lightweight_read_with_effective_host_fallback(self) -> None:
        path = self.write(self.root / "custom memory.yaml", {"semantics": {
            "enabled": False, "max_output_tokens": 2300, "max_calls_per_hour": 7, "max_tokens_per_day": 12345}})
        host = _Host(self.root)
        host.activity_profiles = lambda: {"servers": {}, "plugin": {"configPath": str(path)},
            "defaults": {"model": {"primary": "host/default"}}}
        host.memory_runtime_status = lambda: {"running": True, "ready": True}
        backend = self.backend(host)
        before = path.read_bytes()
        with patch("mirally.runtime.activity.settings.MemoryReader", side_effect=AssertionError("no database queries")):
            result = backend.model_settings()
        self.assertEqual(result["model"], "")
        self.assertEqual(result["effective_model"], "host/default")
        self.assertEqual(result["max_output_tokens"], 2300)
        self.assertEqual(result["max_calls_per_hour"], 7)
        self.assertFalse(result["enabled"])
        self.assertTrue(result["canSave"])
        self.assertTrue(result["runtime"]["verified"])
        self.assertEqual(path.read_bytes(), before)

    def test_model_only_save_preserves_enablement_budgets_unknown_fields_and_custom_profile(self) -> None:
        path = self.root / "portable product" / "profiles" / "memory.yaml"
        for enabled in (False, True):
            original = {"enabled": False, "semantics": {"enabled": enabled, "model": "old/model",
                "max_output_tokens": 800, "max_calls_per_hour": 7, "max_tokens_per_day": 12345,
                "future_option": "retain"}, "unknown": {"synthetic_private": "retain"}}
            self.write(path, original)
            host = _Host(self.root, plugin={"root": str(path.parent.parent), "configPath": str(path)})
            backend = self.backend(host)
            result = backend.set_semantic_model("  provider/utility  ", max_output_tokens=2200)
            self.assertTrue(result["saved"])
            self.assertFalse(result["applied"])
            self.assertEqual(yaml.safe_load(path.read_text(encoding="utf-8")), {**original, "semantics": {
                **original["semantics"], "model": "provider/utility", "max_output_tokens": 2200}})
            self.assertFalse((self.root / "config/mirally/memory.yaml").exists())
            result = backend.set_semantic_model("")
            self.assertIsNone(result["semantics"]["model"])
            self.assertEqual(result["semantics"]["enabled"], enabled)
            self.assertEqual(result["semantics"]["max_output_tokens"], 2200)

    def test_model_only_save_force_refreshes_effective_custom_profile(self) -> None:
        first = self.write(self.root / "first.yaml", {"semantics": {"enabled": True, "model": "first/model"}})
        second = self.write(self.root / "second.yaml", {"semantics": {"enabled": False, "model": "second/model"}})
        host = _Host(self.root, plugin={"configPath": str(first)})
        backend = self.backend(host)
        self.assertEqual(backend.model_settings()["model"], "first/model")
        original = first.read_bytes()
        host.plugin["configPath"] = str(second)
        result = backend.set_semantic_model("new/model")
        self.assertTrue(result["saved"])
        self.assertFalse(result["semantics"]["enabled"])
        self.assertEqual(first.read_bytes(), original)
        self.assertEqual(yaml.safe_load(second.read_text(encoding="utf-8"))["semantics"]["model"], "new/model")

    def test_partial_model_save_preserves_external_changes_to_other_field(self) -> None:
        path = self.root / "custom memory.yaml"
        original = {"enabled": True, "semantics": {"enabled": False, "model": "original/model",
            "max_output_tokens": 800, "max_calls_per_hour": 7, "max_tokens_per_day": 12345,
            "future_option": "retain"}, "unknown": {"private_option": "retain"}}
        self.write(path, original)
        host = _Host(self.root, plugin={"configPath": str(path)})
        backend = self.backend(host)
        loaded = backend.model_settings()
        self.assertEqual(loaded["model"], "original/model")
        external = {**original, "semantics": {**original["semantics"], "model": "external/model"}}
        self.write(path, external)
        result = backend.set_semantic_model(max_output_tokens=2200)
        self.assertTrue(result["saved"])
        expected = {**external, "semantics": {**external["semantics"], "max_output_tokens": 2200}}
        self.assertEqual(yaml.safe_load(path.read_text(encoding="utf-8")), expected)
        backend.model_settings()
        external = {**expected, "semantics": {**expected["semantics"], "max_output_tokens": 3600}}
        self.write(path, external)
        result = backend.set_semantic_model(model="chosen/model")
        self.assertTrue(result["saved"])
        expected = {**external, "semantics": {**external["semantics"], "model": "chosen/model"}}
        self.assertEqual(yaml.safe_load(path.read_text(encoding="utf-8")), expected)
        backend.set_semantic_model(model="")
        self.assertIsNone(yaml.safe_load(path.read_text(encoding="utf-8"))["semantics"]["model"])
        self.assertEqual(load_memory_config(self.root, path).semantics.max_output_tokens, 3600)

    def test_invalid_model_settings_and_unverified_profile_never_write(self) -> None:
        backend = self.backend()
        for model, tokens in ((None, None), (False, None), ("bad model", None), ("x" * 513, None),
                              ("model", 127), ("model", 16001), ("model", True), ("model", "500")):
            with self.subTest(model=model, tokens=tokens), self.assertRaises(ValueError):
                backend.set_semantic_model(model, tokens)
        self.assertEqual(list(self.root.iterdir()), [])
        host = _Host(self.root)
        host.activity_profiles = lambda: (_ for _ in ()).throw(RuntimeError("unavailable"))
        backend = self.backend(host)
        self.assertFalse(backend.model_settings()["canSave"])
        with self.assertRaisesRegex(RuntimeError, "无法确认有效 Memory 配置"):
            backend.set_semantic_model("new/model", 800)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_existing_semantic_save_can_change_output_cap_without_resetting_budget(self) -> None:
        path = self.write(self.root / "config/mirally/memory.yaml", {"semantics": {
            "enabled": False, "max_calls_per_hour": 3, "max_output_tokens": 700}})
        result = self.backend().set_semantics(True, max_output_tokens=16000)
        self.assertTrue(result["semantics"]["enabled"])
        saved = yaml.safe_load(path.read_text(encoding="utf-8"))["semantics"]
        self.assertEqual(saved["max_output_tokens"], 16000)
        self.assertEqual(saved["max_calls_per_hour"], 3)
        before = path.read_bytes()
        with self.assertRaises(ValueError):
            self.backend().set_semantics(True, max_output_tokens=False)
        self.assertEqual(path.read_bytes(), before)

    def test_semantics_budget_model_save_and_offline_reload_are_distinct(self) -> None:
        backend = self.backend()
        result = backend.set_semantics(True,model='',model_provided=True,max_calls_per_hour=24,max_tokens_per_day=60000)
        self.assertTrue(result['saved'])
        self.assertFalse(result['applied'])
        self.assertIn('尚未应用',result['reload_error'])
        self.assertIsNone(result['semantics']['model'])
        self.assertEqual(result['semantics']['max_calls_per_hour'],24)
        self.assertTrue(backend.status()['memory']['controls']['set-semantics'])

    def test_reload_of_a_different_memory_profile_cannot_claim_applied(self) -> None:
        host = _Host(self.root)
        native = host._native_json
        def respond(*args):
            if args[:3] == ('gateway','call','mirally.memory.reloadSettings'):
                return {'applied':True,'settings':{'semantics':{'enabled':False}}}
            return native(*args)
        with patch.object(host,'_native_json',side_effect=respond):
            result = self.backend(host).set_semantics(True)
        self.assertTrue(result['saved'])
        self.assertFalse(result['applied'])
        self.assertIn('不一致',result['reload_error'])

    def test_invalid_semantics_or_unverified_effective_profile_never_write(self) -> None:
        backend = self.backend()
        for args in ({'enabled':'true'},{'enabled':True,'max_calls_per_hour':True},
                     {'enabled':True,'max_tokens_per_day':-1},{'enabled':True,'model':'bad model','model_provided':True}):
            with self.subTest(args=args), self.assertRaises(ValueError):
                backend.set_semantics(**args)
        self.assertEqual(list(self.root.iterdir()),[])
        host = _Host(self.root)
        with patch.object(host,'_native_json',side_effect=RuntimeError('native profile unavailable')):
            backend = self.backend(host)
            self.assertFalse(backend.status()['memory']['controls']['set-semantics'])
            with self.assertRaisesRegex(RuntimeError,'无法确认有效 Memory 配置'):
                backend.set_semantics(True)
        self.assertEqual(list(self.root.iterdir()),[])

    def test_invalid_or_missing_custom_profile_is_never_overwritten(self) -> None:
        path = self.root / 'custom.yaml'
        host = _Host(self.root, {'mirally-activity':{'args':['--config',str(path)]}})
        backend = self.backend(host)
        with self.assertRaises(FileNotFoundError):
            backend.set_enabled(False)
        self.assertFalse(path.exists())
        path.write_text('not: [valid yaml',encoding='utf-8')
        before = path.read_bytes()
        with self.assertRaises(yaml.YAMLError):
            backend.set_enabled(False)
        self.assertEqual(path.read_bytes(),before)
        self.assertFalse(backend.status()['activity']['controls']['set-enabled'])

    def test_auto_start_errors_and_close_do_not_enable_custom_missing_profile(self) -> None:
        path = self.root / 'missing custom.yaml'
        backend = self.backend(_Host(self.root, {'mirally-activity':{'args':['--config',str(path)]}}))
        backend._capture = capture = _Capture()
        backend.auto_start_capture()
        self.assertEqual(capture.calls,[])
        self.assertIn('不存在',backend.status()['activity']['auto_start_error'])
        backend.close()
        backend.auto_start_capture()
        self.assertFalse(path.exists())

    def test_custom_mcp_cwd_root_config_and_memory_source_remain_separate(self) -> None:
        product = self.root / "工作 根"
        self.activity(product / "profiles/采集.yaml", "data/collector.sqlite3")
        self.activity(product / "profiles/记忆来源.yaml", "data/historical.sqlite3")
        memory = self.write(product / "profiles/记忆.yaml", {"enabled": True,
            "activity_config_path": "profiles/记忆来源.yaml", "storage": {
                "database_path": "memory/store.sqlite3", "projection_database_path": "memory/projection.sqlite3"}})
        self.seed_activity(product / "data/collector.sqlite3")
        host = _Host(self.root, {"mirally-activity": {"cwd": str(product / "profiles"),
            "args": ["-m", "mirally.runtime.activity.mcp_server", "--root", "..", "--config", "采集.yaml"]}},
            {"root": str(product), "configPath": "profiles/记忆.yaml"})
        backend = self.backend(host)
        status = backend.status()
        self.assertEqual(status["activity"]["config_path"], str(product / "profiles/采集.yaml"))
        self.assertEqual(status["activity"]["database"], str(product / "data/collector.sqlite3"))
        self.assertEqual(status["activity"]["collector"]["status"], "degraded")
        self.assertTrue(status["activity"]["collector"]["snapshot"])
        self.assertEqual(status["activity"]["collector"]["updated_at_utc_ms"], 123)
        self.assertEqual(status["memory"]["config_path"], str(memory))
        self.assertFalse(status["memory"]["source_matches_activity"])
        self.assertEqual(status["memory"]["activity_database"], str(product / "data/historical.sqlite3"))
        self.assertEqual(status["memory"]["health"]["status"], "not_ready")
        backend.status()
        self.assertEqual(sum(call[:3] in [("config", "get", "mcp.servers"),
            ("config", "get", "plugins.entries.mirally-memory.config")] for call in host.calls), 2)
        self.assertFalse((product / "memory").exists())

    def test_explicit_missing_or_invalid_profile_is_not_replaced_by_defaults(self) -> None:
        self.activity()
        host = _Host(self.root, {"mirally-activity": {"cwd": str(self.root),
            "args": ["--config", "absent.yaml"]}}, {"configPath": "missing-memory.yaml"})
        backend = self.backend(host)
        status = backend.status()
        self.assertIn("不存在", status["activity"]["error"])
        self.assertIn("不存在", status["memory"]["error"])
        with self.assertRaises(FileNotFoundError):
            backend.open_browser()
        with self.assertRaises(FileNotFoundError):
            backend.memory_search()
        host.servers["mirally-activity"]["args"] = ["--config", ""]
        backend.refresh_profiles()
        self.assertIn("非空", backend.status()["activity"]["error"])

    def test_memory_ready_does_not_claim_worker_is_running_and_queries_are_read_only(self) -> None:
        self.activity()
        self.write(self.root / "config/mirally/memory.yaml", {"enabled": True,
            "storage": {"database_path": "data/memory.sqlite3", "projection_database_path": "data/projection.sqlite3"}})
        database = self.root / "data/memory.sqlite3"
        with MemoryStore(database) as store:
            store.put_memory({"id": "fixture", "kind": "episodic", "content": "研究便携配置",
                              "status": "active", "provenance": "observed"})
            store.put_memory({"id": "candidate", "kind": "semantic", "content": "等待确认的候选",
                              "status": "candidate", "provenance": "model_inference"})
        before = self.digest(database)
        backend = self.backend()
        status = backend.status()
        self.assertEqual(status["memory"]["health"]["status"], "ready")
        self.assertEqual(status["memory"]["database"], str(database))
        self.assertFalse(status["memory"]["runtime"]["verified"])
        self.assertEqual([row["id"] for row in backend.memory_search()["memories"]], ["fixture"])
        self.assertEqual(len(backend.memory_search(include_candidates=True)["memories"]), 2)
        self.assertEqual(backend.memory_read("fixture")["text"], "研究便携配置")
        self.assertEqual(self.digest(database), before)
        self.assertFalse((self.root / "data/projection.sqlite3").exists())

    def test_memory_paging_and_read_windows_use_existing_read_only_interfaces(self) -> None:
        self.activity()
        self.write(self.root / "config/mirally/memory.yaml", {"enabled": True,
            "storage": {"database_path": "data/memory.sqlite3", "projection_database_path": "data/projection.sqlite3"}})
        database = self.root / "data/memory.sqlite3"
        with MemoryStore(database) as store:
            for index in range(3):
                store.put_memory({"id": f"fixture-{index}", "kind": "episodic",
                    "content": "第一行\n第二行\n第三行", "status": "active", "provenance": "observed"})
        backend = self.backend()
        first = backend.memory_search(limit=1)
        second = backend.memory_search(limit=1, offset=first["next_offset"])
        self.assertNotEqual(first["memories"][0]["id"], second["memories"][0]["id"])
        window = backend.memory_read("fixture-0", from_line=2, line_count=1)
        self.assertEqual(window["text"], "第二行")
        self.assertEqual(window["from_line"], 2)
        self.assertTrue(window["truncated"])
        with self.assertRaises(ValueError):
            backend.memory_search(offset=-1)
        with self.assertRaises(ValueError):
            backend.memory_read("fixture-0", from_line=0)

    def test_memory_browser_filtered_pages_and_archived_model_details_remain_read_only(self) -> None:
        self.activity()
        self.write(self.root / "config/mirally/memory.yaml", {"enabled": True,
            "storage": {"database_path": "data/memory.sqlite3", "projection_database_path": "data/projection.sqlite3"}})
        database = self.root / "data/memory.sqlite3"
        with MemoryStore(database) as store:
            for index in range(12):
                store.put_memory({"id": f"rule-{index}", "kind": "episodic", "content": "same keyword",
                    "provenance": "observed", "observed_start_utc_ms": 1000, "observed_end_utc_ms": 2000})
            for index in range(3):
                store.put_memory({"id": f"model-{index}", "kind": "episodic", "content": "same keyword\nmodel detail",
                    "provenance": "model_inference", "observed_start_utc_ms": 100 + index * 100,
                    "observed_end_utc_ms": 150 + index * 100, "source_observation_ids": ["source-one", "source-two"],
                    "metadata": {"observation_scope": "desktop"}})
            store.set_state("memory_archival:model-2", {"source_retention_status": "archived", "references_unavailable": True})
        before = self.digest(database)
        backend = self.backend()
        filters = {"provenances": ["model_inference"], "kinds": ["episodic"], "start_utc_ms": 50, "end_utc_ms": 400}
        first = backend.memory_search("keyword", limit=2, **filters)
        self.assertEqual([row["id"] for row in first["memories"]], ["model-2", "model-1"])
        second = backend.memory_search("keyword", limit=2, offset=first["next_offset"], **filters)
        self.assertEqual([row["id"] for row in second["memories"]], ["model-0"])
        self.assertIsNone(second["next_offset"])
        detail = backend.memory_read("model-2")
        self.assertEqual(detail["text"], "same keyword\nmodel detail")
        self.assertEqual(detail["memory"]["provenance"], "model_inference")
        self.assertEqual(detail["memory"]["observed_start_utc_ms"], 300)
        self.assertEqual(detail["memory"]["source_observation_ids"], ["source-one", "source-two"])
        self.assertEqual(detail["memory"]["metadata"]["source_retention_status"], "archived")
        self.assertTrue(detail["memory"]["metadata"]["references_unavailable"])
        self.assertEqual(self.digest(database), before)
        self.assertFalse((self.root / "data/projection.sqlite3").exists())

    def test_official_runtime_status_is_reported_independently(self) -> None:
        backend = self.backend(_Host(self.root, runtime={"running": True, "ready": True,
            "worker": {"maintenanceRunning": True, "maintenanceStatus": "ready"},
            "semantic": {"state": "disabled"}}))
        runtime = backend.status()["memory"]["runtime"]
        self.assertTrue(runtime["verified"])
        self.assertTrue(runtime["maintenance_running"])
        self.assertEqual(runtime["semantic"]["state"], "disabled")
        backend.host.runtime["ready"] = False
        self.assertEqual(backend.status()["memory"]["runtime"]["status"], "starting")

    def test_browser_reuses_http_instance_and_missing_projection_remains_missing(self) -> None:
        self.activity()
        database = self.root / "data/observations.sqlite3"
        self.seed_activity(database)
        before = self.digest(database)
        backend = self.backend()
        with patch("mirally.runtime.activity.settings.webbrowser.open", return_value=True) as browser:
            self.assertTrue(backend.open_browser()["opened"])
            runtime = backend._runtime
            backend.open_browser("monitor")
            self.assertIs(backend._runtime, runtime)
            urls = [call.args[0] for call in browser.call_args_list]
            self.assertIn("/monitor?token=", urls[1])
            base = runtime.url.split("/?", 1)[0]
            headers = {"Authorization": "Bearer " + runtime.token}
            with urllib.request.urlopen(urllib.request.Request(base + "/monitor", headers=headers), timeout=3) as response:
                page = response.read().decode("utf-8")
            self.assertIn("window.__mirallyActivityViewOnly=true", page)
            self.assertIn("记录状态不代表采集进程仍在运行", page)
            with urllib.request.urlopen(urllib.request.Request(base + "/api/status", headers=headers), timeout=3) as response:
                status = json.loads(response.read())
            self.assertTrue(status["collector"]["snapshot"])
            self.assertEqual(status["metrics"]["capture_calls"], 2)
            self.assertFalse(status["knowledge"]["knowledge"]["available"])
            for action in ("pause", "resume", "stop"):
                with self.assertRaises(urllib.error.HTTPError) as raised:
                    urllib.request.urlopen(urllib.request.Request(base + "/api/control/" + action,
                        headers=headers, method="POST"), timeout=3)
                self.assertEqual(raised.exception.code, 405)
            with self.assertRaises(urllib.error.HTTPError) as raised:
                urllib.request.urlopen(urllib.request.Request(base + "/api/knowledge/items", headers=headers), timeout=3)
            self.assertEqual(raised.exception.code, 503)
        self.assertEqual(self.digest(database), before)
        self.assertFalse((database.parent / "activity-knowledge.sqlite3").exists())
        self.assertFalse(hasattr(runtime, "sensor_thread"))

    def test_profile_switch_closes_previous_browser_and_missing_profile_does_not_reuse_it(self) -> None:
        first = self.activity(self.root / "profiles/first.yaml", "first.sqlite3")
        second = self.activity(self.root / "profiles/second.yaml", "second.sqlite3")
        self.seed_activity(self.root / "first.sqlite3")
        self.seed_activity(self.root / "second.sqlite3")
        host = _Host(self.root, {"mirally-activity": {"args": ["--config", str(first)]}})
        backend = self.backend(host)
        with patch("mirally.runtime.activity.settings.webbrowser.open", return_value=True):
            backend.open_browser()
            old = backend._runtime
            host.servers["mirally-activity"]["args"] = ["--config", str(second)]
            backend.refresh_profiles()
            backend.open_browser("monitor")
            self.assertIsNot(backend._runtime, old)
            self.assertTrue(old._closed)
            host.servers["mirally-activity"]["args"] = ["--config", str(self.root / "missing.yaml")]
            backend.refresh_profiles()
            with self.assertRaises(FileNotFoundError):
                backend.open_browser()

    def test_capture_confirmation_rechecks_scope_and_temporary_session_preserves_profile(self) -> None:
        path = self.activity()
        backend = self.backend()
        backend._capture = capture = _Capture()
        expected = backend.status()["activity"]
        self.assertIn("前台窗口元数据", [row["label"] for row in expected["capture"] if row["enabled"]])
        saved = path.read_bytes()
        backend.start_capture(expected_activity=expected)
        self.assertFalse(capture.calls[0][1].enabled)
        self.assertEqual(path.read_bytes(), saved)
        running = backend.status()["activity"]
        self.assertFalse(running["enabled"])
        self.assertTrue(running["live_enabled"])
        self.assertFalse(running["collector"]["snapshot"])
        self.assertTrue(running["controls"]["stop"])
        with patch("mirally.runtime.activity.settings.webbrowser.open", return_value=True) as browser:
            backend.open_browser("monitor")
            self.assertEqual(browser.call_args.args[0], capture.url)
        backend.pause_capture()
        self.assertTrue(backend.status()["activity"]["controls"]["resume"])
        backend.resume_capture()
        self.activity(database="new-source.sqlite3")
        changed = backend.status()["activity"]
        self.assertTrue(changed["config_changed"])
        self.assertEqual(changed["database"], running["database"])
        self.assertIn("先停止", changed["warning"])
        with self.assertRaisesRegex(RuntimeError, "请先停止"):
            backend.start_capture()
        backend.stop_capture()
        with self.assertRaisesRegex(RuntimeError, "重新开始"):
            backend.start_capture(expected_activity=expected)
        self.assertEqual(len([call for call in capture.calls if call[0] == "start"]), 1)

    def test_start_checks_changed_effective_profile_even_while_profile_cache_is_fresh(self) -> None:
        first = self.activity(self.root / "profiles/first.yaml", "first.sqlite3")
        second = self.activity(self.root / "profiles/second.yaml", "second.sqlite3")
        host = _Host(self.root, {"mirally-activity": {"args": ["--config", str(first)]}})
        backend = self.backend(host)
        backend._capture = capture = _Capture()
        expected = backend.status()["activity"]
        host.servers["mirally-activity"]["args"] = ["--config", str(second)]
        with self.assertRaisesRegex(RuntimeError, "重新开始"):
            backend.start_capture(expected_activity=expected)
        self.assertEqual(capture.calls, [])

    def test_confirmation_detects_screenshot_scope_and_privacy_changes(self) -> None:
        base = {"enabled": False, "collection": {"default_level": "content"},
                "storage": {"database_path": "fixture.sqlite3"}, "visual": {"enabled": True, "scope": "foreground"},
                "privacy": {"excluded_apps": ["secret.exe"], "allowed_apps": ["editor.exe"],
                            "excluded_windows": ["private*"]}}
        path = self.root / "config/mirally/activity.yaml"
        backend = self.backend()
        backend._capture = capture = _Capture()
        for field in ("visual_scope", "excluded_apps", "allowed_apps", "excluded_windows"):
            with self.subTest(field=field):
                self.write(path, base)
                expected = backend.status()["activity"]
                self.assertEqual(len(expected["config_fingerprint"]), 64)
                modified = json.loads(json.dumps(base))
                if field == "visual_scope":
                    modified["visual"]["scope"] = "monitors"
                else:
                    modified["privacy"][field] = []
                self.write(path, modified)
                with self.assertRaisesRegex(RuntimeError, "重新开始"):
                    backend.start_capture(expected_activity=expected)
                self.assertEqual(capture.calls, [])
        status = backend.status()["activity"]
        self.assertIn("前台窗口", status["scope_note"])
        self.assertIn("secret.exe", status["scope_note"])
        self.assertIn("屏幕截图（前台窗口）", [row["label"] for row in status["capture"]])

    def test_capture_scope_honors_uia_source_and_content_switches(self) -> None:
        path = self.root / "config/mirally/activity.yaml"
        self.write(path, {"enabled": True, "collection": {"default_level": "content"},
            "ui_structure": {"enabled": False, "content": True},
            "input": {"enabled": False, "text": True, "clipboard": True}})
        backend = self.backend()
        scope = {row["label"]: row["enabled"] for row in backend.status()["activity"]["capture"]}
        self.assertFalse(scope["UIA 正文"])
        self.assertFalse(scope["输入文本"])
        self.assertFalse(scope["剪贴板正文"])
        self.write(path, {"enabled": True, "collection": {"default_level": "content"},
            "ui_structure": {"enabled": True, "content": False}, "input": {"text": True}})
        scope = {row["label"]: row["enabled"] for row in backend.status()["activity"]["capture"]}
        self.assertFalse(scope["UIA 正文"])
        self.assertFalse(scope["输入文本"])

    def test_capture_controls_reject_old_session_identity_after_restart(self) -> None:
        self.activity()
        backend = self.backend()
        backend._capture = capture = _Capture()
        backend.start_capture()
        first = backend.status()["activity"]["session_id"]
        backend.stop_capture(expected_session_id=first)
        backend.start_capture()
        second = backend.status()["activity"]["session_id"]
        self.assertNotEqual(first, second)
        before = list(capture.calls)
        for action in (backend.pause_capture, backend.resume_capture, backend.stop_capture):
            with self.assertRaisesRegex(RuntimeError, "会话已变化"):
                action(expected_session_id=first)
        self.assertEqual(capture.calls, before)
        self.assertIsNotNone(capture.runtime)
        backend.pause_capture(expected_session_id=second)
        self.assertEqual(backend.status()["activity"]["collector"]["status"], "paused")


if __name__ == "__main__":
    unittest.main()
