"""Mirally native presentation: windows, companion surfaces and explicit intake.

This module owns presentation geometry and same-origin native gestures. Product
startup, state and transport are supplied by FrameworkShell.
"""
from __future__ import annotations

import base64
import ctypes
import json
import logging
import math
from pathlib import Path
import threading
from urllib.parse import urlsplit

ASSETS = Path(__file__).resolve().parent / "assets"


def _companion_viewport_scale(client_size, viewport, fallback: float) -> float:
    """Use the WebView's actual CSS viewport, which can differ from Form DPI."""
    if not isinstance(viewport, list) or len(viewport) != 2 or any(
            not isinstance(value, (int, float)) or isinstance(value, bool)
            or not math.isfinite(value) or value <= 0 for value in viewport):
        return fallback
    sx, sy = client_size[0] / viewport[0], client_size[1] / viewport[1]
    if not 0.25 <= sx <= 8 or not math.isclose(sx, sy, rel_tol=0.05):
        return fallback
    return sx

def _frame_style(form, style: int | None = None) -> int:
    """Change chrome on the existing HWND; recreating it breaks WebView2 clips."""
    user = ctypes.windll.user32
    get = user.GetWindowLongPtrW
    get.argtypes, get.restype = [ctypes.c_void_p, ctypes.c_int], ctypes.c_ssize_t
    hwnd = int(form.Handle.ToInt64())
    previous = int(get(hwnd, -16))
    if style is not None:
        put = user.SetWindowLongPtrW
        put.argtypes, put.restype = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t], ctypes.c_ssize_t
        put(hwnd, -16, style)
        user.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint]
        user.SetWindowPos(hwnd, None, 0, 0, 0, 0, 0x0037)  # FRAMECHANGED | NOACTIVATE | NOZORDER | NOMOVE | NOSIZE
    return previous

def _frame_composition(form, policy: int | None = None) -> int:
    """DWM's automatic frame otherwise paints outside a custom window region."""
    dwm = ctypes.windll.dwmapi
    hwnd = int(form.Handle.ToInt64())
    get, put = dwm.DwmGetWindowAttribute, dwm.DwmSetWindowAttribute
    get.argtypes = put.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint]
    previous = ctypes.c_int(2)
    get(hwnd, 2, ctypes.byref(previous), 4)
    if policy is not None:
        value = ctypes.c_int(policy)
        put(hwnd, 2, ctypes.byref(value), 4)
    return previous.value

def _capture_affinity(form, exclude: bool) -> None:
    # Supported Windows versions can omit our own floating chrome from capture.
    api = ctypes.windll.user32.SetWindowDisplayAffinity
    api.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    api(int(form.Handle.ToInt64()), 0x11 if exclude else 0)

def _opaque_page_background(value):
    """Accept only opaque RGB channels from the same-origin presentation."""
    if (not isinstance(value, list) or len(value) != 3
            or any(type(channel) is not int or not 0 <= channel <= 255 for channel in value)):
        return None
    from System.Drawing import Color
    return Color.FromArgb(255, *value)

def _client_glass(form, margin: int) -> None:
    """Let transparent WebView pixels composite through the host client area."""
    class Margins(ctypes.Structure):
        _fields_ = [(side, ctypes.c_int) for side in ('left', 'right', 'top', 'bottom')]
    values = Margins(margin, margin, margin, margin)
    api = ctypes.windll.dwmapi.DwmExtendFrameIntoClientArea
    api.argtypes = [ctypes.c_void_p, ctypes.POINTER(Margins)]
    api(int(form.Handle.ToInt64()), ctypes.byref(values))

def startup_html() -> str:
    # Use the canonical vector's production PNG export. The SVG master is over
    # WebView2 NavigateToString's 2 MB limit and expensive to animate as an image.
    logo = base64.b64encode((ASSETS / "mirally-512.png").read_bytes()).decode("ascii")
    return (ASSETS / "control-ui-startup.html").read_text(encoding="utf-8").replace("__MIRALLY_LOGO__", logo)


class DesktopSurface:
    """Native window and companion behavior shared by Mirally's desktop shell."""

    def __init__(self, root: Path):
        self.root = root
        self.window = None
        self.closed = threading.Event()
        self._connecting = threading.Lock()
        self._product_origin = None
        self._workspace = None
        self._system_menu = None
        self._shape_key = None
        self._surface_mode = 'badge'
        self._surface_view = 'compact'
        self._companion_css_scale = 1.0
        self._shape_parts_key = None
        self._workspace_backcolor = None
        self._workspace_glass_margin = 0
        self._glass_active = False
        self._context_request = None
        self._startup_status = None
        self._context_menu_bound = False
        self._startup_visible = threading.Event()

    def _companion_capture_excluded(self) -> bool:
        """The product shell may override its floating-window capture policy."""
        return True

    def _companion_action(self, message: dict) -> None:
        """Window-only messages from our authenticated, same-origin presentation.

        Keep the WebView fixed while its CSS panel grows; clip the host to that
        panel, rather than resizing and recapturing its contents every frame.
        """
        from System.Drawing import Color, Point, Rectangle, Region, Size
        from System.Drawing.Drawing2D import GraphicsPath
        from System.Windows.Forms import FormWindowState, Screen
        form = self.window.native
        action = message.get('action')
        if action == 'dock':
            if self._workspace is not None:
                return
            self._companion_css_scale = _companion_viewport_scale(
                (form.webview.ClientSize.Width, form.webview.ClientSize.Height),
                message.get('viewport'), form.DeviceDpi / 96)
            background = _opaque_page_background(message.get('background'))
            self._workspace = (form.RestoreBounds if form.WindowState != FormWindowState.Normal else form.Bounds,
                               form.WindowState, form.MinimumSize, form.TopMost,
                               background if background is not None else form.webview.DefaultBackgroundColor,
                               _frame_style(form), _frame_composition(form))
            self._workspace_backcolor = background if background is not None else form.BackColor
            # pywebview's shadow setup extends a one-pixel client margin.
            self._workspace_glass_margin = 1 if self.window.shadow else 0
            form.WindowState = FormWindowState.Normal
            form.MinimumSize = Size(0, 0)
            _frame_style(form, _frame_style(form) & ~0x00C40000)
            _frame_composition(form, 1)
            _capture_affinity(form, self._companion_capture_excluded())
            form.webview.DefaultBackgroundColor = Color.Transparent
            form.PerformLayout()
            area = Screen.FromControl(form).WorkingArea
            scale = self._companion_css_scale
            width = min(round(400 * scale), area.Width)
            height = min(round(680 * scale), area.Height - 24)
            form.Bounds = Rectangle(area.Right - width - 2, area.Top + (area.Height - height) // 2, width, height)
            form.ShowInTaskbar = True
            form.TopMost = True
            self._shape_key = None
            self._shape_parts_key = None
            self._initial_companion_shape(message)
        elif action == 'workspace' and self._workspace is not None:
            bounds, state, minimum, topmost, background, style, composition = self._workspace
            self._workspace = None
            self._shape_key = None
            self._shape_parts_key = None
            # Keep the old clip until both renderers have an opaque page-colored
            # backplate. Region/frame/size changes can expose an unpainted WebView
            # frame; the glass clear color and startup color are not page colors.
            current_background = _opaque_page_background(message.get('background'))
            form.BackColor = current_background if current_background is not None else self._workspace_backcolor
            form.webview.DefaultBackgroundColor = current_background if current_background is not None else background
            _client_glass(form, self._workspace_glass_margin)
            self._workspace_backcolor = None
            self._glass_active = False
            # WindowState owns minimization/maximization. Reapplying the saved
            # WS_MAXIMIZE/WS_MINIMIZE bits here makes Windows report that state
            # before the real ShowWindow transition, leaving sidebar bounds
            # in place when WindowState is assigned below.
            _frame_style(form, style & ~0x21000000)
            _frame_composition(form, composition)
            form.MinimumSize = minimum
            form.Bounds = bounds
            form.PerformLayout()
            form.WindowState = state
            form.TopMost = topmost
            previous = form.Region
            form.Region = None
            if previous:
                previous.Dispose()
            _capture_affinity(form, False)
        elif action == 'surface' and self._workspace is not None:
            self._initial_companion_shape(message)
        elif action == 'drag' and self._workspace is not None:
            # Only the webpage's real pointer gesture initiates a native move.
            user = ctypes.windll.user32
            user.ReleaseCapture()
            user.PostMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
            user.PostMessageW(int(form.Handle.ToInt64()), 0x00A1, 2, 0)  # WM_NCLBUTTONDOWN / HTCAPTION
        elif action == 'minimize':
            form.WindowState = FormWindowState.Minimized
        elif action == 'capture-target':
            self._capture_target(message.get('requestId'))
        elif action == 'shape' and self._workspace is not None:
            rect, viewport = message.get('rect'), message.get('viewport')
            if not isinstance(rect, list) or len(rect) != 4 or not isinstance(viewport, list) or len(viewport) != 2:
                return
            if any(not isinstance(v, (int, float)) or not math.isfinite(v) for v in rect + viewport):
                return
            x, y, width, height = rect
            vw, vh = viewport
            if vw <= 0 or vh <= 0 or width <= 0 or height <= 0 or x < 0 or y < 0 or x + width > vw + 1 or y + height > vh + 1:
                return
            view = form.webview
            sx, sy = view.ClientSize.Width / vw, view.ClientSize.Height / vh
            # An old workspace viewport can arrive while docking. WebView zoom
            # and DPI scale uniformly; unequal axes identify stale resize geometry.
            if not math.isclose(sx, sy, rel_tol=0.05):
                return
            self._companion_css_scale = sx
            origin = view.PointToScreen(Point(0, 0))
            ox, oy = origin.X - form.Bounds.X, origin.Y - form.Bounds.Y
            left, top = ox + round(x * sx), oy + round(y * sy)
            right, bottom = ox + round((x + width) * sx), oy + round((y + height) * sy)
            key = (left, top, right, bottom)
            parts = message.get('parts', [[x, y, width, height, min(25, width / 2, height / 2)]])
            if not isinstance(parts, list) or not 1 <= len(parts) <= 3:
                return
            for part in parts:
                if not isinstance(part, list) or len(part) != 5 or any(
                    not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v) for v in part
                ):
                    return
                px, py, pw, ph, corner = part
                if pw <= 0 or ph <= 0 or corner < 0 or px < x-1 or py < y-1 or px+pw > x+width+1 or py+ph > y+height+1:
                    return
            # DSH/UI messages use fractional CSS pixels. Compare the geometry
            # Windows actually paints: tiny sub-pixel layout changes must not
            # rebuild a GDI Region that resolves to exactly the same pixels.
            native_parts = []
            for px, py, pw, ph, corner in parts:
                pl, pt = ox + round(px*sx), oy + round(py*sy)
                pr, pb = ox + round((px+pw)*sx), oy + round((py+ph)*sy)
                # CSS draws the smooth badge edge inside the binary native clip.
                padding = max(1, math.ceil(sx)) if self._surface_mode == 'badge' and pw <= 64 and ph <= 64 else 0
                pl, pt, pr, pb = pl-padding, pt-padding, pr+padding, pb+padding
                radius = min(round(corner*sx)+padding, (pr-pl)//2, (pb-pt)//2)
                if pr <= pl or pb <= pt:
                    return
                native_parts.append((pl, pt, pr, pb, radius))
            parts_key = tuple(native_parts)
            if message.get('view') in {'compact', 'quick', 'menu', 'detail', 'reply'}:
                self._surface_view = message['view']
                self._companion_background()
            if parts_key == self._shape_parts_key or right <= left or bottom <= top:
                return
            region = Region()
            region.MakeEmpty()
            for pl, pt, pr, pb, radius in native_parts:
                diameter = radius*2
                path = GraphicsPath()
                if diameter < 2:
                    path.AddRectangle(Rectangle(pl, pt, pr-pl, pb-pt))
                else:
                    for ax, ay, angle in ((pl, pt, 180), (pr-diameter, pt, 270),
                                           (pr-diameter, pb-diameter, 0), (pl, pb-diameter, 90)):
                        path.AddArc(ax, ay, diameter, diameter, angle, 90)
                path.CloseFigure()
                region.Union(path)
                path.Dispose()
            self._shape_key, self._shape_parts_key = key, parts_key
            previous = form.Region
            form.Region = region
            if previous:
                previous.Dispose()

    def _initial_companion_shape(self, message: dict) -> None:
        form = self.window.native
        requested = message.get('surfaceMode')
        if requested in {'rail', 'badge', 'pet'}:
            self._surface_mode = requested
        requested_view = message.get('view', 'detail' if message.get('expanded') is True else 'compact')
        if requested_view in {'compact', 'quick', 'menu', 'detail', 'reply'}:
            self._surface_view = requested_view
        self._companion_background()
        scale = self._companion_css_scale
        vw, vh = form.webview.ClientSize.Width / scale, form.webview.ClientSize.Height / scale
        anchor_width, anchor_height = {
            'rail': (80, 300), 'badge': (48, 48), 'pet': (80, 96),
        }[self._surface_mode]
        if self._surface_view in {'quick', 'menu', 'reply'}:
            bubble_width, bubble_height = {'quick': (248, 188), 'menu': (220, 304), 'reply': (296, 228)}[self._surface_view]
            width, height = anchor_width + 8 + bubble_width, max(anchor_height, bubble_height)
            parts = [[vw-2-anchor_width, (vh-anchor_height)/2, anchor_width, anchor_height, min(25, anchor_width/2)],
                     [vw-2-width, (vh-bubble_height)/2, bubble_width, bubble_height, 18]]
        else:
            width, height = (376, 560) if self._surface_view == 'detail' else (anchor_width, anchor_height)
            width, height = min(width, vw-4), min(height, vh-32)
            parts = [[vw-2-width, (vh-height)/2, width, height, min(25, width/2, height/2)]]
        # Position-only viewport changes do not trigger ResizeObserver.
        self._companion_action({'action': 'shape', 'expanded': message.get('expanded'),
            'viewport': [vw, vh], 'rect': [vw-2-width, (vh-height)/2, width, height], 'parts': parts})
        form.webview.CoreWebView2.ExecuteScriptAsync(
            "requestAnimationFrame(() => window.__mirallyCompanion?.syncShape?.())"
        )

    def _companion_background(self) -> None:
        from System.Drawing import Color
        transparent = self._surface_view != 'detail'
        if transparent == self._glass_active or self._workspace is None:
            return
        form = self.window.native
        # Black is the transparent clear color for DWM glass, not a color key.
        # Full detail and workspace views keep their original opaque backplate.
        form.BackColor = Color.Black if transparent else self._workspace_backcolor
        _client_glass(form, -1 if transparent else 0)
        self._glass_active = transparent

    def _capture_target(self, request_id) -> None:
        if not isinstance(request_id, str) or not 0 < len(request_id) <= 128:
            return
        from ctypes import wintypes
        from System import Action
        form = self.window.native
        hwnd = int(form.Handle.ToInt64())
        point = wintypes.POINT()
        if not ctypes.windll.user32.GetCursorPos(ctypes.byref(point)):
            return
        self._context_request = request_id
        position = (point.x, point.y)

        def capture():
            from mirally.desktop_context import capture_target, DesktopContextError
            try:
                result = capture_target(own_hwnd=hwnd, screen_point=position)
            except DesktopContextError as error:
                result = {'error': str(error)}
            except Exception:
                result = {'error': '暂时无法读取目标窗口，请重试或添加文件。'}
            result['requestId'] = request_id

            def deliver():
                if self.closed.is_set() or self._context_request != request_id:
                    return
                view = form.webview.CoreWebView2
                parts = urlsplit(str(view.Source))
                if (parts.scheme, parts.netloc) != self._product_origin:
                    return
                view.ExecuteScriptAsync(f"window.__mirallyContext?.receive({json.dumps(result)})")
            try:
                if not self.closed.is_set():
                    form.BeginInvoke(Action(deliver))
            except Exception:
                pass  # Closing the native window retires the pending handoff.
        threading.Thread(target=capture, daemon=True).start()

    def _dock(self) -> None:
        self.window.evaluate_js("window.__mirallyCompanion?.dock()")

    def _dispatch_window_action(self, action) -> None:
        # Existing menu callbacks use workers: evaluate_js/host calls can wait
        # for the UI thread, so they must not run inside a window procedure.
        def run():
            if self.closed.is_set():
                return
            try:
                action()
            except Exception:
                logging.getLogger(__name__).exception("Window menu action failed")
        threading.Thread(target=run, daemon=True).start()

    def _context_menu_requested(self, sender, args) -> None:
        # A hover may expand the anchor before WebView delivers right-click.
        # Let the page hit-test the current companion rather than relying on
        # a particular form/view or the host's asynchronous shape snapshot.
        if self.closed.is_set() or self._workspace is None:
            return
        parts = urlsplit(str(sender.Source))
        if (parts.scheme, parts.netloc) != self._product_origin:
            return
        target = args.ContextMenuTarget
        if target.IsEditable or target.HasSelection:
            return  # Keep native copy/paste and selected-text commands.
        location = args.Location
        size = self.window.native.webview.ClientSize
        if size.Width <= 0 or size.Height <= 0:
            return
        args.Handled = True
        # WebView's event point is in client pixels; DOM hit testing uses CSS
        # pixels, including when the display DPI or browser zoom has changed.
        sender.ExecuteScriptAsync(
            "window.__mirallyCompanion?.requestContextMenu("
            f"{int(location.X)} * innerWidth / {int(size.Width)}, "
            f"{int(location.Y)} * innerHeight / {int(size.Height)})"
        )

    def _browser_ready(self) -> None:
        if self.closed.is_set():
            return
        from System import Action

        def apply():
            view = self.window.native.webview.CoreWebView2
            # The CoreWebView2 instance survives navigation; subscribe once.
            if not self._context_menu_bound:
                view.ContextMenuRequested += self._context_menu_requested
                self._context_menu_bound = True
            settings = view.Settings
            settings.AreDefaultContextMenusEnabled = True
            settings.AreBrowserAcceleratorKeysEnabled = True
            if not self._startup_visible.is_set():
                # Two frames let the startup document reach composition before
                # revealing the same HWND; do not Show/Activate it again.
                view.ExecuteScriptAsync(
                    "requestAnimationFrame(() => requestAnimationFrame(() => "
                    "window.chrome.webview.postMessage({type:'mirally-startup-painted'})))"
                )

        self.window.native.Invoke(Action(apply))
        if self._startup_status:
            self._status(*self._startup_status)
