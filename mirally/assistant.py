"""Compatibility import for :mod:`mirally.core.assistant`."""
from importlib import import_module
import sys

_module = import_module("mirally.core.assistant")
sys.modules[__name__] = _module
