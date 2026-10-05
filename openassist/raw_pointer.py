"""Compatibility import for :mod:`openassist.platform.raw_pointer`."""
from importlib import import_module
import sys

_module = import_module("openassist.platform.raw_pointer")
sys.modules[__name__] = _module
