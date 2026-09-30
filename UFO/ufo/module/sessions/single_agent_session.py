"""Session adapter for the Windows single-agent runtime."""

from __future__ import annotations

from typing import Any, Dict, List

from rich.console import Console

from ufo.agents.single_agent.runner import SingleAgentRunner
from ufo.module import interactor

console = Console()


class SingleAgentSession:
    """Run exactly one request without HostAgent/AppAgent hand-offs."""

    def __init__(
        self,
        task: str,
        request: str = "",
        keep_console: bool = False,
        **_: Any,
    ) -> None:
        self.task = task
        self.request = request
        self.keep_console = keep_console

    async def run(self) -> List[Dict[str, Any]]:
        request = self.request or interactor.first_request()
        runner = SingleAgentRunner(
            request=request,
            task=self.task,
            keep_console=self.keep_console,
        )
        result = await runner.run()
        style = "green" if result["status"] == "done" else "red"
        console.print(f"[{style}]{result['message']}[/{style}]")
        console.print(f"[dim]Log: {result['log']}[/dim]")
        return [{"request": request, "result": result}]
