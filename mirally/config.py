"""Compatibility import for :mod:`mirally.runtime.config`."""
from importlib import import_module
import sys

_module = import_module("mirally.runtime.config")
sys.modules[__name__] = _module
