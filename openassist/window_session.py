"""Compatibility import for :mod:`openassist.platform.window_session`."""
from importlib import import_module
import sys

_module = import_module("openassist.platform.window_session")
sys.modules[__name__] = _module
