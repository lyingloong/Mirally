"""Raw collection stays lossless when later views use small budgets."""

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from mirally.core.activity import ActivityPolicy, CollectionLevel, ObservationStore
from mirally.core.activity.knowledge import ActivityOrganizer, ContextQuery, KnowledgeStore
from mirally.platform.activity.ocr import OCRResult
from mirally.runtime.activity.config import ActivityConfig
from mirally.runtime.activity.service import ActivitySensor


class ActivityRawPipelineTests(unittest.TestCase):
    def test_large_input_and_ocr_survive_sensor_store_projection_and_bounded_query(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            body = "完整原文😀" * 22000
            lines = tuple({"text": "行正文", "words": [{"text": str(index), "bounds": {"x": index}}]}
                          for index in range(2400))
            ocr = OCRResult("ok", body, lines, language="zh-Hans")
            source = SimpleNamespace(available=True, _boot="fixture")
            config = replace(ActivityConfig.disabled(root), enabled=True,
                             database_path=root / "raw.sqlite3", asset_directory=root / "assets",
                             policy=ActivityPolicy(enabled=True, default_level=CollectionLevel.CONTENT,
                                                   exclude_self=False, max_payload_bytes=1024),
                             ocr_enabled=True)
            sensor = ActivitySensor(config, source=source,
                                    ocr_source=SimpleNamespace(available=True, recognize=lambda *args, **kwargs: ocr))
            try:
                captured = sensor._recognize_capture(root / "fixture.png")
                self.assertEqual(captured.text, body)
                self.assertEqual(captured.lines, lines)
                edit = sensor.emit("input_text", requested_level=CollectionLevel.CONTENT, app_id="fixture",
                                   payload={"operation": "insert", "inserted_text": body, "baseline_known": True})
                frame = sensor.emit("content_snapshot", requested_level=CollectionLevel.CONTENT,
                                    ocr_summary=captured.as_dict())
                tree = sensor.emit("context_snapshot", requested_level=CollectionLevel.CONTENT,
                                   uia_summary={"nodes": [{"text": body}], "capture_scope": {
                                       "root": "target_window", "excluded_subtrees": ["password", "offscreen"]}})
                self.assertEqual(edit.payload["inserted_text"], body)
                self.assertNotIn("payload_limit", edit.missing_reasons)
                self.assertEqual(frame.ocr_summary["text"], body)
                self.assertEqual(len(frame.ocr_summary["lines"]), 2400)
                with KnowledgeStore(root / "knowledge.sqlite3") as knowledge:
                    organizer = ActivityOrganizer(sensor.store, knowledge)
                    self.assertEqual(organizer.catch_up().projected, 3)
                    item = knowledge.get_item_by_observation_id(frame.observation_id)
                    self.assertTrue(item.structured["projection"]["text_summary_truncated"])
                    self.assertEqual(item.structured["ocr_text"], body)
                    evidence = organizer.get_evidence(item.structured["activity"]["evidence_ids"])
                    self.assertEqual(evidence[0].text, body)
                    organizer.activity_timeline(ContextQuery(token_budget=128))
                    scope_item = knowledge.get_item_by_observation_id(tree.observation_id)
                    self.assertEqual(scope_item.structured["source"]["capture_scope"]["root"], "target_window")
                    self.assertEqual(sensor.store.get(edit.observation_id).payload["inserted_text"], body)
                    self.assertEqual(sensor.store.get(frame.observation_id).ocr_summary["text"], body)
            finally:
                sensor.close()
            with ObservationStore(root / "raw.sqlite3", read_only=True) as reopened:
                self.assertEqual(reopened.get(edit.observation_id).payload["inserted_text"], body)
                self.assertEqual(reopened.get(frame.observation_id).ocr_summary["text"], body)


if __name__ == "__main__":
    unittest.main()
