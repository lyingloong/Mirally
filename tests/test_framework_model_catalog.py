from pathlib import Path
import tempfile
import unittest

from mirally.framework import FrameworkError, FrameworkService


class ModelCatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.service = FrameworkService(self.root, self.root / "home")

    def tearDown(self):
        self.service.close()
        self.temp.cleanup()

    def call(self, method, **params):
        return self.service.call(method, params)

    def test_multiple_models_share_credentials_and_roundtrip(self):
        self.call("settings.update", patch={"model": {"base_url": "https://one.example/v1", "model_id": "one", "api_key": "private", "image_enabled": True}})
        self.call("model.catalog.save", connection_id="default", model={"model_id": "two", "context_window": 40000})
        self.call("model.catalog.save", connection_id="default", model={"model_id": "three", "image_enabled": True})
        self.call("model.catalog.save", connection_id="default", model={"model_id": "two", "max_tokens": 4096})
        settings = self.call("settings.get")
        self.assertNotIn("private", str(settings))
        self.assertEqual(self.call("internal.settings.get")["model"]["api_key"], "private")
        self.service.close(); self.service = FrameworkService(self.root, self.root / "home")
        models = self.call("model.connections.list")["connections"][0]["models"]
        self.assertEqual({item["model_id"] for item in models}, {"one", "two", "three"})
        two = next(item for item in models if item["model_id"] == "two")
        self.assertFalse(two["image_enabled"])
        self.assertEqual((two["context_window"], two["max_tokens"]), (40000, 4096))
        self.assertNotIn("api_key", two)
        with self.assertRaises(FrameworkError):
            self.call("model.catalog.save", connection_id="default", model={"model_id": "other", "api_key": "secret"})
        self.call("model.catalog.remove", connection_id="default", model_id="two")
        self.assertEqual(len(self.call("model.connections.list")["connections"][0]["models"]), 2)

    def test_first_model_becomes_default_for_an_empty_connection(self):
        for connection_id in ('default', 'named'):
            with self.subTest(connection_id=connection_id):
                payload = {'base_url': 'https://one.example', 'model_id': '', 'api_key': 'private'}
                if connection_id == 'default':
                    self.call('model.connection.save', connection_id='default', model=payload)
                    identity = 'default'
                else:
                    identity = self.call('model.connection.save', name='新服务', model=payload)['connection_id']
                listed = self.call('model.connections.list')['connections']
                self.assertEqual(next(c for c in listed if c['id'] == identity)['models'], [])
                self.call('model.catalog.save', connection_id=identity, model={'model_id': 'first', 'image_enabled': True})
                settings = self.call('internal.settings.get')
                saved = settings['model'] if identity == 'default' else settings['model_connections'][identity]['model']
                self.assertEqual((saved['model_id'], saved['api_key'], saved['base_url']), ('first', 'private', 'https://one.example'))
                self.assertTrue(saved['image_enabled'])
                self.assertNotIn(identity, settings['model_catalogs'])
                self.call('model.catalog.save', connection_id=identity, model={'model_id': 'second'})
                self.assertEqual(settings['model']['api_key'], 'private')
                self.assertEqual(len(next(c for c in self.call('model.connections.list')['connections'] if c['id'] == identity)['models']), 2)

    def test_active_command_prevents_mutation_but_unknown_does_not(self):
        self.call("settings.update", patch={"model": {"base_url": "https://one.example", "model_id": "one"}})
        self.call("model.catalog.save", connection_id="default", model={"model_id": "two"})
        conversation = self.call("conversation.ensure", conversation_id="one")
        self.call("conversation.bind", conversation_id="one", session_id="session-one")
        self.call("conversation.patch", conversation_id="one", expected_version=conversation["metadata_version"], patch={"model_selection": {"connection_id": "default", "model_id": "two"}})
        self.call("command.admit", conversation_id="one", command_id="c1", text="hello")
        with self.assertRaises(FrameworkError):
            self.call("model.catalog.save", connection_id="default", model={"model_id": "two", "max_tokens": 4000})
        with self.assertRaises(FrameworkError):
            self.call("model.connection.save", connection_id="default", model={"temperature": 0.5})
        self.call("command.ack", conversation_id="one", command_id="c1", state="unknown")
        self.call("model.catalog.save", connection_id="default", model={"model_id": "two", "max_tokens": 4000})
        with self.assertRaises(FrameworkError):
            self.call("model.catalog.remove", connection_id="default", model_id="two")


if __name__ == "__main__":
    unittest.main()
