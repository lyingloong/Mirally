"""Compatibility import for :mod:`openassist.core.assistant`."""
from importlib import import_module
import sys

_module = import_module("openassist.core.assistant")
sys.modules[__name__] = _module
