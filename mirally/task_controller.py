"""Compatibility import for :mod:`mirally.core.task_controller`."""
from importlib import import_module
import sys

_module = import_module("mirally.core.task_controller")
sys.modules[__name__] = _module
