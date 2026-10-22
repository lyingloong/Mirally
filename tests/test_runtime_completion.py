import sys
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch

from mirally.runtime.completion import ufo_completion
from mirally.runtime.ufo.config import ExecutionLimits
from mirally.runtime.ufo.locking import UFO_RUNTIME_LOCK


class RuntimeCompletionTests(unittest.TestCase):
    def fake_llm(self, completion):
        module = ModuleType("ufo.llm")
        module.AgentType = SimpleNamespace(APP="APP_AGENT")
        module.llm_call = SimpleNamespace(get_completion=completion)
        return module

    def test_product_limits_and_global_scope_are_used_during_completion(self):
        limits = ExecutionLimits(model_seconds=3)
        def complete(*args):
            self.assertTrue(UFO_RUNTIME_LOCK.locked())
            return "answer", 0
        with patch.dict(sys.modules, {"ufo.llm": self.fake_llm(complete)}), patch(
            "mirally.runtime.completion.bounded_model_services"
        ) as bounded, patch("mirally.runtime.completion.load_execution_limits", return_value=limits) as load:
            self.assertEqual(ufo_completion([], product_root=Path("product")), "answer")
        load.assert_called_once_with(Path("product"))
        bounded.assert_called_once_with(limits)
        self.assertFalse(UFO_RUNTIME_LOCK.locked())

    def test_completion_exception_restores_scope_and_releases_lock(self):
        with patch.dict(sys.modules, {"ufo.llm": self.fake_llm(Mock(side_effect=RuntimeError("network")))}), patch(
            "mirally.runtime.completion.bounded_model_services"
        ) as bounded:
            with self.assertRaisesRegex(RuntimeError, "network"):
                ufo_completion([], limits=ExecutionLimits())
            bounded.return_value.__exit__.assert_called_once()
        self.assertFalse(UFO_RUNTIME_LOCK.locked())

    def test_native_scope_rejects_concurrent_completion_without_calling_model(self):
        completion = Mock()
        UFO_RUNTIME_LOCK.acquire()
        try:
            with patch.dict(sys.modules, {"ufo.llm": self.fake_llm(completion)}):
                with self.assertRaises(RuntimeError):
                    ufo_completion([], limits=ExecutionLimits())
            completion.assert_not_called()
            self.assertTrue(UFO_RUNTIME_LOCK.locked())
        finally:
            UFO_RUNTIME_LOCK.release()


if __name__ == "__main__":
    unittest.main()
