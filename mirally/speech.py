"""Compatibility import for :mod:`mirally.runtime.speech`."""
from importlib import import_module
import sys

_module = import_module("mirally.runtime.speech")
sys.modules[__name__] = _module
