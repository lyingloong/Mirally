"""Compatibility import for :mod:`openassist.core.task_controller`."""
from importlib import import_module
import sys

_module = import_module("openassist.core.task_controller")
sys.modules[__name__] = _module
