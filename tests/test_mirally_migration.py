"""Private-state name migration must preserve authored content and credentials."""

from __future__ import annotations

from contextlib import redirect_stdout
import io
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import yaml

from tools import migrate_to_mirally as migration


class MirallyMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "中文 带空格 openassist"
        self.config = self.root / "config/openassist"
        self.config.mkdir(parents=True)
        self.root = self.root.resolve()
        self.config = self.root / "config/openassist"

    def write_json(self, value: dict, base: Path | None = None) -> bytes:
        path = (base or self.config) / "openclaw/openclaw.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        path.write_bytes(raw)
        return raw

    def checkout_scaffold(self, *, crlf: bool = False) -> Path:
        """Model Git placing the new public tree beside ignored legacy state."""
        target = self.root / "config/mirally"
        for relative in migration.PUBLIC_SCAFFOLD_SHA256:
            raw = (migration.ROOT / "config/mirally" / relative).read_bytes()
            if crlf:
                raw = raw.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
            path = target / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        return target

    def tree_bytes(self, directory: Path) -> dict[str, bytes | None]:
        return {path.relative_to(directory).as_posix(): None if path.is_dir() else path.read_bytes()
                for path in directory.rglob("*")}

    def test_shipped_public_scaffold_fingerprints_match_release_files(self) -> None:
        for relative, expected in migration.PUBLIC_SCAFFOLD_SHA256.items():
            with self.subTest(relative=relative):
                raw = (migration.ROOT / "config/mirally" / relative).read_bytes()
                self.assertEqual(hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest(), expected,
                                 "Public template changed: review and update its migration fingerprint.")

    def test_checkout_scaffold_and_legacy_private_state_migrate_without_overwrite(self) -> None:
        original = self.write_json({"apiKey": "private credential", "agents": {
            "defaults": {"workspace": "config/openassist/openclaw/workspace"}}})
        (self.config / "memory.yaml").write_bytes(b"activity_config_path: config/openassist/activity.yaml\r\n")
        private = self.config / "ufo/agents.yaml"
        private.parent.mkdir()
        private.write_bytes(b"API_KEY: private credential\r\n")
        history = self.config / "openclaw/agents/main/sessions/history.jsonl"
        history.parent.mkdir(parents=True)
        history.write_bytes(b'{"message":"unchanged history"}\r\n')
        target = self.checkout_scaffold()
        # A shared byte-identical static file is safe; it is not overwritten.
        (self.config / "execution.yaml").write_bytes((target / "execution.yaml").read_bytes())
        before_source, before_target = self.tree_bytes(self.config), self.tree_bytes(target)
        plan = migration.build_plan(self.root)
        self.assertTrue(plan.move_directory)
        self.assertEqual(self.tree_bytes(self.config), before_source)
        self.assertEqual(self.tree_bytes(target), before_target)
        self.assertFalse((self.root / ".artifacts").exists())
        backup = migration.apply_plan(plan)
        self.assertFalse(self.config.exists())
        self.assertEqual(self.tree_bytes(backup / "checkout-scaffold"), before_target)
        for relative, raw in before_target.items():
            if raw is not None:
                self.assertEqual((target / relative).read_bytes(), raw)
        self.assertEqual((target / "ufo/agents.yaml").read_bytes(), before_source["ufo/agents.yaml"])
        self.assertEqual((target / history.relative_to(self.config)).read_bytes(), before_source[history.relative_to(self.config).as_posix()])
        updated = json.loads((target / "openclaw/openclaw.json").read_bytes())
        self.assertEqual(updated["apiKey"], "private credential")
        self.assertEqual(updated["agents"]["defaults"]["workspace"], "config/mirally/openclaw/workspace")
        self.assertEqual((backup / "openclaw/openclaw.json").read_bytes(), original)
        self.assertIsNone(migration.apply_plan(migration.build_plan(self.root)))

    def test_crlf_checkout_templates_keep_original_bytes(self) -> None:
        target = self.checkout_scaffold(crlf=True)
        before = self.tree_bytes(target)
        (self.config / "private.bin").write_bytes(b"\x00private\xff")
        migration.apply_plan(migration.build_plan(self.root))
        for relative, raw in before.items():
            if raw is not None:
                self.assertEqual((target / relative).read_bytes(), raw)
        self.assertEqual((target / "private.bin").read_bytes(), b"\x00private\xff")

    def test_checkout_destination_rejects_private_unknown_and_modified_public_files(self) -> None:
        target = self.checkout_scaffold()
        before_source = self.tree_bytes(self.config)
        for relative in ("memory.yaml", "ufo/agents.yaml", "unknown.txt", "activity.yaml.template"):
            with self.subTest(relative=relative):
                path = target / relative
                previous = path.read_bytes() if path.exists() else None
                path.write_bytes(b"private authored content must survive")
                before_target = self.tree_bytes(target)
                with self.assertRaisesRegex(migration.MigrationError, "refusing to merge"):
                    migration.build_plan(self.root)
                self.assertEqual(self.tree_bytes(target), before_target)
                self.assertEqual(self.tree_bytes(self.config), before_source)
                if previous is None:
                    path.unlink()
                else:
                    path.write_bytes(previous)
        self.assertFalse((self.root / ".artifacts").exists())

    def test_same_path_different_bytes_or_type_refuses_checkout_merge(self) -> None:
        self.checkout_scaffold()
        path = self.config / "execution.yaml"
        path.write_bytes(b"authored settings\n")
        with self.assertRaisesRegex(migration.MigrationError, "different content or types"):
            migration.build_plan(self.root)
        self.assertEqual(path.read_bytes(), b"authored settings\n")
        path.unlink()
        path.mkdir()
        with self.assertRaisesRegex(migration.MigrationError, "different content or types"):
            migration.build_plan(self.root)
        self.assertTrue(path.is_dir())
        self.assertFalse((self.root / ".artifacts").exists())

    def test_checkout_destination_rejects_unknown_empty_directory(self) -> None:
        target = self.checkout_scaffold()
        (target / "private-state").mkdir()
        with self.assertRaisesRegex(migration.MigrationError, "refusing to merge"):
            migration.build_plan(self.root)
        self.assertTrue((target / "private-state").is_dir())
        self.assertFalse((self.root / ".artifacts").exists())

    def test_preview_rechecks_both_complete_trees_before_apply(self) -> None:
        target = self.checkout_scaffold()
        history = self.config / "history.bin"
        history.write_bytes(b"original")
        for path in (history, target / "execution.yaml", target / "new-private.yaml"):
            with self.subTest(path=path):
                previous = path.read_bytes() if path.exists() else None
                plan = migration.build_plan(self.root)
                path.write_bytes(b"changed after preview")
                with self.assertRaisesRegex(migration.MigrationError, "changed after preview"):
                    migration.apply_plan(plan)
                self.assertTrue(self.config.is_dir())
                self.assertEqual(path.read_bytes(), b"changed after preview")
                self.assertFalse((self.root / ".artifacts").exists())
                if previous is None:
                    path.unlink()
                else:
                    path.write_bytes(previous)

    def test_checkout_merge_failure_restores_both_original_trees(self) -> None:
        self.write_json({"agents": {"defaults": {"workspace": "config/openassist/workspace"}}})
        (self.config / "memory.yaml").write_bytes(b"activity_config_path: config/openassist/activity.yaml\n")
        target = self.checkout_scaffold()
        before_source, before_target = self.tree_bytes(self.config), self.tree_bytes(target)
        plan = migration.build_plan(self.root)
        write = migration._atomic_write

        def fail_second_config(path: Path, data: bytes, mode=None) -> None:
            if path == target / "memory.yaml":
                raise OSError("injected configuration write failure")
            write(path, data, mode)

        with patch.object(migration, "_atomic_write", side_effect=fail_second_config):
            with self.assertRaisesRegex(migration.MigrationError, "prior configuration was restored"):
                migration.apply_plan(plan)
        self.assertEqual(self.tree_bytes(self.config), before_source)
        self.assertEqual(self.tree_bytes(target), before_target)

    def test_partial_public_copy_failure_restores_both_original_trees(self) -> None:
        (self.config / "private.bin").write_bytes(b"private state")
        target = self.checkout_scaffold()
        before_source, before_target = self.tree_bytes(self.config), self.tree_bytes(target)
        plan = migration.build_plan(self.root)
        open_file = Path.open

        def fail_copy(path: Path, mode="r", *args, **kwargs):
            if path == target / "speech.yaml.template" and mode == "xb":
                raise OSError("injected public copy failure")
            return open_file(path, mode, *args, **kwargs)

        with patch.object(Path, "open", fail_copy):
            with self.assertRaisesRegex(migration.MigrationError, "prior configuration was restored"):
                migration.apply_plan(plan)
        self.assertEqual(self.tree_bytes(self.config), before_source)
        self.assertEqual(self.tree_bytes(target), before_target)

    def test_failure_after_staging_checkout_restores_original_destination(self) -> None:
        target = self.checkout_scaffold()
        before_source, before_target = self.tree_bytes(self.config), self.tree_bytes(target)
        plan = migration.build_plan(self.root)
        rename = Path.rename

        def fail_legacy_move(path: Path, destination):
            if path == self.config:
                raise OSError("injected legacy directory move failure")
            return rename(path, destination)

        with patch.object(Path, "rename", fail_legacy_move):
            with self.assertRaisesRegex(migration.MigrationError, "prior configuration was restored"):
                migration.apply_plan(plan)
        self.assertEqual(self.tree_bytes(self.config), before_source)
        self.assertEqual(self.tree_bytes(target), before_target)

    def test_preview_then_apply_preserves_private_state_credentials_and_history(self) -> None:
        private_text = "openassist ${OPENASSIST_SECRET} D:/Code/openassist/config/openassist"
        configuration = {
            "models": {"providers": {"openassist-provider": {
                "apiKey": private_text, "baseUrl": "https://openassist.example/v1", "model": "openassist-model",
            }}},
            "gateway": {"auth": {"token": private_text}},
            "messages": {"history": private_text},
            "agents": {
                "defaults": {"workspace": str(self.config / "openclaw/workspace"), "model": "openassist/model"},
                "entries": {"custom": {"agentDir": "config/openassist/agent", "prompt": private_text}},
                "list": [{"id": "openassist", "workspace": "./config/openassist/custom"}],
            },
            "plugins": {
                "load": {"paths": [str(self.root / "integrations/openclaw/memory")]},
                "entries": {"mirally-memory": {"config": {
                    "root": str(self.root), "configPath": str(self.config / "memory.yaml"),
                    "apiKey": private_text, "description": private_text,
                }}},
            },
            "mcp": {"servers": {"mirally-memory": {
                "command": str(self.root / ".venv/Scripts/python.exe"), "cwd": str(self.root),
                "args": ["-m", "openassist.runtime.memory.mcp_server", "--root", str(self.root),
                         "--config", str(self.config / "memory.yaml"), "--description", private_text],
                "env": {"OPENASSIST_MEMORY_CODE_REVISION": "openassist-revision", "OTHER_SECRET": private_text,
                        "OPENASSIST_ACTIVITY_CONFIG": "config/openassist/activity.yaml"},
            }}},
        }
        original = self.write_json(configuration)
        history = self.config / "openclaw/agents/main/sessions/history.jsonl"
        history.parent.mkdir(parents=True)
        history.write_bytes(private_text.encode("utf-8"))
        credential = self.config / "ufo/agents.yaml"
        credential.parent.mkdir()
        credential.write_bytes(b"API_KEY: openassist-credential\r\n")
        (self.config / "memory.yaml").write_text(
            "# preserve this comment\nactivity_config_path: config/openassist/activity.yaml\n"
            "semantics:\n  model: openassist/model\n", encoding="utf-8"
        )
        plan = migration.build_plan(self.root)
        self.assertTrue(plan.move_directory)
        self.assertEqual((self.config / "openclaw/openclaw.json").read_bytes(), original)
        self.assertFalse((self.root / ".artifacts").exists())
        backup = migration.apply_plan(plan)
        target = self.root / "config/mirally"
        updated = json.loads((target / "openclaw/openclaw.json").read_text(encoding="utf-8"))
        self.assertFalse(self.config.exists())
        self.assertEqual(updated["models"], configuration["models"])
        self.assertEqual(updated["gateway"], configuration["gateway"])
        self.assertEqual(updated["messages"], configuration["messages"])
        self.assertEqual(updated["agents"]["defaults"]["model"], "openassist/model")
        self.assertEqual(updated["agents"]["defaults"]["workspace"], str(target / "openclaw/workspace"))
        self.assertEqual(updated["agents"]["entries"]["custom"]["agentDir"], "config/mirally/agent")
        self.assertEqual(updated["agents"]["list"][0]["workspace"], "./config/mirally/custom")
        plugin = updated["plugins"]["entries"]["mirally-memory"]["config"]
        self.assertEqual(plugin["root"], str(self.root))
        self.assertEqual(plugin["configPath"], str(target / "memory.yaml"))
        self.assertEqual(plugin["apiKey"], private_text)
        self.assertEqual(plugin["description"], private_text)
        server = updated["mcp"]["servers"]["mirally-memory"]
        self.assertEqual(server["args"][1], "mirally.runtime.memory.mcp_server")
        self.assertEqual(server["args"][3], str(self.root))
        self.assertEqual(server["args"][5], str(target / "memory.yaml"))
        self.assertEqual(server["args"][7], private_text)
        self.assertEqual(server["env"], {"MIRALLY_MEMORY_CODE_REVISION": "openassist-revision",
                                        "OTHER_SECRET": private_text,
                                        "MIRALLY_ACTIVITY_CONFIG": "config/mirally/activity.yaml"})
        self.assertEqual((target / history.relative_to(self.config)).read_bytes(), private_text.encode("utf-8"))
        self.assertEqual((target / "ufo/agents.yaml").read_bytes(), b"API_KEY: openassist-credential\r\n")
        self.assertEqual((backup / "openclaw/openclaw.json").read_bytes(), original)
        self.assertIn("# preserve this comment", (target / "memory.yaml").read_text(encoding="utf-8"))

    def test_existing_target_is_never_overwritten_or_merged(self) -> None:
        target = self.root / "config/mirally"
        target.mkdir()
        (self.config / "source").write_text("keep source", encoding="utf-8")
        (target / "target").write_text("keep target", encoding="utf-8")
        with self.assertRaisesRegex(migration.MigrationError, "refusing to merge"):
            migration.build_plan(self.root)
        self.assertEqual((self.config / "source").read_text(), "keep source")
        self.assertEqual((target / "target").read_text(), "keep target")
        self.assertFalse((self.root / ".artifacts").exists())

    def test_repeated_apply_does_not_create_backups_or_change_files(self) -> None:
        self.write_json({"agents": {"defaults": {"workspace": "config/openassist/workspace"}}})
        migration.apply_plan(migration.build_plan(self.root))
        target = self.root / "config/mirally/openclaw/openclaw.json"
        before = target.read_bytes()
        backups = list((self.root / ".artifacts/name-migration").iterdir())
        plan = migration.build_plan(self.root)
        self.assertFalse(plan.move_directory)
        self.assertEqual(plan.changes, ())
        self.assertIsNone(migration.apply_plan(plan))
        self.assertEqual(target.read_bytes(), before)
        self.assertEqual(list((self.root / ".artifacts/name-migration").iterdir()), backups)

    def test_previous_root_updates_already_migrated_paths_and_preserves_external_paths(self) -> None:
        previous = self.root.parent / "旧 openassist 目录"
        target = self.root / "config/mirally"
        self.config.rename(target)
        old_config = previous / "config/mirally"
        outside = str(previous) + "-outside/config/openassist/external"
        original = {
            "agents": {"defaults": {"workspace": str(old_config / "openclaw/workspace")}},
            "plugins": {"load": {"paths": [str(previous / "integrations/openclaw/memory"), outside]},
                        "entries": {"mirally-memory": {"config": {"root": str(previous)}}}},
            "skills": {"load": {"extraDirs": [str(previous / "integrations/openclaw/skills")]}},
            "mcp": {"servers": {"custom": {"command": str(previous / ".venv/Scripts/python.exe"),
                                            "cwd": str(previous),
                                            "args": ["-m", "mirally.runtime.memory.mcp_server",
                                                     "--config=" + str(previous / "config/openassist/memory.yaml")],
                                            "env": {"OPENCLAW_STATE_DIR": str(old_config / "openclaw")}}}},
            "apiKey": str(previous), "history": str(old_config),
        }
        self.write_json(original, target)
        migration.apply_plan(migration.build_plan(self.root, previous))
        updated = json.loads((target / "openclaw/openclaw.json").read_text(encoding="utf-8"))
        self.assertEqual(updated["agents"]["defaults"]["workspace"], str(target / "openclaw/workspace"))
        self.assertEqual(updated["plugins"]["load"]["paths"], [str(self.root / "integrations/openclaw/memory"), outside])
        self.assertEqual(updated["skills"]["load"]["extraDirs"], [str(self.root / "integrations/openclaw/skills")])
        server = updated["mcp"]["servers"]["custom"]
        self.assertEqual(server["command"], str(self.root / ".venv/Scripts/python.exe"))
        self.assertEqual(server["cwd"], str(self.root))
        self.assertEqual(server["args"][-1], "--config=" + str(target / "memory.yaml"))
        self.assertEqual(server["env"]["OPENCLAW_STATE_DIR"], str(target / "openclaw"))
        self.assertEqual(updated["apiKey"], str(previous))
        self.assertEqual(updated["history"], str(old_config))
        self.assertEqual(migration.build_plan(self.root, previous).changes, ())

    def test_yaml_paths_and_exact_speech_environment_reference_preserve_content(self) -> None:
        (self.config / "activity.yaml").write_text(
            "# content is user-authored\r\nstorage:\r\n"
            "  directory: config/openassist/data\r\n  database_path: ${OPENASSIST_STORAGE}/observations.sqlite3\r\n"
            "privacy:\r\n  excluded_windows: [openassist]\r\n", encoding="utf-8", newline=""
        )
        (self.config / "speech.yaml").write_text(
            "api_key: ${OPENASSIST_SPEECH_API_KEY}\nmodel: openassist-model\n"
            "base_url: https://openassist.example\n", encoding="utf-8"
        )
        (self.config / "memory.yaml").write_text(
            "activity_config_path: config/openassist/activity.yaml\nstorage:\n"
            "  projection_database_path: '%OPENASSIST_STORAGE%/projection.sqlite3'\n"
            "semantics:\n  model: openassist-model\n", encoding="utf-8"
        )
        migration.apply_plan(migration.build_plan(self.root))
        target = self.root / "config/mirally"
        activity = yaml.safe_load((target / "activity.yaml").read_bytes())
        self.assertEqual(activity["storage"]["directory"], "config/mirally/data")
        self.assertEqual(activity["storage"]["database_path"], "${MIRALLY_STORAGE}/observations.sqlite3")
        self.assertEqual(activity["privacy"]["excluded_windows"], ["openassist"])
        self.assertIn(b"# content is user-authored\r\n", (target / "activity.yaml").read_bytes())
        speech = yaml.safe_load((target / "speech.yaml").read_bytes())
        self.assertEqual(speech["api_key"], "${MIRALLY_SPEECH_API_KEY}")
        self.assertEqual(speech["model"], "openassist-model")
        self.assertEqual(speech["base_url"], "https://openassist.example")
        memory = yaml.safe_load((target / "memory.yaml").read_bytes())
        self.assertEqual(memory["storage"]["projection_database_path"], "%MIRALLY_STORAGE%/projection.sqlite3")
        self.assertEqual(memory["semantics"]["model"], "openassist-model")

    def test_environment_collision_refuses_to_overwrite_authored_value(self) -> None:
        original = self.write_json({"mcp": {"servers": {"custom": {"env": {
            "OPENASSIST_ROOT": "old value", "MIRALLY_ROOT": "authored value",
        }}}}})
        with self.assertRaisesRegex(migration.MigrationError, "conflicting"):
            migration.build_plan(self.root)
        self.assertEqual((self.config / "openclaw/openclaw.json").read_bytes(), original)
        self.assertFalse((self.root / ".artifacts").exists())

    def test_failure_restores_directory_and_configs_after_backing_up(self) -> None:
        original = self.write_json({"agents": {"defaults": {"workspace": "config/openassist/workspace"}}})
        memory = b"activity_config_path: config/openassist/activity.yaml\n"
        (self.config / "memory.yaml").write_bytes(memory)
        plan = migration.build_plan(self.root)
        write = migration._atomic_write

        def fail_second_config(path: Path, data: bytes, mode=None) -> None:
            if path == self.root / "config/mirally/memory.yaml":
                raise OSError("injected file replacement failure")
            write(path, data, mode)

        with patch.object(migration, "_atomic_write", side_effect=fail_second_config):
            with self.assertRaisesRegex(migration.MigrationError, "restored"):
                migration.apply_plan(plan)
        self.assertTrue(self.config.is_dir())
        self.assertFalse((self.root / "config/mirally").exists())
        self.assertEqual((self.config / "openclaw/openclaw.json").read_bytes(), original)
        self.assertEqual((self.config / "memory.yaml").read_bytes(), memory)
        backups = list((self.root / ".artifacts/name-migration").iterdir())
        self.assertEqual(len(backups), 1)
        self.assertEqual((backups[0] / "openclaw/openclaw.json").read_bytes(), original)

    def test_cli_defaults_to_preview_and_never_prints_secrets(self) -> None:
        original = self.write_json({"apiKey": "DO-NOT-PRINT-openassist-secret", "agents": {
            "defaults": {"workspace": "config/openassist/workspace"},
        }})
        output = io.StringIO()
        with patch("sys.argv", ["migrate_to_mirally.py", "--root", str(self.root)]), redirect_stdout(output):
            self.assertEqual(migration.main(), 0)
        self.assertIn("Preview only", output.getvalue())
        self.assertNotIn("DO-NOT-PRINT", output.getvalue())
        self.assertEqual((self.config / "openclaw/openclaw.json").read_bytes(), original)
        self.assertFalse((self.root / "config/mirally").exists())

    def test_yaml_alias_is_rejected_before_any_shared_scalar_is_changed(self) -> None:
        (self.config / "memory.yaml").write_text(
            "description: &shared config/openassist/activity.yaml\nactivity_config_path: *shared\n",
            encoding="utf-8"
        )
        with self.assertRaisesRegex(migration.MigrationError, "anchors"):
            migration.build_plan(self.root)
        self.assertFalse((self.root / ".artifacts").exists())

    def test_yaml_mapping_alias_cannot_change_an_unrelated_authored_mapping(self) -> None:
        original = b"description: &shared\n  directory: config/openassist/data\nstorage: *shared\n"
        (self.config / "activity.yaml").write_bytes(original)
        with self.assertRaisesRegex(migration.MigrationError, "anchors"):
            migration.build_plan(self.root)
        self.assertEqual((self.config / "activity.yaml").read_bytes(), original)

    def test_literal_speech_credential_with_environment_like_text_is_preserved(self) -> None:
        for credential in ("literal-${OPENASSIST_SPEECH_API_KEY}-credential", "$OPENASSIST_X", "%OPENASSIST_X%"):
            with self.subTest(credential=credential):
                original = ("api_key: '" + credential + "'\n").encode("utf-8")
                (self.config / "speech.yaml").write_bytes(original)
                plan = migration.build_plan(self.root)
                self.assertEqual(plan.changes, ())
                self.assertEqual((self.config / "speech.yaml").read_bytes(), original)

    def test_speech_environment_reference_keeps_whitespace_around_real_placeholder(self) -> None:
        (self.config / "speech.yaml").write_text(
            "api_key: '  ${OPENASSIST_SPEECH_API_KEY}  '\n", encoding="utf-8"
        )
        migration.apply_plan(migration.build_plan(self.root))
        updated = yaml.safe_load((self.root / "config/mirally/speech.yaml").read_bytes())
        self.assertEqual(updated["api_key"], "  ${MIRALLY_SPEECH_API_KEY}  ")

    def test_legacy_top_level_memory_paths_follow_config_and_physical_root_moves(self) -> None:
        (self.config / "memory.yaml").write_text(
            "database_path: config/openassist/data/memory.sqlite3\n"
            "projection_database_path: config/openassist/data/projection.sqlite3\n",
            encoding="utf-8",
        )
        migration.apply_plan(migration.build_plan(self.root))
        path = self.root / "config/mirally/memory.yaml"
        updated = yaml.safe_load(path.read_bytes())
        self.assertEqual(updated["database_path"], "config/mirally/data/memory.sqlite3")
        self.assertEqual(updated["projection_database_path"], "config/mirally/data/projection.sqlite3")
        previous = self.root.parent / "旧根 中文 空格"
        path.write_text(yaml.safe_dump({
            "database_path": str(previous / "config/mirally/data/memory.sqlite3"),
            "projection_database_path": str(previous / "config/openassist/data/projection.sqlite3"),
            "description": str(previous / "config/openassist/user-message"),
        }, allow_unicode=True), encoding="utf-8")
        migration.apply_plan(migration.build_plan(self.root, previous))
        updated = yaml.safe_load(path.read_bytes())
        self.assertEqual(updated["database_path"], str(self.root / "config/mirally/data/memory.sqlite3"))
        self.assertEqual(updated["projection_database_path"], str(self.root / "config/mirally/data/projection.sqlite3"))
        self.assertEqual(updated["description"], str(previous / "config/openassist/user-message"))

    def test_included_configuration_is_rejected_before_moving_or_writing(self) -> None:
        original = self.write_json({"agents": {"defaults": {
            "$include": "config/openassist/agents.json", "workspace": "config/openassist/workspace",
        }}})
        with self.assertRaisesRegex(migration.MigrationError, r"\$include"):
            migration.build_plan(self.root)
        self.assertEqual((self.config / "openclaw/openclaw.json").read_bytes(), original)
        self.assertFalse((self.root / ".artifacts").exists())

    def create_directory_link(self, link: Path, destination: Path) -> None:
        if os.name == "nt":
            completed = subprocess.run(
                [os.environ.get("COMSPEC", "cmd.exe"), "/c", "mklink", "/J", str(link), str(destination)],
                capture_output=True, encoding="utf-8", errors="replace", check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if completed.returncode:
                self.skipTest("The host could not create an isolated Windows directory junction.")
            self.addCleanup(link.rmdir)
        else:
            link.symlink_to(destination, target_is_directory=True)
            self.addCleanup(link.unlink)

    def test_nested_checkout_junction_is_rejected_without_reading_its_target(self) -> None:
        target = self.root / "config/mirally"
        target.mkdir()
        external = self.root.parent / "outside public scaffold"
        external.mkdir()
        (external / "private.yaml").write_bytes(b"external private state")
        self.create_directory_link(target / "ufo", external)
        with self.assertRaisesRegex(migration.MigrationError, "reparse points"):
            migration.build_plan(self.root)
        self.assertEqual((external / "private.yaml").read_bytes(), b"external private state")
        self.assertFalse((self.root / ".artifacts").exists())

    def test_link_added_after_checkout_preview_is_rejected_before_staging(self) -> None:
        self.checkout_scaffold()
        plan = migration.build_plan(self.root)
        external = self.root.parent / "outside late legacy link"
        external.mkdir()
        self.create_directory_link(self.config / "late-link", external)
        with self.assertRaisesRegex(migration.MigrationError, "reparse points"):
            migration.apply_plan(plan)
        self.assertTrue(self.config.is_dir())
        self.assertFalse((self.root / ".artifacts").exists())

    def test_checkout_hardlink_is_rejected_even_if_its_bytes_match_a_template(self) -> None:
        target = self.checkout_scaffold()
        external = self.root.parent / "hardlinked public template"
        path = target / "execution.yaml"
        os.link(path, external)
        self.addCleanup(external.unlink)
        with self.assertRaisesRegex(migration.MigrationError, "without links"):
            migration.build_plan(self.root)
        self.assertEqual(path.read_bytes(), external.read_bytes())
        self.assertFalse((self.root / ".artifacts").exists())

    def test_linked_config_parent_cannot_migrate_external_configuration(self) -> None:
        self.config.rmdir()
        self.config.parent.rmdir()
        external = self.root.parent / "outside maintained config"
        external.mkdir()
        self.create_directory_link(self.root / "config", external)
        original = self.write_json({"agents": {"defaults": {"workspace": "config/openassist/workspace"}}}, external / "openassist")
        with self.assertRaisesRegex(migration.MigrationError, "reparse points"):
            migration.build_plan(self.root)
        self.assertEqual((external / "openassist/openclaw/openclaw.json").read_bytes(), original)
        self.assertFalse((external / "mirally").exists())
        self.assertFalse((self.root / ".artifacts").exists())

    def test_linked_openclaw_parent_is_rejected_before_reading_external_json(self) -> None:
        external = self.root.parent / "outside openclaw state"
        external.mkdir()
        self.create_directory_link(self.config / "openclaw", external)
        original = b'{"agents":{"defaults":{"workspace":"config/openassist/workspace"}}}\n'
        (external / "openclaw.json").write_bytes(original)
        with self.assertRaisesRegex(migration.MigrationError, "reparse points"):
            migration.build_plan(self.root)
        self.assertEqual((external / "openclaw.json").read_bytes(), original)
        self.assertTrue(self.config.exists())
        self.assertFalse((self.root / "config/mirally").exists())

    def test_new_target_junction_is_never_used_as_destination(self) -> None:
        external = self.root.parent / "outside target state"
        external.mkdir()
        self.create_directory_link(self.root / "config/mirally", external)
        with self.assertRaisesRegex(migration.MigrationError, "reparse points"):
            migration.build_plan(self.root)
        self.assertEqual(list(external.iterdir()), [])

    def test_backup_parent_junction_is_rejected_before_active_state_is_moved(self) -> None:
        self.write_json({"agents": {"defaults": {"workspace": "config/openassist/workspace"}}})
        plan = migration.build_plan(self.root)
        external = self.root.parent / "outside backups"
        external.mkdir()
        self.create_directory_link(self.root / ".artifacts", external)
        with self.assertRaisesRegex(migration.MigrationError, "reparse points"):
            migration.apply_plan(plan)
        self.assertTrue(self.config.exists())
        self.assertFalse((self.root / "config/mirally").exists())
        self.assertEqual(list(external.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
