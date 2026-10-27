from pathlib import Path
import tempfile
import unittest
import yaml

from mirally.framework import FrameworkError, FrameworkService
from mirally.framework_workers import WorkerSupervisor


class ModelConnectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "中文 root"
        self.root.mkdir()
        self.home = self.root / "product home"
        self.service = FrameworkService(self.root, self.home)

    def tearDown(self):
        self.service.close()
        self.temp.cleanup()

    def call(self, method, **params):
        return self.service.call(method, params)

    def test_legacy_default_remains_owned_without_copying_credentials(self):
        self.call("settings.update", patch={"model": {"base_url": "https://one.example/v1", "model_id": "old", "api_key": "old-secret"}})
        listed = self.call("model.connections.list")
        self.assertEqual(listed["routes"], {"chat": "default", "background": "default"})
        self.assertEqual(listed["connections"][0]["model"]["model_id"], "old")
        self.assertNotIn("old-secret", str(listed))
        self.assertEqual(self.call("internal.settings.get")["model_connections"], {})

    def test_connections_keep_independent_keys_routes_and_durable_names(self):
        first = self.call("model.connection.save", name="对话", model={"base_url": "https://one.example", "model_id": "first", "api_key": "first-secret"})["connection_id"]
        second = self.call("model.connection.save", name="后台", model={"base_url": "https://two.example", "model_id": "second"})["connection_id"]
        self.assertEqual(self.call("internal.settings.get")["model_connections"][second]["model"]["api_key"], "")
        self.call("model.connection.save", connection_id=first, name="对话改名", model={"max_tokens": 8192})
        self.call("model.routes.set", routes={"chat": first, "background": second})
        public = self.call("settings.get")
        self.assertNotIn("first-secret", str(public))
        self.service.close();self.service = FrameworkService(self.root, self.home)
        self.assertEqual(self.call("internal.settings.get")["model_connections"][first]["model"]["api_key"], "first-secret")
        self.assertEqual(self.call("model.connections.list")["routes"]["background"], second)
        with self.assertRaisesRegex(FrameworkError, "先切换"):
            self.call("model.connection.remove", connection_id=first)
        self.call("model.routes.set", routes={"chat": second})
        self.call("model.connection.remove", connection_id=first)
        self.assertNotIn(first, self.call("settings.get")["model_connections"])

    def test_invalid_mutations_do_not_change_connection_or_inherit_foreign_key(self):
        identity = self.call("model.connection.save", name="原连接", model={"base_url":"https://one.example", "model_id":"one", "api_key":"first-secret"})["connection_id"]
        before = self.call("internal.settings.get")
        for method, params in [
            ("model.connection.save", {"connection_id": identity,"model":{"base_url":"https://different.example"}}),
            ("model.connection.save", {"connection_id":"missing","name":"x","model":{}}),
            ("model.connection.save", {"name":"x","model":{"api_key_configured":True}}),
            ("model.routes.set", {"routes":{"chat":"missing"}}),
            ("settings.update", {"patch":{"model_connections":{}}}),
        ]:
            with self.subTest(method=method), self.assertRaises(FrameworkError):self.call(method,**params)
            self.assertEqual(self.call("internal.settings.get"),before)
        self.call("model.connection.save", connection_id=identity, model={"base_url":"https://different.example", "api_key":""})
        self.assertFalse(self.call("model.connections.list")["connections"][1]["model"]["api_key_configured"])

    def test_background_worker_profile_uses_its_own_selected_model(self):
        identity = self.call("model.connection.save", name="后台", model={"base_url":"https://background.example", "model_id":"background-model"})["connection_id"]
        self.call("settings.update", patch={"model":{"model_id":"chat-model"}})
        self.call("model.routes.set", routes={"background":identity})
        supervisor=WorkerSupervisor(self.root,self.home)
        try:
            _, memory_path=supervisor._profiles(self.call("internal.settings.get"))
            self.assertEqual(yaml.safe_load(memory_path.read_text(encoding="utf-8"))["semantics"]["model"],"background-model")
        finally:supervisor.close()

    def test_web_research_reference_prevents_deleting_its_connection(self):
        identity=self.call("model.connection.save",name="网页",model={"base_url":"https://api.deepseek.com", "model_id":"deepseek-flash"})["connection_id"]
        capabilities=self.call("capabilities.get")
        self.call("capabilities.configure",expected_revision=capabilities["revision"],patch={"web_enabled":True,"search_connection":identity})
        with self.assertRaisesRegex(FrameworkError,"网页研究"):
            self.call("model.connection.remove",connection_id=identity)

    def test_session_selection_and_command_snapshot_survive_restart_and_retry(self):
        identity = self.call("model.connection.save", name="对话", model={"base_url":"https://one.example", "model_id":"first", "api_key":"secret"})["connection_id"]
        self.call("conversation.ensure", conversation_id="one")
        self.call("conversation.bind", conversation_id="one", session_id="session-one")
        current = self.call("conversation.get", conversation_id="one")
        selection = {"connection_id": identity, "model_id": "first"}
        current = self.call("conversation.patch", conversation_id="one", expected_version=current["metadata_version"], patch={"model_selection":selection})
        command = self.call("command.admit", conversation_id="one", command_id="c1", text="hello")
        self.assertEqual(command["model_selection"], selection)
        self.assertNotIn("secret", str(command))
        self.call("conversation.patch", conversation_id="one", expected_version=current["metadata_version"], patch={"model_selection":None})
        self.service.close(); self.service = FrameworkService(self.root, self.home)
        self.assertIsNone(self.call("conversation.get", conversation_id="one")["model_selection"])
        self.assertEqual(self.call("command.admit", conversation_id="one", command_id="c1", text="hello")["model_selection"], selection)
        self.assertEqual(self.call("command.list", conversation_id="one", command_id="c1")["commands"][0]["model_selection"], selection)
        with self.assertRaisesRegex(FrameworkError, "请求"):
            self.call("model.connection.remove", connection_id=identity)
        self.call("command.ack", conversation_id="one", command_id="c1", state="completed")
        self.call("model.connection.remove", connection_id=identity)

    def test_selected_connection_cannot_be_removed(self):
        identity = self.call("model.connection.save", name="对话", model={"base_url":"https://one.example", "model_id":"first"})["connection_id"]
        current = self.call("conversation.ensure", conversation_id="one")
        with self.assertRaisesRegex(FrameworkError, "模型列表"):
            self.call("conversation.patch", conversation_id="one", expected_version=current["metadata_version"], patch={"model_selection":{"connection_id":identity,"model_id":"not-in-catalog"}})
        self.assertIsNone(self.call("conversation.get", conversation_id="one")["model_selection"])
        self.call("conversation.patch", conversation_id="one", expected_version=current["metadata_version"], patch={"model_selection":{"connection_id":identity,"model_id":"first"}})
        with self.assertRaisesRegex(FrameworkError, "会话"):
            self.call("model.connection.remove", connection_id=identity)

    def test_existing_v6_conversations_and_commands_migrate_without_guessing_selection(self):
        self.call("conversation.ensure", conversation_id="one")
        self.call("conversation.bind", conversation_id="one", session_id="session-one")
        self.call("command.admit", conversation_id="one", command_id="old", text="hello")
        self.service.db.execute("ALTER TABLE conversations DROP COLUMN model_selection_json")
        self.service.db.execute("ALTER TABLE commands DROP COLUMN model_selection_json")
        self.service.db.execute("PRAGMA user_version=6")
        self.service.close(); self.service = FrameworkService(self.root, self.home)
        self.assertIsNone(self.call("conversation.get", conversation_id="one")["model_selection"])
        self.assertIsNone(self.call("command.list", conversation_id="one", command_id="old")["commands"][0]["model_selection"])

    def test_catalog_selection_and_default_model_change_are_guarded(self):
        self.call("settings.update", patch={"model":{"base_url":"https://one.example", "model_id":"first"}})
        self.call("model.catalog.save", connection_id="default", model={"model_id":"second", "reasoning_enabled":True})
        current = self.call("conversation.ensure", conversation_id="one")
        current = self.call("conversation.patch", conversation_id="one", expected_version=current["metadata_version"], patch={"model_selection":{"connection_id":"default","model_id":"second"}})
        with self.assertRaisesRegex(FrameworkError, "会话"):
            self.call("model.catalog.remove", connection_id="default", model_id="second")
        with self.assertRaises(FrameworkError):
            self.call("settings.update", patch={"model":{"model_id":"second"}})
        current = self.call("conversation.patch", conversation_id="one", expected_version=current["metadata_version"], patch={"model_selection":{"connection_id":"default","model_id":"first"}})
        with self.assertRaisesRegex(FrameworkError, "会话"):
            self.call("settings.update", patch={"model":{"model_id":"third"}})

    def test_first_creation_selection_is_atomic_and_retries_never_overwrite(self):
        self.call("settings.update", patch={"model":{"base_url":"https://one.example", "model_id":"first"}})
        self.call("model.catalog.save", connection_id="default", model={"model_id":"second"})
        with self.assertRaises(FrameworkError):
            self.call("conversation.ensure", conversation_id="bad", model_selection={"connection_id":"default", "model_id":"missing"})
        with self.assertRaises(FrameworkError):
            self.call("conversation.get", conversation_id="bad")
        first = self.call("conversation.ensure", conversation_id="one", model_selection={"connection_id":"default", "model_id":"first"})
        self.assertEqual(first["model_selection"]["model_id"], "first")
        changed = self.call("conversation.patch", conversation_id="one", expected_version=first["metadata_version"], patch={"model_selection":{"connection_id":"default", "model_id":"second"}})
        replay = self.call("conversation.ensure", conversation_id="one", model_selection={"connection_id":"default", "model_id":"first"})
        self.assertEqual(replay["model_selection"], changed["model_selection"])

    def test_running_command_blocks_parameter_changes_but_unknown_does_not(self):
        self.call("settings.update", patch={"model":{"base_url":"https://one.example", "model_id":"first"}})
        self.call("model.catalog.save", connection_id="default", model={"model_id":"second"})
        current = self.call("conversation.ensure", conversation_id="one")
        self.call("conversation.bind", conversation_id="one", session_id="session-one")
        self.call("conversation.patch", conversation_id="one", expected_version=current["metadata_version"], patch={"model_selection":{"connection_id":"default","model_id":"second"}})
        self.call("command.admit", conversation_id="one", command_id="c1", text="hello")
        for method, params in [
            ("settings.update", {"patch":{"model":{"temperature":0.1}}}),
            ("model.connection.save", {"connection_id":"default","model":{"temperature":0.1}}),
            ("model.catalog.save", {"connection_id":"default","model":{"model_id":"second","temperature":0.1}}),
        ]:
            with self.subTest(method=method), self.assertRaises(FrameworkError):
                self.call(method, **params)
        self.call("command.ack", conversation_id="one", command_id="c1", state="unknown")
        self.call("settings.update", patch={"model":{"temperature":0.1}})
        self.call("model.catalog.save", connection_id="default", model={"model_id":"second","temperature":0.1})
