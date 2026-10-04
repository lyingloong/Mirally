"""Exercise real UFO initialization without credentials or desktop actions."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
UFO_ROOT = ROOT / "UFO"


@unittest.skipUnless(sys.platform == "win32" and (UFO_ROOT / "ufo").is_dir(),
                     "Requires the Windows UFO submodule and dependencies")
class NativeInitializationTests(unittest.TestCase):
    def test_session_initializes_without_galaxy_config(self):
        # A subprocess avoids sharing UFO's module-level config caches with
        # other tests. All config and session logs live in a temporary root.
        with tempfile.TemporaryDirectory() as directory:
            product_root = Path(directory)
            config_dir = product_root / "config" / "openassist" / "ufo"
            config_dir.mkdir(parents=True)

            def absolute_prompts(value):
                if isinstance(value, dict):
                    return {key: absolute_prompts(item) for key, item in value.items()}
                if isinstance(value, list):
                    return [absolute_prompts(item) for item in value]
                if isinstance(value, str) and value.startswith("ufo/"):
                    return (UFO_ROOT / value).as_posix()
                return value

            for source in (ROOT / "config" / "openassist" / "ufo").iterdir():
                if source.suffix != ".yaml" and source.name != "agents.yaml.template":
                    continue
                if source.name == "agents.yaml":
                    continue  # Never read local API credentials.
                value = yaml.safe_load(source.read_text(encoding="utf-8"))
                if source.name == "system.yaml":
                    value.update({
                        "HOSTAGENT_PROMPT": "ufo/prompts/share/base/host_agent.yaml",
                        "HOSTAGENT_EXAMPLE_PROMPT": "ufo/prompts/examples/{mode}/host_agent_example.yaml",
                        "API_PROMPT": "ufo/prompts/share/base/api.yaml",
                    })
                name = "agents.yaml" if source.name.endswith(".template") else source.name
                (config_dir / name).write_text(
                    yaml.safe_dump(absolute_prompts(value)), encoding="utf-8"
                )

            script = textwrap.dedent("""
                import sys
                import asyncio
                import logging
                import threading
                from pathlib import Path
                from unittest.mock import AsyncMock, patch

                sys.path.insert(0, sys.argv[1])
                from openassist.runtime.config import configure_ufo_config_root
                configure_ufo_config_root(Path.cwd(), ufo_root=Path(sys.argv[2]))

                with patch('config.config_loader.get_galaxy_config',
                           side_effect=AssertionError('Galaxy config must not be loaded')):
                    from ufo.module.context import Context, ContextNames
                    from ufo.automator.ui_control.inspector import UIABackendStrategy
                    from openassist.runtime.native_compat import native_compatibility
                    from ufo.client.mcp import local_servers
                    previous_loader = local_servers.load_all_servers
                    previous_default = ContextNames.default_value
                    previous_find = UIABackendStrategy.find_control_elements_in_descendants
                    from ufo.module.sessions.session import Session
                    with native_compatibility():
                        from ufo.client.computer import Computer
                        from ufo.client.mcp.mcp_registry import MCPRegistry
                        expected_namespaces = {
                            'UICollector', 'HostUIExecutor', 'AppUIExecutor',
                            'CommandLineExecutor', 'WordCOMExecutor',
                            'ExcelCOMExecutor', 'PowerPointCOMExecutor', 'PDFReaderExecutor',
                        }
                        assert expected_namespaces <= set(MCPRegistry.list())
                        assert 'ufo.client.mcp.local_servers.constellation_mcp_server' not in sys.modules
                        context = Context()
                        assert context.get(ContextNames.WEAVING_MODE) == 'creation'
                        session = Session('initialization-test', False, 0,
                                          request='test', mode='normal')
                        assert session.context.get(ContextNames.MODE) == 'normal'
                        assert session.context.get(ContextNames.WEAVING_MODE) == 'creation'
                    assert ContextNames.default_value is previous_default
                    assert UIABackendStrategy.find_control_elements_in_descendants is previous_find
                    assert local_servers.load_all_servers is previous_loader
                    assert 'galaxy' not in sys.modules
                    from openassist.runtime.ufo_full import run_native_request
                    from ufo.agents.agent.basic import BasicAgent
                    previous_handle = BasicAgent.handle
                    previous_handlers = logging.getLogger().handlers[:]
                    events = []
                    with patch.object(Session, 'run', new_callable=AsyncMock) as run, \
                         patch.object(Session, 'is_error', return_value=False):
                        result = asyncio.run(run_native_request(
                            'test', 'adapter-initialization-test',
                            event_callback=events.append,
                            input_callback=lambda question: '',
                            confirmation_callback=lambda action, target: False,
                            cancel_event=threading.Event()))
                        run.assert_awaited_once()
                    # A mocked Session.run has no business completion evidence.
                    assert result['status'] == 'incomplete'
                    assert events[-1]['type'] == 'finished'
                    assert BasicAgent.handle is previous_handle
                    assert logging.getLogger().handlers == previous_handlers
                    assert ContextNames.default_value is previous_default
                    assert UIABackendStrategy.find_control_elements_in_descendants is previous_find
                    assert local_servers.load_all_servers is previous_loader
                    assert 'galaxy' not in sys.modules
                print('native initialization OK')
            """)
            environment = dict(os.environ, PYTHONIOENCODING="utf-8")
            result = subprocess.run(
                [sys.executable, "-c", script, str(ROOT), str(UFO_ROOT)],
                cwd=product_root, env=environment, capture_output=True,
                text=True, encoding="utf-8", timeout=60,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("native initialization OK", result.stdout)
            self.assertNotIn("Error loading module", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
