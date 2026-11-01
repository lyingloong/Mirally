from __future__ import annotations

import copy
import io
import os
import subprocess
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


def cli(args, *, env_extra=None, script=False):
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    if env_extra:
        env.update(env_extra)
    target = [sys.executable, str(ROOT / "tools" / "dev_workflow" / "ops.py")] if script else [
        sys.executable,
        "-m",
        "tools.dev_workflow.ops",
    ]
    return subprocess.run(
        [*target, *args],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


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

    def test_pending_cli_survives_gbk_and_ascii_consoles(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            config_path = Path(directory) / "config.json"
            state = wf.empty_state()
            point = wf.new_point(state, source="user", title="emoji \U0001f600 中文")
            point["status"] = "awaiting_merge"
            point["merge_ready_at"] = wf.iso(wf.utcnow())
            point["pr"] = 14
            wf.save_state(state_path, state)
            wf.write_json(config_path, make_config())
            for encoding in ("gbk:strict", "ascii:strict"):
                result = cli(
                    ["--state", str(state_path), "--config", str(config_path), "pending"],
                    env_extra={"PYTHONIOENCODING": encoding},
                )
                self.assertEqual(result.returncode, 0, result.stderr.decode("ascii", "replace"))
                self.assertIn(b"pending merge pool: 1", result.stdout)
                self.assertNotIn(b"Traceback", result.stderr)


class EntrypointTests(unittest.TestCase):
    def test_direct_script_entrypoint(self):
        result = cli(["--help"], script=True)
        self.assertEqual(result.returncode, 0, result.stderr.decode("ascii", "replace"))
        self.assertIn(b"pending", result.stdout)


class RecordTests(unittest.TestCase):
    def setUp(self):
        self.state = wf.empty_state()
        self.config = make_config()
        self.point = wf.new_point(self.state, source="user", title="work")

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
        self.point["status"] = "verifying"
        point = ops.record_point(
            self.state,
            self.config,
            self.point["id"],
            status="reviewing",
            branch="agent/dev-0001",
            worktree=r"C:\work\dev-0001",
            pr=13,
            head_sha="c" * 40,
            task_assignments=["implementation=t2", "verification=t3"],
        )
        self.assertEqual(point["status"], "reviewing")
        self.assertEqual(point["branch"], "agent/dev-0001")
        self.assertEqual(point["pr"], 13)
        self.assertEqual(point["task_ids"]["verification"], "t3")

    def test_record_rejects_restricted_merge_status(self):
        self.point["status"] = "awaiting_merge"
        self.point["merge_ready_at"] = wf.iso(wf.utcnow())
        with self.assertRaises(wf.WorkflowError):
            ops.record_point(self.state, self.config, self.point["id"], status="merging")
        self.assertEqual(self.point["status"], "awaiting_merge")
        self.assertIsNone(self.point.get("merge_approved_at"))

    def test_record_cli_rejects_non_head_merge_and_keeps_state(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            config_path = Path(directory) / "config.json"
            state = wf.empty_state()
            first = wf.new_point(state, source="user", title="head")
            second = wf.new_point(state, source="user", title="second")
            for point in (first, second):
                point["status"] = "awaiting_merge"
                point["merge_ready_at"] = wf.iso(wf.utcnow())
            wf.save_state(state_path, state)
            wf.write_json(config_path, make_config())
            result = cli(
                [
                    "--state",
                    str(state_path),
                    "--config",
                    str(config_path),
                    "record",
                    second["id"],
                    "--status",
                    "merging",
                ]
            )
            self.assertEqual(result.returncode, 2, result.stderr.decode("ascii", "replace"))
            saved = wf.load_state(state_path)
            saved_second = wf.point_by_id(saved, second["id"])
            self.assertEqual(saved_second["status"], "awaiting_merge")
            self.assertIsNone(saved_second.get("merge_approved_at"))


if __name__ == "__main__":
    unittest.main()
