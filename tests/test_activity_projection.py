"""Content fidelity regressions for deterministic activity projections."""

from __future__ import annotations

import unittest

from mirally.core.activity.models import Observation
from mirally.core.activity.knowledge.projection import build_projection


def observation(kind: str, **values) -> Observation:
    defaults = dict(
        observation_id="obs-fixture", kind=kind, source_id="fixture", source_epoch="epoch-2",
        source_seq=7, ingest_seq=9, boot_id="boot-fixture", clock_epoch=1,
        observed_at_utc_ms=1000, received_at_utc_ms=1800, monotonic_ns=999,
        time_quality="observed", session_instance_id="session-fixture",
        app_id="editor.exe", process_instance_id="process-fixture",
        window_instance_id="window-fixture", identity_confidence="verified_process_start",
    )
    defaults.update(values)
    return Observation.create(**defaults)


class ActivityProjectionTests(unittest.TestCase):
    def project(self, value: Observation):
        return build_projection(value, "activity.v2")

    def test_source_links_and_time_quality_survive_projection(self):
        raw = observation("ocr_result", caused_by=("obs-frame", "obs-trigger"),
                          capture_group_id="capture-A", capture_started_at=1100,
                          capture_finished_at=1500, time_quality="clock_adjusted",
                          payload={"scope": "monitor", "monitor_id": "monitor-1",
                                   "monitor_bounds": [-1920, 0, 1920, 1080]},
                          ocr_summary={"status": "ok", "text": "fixture"})
        item, evidence = self.project(raw)
        source = item.structured["source"]
        for key in ("kind", "source_epoch", "source_seq", "ingest_seq", "session_instance_id",
                    "observed_at_utc_ms", "received_at_utc_ms", "capture_started_at",
                    "capture_finished_at", "monotonic_ns", "clock_epoch", "time_quality",
                    "identity_confidence", "capture_group_id"):
            self.assertEqual(source[key], getattr(raw, key))
        self.assertEqual(source["caused_by"], ["obs-frame", "obs-trigger"])
        self.assertEqual(source["scope"], "monitor")
        self.assertEqual(source["monitor_bounds"], [-1920, 0, 1920, 1080])
        self.assertNotIn("payload", source)
        self.assertEqual(evidence.structured["source"], source)
        self.assertEqual(item.end_utc_ms, 1500)

    def test_deletion_keeps_meaning_and_evidence_references(self):
        raw = observation("input_text", actor="UNKNOWN", actor_basis="observed_committed_text",
                          payload={"operation": "delete", "inserted_text": "",
                                   "removed_char_count": 7, "offset": 9, "baseline_known": True,
                                   "control_id": "edit-A", "provenance": "focused_uia"})
        item, evidence = self.project(raw)
        self.assertEqual(item.structured["input_text"], dict(raw.payload))
        self.assertIn("删除 7 个字符", item.text_summary)
        activity = item.structured["activity"]
        self.assertEqual(activity["action"], "committed_text_changed")
        self.assertEqual(activity["change"]["offset"], 9)
        self.assertEqual(activity["object"]["control_id"], "edit-A")
        self.assertEqual(activity["evidence_ids"], [evidence.evidence_id])
        self.assertEqual(activity["source_observation_ids"], [raw.observation_id])
        self.assertEqual(activity["actor"], "UNKNOWN")
        self.assertIn("actor_unconfirmed", activity["unknowns"])
        self.assertIn("save_status_unconfirmed", activity["unknowns"])
        self.assertEqual(activity["result"], "unconfirmed")
        self.assertEqual(evidence.text, "")

    def test_replace_and_unknown_baseline_are_distinct_facts(self):
        item, evidence = self.project(observation("input_text", payload={
            "operation": "replace", "inserted_text": "项目", "removed_char_count": 3,
            "offset": 4, "baseline_known": True, "control_id": "edit-A",
        }))
        self.assertEqual(item.text_summary, "项目")
        self.assertEqual(evidence.text, "项目")
        self.assertIn("删除 3 个字符", item.structured["activity"]["summary"])
        self.assertEqual(item.structured["activity"]["change"]["inserted_char_count"], 2)
        snapshot, _ = self.project(observation("input_text", payload={
            "operation": "snapshot", "text": "existing", "baseline_known": False,
        }, quality="partial", missing_reasons=("initial_baseline_unknown",)))
        self.assertEqual(snapshot.structured["activity"]["action"], "committed_text_observed")
        self.assertIn("previous_text_baseline_unknown", snapshot.structured["activity"]["unknowns"])
        self.assertNotIn("inserted_char_count", snapshot.structured["activity"]["change"])

    def test_unavailable_input_is_not_an_empty_successful_edit(self):
        item, _ = self.project(observation("input_text", payload={"status": "unavailable"},
                                         quality="unavailable", missing_reasons=("unsupported",)))
        self.assertEqual(item.structured["activity"]["action"], "committed_text_unavailable")
        self.assertTrue(item.text_summary)
        self.assertEqual(item.quality, "unavailable")
        self.assertIn("unsupported", item.missing_reasons)

    def test_clipboard_content_and_signal_keep_sequence_and_status(self):
        payload = {"operation": "paste", "sequence": 12, "status": "ok",
                   "text": "合成剪贴板", "truncated": False}
        item, evidence = self.project(observation("clipboard_content", payload=payload))
        self.assertEqual(item.structured["clipboard"], payload)
        self.assertEqual(item.structured["clipboard_text"], payload["text"])
        self.assertEqual(evidence.text, payload["text"])
        self.assertEqual(item.structured["clipboard_operation"], "paste")
        self.assertEqual(item.structured["activity"]["result"], "unconfirmed")
        self.assertIn("paste_destination_and_success_unconfirmed", item.structured["activity"]["unknowns"])
        signal, _ = self.project(observation("clipboard_operation", payload={"operation": "copy", "sequence": 11}))
        self.assertEqual(signal.structured["clipboard"]["sequence"], 11)
        self.assertEqual(signal.structured["activity"]["action"], "clipboard_signal_observed")

    def test_uia_preserves_relationships_bounds_and_focus_only_text(self):
        nodes = [{"role": "EditControl", "control_id": "edit-A", "parent_id": "root",
                  "bounds": {"x": 3, "y": 5, "width": 40, "height": 20},
                  "read_only": False, "text_status": "ok", "value": "document fixture"}]
        focused = {"control_id": "edit-A", "role": "EditControl", "text_status": "ok",
                   "read_only": False, "text": "focused fixture"}
        raw = observation("context_snapshot", payload={"uia_status": "ok"}, uia_summary={
            "status": "ok", "nodes": nodes, "focused_control": focused,
            "content_enabled": True, "node_count": 1, "truncated": False,
        })
        item, evidence = self.project(raw)
        self.assertEqual(item.structured["uia_nodes"], nodes)
        self.assertEqual(item.structured["focused_control"], focused)
        self.assertEqual(item.structured["uia_metadata"]["content_enabled"], True)
        self.assertIn("focused fixture", evidence.text)
        self.assertIn("document fixture", evidence.text)
        self.assertEqual(item.kind, "document")

    def test_bounded_uia_nodes_are_not_silently_limited_to_500(self):
        nodes = [{"role": "TextControl", "control_id": str(index)} for index in range(510)]
        item, _ = self.project(observation("context_snapshot", uia_summary={"nodes": nodes, "status": "ok"}))
        self.assertEqual(len(item.structured["uia_nodes"]), 510)
        self.assertFalse(item.structured["projection"]["source_truncated"])

    def test_ocr_keeps_full_body_lines_and_language(self):
        body = "识别内容" * 1500
        lines = [{"text": "识别内容", "words": [{"text": "识别", "bounds": [1, 2, 3, 4]}]}]
        item, evidence = self.project(observation("content_snapshot", ocr_summary={
            "status": "ok", "text": body, "lines": lines, "language": "zh-Hans",
            "engine": "fixture", "duration_ms": 7,
        }))
        self.assertEqual(item.text_summary, body)
        self.assertEqual(evidence.text, body)
        self.assertEqual(item.structured["ocr_text"], body)
        self.assertEqual(item.structured["ocr_lines"], lines)
        self.assertEqual(item.structured["ocr_metadata"]["language"], "zh-Hans")
        self.assertEqual(item.quality, "complete")

    def test_summary_compression_is_explicit_without_discarding_evidence(self):
        body = "甲" * 14000
        item, evidence = self.project(observation("ocr_result", ocr_summary={"status": "ok", "text": body}))
        self.assertEqual(len(item.text_summary), 12000)
        self.assertEqual(evidence.text, body)
        self.assertEqual(item.structured["ocr_text"], body)
        self.assertEqual(item.quality, "partial")
        self.assertLess(item.confidence, 1.0)
        self.assertEqual(evidence.quality, "complete")
        self.assertIn("projection_text_summary_limit", item.missing_reasons)
        projection = item.structured["projection"]
        self.assertTrue(projection["text_summary_truncated"])
        self.assertFalse(projection["evidence_text_truncated"])
        self.assertEqual(projection["text_summary_original_chars"], 14000)

    def test_source_truncation_downgrades_both_views_even_when_source_says_complete(self):
        for values in (
            {"truncated": True},
            {"payload": {"truncated": True}},
            {"uia_summary": {"status": "ok", "focused_control": {"truncated": True}}},
            {"ocr_summary": {"status": "ok", "text": "prefix", "missing_reasons": ["ocr_result_limit"]}},
        ):
            with self.subTest(values=values):
                item, evidence = self.project(observation("content_snapshot", **values))
                self.assertEqual(item.quality, "partial")
                self.assertEqual(evidence.quality, "partial")
                self.assertTrue(item.structured["projection"]["source_truncated"])
                self.assertIn("source_content_truncated", item.missing_reasons)
                self.assertEqual(item.structured["source"]["quality"], "complete")

    def test_actor_is_not_promoted_from_visibility_or_control_delta(self):
        for actor, basis in (("UNKNOWN", "foreground_only"), ("BACKGROUND", "monitor_observation"),
                             ("USER", "raw_input_metadata")):
            with self.subTest(actor=actor):
                item, _ = self.project(observation("content_snapshot", actor=actor, actor_basis=basis,
                                                  payload={"scope": "monitor" if actor == "BACKGROUND" else "foreground"}))
                activity = item.structured["activity"]
                self.assertEqual(activity["actor"], actor)
                self.assertEqual(activity["actor_basis"], basis)
                self.assertEqual(activity["action"], "screen_content_observed")
                self.assertIn("reading_unconfirmed", activity["unknowns"])
                self.assertEqual(activity["result"], "unconfirmed")

    def test_failed_capture_does_not_claim_content_was_observed(self):
        examples = (
            ("content_snapshot", {"payload": {"capture_status": "black_frame_filtered"}}, "screen_content_unavailable"),
            ("context_snapshot", {"uia_summary": {"status": "error"}}, "accessible_content_unavailable"),
            ("clipboard_content", {"payload": {"status": "stale", "sequence": 3}}, "clipboard_content_unavailable"),
        )
        for kind, values, expected in examples:
            with self.subTest(kind=kind):
                item, _ = self.project(observation(kind, quality="unavailable", **values))
                self.assertEqual(item.structured["activity"]["action"], expected)
                self.assertIn("未取得", item.structured["activity"]["summary"])
                self.assertEqual(item.structured["activity"]["result"], "unconfirmed")

    def test_projection_is_deterministic_and_does_not_alias_raw_nested_content(self):
        raw = observation("context_snapshot", uia_summary={"nodes": [{"bounds": {"x": 8}, "text": "fixture"}]})
        before = raw.to_dict()
        first = self.project(raw)
        second = self.project(raw)
        self.assertEqual(first[0].to_dict(), second[0].to_dict())
        self.assertEqual(first[1].to_dict(), second[1].to_dict())
        self.assertEqual(raw.to_dict(), before)
        first[0].structured["uia_nodes"][0]["bounds"]["x"] = 99
        self.assertEqual(raw.uia_summary["nodes"][0]["bounds"]["x"], 8)
        self.assertNotEqual(first[0].item_id, build_projection(raw, "activity.v3")[0].item_id)


if __name__ == "__main__":
    unittest.main()
