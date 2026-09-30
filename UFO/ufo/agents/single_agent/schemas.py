"""Response and observation schemas for the Windows single agent."""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, model_validator


ActionType = Literal[
    "focus_window",
    "click_element",
    "click",
    "type_text",
    "press_keys",
    "scroll",
    "wait",
]


class SingleAgentAction(BaseModel):
    """One validated desktop action."""

    type: ActionType
    window_id: Optional[str] = None
    element_id: Optional[str] = None
    x: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    y: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    button: Literal["left", "right", "middle"] = "left"
    double: bool = False
    text: Optional[str] = Field(default=None, max_length=2000)
    keys: Optional[List[str]] = Field(default=None, max_length=12)
    amount: Optional[int] = Field(default=None, ge=-20, le=20)
    seconds: Optional[float] = Field(default=None, ge=0.1, le=5.0)
    sensitive: bool = False
    description: Optional[str] = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def validate_action_arguments(self) -> "SingleAgentAction":
        required = {
            "focus_window": ("window_id",),
            "click_element": ("element_id",),
            "click": ("x", "y"),
            "type_text": ("text",),
            "press_keys": ("keys",),
            "scroll": ("amount",),
            "wait": ("seconds",),
        }
        missing = [
            field_name
            for field_name in required[self.type]
            if getattr(self, field_name) is None
        ]
        if missing:
            raise ValueError(
                f"Action '{self.type}' is missing: {', '.join(missing)}"
            )
        if self.type == "press_keys" and not self.keys:
            raise ValueError("press_keys requires at least one key stroke")
        return self


class SingleAgentResponse(BaseModel):
    """One perception-decision response from the model."""

    observation: str = Field(min_length=1)
    thought: str = Field(min_length=1)
    actions: List[SingleAgentAction] = Field(default_factory=list, max_length=3)
    status: Literal["continue", "done", "need_user", "error"]
    message: Optional[str] = None
    question: Optional[str] = None

    @model_validator(mode="after")
    def validate_status(self) -> "SingleAgentResponse":
        if self.status == "continue" and not self.actions:
            raise ValueError("continue requires at least one action")
        if self.status != "continue" and self.actions:
            raise ValueError(f"{self.status} must not include actions")
        if self.status == "need_user" and not self.question:
            raise ValueError("need_user requires a question")
        return self


class DesktopObservation(BaseModel):
    """Serializable state captured before one model decision."""

    windows: List[Dict[str, Any]]
    active_window: Optional[Dict[str, Any]] = None
    controls: List[Dict[str, Any]] = Field(default_factory=list)
    screenshot: str
    screenshot_path: str
    display_size: Dict[str, int] = Field(default_factory=dict)
    screenshot_size: Dict[str, int] = Field(default_factory=dict)
    foreground_handle: int = 0

    def prompt_state(self) -> Dict[str, Any]:
        """Return observation data without the large base64 screenshot."""
        return {
            "windows": self.windows,
            "active_window": self.active_window,
            "controls": self.controls,
            "display_size": self.display_size,
            "screenshot_size": self.screenshot_size,
        }
