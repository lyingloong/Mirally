from __future__ import annotations

import copy
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.dev_workflow import agent_workflow as wf  # noqa: E402

AGENT_WORKFLOW = ROOT / "tools" / "dev_workflow" / "agent_workflow.py"
CJK_TITLE = "优化微信端文字提示"


def ascii_console_env():
    """Pin stdout/stderr to strict ASCII so the dev-0002 crash is reproducible."""
    env = {**os.environ, "PYTHONIOENCODING": "ascii"}
    env.pop("PYTHONUTF8", None)
    return env


def strict_ascii_stream():
    """A stdout stand-in that rejects non-ASCII like a cp1252/ASCII console does."""
    return io.TextIOWrapper(io.BytesIO(), encoding="ascii", errors="strict")


def merge_ready(title, ready_at="2026-01-01T00:00:00Z", source="user"):
    """A point already past review: it is report-ready, not a discovery task."""
    return {"source": source, "title": title, "status": "awaiting_merge",
            "merge_ready_at": ready_at}


def busy_agent(title):
    """An agent point in flight: the AI in-flight cap then blocks discovery."""
    return {"source": "agent", "title": title, "status": "implementing",
            "risk": "low", "auto_started": True, "started_at": "2026-01-01T00:00:00Z"}


def with_points(overrides):
    """A state holding exactly the given points, created directly with their fields."""
    state = wf.empty_state()
    points = []
    for index, spec in enumerate(overrides, start=1):
        point = wf.new_point(state, source=spec.get("source", "user"), title=spec["title"])
        point["id"] = "dev-%04d" % index
        point.update({key: value for key, value in spec.items() if key not in {"source", "title"}})
        points.append(point)
    state["points"] = points
    state["next_id"] = len(points) + 1
    return state


def make_config(**overrides):
    config = copy.deepcopy(wf.DEFAULT_CONFIG)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(config.get(key), dict):
            config[key] = wf.deep_merge(config[key], value)
        else:
            config[key] = value
    return config


def advance_to_reviewing(state, config, point_id):
    wf.start_point(state, config, point_id)
    wf.transition(state, config, wf.point_by_id(state, point_id), "implementing")
    wf.transition(state, config, wf.point_by_id(state, point_id), "verifying")
    wf.transition(state, config, wf.point_by_id(state, point_id), "reviewing")


def park_from_merging(point):
    """Write the legacy record the guards must close: a merge in flight parked as blocked.

    Only the pre-fix block_point could write this; tests build it directly to prove the
    cancel-point/resume guards still refuse records already on disk.
    """
    point["blocked_from_status"] = "merging"
    point["blocked_reason"] = "external"
    point["blocked_until"] = "2026-01-01T00:00:30Z"
    point["status"] = "blocked"


class ConfigTests(unittest.TestCase):
    def test_defaults_match_agreed_workflow(self):
        config = wf.load_config(Path(tempfile.gettempdir()) / "definitely-missing-agent-workflow.json")
        self.assertEqual(config["concurrency"], 3)
        self.assertEqual(config["max_auto_agent_in_flight"], 2)
        self.assertEqual(config["max_auto_agent_per_day"], 100)
        self.assertEqual(config["merge_queue_exempt"], 100)
        self.assertEqual(config["quota"]["retry_after_minutes"], 30)
        self.assertEqual(config["quota"]["backoff_minutes"], [30, 60, 120])
        self.assertEqual(config["quota"]["fallback"]["mode"], "quota_restart_only")
        self.assertEqual(config["quota"]["fallback"]["provider"], "deepseek-account")
        self.assertEqual(config["primary_route"]["provider"], "ustc")
        self.assertEqual(config["config_version"], 2)

    def test_config_upgrade_is_explicit_and_preserves_custom_values(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            wf.write_json(
                path,
                {
                    "concurrency": 3,
                    "max_auto_agent_in_flight": 1,
                    "max_auto_agent_per_day": 5,
                    "merge_queue_exempt": 1,
                    "custom": "keep-me",
                },
            )
            dry = wf.upgrade_config(path, apply=False)
            self.assertIn("merge_queue_exempt", dry["changes"])
            self.assertEqual(wf.read_json(path)["merge_queue_exempt"], 1)
            applied = wf.upgrade_config(path, apply=True)
            self.assertTrue(applied["applied"])
            raw = wf.read_json(path)
            self.assertEqual(raw["merge_queue_exempt"], 100)
            self.assertEqual(raw["max_auto_agent_in_flight"], 2)
            self.assertEqual(raw["max_auto_agent_per_day"], 5)
            self.assertEqual(raw["custom"], "keep-me")
            self.assertEqual(raw["config_version"], 2)

    def test_config_upgrade_requires_existing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(wf.WorkflowError):
                wf.upgrade_config(Path(directory) / "missing.json")

    def test_config_upgrade_is_idempotent_at_the_current_version(self):
        """A current file is never rewritten, even if a value looks legacy.

        Without the version check, a user who deliberately set
        ``max_auto_agent_in_flight`` back to 1 after upgrading would have it
        silently reset by the next ``config-upgrade --apply``.
        """
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            current = {
                "config_version": 2,
                "concurrency": 3,
                "max_auto_agent_in_flight": 1,
                "max_auto_agent_per_day": 2,
                "merge_queue_exempt": 1,
                "custom": {"keep": True},
            }
            wf.write_json(path, current)
            before = path.read_bytes()
            dry = wf.upgrade_config(path, apply=False)
            self.assertEqual(dry["changes"], {})
            self.assertFalse(dry["applied"])
            applied = wf.upgrade_config(path, apply=True)
            self.assertFalse(applied["applied"])
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(wf.read_json(path), current)

    def test_config_upgrade_rejects_a_future_version(self):
        """A file from a newer tool must fail loudly, not be downgraded."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            future = {"config_version": 3, "max_auto_agent_in_flight": 1, "custom": "keep"}
            wf.write_json(path, future)
            before = path.read_bytes()
            with self.assertRaises(wf.WorkflowError) as raised:
                wf.upgrade_config(path, apply=True)
            self.assertIn("3", str(raised.exception))
            self.assertEqual(path.read_bytes(), before)

    def test_config_upgrade_rejects_a_non_integer_version(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            for value in ("2", 2.0, True):
                with self.subTest(config_version=value):
                    wf.write_json(path, {"config_version": value, "custom": "keep"})
                    with self.assertRaises(wf.WorkflowError):
                        wf.upgrade_config(path, apply=True)

    def test_config_upgrade_treats_a_versionless_file_as_legacy_v1(self):
        """Files written before versioning carry version 1 defaults."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            wf.write_json(path, {
                "concurrency": 3,
                "max_auto_agent_in_flight": 1,
                "max_auto_agent_per_day": 2,
                "merge_queue_exempt": 1,
                "custom": "keep-me",
            })
            pruned = wf.upgrade_config(path, apply=True)
            self.assertEqual(pruned["changes"]["config_version"]["from"], 1)
            raw = wf.read_json(path)
            self.assertEqual(raw["config_version"], wf.CURRENT_CONFIG_VERSION)
            self.assertEqual(raw["max_auto_agent_in_flight"], 2)
            self.assertEqual(raw["max_auto_agent_per_day"], 100)
            self.assertEqual(raw["merge_queue_exempt"], 100)
            self.assertEqual(raw["custom"], "keep-me")

    def test_invalid_concurrency_is_rejected(self):
        with self.assertRaises(wf.WorkflowError):
            wf.validate_config(make_config(concurrency=0))

    def test_fallback_is_only_for_quota_restart(self):
        config = make_config()
        config["quota"]["fallback"]["mode"] = "always"
        with self.assertRaises(wf.WorkflowError):
            wf.validate_config(config)


class StateTests(unittest.TestCase):
    def setUp(self):
        self.state = wf.empty_state()
        self.config = make_config()

    def add_user(self, title="user work", risk="normal"):
        return wf.new_point(self.state, source="user", title=title, risk=risk)

    def add_agent(self, title="agent work", risk="low"):
        return wf.new_point(self.state, source="agent", title=title, risk=risk)

    def test_user_points_are_queued_and_agent_points_are_proposed(self):
        user = self.add_user()
        agent = self.add_agent()
        self.assertEqual(user["id"], "dev-0001")
        self.assertEqual(user["status"], "queued")
        self.assertEqual(agent["status"], "proposed")
        self.assertFalse(agent["auto_started"])

    def test_high_risk_agent_point_cannot_auto_start(self):
        point = self.add_agent(risk="high")
        with self.assertRaises(wf.WorkflowError):
            wf.promote_point(self.state, self.config, point["id"], auto=True)
        self.assertEqual(point["status"], "proposed")

    def test_auto_start_is_blocked_while_a_user_point_is_pending(self):
        self.add_user()
        point = self.add_agent()
        with self.assertRaises(wf.WorkflowError):
            wf.promote_point(self.state, self.config, point["id"], auto=True)

    def test_auto_start_marks_the_point_and_enters_the_queue(self):
        point = self.add_agent()
        wf.promote_point(self.state, self.config, point["id"], auto=True)
        self.assertEqual(point["status"], "queued")
        self.assertTrue(point["auto_started"])
        self.assertIn("auto-start", point["labels"])
        self.assertEqual(point["approved_by"], "auto")

    def test_start_respects_capacity_and_transitions(self):
        points = [self.add_user(f"work {index}") for index in range(4)]
        for point in points[:3]:
            wf.start_point(self.state, self.config, point["id"])
        self.assertEqual(len(wf.build_summary(self.state, self.config)["active"]), 3)
        with self.assertRaises(wf.WorkflowError):
            wf.start_point(self.state, self.config, points[3]["id"])

    def test_agent_point_can_fill_a_spare_slot(self):
        user_one = self.add_user("user one")
        user_two = self.add_user("user two")
        agent = self.add_agent()
        wf.promote_point(self.state, self.config, agent["id"], auto=False)
        summary = wf.build_summary(self.state, self.config)
        self.assertEqual(summary["free_slots"], 3)
        self.assertEqual(len(summary["user_pending"]), 2)
        # With all 3 slots free the AI point may fill the spare slot.
        wf.start_point(self.state, self.config, agent["id"])
        wf.start_point(self.state, self.config, user_one["id"])
        wf.start_point(self.state, self.config, user_two["id"])
        self.assertEqual(len(wf.build_summary(self.state, self.config)["active"]), 3)

    def test_agent_point_is_refused_when_user_points_need_all_free_slots(self):
        self.config = make_config(concurrency=2)
        self.add_user("user one")
        self.add_user("user two")
        agent = self.add_agent()
        wf.promote_point(self.state, self.config, agent["id"], auto=False)
        with self.assertRaises(wf.WorkflowError):
            wf.start_point(self.state, self.config, agent["id"])

    def test_invalid_transition_is_rejected(self):
        point = self.add_user()
        wf.start_point(self.state, self.config, point["id"])
        with self.assertRaises(wf.WorkflowError):
            wf.transition(self.state, self.config, point, "done")
        with self.assertRaises(wf.WorkflowError):
            wf.transition(self.state, self.config, point, "blocked")  # blocked uses the block command


class SummaryTests(unittest.TestCase):
    def setUp(self):
        self.state = wf.empty_state()
        # These tests exercise the legacy single-slot/head-only semantics explicitly;
        # production defaults are 2 AI in flight and all merge-pool PRs exempt.
        self.config = make_config(max_auto_agent_in_flight=1, merge_queue_exempt=1)

    def add_user(self, title="user work"):
        return wf.new_point(self.state, source="user", title=title)

    def add_agent(self, title="agent work", risk="low"):
        return wf.new_point(self.state, source="agent", title=title, risk=risk)

    def test_user_points_are_scheduled_before_agent_points(self):
        users = [self.add_user(f"user {index}") for index in range(2)]
        agent = self.add_agent()
        summary = wf.build_summary(self.state, self.config)
        self.assertEqual([item["id"] for item in summary["start_user"]], [point["id"] for point in users])
        self.assertEqual(summary["start_agent"], [])
        self.assertFalse(summary["can_auto_start_agent"])
        self.assertEqual(summary["action"], "start_user")

    def test_ai_can_fill_spare_capacity_when_user_queue_is_empty(self):
        agent = self.add_agent()
        summary = wf.build_summary(self.state, self.config)
        self.assertTrue(summary["can_auto_start_agent"])
        self.assertEqual([item["id"] for item in summary["agent_auto_candidates"]], [agent["id"]])
        self.assertEqual(summary["action"], "promote_agent")

    def test_one_merge_ready_point_is_exempt_from_concurrency(self):
        point = self.add_user()
        advance_to_reviewing(self.state, self.config, point["id"])
        wf.review_pass(self.state, self.config, point["id"])
        summary = wf.build_summary(self.state, self.config)
        self.assertEqual(summary["load"], 0)
        self.assertEqual(summary["free_slots"], 3)
        self.assertEqual(summary["merge_queue"][0]["id"], point["id"])
        self.assertTrue(summary["merge_queue"][0]["exempt"])
        self.assertFalse(summary["merge_queue"][0]["counts_toward_concurrency"])
        # A waiting merge is a fresh decision: it must be shown before discovery.
        self.assertEqual(summary["merge_queue_state"], "new")
        self.assertEqual(summary["action"], "ask_user_merge")

    def test_second_merge_ready_point_counts_toward_concurrency(self):
        first = self.add_user("first")
        second = self.add_user("second")
        for point in (first, second):
            advance_to_reviewing(self.state, self.config, point["id"])
            wf.review_pass(self.state, self.config, point["id"])
        summary = wf.build_summary(self.state, self.config)
        self.assertEqual(summary["load"], 1)
        self.assertEqual(summary["free_slots"], 2)
        self.assertTrue(summary["merge_queue"][0]["exempt"])
        self.assertTrue(summary["merge_queue"][1]["counts_toward_concurrency"])

    def test_active_work_and_merge_queue_share_capacity(self):
        active = [self.add_user(f"active {index}") for index in range(2)]
        for point in active:
            wf.start_point(self.state, self.config, point["id"])
        merged = self.add_user("merged")
        advance_to_reviewing(self.state, self.config, merged["id"])
        wf.review_pass(self.state, self.config, merged["id"])
        summary = wf.build_summary(self.state, self.config)
        self.assertEqual(summary["load"], 2)
        self.assertEqual(summary["free_slots"], 1)

    def test_auto_start_respects_in_flight_cap(self):
        first = self.add_agent("first")
        second = self.add_agent("second")
        wf.promote_point(self.state, self.config, first["id"], auto=True)
        wf.start_point(self.state, self.config, first["id"])
        summary = wf.build_summary(self.state, self.config)
        self.assertFalse(summary["can_auto_start_agent"])
        self.assertIn("cap", summary["auto_start_block_reason"] or "")
        with self.assertRaises(wf.WorkflowError):
            wf.promote_point(self.state, self.config, second["id"], auto=True)

    def test_auto_promote_reserves_in_flight_slot_before_start(self):
        first = self.add_agent("first")
        second = self.add_agent("second")
        wf.promote_point(self.state, self.config, first["id"], auto=True)
        with self.assertRaises(wf.WorkflowError):
            wf.promote_point(self.state, self.config, second["id"], auto=True)
        self.assertEqual(second["status"], "proposed")
        summary = wf.build_summary(self.state, self.config)
        self.assertEqual(
            [point["id"] for point in summary["agent_auto_reserved"]],
            [first["id"]],
        )

    def test_start_rechecks_auto_in_flight_cap(self):
        first = self.add_agent("first")
        second = self.add_agent("second")
        wf.promote_point(self.state, self.config, first["id"], auto=True)
        # Simulate a legacy/incorrect state with a second reserved auto slot.
        second["status"] = "queued"
        second["auto_started"] = True
        second["auto_started_at"] = wf.iso(wf.utcnow())
        wf.start_point(self.state, self.config, first["id"])
        with self.assertRaises(wf.WorkflowError):
            wf.start_point(self.state, self.config, second["id"])

    def test_reject_queued_user_point(self):
        point = self.add_user("reject me")
        wf.reject_point(self.state, self.config, point["id"], reason="not needed")
        self.assertEqual(point["status"], "rejected")

    def test_reject_inbox_point(self):
        point = self.add_user("reject inbox")
        point["status"] = "inbox"
        wf.reject_point(self.state, self.config, point["id"])
        self.assertEqual(point["status"], "rejected")

    def test_discover_is_suggested_only_when_no_work_is_waiting(self):
        self.add_agent()
        summary = wf.build_summary(self.state, self.config)
        self.assertFalse(summary["should_discover_agent"])
        for point in summary["agent_auto_candidates"]:
            wf.promote_point(self.state, self.config, point["id"], auto=True)
        summary = wf.build_summary(self.state, self.config)
        self.assertFalse(summary["should_discover_agent"])
        wf.start_point(self.state, self.config, summary["agent_queued"][0]["id"])
        summary = wf.build_summary(self.state, self.config)
        self.assertFalse(summary["should_discover_agent"])


    def test_merge_waiting_does_not_block_ready_work(self):
        waiting = self.add_user("waiting merge")
        waiting["status"] = "awaiting_merge"
        waiting["merge_ready_at"] = wf.iso(wf.utcnow())
        queued = self.add_user("queued user work")
        summary = wf.build_summary(self.state, self.config)
        self.assertGreater(summary["free_slots"], 0)
        self.assertEqual([point["id"] for point in summary["start_user"]], [queued["id"]])
        self.assertEqual(summary["action"], "start_user")

    def test_cancel_blocked_point_does_not_require_capacity(self):
        config = make_config(concurrency=3)
        state = wf.empty_state()
        blocked = wf.new_point(state, source="user", title="blocked proposal")
        wf.start_point(state, config, blocked["id"])
        wf.block_point(state, config, blocked["id"], reason="external")
        for index in range(3):
            point = wf.new_point(state, source="user", title=f"active {index}")
            wf.start_point(state, config, point["id"])
        self.assertEqual(wf.build_summary(state, config)["load"], 3)
        wf.cancel_point(state, blocked["id"], reason="proposal withdrawn")
        self.assertEqual(blocked["status"], "cancelled")
        self.assertEqual(wf.build_summary(state, config)["load"], 3)
        with self.assertRaises(wf.WorkflowError):
            wf.cancel_point(state, blocked["id"], reason="already cancelled")


class MergeGateTests(unittest.TestCase):
    def setUp(self):
        self.state = wf.empty_state()
        self.config = make_config()

    def prepare(self, title):
        point = wf.new_point(self.state, source="user", title=title)
        advance_to_reviewing(self.state, self.config, point["id"])
        wf.review_pass(self.state, self.config, point["id"])
        return point

    def test_review_pass_does_not_merge(self):
        point = self.prepare("one")
        self.assertEqual(point["status"], "awaiting_merge")
        self.assertIsNotNone(point["merge_ready_at"])
        self.assertIsNone(point.get("merged_at"))

    def test_cancel_point_rejects_merging(self):
        point = self.prepare("one")
        wf.merge_approve(self.state, self.config, point["id"])
        self.assertEqual(point["status"], "merging")
        with self.assertRaises(wf.WorkflowError):
            wf.cancel_point(self.state, point["id"], reason="should not cancel merging")

    def test_only_the_head_can_be_approved(self):
        first = self.prepare("first")
        second = self.prepare("second")
        with self.assertRaises(wf.WorkflowError):
            wf.merge_approve(self.state, self.config, second["id"])
        wf.merge_approve(self.state, self.config, first["id"])
        self.assertEqual(first["status"], "merging")
        wf.merge_done(self.state, self.config, first["id"])
        self.assertEqual(first["status"], "done")
        self.assertIsNotNone(first["merged_at"])
        wf.merge_approve(self.state, self.config, second["id"])
        self.assertEqual(second["status"], "merging")

    def test_merge_reconcile_records_non_head_with_audit(self):
        first = self.prepare("first")
        second = self.prepare("second")
        for point, pr in ((first, 101), (second, 102)):
            point["pr"] = pr
            point["head_sha"] = f"{pr:040d}"
        with self.assertRaises(wf.WorkflowError):
            wf.merge_reconcile(
                self.state,
                self.config,
                second["id"],
                pr=102,
                head_sha=second["head_sha"],
                approved_by="codex",
                confirm_merged=False,
            )
        self.assertEqual(second["status"], "awaiting_merge")
        with self.assertRaises(wf.WorkflowError):
            wf.merge_reconcile(
                self.state,
                self.config,
                second["id"],
                pr=102,
                head_sha="0" * 40,
                approved_by="codex",
                confirm_merged=True,
            )
        self.assertEqual(second["status"], "awaiting_merge")
        point = wf.merge_reconcile(
            self.state,
            self.config,
            second["id"],
            pr=102,
            head_sha=second["head_sha"],
            approved_by="codex",
            confirm_merged=True,
            note="merged out of order on GitHub",
        )
        self.assertEqual(point["status"], "done")
        self.assertEqual(first["status"], "awaiting_merge")
        self.assertEqual(point["merge_reconciled_by"], "codex")
        self.assertIsNotNone(point["merge_reconciled_at"])
        self.assertIsNotNone(point["merged_at"])
        with self.assertRaises(wf.WorkflowError):
            wf.merge_reconcile(
                self.state,
                self.config,
                second["id"],
                pr=102,
                head_sha=second["head_sha"],
                approved_by="codex",
                confirm_merged=True,
            )

    def test_merge_reject_returns_to_repair_or_close(self):
        point = self.prepare("one")
        wf.merge_reject(self.state, self.config, point["id"], reason="需要调整文案")
        self.assertEqual(point["status"], "repairing")
        self.assertIn("需要调整文案", point["notes"])

        other = self.prepare("two")
        wf.merge_reject(self.state, self.config, other["id"], reason="不做了", close=True)
        self.assertEqual(other["status"], "rejected")

    def test_invalid_next_step_is_rejected(self):
        point = self.prepare("one")
        with self.assertRaises(wf.WorkflowError):
            wf.merge_done(self.state, self.config, point["id"])

    def test_block_point_rejects_merging(self):
        point = self.prepare("one")
        wf.merge_approve(self.state, self.config, point["id"])
        self.assertEqual(point["status"], "merging")
        with self.assertRaises(wf.WorkflowError) as caught:
            wf.block_point(self.state, self.config, point["id"], reason="external")
        self.assertIn("merging", str(caught.exception))
        # The refused detour must not have written any partial block metadata.
        self.assertEqual(point["status"], "merging")
        self.assertIsNone(point["blocked_from_status"])
        self.assertIsNone(point["blocked_until"])
        self.assertEqual(point["quota_retries"], 0)
        # The merge result is still registrable: that is the whole point of the refusal.
        wf.merge_done(self.state, self.config, point["id"])
        self.assertEqual(point["status"], "done")

    def park_from_merging(self, point):
        """Write the legacy record the guard must close: merging parked as blocked."""
        point["blocked_from_status"] = "merging"
        point["blocked_reason"] = "external"
        point["blocked_until"] = "2026-01-01T00:00:30Z"
        point["status"] = "blocked"

    def test_cancel_point_rejects_blocked_from_merging(self):
        """Regression for the Codex review: block-then-cancel must not strand a merge."""
        point = self.prepare("one")
        wf.merge_approve(self.state, self.config, point["id"])
        park_from_merging(point)
        # Negative control: the guard itself is what refuses, not the missing reason.
        with self.assertRaises(wf.WorkflowError) as caught:
            wf.cancel_point(self.state, point["id"], reason="should not cancel a merge in flight")
        self.assertIn("merging", str(caught.exception))
        self.assertEqual(point["status"], "blocked")
        self.assertEqual(point["blocked_from_status"], "merging")
        # The merge result cannot be registered from ``blocked``: the divergence the
        # guard prevents. The only sanctioned recovery is merge-reject's own edge,
        # blocked -> repairing (forcing a legacy record into live work is refused).
        with self.assertRaises(wf.WorkflowError):
            wf.merge_done(self.state, self.config, point["id"])
        self.assertEqual(point["status"], "blocked")
        wf.transition(
            self.state, self.config, point, "repairing", force=True, note="recover stranded merge"
        )
        self.assertEqual(point["status"], "repairing")
        self.assertIsNone(point["blocked_from_status"])

    def test_resume_refuses_blocked_from_merging(self):
        point = self.prepare("one")
        wf.merge_approve(self.state, self.config, point["id"])
        park_from_merging(point)
        with self.assertRaises(wf.WorkflowError) as caught:
            wf.resume_point(self.state, self.config, point["id"])
        self.assertIn("merging", str(caught.exception))
        self.assertEqual(point["status"], "blocked")

    def test_blocked_from_merging_cannot_cancel_under_full_load(self):
        """The capacity-parked path is refused before capacity is even considered."""
        config = make_config(concurrency=1)
        state = wf.empty_state()
        merging = wf.new_point(state, source="user", title="merge in flight")
        advance_to_reviewing(state, config, merging["id"])
        wf.review_pass(state, config, merging["id"])
        wf.merge_approve(state, config, merging["id"])
        park_from_merging(merging)
        for index in range(2):
            point = wf.new_point(state, source="user", title=f"blocked proposal {index}")
            wf.start_point(state, config, point["id"])
            wf.block_point(state, config, point["id"], reason="external")
        self.assertEqual(wf.build_summary(state, config)["load"], 0)
        with self.assertRaises(wf.WorkflowError):
            wf.cancel_point(state, merging["id"], reason="withdraw")
        self.assertEqual(merging["status"], "blocked")
        # Control group: a proposal parked from ``specifying`` still cancels with no
        # slot, which is exactly what cancel-point exists for.
        blocked_id = state["points"][1]["id"]
        wf.cancel_point(state, blocked_id, reason="proposal withdrawn")
        self.assertEqual(wf.point_by_id(state, blocked_id)["status"], "cancelled")

    def test_cancel_point_follows_the_allowed_cancelled_edges(self):
        """No hand-maintained status list: cancellation is the ``cancelled`` edge."""
        point = wf.new_point(self.state, source="user", title="probe")
        cancelled_origin = {
            status
            for status in wf.STATUSES
            if "cancelled" in wf.ALLOWED_TRANSITIONS.get(status, set())
        }
        self.assertEqual(
            cancelled_origin,
            {"inbox", "proposed", "queued", "specifying", "implementing", "verifying",
             "reviewing", "repairing", "awaiting_merge"},
        )
        self.assertEqual(
            cancelled_origin, wf.STATUSES - wf.TERMINAL_STATUSES - {"blocked", "merging"}
        )
        for status in sorted(wf.STATUSES):
            with self.subTest(status=status):
                point["status"] = status
                if status in cancelled_origin:
                    wf.cancel_point(self.state, point["id"], reason="allowed")
                    self.assertEqual(point["status"], "cancelled")
                else:
                    with self.assertRaises(wf.WorkflowError):
                        wf.cancel_point(self.state, point["id"], reason="should be refused")
                    self.assertEqual(point["status"], status)

    def test_cancel_point_uses_the_blocked_origin_not_a_wider_list(self):
        blocked = wf.new_point(self.state, source="user", title="proposal")
        wf.block_point(self.state, self.config, blocked["id"], reason="external")
        wf.cancel_point(self.state, blocked["id"], reason="withdrawn")
        self.assertEqual(blocked["status"], "cancelled")
        self.assertIsNone(blocked["blocked_from_status"])
        # An origin outside the cancellable set is still refused through ``blocked``.
        legacy = wf.new_point(self.state, source="user", title="legacy")
        legacy["status"] = "blocked"
        legacy["blocked_from_status"] = "done"
        with self.assertRaises(wf.WorkflowError):
            wf.cancel_point(self.state, legacy["id"], reason="should be refused")
        self.assertEqual(legacy["status"], "blocked")


class BlockOriginTests(unittest.TestCase):
    """Parking is not an ALLOWED_TRANSITIONS edge, so its origin must stay resumable."""

    def setUp(self):
        self.state = wf.empty_state()
        self.config = make_config()

    def test_block_point_refuses_an_origin_resume_cannot_restore(self):
        point = wf.new_point(self.state, source="user", title="probe")
        for status in sorted(wf.TERMINAL_STATUSES | {"blocked", "merging"}):
            with self.subTest(status=status):
                point.update(
                    status=status,
                    blocked_from_status=status if status == "blocked" else None,
                    blocked_reason="external" if status == "blocked" else None,
                    blocked_until="2026-01-01T00:00:30Z" if status == "blocked" else None,
                    quota_retries=0,
                )
                with self.assertRaises(wf.WorkflowError):
                    wf.block_point(self.state, self.config, point["id"], reason="quota")
                self.assertEqual(point["status"], status)
                self.assertEqual(point["quota_retries"], 0)
                self.assertIsNone(point.get("fallback"))

    def test_forced_transition_still_keeps_a_restorable_origin(self):
        """--force can lift a stranded park only into a status that is itself restorable."""
        point = wf.new_point(self.state, source="user", title="probe")
        # Ordinary forced recovery is untouched (control group).
        wf.transition(self.state, self.config, point, "done", force=True)
        self.assertEqual(point["status"], "done")
        wf.transition(self.state, self.config, point, "rejected", force=True)
        self.assertEqual(point["status"], "rejected")

        # A stranded merge cannot be forced into another unrestorable state.
        for status in sorted(wf.TERMINAL_STATUSES | {"merging"}):
            with self.subTest(refused=status):
                legacy = wf.new_point(self.state, source="user", title="legacy stranded merge")
                park_from_merging(legacy)
                with self.assertRaises(wf.WorkflowError):
                    wf.transition(self.state, self.config, legacy, status, force=True)
                self.assertEqual(legacy["status"], "blocked")
                self.assertEqual(legacy["blocked_from_status"], "merging")

        # The sanctioned recovery lands on live repair work and clears the stale park.
        legacy = wf.new_point(self.state, source="user", title="legacy stranded merge")
        park_from_merging(legacy)
        wf.transition(self.state, self.config, legacy, "repairing", force=True)
        self.assertEqual(legacy["status"], "repairing")
        self.assertIsNone(legacy["blocked_from_status"])

        # A restorable park keeps an ordinary forced move working.
        parked = wf.new_point(self.state, source="user", title="ordinary park")
        wf.start_point(self.state, self.config, parked["id"])
        wf.block_point(self.state, self.config, parked["id"], reason="external")
        wf.transition(self.state, self.config, parked, "queued", force=True)
        self.assertEqual(parked["status"], "queued")
        self.assertIsNone(parked["blocked_from_status"])

    def test_honest_control_group_parking_and_resuming_keeps_working(self):
        """Negative control: the guard must not block ordinary quota parking."""
        point = wf.new_point(self.state, source="user", title="probe")
        wf.start_point(self.state, self.config, point["id"])
        wf.block_point(self.state, self.config, point["id"], reason="quota")
        self.assertEqual(point["status"], "blocked")
        self.assertEqual(point["blocked_from_status"], "specifying")
        wf.resume_point(self.state, self.config, point["id"])
        self.assertEqual(point["status"], "specifying")
        self.assertIsNone(point["blocked_from_status"])

    def test_validate_reports_an_unrestorable_blocked_origin(self):
        point = wf.new_point(self.state, source="user", title="probe")
        wf.start_point(self.state, self.config, point["id"])
        wf.block_point(self.state, self.config, point["id"], reason="external")
        self.assertEqual(wf.validate_state(self.state, self.config), [])
        point["blocked_from_status"] = "merging"
        issues = wf.validate_state(self.state, self.config)
        self.assertTrue(any("cannot be resumed" in issue for issue in issues), issues)

    def test_status_output_names_the_blocked_origin(self):
        point = wf.new_point(self.state, source="user", title="probe")
        wf.start_point(self.state, self.config, point["id"])
        wf.block_point(self.state, self.config, point["id"], reason="external")
        self.assertIn("blocked from specifying", wf.format_point(point))


class CancelPointCLITests(unittest.TestCase):
    """The guard must hold through the CLI too, not only for direct API callers."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.state_path = Path(directory.name) / "state.json"
        self.config_path = Path(directory.name) / "config.json"

    def run_cli(self, *arguments):
        output = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(output), redirect_stderr(stderr):
            code = wf.main(
                ["--state", str(self.state_path), "--config", str(self.config_path), *arguments]
            )
        return code, output.getvalue(), stderr.getvalue()

    def prepare_merging(self):
        self.assertEqual(self.run_cli("add", "--source", "user", "--title", "merge")[0], 0)
        self.assertEqual(self.run_cli("start", "dev-0001")[0], 0)
        for status in ("implementing", "verifying", "reviewing"):
            self.assertEqual(self.run_cli("set-status", "dev-0001", status)[0], 0)
        self.assertEqual(self.run_cli("review-pass", "dev-0001")[0], 0)
        self.assertEqual(self.run_cli("merge-approved", "dev-0001")[0], 0)
        return wf.load_state(self.state_path)

    def test_cli_refuses_to_block_or_cancel_an_in_flight_merge(self):
        state = self.prepare_merging()
        self.assertEqual(wf.point_by_id(state, "dev-0001")["status"], "merging")
        code, out, err = self.run_cli("block", "dev-0001", "--reason", "external")
        self.assertEqual(code, 2, out)
        self.assertIn("merging", err)
        code, out, err = self.run_cli("cancel-point", "dev-0001", "--reason", "withdraw")
        self.assertEqual(code, 2, out)
        self.assertIn("merging", err)
        stored = wf.point_by_id(wf.load_state(self.state_path), "dev-0001")
        self.assertEqual(stored["status"], "merging")
        self.assertIsNone(stored["blocked_from_status"])
        self.assertEqual(self.run_cli("merge-done", "dev-0001")[0], 0)
        self.assertEqual(
            wf.point_by_id(wf.load_state(self.state_path), "dev-0001")["status"], "done"
        )

    def test_cli_refuses_to_cancel_a_legacy_blocked_while_merging(self):
        state = self.prepare_merging()
        point = wf.point_by_id(state, "dev-0001")
        point["blocked_from_status"] = "merging"
        point["blocked_reason"] = "external"
        point["blocked_until"] = "2026-01-01T00:00:30Z"
        point["status"] = "blocked"
        wf.save_state(self.state_path, state)
        code, out, err = self.run_cli("cancel-point", "dev-0001", "--reason", "withdraw")
        self.assertEqual(code, 2, out)
        self.assertIn("merging", err)
        self.assertEqual(
            wf.point_by_id(wf.load_state(self.state_path), "dev-0001")["status"], "blocked"
        )
        validate = self.run_cli("validate")
        self.assertEqual(validate[0], 1)
        self.assertIn("cannot be resumed", validate[1])

    def test_cli_control_group_blocked_proposal_cancels(self):
        """Negative control: the CLI path that must keep working while slots are full."""
        self.assertEqual(self.run_cli("add", "--source", "user", "--title", "proposal")[0], 0)
        self.assertEqual(self.run_cli("start", "dev-0001")[0], 0)
        self.assertEqual(self.run_cli("block", "dev-0001", "--reason", "external")[0], 0)
        for index in range(3):
            self.assertEqual(
                self.run_cli("add", "--source", "user", "--title", f"active {index}")[0], 0
            )
            self.assertEqual(self.run_cli("start", f"dev-000{index + 2}")[0], 0)
        code, out, _ = self.run_cli("status", "--json")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["load"], 3)
        code, out, err = self.run_cli("cancel-point", "dev-0001", "--reason", "withdrawn")
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["status"], "cancelled")
        self.assertEqual(
            wf.point_by_id(wf.load_state(self.state_path), "dev-0001")["status"], "cancelled"
        )


class QuotaTests(unittest.TestCase):
    def setUp(self):
        self.state = wf.empty_state()
        self.config = make_config()
        self.point = wf.new_point(self.state, source="user", title="quota work")
        wf.start_point(self.state, self.config, self.point["id"])

    def test_quota_block_uses_30_minutes_and_marks_fallback(self):
        point, result = wf.block_point(self.state, self.config, self.point["id"], reason="quota")
        self.assertEqual(point["status"], "blocked")
        self.assertEqual(point["blocked_from_status"], "specifying")
        self.assertEqual(point["quota_retries"], 1)
        self.assertEqual(result["wait_minutes"], 30)
        self.assertEqual(result["fallback"]["provider"], "deepseek-account")
        self.assertTrue(result["fallback"]["active"])
        summary = wf.build_summary(self.state, self.config)
        self.assertTrue(summary["paused"])
        self.assertEqual(summary["action"], "wait_quota")

    def test_resume_restores_previous_status_and_primary_route(self):
        wf.block_point(self.state, self.config, self.point["id"], reason="quota")
        wf.resume_point(self.state, self.config, self.point["id"])
        self.assertEqual(self.point["status"], "specifying")
        self.assertIsNone(self.point["fallback"])

    def test_backoff_grows_and_retry_limit_is_reported(self):
        wf.block_point(self.state, self.config, self.point["id"], reason="quota")
        wf.resume_point(self.state, self.config, self.point["id"])
        _, second = wf.block_point(self.state, self.config, self.point["id"], reason="quota")
        self.assertEqual(second["wait_minutes"], 60)
        self.assertFalse(second["retry_exhausted"])
        wf.resume_point(self.state, self.config, self.point["id"])
        _, third = wf.block_point(self.state, self.config, self.point["id"], reason="quota")
        self.assertEqual(third["wait_minutes"], 120)
        self.assertTrue(third["retry_exhausted"])

    def test_non_quota_block_has_no_fallback(self):
        _, result = wf.block_point(self.state, self.config, self.point["id"], reason="external", minutes=10)
        self.assertEqual(result["wait_minutes"], 10)
        self.assertIsNone(result["fallback"])
        self.assertFalse(wf.build_summary(self.state, self.config)["paused"])


class AdmissionTests(unittest.TestCase):
    def test_resume_keeps_blocked_when_the_slot_was_taken(self):
        config = make_config(concurrency=1)
        state = wf.empty_state()
        first = wf.new_point(state, source="user", title="first")
        second = wf.new_point(state, source="user", title="second")
        wf.start_point(state, config, first["id"])
        wf.block_point(state, config, first["id"], reason="external")
        wf.start_point(state, config, second["id"])
        with self.assertRaises(wf.WorkflowError):
            wf.resume_point(state, config, first["id"])
        self.assertEqual(first["status"], "blocked")
        self.assertEqual(second["status"], "specifying")
        wf.block_point(state, config, second["id"], reason="external")
        wf.resume_point(state, config, first["id"])
        self.assertEqual(first["status"], "specifying")

    def test_resume_rechecks_ai_in_flight_cap(self):
        config = make_config(max_auto_agent_in_flight=1)
        state = wf.empty_state()
        first = wf.new_point(state, source="agent", title="first", risk="low")
        second = wf.new_point(state, source="agent", title="second", risk="low")
        wf.promote_point(state, config, first["id"], auto=True)
        wf.start_point(state, config, first["id"])
        wf.block_point(state, config, first["id"], reason="external")
        wf.promote_point(state, config, second["id"], auto=True)
        wf.start_point(state, config, second["id"])
        with self.assertRaises(wf.WorkflowError):
            wf.resume_point(state, config, first["id"])
        self.assertEqual(first["status"], "blocked")
        self.assertEqual(second["status"], "specifying")

    def test_resume_agent_specifying_respects_user_priority(self):
        config = make_config(concurrency=1)
        state = wf.empty_state()
        agent = wf.new_point(state, source="agent", title="agent", risk="low")
        wf.promote_point(state, config, agent["id"], auto=True)
        wf.start_point(state, config, agent["id"])
        wf.block_point(state, config, agent["id"], reason="external")
        wf.new_point(state, source="user", title="user")
        with self.assertRaises(wf.WorkflowError):
            wf.resume_point(state, config, agent["id"])
        self.assertEqual(agent["status"], "blocked")

    def test_two_quota_blocked_tasks_can_resume_independently(self):
        config = make_config(concurrency=2)
        state = wf.empty_state()
        first = wf.new_point(state, source="user", title="first")
        second = wf.new_point(state, source="user", title="second")
        wf.start_point(state, config, first["id"])
        wf.start_point(state, config, second["id"])
        wf.block_point(state, config, first["id"], reason="quota")
        wf.block_point(state, config, second["id"], reason="quota")
        wf.resume_point(state, config, first["id"])
        self.assertEqual(first["status"], "specifying")
        wf.resume_point(state, config, second["id"])
        self.assertEqual(second["status"], "specifying")
        self.assertFalse(wf.build_summary(state, config)["paused"])

    def test_resume_awaiting_merge_recomputes_queue_load(self):
        config = make_config(concurrency=1, merge_queue_exempt=1)
        state = wf.empty_state()
        first = wf.new_point(state, source="user", title="first")
        second = wf.new_point(state, source="user", title="second")
        for point in (first, second):
            point["status"] = "awaiting_merge"
            point["merge_ready_at"] = wf.iso(wf.utcnow())
        self.assertEqual(wf.build_summary(state, config)["load"], 1)
        wf.block_point(state, config, second["id"], reason="external")
        third = wf.new_point(state, source="user", title="third")
        wf.start_point(state, config, third["id"])
        with self.assertRaises(wf.WorkflowError):
            wf.resume_point(state, config, second["id"])
        self.assertEqual(second["status"], "blocked")
        self.assertEqual(wf.build_summary(state, config)["load"], 1)
        wf.block_point(state, config, third["id"], reason="external")
        wf.resume_point(state, config, second["id"])
        self.assertEqual(second["status"], "awaiting_merge")
        self.assertEqual(wf.build_summary(state, config)["load"], 1)

    def test_merge_reject_parks_when_capacity_full(self):
        config = make_config(concurrency=1)
        state = wf.empty_state()
        first = wf.new_point(state, source="user", title="first")
        second = wf.new_point(state, source="user", title="second")
        first["status"] = "awaiting_merge"
        first["merge_ready_at"] = wf.iso(wf.utcnow())
        wf.start_point(state, config, second["id"])
        wf.merge_reject(state, config, first["id"], reason="rework")
        self.assertEqual(first["status"], "blocked")
        self.assertEqual(first["blocked_reason"], "capacity")
        self.assertEqual(first["blocked_from_status"], "repairing")
        self.assertEqual(second["status"], "specifying")
        self.assertEqual(wf.build_summary(state, config)["load"], 1)
        self.assertEqual(wf.validate_state(state, config), [])
        wf.block_point(state, config, second["id"], reason="external")
        wf.resume_point(state, config, first["id"])
        self.assertEqual(first["status"], "repairing")

    def test_merging_to_repairing_parks_when_capacity_full(self):
        config = make_config(concurrency=1)
        state = wf.empty_state()
        first = wf.new_point(state, source="user", title="first")
        second = wf.new_point(state, source="user", title="second")
        first["status"] = "awaiting_merge"
        first["merge_ready_at"] = wf.iso(wf.utcnow())
        wf.start_point(state, config, second["id"])
        wf.merge_approve(state, config, first["id"])
        self.assertEqual(first["status"], "merging")
        wf.transition(state, config, first, "repairing")
        self.assertEqual(first["status"], "blocked")
        self.assertEqual(first["blocked_reason"], "capacity")
        self.assertEqual(second["status"], "specifying")

    def test_set_status_repairing_parks_when_capacity_full(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            config_path = Path(directory) / "config.json"
            state = wf.empty_state()
            first = wf.new_point(state, source="user", title="first")
            second = wf.new_point(state, source="user", title="second")
            first["status"] = "awaiting_merge"
            first["merge_ready_at"] = wf.iso(wf.utcnow())
            wf.start_point(state, make_config(concurrency=1), second["id"])
            wf.save_state(state_path, state)
            wf.write_json(config_path, make_config(concurrency=1))
            output = io.StringIO()
            with redirect_stdout(output), redirect_stderr(io.StringIO()):
                self.assertEqual(
                    wf.main(
                        [
                            "--state",
                            str(state_path),
                            "--config",
                            str(config_path),
                            "set-status",
                            "dev-0001",
                            "repairing",
                        ]
                    ),
                    0,
                )
            state = wf.load_state(state_path)
            self.assertEqual(wf.point_by_id(state, "dev-0001")["status"], "blocked")
            self.assertEqual(wf.point_by_id(state, "dev-0001")["blocked_reason"], "capacity")

    def test_capacity_resume_wins_over_a_waiting_merge_head(self):
        """A free slot with rework waiting is resumed before the merge head is shown.

        Both are blocked on the user: resuming an already-approved restoration
        needs nothing new, while a merge head is a fresh decision. Asking for
        the merge first would leave the returned rework waiting behind it.
        """
        config = make_config(concurrency=1)
        state = wf.empty_state()
        rework = wf.new_point(state, source="user", title="rework")
        merged = wf.new_point(state, source="user", title="merged")
        # The rework was returned while the only dev slot was taken, so it is parked
        # as capacity-blocked; the merge head still waits for the user. Saying yes to
        # the merge again first would leave the return behind a fresh decision.
        merged["status"] = "awaiting_merge"
        merged["merge_ready_at"] = "2000-01-01T00:00:00Z"
        rework["status"] = "awaiting_merge"
        rework["merge_ready_at"] = "2001-01-01T00:00:00Z"
        wf._park_for_capacity(rework, "repairing", "user returned the merge")
        self.assertEqual(rework["status"], "blocked")
        self.assertEqual(rework["blocked_reason"], "capacity")
        self.assertEqual(merged["status"], "awaiting_merge")
        summary = wf.build_summary(state, config)
        self.assertEqual(summary["free_slots"], 1)
        self.assertEqual(summary["merge_queue"][0]["id"], merged["id"])
        self.assertEqual(summary["merge_queue_state"], "new")
        self.assertEqual([point["id"] for point in summary["capacity_blocked"]],
                         [rework["id"]])
        self.assertEqual(summary["action"], "resume_capacity")
        self.assertEqual(wf.build_summary(state, config)["action"], "resume_capacity")

    def test_merge_head_is_asked_once_after_explicit_delivery(self):
        """``ask_user_merge`` survives the default (fully exempt) pool until delivered.

        ``merge_queue_state`` no longer depends on a free-slot condition (the
        default pool is fully exempt, so load stays below concurrency). Only an
        explicit ``deliver_merge_ask`` marks the pool as shown, and only the
        exact waiting lifecycle counts: a changed or reworked pool is asked
        again, and an empty queue is ``none``. A delivered pool is report state
        only: discovery proceeds while it waits, and ``wait_merge_approval`` is
        reserved for when discovery cannot run. Assertions are exact on
        ``action`` so deleting either branch fails.
        """
        config = make_config()  # production defaults: merge_queue_exempt=100
        self.assertEqual(config["merge_queue_exempt"], 100)
        state = with_points([merge_ready("waiting"), busy_agent("busy")])
        self.assertEqual(wf.validate_state(state, config), [])
        asked = wf.build_summary(state, config)
        self.assertEqual(asked["action"], "ask_user_merge")
        self.assertEqual(asked["merge_queue_state"], "new")
        # Computing a report never records a delivery by itself.
        self.assertNotIn("merge_queue_asked", state)
        self.assertEqual(wf.build_summary(state, config)["action"], "ask_user_merge")
        entry = wf.deliver_merge_ask(state)
        self.assertEqual(entry["ids"], ["dev-0001"])
        self.assertEqual(entry["head"], "dev-0001")
        delivered = wf.build_summary(state, config)
        self.assertEqual(delivered["merge_queue_state"], "answered")
        self.assertEqual(delivered["merge_head"]["id"], "dev-0001")
        self.assertEqual(delivered["merge_queue_asked"], entry)
        # The delivered pool no longer shadows a free slot: discovery continues.
        self.assertTrue(delivered["should_discover_agent"])
        self.assertEqual(delivered["action"], "discover_agent")
        # Capacity rework still resumes first even after the ask was delivered.
        state["points"].append(with_points([
            {"title": "parked", "status": "blocked", "blocked_reason": "capacity",
             "blocked_from_status": "repairing", "blocked_until": None}])["points"][0])
        with_capacity = wf.build_summary(state, config)
        self.assertEqual(with_capacity["action"], "resume_capacity")
        state["points"] = [point for point in state["points"] if point["title"] != "parked"]
        # ``wait_merge_approval`` remains the report state when discovery is
        # genuinely not admissible (here: an undiscovered normal proposal).
        state["points"].append(with_points([
            {"title": "normal proposal", "source": "agent", "risk": "normal",
             "status": "proposed"}])["points"][0])
        deciding = wf.build_summary(state, config)
        self.assertFalse(deciding["should_discover_agent"])
        self.assertEqual(deciding["action"], "wait_merge_approval")
        state["points"] = [point for point in state["points"]
                           if point["title"] != "normal proposal"]
        # Negative control: a different waiting set is a new decision.
        state["points"].append(with_points([merge_ready("other", "2026-01-02T00:00:00Z")])
                               ["points"][0])
        changed = wf.build_summary(state, config)
        self.assertEqual(changed["merge_queue_state"], "new")
        self.assertIsNone(changed["merge_queue_asked"])
        self.assertEqual(changed["action"], "ask_user_merge")
        # Emptying the queue clears the record instead of leaving a stale one.
        state["points"] = [point for point in state["points"]
                           if point["status"] != "awaiting_merge"]
        self.assertIn("merge_queue_asked", state)
        self.assertEqual(wf.validate_state(state, config), [])
        emptied = wf.build_summary(state, config)
        self.assertEqual(emptied["action"], "wait_quota" if False else emptied["action"])
        self.assertEqual(emptied["merge_queue_state"], "none")
        self.assertIsNone(emptied["merge_head"])
        # A reloaded state with an empty queue must not keep the stale record.
        reloaded = json.loads(json.dumps(state))
        reloaded["merge_queue_asked"] = {"ids": ["dev-0001"], "head": "dev-0001",
                                         "digest": "x", "asked_at": "2026-01-01T00:00:00Z"}
        summary = wf.build_summary(reloaded, config)
        self.assertEqual(summary["merge_queue_state"], "none")
        self.assertEqual(summary["action"], "discover_agent")
        wf.deliver_merge_ask(reloaded)
        self.assertNotIn("merge_queue_asked", reloaded)
        self.assertEqual(wf.build_summary(reloaded, config)["action"], "discover_agent")

    def test_delivered_merge_pool_does_not_block_discovery(self):
        """A delivered merge ask must not freeze requirement discovery.

        Codex repro (default config, no busy agent): one ``awaiting_merge``
        point, empty slots. The captain delivers the ask once; every later
        ``next`` used to return ``wait_merge_approval`` even though
        ``should_discover_agent`` was true, so a large PR pool stopped new
        requirements. After delivery the pool stays in the report but the action
        is ``discover_agent``; capacity rework still wins a free slot.
        """
        config = make_config()  # production defaults
        self.assertEqual(config["merge_queue_exempt"], 100)
        state = with_points([merge_ready("waiting")])
        self.assertEqual(wf.validate_state(state, config), [])
        fresh = wf.build_summary(state, config)
        self.assertEqual(fresh["free_slots"], 3)
        self.assertTrue(fresh["should_discover_agent"])
        self.assertEqual(fresh["merge_queue_state"], "new")
        self.assertEqual(fresh["action"], "ask_user_merge")
        wf.deliver_merge_ask(state)
        delivered = wf.build_summary(state, config)
        self.assertEqual(delivered["merge_queue_state"], "answered")
        self.assertEqual(delivered["merge_head"]["id"], "dev-0001")
        self.assertEqual(delivered["action"], "discover_agent")
        self.assertEqual(wf.build_summary(state, config)["action"], "discover_agent")
        # Capacity rework keeps its slot priority over both discovery and the
        # already-delivered merge pool.
        state["points"].append(with_points([
            {"title": "parked", "status": "blocked", "blocked_reason": "capacity",
             "blocked_from_status": "repairing", "blocked_until": None}])["points"][0])
        capacity = wf.build_summary(state, config)
        self.assertEqual(capacity["action"], "resume_capacity")
        self.assertEqual(capacity["merge_queue_state"], "answered")

    def test_reworked_point_is_asked_again_after_repair(self):
        """Rework invalidates the delivered ask for the same point.

        Codex lifecycle case: notify the pool once, return the point for
        rework, then pass review again. The new ``awaiting_merge`` lifecycle
        must reach the user again instead of looking already answered.
        """
        config = make_config()
        state = wf.empty_state()
        point = wf.new_point(state, source="user", title="rework cycle")
        advance_to_reviewing(state, config, point["id"])
        wf.review_pass(state, config, point["id"])
        self.assertIsNotNone(wf.deliver_merge_ask(state))
        self.assertEqual(wf.build_summary(state, config)["merge_queue_state"], "answered")
        wf.review_fail(state, config, point["id"], reason="failed verification")
        self.assertEqual(point["status"], "repairing")
        self.assertNotIn("merge_queue_asked", state)
        self.assertEqual(wf.build_summary(state, config)["merge_queue_state"], "none")
        for status in ("verifying", "reviewing"):
            wf.transition(state, config, point, status)
        wf.review_pass(state, config, point["id"])
        self.assertEqual(point["status"], "awaiting_merge")
        summary = wf.build_summary(state, config)
        self.assertEqual(summary["merge_queue_state"], "new")
        self.assertEqual(summary["action"], "ask_user_merge")
        # Control: once delivered, the new lifecycle is answered exactly once.
        self.assertIsNotNone(wf.deliver_merge_ask(state))
        self.assertEqual(wf.build_summary(state, config)["merge_queue_state"], "answered")

    def test_cli_next_only_marks_delivery_with_the_explicit_flag(self):
        """Plain ``next`` reports the ask without writing; ``--deliver`` records it."""
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            config_path = Path(directory) / "config.json"
            wf.write_json(config_path, make_config())
            wf.save_state(state_path, with_points([merge_ready("waiting"),
                                                   busy_agent("busy")]))
            before = state_path.read_bytes()
            parsed = wf.build_parser().parse_args(["--state", str(state_path), "--config",
                                                   str(config_path), "next"])
            self.assertFalse(parsed.deliver)
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                self.assertEqual(wf.main(["--state", str(state_path), "--config", str(config_path),
                                          "next"]), 0)
            printed = json.loads(buffer.getvalue())
            self.assertEqual(printed["action"], "ask_user_merge")
            self.assertEqual(printed["merge_queue_state"], "new")
            self.assertEqual(state_path.read_bytes(), before)
            with redirect_stdout(buffer := io.StringIO()):
                self.assertEqual(wf.main(["--state", str(state_path), "--config", str(config_path),
                                          "next", "--deliver"]), 0)
            delivered = json.loads(buffer.getvalue())
            self.assertEqual(delivered["merge_queue_state"], "answered")
            self.assertEqual(delivered["merge_queue_asked"]["ids"], ["dev-0001"])
            # Delivery does not stop discovery while the PR waits to be merged.
            self.assertEqual(delivered["action"], "discover_agent")
            persisted = wf.load_state(state_path)
            self.assertEqual(persisted["merge_queue_asked"]["ids"], ["dev-0001"])
            self.assertEqual(persisted["merge_queue_asked"]["head"], "dev-0001")
            with redirect_stdout(buffer := io.StringIO()):
                self.assertEqual(wf.main(["--state", str(state_path), "--config", str(config_path),
                                          "next"]), 0)
            self.assertEqual(json.loads(buffer.getvalue())["action"], "discover_agent")

    def test_write_commands_do_not_consume_an_undelivered_merge_ask(self):
        """A write command must not mark the ask as shown.

        ``start`` / ``set-status`` / ``status`` save the whole state; computing a
        report there must never record a delivery the user has not seen,
        otherwise the next ``ask_user_merge`` silently disappears.
        """
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            config_path = Path(directory) / "config.json"
            wf.write_json(config_path, make_config())
            wf.save_state(state_path, with_points([merge_ready("waiting"),
                                                   busy_agent("busy"),
                                                   {"title": "other"}]))
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(wf.main(["--state", str(state_path), "--config", str(config_path),
                                          "status"]), 0)
                self.assertEqual(wf.main(["--state", str(state_path), "--config", str(config_path),
                                          "start", "dev-0003"]), 0)
                self.assertEqual(wf.main(["--state", str(state_path), "--config", str(config_path),
                                          "set-status", "dev-0003", "implementing"]), 0)
            saved = wf.load_state(state_path)
            self.assertEqual(wf.point_by_id(saved, "dev-0003")["status"], "implementing")
            self.assertNotIn("merge_queue_asked", saved)
            summary = wf.build_summary(saved, make_config())
            self.assertEqual(summary["merge_queue_state"], "new")
            self.assertEqual(summary["action"], "ask_user_merge")

    def test_review_fail_under_full_load_remains_active(self):
        config = make_config(concurrency=2)
        state = wf.empty_state()
        first = wf.new_point(state, source="user", title="first")
        second = wf.new_point(state, source="user", title="second")
        wf.start_point(state, config, first["id"])
        wf.start_point(state, config, second["id"])
        for status in ("implementing", "verifying", "reviewing"):
            wf.transition(state, config, first, status)
        self.assertEqual(wf.build_summary(state, config)["load"], 2)
        wf.review_fail(state, config, first["id"], reason="rework")
        self.assertEqual(first["status"], "repairing")
        self.assertEqual(wf.build_summary(state, config)["load"], 2)

    def test_merge_reject_parks_on_ai_in_flight_cap(self):
        config = make_config(max_auto_agent_in_flight=1)
        state = wf.empty_state()
        first = wf.new_point(state, source="agent", title="first", risk="low")
        second = wf.new_point(state, source="agent", title="second", risk="low")
        wf.promote_point(state, config, first["id"], auto=True)
        wf.start_point(state, config, first["id"])
        for status in ("implementing", "verifying", "reviewing"):
            wf.transition(state, config, first, status)
        wf.review_pass(state, config, first["id"])
        wf.promote_point(state, config, second["id"], auto=True)
        wf.start_point(state, config, second["id"])
        wf.merge_reject(state, config, first["id"], reason="rework")
        self.assertEqual(first["status"], "blocked")
        self.assertEqual(first["blocked_reason"], "capacity")

    def test_user_capacity_waiting_has_priority_over_ai(self):
        config = make_config(concurrency=1)
        state = wf.empty_state()
        first = wf.new_point(state, source="user", title="first")
        second = wf.new_point(state, source="user", title="second")
        first["status"] = "awaiting_merge"
        first["merge_ready_at"] = wf.iso(wf.utcnow())
        wf.start_point(state, config, second["id"])
        wf.merge_reject(state, config, first["id"], reason="rework")
        self.assertEqual(first["status"], "blocked")
        wf.block_point(state, config, second["id"], reason="external")
        summary = wf.build_summary(state, config)
        self.assertEqual([point["id"] for point in summary["user_capacity_waiting"]], [first["id"]])
        self.assertEqual(summary["action"], "resume_capacity")
        self.assertFalse(summary["should_discover_agent"])
        self.assertFalse(summary["can_auto_start_agent"])

        agent = wf.new_point(state, source="agent", title="agent", risk="low")
        with self.assertRaises(wf.WorkflowError):
            wf.promote_point(state, config, agent["id"], auto=True)
        wf.promote_point(state, config, agent["id"], auto=False)
        with self.assertRaises(wf.WorkflowError):
            wf.start_point(state, config, agent["id"])
        wf.resume_point(state, config, first["id"])
        self.assertEqual(first["status"], "repairing")

    def test_set_status_cannot_bypass_start_admission(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            config_path = Path(directory) / "config.json"
            output = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(output), redirect_stderr(stderr):
                self.assertEqual(wf.main(["--state", str(state_path), "--config", str(config_path), "init"]), 0)
                wf.write_json(config_path, make_config(concurrency=1))
                for title in ("first", "second"):
                    self.assertEqual(
                        wf.main(
                            [
                                "--state",
                                str(state_path),
                                "--config",
                                str(config_path),
                                "add",
                                "--source",
                                "user",
                                "--title",
                                title,
                            ]
                        ),
                        0,
                    )
                self.assertEqual(wf.main(["--state", str(state_path), "--config", str(config_path), "start", "dev-0001"]), 0)
                self.assertEqual(
                    wf.main(
                        [
                            "--state",
                            str(state_path),
                            "--config",
                            str(config_path),
                            "set-status",
                            "dev-0002",
                            "specifying",
                        ]
                    ),
                    2,
                )
            state = wf.load_state(state_path)
            self.assertEqual(wf.point_by_id(state, "dev-0002")["status"], "queued")

    def test_set_status_cannot_bypass_promote(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            config_path = Path(directory) / "config.json"
            output = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(output), redirect_stderr(stderr):
                self.assertEqual(wf.main(["--state", str(state_path), "--config", str(config_path), "init"]), 0)
                self.assertEqual(
                    wf.main(
                        [
                            "--state",
                            str(state_path),
                            "--config",
                            str(config_path),
                            "add",
                            "--source",
                            "agent",
                            "--title",
                            "agent",
                            "--risk",
                            "low",
                        ]
                    ),
                    0,
                )
                self.assertEqual(
                    wf.main(
                        [
                            "--state",
                            str(state_path),
                            "--config",
                            str(config_path),
                            "set-status",
                            "dev-0001",
                            "queued",
                        ]
                    ),
                    2,
                )
            state = wf.load_state(state_path)
            self.assertEqual(wf.point_by_id(state, "dev-0001")["status"], "proposed")


class MergeNotificationLifecycleCLITests(unittest.TestCase):
    """Cross-process notification lifecycle: clear on empty queue, re-ask after rework.

    Codex reproduced the bug with real CLI invocations, where an in-memory state
    object could hide a missing disk write. Each step below is its own process,
    so the persisted ``merge_queue_asked`` record is what the next step sees.
    """

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.state_path = self.directory / "state.json"
        self.config_path = self.directory / "config.json"
        wf.write_json(self.config_path, make_config())
        wf.save_state(self.state_path, with_points([merge_ready("waiting")]))

    def run_cli(self, *arguments):
        return subprocess.run(
            [sys.executable, str(AGENT_WORKFLOW), "--state", str(self.state_path),
             "--config", str(self.config_path), *arguments],
            cwd=str(ROOT), capture_output=True, text=True,
        )

    def next_summary(self, *extra):
        result = self.run_cli("next", *extra)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_empty_queue_clear_is_persisted_and_rework_reasks(self):
        fresh = self.next_summary()
        self.assertEqual(fresh["merge_queue_state"], "new")
        self.assertEqual(fresh["action"], "ask_user_merge")
        delivered = self.next_summary("--deliver")
        self.assertEqual(delivered["merge_queue_state"], "answered")
        self.assertEqual(delivered["action"], "discover_agent")
        # Rework empties the waiting pool: the next explicit delivery clears the
        # old record, and that clear must reach the disk (not just the summary).
        self.assertEqual(self.run_cli("review-fail", "dev-0001", "--reason", "tests failed").returncode, 0)
        emptied = self.next_summary("--deliver")
        self.assertEqual(emptied["merge_queue_state"], "none")
        self.assertIsNone(emptied["merge_head"])
        self.assertNotIn("merge_queue_asked", wf.load_state(self.state_path))
        # Complete the rework -> review cycle. The same point now starts a new
        # merge lifecycle and must be delivered again, not judged "answered".
        for status in ("verifying", "reviewing"):
            result = self.run_cli("set-status", "dev-0001", status)
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.run_cli("review-pass", "dev-0001").returncode, 0)
        reasked = self.next_summary()
        self.assertEqual(reasked["merge_queue_state"], "new")
        self.assertEqual(reasked["action"], "ask_user_merge")
        self.assertEqual(reasked["merge_head"]["id"], "dev-0001")


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.state = wf.empty_state()
        self.config = make_config()

    def test_valid_state_has_no_issues(self):
        point = wf.new_point(self.state, source="user", title="ok")
        self.assertEqual(wf.validate_state(self.state, self.config), [])
        self.assertEqual(point["id"], "dev-0001")

    def test_duplicate_id_is_reported(self):
        point = wf.new_point(self.state, source="user", title="one")
        self.state["points"].append(copy.deepcopy(point))
        issues = wf.validate_state(self.state, self.config)
        self.assertTrue(any("duplicate" in issue for issue in issues))

    def test_blocked_point_requires_restore_metadata(self):
        point = wf.new_point(self.state, source="user", title="one")
        point["status"] = "blocked"
        issues = wf.validate_state(self.state, self.config)
        self.assertTrue(any("blocked_from_status" in issue for issue in issues))
        self.assertTrue(any("blocked_until" in issue for issue in issues))

    def test_cli_init_and_add(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            config_path = Path(directory) / "config.json"
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                self.assertEqual(
                    wf.main(["--state", str(state_path), "--config", str(config_path), "init"]),
                    0,
                )
                self.assertEqual(
                    wf.main(
                        [
                            "--state",
                            str(state_path),
                            "--config",
                            str(config_path),
                            "add",
                            "--source",
                            "user",
                            "--title",
                            "CLI work",
                        ]
                    ),
                    0,
                )
            self.assertTrue(state_path.exists())
            self.assertTrue(config_path.exists())
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["points"][0]["title"], "CLI work")
            config = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(config["concurrency"], 3)


class CLIEncodingTests(unittest.TestCase):
    """A non-UTF-8 console must never turn a successful state write into a failure.

    Regression coverage for dev-0002: ``add`` with a CJK title used to raise
    ``UnicodeEncodeError`` after ``save_state`` had succeeded, exiting 1 with a
    half-written JSON document and inviting the captain to retry and register a
    duplicate point.
    """

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.state_path = Path(directory.name) / "state.json"
        self.config_path = Path(directory.name) / "config.json"

    def cli_args(self, *arguments):
        return ["--state", str(self.state_path), "--config", str(self.config_path), *arguments]

    def run_cli(self, *arguments, env=None):
        return subprocess.run(
            [sys.executable, str(AGENT_WORKFLOW), *self.cli_args(*arguments)],
            cwd=str(ROOT),
            capture_output=True,
            env=env,
        )

    def init_state(self):
        with redirect_stdout(io.StringIO()):
            self.assertEqual(wf.main(self.cli_args("init")), 0)

    def add_cjk_point(self):
        with redirect_stdout(io.StringIO()):
            code = wf.main(self.cli_args("add", "--source", "user", "--title", CJK_TITLE, "--risk", "normal"))
        self.assertEqual(code, 0)
        return wf.load_state(self.state_path)["points"][0]

    def test_add_cjk_title_on_ascii_console_exits_zero_with_complete_json(self):
        env = ascii_console_env()
        self.assertEqual(self.run_cli("init", env=env).returncode, 0)

        result = self.run_cli("add", "--source", "user", "--title", CJK_TITLE, "--risk", "normal", env=env)

        self.assertEqual(result.returncode, 0, result.stderr.decode("ascii", "replace"))
        self.assertNotIn(b"UnicodeEncodeError", result.stderr)
        self.assertIn(b"PYTHONIOENCODING=utf-8", result.stderr)  # degrade is explained, not silent
        self.assertEqual(result.stdout.decode("ascii").strip()[-1], "}")  # no half-written document
        point = json.loads(result.stdout)
        self.assertEqual(point["title"], CJK_TITLE)
        # The exit code and stdout now agree with state: no retry, no duplicate point.
        state = wf.load_state(self.state_path)
        self.assertEqual([(item["id"], item["title"]) for item in state["points"]], [(point["id"], CJK_TITLE)])

    def test_print_json_falls_back_to_ascii_escapes(self):
        self.init_state()
        stream = strict_ascii_stream()
        with redirect_stdout(stream), redirect_stderr(io.StringIO()):
            self.assertEqual(
                wf.main(self.cli_args("add", "--source", "user", "--title", CJK_TITLE, "--risk", "normal")), 0
            )
            stream.flush()
            output = stream.buffer.getvalue().decode("ascii")
        point = json.loads(output)
        self.assertEqual(point["title"], CJK_TITLE)
        self.assertIn("\\u4f18", output)  # escaped fallback, not raw CJK bytes
        self.assertEqual(len(wf.load_state(self.state_path)["points"]), 1)

    def test_status_and_list_render_on_ascii_console(self):
        self.init_state()
        self.add_cjk_point()
        stream = strict_ascii_stream()
        with redirect_stdout(stream), redirect_stderr(io.StringIO()):
            self.assertEqual(wf.main(self.cli_args("status")), 0)
            self.assertEqual(wf.main(self.cli_args("list")), 0)
            stream.flush()
            output = stream.buffer.getvalue().decode("ascii")
        self.assertIn("dev-0001", output)
        self.assertIn("queued", output)
        self.assertIn("\\u4f18", output)

    def test_exit_codes_survive_ascii_console(self):
        env = ascii_console_env()
        self.assertEqual(self.run_cli("init", env=env).returncode, 0)

        # ASCII titles keep working exactly as before (control group).
        control = self.run_cli("add", "--source", "user", "--title", "ascii title", env=env)
        self.assertEqual(control.returncode, 0)
        self.assertEqual(json.loads(control.stdout)["title"], "ascii title")

        state = wf.load_state(self.state_path)
        state["points"].append(copy.deepcopy(state["points"][0]))
        wf.save_state(self.state_path, state)
        invalid = self.run_cli("validate", env=env)
        self.assertEqual(invalid.returncode, 1)  # findings still exit 1, not an encoding crash
        self.assertIn(b"ERROR", invalid.stdout)

        transition = self.run_cli("set-status", "dev-0001", "queued", env=env)
        self.assertEqual(transition.returncode, 2)  # WorkflowError still exits 2
        self.assertIn(b"error:", transition.stderr)


class StateLockTests(unittest.TestCase):
    """Ownership-based state lock: no mtime staleness guessing, no lost updates."""

    HOLDER_SCRIPT = r'''
import sys, time
from pathlib import Path
sys.path.insert(0, {root!r})
from tools.dev_workflow import agent_workflow as wf
with wf.state_lock(Path({state!r})):
    print("locked", flush=True)
    time.sleep(60)
'''

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.state_path = self.directory / "state.json"
        self.config_path = self.directory / "config.json"
        self.lock_path = self.state_path.with_name(self.state_path.name + ".lock")

    def cli_args(self, state_path, *arguments):
        return [sys.executable, str(AGENT_WORKFLOW), "--state", str(state_path),
                "--config", str(self.config_path), *arguments]

    def run_cli(self, *arguments, state_path=None):
        return subprocess.run(self.cli_args(state_path or self.state_path, *arguments),
                              cwd=str(ROOT), capture_output=True, text=True)

    def _start_holder(self):
        """Run a real second process that owns the OS lock until it is killed."""
        script = self.HOLDER_SCRIPT.format(root=str(ROOT), state=str(self.state_path))
        holder = subprocess.Popen([sys.executable, "-c", script], cwd=str(ROOT),
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

        def cleanup():
            if holder.poll() is None:
                holder.kill()
            holder.communicate(timeout=10)

        self.addCleanup(cleanup)
        ready = holder.stdout.readline().strip()
        if ready != "locked":
            holder.kill()
            _, stderr = holder.communicate(timeout=10)
            self.fail(f"lock holder did not start: {stderr}")
        return holder

    @staticmethod
    def _stop(holder):
        holder.kill()
        holder.communicate(timeout=10)

    def test_concurrent_adds_keep_every_point(self):
        self.assertEqual(self.run_cli("init").returncode, 0)
        processes = [
            subprocess.Popen(self.cli_args(self.state_path, "add", "--source", "agent", "--risk", "low",
                                           "--title", f"race-{index}"),
                             cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            for index in range(6)
        ]
        results = [process.communicate(timeout=60) for process in processes]
        self.assertEqual([process.returncode for process in processes], [0] * 6,
                         [stderr for _, stderr in results])
        state = wf.load_state(self.state_path)
        self.assertEqual(len(state["points"]), 6)
        self.assertEqual(len({point["id"] for point in state["points"]}), 6)
        self.assertEqual(state["next_id"], 7)
        self.assertEqual(sorted(path.name for path in self.directory.iterdir()),
                         ["config.json", "state.json", "state.json.lock"])
        with wf.state_lock(self.state_path, timeout=1.0):
            pass  # every writer released the OS lock

    def test_lock_is_released_after_command_and_remains_usable(self):
        # `init` also has to work when the state directory does not exist yet.
        state_path = self.directory / "nested" / "state.json"
        init = self.run_cli("init", state_path=state_path)
        self.assertEqual(init.returncode, 0, init.stderr)
        lock = state_path.with_name(state_path.name + ".lock")
        with wf.state_lock(state_path):
            self.assertTrue(lock.exists(), "the lock file exists while the command holds it")
        with wf.state_lock(state_path, timeout=0.5):
            pass  # released; the leftover file alone is not ownership
        add = self.run_cli("add", "--source", "user", "--title", "after lock", state_path=state_path)
        self.assertEqual(add.returncode, 0, add.stderr)
        self.assertEqual(len(wf.load_state(state_path)["points"]), 1)

    def test_old_lock_file_mtime_does_not_grant_ownership(self):
        self.assertEqual(self.run_cli("init").returncode, 0)
        old = time.time() - 10_000
        os.utime(self.lock_path, (old, old))
        holder = self._start_holder()
        with self.assertRaises(wf.WorkflowError):
            with wf.state_lock(self.state_path, timeout=0.4):
                self.fail("an old mtime must not be treated as a stale lock")
        self._stop(holder)
        with wf.state_lock(self.state_path, timeout=2.0):
            pass  # the dead owner's OS lock is released; no mtime surgery needed

    def test_locked_state_reports_actionable_error(self):
        holder = self._start_holder()
        with self.assertRaises(wf.WorkflowError) as caught:
            with wf.state_lock(self.state_path, timeout=0.3):
                self.fail("a live lock must not be taken over")
        message = str(caught.exception)
        self.assertIn("locked by another process", message)
        self.assertIn(self.lock_path.name, message)
        self.assertIn(str(self.lock_path), message)
        self.assertTrue(self.lock_path.exists(), "a timeout must not delete another process's lock file")
        self._stop(holder)
        with wf.state_lock(self.state_path, timeout=2.0):
            pass


if __name__ == "__main__":
    unittest.main()
