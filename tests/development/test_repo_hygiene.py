"""Repository hygiene: AgentTeams runtime state must never become a repo artifact."""
from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SAMPLE = '.agent-teams/mirally-dev-pool/team.json'


@unittest.skipUnless(shutil.which('git'), 'git is required for repository hygiene checks')
class AgentTeamsRuntimeStateTests(unittest.TestCase):
    def git(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(['git', '-C', str(ROOT), *args], capture_output=True, text=True)

    def test_runtime_state_is_ignored(self) -> None:
        result = self.git('check-ignore', '-v', '--no-index', SAMPLE)
        self.assertEqual(result.returncode, 0, f'{SAMPLE} is not ignored: {result.stderr}')
        self.assertIn('.gitignore', result.stdout)

    def test_runtime_state_is_not_tracked(self) -> None:
        result = self.git('ls-files', '.agent-teams')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), '')

    def test_gitignore_documents_the_rule(self) -> None:
        lines = [line.strip() for line in (ROOT / '.gitignore').read_text(encoding='utf-8').splitlines()]
        self.assertIn('**/.agent-teams/', lines)


if __name__ == '__main__':
    unittest.main()
