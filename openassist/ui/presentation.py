"""Public presentation vocabulary. Never display raw model or Python logs."""

from __future__ import annotations

from dataclasses import dataclass

from openassist.core.text import message_text, public_text


@dataclass(frozen=True)
class Theme:
    name: str
    background: str
    surface: str
    text: str
    muted: str
    border: str
    accent: str
    tint: str
    danger: str
    signal: str = ""


DEFAULT_THEME = "sky"

THEMES = {
    "sky": Theme("晴空蓝", "#F7F9FC", "#FFFFFF", "#192B3A", "#627387", "#E1E8EF", "#0878B2", "#EDF5FB", "#AD414B", "#199FE6"),
    "mineral": Theme("矿物白", "#F5F6F3", "#FFFFFF", "#182922", "#67746D", "#DDE3DD", "#426A55", "#E8EFE8", "#AB3E43"),
    "porcelain": Theme("瓷白蓝", "#F5F6FA", "#FFFFFF", "#182438", "#687387", "#DEE3EC", "#275DD7", "#E8EEFC", "#AF3847"),
    "graphite": Theme("石墨黑", "#1C1D20", "#252629", "#F2EFE8", "#ACA9A2", "#3D3E41", "#D5BE98", "#34312C", "#EE9399"),
}

PHASES = {
    "idle": ("准备就绪", "让想法，\n成为下一步。"),
    "preparing": ("准备中", "正在准备\n你的工作空间"),
    "thinking": ("思考中", "正在想一想，\n如何帮到你。"),
    "observing": ("观察中", "正在查看\n当前桌面"),
    "planning": ("规划中", "正在安排\n下一步操作"),
    "waiting_model": ("等待模型回复", "正在等待\n模型回复"),
    "executing": ("执行中", "正在执行\n当前操作"),
    "verifying": ("检查中", "正在检查\n操作结果"),
    "pausing": ("正在暂停", "等待当前步骤\n安全结束"),
    "paused": ("已暂停", "按你的节奏，\n随时继续。"),
    "takeover": ("由你掌控", "电脑现在\n交给你。"),
    "waiting_user": ("需要补充信息", "有一件事\n需要你补充"),
    "waiting_confirmation": ("等待确认", "这一步，\n由你决定。"),
    "stopping": ("正在停止", "等待当前步骤\n安全结束"),
    "completed": ("已完成", "这件事，\n做好了。"),
    "partial": ("部分完成", "已完成一部分，\n还有内容待处理"),
    "incomplete": ("尚未完成", "这件事\n还没有完成"),
    "cancelled": ("已停止", "任务已停止"),
    "failed": ("需要处理", "任务暂时\n无法继续"),
}

ACTION_NAMES = {
    "focus_window": "切换工作窗口", "select_application_window": "切换工作窗口",
    "click_element": "选择目标控件", "click_input": "选择目标控件",
    "click": "点击目标位置", "click_on_coordinates": "点击目标位置",
    "type_text": "输入文字", "fill_text": "填写输入框", "set_edit_text": "填写输入框",
    "press_keys": "执行按键操作", "keyboard_input": "执行按键操作",
    "scroll": "浏览页面内容", "wheel_mouse_input": "浏览页面内容", "wait": "等待应用响应",
}


def action_title(action: str) -> str:
    return "正在" + ACTION_NAMES.get(action, "处理当前操作")


def phase_title(event) -> str:
    return event.get("title") or (
        action_title(event["action"]) if event.get("action") else PHASES[event["phase"]][1]
    )

def blend(start: str, end: str, progress: float) -> str:
    a = tuple(int(start[i:i + 2], 16) for i in (1, 3, 5))
    b = tuple(int(end[i:i + 2], 16) for i in (1, 3, 5))
    return "#" + "".join(f"{round(x + (y - x) * progress):02x}" for x, y in zip(a, b))


def choose_ui_font(root) -> str:
    from tkinter import font
    available = set(font.families(root))
    return next((family for family in ("Noto Sans SC", "Microsoft YaHei UI", "微软雅黑", "Segoe UI") if family in available), "TkDefaultFont")
