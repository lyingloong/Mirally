"""Compatibility import for :mod:`openassist.core.conversation`."""
from importlib import import_module
import sys

_module = import_module("openassist.core.conversation")
sys.modules[__name__] = _module
