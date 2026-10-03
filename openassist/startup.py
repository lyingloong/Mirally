"""Compatibility import for :mod:`openassist.ui.startup`."""
from importlib import import_module
import sys

_module = import_module("openassist.ui.startup")
sys.modules[__name__] = _module
