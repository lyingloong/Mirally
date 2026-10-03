"""Compatibility import for :mod:`openassist.ui.shell`."""
from importlib import import_module
import sys

_module = import_module("openassist.ui.shell")
sys.modules[__name__] = _module
