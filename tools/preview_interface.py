"""Render the real Tk interface states for visual QA without an Agent or API.

Run from the repository root: .venv/Scripts/python.exe tools/preview_interface.py
Only the application's own rectangle is saved, never the rest of the desktop.
"""
from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PIL import ImageGrab
from openassist.presentation import THEMES, DEFAULT_THEME
from openassist.shell import OpenAssistShell


def main():
    output = ROOT / ".artifacts" / "interface"
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="openassist-preview-") as directory:
        home = Path(directory)
        (home / "config" / "openassist").mkdir(parents=True)
        app = OpenAssistShell(home, demo=True, startup=False)
        app.dock_on_run = False  # Dedicated edge harness covers the compact mode.
        errors = []
        app.root.report_callback_exception = lambda *error: errors.append(error)

        def pump(seconds=0.35):
            until = time.monotonic() + seconds
            while time.monotonic() < until:
                app.root.update()
                time.sleep(0.01)
            if errors:
                raise AssertionError(errors)

        def snapshot(name):
            pump()
            assert app.send_button.winfo_ismapped(), "Input controls disappeared"
            for widget in (app.send_button, app.stop_button, app.takeover_button):
                assert widget.winfo_rooty() + widget.winfo_height() <= app.root.winfo_rooty() + app.root.winfo_height(), "Bottom controls clipped"
            x, y = app.root.winfo_rootx(), app.root.winfo_rooty()
            ImageGrab.grab(bbox=(x, y, x + app.root.winfo_width(), y + app.root.winfo_height())).save(output / name)

        try:
            snapshot("01-idle.png")
            app.controller.mode = "desktop"  # The following fixtures are work states.
            geometry = app.root.geometry()
            app.task_var.set("整理本周的项目资料")
            for phase, title in (("observing", "正在查看\n当前桌面"), ("planning", "正在安排\n下一步操作"), ("executing", "正在整理\n本周资料")):
                app._on_state({"phase": phase, "title": title, "detail": "文件资源管理器"})
                pump(0.8)
            snapshot("02-executing.png")
            for key in ("graphite", "porcelain"):
                app.theme_key, app.theme = key, THEMES[key]
                app._apply_theme()
                snapshot(f"03-{key}.png")
            app.theme_key, app.theme = DEFAULT_THEME, THEMES[DEFAULT_THEME]
            app._apply_theme()
            app._on_state({"phase": "waiting_confirmation", "title": "这一步，\n由你决定。", "detail": "将 12 份文件移动到归档目录。此操作会改变文件位置。", "action": "移动文件", "target": "项目资料 / 归档", "request_id": "preview"})
            snapshot("04-confirmation.png")
            assert app.root.geometry() == geometry, f"State change moved/resized the window: {geometry} -> {app.root.geometry()}"
            app.root.geometry(f"{round(450 * app.scale)}x{round(650 * app.scale)}")
            snapshot("05-compact-confirmation.png")
            app._on_state({"phase": "failed", "title": "任务暂时\n无法继续", "detail": "执行未完成，可以调整任务后重试。"})
            snapshot("06-error.png")
            app._toggle_settings()
            snapshot("07-settings.png")
            app._dismiss_drawers()
            app._on_state({"phase": "idle", "title": "让想法，\n成为下一步。", "detail": "告诉我你想完成什么"})
            # Exercise actual demo controller, reply and approval through the GUI.
            app.root.geometry(f"{round(540 * app.scale)}x{round(800 * app.scale)}")
            app.composer.insert("1.0", "整理本周的项目资料")
            app._submit()
            deadline = time.monotonic() + 25
            replied = approved = supplemented = False
            while app.controller.active and time.monotonic() < deadline:
                pump(0.04)
                if app._phase == "observing" and not supplemented:
                    app.controller.pause()
                    pause_deadline = time.monotonic() + 3
                    while app._phase != "paused" and time.monotonic() < pause_deadline:
                        pump(0.04)
                    assert app._phase == "paused"
                    for text in ("请保留原文件，只整理副本。", "补充：只处理 PDF，归档名称使用本周日期。"):
                        app.composer.insert("1.0", text)
                        app.send_button.invoke()
                        pump(0.1)
                    assert len(app._receipts) == 2
                    assert all(r["status"] == "queued" for r in app._receipts.values())
                    assert "只处理 PDF" in app.receipt_text.get("1.0", "end")
                    snapshot("09-supplements-received.png")
                    app.controller.resume()
                    supplemented = True
                if app._phase == "waiting_user" and not replied:
                    app.composer.insert("1.0", "项目资料 / 归档")
                    app._submit()
                    replied = True
                if app._phase == "waiting_confirmation" and not approved:
                    assert len(app._receipts) == 3
                    assert all(r["status"] == "applied" for r in app._receipts.values())
                    snapshot("10-replies-applied.png")
                    app.root.geometry(f"{round(450 * app.scale)}x{round(650 * app.scale)}")
                    snapshot("11-compact-receipts-confirmation.png")
                    assert app.allow.winfo_ismapped(), "Confirmation hidden by receipts"
                    app._confirm(True)
                    approved = True
            pump()
            assert replied and approved and app._phase == "completed", app._phase
            snapshot("08-demo-completed.png")
            app.composer.insert("1.0", "新任务")
            app._submit()
            pump(0.1)
            assert not app._receipts and not app.feedback.winfo_ismapped(), "Old receipts carried into new task"
            app.controller.pause()
            pause_deadline = time.monotonic() + 3
            while app._phase != "paused" and time.monotonic() < pause_deadline:
                pump(0.04)
            assert app._phase == "paused"
            app.composer.insert("1.0", "这条补充尚未交给 Agent")
            app._submit()
            app.controller.stop()
            stop_deadline = time.monotonic() + 3
            while app.controller.active and time.monotonic() < stop_deadline:
                pump(0.04)
            pump()
            assert not app.controller.active
            assert next(iter(app._receipts.values()))["status"] == "unused"
            snapshot("12-supplement-unused.png")
            print("UI state and demo checks passed. Screenshots:", output)
        finally:
            app.controller.stop()
            deadline = time.monotonic() + 5
            while app.controller.active and time.monotonic() < deadline:
                app.root.update()
                time.sleep(0.01)
            app.session.close()
            app.root.destroy()


if __name__ == "__main__":
    main()
