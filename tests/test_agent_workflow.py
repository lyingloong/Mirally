from __future__ import annotations

import copy
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import agent_workflow as wf  # noqa: E402


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
    wf.transition(state, wf.point_by_id(state, point_id), "implementing")
    wf.transition(state, wf.point_by_id(state, point_id), "verifying")
    wf.transition(state, wf.point_by_id(state, point_id), "reviewing")


class ConfigTests(unittest.TestCase):
    def test_defaults_match_agreed_workflow(self):
        config = wf.load_config(Path(tempfile.gettempdir()) / "definitely-missing-agent-workflow.json")
        self.assertEqual(config["concurrency"], 3)
        self.assertEqual(config["quota"]["retry_after_minutes"], 30)
        self.assertEqual(config["quota"]["backoff_minutes"], [30, 60, 120])
        self.assertEqual(config["quota"]["fallback"]["mode"], "quota_restart_only")
        self.assertEqual(config["quota"]["fallback"]["provider"], "deepseek-account")
        self.assertEqual(config["primary_route"]["provider"], "ustc")

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
        wf.transition(self.state, agent, "queued")
        with self.assertRaises(wf.WorkflowError):
            wf.start_point(self.state, self.config, agent["id"])

    def test_invalid_transition_is_rejected(self):
        point = self.add_user()
        wf.start_point(self.state, self.config, point["id"])
        with self.assertRaises(wf.WorkflowError):
            wf.transition(self.state, point, "done")
        with self.assertRaises(wf.WorkflowError):
            wf.transition(self.state, point, "blocked")  # blocked uses the block command


class SummaryTests(unittest.TestCase):
    def setUp(self):
        self.state = wf.empty_state()
        self.config = make_config()

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
        wf.review_pass(self.state, point["id"])
        summary = wf.build_summary(self.state, self.config)
        self.assertEqual(summary["load"], 0)
        self.assertEqual(summary["free_slots"], 3)
        self.assertEqual(summary["merge_queue"][0]["id"], point["id"])
        self.assertTrue(summary["merge_queue"][0]["exempt"])
        self.assertFalse(summary["merge_queue"][0]["counts_toward_concurrency"])
        self.assertEqual(summary["action"], "ask_user_merge")

    def test_second_merge_ready_point_counts_toward_concurrency(self):
        first = self.add_user("first")
        second = self.add_user("second")
        for point in (first, second):
            advance_to_reviewing(self.state, self.config, point["id"])
            wf.review_pass(self.state, point["id"])
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
        wf.review_pass(self.state, merged["id"])
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


class MergeGateTests(unittest.TestCase):
    def setUp(self):
        self.state = wf.empty_state()
        self.config = make_config()

    def prepare(self, title):
        point = wf.new_point(self.state, source="user", title=title)
        advance_to_reviewing(self.state, self.config, point["id"])
        wf.review_pass(self.state, point["id"])
        return point

    def test_review_pass_does_not_merge(self):
        point = self.prepare("one")
        self.assertEqual(point["status"], "awaiting_merge")
        self.assertIsNotNone(point["merge_ready_at"])
        self.assertIsNone(point.get("merged_at"))

    def test_only_the_head_can_be_approved(self):
        first = self.prepare("first")
        second = self.prepare("second")
        with self.assertRaises(wf.WorkflowError):
            wf.merge_approve(self.state, second["id"])
        wf.merge_approve(self.state, first["id"])
        self.assertEqual(first["status"], "merging")
        wf.merge_done(self.state, first["id"])
        self.assertEqual(first["status"], "done")
        self.assertIsNotNone(first["merged_at"])
        wf.merge_approve(self.state, second["id"])
        self.assertEqual(second["status"], "merging")

    def test_merge_reject_returns_to_repair_or_close(self):
        point = self.prepare("one")
        wf.merge_reject(self.state, point["id"], reason="需要调整文案")
        self.assertEqual(point["status"], "repairing")
        self.assertIn("需要调整文案", point["notes"])

        other = self.prepare("two")
        wf.merge_reject(self.state, other["id"], reason="不做了", close=True)
        self.assertEqual(other["status"], "rejected")

    def test_invalid_next_step_is_rejected(self):
        point = self.prepare("one")
        with self.assertRaises(wf.WorkflowError):
            wf.merge_done(self.state, point["id"])


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
        wf.resume_point(self.state, self.point["id"])
        self.assertEqual(self.point["status"], "specifying")
        self.assertIsNone(self.point["fallback"])

    def test_backoff_grows_and_retry_limit_is_reported(self):
        wf.block_point(self.state, self.config, self.point["id"], reason="quota")
        wf.resume_point(self.state, self.point["id"])
        _, second = wf.block_point(self.state, self.config, self.point["id"], reason="quota")
        self.assertEqual(second["wait_minutes"], 60)
        self.assertFalse(second["retry_exhausted"])
        wf.resume_point(self.state, self.point["id"])
        _, third = wf.block_point(self.state, self.config, self.point["id"], reason="quota")
        self.assertEqual(third["wait_minutes"], 120)
        self.assertTrue(third["retry_exhausted"])

    def test_non_quota_block_has_no_fallback(self):
        _, result = wf.block_point(self.state, self.config, self.point["id"], reason="external", minutes=10)
        self.assertEqual(result["wait_minutes"], 10)
        self.assertIsNone(result["fallback"])
        self.assertFalse(wf.build_summary(self.state, self.config)["paused"])


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


if __name__ == "__main__":
    unittest.main()
