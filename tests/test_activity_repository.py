"""Isolation, provenance and lifecycle regressions for derived activity storage."""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from openassist.core.activity.knowledge import ActivityEpisode, ActivityItem, Evidence, KnowledgeStore


class ActivityRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "knowledge.sqlite3"
        self.store = KnowledgeStore(self.path)

    def tearDown(self) -> None:
        self.store.close()
        self.directory.cleanup()

    def put(self, item_id: str, *, at: int = 100, episode: str | None = None,
            window: str = "window-1", session: str | None = None,
            actor: str = "UNKNOWN", scope: str = "foreground", title: str | None = None,
            url: str | None = None, document: str | None = None,
            source_ids: tuple[str, ...] | None = None,
            context_field_sources: dict[str, tuple[str, ...]] | None = None) -> ActivityItem:
        item = ActivityItem(
            item_id=item_id, kind="window", start_utc_ms=at, end_utc_ms=at,
            app_id="fixture.exe", window_instance_id=window, title=title, url=url,
            document_ref=document, text_summary="fixture searchable text", episode_id=episode,
            source_observation_ids=source_ids or (f"obs-{item_id}",), actor=actor,
            structured={"source": {"session_instance_id": session, "scope": scope},
                        **({"context_field_sources": context_field_sources}
                           if context_field_sources else {})},
        )
        evidence = Evidence(
            evidence_id=f"evidence-{item_id}", source_observation_ids=item.source_observation_ids,
            observed_at_utc_ms=at, received_at_utc_ms=at, content_type="window",
            text=item.text_summary,
        )
        self.store.upsert_item(item, evidence)
        if episode:
            self.store.upsert_episode(ActivityEpisode(
                episode_id=episode, start_utc_ms=at, end_utc_ms=at, primary_app=item.app_id,
                primary_window_id=window, item_ids=(item_id,),
            ))
        return item

    def test_transaction_rolls_back_items_evidence_search_episodes_and_checkpoint(self) -> None:
        self.put("existing", episode="existing-episode")
        with self.assertRaisesRegex(RuntimeError, "projection failed"):
            with self.store.transaction():
                self.store.finalize_episodes(200)
                self.put("partial", episode="partial-episode")
                self.store.set_checkpoint("fixture", "generation", 10)
                raise RuntimeError("projection failed")
        self.assertIsNone(self.store.get_item("partial"))
        self.assertIsNone(self.store.get_item_by_observation_id("obs-partial"))
        self.assertIsNone(self.store.get_episode("partial-episode"))
        self.assertEqual(self.store.get_evidence(("evidence-partial",)), [])
        self.assertFalse(self.store.get_episode("existing-episode").finalized)
        self.assertEqual(self.store.checkpoint("fixture"), ("", 0))
        self.assertEqual([item.item_id for item in self.store.query_items(query="searchable")], ["existing"])

    def test_upsert_item_serialization_failure_cannot_leave_partial_rows(self) -> None:
        item = ActivityItem(item_id="bad", kind="input", start_utc_ms=100, end_utc_ms=100,
                            source_observation_ids=("obs-bad",))
        bad_evidence = Evidence(evidence_id="bad-evidence", source_observation_ids=("obs-bad",),
                                observed_at_utc_ms=100, received_at_utc_ms=100,
                                content_type="input", structured={"unserializable": object()})
        with self.assertRaises(TypeError):
            self.store.upsert_item(item, bad_evidence)
        self.assertIsNone(self.store.get_item("bad"))
        self.assertIsNone(self.store.get_item_by_observation_id("obs-bad"))
        self.assertEqual(self.store.health().item_count, 0)
        self.put("after-failure")
        self.assertEqual(self.store.health().item_count, 1)

    def test_nested_transaction_can_rollback_inner_projection_and_commit_outer(self) -> None:
        with self.store.transaction():
            self.put("before")
            try:
                with self.store.transaction():
                    self.put("failed", episode="failed-episode")
                    raise ValueError("inner failed")
            except ValueError:
                pass
            self.put("after")
        self.store.close()
        self.store = KnowledgeStore(self.path)
        self.assertIsNotNone(self.store.get_item("before"))
        self.assertIsNotNone(self.store.get_item("after"))
        self.assertIsNone(self.store.get_item("failed"))
        self.assertIsNone(self.store.get_episode("failed-episode"))

    def test_observation_lookup_is_exact_updates_sources_and_finds_episode(self) -> None:
        item = self.put("indexed", episode="episode", source_ids=("obs-a", "obs-a-long"))
        self.assertEqual(self.store.get_item_by_observation_id("obs-a").item_id, item.item_id)
        self.assertIsNone(self.store.get_item_by_observation_id("obs"))
        self.assertEqual(self.store.get_episode_by_observation_id("obs-a-long").episode_id, "episode")
        updated = replace(item, source_observation_ids=("obs-revised",))
        evidence = Evidence(evidence_id="evidence-indexed", source_observation_ids=("obs-revised",),
                            observed_at_utc_ms=100, received_at_utc_ms=100, content_type="window")
        self.store.upsert_item(updated, evidence)
        self.assertIsNone(self.store.get_item_by_observation_id("obs-a"))
        self.assertEqual(self.store.get_item_by_observation_id("obs-revised").item_id, "indexed")
        self.assertEqual(self.store.get_evidence((evidence.evidence_id,))[0].source_observation_ids,
                         ("obs-revised",))

    def test_old_store_backfills_source_index_without_changing_projection_version(self) -> None:
        self.put("legacy", episode="legacy-episode", source_ids=('obs-"quoted"',))
        self.store._connection.execute("DROP TABLE activity_item_sources")
        self.store._connection.execute("DROP TABLE projection_errors")
        self.store._connection.execute("DROP TABLE activity_collection_gaps")
        self.store._connection.execute("UPDATE knowledge_meta SET value='2' WHERE key='schema_version'")
        self.store._connection.commit()
        self.store.close()
        self.store = KnowledgeStore(self.path)
        self.assertEqual(self.store.get_item_by_observation_id('obs-"quoted"').item_id, "legacy")
        self.assertEqual(self.store.projection_versions(), ("activity.v1",))
        self.assertEqual(self.store.list_projection_errors(), [])

    def test_window_context_resolves_fields_without_borrowing_future_or_other_session(self) -> None:
        self.put("resource", at=100, session="session-a", title="old title",
                 url="https://fixture.invalid/a", document="fixture-document")
        self.put("updated-title", at=150, session="session-a", title="new title")
        self.put("other-session", at=160, session="session-b", title="other title", url="https://fixture.invalid/b")
        self.put("future", at=300, session="session-a", title="future title", url="https://fixture.invalid/future")
        context = self.store.latest_window_context(window_id="window-1", at_utc_ms=200,
                                                   app_id="fixture.exe", session_instance_id="session-a")
        self.assertEqual((context.title, context.url, context.document_ref),
                         ("new title", "https://fixture.invalid/a", "fixture-document"))
        self.assertEqual(set(context.structured["context_observation_ids"]), {"obs-resource", "obs-updated-title"})
        self.assertIsNone(self.store.latest_window_context(window_id="window-1", at_utc_ms=99))
        self.assertIsNone(self.store.latest_window_context(window_id="window-1", at_utc_ms=200, app_id="other.exe"))

    def test_window_context_ignores_later_item_that_only_inherited_url(self) -> None:
        original = self.put("resource", at=100, session="session-a",
                            url="https://fixture.invalid/a")
        # This input item carries the previous URL for query convenience, but
        # it is not a new observation of the address bar.
        self.put("inherited", at=200, session="session-a",
                 url="https://fixture.invalid/a",
                 context_field_sources={"url": ("obs-resource",)})
        late = self.put("late-direct", at=150, session="session-a",
                        url="https://fixture.invalid/b")
        context = self.store.latest_window_context(window_id="window-1", at_utc_ms=200,
                                                   app_id="fixture.exe", session_instance_id="session-a")
        self.assertEqual(context.url, late.url)
        self.assertEqual(context.structured["context_field_sources"]["url"], late.source_observation_ids)
        self.assertNotIn("inherited", context.structured["context_observation_ids"])

    def test_episode_latest_url_skips_later_input_and_empty_urls(self) -> None:
        self.put("page", at=100, episode="episode", url="https://fixture.invalid/a")
        self.put("input", at=150, episode="episode")
        self.put("blank", at=200, episode="episode", url="   ")
        self.assertEqual(self.store.latest_item_in_episode("episode").item_id, "page")

    def test_foreground_change_preserves_background_and_monitor_scope(self) -> None:
        self.put("foreground", episode="foreground-episode", window="old-window")
        self.put("current", episode="current-episode", window="current-window")
        self.put("background", episode="background-episode", window="other-window", actor="BACKGROUND")
        self.put("monitor", episode="monitor-episode", window="opaque-screen-id", scope="monitor")
        self.store.finalize_episodes(200, reason="window_changed", except_window="current-window",
                                     foreground_only=True)
        self.assertEqual(self.store.get_episode("foreground-episode").end_reason, "window_changed")
        for episode in ("current-episode", "background-episode", "monitor-episode"):
            self.assertFalse(self.store.get_episode(episode).finalized, episode)

    def test_gap_marks_same_session_foreground_and_background_without_extending_span(self) -> None:
        self.put("foreground-a", episode="foreground-a-episode", session="session-a")
        self.put("monitor-a", episode="monitor-a-episode", session="session-a", actor="BACKGROUND", scope="monitor")
        self.put("foreground-b", episode="foreground-b-episode", session="session-b")
        self.put("future", at=300, episode="future-episode", session="session-a")
        self.assertEqual(self.store.finalize_for_gap(200, ("fixture_restart",), session_instance_id="session-a"), 2)
        for episode_id in ("foreground-a-episode", "monitor-a-episode"):
            episode = self.store.get_episode(episode_id)
            self.assertTrue(episode.finalized)
            self.assertEqual(episode.end_reason, "collection_gap")
            self.assertEqual(episode.end_utc_ms, 100)
            self.assertIn("fixture_restart", episode.data_gaps)
            self.assertIn("collection_gap", episode.data_gaps)
        self.assertFalse(self.store.get_episode("foreground-b-episode").finalized)
        self.assertFalse(self.store.get_episode("future-episode").finalized)

    def test_context_does_not_cross_session_or_global_gap_and_retains_post_gap_sources(self) -> None:
        self.put("a", session="session-a", title="old", url="https://fixture.invalid/a")
        self.put("b", session="session-b", title="other", url="https://fixture.invalid/b")
        self.store.finalize_for_gap(150, ("restart",), session_instance_id="session-a")
        self.assertIsNone(self.store.latest_window_context(window_id="window-1", at_utc_ms=200,
                                                           session_instance_id="session-a"))
        self.assertIsNotNone(self.store.latest_window_context(window_id="window-1", at_utc_ms=200,
                                                              session_instance_id="session-b"))
        self.put("after", at=170, session="session-a", title="after gap")
        context = self.store.latest_window_context(window_id="window-1", at_utc_ms=200,
                                                   session_instance_id="session-a")
        self.assertEqual(context.title, "after gap")
        self.assertIsNone(context.url)
        self.assertEqual(context.structured["context_observation_ids"], ("obs-after",))
        self.store.finalize_for_gap(210, ("global restart",))
        self.assertIsNone(self.store.latest_window_context(window_id="window-1", at_utc_ms=210,
                                                           session_instance_id="session-b"))

    def test_gap_ledger_rolls_back_with_failed_projection(self) -> None:
        self.put("context", session="session-a", title="still valid")
        with self.assertRaises(ValueError):
            with self.store.transaction():
                self.store.finalize_for_gap(150, ("restart",), session_instance_id="session-a")
                raise ValueError("failed gap projection")
        self.assertIsNotNone(self.store.latest_window_context(window_id="window-1", at_utc_ms=200,
                                                              session_instance_id="session-a"))

    def test_projection_failures_persist_retry_budget_and_clear_per_consumer(self) -> None:
        for _ in range(3):
            self.store.record_projection_error("failed", 9, ValueError("fixture failure"), consumer_id="a")
        self.store.record_projection_error("retryable", 10, ValueError("fixture failure"), consumer_id="a")
        self.store.record_projection_error("failed", 9, ValueError("independent consumer"), consumer_id="b")
        self.store.close()
        self.store = KnowledgeStore(self.path)
        errors = self.store.list_projection_errors(consumer_id="a")
        self.assertEqual([(row["observation_id"], row["attempts"]) for row in errors], [("failed", 3), ("retryable", 1)])
        self.assertEqual([row["observation_id"] for row in self.store.list_projection_errors(consumer_id="a", retryable_only=True)],
                         ["retryable"])
        self.assertEqual(self.store.health("a").projection_error_count, 2)
        self.store.clear_projection_error("failed", consumer_id="a")
        self.assertEqual(self.store.health("a").projection_error_count, 1)
        self.assertEqual(self.store.health("b").projection_error_count, 1)
        self.store.reset()
        self.assertEqual(self.store.list_projection_errors(consumer_id="a"), [])
        self.assertEqual(self.store.list_projection_errors(consumer_id="b"), [])

    def test_projection_versions_include_episode_and_reset_clears_sources(self) -> None:
        self.put("v1")
        self.store.upsert_episode(ActivityEpisode(episode_id="v2-episode", start_utc_ms=100, end_utc_ms=100,
                                                   projection_version="activity.v2"))
        self.assertEqual(self.store.projection_versions(), ("activity.v1", "activity.v2"))
        self.store.reset()
        self.assertEqual(self.store.projection_versions(), ())
        self.assertIsNone(self.store.get_item_by_observation_id("obs-v1"))


if __name__ == "__main__":
    unittest.main()
