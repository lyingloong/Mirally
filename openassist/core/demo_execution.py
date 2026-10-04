"""Offline interaction preview using the same task/services contract."""

import asyncio
import time

from openassist.core.execution import ExecutionCapabilities, TaskResult


class DemoExecutor:
    capabilities = ExecutionCapabilities(pause=True, supplement=True, capture_isolation=True)

    async def run(self, request, services):
        async def wait(seconds):
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                services.checkpoint()
                supplements = services.take_supplements()
                if supplements:
                    services.event({"type": "context_applied", "source": "supplement",
                                    "receipt_ids": [item.receipt_id for item in supplements]})
                await asyncio.sleep(0.04)

        services.event({"type": "phase", "phase": "observing", "summary": "体验演示 · 不会操作你的电脑", "step": 1})
        await wait(1.8)
        services.event({"type": "phase", "phase": "planning", "step": 1})
        await wait(2)
        services.event({"type": "phase", "phase": "executing", "summary": "正在整理本周资料", "step": 2})
        await wait(3)
        answer = services.ask("演示：你想把资料归档到哪个文件夹？")
        services.event({"type": "context_applied", "source": "answer", "receipt_ids": list(services.answer_receipt_ids())})
        await wait(1.5)
        if not services.confirm("移动 12 份演示文件", answer or "项目资料 / 归档"):
            return TaskResult("incomplete", "演示已结束。你拒绝了移动操作，没有修改任何文件。", reason="confirmation_declined")
        services.event({"type": "phase", "phase": "verifying", "step": 3})
        await wait(2)
        return TaskResult("completed", "演示完成。你已经体验了进度、追问和确认流程；没有操作电脑或修改文件。")
