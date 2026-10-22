"""Capture/UIA isolation at the product boundary, without a live desktop."""

from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from mirally.core.execution import ExecutionCancelled
from mirally.runtime.ufo.observation import observation_isolation


def control(pid, name="window"):
    return SimpleNamespace(process_id=lambda: pid, element_info=SimpleNamespace(name=name))


class ObservationTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.services = SimpleNamespace(
            capture=lambda stage: self.events.append(stage), cancelled=lambda: False,
            desktop_access=Mock(side_effect=AssertionError("The outer command owns the lease")),
            checkpoint=Mock(side_effect=AssertionError("No second command gate")),
        )
        outer = self

        class Desktop:
            def capture(self):
                outer.events.append("pixels")
                return "image"

        class Control:
            def __init__(self, window):
                self.control = window

            def capture(self):
                return Desktop().capture()

        class Inspector:
            def get_desktop_windows(self):
                return outer.windows

            def find_control_elements_in_descendants(self, window):
                return outer.controls

            def get_control_info_list_of_dict(self, windows):
                return list(windows)

        class State:
            _instance = None

            def __new__(cls):
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                return cls._instance

            def initialize_for_window(self, window):
                self.selected_app_window = window

        self.windows = [control(os.getpid()), control(987654)]
        self.controls = self.windows[:]
        self.module = SimpleNamespace(
            UIServerState=State, ToolError=RuntimeError,
            _verify_id=lambda id, name, items: items[id].element_info.name == name,
        )
        self.targets = (Control, Desktop, Inspector, self.module)
        self.target_patch = patch("mirally.runtime.ufo.observation._native_targets", return_value=self.targets)
        self.target_patch.start()
        self.addCleanup(self.target_patch.stop)

    def test_window_fallback_uses_one_handshake_and_restores_hooks(self):
        Control, Desktop, Inspector, module = self.targets
        previous = Desktop.capture
        with observation_isolation(self.services):
            self.assertEqual(Control(control(987654)).capture(), "image")
        self.assertEqual(self.events, ["before", "pixels", "after"])
        self.assertIs(Desktop.capture, previous)
        self.services.desktop_access.assert_not_called()
        self.services.checkpoint.assert_not_called()

    def test_external_frontend_pid_is_excluded_from_observation_and_selection(self):
        extra = {987654}
        Inspector = self.targets[2]
        with observation_isolation(self.services, lambda: tuple(extra)):
            self.assertEqual(Inspector().get_desktop_windows(), [])
            with self.assertRaises(RuntimeError):
                self.module._verify_id("1", "window", {"1": control(987654)})
            extra.clear()
            self.assertEqual(len(Inspector().get_desktop_windows()), 1)

    def test_failed_before_still_cleans_up_and_never_reads_pixels(self):
        def capture(stage):
            self.events.append(stage)
            if stage == "before":
                raise RuntimeError("UI handshake failed")
        self.services.capture = capture
        with observation_isolation(self.services):
            with self.assertRaisesRegex(RuntimeError, "UI handshake"):
                self.targets[1]().capture()
        self.assertEqual(self.events, ["before", "after"])

    def test_cancel_after_ui_ack_prevents_capture_and_restores_ui(self):
        self.services.cancelled = lambda: "before" in self.events
        with observation_isolation(self.services):
            with self.assertRaises(ExecutionCancelled):
                self.targets[1]().capture()
        self.assertEqual(self.events, ["before", "after"])

    def test_capture_exception_restores_ui_and_next_capture_works(self):
        Desktop = self.targets[1]
        original = Desktop.capture
        count = 0
        def fail_first(instance):
            nonlocal count
            count += 1
            if count == 1:
                raise RuntimeError("capture failed")
            return original(instance)
        Desktop.capture = fail_first
        with observation_isolation(self.services):
            with self.assertRaisesRegex(RuntimeError, "capture failed"):
                Desktop().capture()
            self.assertEqual(Desktop().capture(), "image")
        self.assertEqual(self.events, ["before", "after", "before", "pixels", "after"])

    def test_concurrent_captures_cannot_interleave_ui_handshakes(self):
        entered, release = threading.Event(), threading.Event()
        count_lock = threading.Lock()
        state = {"active": 0, "maximum": 0}
        def capture(stage):
            with count_lock:
                state["active"] += 1 if stage == "before" else -1
                state["maximum"] = max(state["maximum"], state["active"])
            if stage == "before":
                entered.set()
                if not release.wait(2):
                    raise AssertionError("release missing")
        self.services.capture = capture
        with observation_isolation(self.services), ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.targets[1]().capture)
            self.assertTrue(entered.wait(2))
            second = pool.submit(self.targets[1]().capture)
            release.set()
            self.assertEqual(first.result(2), "image")
            self.assertEqual(second.result(2), "image")
        self.assertEqual(state, {"active": 0, "maximum": 1})

    def test_pid_filter_covers_windows_descendants_cache_and_selection(self):
        Control, _, Inspector, module = self.targets
        state = module.UIServerState()
        state.last_app_windows = {"1": self.windows[0]}
        with observation_isolation(self.services):
            self.assertIsNone(state.last_app_windows)
            inspector = Inspector()
            self.assertEqual(inspector.get_desktop_windows(), [self.windows[1]])
            self.assertEqual(inspector.find_control_elements_in_descendants(self.windows[1]), [self.windows[1]])
            cache = {"1": self.windows[0], "2": self.windows[1]}
            self.assertEqual(inspector.get_control_info_list_of_dict(cache), ["2"])
            self.assertEqual(list(cache), ["2"])
            with self.assertRaises(RuntimeError):
                module._verify_id("1", "window", {"1": self.windows[0]})
            with self.assertRaises(RuntimeError):
                state.initialize_for_window(self.windows[0])
            with self.assertRaises(RuntimeError):
                Control(self.windows[0]).capture()
            with self.assertRaises(RuntimeError):
                inspector.find_control_elements_in_descendants(self.windows[0])
            state.initialize_for_window(self.windows[1])
        self.assertIsNone(state.selected_app_window)
        self.assertEqual(self.events, [])

    def test_unreadable_owner_is_not_a_selectable_target(self):
        self.windows.append(SimpleNamespace(process_id=Mock(side_effect=RuntimeError("stale"))))
        with observation_isolation(self.services, excluded_process_ids={987654}):
            self.assertEqual(self.targets[2]().get_desktop_windows(), [])


@unittest.skipUnless(sys.platform == "win32", "Real Windows UFO imports")
class RealObservationImports(unittest.TestCase):
    def test_real_ufo_capture_fallback_and_window_labels_in_isolated_process(self):
        import yaml
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            product = Path(directory)
            config = product / "config" / "mirally" / "ufo"
            config.mkdir(parents=True)
            for source in (root / "config" / "mirally" / "ufo").iterdir():
                if source.name == "agents.yaml" or (source.suffix != ".yaml" and source.name != "agents.yaml.template"):
                    continue
                value = yaml.safe_load(source.read_text(encoding="utf-8"))
                name = "agents.yaml" if source.name.endswith(".template") else source.name
                (config / name).write_text(yaml.safe_dump(value), encoding="utf-8")
            script = textwrap.dedent('''
                import os, sys
                from pathlib import Path
                from types import SimpleNamespace
                from unittest.mock import Mock, patch
                from PIL import Image
                sys.path.insert(0, sys.argv[1])
                from mirally.runtime.config import configure_ufo_config_root
                configure_ufo_config_root(Path.cwd(), ufo_root=Path(sys.argv[1]) / 'UFO')
                from mirally.runtime.native_compat import native_compatibility
                from mirally.runtime.ufo.observation import observation_isolation
                events = []
                services = SimpleNamespace(capture=events.append, cancelled=lambda: False)
                with native_compatibility():
                    from ufo.automator.ui_control import screenshot
                    from ufo.automator.ui_control.inspector import ControlInspectorFacade
                    from ufo.client.mcp.local_servers.ui_mcp_server import UIServerState, _verify_id
                    inspector = ControlInspectorFacade('uia')
                    def window(pid):
                        return SimpleNamespace(process_id=lambda: pid, is_normal=lambda: True,
                            element_info=SimpleNamespace(name='fixture'),
                            capture_as_image=Mock(side_effect=RuntimeError('force fallback')), handle=0)
                    own, target = window(os.getpid()), window(987654)
                    original_capture = screenshot.DesktopPhotographer.capture
                    with patch.object(inspector.backend_strategy, 'get_desktop_windows', return_value=[own, target]), \
                         patch.object(screenshot.ImageGrab, 'grab', return_value=Image.new('RGB', (20, 20), 'blue')):
                        with observation_isolation(services):
                            assert inspector.get_desktop_app_dict() == {'1': target}
                            image = screenshot.ControlPhotographer(target).capture()
                            assert image.size == (20, 20)
                            assert events == ['before', 'after']
                    assert screenshot.DesktopPhotographer.capture is original_capture
                    assert UIServerState().last_app_windows is None
                assert 'galaxy' not in sys.modules
                print('real observation imports OK')
            ''')
            result = subprocess.run(
                [sys.executable, "-c", script, str(root)], cwd=product,
                env=dict(os.environ, PYTHONIOENCODING="utf-8"), capture_output=True,
                text=True, encoding="utf-8", timeout=60,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("real observation imports OK", result.stdout)


if __name__ == "__main__":
    unittest.main()
