"""Compatibility import for :mod:`openassist.ui.settings_view`."""
from importlib import import_module
import sys

_module = import_module("openassist.ui.settings_view")
sys.modules[__name__] = _module
