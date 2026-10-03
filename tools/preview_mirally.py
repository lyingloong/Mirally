"""Offline regressions for branding, startup, full-size layouts and native gates.

Only Mirally's own windows are manipulated; no model or external app is used.
PrintWindow previews may work with the display off; desktop capture isolation
still needs a visible compositor and is verified by its separate harness.
"""
from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from openassist.presentation import PHASES, THEMES
from openassist.shell import OpenAssistShell
from openassist.startup import StartupReveal
from ui_preview_capture import CaptureUnavailable, capture_window


def main():
    output = ROOT / ".artifacts" / "mirally"
    output.mkdir(parents=True, exist_ok=True)
    results = {"previews": [], "unavailable": [], "animation": {}}
    with tempfile.TemporaryDirectory(prefix="mirally-preview-") as directory:
        app = OpenAssistShell(Path(directory), demo=True, startup=False)
        app.root.winfo_pointerxy = lambda: (-10000, -10000)
        errors = []
        app.root.report_callback_exception = lambda *error: errors.append(str(error))

        def pump(seconds=.12):
            deadline = time.perf_counter() + seconds
            while time.perf_counter() < deadline:
                app.root.update()
                time.sleep(.004)
            assert not errors, errors

        def snapshot(name):
            app.root.update_idletasks()
            try:
                capture_window(app.root, output / name)
                results["previews"].append(name)
            except CaptureUnavailable as error:
                results["unavailable"].append({"name": name, "reason": str(error)})

        def visible(widget):
            assert widget.winfo_ismapped(), str(widget)
            top, left = app.root.winfo_rooty(), app.root.winfo_rootx()
            assert widget.winfo_height() > 4 and widget.winfo_width() > 4, str(widget)
            assert widget.winfo_rooty() >= top and widget.winfo_rootx() >= left, str(widget)
            assert widget.winfo_rooty() + widget.winfo_height() <= top + app.root.winfo_height(), str(widget)
            assert widget.winfo_rootx() + widget.winfo_width() <= left + app.root.winfo_width(), str(widget)

        try:
            pump()
            initial_min = app.root.minsize()
            assert app.root.title().startswith("Mirally")
            assert app._brand_image.width() == round(34 * app.scale)
            # Real keyboard dispatch: first character must survive the reveal.
            app.startup = StartupReveal(app.root, app.theme, scale=app.scale,
                                        body_font=app.body_font, on_complete=app.composer.focus_set)
            app.composer.focus_set()
            app.startup.start()
            pump(.2)
            assert app.startup.active
            snapshot("01-startup.png")
            app.root.focus_force()
            app.composer.focus_set()
            app.composer.event_generate("<KeyPress-x>")
            pump(.04)
            assert not app.startup.active, "First key did not dismiss startup"
            assert app.composer.get("1.0", "end-1c") == "x", "First character was swallowed"
            app.composer.delete("1.0", "end")
            app.startup = StartupReveal(app.root, app.theme, scale=app.scale, body_font=app.body_font)
            app.startup.start()
            app._escape()
            assert not app.startup.active, "Escape did not dismiss startup"
            app.startup = StartupReveal(app.root, app.theme, reduced_motion=True)
            app.startup.start()
            assert not app.startup.active

            app.conversation.begin("preview", "帮我规划本周项目，保留会议和交付时间。", kind="conversation")
            app.conversation.finish("preview", "completed", "可以。我们先列出目标、会议与交付节点，再把可用时间安排给重点工作。\n\n你也可以随时补充新的要求，我会接着这段对话调整。")
            app._refresh_conversation()
            app._toggle_settings()
            assert "selected" in app.settings_button.state()
            snapshot("02-settings.png")
            app._toggle_memory()
            assert "selected" not in app.settings_button.state()
            assert "selected" in app.memory_button.state()
            app._dismiss_drawers()
            assert "selected" not in app.memory_button.state()

            app.root.state("zoomed")
            pump(.3)
            if app.root.winfo_width() >= round(980 * app.scale):
                assert app.sidebar.winfo_ismapped(), "Wide workspace lost its session column"
                assert app.chat_text.winfo_width() > app.root.winfo_width() * .5
                assert app.chat_text.winfo_height() > app.root.winfo_height() * .45
                visible(app.sidebar_new)
            visible(app.send_button)
            snapshot("03-maximized-chat.png")
            app.edge.dock()
            pump(.4)
            assert app.edge.mode == "compact" and app.root.state() == "normal"
            app.edge.open_workspace()
            pump(.3)
            assert app.root.state() == "zoomed", "Maximized state was lost"
            assert app.root.minsize() == initial_min

            app.root.attributes("-fullscreen", True)
            pump(.25)
            visible(app.send_button)
            snapshot("04-fullscreen-chat.png")
            app.edge.dock()
            pump(.4)
            assert not app.root.attributes("-fullscreen")
            app.edge.open_workspace()
            pump(.25)
            assert app.root.attributes("-fullscreen"), "Fullscreen state was lost"
            app.root.attributes("-fullscreen", False)
            app.root.state("normal")
            app.root.geometry(f"{initial_min[0]}x{initial_min[1]}")
            pump(.25)
            app.controller.mode = "desktop"
            app._on_receipt({"receipt_id": "test-receipt", "status": "applied", "source": "supplement",
                             "detail": "保留原文件，并按日期归档。"})
            app._on_state({"phase": "waiting_confirmation", "title": PHASES["waiting_confirmation"][1],
                           "detail": "请确认本次归档位置。", "action": "移动文件", "target": "项目资料 / 归档", "request_id": "preview"})
            pump(.15)
            for widget in (app.allow, app.reject, app.send_button, app.composer, app.interaction_text):
                visible(widget)
            visible(app.receipt_text)
            snapshot("05-small-confirmation.png")
            receipt_end = app.receipt_text.bbox("2.end -1c")
            assert receipt_end and receipt_end[1] + receipt_end[3] <= app.receipt_text.winfo_height() - 2, "Receipt detail is clipped"

            # Reproduce the worker's ordering before observing updates the UI.
            app.session.demo = False  # Native input flags, still no desktop agent.
            app.edge.resume_working()
            app.session.apply("working")
            assert app.edge.mode == "compact"
            assert app.session.user32.GetWindowLongPtrW(app.session.hwnd, -20) & 0x08000020 == 0x08000020
            pump(.4)
            app.session.apply("interactive")
            app.edge.open_workspace(interaction=True)
            pump(.2)
            assert not (app.session.user32.GetWindowLongPtrW(app.session.hwnd, -20) & 0x08000020)
            app.session.demo = True
            app._on_state({"phase": "executing", "title": "正在整理\n本周资料", "detail": "体验演示"})
            app.edge.dock()
            pump(.4)
            frames = []
            original = app.edge._set_rect

            def record(rect):
                frames.append(time.perf_counter())
                original(rect)

            app.edge._set_rect = record
            app.edge.expand()
            pump(.4)
            app.edge._set_rect = original
            assert len(frames) >= 8, f"Too few native geometry frames: {len(frames)}"
            intervals = [b - a for a, b in zip(frames, frames[1:])]
            results["animation"] = {"frames": len(frames), "max_gap_ms": round(max(intervals) * 1000, 1)}
            before = app.edge._activity_frame
            pump(.2)
            assert app.edge._activity_frame - before >= 5, "Activity cadence is too slow"
            snapshot("06-expanded-working.png")
            # A fallback capture must remain hidden for the ENTIRE tween,
            # including the final synchronization of Tk's requested geometry.
            app.edge.collapse()
            app.session.capture_begin(force_hide=True)
            try:
                pump(.4)
                assert not app.session.user32.IsWindowVisible(app.session.hwnd), "Animation revealed the capture-hidden window"
            finally:
                app.session.capture_end()
            assert app.session.user32.IsWindowVisible(app.session.hwnd)
            app.edge.expand()
            app.session.capture_begin()
            try:
                pump(.4)
                if app.session.capture_hidden:
                    assert not app.session.user32.IsWindowVisible(app.session.hwnd)
                else:
                    import ctypes
                    from ctypes import wintypes
                    affinity = wintypes.DWORD()
                    assert app.session.user32.GetWindowDisplayAffinity(app.session.hwnd, ctypes.byref(affinity))
                    assert affinity.value == 0x11, "Native exclusion was lost while animating"
            finally:
                app.session.capture_end()
                app.session.user32.SetWindowDisplayAffinity(app.session.hwnd, 0)
            app._reduced.set(True)
            app._motion()
            app.edge.collapse()
            assert app.edge._animation is None
            app.edge.open_workspace()
            app._on_state({"phase": "idle", "title": PHASES["idle"][1]})
            app.theme_key, app.theme = "graphite", THEMES["graphite"]
            app._apply_theme()
            app._toggle_settings()
            pump()
            snapshot("07-graphite-brand.png")
            print("PASS: startup first key/Escape/reduced motion, branding, selected tabs, full-size layouts, window restoration, native input gates.")
            print(json.dumps(results, ensure_ascii=True))
            (output / "verification.json").write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        finally:
            if app.startup:
                app.startup.cancel()
            app.session.apply("interactive")
            app.edge.close()
            app.session.close()
            app.root.destroy()


if __name__ == "__main__":
    main()
