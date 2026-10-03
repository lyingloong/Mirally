"""Compatibility import for :mod:`openassist.runtime.speech`."""
from importlib import import_module
import sys

_module = import_module("openassist.runtime.speech")
sys.modules[__name__] = _module
