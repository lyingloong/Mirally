"""Compatibility import for :mod:`mirally.core.conversation`."""
from importlib import import_module
import sys

_module = import_module("mirally.core.conversation")
sys.modules[__name__] = _module
