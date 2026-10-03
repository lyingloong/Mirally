"""Compatibility import for :mod:`openassist.ui.desktop`."""
from importlib import import_module
import sys

_module = import_module("openassist.ui.desktop")
sys.modules[__name__] = _module
