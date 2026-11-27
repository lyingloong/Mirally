"""Both Activity diagnostics faces must answer from one read surface and one status rule.

The monitor page polls ``/api/events`` while the read-only browser page polls
``/api/browser/observations``; both are served by the same request handler over
two runtimes.  These tests pin the shared paging, cursor-expiry and collector
status behaviour on one fixture, so a rule can no longer be fixed on one face
only.
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
import urllib.error
import urllib.request
from dataclasses import replace
from pathlib import Path

from mirally.core.activity import Observation, ObservationStore
from mirally.core.activity.knowledge import ActivityOrganizer, KnowledgeStore
from mirally.runtime.activity.browser import ActivityBrowserRuntime
from mirally.runtime.activity.lock import ActivityProcessLock
from mirally.runtime.activity.monitor import ActivityMonitorRuntime, build_test_config
from mirally.runtime.activity.read_surface import derive_collector_status
from mirally.runtime.activity.service import ActivitySensor


class _StableForeground:
    """A foreground source that never reports a change, so the fixture cannot drift."""

    available = False

    def poll(self):
        return None


def http_get(base: str, token: str, path: str) -> dict:
    request = urllib.request.Request(base + path, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


class ActivityReadSurfaceFixture(unittest.TestCase):
    """Six recorded observations behind both diagnostics runtimes."""

    KINDS = ("window_context", "foreground_changed", "window_context",
             "window_context", "foreground_changed", "window_context")

    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.config = replace(
            build_test_config(self.root, content=False, persist_images=False),
            database_path=self.root / "activity" / "observations.sqlite3",
            asset_directory=self.root / "activity" / "assets",
            input_enabled=False, ui_structure_enabled=False,
            window_titles_enabled=False, browser_urls_enabled=False,
        )
        self.config.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.appended_ids: list[str] = []
        self.window_context_ids: list[str] = []
        with ObservationStore(self.config.database_path, asset_directory=self.config.asset_directory) as source:
            for index, kind in enumerate(self.KINDS):
                value = source.append(Observation.create(
                    kind=kind, source_id="fixture", source_epoch="epoch", source_seq=index, boot_id="boot",
                    observed_at_utc_ms=1_000 + index, app_id="fixture.exe", window_instance_id="window-a",
                    payload={"window_title": f"Fixture {index}"},
                ))
                self.appended_ids.append(value.observation_id)
                if kind == "window_context":
                    self.window_context_ids.append(value.observation_id)

    @staticmethod
    def get(base: str, token: str, path: str) -> dict:
        return http_get(base, token, path)


class SharedCollectorStatusTests(ActivityReadSurfaceFixture):
    """One recorded fact yields one collector status on both faces."""

    CASES = (
        # (recorded collector, recorded diagnostics, recorded pause, expected status, expected basis)
        ({}, {}, {"paused": True}, "unknown", "none"),
        ({}, {"status": "paused"}, {}, "paused", "diagnostics"),
        ({"status": "running"}, {"status": "degraded"}, {}, "degraded", "diagnostics"),
        ({"status": "running"}, {"status": "stopped"}, {"paused": True}, "stopped", "diagnostics"),
        ({"status": "running"}, {}, {"paused": True}, "paused", "pause"),
        ({"status": "disabled"}, {}, {"paused": True}, "disabled", "collector"),
        # A collector thread that died is recorded only in the collector row; both
        # faces must keep showing it instead of the last live diagnostics row.
        ({"status": "error", "error": "RuntimeError: sensor thread died"}, {"status": "running"}, {},
         "error", "collector"),
        # An older diagnostics row must not hide a newer recorded stop: stop() writes
        # the snapshot first, so the snapshot can be the stale half of the pair.
        ({"status": "stopped", "at": 2_000}, {"status": "paused", "updated_at_utc_ms": 1_000}, {},
         "stopped", "collector"),
        ({"status": "stopped", "at": 2_000}, {"status": "running", "updated_at_utc_ms": 1_000},
         {"paused": True}, "stopped", "collector"),
        ({"status": "error", "at": 2_000, "source_epoch": "epoch-a"},
         {"status": "running", "updated_at_utc_ms": 1_000, "source_epoch": "epoch-a"}, {},
         "error", "collector"),
        # Inside one generation, control or sampling code can still write diagnostics
        # after the collector died; that must not read as a collector that runs again.
        ({"status": "error", "at": 2_000, "source_epoch": "epoch-a"},
         {"status": "paused", "updated_at_utc_ms": 3_000, "source_epoch": "epoch-a"}, {"paused": True},
         "error", "collector"),
        # Across generations the later recorded fact describes the current collection.
        ({"status": "stopped", "at": 1_000, "source_epoch": "epoch-a"},
         {"status": "running", "updated_at_utc_ms": 2_000, "source_epoch": "epoch-b"}, {},
         "running", "diagnostics"),
        ({"status": "stopped", "at": 3_000, "source_epoch": "epoch-b"},
         {"status": "paused", "updated_at_utc_ms": 1_000, "source_epoch": "epoch-a"}, {},
         "stopped", "collector"),
        # A stop request is not a terminal fact, so the live snapshot still answers.
        ({"status": "stop_requested", "at": 3_000}, {"status": "running", "updated_at_utc_ms": 1_000}, {},
         "running", "diagnostics"),
    )

    def setUp(self) -> None:
        super().setUp()
        self.monitor = ActivityMonitorRuntime(self.config, root=self.root)
        self.addCleanup(self.monitor.close)
        self.browser = ActivityBrowserRuntime(self.config, root=self.root)
        self.addCleanup(self.browser.close)

    def record(self, collector: dict, diagnostics: dict, pause: dict) -> None:
        self.monitor.store.set_state("collector", collector)
        self.monitor.store.set_state("collector_diagnostics", diagnostics)
        self.monitor.store.set_state("collector_pause", pause)

    def test_both_faces_derive_the_same_collector_status(self) -> None:
        for collector, diagnostics, pause, expected, basis in self.CASES:
            with self.subTest(collector=collector, diagnostics=diagnostics, pause=pause):
                self.record(collector, diagnostics, pause)
                monitor = self.monitor.status()["collector"]
                browser = self.browser.status()["collector"]
                self.assertEqual(monitor["status"], expected)
                self.assertEqual(browser["status"], expected)
                self.assertEqual(monitor["status_basis"], basis)
                self.assertEqual(browser["status_basis"], basis)
                self.assertEqual(
                    derive_collector_status(collector=collector, diagnostics=diagnostics, pause=pause),
                    (expected, basis),
                )

    def test_only_the_read_only_face_marks_its_status_as_a_stored_snapshot(self) -> None:
        self.record({}, {"status": "running"}, {})
        monitor = self.monitor.status()["collector"]
        browser = self.browser.status()["collector"]
        self.assertEqual(monitor["status"], browser["status"])
        self.assertNotIn("snapshot", monitor)
        self.assertIs(browser["snapshot"], True)

    def test_metrics_come_from_the_face_not_a_shared_sensor_adapter(self) -> None:
        self.monitor.store.set_state("collector_diagnostics", {"status": "degraded", "metrics": {"events_total": 3}})
        self.assertFalse(hasattr(self.browser, "sensor"))
        self.assertIs(self.browser.metrics()["read_only"], True)
        self.assertEqual(self.browser.metrics()["events_total"], 3)
        self.assertEqual(self.monitor.status()["metrics"], self.monitor.metrics())


class SharedReadRouteTests(ActivityReadSurfaceFixture):
    """The shared read surface answers both faces identically over HTTP."""

    def setUp(self) -> None:
        super().setUp()
        self.monitor = ActivityMonitorRuntime(self.config, root=self.root)
        # A test-scoped guard plus a source that never emits keeps this run off the
        # machine's real collector and keeps the fixture stable between requests.
        self.monitor.lock = ActivityProcessLock(
            name=f"Local\\MirallyActivityReadSurfaceTest-{self.root.name}",
            fallback_path=self.config.database_path.with_suffix(".lock"),
        )
        self.monitor.sensor = ActivitySensor(self.config, store=self.monitor.store, source=_StableForeground(),
                                             input_source=None, uia_source=None)
        self.addCleanup(self.monitor.close)
        self.monitor_api = self.monitor.start(port=0).split("/?", 1)[0]
        # The product's history view opens the browser without projection sync, so
        # both faces can be served over one fixture without competing writers.
        self.browser = ActivityBrowserRuntime(self.config, root=self.root, sync=False, require_knowledge=False)
        self.addCleanup(self.browser.close)
        self.browser_api = self.browser.start(port=0).split("/?", 1)[0]
        self.faces = (("monitor", self.monitor_api, self.monitor.token),
                      ("browser", self.browser_api, self.browser.token))

    def walk(self, base: str, token: str, *, path: str, key: str, cursor_param: str) -> list[dict]:
        rows: list[dict] = []
        cursor = None
        while True:
            page = self.get(base, token, path + (f"&{cursor_param}={cursor}" if cursor is not None else ""))
            rows.extend(page[key])
            if not page["has_more"]:
                return rows
            self.assertIsNotNone(page["cursor"])
            cursor = page["cursor"]
            self.assertLessEqual(len(rows), len(self.KINDS))

    def test_one_page_agrees_across_both_faces(self) -> None:
        paths = ("/api/browser/observations?limit=2",
                 "/api/browser/observations?kind=window_context&limit=2",
                 "/api/browser/observations?kind=window_context&limit=2&include_content=1",
                 "/api/events?after=0&limit=2",
                 "/api/events?kind=foreground_changed&limit=2")
        for path in paths:
            with self.subTest(path=path):
                responses = {face: self.get(base, token, path) for face, base, token in self.faces}
                monitor, browser = responses["monitor"], responses["browser"]
                self.assertEqual(monitor, browser)
                self.assertTrue(monitor.get("observations") or monitor.get("events"))

    def test_full_pages_agree_and_cover_the_fixture_once(self) -> None:
        for path, key, cursor_param, expected in (
            ("/api/browser/observations?limit=2", "observations", "before", list(reversed(self.appended_ids))),
            ("/api/browser/observations?kind=window_context&limit=2", "observations", "before",
             list(reversed(self.window_context_ids))),
            ("/api/events?limit=2", "events", "after", self.appended_ids),
            ("/api/events?kind=window_context&limit=2", "events", "after", self.window_context_ids),
        ):
            with self.subTest(path=path):
                walks = [self.walk(base, token, path=path, key=key, cursor_param=cursor_param)
                         for _face, base, token in self.faces]
                self.assertEqual(walks[0], walks[1], "both faces must page identically")
                self.assertEqual([row["observation_id"] for row in walks[0]], list(expected))

    def test_expired_cursor_behaves_the_same_on_both_faces(self) -> None:
        connection = sqlite3.connect(str(self.config.database_path))
        try:
            connection.execute("PRAGMA busy_timeout = 1000")
            connection.execute("INSERT INTO schema_meta(key,value) VALUES('memorized_retention_ingest_seq', '4')")
            connection.commit()
        finally:
            connection.close()
        responses = {face: self.get(base, token, "/api/events?after=0&limit=2") for face, base, token in self.faces}
        for face, payload in responses.items():
            with self.subTest(face=face):
                self.assertTrue(payload["cursor_expired"])
                self.assertEqual(payload["events"], [])
                self.assertEqual(payload["cursor"], 0)
                self.assertFalse(payload["has_more"])
        self.assertEqual(responses["monitor"], responses["browser"])

    def test_controlled_routes_stay_closed_on_the_read_only_face(self) -> None:
        request = urllib.request.Request(self.browser_api + "/api/control/pause", method="POST",
                                         headers={"Authorization": f"Bearer {self.browser.token}"})
        with self.assertRaises(urllib.error.HTTPError) as raised:
            urllib.request.urlopen(request, timeout=5)
        self.assertEqual(raised.exception.code, 405)


class SharedEvidenceRouteTests(unittest.TestCase):
    """The evidence list is one projection read behind both faces: channels, gaps, budget.

    The monitor entry used to answer ``/api/knowledge/evidence`` through the
    organizer's context projection while the browser entry answered it with a raw
    knowledge query.  Serving both entries from one list silently dropped the
    OCR/UIA channel selection, the projection gaps and the output budget from the
    monitor answer, so every request here goes to both entries and one answer is
    required.
    """

    OCR_MARKER = "仅 OCR 通道正文"
    UIA_MARKER = "仅 UIA 通道正文"
    ROWS = 4

    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.config = replace(
            build_test_config(self.root, content=True, persist_images=False),
            database_path=self.root / "activity" / "observations.sqlite3",
            asset_directory=self.root / "activity" / "assets",
            input_enabled=False, ui_structure_enabled=False,
            window_titles_enabled=False, browser_urls_enabled=False,
        )
        self.config.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.knowledge_path = self.config.database_path.with_name("activity-knowledge.sqlite3")
        with ObservationStore(self.config.database_path, asset_directory=self.config.asset_directory) as source:
            for index in range(self.ROWS):
                source.append(Observation.create(
                    kind="window_context", source_id="fixture", source_epoch="epoch", source_seq=index * 2,
                    boot_id="boot", observed_at_utc_ms=2_000 + index, app_id="fixture.exe",
                    window_instance_id="window-a", payload={"window_title": f"Fixture window {index}"},
                ))
                source.append(Observation.create(
                    kind="content_snapshot", source_id="fixture", source_epoch="epoch",
                    source_seq=index * 2 + 1, boot_id="boot", observed_at_utc_ms=2_100 + index,
                    app_id="fixture.exe", window_instance_id="window-a",
                    ocr_summary={"status": "ok", "text": f"{self.OCR_MARKER} {index} " + ("屏幕正文 " * 24)},
                    uia_summary={"status": "ok", "nodes": [{"name": f"{self.UIA_MARKER} {index}", "role": "text"}]},
                ))
        # The projection is rebuildable, so it is built once here and both faces then
        # read one knowledge database instead of racing to write it.
        with ObservationStore(self.config.database_path, asset_directory=self.config.asset_directory,
                              read_only=True) as source, KnowledgeStore(self.knowledge_path) as knowledge:
            ActivityOrganizer(source, knowledge).catch_up()
        self.monitor = ActivityMonitorRuntime(self.config, root=self.root)
        self.monitor.lock = ActivityProcessLock(
            name=f"Local\\MirallyActivityEvidenceRouteTest-{self.root.name}",
            fallback_path=self.config.database_path.with_suffix(".lock"),
        )
        self.monitor.sensor = ActivitySensor(self.config, store=self.monitor.store, source=_StableForeground(),
                                             input_source=None, uia_source=None)
        self.addCleanup(self.monitor.close)
        self.monitor_api = self.monitor.start(port=0).split("/?", 1)[0]
        # The product's history view opens the browser without projection sync, so the
        # two faces read the same recorded projection instead of competing writers.
        self.browser = ActivityBrowserRuntime(self.config, root=self.root, sync=False, require_knowledge=False)
        self.addCleanup(self.browser.close)
        self.browser_api = self.browser.start(port=0).split("/?", 1)[0]
        self.faces = (("monitor", self.monitor_api, self.monitor.token),
                      ("browser", self.browser_api, self.browser.token))

    def both(self, path: str) -> dict:
        """Ask both HTTP entries the same question and require one single answer."""
        answers = {face: http_get(base, token, path) for face, base, token in self.faces}
        self.assertEqual(answers["monitor"], answers["browser"], f"{path}: both entries must answer identically")
        return answers["monitor"]

    def test_evidence_list_keeps_the_requested_content_channels_only(self) -> None:
        hidden = self.both("/api/knowledge/evidence?limit=50&tokens=100000&include_ocr=0&include_uia=0")
        self.assertTrue(hidden["evidence"])
        for row in hidden["evidence"]:
            encoded = json.dumps(row, ensure_ascii=False)
            self.assertNotIn(self.OCR_MARKER, encoded)
            self.assertNotIn(self.UIA_MARKER, encoded)
            self.assertNotIn("ocr_text", row["structured"])
            self.assertNotIn("uia_text", row["structured"])
        revealed = self.both("/api/knowledge/evidence?limit=50&tokens=100000&include_ocr=1&include_uia=1")
        encoded = json.dumps(revealed["evidence"], ensure_ascii=False)
        self.assertIn(self.OCR_MARKER, encoded)
        self.assertIn(self.UIA_MARKER, encoded)

    def test_evidence_list_stops_at_the_output_budget(self) -> None:
        full = self.both("/api/knowledge/evidence?limit=50&tokens=100000")
        self.assertGreaterEqual(len(full["evidence"]), self.ROWS * 2)
        self.assertFalse(full["truncated"])
        bounded = self.both("/api/knowledge/evidence?limit=50&tokens=200")
        self.assertTrue(bounded["truncated"])
        self.assertLess(len(bounded["evidence"]), len(full["evidence"]))

    def test_evidence_pages_walk_the_same_rows_on_both_entries(self) -> None:
        seen: list[str] = []
        cursor = None
        while True:
            page = self.both("/api/knowledge/evidence?limit=2&tokens=100000"
                             + (f"&after_item={cursor}" if cursor else ""))
            self.assertTrue(page["evidence"])
            self.assertNotEqual(page["data_status"], "empty")
            seen += [row["evidence_id"] for row in page["evidence"]]
            self.assertLessEqual(len(seen), self.ROWS * 2)
            if not page["truncated"]:
                break
            self.assertIsNotNone(page["next_cursor"])
            cursor = page["next_cursor"]
        self.assertEqual(len(seen), len(set(seen)), "one evidence row must be served once")
        self.assertEqual(len(seen), self.ROWS * 2)

    def test_evidence_detail_and_projection_gaps_agree_on_both_entries(self) -> None:
        listed = self.both("/api/knowledge/evidence?limit=1&tokens=100000")
        self.assertNotIn("projection_incomplete", listed["gaps"])
        evidence_id = listed["evidence"][0]["evidence_id"]
        detail = self.both(f"/api/knowledge/evidence?id={evidence_id}")
        self.assertEqual([row["evidence_id"] for row in detail["evidence"]], [evidence_id])
        self.monitor.knowledge_store.record_projection_error("observation-missing-fixture", 1,
                                                             RuntimeError("fixture gap"))
        marked = self.both("/api/knowledge/evidence?limit=1&tokens=100000")
        self.assertIn("projection_incomplete", marked["gaps"])


if __name__ == "__main__":
    unittest.main()
