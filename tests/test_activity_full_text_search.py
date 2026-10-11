"""Complete evidence is searchable independently of bounded reader summaries."""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from openassist.core.activity.knowledge import ActivityOrganizer, KnowledgeStore
from openassist.core.activity.models import Observation
from openassist.core.activity.store import ObservationStore


class ActivityFullTextSearchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        self.source = ObservationStore(root / "raw.sqlite3")
        self.knowledge = KnowledgeStore(root / "knowledge.sqlite3")
        self.organizer = ActivityOrganizer(self.source, self.knowledge)
        self.fts_available = self.knowledge._fts_available
        self.sequence = 0

    def tearDown(self) -> None:
        self.knowledge.close()
        self.source.close()
        self.directory.cleanup()

    def append(self, channel: str, body: str, *, at: int = 1000, app: str = "target.exe", window: str = "target-window"):
        kinds = {"snapshot": "input_text", "insert": "input_text", "uia": "context_snapshot",
                 "ocr": "content_snapshot", "clipboard": "clipboard_content"}
        fields = {
            "snapshot": {"payload": {"operation": "snapshot", "text": body, "baseline_known": False}},
            "insert": {"payload": {"operation": "insert", "inserted_text": body, "baseline_known": True}},
            "uia": {"payload": {"uia_status": "ok"}, "uia_summary": {"nodes": [{"text": body}]}},
            "ocr": {"ocr_summary": {"status": "ok", "text": body}},
            "clipboard": {"payload": {"status": "ok", "text": body}},
        }
        row = self.source.append(Observation.create(
            kind=kinds[channel], source_id="fixture", source_epoch="epoch", source_seq=self.sequence,
            boot_id="boot", observed_at_utc_ms=at, app_id=app, window_instance_id=window,
            **fields[channel],
        ))
        self.sequence += 1
        self.assertEqual(self.organizer.catch_up().errors, 0)
        return row, self.knowledge.get_item_by_observation_id(row.observation_id)

    def fts_modes(self):
        return (True, False) if self.fts_available else (False,)

    def fts_ids(self, query: str):
        with self.knowledge._lock:
            return {row[0] for row in self.knowledge._execute(
                "SELECT item_id FROM activity_fts WHERE activity_fts MATCH ?", (query,),
            ).fetchall()}

    def test_full_input_snapshot_edit_uia_ocr_and_clipboard_tails_are_searchable(self) -> None:
        body = "前文" * 7000 + "\n英语尾词 uniquetailtoken\n前接中文尾部独有词后接"
        records = [self.append(channel, body, at=1000 + index)
                   for index, channel in enumerate(("snapshot", "insert", "uia", "ocr", "clipboard"))]
        expected = {item.item_id for _, item in records}
        if self.fts_available:
            # Query the index itself so the literal fallback cannot mask a
            # regression that indexes only the first 12,000 input characters.
            self.assertEqual(self.fts_ids("uniquetailtoken"), expected)
        for enabled in self.fts_modes():
            self.knowledge._fts_available = enabled
            for query in ("uniquetailtoken", "中文尾部独有词"):
                with self.subTest(fts=enabled, query=query):
                    self.assertEqual({item.item_id for item in self.knowledge.query_items(query=query)}, expected)
        for row, item in records:
            self.assertEqual(len(item.text_summary), 12000)
            self.assertTrue(item.structured["projection"]["text_summary_truncated"])
            evidence = self.knowledge.get_evidence(item.structured["activity"]["evidence_ids"])[0]
            self.assertEqual(evidence.text, body)
            self.assertEqual(self.knowledge.get_item(item.item_id).to_dict(), item.to_dict())
            raw = self.source.get(row.observation_id)
            self.assertEqual(raw.to_dict(), row.to_dict())

    def test_old_summary_only_index_recalls_full_evidence_without_rebuild(self) -> None:
        if not self.fts_available:
            self.skipTest("FTS5 is unavailable")
        body = "旧索引前文" * 3000 + "\nlegacytailtoken 前接旧正文尾词后接"
        _, item = self.append("insert", body)
        with self.knowledge._lock:
            self.knowledge._execute("UPDATE activity_fts SET text_summary=? WHERE item_id=?", (item.text_summary, item.item_id))
        self.assertEqual(self.fts_ids("legacytailtoken"), set())
        for query in ("legacytailtoken", "旧正文尾词"):
            with self.subTest(query=query):
                self.assertEqual([value.item_id for value in self.knowledge.query_items(query=query)], [item.item_id])
        self.assertEqual(self.knowledge.get_item(item.item_id).text_summary, item.text_summary)
        self.assertEqual(self.knowledge.get_evidence(item.structured["activity"]["evidence_ids"])[0].text, body)

    def test_mixed_fts_and_literal_hits_keep_filtered_stable_pages(self) -> None:
        prefix = "不可见摘要之外的正文" * 1600 + "\n"
        marker = "内部尾词"
        selected = []
        for channel, at, tail in (
            ("insert", 2000, marker), ("snapshot", 3000, "前接" + marker + "后接"),
            ("insert", 3000, "前接" + marker + "后接"), ("snapshot", 5000, "前接" + marker + "后接"),
        ):
            _, item = self.append(channel, prefix + tail, at=at)
            selected.append(item)
        for channel, at, app, window in (
            ("insert", 1999, "target.exe", "target-window"),
            ("insert", 6000, "target.exe", "target-window"),
            ("insert", 4000, "other.exe", "target-window"),
            ("insert", 4100, "target.exe", "other-window"),
            ("ocr", 4200, "target.exe", "target-window"),
        ):
            self.append(channel, prefix + "前接" + marker + "后接", at=at, app=app, window=window)
        if self.fts_available:
            self.assertEqual(self.fts_ids(marker), {selected[0].item_id})
        expected = [item.item_id for item in sorted(selected, key=lambda item: (item.start_utc_ms, item.item_id), reverse=True)]
        for enabled in self.fts_modes():
            self.knowledge._fts_available = enabled
            with self.subTest(fts=enabled):
                seen = []
                cursor = None
                while True:
                    page = self.knowledge.query_items(
                        query=marker, start_utc_ms=2000, end_utc_ms=6000, apps=("target.exe",),
                        windows=("target-window",), kinds=("input",), limit=2, after_item_id=cursor,
                    )
                    if not page:
                        break
                    seen.extend(item.item_id for item in page)
                    cursor = page[-1].item_id
                self.assertEqual(seen, expected)
                self.assertEqual(len(seen), len(set(seen)))

    def test_tail_literal_wildcards_and_backslashes_are_escaped(self) -> None:
        prefix = "完整证据前文" * 2500 + "\n"
        items = {}
        for name, tail in (("combined", "100%_\\tail"), ("percent", "100%tail"),
                           ("underscore", "100_tail"), ("backslash", "100\\tail"), ("plain", "100XAtail")):
            _, items[name] = self.append("insert", prefix + tail)
        expected = {"%": {"combined", "percent"}, "_": {"combined", "underscore"},
                    "\\": {"combined", "backslash"}, "%_\\": {"combined"}}
        for enabled in self.fts_modes():
            self.knowledge._fts_available = enabled
            for query, names in expected.items():
                with self.subTest(fts=enabled, query=query):
                    self.assertEqual({item.item_id for item in self.knowledge.query_items(query=query)},
                                     {items[name].item_id for name in names})

    def test_evidence_update_replaces_old_full_text_index_content(self) -> None:
        _, item = self.append("insert", "原文" * 7000 + " oldtailtoken")
        evidence = self.knowledge.get_evidence(item.structured["activity"]["evidence_ids"])[0]
        updated = replace(evidence, text="原文" * 7000 + " newtailtoken")
        self.knowledge.upsert_item(item, updated)
        for enabled in self.fts_modes():
            self.knowledge._fts_available = enabled
            with self.subTest(fts=enabled):
                self.assertEqual(self.knowledge.query_items(query="oldtailtoken"), [])
                self.assertEqual([value.item_id for value in self.knowledge.query_items(query="newtailtoken")], [item.item_id])


if __name__ == "__main__":
    unittest.main()
