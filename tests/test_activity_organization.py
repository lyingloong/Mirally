"""Activity truth-preserving organization regressions with isolated stores."""

from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from openassist.core.activity import Observation, ObservationStore
from openassist.core.activity.knowledge import ActivityOrganizer, ContextQuery, KnowledgeStore


class ActivityOrganizationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        self.source = ObservationStore(root / "observations.sqlite3")
        self.knowledge = KnowledgeStore(root / "knowledge.sqlite3")
        self.organizer = ActivityOrganizer(self.source, self.knowledge)
        self.seq = 0

    def tearDown(self) -> None:
        self.knowledge.close()
        self.source.close()
        self.directory.cleanup()

    def append(self, kind: str, at: int, *, window: str = "window-a", app: str | None = "browser", **kwargs):
        row = Observation.create(
            kind=kind, source_id="fixture", source_epoch="epoch", source_seq=self.seq,
            boot_id="boot", session_instance_id="session", window_instance_id=window,
            app_id=app, observed_at_utc_ms=at, **kwargs,
        )
        self.seq += 1
        return self.source.append(row)

    def item(self, row):
        return self.knowledge.get_item_by_observation_id(row.observation_id)

    def test_metadata_and_focus_changes_do_not_fragment_background_monitor(self) -> None:
        self.append("window_context", 1000, payload={"window_title": "Page A"})
        first = self.append("content_snapshot", 1001, window="monitor-1", app=None, actor="BACKGROUND", payload={"scope": "monitor"})
        self.append("window_context", 1002, payload={"window_title": "Page A updated"})
        self.append("foreground_changed", 1003, window="window-b", app="editor")
        second = self.append("content_snapshot", 1004, window="monitor-1", app=None, actor="BACKGROUND", payload={"scope": "monitor"})
        self.organizer.catch_up()
        self.assertEqual(self.item(first).episode_id, self.item(second).episode_id)
        monitor = self.knowledge.get_episode(self.item(first).episode_id)
        self.assertNotEqual(monitor.end_reason, "window_changed")

    def test_late_ocr_uses_original_resource_and_capture_time(self) -> None:
        self.append("window_context", 1000, payload={"window_title": "Page A", "browser_url": "https://example.test/a"})
        frame = self.append("content_snapshot", 1001, payload={"scope": "foreground"})
        self.append("window_context", 1002, payload={"window_title": "Page B", "browser_url": "https://example.test/b"})
        ocr = self.append("ocr_result", 9000, caused_by=(frame.observation_id,), ocr_summary={"status": "ok", "text": "Page A body"})
        self.organizer.catch_up()
        projected = self.item(ocr)
        self.assertEqual(projected.episode_id, self.item(frame).episode_id)
        self.assertEqual(projected.url, "https://example.test/a")
        self.assertEqual(projected.start_utc_ms, self.item(frame).start_utc_ms)
        self.assertEqual(projected.structured["source"]["result_observed_at_utc_ms"], 9000)
        self.assertEqual(self.knowledge.get_episode(projected.episode_id).end_utc_ms, 1001)

    def test_unlinked_ocr_cannot_be_attached_to_current_page(self) -> None:
        page = self.append("window_context", 1000, payload={"browser_url": "https://example.test/new"})
        ocr = self.append("ocr_result", 1001, caused_by=("missing-frame",), ocr_summary={"text": "Unknown origin"})
        self.organizer.catch_up()
        projected = self.item(ocr)
        self.assertNotEqual(projected.episode_id, self.item(page).episode_id)
        self.assertIsNone(projected.url)
        self.assertIn("causal_evidence_missing", projected.missing_reasons)

    def test_late_ocr_result_context_cannot_override_original_frame_object(self) -> None:
        context = self.append("window_context", 1000, payload={
            "window_title": "Original page", "browser_url": "https://fixture.invalid/original",
            "document_ref": "document-original",
        })
        frame = self.append("content_snapshot", 1001, process_instance_id="original-process",
                            payload={"scope": "foreground"})
        self.append("window_context", 2000, window="window-b", app="another-browser", payload={
            "window_title": "New page", "browser_url": "https://fixture.invalid/new",
        })
        ocr = self.append("ocr_result", 3000, window="window-b", app="another-browser",
                          process_instance_id="new-process", caused_by=(frame.observation_id,), payload={
                              "window_title": "New page", "browser_url": "https://fixture.invalid/new",
                              "document_ref": "document-new", "scope": "monitor", "monitor_id": "wrong-monitor",
                          }, ocr_summary={"status": "ok", "text": "Original page body"})
        self.organizer.catch_up()
        projected = self.item(ocr)
        original = self.item(frame)
        self.assertEqual((projected.app_id, projected.process_instance_id, projected.window_instance_id),
                         (original.app_id, original.process_instance_id, original.window_instance_id))
        self.assertEqual((projected.title, projected.url, projected.document_ref),
                         (original.title, original.url, original.document_ref))
        target = projected.structured["activity"]["object"]
        self.assertEqual((target["app_id"], target["process_instance_id"], target["window_instance_id"]),
                         (original.app_id, original.process_instance_id, original.window_instance_id))
        self.assertEqual((target["title"], target["url"], target["document_ref"]),
                         (original.title, original.url, original.document_ref))
        self.assertEqual(target["type"], "window")
        self.assertEqual(target["scope"], "foreground")
        self.assertNotIn("monitor_id", target)
        self.assertEqual(projected.structured["title"], original.title)
        self.assertEqual(projected.structured["url"], original.url)
        self.assertIn(context.observation_id, projected.structured["context_observation_ids"])
        evidence = self.knowledge.get_evidence(projected.structured["activity"]["evidence_ids"])[0]
        self.assertEqual(evidence.structured["activity"]["object"], target)
        self.assertEqual(projected.episode_id, original.episode_id)

    def test_late_monitor_ocr_retains_monitor_scope_without_foreground_resource(self) -> None:
        bounds = [-1920, 0, 1920, 1080]
        frame = self.append("content_snapshot", 1000, window="opaque-display", app=None, actor="BACKGROUND",
                            payload={"scope": "monitor", "monitor_id": "display-1", "monitor_bounds": bounds})
        ocr = self.append("ocr_result", 2000, caused_by=(frame.observation_id,), payload={
            "window_title": "Foreground page", "browser_url": "https://fixture.invalid/foreground",
            "document_ref": "foreground-document", "scope": "foreground",
        }, ocr_summary={"status": "ok", "text": "Display body"})
        self.organizer.catch_up()
        projected = self.item(ocr)
        self.assertIsNone(projected.app_id)
        self.assertEqual(projected.window_instance_id, "opaque-display")
        self.assertIsNone(projected.title)
        self.assertIsNone(projected.url)
        self.assertIsNone(projected.document_ref)
        self.assertNotIn("title", projected.structured)
        self.assertNotIn("url", projected.structured)
        target = projected.structured["activity"]["object"]
        self.assertEqual((target["type"], target["scope"], target["monitor_id"]),
                         ("monitor", "monitor", "display-1"))
        self.assertEqual(target["monitor_bounds"], bounds)
        self.assertIsNone(target["app_id"])
        self.assertIsNone(target["url"])
        self.assertEqual(projected.structured["source"]["scope"], "monitor")
        self.assertEqual(projected.episode_id, self.item(frame).episode_id)

    def test_unlinked_ocr_preserves_original_failure_quality_and_explains_missing_cause(self) -> None:
        for at, quality in enumerate(("complete", "partial", "unavailable", "gap", "stale"), start=1000):
            with self.subTest(quality=quality):
                ocr = self.append("ocr_result", at, caused_by=("missing-frame",), quality=quality,
                                  missing_reasons=("fixture_source_reason",), ocr_summary={"status": "error"})
                self.organizer.catch_up()
                projected = self.item(ocr)
                expected_quality = "partial" if quality == "complete" else quality
                self.assertEqual(projected.quality, expected_quality)
                self.assertIn("causal_origin_unknown", projected.structured["activity"]["unknowns"])
                self.assertEqual(projected.structured["missing_reasons"], list(projected.missing_reasons))
                self.assertIn("fixture_source_reason", projected.missing_reasons)
                self.assertIn("causal_evidence_missing", projected.missing_reasons)
                evidence = self.knowledge.get_evidence(projected.structured["activity"]["evidence_ids"])[0]
                self.assertEqual(evidence.quality, expected_quality)
                self.assertEqual(evidence.structured["missing_reasons"], list(projected.missing_reasons))
                self.assertIn("causal_origin_unknown", evidence.structured["activity"]["unknowns"])

    def test_collection_gap_breaks_visit_and_unknown_context(self) -> None:
        first = self.append("window_context", 1000, payload={"browser_url": "https://example.test/a"})
        self.append("collection_gap", 1500, app=None, window="", actor="SYSTEM", quality="gap", missing_reasons=("paused",))
        second = self.append("input_text", 1600, payload={"operation": "delete", "removed_char_count": 7, "offset": 9})
        self.organizer.catch_up()
        self.assertNotEqual(self.item(first).episode_id, self.item(second).episode_id)
        self.assertIn("paused", self.knowledge.get_episode(self.item(first).episode_id).data_gaps)
        self.assertIsNone(self.item(second).url)

    def test_context_is_inherited_with_sources_without_future_information(self) -> None:
        context = self.append("window_context", 1000, payload={"window_title": "Document", "browser_url": "https://example.test/a"})
        edit = self.append("input_text", 1100, payload={"inserted_text": "draft", "operation": "insert"})
        self.append("window_context", 1200, payload={"browser_url": "https://example.test/b"})
        self.organizer.catch_up()
        projected = self.item(edit)
        self.assertEqual(projected.url, "https://example.test/a")
        self.assertEqual(projected.title, "Document")
        self.assertIn(context.observation_id, projected.structured["context_observation_ids"])

    def test_parser_failure_rolls_back_and_retry_restores_one_item(self) -> None:
        class Parser:
            fail = True

            def parse(self, item):
                if self.fail:
                    raise ValueError("fixture parser failure")
                return None

        parser = Parser()
        self.organizer.parser_registry = parser
        row = self.append("input_text", 1000, payload={"operation": "delete", "removed_char_count": 7})
        first = self.organizer.catch_up()
        self.assertEqual(first.errors, 1)
        self.assertEqual(self.knowledge.health().item_count, 0)
        self.assertEqual(self.knowledge.health().episode_count, 0)
        self.assertEqual(self.knowledge.health(self.organizer.consumer_id).projection_error_count, 1)
        parser.fail = False
        recovered = self.organizer.catch_up()
        self.assertEqual(recovered.projected, 1)
        self.assertIsNotNone(self.item(row))
        self.assertEqual(self.knowledge.health(self.organizer.consumer_id).projection_error_count, 0)

    def test_persistent_failure_has_bounded_retries_and_never_claims_empty(self) -> None:
        class Parser:
            def parse(self, item):
                raise ValueError("fixture persistent failure")

        self.organizer.parser_registry = Parser()
        self.append("input_text", 1000, payload={"inserted_text": "draft"})
        for _ in range(5):
            self.organizer.catch_up()
        errors = self.knowledge.list_projection_errors(consumer_id=self.organizer.consumer_id)
        self.assertEqual(errors[0]["attempts"], 3)
        self.assertEqual(self.organizer.activity_timeline(ContextQuery())["data_status"], "projection_incomplete")

    def test_old_projection_requires_rebuild_without_mixing_versions(self) -> None:
        row = self.append("window_context", 1000, payload={"window_title": "Fixture"})
        item, evidence = self.organizer.project(row)
        self.knowledge.upsert_item(replace(item, projection_version="activity.v1"), evidence)
        with self.assertRaisesRegex(ValueError, "rebuild"):
            self.organizer.catch_up()
        self.assertTrue(self.organizer.health()["projection_rebuild_required"])
        self.organizer.rebuild()
        self.assertFalse(self.organizer.health()["projection_rebuild_required"])
        self.assertEqual(self.knowledge.health().item_count, 1)

    def test_timeline_describes_delete_and_unknown_result_with_evidence(self) -> None:
        edit = self.append("input_text", 1000, actor="UNKNOWN", actor_basis="observed_committed_text",
                           payload={"operation": "delete", "removed_char_count": 7, "offset": 9, "baseline_known": True})
        self.organizer.catch_up()
        timeline = self.organizer.activity_timeline(ContextQuery())
        row = timeline["timeline"][0]
        self.assertEqual(row["change"]["removed_char_count"], 7)
        self.assertEqual(row["actor"], "UNKNOWN")
        self.assertEqual(row["result"], "unconfirmed")
        self.assertEqual(row["source_observation_ids"], [edit.observation_id])
        self.assertTrue(row["evidence_ids"])

    def test_timeline_pagination_and_budget_preserve_all_fact_rows(self) -> None:
        for at in range(1000, 1006):
            self.append("input_text", at, payload={"inserted_text": "draft", "operation": "insert"})
        self.organizer.catch_up()
        cursor = None
        seen = []
        while True:
            timeline = self.organizer.activity_timeline(ContextQuery(max_items=2, token_budget=4000, after_item_id=cursor))
            seen.extend(value["item_id"] for value in timeline["timeline"])
            self.assertLessEqual(len(json.dumps(timeline, ensure_ascii=False, separators=(",", ":")).encode("utf-8")), 4000 * 3)
            if not timeline["truncated"]:
                break
            self.assertIsNotNone(timeline["next_cursor"])
            cursor = timeline["next_cursor"]
        self.assertEqual(len(seen), 6)
        self.assertEqual(len(set(seen)), 6)
        self.assertEqual(self.organizer.activity_timeline(ContextQuery(token_budget=128))["data_status"], "budget_exceeded")

    def test_new_uia_and_ocr_metadata_respects_context_content_flags(self) -> None:
        self.append("context_snapshot", 1000, uia_summary={"nodes": [{"role": "Edit", "value": "secret"}]})
        self.append("content_snapshot", 1001, ocr_summary={"text": "secret", "lines": [{"text": "secret"}], "language": "fixture"})
        self.organizer.catch_up()
        context = self.organizer.context(ContextQuery(mode="structured", include_uia=False, include_ocr=False, token_budget=16000))
        for item in context.items:
            self.assertNotIn("uia_nodes", item.structured)
            self.assertNotIn("uia_metadata", item.structured)
            self.assertNotIn("ocr_lines", item.structured)
            self.assertNotIn("ocr_metadata", item.structured)


if __name__ == "__main__":
    unittest.main()
