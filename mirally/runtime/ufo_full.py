"""Compatibility entry point; production uses UfoExecutor's typed protocol."""

from __future__ import annotations

from mirally.core.execution import ExecutionRequest, ExecutionServices, ExecutionCancelled, Reobserve, Supplement
from mirally.runtime.ufo.adapter import UfoExecutor


async def run_native_request(request, task, *, event_callback, input_callback,
                             confirmation_callback, cancel_event, control_callback=None,
                             capture_callback=None, supplement_callback=None,
                             desktop_callback=None, conversation_context=None,
                             initial_user_inputs=()):
    """Older integrations must supply isolation/control callbacks for live work."""
    def checkpoint():
        if cancel_event.is_set():
            raise ExecutionCancelled()
        return control_callback() if control_callback else 0

    def capture(stage):
        if capture_callback is None:
            raise RuntimeError("Native capture requires a product isolation callback")
        return capture_callback(stage)

    def desktop(stage, expected_revision=None):
        if stage == "before":
            revision = checkpoint()
            if expected_revision is not None and revision != expected_revision:
                raise Reobserve()
        if desktop_callback is None:
            raise RuntimeError("Native tools require a product desktop control callback")
        return desktop_callback(stage)

    services = ExecutionServices(
        event=event_callback, ask=input_callback, confirm=confirmation_callback,
        checkpoint=checkpoint, capture=capture,
        take_supplements=lambda: [Supplement("", text) for text in (supplement_callback() if supplement_callback else [])],
        desktop_access=desktop, cancelled=cancel_event.is_set, revision=checkpoint,
    )
    result = await UfoExecutor().run(ExecutionRequest(task, request, conversation_context or {},
                                                    tuple(initial_user_inputs)), services)
    return result.as_dict()
