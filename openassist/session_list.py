"""Compatibility import for :mod:`openassist.ui.session_list`."""
from importlib import import_module
import sys

_module = import_module("openassist.ui.session_list")
sys.modules[__name__] = _module
