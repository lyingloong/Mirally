"""Compatibility import for :mod:`openassist.ui.lyric_view`."""
from importlib import import_module
import sys

_module = import_module("openassist.ui.lyric_view")
sys.modules[__name__] = _module
