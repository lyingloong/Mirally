"""Compatibility import for :mod:`openassist.runtime.config`."""
from importlib import import_module
import sys

_module = import_module("openassist.runtime.config")
sys.modules[__name__] = _module
