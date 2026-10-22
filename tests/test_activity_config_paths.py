"""Storage settings must survive relocation and independent launch directories."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

import yaml

from mirally.runtime.activity.config import load_activity_config
from mirally.runtime.memory.config import load_memory_config


class ActivityConfigPathTests(unittest.TestCase):
    def test_missing_profile_defaults_to_content_without_creating_data(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            with patch.dict(os.environ, {'LOCALAPPDATA': str(root / 'user local')}):
                config = load_activity_config(root)
            self.assertTrue(config.enabled)
            self.assertTrue(config.policy.enabled)
            self.assertEqual(config.policy.default_level, 'content')
            self.assertTrue(config.visual_enabled and config.ocr_enabled and config.ui_structure_enabled)
            self.assertTrue(config.input_enabled and config.input_text_enabled and config.clipboard_enabled)
            self.assertTrue(config.policy.exclude_self and config.policy.agent_pause_content)
            self.assertTrue(config.policy.deny_content_on_unknown_desktop)
            self.assertEqual(list(root.iterdir()), [])

    def test_explicit_disabled_and_restricted_scope_are_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            path = root / 'activity.yaml'
            path.write_text('enabled: false\ncollection:\n  default_level: structure\nvisual:\n  scope: foreground\n  enabled: false\nprivacy:\n  allowed_apps: [editor.exe]\n', encoding='utf-8')
            config = load_activity_config(root, path)
            self.assertFalse(config.enabled)
            self.assertEqual(config.policy.default_level,'structure')
            self.assertEqual(config.visual_scope,'foreground')
            self.assertEqual(config.policy.allowed_apps,('editor.exe',))
            self.assertFalse(config.visual_enabled)

    def test_enabled_requires_boolean_instead_of_truthy_strings(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / 'activity.yaml'
            for value in ('false', 'true', 0, None, []):
                with self.subTest(value=value):
                    path.write_text(yaml.safe_dump({'enabled':value}), encoding='utf-8')
                    with self.assertRaisesRegex(ValueError,'must be a boolean'):
                        load_activity_config(root,path)

    def test_relative_storage_follows_relocated_repository_from_other_cwd(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary).resolve()
            original = directory / "original repo"
            config_path = original / "config/mirally/activity.yaml"
            config_path.parent.mkdir(parents=True)
            config_path.write_text(
                "enabled: false\nstorage:\n"
                "  database_path: data/observations.sqlite3\n"
                "  directory: data\n"
                "  knowledge_database_path: data/knowledge.sqlite3\n",
                encoding="utf-8",
            )
            relocated = directory / "迁移 repo"
            shutil.copytree(original, relocated)
            launch_directory = directory / "unrelated launcher"
            launch_directory.mkdir()
            previous = Path.cwd()
            try:
                os.chdir(launch_directory)
                for root in (original, relocated):
                    with self.subTest(root=root.name):
                        config = load_activity_config(root)
                        self.assertEqual(config.database_path, root / "data/observations.sqlite3")
                        self.assertEqual(config.asset_directory, root / "data/assets")
                        self.assertEqual(config.knowledge_database_path, root / "data/knowledge.sqlite3")
                        self.assertFalse((root / "data").exists())
            finally:
                os.chdir(previous)
            self.assertEqual(list(launch_directory.iterdir()), [])

    def test_absolute_and_environment_storage_overrides_are_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            external = root / "external data"
            path = root / "activity.yaml"
            path.write_text(yaml.safe_dump({
                "storage": {
                    "database_path": str(external / "observations.sqlite3"),
                    "directory": "${MIRALLY_TEST_STORAGE}",
                },
            }), encoding="utf-8")
            with patch.dict(os.environ, {"MIRALLY_TEST_STORAGE": str(external)}):
                config = load_activity_config(root, path)
            self.assertEqual(config.database_path, external / "observations.sqlite3")
            self.assertEqual(config.asset_directory, external / "assets")
            self.assertEqual(config.knowledge_database_path, external / "activity-knowledge.sqlite3")

    def test_null_storage_keeps_user_local_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            path = root / "activity.yaml"
            path.write_text(
                "storage:\n  database_path: null\n  directory: null\n"
                "  knowledge_database_path: null\n", encoding="utf-8"
            )
            local_data = root / "user local data"
            with patch.dict(os.environ, {"LOCALAPPDATA": str(local_data)}):
                config = load_activity_config(root, path)
            expected = local_data / "Mirally/activity"
            self.assertEqual(config.database_path, expected / "observations.sqlite3")
            self.assertEqual(config.asset_directory, expected / "assets")
            self.assertEqual(config.knowledge_database_path, expected / "activity-knowledge.sqlite3")
            self.assertFalse(local_data.exists())

    def test_memory_rejects_relative_activity_storage_alias_from_other_cwd(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "repo"
            configs = root / "config/mirally"
            configs.mkdir(parents=True)
            (configs / "activity.yaml").write_text(
                "storage:\n  database_path: data/shared.sqlite3\n", encoding="utf-8"
            )
            (configs / "memory.yaml").write_text(
                "storage:\n  database_path: data/shared.sqlite3\n", encoding="utf-8"
            )
            # The caller's cwd is intentionally outside this synthetic repository.
            with self.assertRaisesRegex(ValueError, "independent databases"):
                load_memory_config(root)


if __name__ == "__main__":
    unittest.main()
