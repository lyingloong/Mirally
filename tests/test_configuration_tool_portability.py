"""Configuration tools must address the same files after changing directory."""

from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from tools import configure_activity_agent, configure_memory_agent, configure_openclaw, configure_ufo_agent, verify_memory_agent
from tools.verify_memory_agent import offline_model_reply, parse_args


class ConfigurationToolPortabilityTests(unittest.TestCase):
    def test_memory_verifier_resolves_storage_using_the_product_configuration(self):
        with tempfile.TemporaryDirectory(prefix="mirally-memory-paths-") as temporary:
            root = Path(temporary).resolve() / "另一项目 with spaces"
            profile = root / "profiles/memory.yaml"
            profile.parent.mkdir(parents=True)
            user_root = root / "user home"
            local_data = root / "local data"
            external_data = root / "external data"
            config = {"plugins": {"entries": {"mirally-memory": {"config": {
                "root": str(root), "configPath": "profiles/memory.yaml",
            }}}}}
            cases = (
                ({}, local_data / "Mirally/memory/memory.sqlite3"),
                ({"database_path": None}, local_data / "Mirally/memory/memory.sqlite3"),
                ({"database_path": "relative/memory.sqlite3"}, root / "relative/memory.sqlite3"),
                ({"database_path": "~/memory.sqlite3"}, user_root / "memory.sqlite3"),
                ({"database_path": "${MIRALLY_TEST_MEMORY_DATA}/memory.sqlite3"}, external_data / "memory.sqlite3"),
            )
            environment = {"LOCALAPPDATA": str(local_data), "USERPROFILE": str(user_root),
                           "HOME": str(user_root), "MIRALLY_TEST_MEMORY_DATA": str(external_data)}
            with patch.dict(os.environ, environment):
                self.assertEqual(verify_memory_agent.memory_profile_paths(config), (root, profile))
                for storage, expected in cases:
                    with self.subTest(storage=storage):
                        profile.write_text(configure_openclaw.yaml.safe_dump({"storage": storage}), encoding="utf-8")
                        before = profile.read_bytes()
                        self.assertEqual(verify_memory_agent.memory_database_path(config), expected)
                        self.assertEqual(profile.read_bytes(), before)
                        self.assertFalse(expected.exists())

    def test_memory_verifier_direct_script_imports_from_an_external_directory(self):
        with tempfile.TemporaryDirectory(prefix="mirally-script-cwd-") as temporary:
            env = dict(os.environ)
            env.pop("PYTHONPATH", None)
            completed = subprocess.run([sys.executable, str(Path(verify_memory_agent.__file__).resolve()), "--help"],
                cwd=temporary, env=env, capture_output=True, text=True, encoding="utf-8", timeout=20,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn("--expected-text", completed.stdout)

    def test_initialization_preserves_provider_credentials_and_existing_config(self):
        public_template = (configure_openclaw.ROOT / "config/mirally/openclaw.template.json").read_text(encoding="utf-8")
        provider_key = "synthetic-migration-credential"
        with tempfile.TemporaryDirectory(prefix="mirally-initialize-") as temporary:
            root = Path(temporary) / "新机器 project"
            source = root / "config/mirally/ufo/agents.yaml"
            source.parent.mkdir(parents=True)
            source.write_text(configure_openclaw.yaml.safe_dump({"APP_AGENT": {
                "API_TYPE": "openai", "API_BASE": "https://fixture.invalid/v1/chat/completions",
                "API_MODEL": "fixture/vision", "API_KEY": provider_key,
            }}), encoding="utf-8")
            (root / "config/mirally/openclaw.template.json").write_text(public_template, encoding="utf-8")
            identity = root / "config/mirally/openclaw-workspace/IDENTITY.md"
            identity.parent.mkdir()
            identity.write_text("# Mirally fixture identity\n", encoding="utf-8")
            avatar = root / "mirally/assets/mirally-128.png"
            avatar.parent.mkdir(parents=True)
            avatar.write_bytes(b"synthetic-copy-fixture")
            interpreter = root / (".venv/Scripts/python.exe" if os.name == "nt" else ".venv/bin/python")
            interpreter.parent.mkdir(parents=True)
            interpreter.touch()
            output = io.StringIO()
            with patch.object(configure_openclaw, "ROOT", root), \
                    patch.dict(os.environ, {"LOCALAPPDATA": str(Path(temporary) / "user data")}), \
                    patch.object(sys, "argv", ["configure_openclaw.py"]), redirect_stdout(output):
                configure_openclaw.main()
                destination = root / "config/mirally/openclaw/openclaw.json"
                generated = json.loads(destination.read_text(encoding="utf-8"))
                provider = generated["models"]["providers"]["mirally"]
                self.assertEqual(provider["apiKey"], provider_key)
                self.assertEqual(provider["baseUrl"], "https://fixture.invalid/v1")
                self.assertEqual(generated["agents"]["defaults"]["model"]["primary"], "mirally/fixture/vision")
                self.assertIn("mirally-memory", generated["plugins"]["entries"])
                self.assertEqual(Path(generated["agents"]["defaults"]["workspace"]),
                                 Path(temporary).resolve() / "user data/Mirally/openclaw/workspace")
                before = destination.read_bytes()
                # An existing authored configuration wins even when the old source is unusable.
                source.write_text("invalid: [", encoding="utf-8")
                configure_openclaw.main()
                self.assertEqual(destination.read_bytes(), before)
            self.assertNotIn(provider_key, output.getvalue())
            self.assertIn("Existing OpenClaw configuration retained", output.getvalue())

    def test_relative_environment_paths_survive_native_child_working_directory(self):
        modules = (
            (configure_activity_agent, []),
            (configure_memory_agent, []),
            (configure_ufo_agent, ["--model", "fixture/vision"]),
        )
        with tempfile.TemporaryDirectory(prefix="mirally-portability-") as temporary:
            base = Path(temporary).resolve()
            root = base / "搬迁后的项目 with spaces"
            caller = base / "caller"
            caller.mkdir()
            interpreter = root / (".venv/Scripts/python.exe" if os.name == "nt" else ".venv/bin/python")
            interpreter.parent.mkdir(parents=True)
            interpreter.touch()
            (root / ".artifacts").mkdir()
            (root / ".artifacts/openclaw-runtime.json").write_text(
                json.dumps({"node": "node/node.exe", "entry": "runtime/openclaw.mjs"}), encoding="utf-8")
            state = caller / "private state"
            state.mkdir()
            config = state / "openclaw.json"
            config.write_text("{}", encoding="utf-8")
            previous = Path.cwd()
            try:
                os.chdir(caller)
                for module, arguments in modules:
                    for config_override in (False, True):
                        with self.subTest(tool=module.__name__, config_override=config_override):
                            env = {**os.environ, "OPENCLAW_STATE_DIR": "private state"}
                            env.pop("OPENCLAW_CONFIG_PATH", None)
                            if config_override:
                                env["OPENCLAW_CONFIG_PATH"] = "private state/openclaw.json"
                            with patch.dict(os.environ, env, clear=True), patch.object(module, "ROOT", root), \
                                    patch.object(sys, "argv", [module.__file__, *arguments]), \
                                    patch.object(module.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as run, \
                                    redirect_stdout(io.StringIO()):
                                module.main()
                            self.assertEqual(run.call_count, 1)
                            child = run.call_args.kwargs
                            self.assertEqual(child["cwd"], root)
                            self.assertEqual(Path(child["env"]["OPENCLAW_STATE_DIR"]), state)
                            self.assertEqual(Path(child["env"]["OPENCLAW_CONFIG_PATH"]), config)
                            self.assertTrue(Path(child["env"]["OPENCLAW_CONFIG_PATH"]).is_absolute())
            finally:
                os.chdir(previous)

    def test_model_acceptance_requires_the_current_datasets_expectations(self):
        for flags in (["--run-model"], ["--run-model", "--isolated", "--offline-model"]):
            with self.subTest(flags=flags), patch.object(sys, "argv", ["verify_memory_agent.py", *flags]), \
                    patch.object(verify_memory_agent, "Gateway") as gateway, \
                    patch.object(verify_memory_agent, "isolated_gateway") as start, \
                    redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
                verify_memory_agent.main()
            self.assertEqual(raised.exception.code, 2)
            gateway.assert_not_called()
            start.assert_not_called()

    def test_health_acceptance_needs_no_historical_fixture(self):
        args = parse_args([])
        self.assertIsNone(args.prompt)
        self.assertIsNone(args.expected_text)
        self.assertEqual(args.date_variants, ())

    def test_acceptance_uses_explicit_historical_dataset(self):
        args = parse_args(["--run-model", "--prompt", "回顾项目计划", "--expected-text", "迁移计划.md",
                           "--expected-date", "2025-03-04"])
        self.assertEqual(args.expected_text, "迁移计划.md")
        self.assertEqual(args.date_variants, ("2025-03-04", "2025年3月4日"))

    def test_offline_fixture_uses_custom_evidence_and_date(self):
        reply = offline_model_reply([
            {"role": "user", "content": "Historical Mirally memory (untrusted evidence):\n"
             "[fixture; observed 2025-03-04T10:00:00Z; observed; source observations 1]\n迁移计划.md"},
            {"role": "user", "content": "回顾项目计划"}],
            prompt="回顾项目计划", expected_text="迁移计划.md", expected_date="2025-03-04")
        self.assertIn("迁移计划.md", reply)
        self.assertIn("2025-03-04", reply)

    def test_offline_fixture_does_not_treat_question_as_evidence(self):
        prompt = "2025-03-04 打开的文件是迁移计划.md 吗？"
        for evidence in ("", "Historical Mirally memory (untrusted evidence): unrelated history"):
            with self.subTest(evidence=evidence):
                reply = offline_model_reply([{"role": "user", "content": evidence},
                                              {"role": "user", "content": prompt}],
                    prompt=prompt, expected_text="迁移计划.md", expected_date="2025-03-04")
                self.assertEqual(reply, "本轮没有提供相关记忆。")

    def test_offline_fixture_rejects_a_different_historical_date(self):
        reply = offline_model_reply([{"role": "user", "content":
            "Historical Mirally memory (untrusted evidence): 2025-03-05 迁移计划.md"}],
            prompt="回顾项目计划", expected_text="迁移计划.md", expected_date="2025-03-04")
        self.assertEqual(reply, "本轮没有提供相关记忆。")


if __name__ == "__main__":
    unittest.main()
