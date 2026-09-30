import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from ufo.llm.openai import OpenAIService


def _config(**agent_overrides):
    agent_config = {
        "API_TYPE": "openai",
        "API_BASE": "https://example.invalid/v1",
        "API_KEY": "test-key",
        "API_MODEL": "qwen3.8-flash",
        "REASONING_MODEL": False,
        **agent_overrides,
    }
    return {
        "appagent": agent_config,
        "MAX_RETRY": 0,
        "TIMEOUT": 10,
        "PRICES": {},
        "TEMPERATURE": 0.0,
        "TOP_P": 0.0,
        "MAX_TOKENS": 2000,
    }


class QwenAdapterTests(unittest.TestCase):
    @patch.object(OpenAIService, "get_openai_client")
    def test_service_initialization_does_not_probe_model(self, get_client):
        client = Mock()
        get_client.return_value = client

        OpenAIService(_config(), "appagent")

        self.assertEqual(client.mock_calls, [])

    @patch.object(OpenAIService, "get_openai_client")
    def test_qwen_effort_and_metrics_are_propagated(self, get_client):
        usage = SimpleNamespace(
            prompt_tokens=120,
            completion_tokens=30,
            model_dump=lambda: {
                "prompt_tokens": 120,
                "completion_tokens": 30,
                "total_tokens": 150,
                "completion_tokens_details": {"reasoning_tokens": 12},
                "prompt_tokens_details": {"cached_tokens": 20},
            },
        )
        response = SimpleNamespace(
            id="response-id",
            usage=usage,
            choices=[SimpleNamespace(message=SimpleNamespace(content="{}"))],
        )
        client = Mock()
        client.chat.completions.create.return_value = response
        get_client.return_value = client
        service = OpenAIService(
            _config(REASONING_EFFORT="low", PRESERVE_THINKING=False),
            "appagent",
        )

        content, cost, metrics = service.chat_completion(
            [{"role": "user", "content": "test"}], 1
        )

        request = client.chat.completions.create.call_args.kwargs
        self.assertEqual(
            request["extra_body"],
            {"reasoning_effort": "low", "preserve_thinking": False},
        )
        self.assertNotIn("max_tokens", request)
        self.assertEqual(content, ["{}"])
        self.assertEqual(cost, 0)
        self.assertEqual(metrics["prompt_tokens"], 120)
        self.assertEqual(metrics["completion_tokens"], 30)
        self.assertEqual(metrics["reasoning_tokens"], 12)
        self.assertEqual(metrics["cached_tokens"], 20)
        self.assertTrue(metrics["enable_thinking"])
        self.assertGreaterEqual(metrics["duration_seconds"], 0)

    @patch.object(OpenAIService, "get_openai_client")
    def test_enable_thinking_is_the_fallback_when_effort_is_unset(self, get_client):
        get_client.return_value = Mock()
        service = OpenAIService(
            _config(ENABLE_THINKING=True, THINKING_BUDGET=256), "appagent"
        )

        self.assertEqual(
            service._provider_extra_body(),
            {"enable_thinking": True, "thinking_budget": 256},
        )

    @patch.object(OpenAIService, "get_openai_client")
    def test_thinking_budget_and_effort_are_mutually_exclusive(self, get_client):
        get_client.return_value = Mock()
        service = OpenAIService(
            _config(REASONING_EFFORT="low", THINKING_BUDGET=256), "appagent"
        )

        with self.assertRaisesRegex(ValueError, "cannot be used"):
            service._provider_extra_body()


if __name__ == "__main__":
    unittest.main()
