"""Use the exact host-prepared model route for UFO HostAgent and AppAgent."""
from __future__ import annotations

from contextlib import contextmanager
import copy


@contextmanager
def selected_model(route, report_usage=lambda value: None):
    from config.config_loader import get_ufo_config
    from ufo.llm.base import BaseService
    from openai import OpenAI

    if route.get("api") != "openai-completions" or not route.get("key") or not route.get("model"):
        raise ValueError("UFO requires a prepared OpenAI-compatible API-key route")
    options = route.get("parameters", {})
    if not isinstance(options, dict) or set(options) - {
        "temperature", "top_p", "max_tokens", "reasoning_effort"
    }:
        raise ValueError("Unsupported prepared generation parameters")
    config = get_ufo_config()
    originals = {}
    factory_descriptor = vars(BaseService)["get_service"]
    client = OpenAI(api_key=route["key"], base_url=route["base_url"], max_retries=0, timeout=90)

    class PreparedService(BaseService):
        def __init__(self):
            self.client, self.max_retry = client, 1

        def chat_completion(self, messages, n=1, stream=False, **unused):
            if stream or n != 1:
                raise ValueError("Unsupported UFO completion mode")
            response = self.client.chat.completions.create(
                model=route["model"], messages=messages, **options,
            )
            usage = response.usage
            if usage:
                report_usage({"input": usage.prompt_tokens, "output": usage.completion_tokens,
                              "total": usage.total_tokens})
            if not response.choices or not isinstance(response.choices[0].message.content, str):
                raise ValueError("The selected model did not return UFO response text")
            return [response.choices[0].message.content], 0.0

    service = PreparedService()

    def get_service(name, agent_type, model_name=None):
        value = getattr(agent_type, "value", agent_type)
        if value not in {"HOST_AGENT", "APP_AGENT"}:
            raise ValueError("This UFO run cannot invoke a different agent model")
        if name != "openai":
            raise ValueError("The UFO model route changed during execution")
        return service

    try:
        for name in ("host_agent", "app_agent"):
            original = getattr(config, name)
            originals[name] = original
            replacement = copy.deepcopy(original)
            replacement.api_type = "openai"
            replacement.api_base = route["base_url"]
            replacement.api_key = route["key"]
            replacement.api_model = route["model"]
            replacement.visual_mode = True
            replacement.reasoning_model = bool(route.get("reasoning"))
            setattr(config, name, replacement)
        BaseService.get_service = staticmethod(get_service)
        yield service
    finally:
        BaseService.get_service = factory_descriptor
        for name, original in originals.items():
            setattr(config, name, original)
        client.close()
