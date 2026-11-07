from __future__ import annotations

import copy
import io
import os
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.dev_workflow import agent_workflow as wf
from tools.dev_workflow import ops
from tools.dev_workflow import progress_page as pp


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


def progress_cli(args, *, env_extra=None):
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, "-m", "tools.dev_workflow.progress_page", *args],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


class ProgressPageConsoleTests(unittest.TestCase):
    """progress_page CLI must degrade cleanly on non-UTF-8 consoles."""

    def setUp(self):
        fixture_root = pp.fixture_root(ROOT)
        fixture_root.mkdir(parents=True, exist_ok=True)
        self._temp = tempfile.TemporaryDirectory(dir=fixture_root)
        self.addCleanup(self._temp.cleanup)
        self.workdir = Path(self._temp.name) / "agent-workflow"
        self.workdir.mkdir()
        wf.save_state(self.workdir / "state.json", wf.empty_state())
        wf.write_json(self.workdir / "config.json", make_config())

    def test_stdout_survives_cp1252_and_gbk_consoles(self):
        for encoding in ("cp1252:strict", "gbk:strict"):
            label = encoding.split(":")[0]
            html = self.workdir / f"console-{label}.html"
            data = self.workdir / f"console-{label}.json"
            result = progress_cli(
                ["--workdir", str(self.workdir), "--out", str(html), "--data", str(data)],
                env_extra={"PYTHONIOENCODING": encoding},
            )
            self.assertEqual(result.returncode, 0, result.stderr.decode("ascii", "replace"))
            self.assertNotIn(b"Traceback", result.stderr)
            self.assertTrue(html.is_file())
            self.assertTrue(data.is_file())

    def test_stderr_and_argparse_survive_cp1252_console(self):
        outside = Path(tempfile.gettempdir()).resolve()
        self.assertNotEqual(outside, ROOT)
        cases = ((["--workdir", str(outside)], 2), (["--help"], 0), (["--no-such-flag"], 2))
        for argv, expected in cases:
            result = progress_cli(argv, env_extra={"PYTHONIOENCODING": "cp1252:strict"})
            self.assertEqual(result.returncode, expected, result.stderr.decode("ascii", "replace"))
            self.assertNotIn(b"Traceback", result.stderr)

    def test_merge_state_treats_zero_checks_as_a_user_decision(self):
        point = {"status": "awaiting_merge", "pr": 12}
        cached = {"state": "OPEN", "mergeable": "MERGEABLE", "checks": {"pass": 0, "pending": 0, "fail": 0}}
        self.assertEqual(pp.merge_state(point, cached), {"state": "user", "reason": "无 CI 检查"})
        # The same PR with at least one green check is the Codex-mergeable boundary.
        self.assertEqual(
            pp.merge_state(point, {"state": "OPEN", "mergeable": "MERGEABLE",
                                   "checks": {"pass": 1, "pending": 0, "fail": 0}})["state"],
            "codex",
        )


CROSS_ENTRY_CHILD = r'''
import contextlib, sys, time
from pathlib import Path
sys.path.insert(0, __ROOT__)
from tools.dev_workflow import agent_workflow as wf
from tools.dev_workflow import ops

mode, state_path, config_path, point_id, go_path = sys.argv[1:6]
label = mode.replace("-", "_")
original_load = wf.load_state

def slow_load(path):
    state = original_load(path)
    time.sleep(0.6)  # widen the read-modify-write window deterministically
    return state

wf.load_state = slow_load
if mode.startswith(("unlocked-", "bypass-")):
    @contextlib.contextmanager
    def _no_lock(state_path, *, timeout=10.0):
        yield
    wf.state_lock = _no_lock
    mode = mode.split("-", 1)[1]

Path(go_path + "." + label + ".ready").write_text("ready", encoding="utf-8")
while not Path(go_path).exists():
    time.sleep(0.01)
if mode == "ops":
    code = ops.main(["--state", state_path, "--config", config_path, "record", point_id,
                     "--branch", "race-branch", "--head-sha", "a" * 40])
else:
    code = wf.main(["--state", state_path, "--config", config_path, "add", "--source", "agent",
                    "--risk", "low", "--title", "race-agent"])
sys.exit(code)
'''


class CrossEntryLockTests(unittest.TestCase):
    """ops.py writes and agent_workflow writes must share one OS lock window."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.state_path = self.directory / "state.json"
        self.config_path = self.directory / "config.json"
        state = wf.empty_state()
        self.point = wf.new_point(state, source="user", title="existing")
        wf.save_state(self.state_path, state)
        wf.write_json(self.config_path, make_config())

    def _run_pair(self, modes):
        script = CROSS_ENTRY_CHILD.replace("__ROOT__", repr(str(ROOT)))
        go_path = self.directory / "go"
        processes = []
        for mode in modes:
            process = subprocess.Popen(
                [sys.executable, "-c", script, mode, str(self.state_path), str(self.config_path),
                 self.point["id"], str(go_path)],
                cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            processes.append((mode, process))

        # Both children patch load_state then wait; start them together so both
        # read the same snapshot before either save recreates the race.
        deadline = time.monotonic() + 20
        for mode, process in processes:
            ready = Path(str(go_path) + "." + mode.replace("-", "_") + ".ready")
            while not ready.exists():
                if process.poll() is not None:
                    _, stderr = process.communicate()
                    self.fail(f"{mode} exited before start: {stderr}")
                if time.monotonic() >= deadline:
                    self.fail(f"{mode} did not become ready")
                time.sleep(0.01)
        go_path.write_text("go", encoding="utf-8")

        results = [process.communicate(timeout=60) for _, process in processes]
        for (mode, process), (_, stderr) in zip(processes, results):
            self.assertEqual(process.returncode, 0, f"{mode}: {stderr}")
        return wf.load_state(self.state_path)

    def test_ops_record_and_agent_add_share_one_lock(self):
        state = self._run_pair(["ops", "agent"])
        titles = [point["title"] for point in state["points"]]
        self.assertIn("race-agent", titles)
        self.assertEqual(wf.point_by_id(state, self.point["id"])["branch"], "race-branch")

    def test_old_ops_entrypoint_without_shared_lock_loses_an_update(self):
        # The pre-repair implementation: agent_workflow held a lock, ops.py did
        # not. Simulate exactly that pair; the same barrier must show a loss.
        state = self._run_pair(["bypass-ops", "agent"])
        titles = [point["title"] for point in state["points"]]
        existing = wf.point_by_id(state, self.point["id"])
        lost_agent = "race-agent" not in titles
        lost_ops = existing.get("branch") != "race-branch"
        self.assertTrue(lost_agent or lost_ops,
                        f"the old unlock-for-ops race unexpectedly kept both updates: {titles} / {existing}")


if __name__ == "__main__":
    unittest.main()
