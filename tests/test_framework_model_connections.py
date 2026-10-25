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
