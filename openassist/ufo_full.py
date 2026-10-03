"""Compatibility import for :mod:`openassist.runtime.ufo_full`."""
from importlib import import_module
import sys

_module = import_module("openassist.runtime.ufo_full")
sys.modules[__name__] = _module
