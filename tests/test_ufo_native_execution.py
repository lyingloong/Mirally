"""Deterministic production-entry integration tests, without a real desktop/API.

The subprocess keeps the actual UFO Session, processors, dispatcher, Computer,
FastMCP registry/client/server, and UI action receiver. Only model completion and
the Windows window/capture/input endpoints are replaced. This verifies wiring;
it does not certify Windows capture exclusion or model quality on a live desktop.
"""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
UFO_ROOT = ROOT / "UFO"


@unittest.skipUnless(sys.platform == "win32" and (UFO_ROOT / "ufo").is_dir(),
                     "Requires Windows, the UFO submodule, and its dependencies")
class NativeExecutionTests(unittest.TestCase):
    def run_scenario(self, scenario):
        with tempfile.TemporaryDirectory() as directory:
            temporary_root = Path(directory)
            config_dir = temporary_root / "config" / "openassist" / "ufo"
            config_dir.mkdir(parents=True)

            def absolute_prompts(value):
                if isinstance(value, dict):
                    return {key: absolute_prompts(item) for key, item in value.items()}
                if isinstance(value, list):
                    return [absolute_prompts(item) for item in value]
                if isinstance(value, str) and value.startswith("ufo/"):
                    return (UFO_ROOT / value).as_posix()
                return value

            for source in (ROOT / "config" / "openassist" / "ufo").iterdir():
                if source.name == "agents.yaml":
                    continue  # Never open local credentials.
                if source.suffix != ".yaml" and source.name != "agents.yaml.template":
                    continue
                value = yaml.safe_load(source.read_text(encoding="utf-8"))
                if source.name == "system.yaml":
                    value.update({
                        "HOSTAGENT_PROMPT": "ufo/prompts/share/base/host_agent.yaml",
                        "HOSTAGENT_EXAMPLE_PROMPT": "ufo/prompts/examples/{mode}/host_agent_example.yaml",
                        "APPAGENT_PROMPT": "ufo/prompts/share/base/app_agent.yaml",
                        "APPAGENT_EXAMPLE_PROMPT": "ufo/prompts/examples/{mode}/app_agent_example.yaml",
                        "APPAGENT_EXAMPLE_PROMPT_AS": "ufo/prompts/examples/{mode}/app_agent_example_as.yaml",
                        "API_PROMPT": "ufo/prompts/share/base/api.yaml",
                        "INPUT_TEXT_API": "set_text",
                        "MAX_STEP": 12, "MAX_RETRY": 1, "JSON_PARSING_RETRY": 1,
                        "SLEEP_TIME": 0, "RECTANGLE_TIME": 0,
                        "MAXIMIZE_WINDOW": False, "SHOW_VISUAL_OUTLINE_ON_SCREEN": False,
                        "SAVE_UI_TREE": False, "SAVE_FULL_SCREEN": False,
                        "SCREENSHOT_TO_MEMORY": False, "EVA_SESSION": False,
                    })
                if source.name == "mcp.yaml":
                    # Still use real native servers. Limit the integration fixture
                    # to the UI tools so no external COM/shell service is started.
                    value = {
                        agent: {"default": {
                            "data_collection": [{"namespace": "UICollector", "type": "local", "reset": False}],
                            "action": [{"namespace": namespace, "type": "local", "reset": False}],
                        }}
                        for agent, namespace in (("HostAgent", "HostUIExecutor"),
                                                 ("AppAgent", "AppUIExecutor"))
                    }
                name = "agents.yaml" if source.name.endswith(".template") else source.name
                (config_dir / name).write_text(yaml.safe_dump(absolute_prompts(value)), encoding="utf-8")

            environment = dict(os.environ, PYTHONIOENCODING="utf-8", NO_COLOR="1")
            result = subprocess.run(
                [sys.executable, str(ROOT / "tests" / "native_execution_scenarios.py"),
                 str(ROOT), str(UFO_ROOT), scenario],
                cwd=temporary_root, env=environment, capture_output=True,
                text=True, encoding="utf-8", timeout=90,
            )
            self.assertEqual(result.returncode, 0, result.stdout[-2000:] + result.stderr[-12000:])
            self.assertIn(f"native execution OK: {scenario}", result.stdout)
            self.assertNotIn("Error loading module", result.stdout + result.stderr)

    def test_history_reaches_host_and_app_and_business_result_returns(self):
        self.run_scenario("history")

    def test_stop_while_model_waits_does_not_start_returned_action(self):
        self.run_scenario("model_stop")

    def test_stop_between_batch_actions_blocks_the_rest(self):
        self.run_scenario("batch_stop")

    def test_supplement_invalidates_old_plan_and_reobserves(self):
        self.run_scenario("supplement")

    def test_takeover_acknowledges_safe_boundary_and_resume_reobserves(self):
        self.run_scenario("takeover")

    def test_native_window_capture_fallback_keeps_isolation_until_finished(self):
        self.run_scenario("capture_fallback")

    def test_queued_confirmation_cannot_authorize_a_superseded_plan(self):
        self.run_scenario("confirmation_change")

    def test_model_failure_after_window_selection_is_failed_not_partial(self):
        self.run_scenario("model_error")

    def test_finish_without_business_result_is_incomplete(self):
        self.run_scenario("empty_result")

    def test_final_capture_failure_does_not_discard_the_business_answer(self):
        self.run_scenario("final_capture_failure")

    def test_completed_subtask_then_model_failure_returns_partial(self):
        self.run_scenario("partial_failure")


if __name__ == "__main__":
    unittest.main()
