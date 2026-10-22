from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
import urllib.error
import urllib.request
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from mirally.core.activity import Actor, CollectionLevel, Observation, ObservationStore
from mirally.core.activity.knowledge.projection import build_projection
from mirally.runtime.activity.browser import ActivityBrowserRuntime
from mirally.runtime.activity.monitor import ActivityMonitorRuntime, build_test_config


class ActivityBrowserRuntimeTests(unittest.TestCase):
    """The developer browser reads every layer without starting collection."""

    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        base = build_test_config(root, content=True, persist_images=True)
        self.config = replace(
            base,
            database_path=root / "activity" / "observations.sqlite3",
            asset_directory=root / "activity" / "assets",
        )
        self.config.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.sequence = 0
        source = ObservationStore(self.config.database_path, asset_directory=self.config.asset_directory)
        self.window = self._append(
            source,
            "window_context",
            1_000,
            payload={"window_title": "Fixture page", "browser_url": "https://fixture.invalid/page"},
        )
        self.frame = self._append(
            source,
            "content_snapshot",
            1_001,
            payload={"scope": "foreground", "trigger": "typing_pause"},
            visual_summary={"status": "ok", "capture_scope": "foreground"},
        )
        self.ocr = self._append(
            source,
            "ocr_result",
            1_002,
            caused_by=(self.frame.observation_id,),
            ocr_summary={"status": "ok", "text": "完整 OCR 正文 " + ("正文内容 " * 30)},
        )
        _, ocr_evidence = build_projection(self.ocr, "activity.v2")
        self.evidence_id = ocr_evidence.evidence_id
        self.input_batch = self._append(
            source,
            "input_batch",
            1_003,
            actor=Actor.USER,
            actor_basis="physical_input",
            payload={"keyboard_count": 2, "click_count": 1},
        )
        self.input_text = self._append(
            source,
            "input_text",
            1_004,
            actor=Actor.USER,
            actor_basis="physical_input",
            caused_by=(self.input_batch.observation_id,),
            payload={"operation": "insert", "inserted_text": "Fixture input", "baseline_known": True},
        )
        self.asset_id = ""
        # Publish a tiny asset through the real store API. This keeps the test
        # on the same metadata and path validation used by collection.
        reservation = source.reserve_asset(mime_type="image/png", scope="activity-content", policy_version=1)
        self.asset_id = reservation.asset_id
        asset_observation = Observation.create(
            kind="content_snapshot",
            source_id="fixture",
            source_epoch="epoch",
            source_seq=self.sequence,
            boot_id="boot",
            observed_at_utc_ms=1_005,
            session_instance_id="session",
            process_instance_id="process",
            window_instance_id="window",
            app_id="fixture.exe",
            collection_level=CollectionLevel.CONTENT,
            asset_refs=(reservation.asset_id,),
            payload={"scope": "foreground", "trigger": "periodic"},
        )
        self.asset_observation = source.commit_asset_bytes(reservation, b"fixture-png", asset_observation)
        source.close()
        self.raw_digest = self._digest(self.config.database_path)
        self.runtime = ActivityBrowserRuntime(self.config, root=root)

    def tearDown(self) -> None:
        self.runtime.close()
        self.directory.cleanup()

    def _append(self, source: ObservationStore, kind: str, at: int, **fields):
        value = Observation.create(
            kind=kind,
            source_id="fixture",
            source_epoch="epoch",
            source_seq=self.sequence,
            boot_id="boot",
            observed_at_utc_ms=at,
            session_instance_id="session",
            process_instance_id="process",
            window_instance_id="window",
            app_id="fixture.exe",
            **fields,
        )
        self.sequence += 1
        return source.append(value)

    @staticmethod
    def _digest(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    @staticmethod
    def _params(**values):
        return {key: [str(value)] for key, value in values.items() if value is not None}

    def test_start_is_read_only_and_does_not_create_a_collector(self) -> None:
        self.assertTrue(self.runtime.browser_only)
        self.assertTrue(self.runtime.status()["browser"]["read_only"])
        self.assertEqual(self._digest(self.config.database_path), self.raw_digest)
        self.assertEqual(self.runtime.start(port=0).split("/?", 1)[0].startswith("http://127.0.0.1:"), True)
        self.assertFalse(hasattr(self.runtime, "sensor_thread"))
        self.assertEqual(self._digest(self.config.database_path), self.raw_digest)

    def test_filtered_event_pages_do_not_report_a_phantom_next_page(self) -> None:
        class FilteredStore:
            def __init__(self, rows):
                self.rows = rows
                self.requested_limit = None

            def query(self, **kwargs):
                self.requested_limit = kwargs["limit"]
                return self.rows[: self.requested_limit]

            def read_since(self, *_args, **_kwargs):
                raise AssertionError("filtered query should use query()")

        def check(runtime_type):
            runtime = runtime_type.__new__(runtime_type)
            runtime.store = FilteredStore([SimpleNamespace(ingest_seq=7)])
            with patch.object(
                __import__(runtime_type.__module__, fromlist=["_safe_observation"]),
                "_safe_observation",
                side_effect=lambda row, **_kwargs: {"ingest_seq": row.ingest_seq},
            ):
                result = runtime.events({"kind": ["ocr_result"], "limit": ["1"]})
            self.assertEqual(runtime.store.requested_limit, 2)
            self.assertEqual(result["events"], [{"ingest_seq": 7}])
            self.assertFalse(result["has_more"])
            self.assertEqual(result["cursor"], 7)

        check(ActivityBrowserRuntime)
        check(ActivityMonitorRuntime)

    def test_all_data_layers_are_queryable_and_paginated(self) -> None:
        self.runtime.start(port=0)
        raw = self.runtime.raw(self._params(limit=2))
        self.assertEqual(len(raw["observations"]), 2)
        self.assertTrue(raw["has_more"])
        self.assertEqual(raw["observations"][0]["observation_id"], self.window.observation_id)

        filtered = self.runtime.raw(self._params(kind="ocr_result", limit=10))
        self.assertEqual([row["observation_id"] for row in filtered["observations"]], [self.ocr.observation_id])
        self.assertNotEqual(filtered["observations"][0]["ocr_summary"]["text"], "")

        items = self.runtime.items(self._params(limit=2))
        self.assertEqual(len(items["items"]), 2)
        self.assertTrue(items["truncated"])
        self.assertTrue(items["next_cursor"])
        next_page = self.runtime.items(self._params(limit=2, after_item=items["next_cursor"]))
        self.assertTrue(next_page["items"])
        item_id = items["items"][0]["item_id"]
        item = self.runtime.item(item_id)
        self.assertEqual(item["item_id"], item_id)
        self.assertIn("structured", item)

        episodes = self.runtime.episodes(self._params(limit=20))
        self.assertTrue(episodes["episodes"])
        episode_id = episodes["episodes"][0]["episode_id"]
        self.assertEqual(self.runtime.episode(episode_id)["episode_id"], episode_id)

        evidence = self.runtime.evidence([self.evidence_id], include_assets=False)
        self.assertTrue(evidence["evidence"])
        self.assertIn("完整 OCR 正文", evidence["evidence"][0]["text"])
        self.assertEqual(self.runtime.asset(self.asset_id)["asset_id"], self.asset_id)

        events = self.runtime.events_view(self._params(limit=20, include_uia=1, include_ocr=1))
        self.assertIn("events", events)
        self.assertTrue(events["events"])
        timeline = self.runtime.timeline(self._params(limit=20, include_uia=1, include_ocr=1))
        self.assertTrue(timeline["timeline"])
        context = self.runtime.context(self._params(limit=20, mode="evidence", include_uia=1, include_ocr=1))
        self.assertTrue(context["items"])
        summary = self.runtime.summary(self._params(limit=20, episodes=20))
        self.assertIn("apps", summary)

        candidate = self.runtime.organizer.propose_memory(
            "fixture browser memory", source_episode_ids=(episode_id,), source_observation_ids=(self.window.observation_id,)
        )
        candidates = self.runtime.candidates(self._params(limit=20))
        self.assertIn(candidate.candidate_id, {row["candidate_id"] for row in candidates["candidates"]})
        fact = self.runtime.organizer.accept_memory(candidate.candidate_id)
        self.assertIsNotNone(fact)
        facts = self.runtime.facts(self._params(limit=20))
        self.assertIn(fact.fact_id, {row["fact_id"] for row in facts["facts"]})
        self.assertIn("health", self.runtime.diagnostics())

    def test_http_browser_routes_are_authenticated_and_read_only(self) -> None:
        url = self.runtime.start(port=0)
        base = url.split("/?", 1)[0]
        headers = {"Authorization": f"Bearer {self.runtime.token}"}

        def get(path: str):
            request = urllib.request.Request(base + path, headers=headers)
            with urllib.request.urlopen(request, timeout=3) as response:
                return response.status, response.read()

        status, page = get("/")
        self.assertEqual(status, 200)
        self.assertIn("Activity 数据浏览", page.decode("utf-8"))

        status, raw_body = get("/api/browser/observations?limit=2&include_content=1")
        self.assertEqual(status, 200)
        raw = json.loads(raw_body.decode("utf-8"))
        self.assertEqual(len(raw["observations"]), 2)
        self.assertTrue(raw["has_more"])
        self.assertIn("ocr_summary", raw["observations"][-1])
        _, evidence_body = get("/api/knowledge/evidence?limit=2&include_assets=1")
        self.assertIn("evidence", json.loads(evidence_body.decode("utf-8")))
        _, episodes_body = get("/api/knowledge/episodes?limit=1")
        self.assertIn("episodes", json.loads(episodes_body.decode("utf-8")))

        control = urllib.request.Request(base + "/api/control/pause", method="POST", headers=headers)
        with self.assertRaises(urllib.error.HTTPError) as raised:
            urllib.request.urlopen(control, timeout=3)
        self.assertEqual(raised.exception.code, 405)

        unauthorized = urllib.request.Request(base + "/api/status")
        with self.assertRaises(urllib.error.HTTPError) as raised:
            urllib.request.urlopen(unauthorized, timeout=3)
        self.assertEqual(raised.exception.code, 401)


if __name__ == "__main__":
    unittest.main()
