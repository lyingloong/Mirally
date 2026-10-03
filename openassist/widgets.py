"""Compatibility import for :mod:`openassist.ui.widgets`."""
from importlib import import_module
import sys

_module = import_module("openassist.ui.widgets")
sys.modules[__name__] = _module
