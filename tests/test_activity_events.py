"""Evidence-backed events use related observations without inventing actions."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from openassist.core.activity import Observation, ObservationStore
from openassist.core.activity.knowledge import ActivityOrganizer, ContextQuery, KnowledgeStore


class ActivityEventTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        self.source = ObservationStore(root / "raw.sqlite3")
        self.knowledge = KnowledgeStore(root / "derived.sqlite3")
        self.organizer = ActivityOrganizer(self.source, self.knowledge)
        self.sequence = 0

    def tearDown(self) -> None:
        self.knowledge.close()
        self.source.close()
        self.directory.cleanup()

    def append(self, kind: str, at: int, *, window="window-a", app="fixture.exe", **fields):
        row = self.source.append(Observation.create(
            kind=kind, source_id="fixture", source_epoch="epoch", source_seq=self.sequence,
            boot_id="boot", session_instance_id="session", window_instance_id=window,
            app_id=app, observed_at_utc_ms=at, **fields,
        ))
        self.sequence += 1
        return row

    def item(self, row):
        return self.knowledge.get_item_by_observation_id(row.observation_id)

    def query(self, **fields):
        self.organizer.catch_up()
        return self.organizer.activity_events(ContextQuery(
            include_uia=True, token_budget=100_000, max_items=100, **fields,
        ))

    def test_actions_window_state_and_supporting_text_form_one_related_event(self):
        state = self.append("window_context", 1000, payload={"window_title": "Draft", "browser_url": "https://example.test/draft"})
        batch = self.append("input_batch", 1001, actor="USER", actor_basis="physical_input",
                            payload={"keyboard_count": 4, "click_count": 1, "scroll_count": 0})
        edit = self.append("input_text", 1002, actor="USER", actor_basis="physical_input",
                           caused_by=(batch.observation_id,), payload={"operation": "insert", "inserted_text": "草稿",
                                                                      "baseline_known": True, "offset": 0})
        content = self.append("context_snapshot", 1003, uia_summary={
            "status": "ok", "nodes": [{"role": "Document", "text": "正文草稿"}],
            "capture_scope": {"root": "foreground_window", "content_enabled": True, "excluded_nodes": 1},
        })
        result = self.query()
        self.assertEqual(len(result["events"]), 1)
        event = result["events"][0]
        self.assertEqual(set(event["source_observation_ids"]),
                         {row.observation_id for row in (state, batch, edit, content)})
        self.assertEqual([action["action"] for action in event["actions"]], ["input_activity", "text_changed"])
        self.assertTrue(event["user_actions_observed"])
        self.assertEqual(event["actions"][1]["caused_by"], [batch.observation_id])
        self.assertIn("正文草稿", event["window_text"][0]["excerpt"])
        self.assertEqual(event["window_text"][0]["capture_scope"]["excluded_nodes"], 1)
        self.assertIn(state.observation_id, event["context_observation_ids"])
        self.assertFalse(event["partial_event"])
        self.assertEqual(event["outcome"], "unconfirmed")

    def test_initial_text_snapshot_remains_state_without_an_edit_action(self):
        row = self.append("input_text", 1000, payload={"operation": "snapshot", "text": "Existing draft", "baseline_known": False})
        event = self.query()["events"][0]
        self.assertEqual(event["actions"], [])
        self.assertEqual(event["input_states"][0]["state"]["text"], "Existing draft")
        self.assertIn("previous_text_baseline_unknown", event["unknowns"])
        self.assertIn("action_evidence_missing", event["unknowns"])
        self.assertFalse(event["user_actions_observed"])
        self.assertEqual(event["source_observation_ids"], [row.observation_id])

    def test_input_batch_helper_timing_survives_raw_projection_and_event(self):
        timing = {"started_monotonic_ns": 1_000_000, "ended_monotonic_ns": 2_000_000,
                  "helper_emitted_at_utc_ms": 5000}
        row = self.append("input_batch", 6000, time_quality="unknown", actor="USER", actor_basis="physical_input",
                          payload={"keyboard_count": 2, "click_count": 1, **timing})
        event = self.query()["events"][0]
        for name, value in timing.items():
            self.assertEqual(self.source.get(row.observation_id).payload[name], value)
            self.assertEqual(self.item(row).structured["input"][name], value)
            self.assertEqual(self.item(row).structured["activity"]["change"][name], value)
            self.assertEqual(event["actions"][0]["change"][name], value)
        self.assertEqual(event["actions"][0]["time_quality"], "unknown")
        self.assertTrue(event["actions"][0]["time_semantics"])
        self.assertEqual(event["actions"][0]["observed_at_utc_ms"], 6000)

    def test_unknown_and_agent_actions_are_not_relabelled_as_user_actions(self):
        self.append("input_batch", 1000, actor="UNKNOWN", actor_basis="unclaimed", payload={"click_count": 1})
        self.append("input_text", 1001, actor="AGENT", actor_basis="agent_control",
                    payload={"operation": "replace", "inserted_text": "new", "removed_char_count": 3, "baseline_known": True})
        event = self.query()["events"][0]
        self.assertEqual([action["actor"] for action in event["actions"]], ["UNKNOWN", "AGENT"])
        self.assertEqual([action["attribution"] for action in event["actions"]], ["unconfirmed", "agent"])
        self.assertFalse(event["user_actions_observed"])
        self.assertTrue(all(action["outcome"] == "unconfirmed" for action in event["actions"]))

    def test_returning_to_a_window_starts_a_separate_visit(self):
        first = self.append("foreground_changed", 1000, payload={"window_title": "A"})
        middle = self.append("foreground_changed", 2000, window="window-b", payload={"window_title": "B"})
        last = self.append("foreground_changed", 3000, payload={"window_title": "A"})
        result = self.query()
        self.assertEqual(len(result["events"]), 3)
        self.assertNotEqual(self.item(first).episode_id, self.item(last).episode_id)
        self.assertEqual([event["window_instance_id"] for event in result["events"]], ["window-a", "window-b", "window-a"])
        self.assertTrue(all(event["actions"] == [] for event in result["events"]))
        self.assertEqual(result["events"][0]["observed_end_utc_ms"], 1000)
        self.assertEqual(self.knowledge.get_episode(self.item(middle).episode_id).end_reason, "window_changed")

    def test_navigation_starts_a_new_resource_event_in_the_same_window(self):
        first = self.append("window_context", 1000, payload={"browser_url": "https://example.test/a"})
        self.append("context_snapshot", 1001, uia_summary={"nodes": [{"text": "A body"}]})
        second = self.append("window_context", 1002, payload={"browser_url": "https://example.test/b"})
        events = self.query()["events"]
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["end_reason"], "navigation")
        self.assertNotEqual(self.item(first).episode_id, self.item(second).episode_id)
        self.assertIn("A body", events[0]["window_text"][0]["excerpt"])
        self.assertEqual(events[1]["window_text"], [])

    def test_collection_gap_closes_an_event_and_is_returned_separately(self):
        before = self.append("window_context", 1000, payload={"window_title": "Before", "browser_url": "https://example.test/before"})
        gap = self.append("collection_gap", 1500, app=None, window=None, actor="SYSTEM", quality="gap", missing_reasons=("paused",))
        after = self.append("input_text", 1600, payload={"operation": "snapshot", "baseline_known": False, "text": "After"})
        result = self.query()
        self.assertEqual(len(result["events"]), 2)
        self.assertEqual(result["collection_status"][0]["source_observation_ids"], [gap.observation_id])
        self.assertNotEqual(self.item(before).episode_id, self.item(after).episode_id)
        self.assertEqual(result["events"][0]["end_reason"], "collection_gap")
        self.assertIn("paused", result["events"][0]["data_gaps"])
        self.assertIsNone(result["events"][1]["window_states"][0]["window"]["url"])

    def test_event_output_exposes_session_coverage_without_claiming_query_completeness(self):
        self.source.set_state("collector_diagnostics", {
            "source_epoch": "epoch",
            "status": "stopped",
            "started_at_utc_ms": 900,
            "stopped_at_utc_ms": 1200,
            "updated_at_utc_ms": 1200,
            "known_gap_counts": {"unattributed_input_batches": 2, "stale_results": 1},
            "sources": {
                "foreground": {"enabled": True, "available": True},
                "input_metadata": {"enabled": True, "available": False, "reason": "worker_exit"},
            },
            "pause_intervals": [],
        })
        self.append("window_context", 1000, payload={"window_title": "Fixture"})
        result = self.query(start_utc_ms=1000, end_utc_ms=1100)
        coverage = result["collection_coverage"]
        self.assertEqual(coverage["status"], "known_gaps")
        self.assertTrue(coverage["diagnostics_available"])
        self.assertTrue(coverage["overlaps_query_time"])
        self.assertEqual(coverage["known_gap_counts"]["unattributed_input_batches"], 2)
        self.assertEqual(coverage["sources"]["input_metadata"]["reason"], "worker_exit")
        self.assertIn("session_totals_not_window_or_query_specific", coverage["limitations"])

    def test_late_ocr_is_text_for_the_original_window_event(self):
        self.append("window_context", 1000, payload={"window_title": "Original", "browser_url": "https://example.test/a"})
        frame = self.append("content_snapshot", 1001, payload={"scope": "foreground"})
        self.append("foreground_changed", 2000, window="window-b", app="other.exe", payload={"window_title": "Other"})
        ocr = self.append("ocr_result", 9000, window="window-b", app="other.exe", caused_by=(frame.observation_id,),
                          ocr_summary={"status": "ok", "text": "Original visible text"})
        result = self.query()
        original = next(event for event in result["events"] if event["event_id"] == self.item(frame).episode_id)
        text = original["window_text"][0]
        self.assertEqual(text["source_observation_ids"], [ocr.observation_id])
        self.assertEqual(text["caused_by"], [frame.observation_id])
        self.assertEqual(text["observed_at_utc_ms"], 1001)
        self.assertEqual(original["window_instance_id"], "window-a")
        other = next(event for event in result["events"] if event["window_instance_id"] == "window-b")
        self.assertEqual(other["window_text"], [])

    def test_monitor_ocr_and_unresolved_ocr_cannot_become_window_action_or_text(self):
        self.append("foreground_changed", 1000)
        frame = self.append("content_snapshot", 1001, app=None, window="monitor-1", actor="BACKGROUND",
                            actor_basis="monitor_observation", payload={"scope": "monitor", "monitor_id": "display"})
        monitor_ocr = self.append("ocr_result", 3000, caused_by=(frame.observation_id,),
                                  ocr_summary={"status": "ok", "text": "Whole desktop text"})
        orphan = self.append("ocr_result", 3001, caused_by=("missing-frame",),
                             ocr_summary={"status": "ok", "text": "Unknown origin text"})
        result = self.query()
        self.assertEqual(len(result["events"]), 1)
        self.assertEqual(result["events"][0]["window_text"], [])
        self.assertEqual(result["events"][0]["actions"], [])
        self.assertEqual(len(result["background_evidence"]), 2)
        text = next(entry for entry in result["background_evidence"] if entry["source_observation_ids"] == [monitor_ocr.observation_id])
        self.assertEqual(text["excerpt"], "Whole desktop text")
        self.assertEqual(text["window_ownership"], "unconfirmed")
        self.assertEqual(result["unattributed_evidence"][0]["source_observation_ids"], [orphan.observation_id])

    def test_budget_exhaustion_is_explicit_and_does_not_change_full_evidence(self):
        body = "完整正文 📝" * 15000
        row = self.append("context_snapshot", 1000, uia_summary={"status": "ok", "nodes": [{"text": body}]})
        self.organizer.catch_up()
        item = self.item(row)
        evidence_id = item.structured["activity"]["evidence_ids"][0]
        small = self.organizer.activity_events(ContextQuery(include_uia=True, token_budget=128))
        self.assertEqual(small["data_status"], "budget_exceeded")
        self.assertTrue(small["truncated"])
        self.assertIsNone(small["next_cursor"])
        self.assertEqual(small["events"], [])
        enough = self.organizer.activity_events(ContextQuery(include_uia=True, token_budget=8000))
        text = enough["events"][0]["window_text"][0]
        self.assertTrue(text["excerpt_truncated"])
        self.assertEqual(text["original_char_count"], len(body))
        self.assertEqual(self.knowledge.get_evidence((evidence_id,))[0].text, body)
        self.assertEqual(self.source.get(row.observation_id).uia_summary["nodes"][0]["text"], body)

    def test_event_limit_pagination_emits_each_window_evidence_once(self):
        rows = [self.append("foreground_changed", 1000 + index, window=f"window-{index}") for index in range(3)]
        self.organizer.catch_up()
        pages = []
        cursor = None
        while True:
            result = self.organizer.activity_events(ContextQuery(max_episodes=1, max_items=100,
                                                                token_budget=8000, after_item_id=cursor))
            pages.append(result)
            self.assertLessEqual(len(pages), 3)
            self.assertEqual(len(result["events"]), 1)
            cursor = result["next_cursor"]
            if cursor is None:
                break
        refs = [ref for page in pages for event in page["events"] for ref in event["source_observation_ids"]]
        self.assertEqual(len(refs), len(set(refs)))
        self.assertEqual(set(refs), {row.observation_id for row in rows})
        self.assertEqual([page["truncated"] for page in pages], [True, True, False])

    def test_repository_five_hundred_item_boundary_keeps_a_continuation(self):
        rows = [self.append("window_context", 1000 + index, window="w", app="f",
                            observation_id=str(index)) for index in range(501)]
        self.organizer.catch_up(batch_size=500)
        first = self.organizer.activity_events(ContextQuery(max_items=500, token_budget=100_000))
        self.assertEqual(len(first["events"]), 1)
        self.assertEqual(len(first["events"][0]["emitted_item_ids"]), 500)
        self.assertTrue(first["truncated"])
        self.assertIsNotNone(first["next_cursor"])
        self.assertTrue(first["events"][0]["partial_event"])
        second = self.organizer.activity_events(ContextQuery(max_items=500, token_budget=100_000,
                                                             after_item_id=first["next_cursor"]))
        self.assertEqual(len(second["events"][0]["emitted_item_ids"]), 1)
        self.assertFalse(second["truncated"])
        self.assertIsNone(second["next_cursor"])
        refs = [ref for page in (first, second) for event in page["events"] for ref in event["source_observation_ids"]]
        self.assertEqual(len(refs), len(set(refs)))
        self.assertEqual(set(refs), {row.observation_id for row in rows})
        self.assertEqual(first["events"][0]["event_id"], second["events"][0]["event_id"])


if __name__ == "__main__":
    unittest.main()
