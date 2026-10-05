"""Compatibility import for :mod:`openassist.ui.branding`."""
from importlib import import_module
import sys

_module = import_module("openassist.ui.branding")
sys.modules[__name__] = _module
