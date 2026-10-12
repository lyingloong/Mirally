"""Exercise Activity discovery through the pinned official OpenClaw loader."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ActivityGuidanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        runtime_path = ROOT / ".artifacts/openclaw-runtime.json"
        if not runtime_path.is_file():
            raise unittest.SkipTest("Pinned OpenClaw runtime is not installed")
        runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
        node = ROOT / runtime["node"]
        dist = (ROOT / runtime["entry"]).parent / "dist"
        if not node.is_file() or not dist.is_dir():
            raise unittest.SkipTest("Pinned OpenClaw runtime is not installed")
        # These bundled entry points intentionally test the official session
        # snapshot and loader, including same-session watcher reconciliation.
        script = r"""
import { readFile, readdir } from 'node:fs/promises';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
const [dist, skillRoot, state] = process.argv.slice(2);
const modules = await readdir(dist);
async function bundled(prefix) {
  const filename = modules.find(name => name.startsWith(prefix) && name.endsWith('.mjs'));
  if (!filename) throw new Error('Official OpenClaw bundle missing: ' + prefix);
  return import(pathToFileURL(path.join(dist, filename)).href);
}
const { t: prepareSnapshot } = await bundled('session-snapshot-');
const { t: buildSnapshot } = await bundled('workspace-skill-prompt-');
const watcherModule = (await Promise.all(modules
  .filter(name => name.startsWith('refresh-') && name.endsWith('.mjs'))
  .map(async name => ({ name, source: await readFile(path.join(dist, name), 'utf8') }))))
  .find(({ source }) => source.includes('export { closeSkillsWatchers,'));
if (!watcherModule) throw new Error('Official skills watcher export is missing');
const { closeSkillsWatchers: closeWatchers } = await import(pathToFileURL(path.join(dist, watcherModule.name)).href);
const workspaceDir = path.join(state, 'workspace');
const skillName = 'mirally-activity';
const admitted = snapshot => snapshot.skills.some(skill => skill.name === skillName);
const config = {
  agents: { defaults: { skills: [skillName] } },
  mcp: { servers: { 'mirally-activity': { enabled: true } } },
};
try {
  const before = await prepareSnapshot({ workspaceDir, config, agentId: 'main' });
  const enabled = structuredClone(config);
  enabled.skills = { load: { extraDirs: [skillRoot] } };
  const after = await prepareSnapshot({
    workspaceDir, config: enabled, agentId: 'main', existingSnapshot: before.snapshot,
  });
  const mcpDisabled = structuredClone(enabled);
  mcpDisabled.mcp.servers['mirally-activity'].enabled = false;
  const disabledMcpSnapshot = await buildSnapshot(workspaceDir, { config: mcpDisabled, agentId: 'main' });
  const skillDisabled = structuredClone(enabled);
  skillDisabled.skills.entries = { 'mirally-activity': { enabled: false } };
  const disabledSkillSnapshot = await buildSnapshot(workspaceDir, { config: skillDisabled, agentId: 'main' });
  const agentFiltered = structuredClone(enabled);
  agentFiltered.agents.entries = { main: { skills: [] } };
  const filteredSnapshot = await buildSnapshot(workspaceDir, { config: agentFiltered, agentId: 'main' });
  console.log(JSON.stringify({
    beforeAdmitted: admitted(before.snapshot),
    afterAdmitted: admitted(after.snapshot),
    shouldRefresh: after.shouldRefresh,
    snapshotVersionChanged: before.snapshot.version !== after.snapshot.version,
    prompt: after.snapshot.prompt,
    disabledMcpAdmitted: admitted(disabledMcpSnapshot),
    disabledSkillAdmitted: admitted(disabledSkillSnapshot),
    agentFilteredAdmitted: admitted(filteredSnapshot),
  }));
} finally {
  await closeWatchers(true);
}
"""
        with tempfile.TemporaryDirectory(prefix="mirally-guidance-") as temporary:
            state = Path(temporary)
            script_path = state / "native-guidance.mjs"
            script_path.write_text(script, encoding="utf-8")
            env = dict(
                os.environ,
                OPENCLAW_STATE_DIR=str(state),
                OPENCLAW_CONFIG_PATH=str(state / "openclaw.json"),
            )
            result = subprocess.run(
                [str(node), str(script_path), str(dist),
                 str(ROOT / "integrations/openclaw/skills"), str(state)],
                cwd=ROOT, env=env, encoding="utf-8", capture_output=True,
                timeout=60, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if result.returncode:
                raise AssertionError(f"Official skill discovery failed: {result.stderr}")
            cls.discovery = json.loads(result.stdout.splitlines()[-1])

    def test_existing_session_refreshes_when_product_skill_root_is_added(self):
        self.assertFalse(self.discovery["beforeAdmitted"])
        self.assertTrue(self.discovery["afterAdmitted"])
        self.assertTrue(self.discovery["shouldRefresh"])
        self.assertTrue(self.discovery["snapshotVersionChanged"])

    def test_native_prompt_advertises_activity_and_user_triggers(self):
        prompt = self.discovery["prompt"]
        self.assertIn("<name>mirally-activity</name>", prompt)
        self.assertIn("你能看到我之前做了什么吗", prompt)
        self.assertIn("能看到我使用电脑的 activity 吗", prompt)
        self.assertIn("普通问答不调用", prompt)

    def test_disabled_mcp_does_not_advertise_activity(self):
        self.assertFalse(self.discovery["disabledMcpAdmitted"])

    def test_explicit_skill_disable_stays_effective(self):
        self.assertFalse(self.discovery["disabledSkillAdmitted"])

    def test_agent_skill_allowlist_stays_effective(self):
        self.assertFalse(self.discovery["agentFilteredAdmitted"])


if __name__ == "__main__":
    unittest.main()
