"""Compatibility for previously injected runner factories."""

from openassist.core.execution import ExecutionCapabilities, TaskResult


class LegacyRunnerExecutor:
    capabilities = ExecutionCapabilities(pause=True, supplement=True, capture_isolation=True)

    def __init__(self, factory, cancel_event):
        self.factory = factory
        self.cancel_event = cancel_event

    async def run(self, request, services):
        runner = self.factory(
            request=request.text, task=request.task_id, keep_console=True,
            event_callback=services.event, input_callback=services.ask,
            confirmation_callback=services.confirm, cancel_event=self.cancel_event(),
            control_callback=services.checkpoint, capture_callback=services.capture,
            supplement_callback=lambda: [item.text for item in services.take_supplements()],
            desktop_callback=services.desktop_access,
            conversation_context=request.conversation_context,
            initial_user_inputs=list(request.initial_inputs),
        )
        return TaskResult.from_legacy(await runner.run())
