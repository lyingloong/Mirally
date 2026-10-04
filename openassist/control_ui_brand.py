"""Versioned presentation assets for the official Control UI desktop host."""
from __future__ import annotations

import base64
import json
from pathlib import Path
from urllib.parse import urlsplit

ASSETS = Path(__file__).resolve().parent / "assets"
BRAND_VERSION = "2026.10.04.2"


def brand_script(url: str) -> str:
    """Build a document-start skin constrained to this Gateway origin.

    Never carry the native bootstrap fragment, credentials, or a Python host API
    into the script. The official frontend continues to own every interaction.
    """
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ValueError("Invalid Control UI origin")
    origin = f"{parts.scheme}://{parts.netloc}"
    if parts.username or parts.password:
        raise ValueError("Credentials are not a presentation origin")
    logo = "data:image/png;base64," + base64.b64encode((ASSETS / "mirally-128.png").read_bytes()).decode("ascii")
    css = "\n".join((ASSETS / name).read_text(encoding="utf-8") for name in (
        "control-ui-mirally.css", "control-ui-companion.css", "control-ui-work.css"))
    script = "\n".join((ASSETS / name).read_text(encoding="utf-8") for name in (
        "control-ui-mirally.js", "control-ui-office.js", "control-ui-intake.js", "control-ui-context.js", "control-ui-work.js", "control-ui-companion.js"))
    return script.replace("__MIRALLY_ORIGIN__", json.dumps(origin)).replace(
        "__MIRALLY_STYLE__", json.dumps(css)
    ).replace("__MIRALLY_MARK__", json.dumps(logo)).replace("__MIRALLY_VERSION__", json.dumps(BRAND_VERSION))


def startup_message(message: str) -> str:
    return {
        "正在连接 OpenClaw": "正在唤醒你的工作伙伴",
        "正在启动后台服务": "正在准备你的工作空间",
        "正在等待原生界面就绪": "正在整理工作空间",
        "正在连接原生界面": "马上就好，准备与你并肩",
    }.get(message, "正在准备你的工作空间")
