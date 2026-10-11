from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from openassist.core.activity.input_text import InputTextTracker
from openassist.core.activity.knowledge import ActivityOrganizer, ContextQuery, KnowledgeStore
from openassist.core.activity.models import Observation
from openassist.core.activity.store import ObservationStore


def _observation(**fields) -> Observation:
    return Observation.create(
        kind="content_snapshot", source_id="fixture", source_epoch="epoch", source_seq=0,
        boot_id="boot", observed_at_utc_ms=1000, **fields,
    )


def _control(text: str, **fields) -> dict:
    return {
        "control_id": "edit", "role": "EditControl", "text_status": "ok",
        "value": text, **fields,
    }


class ActivityRawStorageTests(unittest.TestCase):
    def test_large_multibyte_fields_round_trip_through_all_raw_read_paths(self) -> None:
        text = "中文🙂原文\n" * 12000 + "末尾证据"
        fields = {
            "payload": {"text": text, "metadata": {"operation": "snapshot"}},
            "uia_summary": {
                "status": "ok", "nodes": [{"control_id": "doc", "text": text}],
                "focused_control": {"control_id": "edit", "value": text},
            },
            "visual_summary": {"status": "ok", "details": text, "bounds": [0, 0, 1920, 1080]},
            "ocr_summary": {
                "status": "ok", "text": text,
                "lines": [{"text": text, "words": [{"text": "末尾证据", "bounds": [1, 2, 3, 4]}]}],
            },
        }
        for name, content in fields.items():
            with self.subTest(field=name):
                self.assertGreater(len(json.dumps(content, ensure_ascii=False)), 64 * 1024)
                self.assertGreater(len(json.dumps(content, ensure_ascii=False).encode("utf-8")), 64 * 1024)
                value = _observation(**{name: content})
                self.assertEqual(getattr(Observation.from_dict(value.to_dict()), name), content)
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "observations.sqlite3"
            value = _observation(**fields)
            with ObservationStore(database) as store:
                stored = store.append(value)
                rows, cursor, has_more = store.read_since(0, limit=1)
                self.assertEqual(cursor, stored.ingest_seq)
                self.assertFalse(has_more)
                observations = [stored, rows[0], store.query(limit=1)[0], store.get(stored.observation_id)]
                for observation in observations:
                    self.assertIsNotNone(observation)
                    self.assertEqual(observation.to_dict(), stored.to_dict())
                    self.assertFalse(observation.truncated)
                    for name, content in fields.items():
                        self.assertEqual(getattr(observation, name), content)
                self.assertEqual(Observation.from_dict(stored.to_dict()).to_dict(), stored.to_dict())
            with ObservationStore(database, read_only=True) as store:
                reopened = store.get(stored.observation_id)
                self.assertEqual(reopened.to_dict(), stored.to_dict())

    def test_small_context_budget_preserves_complete_raw_and_evidence_text(self) -> None:
        text = "待保留的中文正文🙂" * 10000 + "终点标记"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with ObservationStore(root / "raw.sqlite3") as source, KnowledgeStore(root / "knowledge.sqlite3") as knowledge:
                row = source.append(Observation.create(
                    kind="input_text", source_id="fixture", source_epoch="epoch", source_seq=0,
                    boot_id="boot", observed_at_utc_ms=1000,
                    payload={"operation": "snapshot", "text": text, "baseline_known": False},
                ))
                organizer = ActivityOrganizer(source, knowledge)
                self.assertEqual(organizer.catch_up().errors, 0)
                bundle = organizer.context(ContextQuery(mode="evidence", token_budget=128))
                self.assertTrue(bundle.truncated)
                self.assertEqual(bundle.items, ())
                self.assertEqual(source.get(row.observation_id).payload["text"], text)
                self.assertEqual(source.read_since()[0][0].payload["text"], text)
                self.assertEqual(source.query(limit=1)[0].payload["text"], text)
                item = knowledge.get_item_by_observation_id(row.observation_id)
                evidence = knowledge.get_evidence(item.structured["activity"]["evidence_ids"])
                self.assertEqual(evidence[0].text, text)

    def test_large_baseline_replacement_and_delete_have_exact_full_deltas(self) -> None:
        prefix = "前文🙂" * 24000
        old_middle = "旧中文正文" * 18000
        new_middle = "新输入中文🙂" * 16000
        suffix = "后文🙂" * 22000
        tracker = InputTextTracker()
        snapshot = tracker.observe("window", _control(prefix + old_middle + suffix), input_trigger=True)
        self.assertFalse(snapshot["baseline_known"])
        self.assertIsNone(snapshot["inserted_text"])
        self.assertEqual(snapshot["text"], prefix + old_middle + suffix)
        replacement = tracker.observe("window", _control(prefix + new_middle + suffix), input_trigger=True)
        self.assertEqual(replacement["operation"], "replace")
        self.assertTrue(replacement["baseline_known"])
        self.assertEqual(replacement["offset"], len(prefix))
        self.assertEqual(replacement["removed_char_count"], len(old_middle))
        self.assertEqual(replacement["inserted_text"], new_middle)
        self.assertGreater(len(new_middle), 64 * 1024)
        deletion = tracker.observe("window", _control(prefix + suffix), input_trigger=True)
        self.assertEqual(deletion["operation"], "delete")
        self.assertTrue(deletion["baseline_known"])
        self.assertEqual(deletion["offset"], len(prefix))
        self.assertEqual(deletion["removed_char_count"], len(new_middle))
        self.assertEqual(deletion["inserted_text"], "")
        with tempfile.TemporaryDirectory() as directory:
            with ObservationStore(Path(directory) / "raw.sqlite3") as store:
                for index, payload in enumerate((snapshot, replacement, deletion)):
                    row = store.append(Observation.create(
                        kind="input_text", source_id="fixture", source_epoch="epoch", source_seq=index,
                        boot_id="boot", payload=payload,
                    ))
                    self.assertEqual(store.get(row.observation_id).payload, payload)

    def test_incomplete_baseline_or_current_text_stays_an_unknown_snapshot(self) -> None:
        tracker = InputTextTracker()
        tracker.observe("window", _control("旧前缀", truncated=True), input_trigger=False)
        snapshot = tracker.observe("window", _control("完整新正文"), input_trigger=True)
        self.assertEqual(snapshot["operation"], "snapshot")
        self.assertFalse(snapshot["baseline_known"])
        self.assertIsNone(snapshot["inserted_text"])
        self.assertEqual(snapshot["text"], "完整新正文")
        partial = tracker.observe("window", _control("不完整", truncated=True), input_trigger=True)
        self.assertEqual(partial["operation"], "snapshot")
        self.assertFalse(partial["baseline_known"])
        self.assertIsNone(partial["inserted_text"])

    def test_evicted_control_baseline_stays_unknown_without_clipping_text(self) -> None:
        tracker = InputTextTracker(capacity=1)
        text = "大段中文🙂" * 20000
        tracker.observe("old-window", _control(text), input_trigger=False)
        tracker.observe("other-window", _control("其他正文"), input_trigger=False)
        snapshot = tracker.observe("old-window", _control(text + "新末尾"), input_trigger=True)
        self.assertFalse(snapshot["baseline_known"])
        self.assertIsNone(snapshot["inserted_text"])
        self.assertEqual(snapshot["text"], text + "新末尾")

    def test_raw_fields_still_require_serializable_json_objects(self) -> None:
        for name in ("payload", "uia_summary", "visual_summary", "ocr_summary"):
            with self.subTest(field=name):
                with self.assertRaises(ValueError):
                    _observation(**{name: ["invalid top-level array"]})
                with self.assertRaises(TypeError):
                    _observation(**{name: {"invalid": {"a", "set"}}})
        with self.assertRaises(ValueError):
            _observation(payload=None)
        _observation(uia_summary=None, visual_summary=None, ocr_summary=None)

    def test_protocol_constraints_remain_enforced(self) -> None:
        base = {"kind": "input_text", "source_id": "fixture", "source_epoch": "epoch", "source_seq": 0, "boot_id": "boot"}
        for invalid in (
            {"kind": ""}, {"kind": "x" * 81}, {"source_id": ""}, {"source_epoch": ""},
            {"source_seq": -1}, {"policy_version": 0}, {"actor": "unrecognized"}, {"collection_level": "invalid"},
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                Observation.create(**{**base, **invalid})


if __name__ == "__main__":
    unittest.main()
