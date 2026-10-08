"""Optional, read-only Windows activity sources."""

from .foreground import ForegroundSnapshot, WindowsForegroundSource
from .browser_url import BrowserURLResult, WindowsBrowserURLSource, normalize_browser_url
from .input_metadata import InputBatch, InputMetadataAggregator
from .input_pump import InputPumpStatus, WindowsInputMetadataSource
from .uia_structure import UIAStructureResult, WindowsUIASource
from .ocr import OCRResult, WindowsNativeOCRSource
from .wgc_capture import CaptureResult, VisualSignatureGate, WindowsWindowCaptureSource

__all__ = [
    "ForegroundSnapshot",
    "BrowserURLResult",
    "WindowsBrowserURLSource",
    "normalize_browser_url",
    "InputBatch",
    "InputMetadataAggregator",
    "InputPumpStatus",
    "UIAStructureResult",
    "WindowsForegroundSource",
    "WindowsInputMetadataSource",
    "WindowsUIASource",
    "CaptureResult",
    "OCRResult",
    "VisualSignatureGate",
    "WindowsNativeOCRSource",
    "WindowsWindowCaptureSource",
]
