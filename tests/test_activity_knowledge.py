"""Regression tests for the rebuildable activity knowledge layer."""

from __future__ import annotations

import tempfile
import json
import hashlib
import os
import subprocess
import sys
import time
import unittest
from dataclasses import replace
from pathlib import Path

from openassist.core.activity import Observation, ObservationStore
from openassist.core.activity.knowledge import ActivityOrganizer, ContextQuery, KnowledgeStore
from openassist.runtime.activity.config import ActivityConfig
from openassist.runtime.activity.organizer_service import ActivityOrganizerService


def _observation(seq: int, kind: str, payload: dict, *, observed: int, app: str = "msedge.exe", window: str = "window-1") -> Observation:
    return Observation.create(
        kind=kind,
        source_id="fixture",
        source_epoch="epoch-1",
        source_seq=seq,
        boot_id="boot-1",
        app_id=app,
        window_instance_id=window,
        observed_at_utc_ms=observed,
        payload=payload,
    )


class ActivityKnowledgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        self.source = ObservationStore(root / "observations.sqlite3")
        self.knowledge = KnowledgeStore(root / "activity-knowledge.sqlite3")
        self.organizer = ActivityOrganizer(self.source, self.knowledge, idle_timeout_ms=30_000)

    def tearDown(self) -> None:
        self.knowledge.close()
        self.source.close()
        self.directory.cleanup()

    def test_projection_groups_window_content_and_input_and_supports_fts(self) -> None:
        self.source.append(_observation(0, "window_context", {"window_title": "Inbox", "browser_url": "https://mail.google.com"}, observed=1000))
        self.source.append(_observation(1, "content_snapshot", {"trigger": "typing"}, observed=2000))
        self.source.append(_observation(2, "input_text", {"inserted_text": "quarterly report"}, observed=3000))

        stats = self.organizer.catch_up()
        self.assertEqual((stats.processed, stats.projected, stats.errors), (3, 3, 0))
        bundle = self.organizer.context(ContextQuery(query="quarterly report", mode="evidence"))
        self.assertEqual(len(bundle.items), 1)
        self.assertEqual(bundle.items[0].text_summary, "quarterly report")
        self.assertEqual(len(bundle.episodes), 1)
        self.assertEqual(len(bundle.evidence), 1)
        self.assertEqual(bundle.evidence[0].text, "quarterly report")
        self.assertEqual(bundle.items[0].episode_id, bundle.episodes[0].episode_id)

    def test_processing_is_idempotent_and_rebuildable(self) -> None:
        self.source.append(_observation(0, "window_context", {"window_title": "Editor"}, observed=1000, app="code.exe"))
        first = self.organizer.catch_up()
        second = self.organizer.catch_up()
        self.assertEqual(first.projected, 1)
        self.assertEqual(second.processed, 0)
        self.assertEqual(self.knowledge.health().item_count, 1)

        rebuilt = self.organizer.rebuild()
        self.assertEqual(rebuilt.projected, 1)
        self.assertEqual(self.knowledge.health().item_count, 1)
        self.assertEqual(self.knowledge.health().episode_count, 1)

    def test_late_observation_is_added_to_existing_episode_when_within_window(self) -> None:
        self.source.append(_observation(0, "window_context", {"window_title": "Docs"}, observed=1000, app="winword.exe"))
        self.organizer.catch_up()
        self.source.append(_observation(1, "context_snapshot", {"uia_status": "ok"}, observed=2500, app="winword.exe"))
        self.organizer.catch_up()
        episodes = self.knowledge.query_episodes()
        self.assertEqual(len(episodes), 1)
        self.assertEqual(len(episodes[0].item_ids), 2)
        self.assertGreaterEqual(episodes[0].revision, 2)

    def test_memory_candidate_keeps_evidence_references(self) -> None:
        row = self.source.append(_observation(0, "input_text", {"inserted_text": "I prefer Edge"}, observed=1000))
        self.organizer.catch_up()
        candidate = self.organizer.propose_memory(
            "用户偏好使用 Edge",
            fact_key="user.browser.preference",
            source_observation_ids=(row.observation_id,),
            confidence=0.82,
        )
        values = self.knowledge.list_memory_candidates(status="candidate")
        self.assertEqual(len(values), 1)
        self.assertEqual(values[0].candidate_id, candidate.candidate_id)
        self.assertEqual(values[0].source_observation_ids, (row.observation_id,))

    def test_context_modes_bound_content_and_summary_reports_apps(self) -> None:
        uia = _observation(0, "context_snapshot", {"uia_status": "ok"}, observed=1000, app="code.exe")
        uia = Observation.create(**{**uia.__dict__, "uia_summary": {"status": "ok", "nodes": ({"role": "Document", "text": "code"},)}})
        self.source.append(uia)
        self.source.append(_observation(1, "input_text", {"inserted_text": "private draft"}, observed=2000, app="code.exe"))
        self.organizer.catch_up()
        outline = self.organizer.context(ContextQuery(mode="outline"))
        self.assertEqual(outline.items[0].structured, {})
        structured = self.organizer.context(ContextQuery(mode="structured", include_uia=True))
        self.assertTrue(any("uia_text" in item.structured for item in structured.items))
        summary = self.organizer.activity_summary(ContextQuery(start_utc_ms=0, end_utc_ms=5000, max_items=20, max_episodes=20))
        self.assertEqual(summary["data_status"], "ok")
        self.assertEqual(summary["apps"][0]["app_id"], "code.exe")

    def test_memory_candidate_can_be_retracted(self) -> None:
        candidate = self.organizer.propose_memory("temporary fact", fact_key="test.fact")
        self.assertTrue(self.organizer.update_memory(candidate.candidate_id, "retracted"))
        self.assertEqual(self.knowledge.list_memory_candidates(status="retracted")[0].status, "retracted")

    def test_memory_candidate_can_be_promoted_to_versioned_fact(self) -> None:
        candidate = self.organizer.propose_memory("user prefers Edge", fact_key="browser.preference", confidence=0.9)
        fact = self.organizer.accept_memory(candidate.candidate_id)
        self.assertIsNotNone(fact)
        self.assertEqual(fact.statement, candidate.statement)
        self.assertEqual(self.knowledge.list_memory_facts(status="active")[0].fact_id, fact.fact_id)
        self.assertEqual(self.knowledge.get_memory_candidate(candidate.candidate_id).status, "accepted")

    def test_named_context_queries_are_bounded_and_return_evidence(self) -> None:
        observed = int(time.time() * 1000)
        row = self.source.append(_observation(0, "input_text", {"inserted_text": "search phrase"}, observed=observed))
        self.organizer.catch_up()
        recent = self.organizer.recent_activity(seconds=10)
        self.assertEqual(len(recent.items), 1)
        around = self.organizer.activity_around(observed, window_seconds=1)
        self.assertEqual(around.items[0].source_observation_ids, (row.observation_id,))
        bundle = self.organizer.context(ContextQuery(mode="evidence"))
        evidence = self.organizer.get_evidence([bundle.evidence[0].evidence_id])
        self.assertEqual(evidence[0].source_observation_ids, (row.observation_id,))

    def test_runtime_organizer_service_can_start_and_close_idempotently(self) -> None:
        root = Path(self.directory.name) / "service"
        base = ActivityConfig.disabled(root)
        config = replace(
            base,
            database_path=root / "observations.sqlite3",
            asset_directory=root / "assets",
            knowledge_database_path=root / "activity-knowledge.sqlite3",
            knowledge_poll_interval_seconds=0.05,
            knowledge_batch_size=1,
        )
        # Creation belongs to the collector; the organizer is a read-only consumer.
        with ObservationStore(config.database_path, asset_directory=config.asset_directory):
            pass
        service = ActivityOrganizerService(config)
        try:
            service.start()
            time.sleep(0.08)
            self.assertTrue(service.health()["knowledge"]["available"])
        finally:
            service.close()
            service.close()

    def test_read_only_consumer_preserves_source_and_does_not_start_writer(self) -> None:
        row = self.source.append(_observation(0, "input_text", {"inserted_text": "evidence"}, observed=1000))
        self.source.close()
        before = hashlib.sha256(self.source.path.read_bytes()).digest()
        missing_assets = self.source.path.parent / "absent-assets"
        with ObservationStore(self.source.path, asset_directory=missing_assets, read_only=True) as reader:
            self.assertIsNone(reader._writer)
            self.assertEqual(reader.get(row.observation_id).payload["inserted_text"], "evidence")
            self.assertFalse(missing_assets.exists())
            with self.assertRaises(PermissionError):
                reader.set_state("unexpected", "write")
            with self.assertRaises(PermissionError):
                reader.recover_assets()
        self.assertEqual(hashlib.sha256(self.source.path.read_bytes()).digest(), before)

    def test_chinese_phrase_and_literal_url_are_searchable(self) -> None:
        self.source.append(_observation(0, "window_context", {"window_title": "项目会议纪要", "browser_url": "https://example.test/docs?q=meeting"}, observed=1000))
        self.organizer.catch_up()
        for query in ("会议", "https://example.test/docs?q=meeting"):
            self.assertEqual(len(self.organizer.context(ContextQuery(query=query)).items), 1)

    def test_knowledge_cli_summary_is_utf8_and_preserves_source_database(self) -> None:
        self.source.append(_observation(0, "input_text", {"inserted_text": "会议纪要\ufffc"}, observed=1000))
        self.source.close()
        root = self.source.path.parent
        config = root / "activity.yaml"
        config.write_text(f"enabled: true\nstorage:\n  database_path: {self.source.path.as_posix()}\n"
                          f"  knowledge_database_path: {(root / 'cli-knowledge.sqlite3').as_posix()}\n", encoding="utf-8")
        before = hashlib.sha256(self.source.path.read_bytes()).digest()
        result = subprocess.run(
            [sys.executable, "-m", "openassist.runtime.activity.cli", "--config", str(config), "knowledge-summary", "--sync"],
            cwd=Path(__file__).resolve().parents[1], env={**os.environ, "PYTHONIOENCODING": "ascii"},
            capture_output=True, encoding="utf-8", timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        summary = json.loads(result.stdout)
        self.assertEqual(summary["snippets"][0]["text"], "会议纪要\ufffc")
        self.assertEqual(hashlib.sha256(self.source.path.read_bytes()).digest(), before)

    def test_chinese_substrings_and_full_text_hits_share_stable_filtered_pages(self) -> None:
        for seq, title in enumerate(("项目会议纪要", "会议", "会议纪要草稿")):
            self.source.append(_observation(seq, "window_context", {"window_title": title}, observed=1000 + seq))
        self.source.append(_observation(3, "window_context", {"window_title": "会议"}, observed=2000, app="other.exe"))
        self.organizer.catch_up()
        cursor = None
        titles = []
        for _ in range(4):
            page = self.organizer.context(ContextQuery(query="会议", apps=("msedge.exe",), max_items=1, after_item_id=cursor))
            titles.extend(item.title for item in page.items)
            if not page.next_cursor:
                break
            cursor = page.next_cursor
        self.assertEqual(titles, ["会议纪要草稿", "会议", "项目会议纪要"])
        self.assertEqual(len(self.organizer.context(ContextQuery(query="msedge.exe")).items), 3)

    def test_literal_search_escapes_wildcards_even_without_fts(self) -> None:
        for seq, title in enumerate(("cost 100%_done", "cost 100x-done")):
            self.source.append(_observation(seq, "window_context", {"window_title": title}, observed=1000 + seq))
        self.organizer.catch_up()
        for fts in (True, False):
            self.knowledge._fts_available = fts
            page = self.organizer.context(ContextQuery(query="100%_done"))
            self.assertEqual([item.title for item in page.items], ["cost 100%_done"])

    def test_expired_cursor_and_invalid_time_range_do_not_silently_return_history(self) -> None:
        with self.assertRaisesRegex(ValueError, "cursor"):
            self.organizer.context(ContextQuery(after_item_id="missing-item"))
        for bounds in ({"start_utc_ms": -1}, {"start_utc_ms": 2000, "end_utc_ms": 1000}):
            with self.assertRaises(ValueError):
                self.organizer.context(ContextQuery(**bounds))

    def test_navigation_and_window_switch_close_episodes_without_merging_return_visit(self) -> None:
        now = int(time.time() * 1000)
        self.source.append(_observation(0, "window_context", {"browser_url": "https://example.test/a"}, observed=now))
        self.source.append(_observation(1, "input_batch", {"keyboard_count": 1}, observed=now + 1))
        self.source.append(_observation(2, "window_context", {"browser_url": "https://example.test/b"}, observed=now + 2))
        self.source.append(_observation(3, "foreground_changed", {}, observed=now + 3, app="code.exe", window="window-2"))
        self.source.append(_observation(4, "window_context", {"browser_url": "https://example.test/b"}, observed=now + 4))
        self.organizer.catch_up()
        episodes = self.knowledge.query_episodes()
        self.assertEqual(len(episodes), 4)
        self.assertTrue(all(episode.summary for episode in episodes))
        self.assertIn("navigation", {episode.end_reason for episode in episodes})
        self.assertIn("window_changed", {episode.end_reason for episode in episodes})
        self.assertEqual(sum(not episode.finalized for episode in episodes), 1)

    def test_complete_context_budget_includes_evidence_and_episode_members(self) -> None:
        for seq in range(8):
            self.source.append(_observation(seq, "input_text", {"inserted_text": "中文会议记录" * 200}, observed=1000 + seq))
        self.organizer.catch_up()
        for mode in ("outline", "evidence"):
            bundle = self.organizer.context(ContextQuery(mode=mode, token_budget=2000, max_items=8))
            encoded = json.dumps(bundle.to_dict(), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            self.assertLessEqual(len(encoded), 2000 * 3)
            self.assertTrue(bundle.truncated)
            ids = {item.item_id for item in bundle.items}
            self.assertTrue(all(set(episode.item_ids) <= ids for episode in bundle.episodes))

    def test_result_limit_has_cursor_and_uia_value_is_preserved(self) -> None:
        for seq in range(4):
            row = _observation(seq, "context_snapshot", {"uia_status": "ok"}, observed=1000 + seq)
            self.source.append(replace(row, uia_summary={"nodes": [{"name": "正文", "value": "Actual edited text"}]}))
        self.organizer.catch_up()
        first = self.organizer.context(ContextQuery(max_items=2))
        second = self.organizer.context(ContextQuery(max_items=2, after_item_id=first.next_cursor))
        self.assertTrue(first.truncated)
        self.assertTrue(first.next_cursor)
        self.assertFalse({item.item_id for item in first.items} & {item.item_id for item in second.items})
        self.assertTrue(all("Actual edited text" in item.text_summary for item in first.items))


if __name__ == "__main__":
    unittest.main()
