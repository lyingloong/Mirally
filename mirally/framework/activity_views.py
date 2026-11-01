"""Pure presentation of Activity capture scope and validated memory filters."""
from __future__ import annotations

from typing import Any, Mapping

from mirally.core.memory.store import KINDS, PROVENANCES


def validate_memory_search_filters(filters: Mapping[str, Any]) -> dict[str, Any]:
    """Validate optional browser filters before any profile/database access."""
    if set(filters) - {"provenances", "kinds", "start_utc_ms", "end_utc_ms"}:
        raise ValueError("不支持的记忆筛选字段。")
    for field, allowed in (("provenances", PROVENANCES), ("kinds", KINDS)):
        if field not in filters:
            continue
        values = filters[field]
        if (not isinstance(values, list) or len(values) > len(allowed)
                or any(not isinstance(value, str) or value not in allowed for value in values)):
            raise ValueError("记忆来源或类型筛选无效。")
    for field in ("start_utc_ms", "end_utc_ms"):
        if field in filters and (type(filters[field]) is not int or not 0 <= filters[field] <= 2**53 - 1):
            raise ValueError("记忆筛选时间必须是有效的非负整数时间戳。")
    if ("start_utc_ms" in filters and "end_utc_ms" in filters
            and filters["start_utc_ms"] >= filters["end_utc_ms"]):
        raise ValueError("记忆筛选开始时间必须早于结束时间。")
    return dict(filters)


def capture_scope(config: Any) -> list[dict[str, Any]]:
    content = config.policy.default_level == "content"
    structure = config.policy.default_level in {"structure", "content"}
    return [{"label": label, "enabled": bool(enabled)} for label, enabled in (
        ("前台窗口元数据", True), ("窗口标题", config.window_titles_enabled),
        ("浏览器地址", config.browser_urls_enabled), ("输入操作元数据", config.input_enabled),
        ("UIA 结构", config.ui_structure_enabled and structure),
        ("屏幕截图（所有显示器）" if config.visual_scope == "monitors" else "屏幕截图（前台窗口）",
         config.visual_enabled and content),
        ("OCR 正文", config.visual_enabled and config.ocr_enabled and content),
        ("UIA 正文", config.ui_structure_enabled and config.uia_content_enabled and content),
        ("输入文本", config.ui_structure_enabled and config.uia_content_enabled and config.input_text_enabled and content),
        ("剪贴板正文", config.input_enabled and config.clipboard_enabled and content))]


def scope_note(config: Any) -> str:
    policy = config.policy
    return "；".join((
        "截图范围：" + ("所有显示器" if config.visual_scope == "monitors" else "前台窗口"),
        "仅限应用：" + ("、".join(policy.allowed_apps) or "未限制"),
        "排除应用：" + ("、".join(policy.excluded_apps) or "无"),
        "排除窗口：" + ("、".join(policy.excluded_windows) or "无"),
        "排除 Mirally 自身：" + ("是" if policy.exclude_self else "否"),
        "智能体控制时暂停正文：" + ("是" if policy.agent_pause_content else "否"),
        "保存截图文件：" + ("是" if config.visual_persist_images and config.visual_enabled
                          and policy.default_level == "content" else "否")))
