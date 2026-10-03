"""Exercise the real edge UI and persistent conversation with an offline demo."""
from __future__ import annotations

import sys
import tempfile
import time
from tkinter import font
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PIL import ImageGrab
from openassist.conversation import ConversationStore
from openassist.shell import OpenAssistShell


def main():
    output = ROOT / ".artifacts" / "interface"
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="openassist-edge-") as directory:
        app = OpenAssistShell(Path(directory), demo=True, startup=False)
        original_wait = app.controller._demo_wait
        # Leave enough running time for the extra passive-hover verification.
        app.controller._demo_wait = lambda seconds: original_wait(seconds + (4 if app.controller.phase == "executing" else 0))
        memory_path = Path(directory) / "conversations"
        app.conversation = app.controller.conversation = ConversationStore(memory_path)
        pointer = [-10000, -10000]
        app.root.winfo_pointerxy = lambda: tuple(pointer)  # Own-app hover observation fixture.
        errors = []
        app.root.report_callback_exception = lambda *error: errors.append(error)

        def pump(seconds=0.1):
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                app.root.update()
                time.sleep(0.01)
            if errors:
                raise AssertionError(errors)

        def until(condition, timeout=6):
            deadline = time.monotonic() + timeout
            while not condition() and time.monotonic() < deadline:
                pump(0.04)
            assert condition(), (app._phase, app.edge.mode)

        def snapshot(name):
            pump(0.35)
            width, height, x, y = app.edge.visible_rect()
            ImageGrab.grab(bbox=(x, y, x + width, y + height)).save(output / name)

        try:
            app.composer.insert("1.0", "整理本周资料，生成一份可继续使用的归档结果")
            app._submit()
            until(lambda: app.edge.mode == "compact" and app._phase == "observing")
            pump(0.9)
            assert app.edge.visible_rect()[0] <= round(275 * app.scale)
            assert app.root.attributes("-alpha") == 1.0
            snapshot("13-edge-working.png")
            pointer[:] = [app.edge.visible_rect()[2] + 15, app.edge.visible_rect()[3] + 30]
            until(lambda: app.edge.mode == "expanded")
            pump(0.4)
            assert not app.controller._editing, "Looking at progress acquired input ownership"
            before = app.edge._activity_frame
            pump(0.4)
            assert app.edge._activity_frame > before + 5, "Expanded activity indicator stopped refreshing"
            pointer[:] = [app.edge.composer.winfo_rootx() + 8, app.edge.composer.winfo_rooty() + 8]
            until(lambda: app.controller.editing_ready)
            assert app._phase not in {"paused", "pausing"}
            assert str(app.edge.composer["state"]) == "normal"
            app.edge.composer.insert("1.0", "只整理 PDF，保留原文件。")
            snapshot("14-edge-compose.png")
            assert app.edge.send.winfo_rooty() + app.edge.send.winfo_height() < app.root.winfo_rooty() + app.root.winfo_height()
            for button in (app.edge.pause, app.edge.stop, app.edge.open):
                assert button.winfo_width() >= button.winfo_reqwidth(), "Edge button was squeezed"
                inset = 2 * (round(10 * app.scale) + 2 + round(8 * app.scale))
                assert font.Font(app.root, family=app.body_font, size=10).measure(button["text"]) <= button.winfo_width() - inset, "Edge button text clipped"
            app.edge.send.invoke()
            pointer[:] = [-10000, -10000]
            until(lambda: any(r["status"] == "applied" for r in app._receipts.values()))
            assert app.edge.mode == "compact"
            assert not app.controller._editing
            snapshot("15-edge-received.png")
            # Draft survives collapse and return, then is sent as a second note.
            app.session._key_queue.put(3)  # Deliver the app's expand-shortcut event.
            until(lambda: app.edge.mode == "expanded" and app.controller.editing_ready)
            pump(0.35)
            assert app.root.focus_get() == app.edge.composer, "Expand shortcut did not focus the input"
            app.edge.composer.insert("1.0", "归档按日期命名")
            app.edge.collapse()
            pump(0.35)
            app.edge.expand()
            pump(0.35)
            assert app.edge.composer.get("1.0", "end-1c") == "归档按日期命名"
            app.edge.set_ready(app._begin_composing())
            app.edge.send.invoke()
            # Opening full records then returning should also preserve drafts
            # and release automatic input ownership without a manual pause.
            pump(0.35)
            app.edge.expand()
            pump(0.35)
            app.edge.open_workspace()
            app.composer.insert("1.0", "请保持文件名可读")
            app._return_to_edge()
            pump(0.35)
            assert not app.controller._editing
            app.edge.expand()
            pump(0.35)
            assert app.edge.composer.get("1.0", "end-1c") == "请保持文件名可读"
            app.edge.set_ready(app._begin_composing())
            app.edge.send.invoke()
            until(lambda: app._phase == "waiting_user", timeout=12)
            assert app.edge.mode == "workspace"
            app.composer.insert("1.0", "项目资料 / 本周归档")
            app._submit()
            until(lambda: app._phase == "observing" and app.edge.mode == "compact")
            assert app.session.working, "Reply failed to restore working state"
            assert app.edge.composer.get("1.0", "end-1c") == "", "Sent answer leaked into a draft"
            until(lambda: app._phase == "waiting_confirmation")
            snapshot("16-edge-confirmation.png")
            app._confirm(True)
            until(lambda: app._phase == "verifying" and app.edge.mode == "compact")
            assert app.session.working, "Approval failed to return to the compact working window"
            until(lambda: not app.controller.active)
            pump()
            assert app._phase == "completed"
            app.edge.open_workspace()
            app._toggle_memory()
            snapshot("17-conversation-memory.png")
            assert app.memory_text.winfo_ismapped() and app.memory_text.winfo_height() >= round(90 * app.scale), "Memory content is clipped"
            restored = ConversationStore(memory_path)
            previous = restored.context()["previous_tasks"]
            assert previous[-1]["inputs"] == ["只整理 PDF，保留原文件。", "归档按日期命名", "请保持文件名可读", "项目资料 / 本周归档"]
            previous_id = restored.snapshot()["id"]
            app.composer.insert("1.0", "继续处理刚才的归档结果")
            app._submit()
            assert app.controller._task_context["previous_tasks"][-1]["inputs"] == previous[-1]["inputs"]
            app.controller.stop()
            until(lambda: not app.controller.active)
            pump()
            app.edge.open_workspace()
            app._new_conversation()
            assert app.conversation.snapshot()["id"] != previous_id
            assert not app.conversation.context()["previous_tasks"]
            app.conversation.select(previous_id)
            app._refresh_conversation()
            assert len(app.conversation.context()["previous_tasks"]) == 2
            print("PASS: smooth activity, live supplement, preserved draft, automatic return after question/approval, persisted context.")
            assert app.session.pointer_monitor_ready, "Asynchronous Raw Input registration failed"
            print("Asynchronous Raw Input pointer monitor:", app.session.pointer_monitor_ready)
            print("Screenshots:", output)
        finally:
            app.controller.stop()
            deadline = time.monotonic() + 5
            while app.controller.active and time.monotonic() < deadline:
                app.root.update()
                time.sleep(0.01)
            app.edge.close()
            app.session.close()
            app.root.destroy()


if __name__ == "__main__":
    main()
