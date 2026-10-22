"""Conversation completion adapters, injected by the application entry point."""

from pathlib import Path

from mirally.runtime.ufo.config import bounded_model_services, load_execution_limits
from mirally.runtime.ufo.locking import UFO_RUNTIME_LOCK


def ufo_completion(messages, *, product_root=None, limits=None):
    """Reuse the configured APP model without constructing a desktop session."""
    product_root = Path(product_root) if product_root else Path(__file__).resolve().parents[2]
    limits = limits or load_execution_limits(product_root)
    if not UFO_RUNTIME_LOCK.acquire(blocking=False):
        raise RuntimeError("另一个请求仍在使用模型或执行器，请等待它完成。")
    try:
        from ufo.llm import AgentType, llm_call

        # A new provider may perform a startup probe before the actual request.
        # Each network call is bounded; cancellation still waits for it to return.
        with bounded_model_services(limits):
            text, _ = llm_call.get_completion(messages, AgentType.APP, False)
        return text
    finally:
        UFO_RUNTIME_LOCK.release()
