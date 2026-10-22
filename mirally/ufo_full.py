"""Compatibility import for :mod:`mirally.runtime.ufo_full`."""
from importlib import import_module
import sys

_module = import_module("mirally.runtime.ufo_full")
sys.modules[__name__] = _module
