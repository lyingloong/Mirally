import unittest
from copy import deepcopy
from pathlib import Path

from tools.configure_activity_agent import activity_patch


class ActivityAgentConfigTests(unittest.TestCase):
    def test_patch_preserves_existing_plugins_tools_and_model_configuration(self):
        config = {"plugins": {"allow": ["existing"], "load": {"paths": ["existing-path"]},
                               "entries": {"existing": {"enabled": True}, "mirally-activity": {"config": {"timeoutMs": 30000}}}},
                  "tools": {"allow": ["read"]}, "agents": {"defaults": {"model": "existing/model"}}}
        original = deepcopy(config)
        patch = activity_patch(config, Path.cwd(), Path("history.yaml"))
        self.assertEqual(config, original)
        self.assertNotIn("agents", patch)
        self.assertNotIn("models", patch)
        self.assertEqual(patch["plugins"]["allow"], ["existing", "mirally-activity"])
        self.assertEqual(patch["plugins"]["load"]["paths"][0], "existing-path")
        self.assertEqual(patch["tools"]["allow"], ["read", "mirally-activity"])
        self.assertNotIn("alsoAllow", patch["tools"])
        self.assertEqual(patch["plugins"]["entries"]["mirally-activity"]["config"]["timeoutMs"], 30000)

    def test_unrestricted_plugin_policy_stays_unrestricted_and_patch_is_idempotent(self):
        patch = activity_patch({}, Path.cwd())
        self.assertNotIn("allow", patch["plugins"])
        self.assertEqual(patch["tools"]["alsoAllow"], ["mirally-activity"])
        self.assertEqual(activity_patch(patch, Path.cwd()), patch)

    def test_explicit_plugin_denial_is_not_overridden(self):
        for config in ({"plugins": {"enabled": False}}, {"plugins": {"deny": ["mirally-activity"]}},
                       {"tools": {"deny": ["group:plugins"]}}):
            with self.assertRaises(ValueError):
                activity_patch(config, Path.cwd())
