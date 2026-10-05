"""Compatibility import for :mod:`openassist.ui.edge_companion`."""
from importlib import import_module
import sys

_module = import_module("openassist.ui.edge_companion")
sys.modules[__name__] = _module
