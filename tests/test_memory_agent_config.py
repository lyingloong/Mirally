"""Native memory configuration must preserve the user's existing runtime."""

from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from tools.configure_memory_agent import memory_patch, memory_revision


ROOT = Path(__file__).resolve().parents[1]


class MemoryAgentConfigTests(unittest.TestCase):
    def test_additive_patch_preserves_existing_memory_agents_and_denials(self):
        config = {"agents": {"defaults": {"model": "authored/model"}},
                  "plugins": {"slots": {"memory": "memory-core"}, "allow": ["existing"],
                              "load": {"paths": ["custom-path"]},
                              "entries": {"mirally-memory": {"config": {"autoRecall": False},
                                          "hooks": {"allowConversationAccess": False, "allowPromptInjection": False}}}},
                  "tools": {"allow": ["read"], "deny": ["mirally-memory__memory_forget"]},
                  "skills": {"load": {"extraDirs": ["custom-skills"], "watch": False}}}
        before = deepcopy(config)
        patch = memory_patch(config, ROOT, ROOT / "history-memory.yaml")
        self.assertEqual(config, before)
        self.assertNotIn("agents", patch)
        self.assertNotIn("models", patch)
        self.assertNotIn("slots", patch["plugins"])
        self.assertNotIn("deny", patch["tools"])
        self.assertFalse(patch["plugins"]["entries"]["mirally-memory"]["config"]["autoRecall"])
        self.assertEqual(patch["plugins"]["entries"]["mirally-memory"]["hooks"],
                         {"allowConversationAccess": False, "allowPromptInjection": False})
        self.assertEqual(patch["plugins"]["allow"], ["existing", "mirally-memory"])
        self.assertEqual(patch["tools"]["allow"], ["read", "mirally-memory__*"])
        self.assertEqual(patch["plugins"]["load"]["paths"][0], "custom-path")

    def test_patch_is_idempotent_and_never_introduces_an_exclusive_plugin_allowlist(self):
        patch = memory_patch({}, ROOT)
        self.assertEqual(memory_patch(patch, ROOT), patch)
        self.assertNotIn("allow", patch["plugins"])
        self.assertNotIn("memory", patch["plugins"])
        self.assertTrue(patch["plugins"]["entries"]["mirally-memory"]["hooks"]["allowConversationAccess"])

    def test_explicit_denials_are_enforced(self):
        for config in ({"plugins": {"enabled": False}},
                       {"plugins": {"deny": ["mirally-memory"]}},
                       {"tools": {"deny": ["mirally-memory__*"]}},
                       {"tools": {"deny": ["bundle-mcp"]}}):
            with self.subTest(config=config), self.assertRaises(ValueError):
                memory_patch(config, ROOT)

    def test_runtime_revision_ignores_captured_data(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "openassist/core/memory/store.py"
            source.parent.mkdir(parents=True)
            source.write_text("revision = 1\n", encoding="utf-8")
            revision = memory_revision(root)
            source.with_name("history.sqlite3").write_bytes(b"private unrelated data")
            self.assertEqual(memory_revision(root), revision)
            source.write_text("revision = 2\n", encoding="utf-8")
            self.assertNotEqual(memory_revision(root), revision)


if __name__ == "__main__":
    unittest.main()
