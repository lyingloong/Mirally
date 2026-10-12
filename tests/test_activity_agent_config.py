import unittest
from copy import deepcopy
from pathlib import Path

from tools.configure_activity_agent import activity_patch


class ActivityAgentConfigTests(unittest.TestCase):
    def test_patch_configures_local_mcp_without_touching_other_configuration(self):
        config = {"plugins": {"allow": ["existing"], "load": {"paths": ["existing-path"]},
                               "entries": {"existing": {"enabled": True}, "mirally-activity": {"config": {"timeoutMs": 30000}}}},
                  "tools": {"allow": ["read"]}, "agents": {"defaults": {"model": "existing/model"}}}
        original = deepcopy(config)
        patch = activity_patch(config, Path.cwd(), Path("history.yaml"))
        self.assertEqual(config, original)
        self.assertNotIn("agents", patch)
        self.assertNotIn("models", patch)
        server = patch["mcp"]["servers"]["mirally-activity"]
        self.assertEqual(server["args"][-2:], ["--config", str((Path.cwd() / "history.yaml").resolve())])
        self.assertEqual(server["requestTimeoutMs"], 30000)
        self.assertIn("activity_events", server["toolFilter"]["include"])
        self.assertEqual(patch["plugins"]["entries"]["mirally-activity"], {"enabled": False})
        self.assertEqual(patch["tools"]["allow"], ["read", "mirally-activity__*"])
        self.assertNotIn("allow", patch["plugins"])

    def test_mcp_patch_is_idempotent(self):
        patch = activity_patch({}, Path.cwd())
        self.assertIn("mcp", patch)
        self.assertEqual(activity_patch(patch, Path.cwd()), patch)

    def test_legacy_plugin_path_retains_policy_checks(self):
        patch = activity_patch({}, Path.cwd(), legacy_plugin=True)
        self.assertNotIn("allow", patch["plugins"])
        self.assertEqual(patch["tools"]["alsoAllow"], ["mirally-activity"])
        for config in ({"plugins": {"enabled": False}}, {"plugins": {"deny": ["mirally-activity"]}},
                       {"tools": {"deny": ["group:plugins"]}}):
            with self.assertRaises(ValueError):
                activity_patch(config, Path.cwd(), legacy_plugin=True)

    def test_mcp_registration_preserves_explicit_denials(self):
        for config in (
            {"plugins": {"deny": ["mirally-activity"]}},
            {"tools": {"deny": ["bundle-mcp"]}},
            {"tools": {"deny": ["group:plugins"]}},
            {"tools": {"deny": ["mirally-activity__*"]}},
        ):
            with self.assertRaises(ValueError):
                activity_patch(config, Path.cwd())
        # Individual denials remain effective while other Activity tools can be enabled.
        config = {"tools": {"alsoAllow": ["existing"], "deny": ["mirally-activity__activity_evidence"]}}
        patch = activity_patch(config, Path.cwd())
        self.assertEqual(patch["tools"], {"alsoAllow": ["existing", "mirally-activity__*"]})
        self.assertEqual(config["tools"]["deny"], ["mirally-activity__activity_evidence"])

    def test_legacy_selection_disables_the_saved_mcp_surface(self):
        config = {"mcp": {"servers": {"mirally-activity": {"enabled": True}}}}
        patch = activity_patch(config, Path.cwd(), legacy_plugin=True)
        self.assertEqual(patch["mcp"]["servers"]["mirally-activity"], {"enabled": False})
