from __future__ import annotations

import json
import unittest
from dataclasses import replace
from types import SimpleNamespace

from mirally.core.activity.knowledge.models import ActivityItem, Evidence
from mirally.runtime.memory.synthesis import build_evidence, split_evidence


class _Knowledge:
    def __init__(self, items, evidence):
        self.items = {item.item_id: item for item in items}
        self.evidence = evidence
        self.pages = []

    def get_item(self, value):
        return self.items.get(value)

    def get_evidence_for_items(self, values):
        self.pages.append(list(values))
        return [e for value in values for e in self.evidence.get(value, ())]

    def evidence_ids_for_items(self, values):
        return {value: tuple(e.evidence_id for e in self.evidence.get(value, ())) for value in values}


def item(value="one", **kwargs):
    return ActivityItem(value, "screen_content", 1000, 2000,
                        app_id="editor", title="Work", text_summary="short index excerpt",
                        source_observation_ids=("obs-" + value,), **kwargs)


def evidence(value="one", text="complete captured text", **kwargs):
    return Evidence("evidence-" + value, ("obs-" + value,), 1000, 1100, "text", text, **kwargs)


def wire(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


class MemorySynthesisTests(unittest.TestCase):
    def build(self, items, captured, states=None):
        knowledge = _Knowledge(items, captured)
        rows = build_evidence(SimpleNamespace(knowledge=knowledge), states or [
            {"scope": "desktop", "item_ids": [row.item_id for row in items]}])
        return rows, knowledge

    def test_complete_evidence_tail_is_used_without_raw_assets(self):
        text = "头部" + "完整正文" * 6000 + "最后一段的重要内容"
        captured = evidence(text=text, structured={"raw": {"secret_blob": "do not include"},
            "activity": {"action": "input_metadata_observed", "change": {"click_count": 3,
                "arbitrary_payload": "not allowed"}}}, asset_refs=("private.png",))
        rows, _ = self.build([item()], {"one": [captured]})
        self.assertEqual(rows[0]["text"], text)
        self.assertEqual(rows[0]["operations"]["change"], {"click_count": 3})
        serialized = wire(rows).decode()
        for forbidden in ("private.png", "secret_blob", "arbitrary_payload", '"structured"', '"asset_refs"'):
            self.assertNotIn(forbidden, serialized)

    def test_duplicates_merge_monitor_context_and_every_reference_in_time_order(self):
        first = item("one", structured={"context_observation_ids": ["context"],
            "source": {"linked_observation_ids": ["linked"]}})
        second = replace(item("two"), title="Monitor view", start_utc_ms=3000, end_utc_ms=4000,
            structured={"source": {"scope": "monitor", "monitor_id": "2"}}, missing_reasons=("ocr_partial",))
        rows, _ = self.build([second, first], {"one": [evidence()], "two": [evidence("two")]})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["scopes"], ["desktop", "monitor:2"])
        self.assertEqual(rows[0]["source_observation_ids"], ["obs-one", "context", "linked", "obs-two"])
        self.assertEqual(rows[0]["observed_start_utc_ms"], 1000)
        self.assertEqual(rows[0]["observed_end_utc_ms"], 4000)
        self.assertEqual(rows[0]["missing_reasons"], ["ocr_partial"])
        self.assertEqual(len(rows[0]["contexts"]), 2)

    def test_missing_windows_items_and_evidence_are_explicit(self):
        states = [{"id": "empty", "scope": "desktop", "item_ids": []},
                  {"scope": "desktop", "item_ids": ["missing", "one"]}]
        rows, _ = self.build([item()], {}, states)
        reasons = {reason for row in rows for reason in row["missing_reasons"]}
        self.assertEqual(reasons, {"window_items_unavailable", "projection_item_unavailable", "projection_evidence_unavailable"})
        self.assertTrue(any(row["text"] == "short index excerpt" for row in rows))

    def test_all_items_are_read_in_bounded_pages(self):
        items = [item(str(index)) for index in range(451)]
        rows, knowledge = self.build(items, {row.item_id: [evidence(row.item_id, text=row.item_id)] for row in items})
        self.assertEqual(len(rows), 451)
        self.assertEqual([len(page) for page in knowledge.pages], [200, 200, 51])

    def test_long_text_and_reference_manifest_have_no_loss_and_each_fragment_can_cite(self):
        text = '正文"\n🙂\\' * 6000 + "不可丢失的尾部"
        refs = [f"observation-{index:05d}" for index in range(1000)]
        row = {"text": text, "source_observation_ids": refs, "scopes": ["desktop", "monitor:1"],
               "kind": "screen_content", "observed_start_utc_ms": 123, "observed_end_utc_ms": 456}
        packets = split_evidence([row], 1100)
        flattened = [fragment for packet in packets for fragment in packet]
        self.assertGreater(len(packets), 2)
        self.assertTrue(all(len(wire(packet)) <= 1100 for packet in packets))
        parts = [fragment["text"] for fragment in flattened if fragment.get("field") == "text"]
        self.assertEqual("".join(parts), text)
        self.assertEqual({ref for fragment in flattened for ref in fragment["source_observation_ids"]}, set(refs))
        for fragment in flattened:
            self.assertTrue(fragment["source_observation_ids"])
            self.assertEqual(fragment["kind"], "screen_content")
            self.assertEqual(fragment["observed_start_utc_ms"], 123)
        self.assertEqual(row["source_observation_ids"], refs)

    def test_long_metadata_is_recoverable_and_small_rows_stay_intact(self):
        row = {"text": "short", "source_observation_ids": ["obs"],
               "title": "很长的标题" * 2000, "contexts": [{"url": "https://example.test/" + "x" * 5000}]}
        packets = split_evidence([row], 700)
        flattened = [fragment for packet in packets for fragment in packet]
        self.assertTrue(all(len(wire(packet)) <= 700 for packet in packets))
        self.assertEqual("".join(part["text"] for part in flattened if part.get("field") == "title"), row["title"])
        contexts = "".join(part["text"] for part in flattened if part.get("field") == "contexts")
        self.assertEqual(json.loads(contexts), row["contexts"])
        small = {"text": "small", "source_observation_ids": ["obs"]}
        self.assertEqual(split_evidence([small]), [[small]])
        self.assertEqual(split_evidence([]), [])

    def test_invalid_budget_and_unrepresentable_source_id_fail_explicitly(self):
        for value in (True, 255, 0, 100.0):
            with self.assertRaises(ValueError):
                split_evidence([], value)
        with self.assertRaisesRegex(ValueError, "source observation ID"):
            split_evidence([{"text": "x", "source_observation_ids": ["x" * 1000]}], 500)


if __name__ == "__main__":
    unittest.main()
