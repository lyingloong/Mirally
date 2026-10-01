"""Windows perception and UI-only action execution for the single agent."""

from __future__ import annotations

import hashlib
import platform
import secrets
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

    BARRIER_ACTIONS = {"focus_window", "click_element", "click", "scroll", "fill_text"}
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
        self._target_states: Dict[str, Dict[str, Any]] = {}
        self._value_key = secrets.token_bytes(32)
        self._observed_foreground = 0
        pyautogui.FAILSAFE = True

    def observe(self, screenshot_path: Path) -> DesktopObservation:
        """Capture top-level windows, foreground controls and one screenshot."""
        window_dict = self._inspector.get_desktop_app_dict(remove_empty=True)
        foreground_handle = int(win32gui.GetForegroundWindow() or 0)
        self._observed_foreground = foreground_handle
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
        self._target_states = {}
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
            target_states=self._target_states,
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
                control_type = info.get("control_type", "")
                metadata = self._control_metadata(wrapper, control_type)
                if control_type == "Edit":
                    value = self._read_value(wrapper)
                    if value is not None:
                        self._target_states[element_id] = {
                            "empty": value == "",
                            "value_token": hashlib.blake2b(
                                value.encode("utf-8"), key=self._value_key
                            ).hexdigest()
                        }
                controls.append(
                    {
                        "id": element_id,
                        "name": name[:200],
                        "type": info.get("control_type", ""),
                        "rect": info.get("control_rect"),
                        "selected": info.get("selected"),
                        **metadata,
                    }
                )
                if len(controls) >= self._max_controls:
                    break
            except Exception:
                continue
        return controls

    @staticmethod
    def _optional_call(control: Any, method: str) -> Any:
        try:
            return getattr(control, method)()
        except Exception:
            return None

    @classmethod
    def _is_password(cls, control: Any) -> Any:
        try:
            return bool(control.element_info.element.CurrentIsPassword)
        except Exception:
            # Win32 Edit controls expose the ES_PASSWORD window style.
            style = cls._optional_call(control, "style")
            return bool(style & 0x20) if isinstance(style, int) else None

    @classmethod
    def _read_value(cls, control: Any) -> Any:
        if cls._is_password(control) is not False:
            return None
        value = cls._optional_call(control, "get_value")
        if value is None and hasattr(control, "style"):
            value = cls._optional_call(control, "window_text")
        return value if isinstance(value, str) else None

    @classmethod
    def _control_metadata(cls, control: Any, control_type: str) -> Dict[str, Any]:
        stable_id = None
        try:
            runtime_id = control.element_info.runtime_id
            if runtime_id:
                stable_id = "uia:" + str(tuple(runtime_id))
        except Exception:
            pass
        if stable_id is None:
            try:
                automation_id = control.element_info.automation_id
                if automation_id:
                    stable_id = "auto:" + str(automation_id)
            except Exception:
                pass
        result = {
            "stable_id": stable_id,
            "focused": cls._optional_call(control, "has_keyboard_focus"),
            "enabled": cls._optional_call(control, "is_enabled"),
        }
        if control_type == "Edit":
            result["editable"] = cls._optional_call(control, "is_editable")
            result["protected"] = cls._is_password(control)
        return result

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
        missing_target = None
        if action.type in {"click_element", "fill_text"} and action.element_id not in self._control_map:
            missing_target = f"Unknown element id: {action.element_id}"
        elif action.type == "focus_window" and action.window_id not in self._window_map:
            missing_target = f"Unknown window id: {action.window_id}"
        if missing_target:
            return {"ok": False, "action": action.type, "executed": False,
                    "error": missing_target, "duration_seconds": 0.0}
        try:
            handler = getattr(self, f"_do_{action.type}")
            detail = handler(action)
            return {
                "ok": True,
                "action": action.type,
                "executed": True,
                **(detail if isinstance(detail, dict) else {"detail": detail}),
                "duration_seconds": round(time.perf_counter() - started, 3),
            }
        except Exception as exc:
            return {
                "ok": False,
                "action": action.type,
                # An OS input error may have happened after partial dispatch.
                "executed": None,
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

    def _text_target_has_focus(self, control: Any) -> bool:
        return (
            int(win32gui.GetForegroundWindow() or 0) == self._observed_foreground
            and self._optional_call(control, "has_keyboard_focus") is True
        )

    def _do_fill_text(self, action: SingleAgentAction) -> Dict[str, Any]:
        """Replace a known Edit field through keyboard input after focus checks."""

        def reject(reason: str) -> Dict[str, Any]:
            return {
                "ok": False,
                "executed": False,
                "typed": False,
                "verified": False,
                "error": reason,
            }

        control = self._control_map.get(action.element_id)
        if control is None:
            return reject(f"Unknown element id: {action.element_id}")
        if control.element_info.control_type != "Edit":
            return reject("fill_text requires an Edit control from the current observation")
        if int(win32gui.GetForegroundWindow() or 0) != self._observed_foreground:
            return reject("Foreground window changed; re-observe before filling")
        if self._optional_call(control, "is_enabled") is not True:
            return reject("Input field is not enabled")
        if self._optional_call(control, "is_visible") is not True:
            return reject("Input field is not visible")
        if self._optional_call(control, "is_editable") is False:
            return reject("Input field is read-only")
        text = action.text or ""
        if self._read_value(control) == text:
            return {
                "detail": "Field already matches; no input sent",
                "verified": True, "typed": False, "executed": False,
            }

        control.click_input()
        deadline = time.monotonic() + 0.5
        while not self._text_target_has_focus(control):
            if time.monotonic() >= deadline:
                return {
                    "ok": False, "error": "Target focus not verified; no text sent",
                    "verified": False, "typed": False,
                }
            time.sleep(0.02)
        keyboard.send_keys(self._key_stroke("CTRL+A"), pause=0.03)
        if not self._text_target_has_focus(control):
            return {
                "ok": False, "error": "Target lost focus; no text sent",
                "verified": False, "typed": False,
            }
        detail = self._do_type_text(action)
        deadline = time.monotonic() + 0.5
        while True:
            value = self._read_value(control)
            if value is None:
                return {
                    "detail": detail + "; value unavailable, verify the visible UI",
                    "verified": None, "typed": True,
                }
            if value == text:
                return {
                    "detail": detail + "; field content verified",
                    "verified": True, "typed": True,
                }
            if time.monotonic() >= deadline:
                return {
                    "ok": False,
                    "error": "Field content does not match the requested text",
                    "verified": False, "typed": True,
                }
            time.sleep(0.02)

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
