"""Windows perception and UI-only action execution for the single agent."""

from __future__ import annotations

import platform
import time
from pathlib import Path
from typing import Any, Dict

if platform.system() != "Windows":  # pragma: no cover - Windows-only runtime
    raise RuntimeError("The single-agent computer runtime requires Windows")

import psutil
import pyautogui
import win32con
import win32console
import win32gui
from pywinauto import keyboard

from config.config_loader import get_ufo_config
from ufo.agents.single_agent.schemas import DesktopObservation, SingleAgentAction
from ufo.automator.ui_control.controller import TextTransformer
from ufo.automator.ui_control.inspector import ControlInspectorFacade
from ufo.automator.ui_control.screenshot import PhotographerFacade


class WindowsComputer:
    """Capture desktop state and execute a deliberately small UI action set."""

    BARRIER_ACTIONS = {"focus_window", "click_element", "click", "scroll"}
    _MODIFIER_KEYS = {
        "CTRL": "VK_CONTROL",
        "CONTROL": "VK_CONTROL",
        "SHIFT": "VK_SHIFT",
        "ALT": "VK_MENU",
        "WIN": "VK_LWIN",
        "WINDOWS": "VK_LWIN",
    }
    _SPECIAL_KEYS = {
        "ENTER": "ENTER",
        "RETURN": "ENTER",
        "TAB": "TAB",
        "ESC": "ESC",
        "ESCAPE": "ESC",
        "SPACE": "SPACE",
        "BACKSPACE": "BACKSPACE",
        "DELETE": "DELETE",
        "DEL": "DELETE",
        "HOME": "HOME",
        "END": "END",
        "PGUP": "PGUP",
        "PAGEUP": "PGUP",
        "PGDN": "PGDN",
        "PAGEDOWN": "PGDN",
        "UP": "UP",
        "DOWN": "DOWN",
        "LEFT": "LEFT",
        "RIGHT": "RIGHT",
        **{f"F{i}": f"F{i}" for i in range(1, 13)},
    }

    def __init__(self, max_controls: int = 60) -> None:
        config = get_ufo_config()
        backend = "win32" if "win32" in config.system.control_backend else "uia"
        self._config = config
        self._inspector = ControlInspectorFacade(backend)
        self._photographer = PhotographerFacade()
        self._max_controls = max_controls
        self._window_map: Dict[str, Any] = {}
        self._control_map: Dict[str, Any] = {}
        pyautogui.FAILSAFE = True

    def observe(self, screenshot_path: Path) -> DesktopObservation:
        """Capture top-level windows, foreground controls and one screenshot."""
        window_dict = self._inspector.get_desktop_app_dict(remove_empty=True)
        foreground_handle = int(win32gui.GetForegroundWindow() or 0)
        self._window_map = {}
        windows = []
        active_wrapper = None

        for wrapper in window_dict.values():
            try:
                handle = int(wrapper.handle)
                window_id = f"w:{handle}"
                self._window_map[window_id] = wrapper
                info = self._inspector.get_control_info(
                    wrapper,
                    ["control_text", "control_type", "control_rect"],
                )
                process_name = ""
                try:
                    process_name = psutil.Process(wrapper.process_id()).name()
                except (psutil.Error, OSError):
                    pass
                windows.append(
                    {
                        "id": window_id,
                        "name": info.get("control_text", ""),
                        "type": info.get("control_type", ""),
                        "process": process_name,
                        "rect": info.get("control_rect"),
                        "active": handle == foreground_handle,
                        "minimized": bool(wrapper.is_minimized()),
                    }
                )
                if handle == foreground_handle:
                    active_wrapper = wrapper
            except Exception:
                continue

        active_window = next((item for item in windows if item["active"]), None)
        self._control_map = {}
        display_width, display_height = pyautogui.size()
        controls = (
            self._collect_controls(active_wrapper, display_width, display_height)
            if active_wrapper is not None
            else []
        )

        screenshot_path.parent.mkdir(parents=True, exist_ok=True)
        screenshot = self._photographer.capture_desktop_screen_screenshot(
            all_screens=False,
            save_path=str(screenshot_path),
        )
        screenshot.thumbnail((1600, 1000))
        screenshot.save(screenshot_path)
        return DesktopObservation(
            windows=windows,
            active_window=active_window,
            controls=controls,
            screenshot=self._photographer.encode_image(screenshot),
            screenshot_path=str(screenshot_path),
            display_size={"width": display_width, "height": display_height},
            screenshot_size={"width": screenshot.width, "height": screenshot.height},
            foreground_handle=foreground_handle,
        )

    def _collect_controls(
        self, window: Any, display_width: int, display_height: int
    ) -> list[Dict[str, Any]]:
        try:
            wrappers = self._inspector.find_control_elements_in_descendants(
                window,
                control_type_list=self._config.system.control_list,
            )
        except Exception:
            return []

        def priority(wrapper: Any) -> int:
            try:
                return 1 if wrapper.element_info.control_type == "Text" else 0
            except Exception:
                return 2

        controls = []
        for wrapper in sorted(wrappers, key=priority):
            try:
                info = self._inspector.get_control_info(
                    wrapper,
                    ["control_text", "control_type", "control_rect", "selected"],
                )
                if not self._is_relevant_control(
                    info, display_width, display_height
                ):
                    continue
                element_id = f"c:{len(controls) + 1}"
                name = info.get("control_text", "")
                self._control_map[element_id] = wrapper
                controls.append(
                    {
                        "id": element_id,
                        "name": name[:200],
                        "type": info.get("control_type", ""),
                        "rect": info.get("control_rect"),
                        "selected": info.get("selected"),
                    }
                )
                if len(controls) >= self._max_controls:
                    break
            except Exception:
                continue
        return controls

    @staticmethod
    def _is_relevant_control(
        info: Dict[str, Any], display_width: int, display_height: int
    ) -> bool:
        if not info:
            return False
        rect = info.get("control_rect")
        if not rect or len(rect) != 4:
            return False
        left, top, right, bottom = rect
        if right <= left or bottom <= top:
            return False
        if right <= 0 or bottom <= 0 or left >= display_width or top >= display_height:
            return False
        control_type = info.get("control_type", "")
        name = str(info.get("control_text", "") or "").strip()
        return bool(name) or control_type in {
            "Button",
            "Edit",
            "ComboBox",
            "CheckBox",
            "RadioButton",
            "MenuItem",
            "TabItem",
            "TreeItem",
            "ListItem",
            "Hyperlink",
        }

    def execute(self, action: SingleAgentAction) -> Dict[str, Any]:
        """Execute one validated action without invoking a process or shell."""
        started = time.perf_counter()
        try:
            handler = getattr(self, f"_do_{action.type}")
            detail = handler(action)
            return {
                "ok": True,
                "action": action.type,
                "detail": detail,
                "duration_seconds": round(time.perf_counter() - started, 3),
            }
        except Exception as exc:
            return {
                "ok": False,
                "action": action.type,
                "error": str(exc),
                "duration_seconds": round(time.perf_counter() - started, 3),
            }

    def _do_focus_window(self, action: SingleAgentAction) -> str:
        window = self._window_map.get(action.window_id)
        if window is None:
            raise ValueError(f"Unknown window id: {action.window_id}")
        if window.is_minimized():
            window.restore()
        window.set_focus()
        return f"Focused {action.window_id}"

    def _do_click_element(self, action: SingleAgentAction) -> str:
        control = self._control_map.get(action.element_id)
        if control is None:
            raise ValueError(f"Unknown element id: {action.element_id}")
        control.click_input(button=action.button, double=action.double)
        return f"Clicked {action.element_id}"

    @staticmethod
    def _do_click(action: SingleAgentAction) -> str:
        width, height = pyautogui.size()
        x = min(width - 1, max(0, round(float(action.x) * width)))
        y = min(height - 1, max(0, round(float(action.y) * height)))
        pyautogui.click(x, y, button=action.button, clicks=2 if action.double else 1)
        return f"Clicked ({x}, {y})"

    def _do_type_text(self, action: SingleAgentAction) -> str:
        transformed = self._literal_text(action.text or "")
        keyboard.send_keys(
            transformed,
            pause=self._config.system.input_text_inter_key_pause,
            with_spaces=True,
        )
        return f"Typed {len(action.text or '')} characters"

    @staticmethod
    def _literal_text(text: str) -> str:
        """Escape send_keys syntax so type_text can never become a key command."""
        escaped_braces = "".join(
            "{{}" if character == "{" else "{}}" if character == "}" else character
            for character in text
        )
        return TextTransformer.transform_text(escaped_braces, "all")

    @classmethod
    def _do_press_keys(cls, action: SingleAgentAction) -> str:
        strokes = action.keys or []
        for stroke in strokes:
            keyboard.send_keys(cls._key_stroke(stroke), pause=0.03, with_spaces=True)
            time.sleep(0.05)
        return f"Pressed {', '.join(strokes)}"

    @staticmethod
    def _do_scroll(action: SingleAgentAction) -> str:
        kwargs: Dict[str, int] = {}
        if action.x is not None and action.y is not None:
            width, height = pyautogui.size()
            kwargs = {
                "x": round(action.x * width),
                "y": round(action.y * height),
            }
        pyautogui.scroll(action.amount or 0, **kwargs)
        return f"Scrolled {action.amount}"

    @staticmethod
    def _do_wait(action: SingleAgentAction) -> str:
        seconds = float(action.seconds or 0.1)
        time.sleep(seconds)
        return f"Waited {seconds:.1f} seconds"

    @classmethod
    def _key_stroke(cls, value: str) -> str:
        """Convert explicit key notation such as CTRL+L to send_keys syntax."""
        if not isinstance(value, str) or not value.strip():
            raise ValueError("Key stroke must be a non-empty string")
        parts = [part.strip().upper() for part in value.split("+")]
        modifiers = parts[:-1]
        key = parts[-1]
        if any(modifier not in cls._MODIFIER_KEYS for modifier in modifiers):
            raise ValueError(f"Unsupported key modifier in: {value}")

        if key in cls._SPECIAL_KEYS:
            key_sequence = f"{{{cls._SPECIAL_KEYS[key]}}}"
        elif key in cls._MODIFIER_KEYS and len(parts) == 1:
            key_sequence = f"{{{cls._MODIFIER_KEYS[key]}}}"
        elif len(key) == 1 and key.isascii() and key.isalnum():
            key_sequence = key.lower()
        else:
            raise ValueError(f"Unsupported key: {key}")

        if not modifiers:
            return key_sequence
        down = "".join(f"{{{cls._MODIFIER_KEYS[item]} down}}" for item in modifiers)
        up = "".join(
            f"{{{cls._MODIFIER_KEYS[item]} up}}" for item in reversed(modifiers)
        )
        return f"{down}{key_sequence}{up}"


class ConsoleWindow:
    """Best-effort console minimization so it does not cover the target UI."""

    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled
        self.handle = int(win32console.GetConsoleWindow() or 0)

    def hide(self) -> None:
        if self.enabled and self.handle:
            win32gui.ShowWindow(self.handle, win32con.SW_MINIMIZE)

    def show(self) -> None:
        if self.enabled and self.handle:
            win32gui.ShowWindow(self.handle, win32con.SW_RESTORE)
            try:
                win32gui.SetForegroundWindow(self.handle)
            except Exception:
                pass
