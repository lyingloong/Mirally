"""Compatibility import for :mod:`openassist.platform.native_taskbar`."""
from importlib import import_module
import sys

_module = import_module("openassist.platform.native_taskbar")
sys.modules[__name__] = _module
