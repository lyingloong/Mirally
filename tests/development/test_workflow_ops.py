from __future__ import annotations

import copy
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.dev_workflow import agent_workflow as wf
from tools.dev_workflow import ops


def make_config(**overrides):
    config = copy.deepcopy(wf.DEFAULT_CONFIG)
    config["merge_queue_exempt"] = 100
    for key, value in overrides.items():
        config[key] = value
    return config


class PendingPoolTests(unittest.TestCase):
    def test_pending_rows_include_pool_metadata(self):
        state = wf.empty_state()
        config = make_config()
        first = wf.new_point(state, source="user", title="first")
        second = wf.new_point(state, source="agent", title="second")
        for point in (first, second):
            point["status"] = "awaiting_merge"
            point["merge_ready_at"] = wf.iso(wf.utcnow())
            point["pr"] = 10 if point is first else 11
            point["head_sha"] = "a" * 40 if point is first else "b" * 40
        rows = ops.pending_rows(state, config)
        self.assertEqual([row["id"] for row in rows], [first["id"], second["id"]])
        self.assertTrue(all(row["exempt"] for row in rows))
        self.assertFalse(any(row["counts_toward_concurrency"] for row in rows))
        self.assertEqual(rows[0]["pr"], 10)

    def test_pending_cli_prints_pool(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            config_path = Path(directory) / "config.json"
            state = wf.empty_state()
            point = wf.new_point(state, source="user", title="pooled")
            point["status"] = "awaiting_merge"
            point["merge_ready_at"] = wf.iso(wf.utcnow())
            point["pr"] = 12
            wf.save_state(state_path, state)
            wf.write_json(config_path, make_config())
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(
                    ops.main(["--state", str(state_path), "--config", str(config_path), "pending"]),
                    0,
                )
            self.assertIn("pending merge pool: 1", output.getvalue())
            self.assertIn("PR #12", output.getvalue())


class RecordTests(unittest.TestCase):
    def setUp(self):
        self.state = wf.empty_state()
        self.config = make_config()
        self.point = wf.new_point(self.state, source="user", title="work")
        self.point["status"] = "reviewing"

    def test_spec_path_records_an_existing_file(self):
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as handle:
            handle.write("# spec\n")
            path = handle.name
        point = ops.set_spec_path(self.state, self.point["id"], path)
        self.assertEqual(point["spec_path"], path)

    def test_spec_path_rejects_missing_file(self):
        with self.assertRaises(wf.WorkflowError):
            ops.set_spec_path(self.state, self.point["id"], "missing-dev-spec.md")

    def test_record_advances_state_and_metadata_in_one_call(self):
        point = ops.record_point(
            self.state,
            self.config,
            self.point["id"],
            status="awaiting_merge",
            branch="agent/dev-0001",
            worktree=r"C:\work\dev-0001",
            pr=13,
            head_sha="c" * 40,
            task_assignments=["implementation=t2", "verification=t3"],
        )
        self.assertEqual(point["status"], "awaiting_merge")
        self.assertEqual(point["branch"], "agent/dev-0001")
        self.assertEqual(point["pr"], 13)
        self.assertEqual(point["task_ids"]["verification"], "t3")
        self.assertIsNotNone(point["merge_ready_at"])

    def test_record_does_not_bypass_state_machine(self):
        with self.assertRaises(wf.WorkflowError):
            ops.record_point(self.state, self.config, self.point["id"], status="done")


if __name__ == "__main__":
    unittest.main()
