"""Composition root: bind product controllers to runtime implementations."""

from pathlib import Path
from functools import partial

from mirally.core.assistant import Assistant, DemoAssistant
from mirally.core.task_controller import TaskController


def create_executor(product_root: Path):
    from mirally.runtime.ufo.adapter import UfoExecutor

    return UfoExecutor(product_root=product_root)


def create_controller(product_root: Path, *, demo=False, conversation=None, assistant=None, **controller_options):
    if demo:
        return TaskController(demo=True, assistant=assistant or DemoAssistant(),
                              conversation=conversation, **controller_options)
    from mirally.runtime.completion import ufo_completion

    return TaskController(executor=create_executor(product_root),
                          assistant=assistant or Assistant(partial(ufo_completion, product_root=product_root)),
                          conversation=conversation, **controller_options)
