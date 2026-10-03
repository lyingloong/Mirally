"""Compatibility import for :mod:`openassist.ui.presentation`."""
from importlib import import_module
import sys

_module = import_module("openassist.ui.presentation")
sys.modules[__name__] = _module
