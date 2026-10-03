"""Real-window regressions for idle docking, session discovery and edge routing."""
from pathlib import Path
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from openassist.assistant import AssistantDecision
from openassist.conversation import ConversationStore
from openassist.shell import OpenAssistShell
from ui_preview_capture import capture_window


def main():
    output = ROOT / ".artifacts" / "mirally-navigation"
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="mirally-navigation-") as directory:
        app = OpenAssistShell(Path(directory), demo=True, startup=False)
        app.conversation = app.controller.conversation = ConversationStore(Path(directory) / "conversations")
        app.root.winfo_pointerxy = lambda: (-10000, -10000)
        errors = []
        app.root.report_callback_exception = lambda *error: errors.append(str(error))
        gate = threading.Event()

        def pump(seconds=.15):
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                app.root.update()
                time.sleep(.003)
            assert not errors, errors

        def until(predicate, timeout=5):
            deadline = time.monotonic() + timeout
            while not predicate() and time.monotonic() < deadline:
                pump(.04)
            assert predicate(), (app._phase, app.edge.mode)

        def visible(widget):
            assert widget.winfo_ismapped(), str(widget)
            assert widget.winfo_width() >= 20, str(widget)
            assert widget.winfo_rooty() + widget.winfo_height() <= app.root.winfo_rooty() + app.root.winfo_height()

        try:
            pump()
            assert "disabled" not in app.return_button.state()
            app.return_button.invoke()
            pump(.4)
            assert app.edge.mode == "compact" and app._phase == "idle"
            capture_window(app.root, output / "01-idle-edge.png")
            app.edge.open_workspace(interaction=True)
            pump()
            app.root.geometry(f"{app.root.minsize()[0]}x{app.root.minsize()[1]}")
            pump()
            visible(app.history_button)
            app.history_button.invoke()
            pump()
            visible(app.history_list)
            visible(app.history_new)
            app.conversation.begin("old", "继续上周的项目计划", kind="conversation")
            app.conversation.finish("old", "completed", "先整理目标，再安排工作。")
            first_id = app.conversation.snapshot()["id"]
            app._refresh_conversation()
            app.history_new.invoke()
            second_id = app.conversation.snapshot()["id"]
            assert first_id != second_id
            index = next(i for i, (key, _) in enumerate(app._saved_sessions) if key == first_id)
            app.history_button.invoke()
            pump()
            capture_window(app.root, output / "02-small-history.png")
            app.history_list.canvas.event_generate("<Button-1>", x=40, y=index*app.history_list.row_height+20)
            pump()
            assert app.conversation.snapshot()["id"] == first_id
            assert "继续上周" in app.chat_text.get("1.0", "end")
            assert not app._memory_open
            assert not app._history_open
            capture_window(app.root, output / "02-small-sessions.png")
            app.root.state("zoomed")
            pump(.3)
            visible(app.sidebar_list)
            index = next(i for i, (key, _) in enumerate(app._saved_sessions) if key == second_id)
            app.sidebar_list.canvas.event_generate("<Button-1>", x=40, y=index*app.sidebar_list.row_height+20)
            pump()
            assert app.conversation.snapshot()["id"] == second_id
            assert app.sidebar_list.current == second_id
            capture_window(app.root, output / "04-wide-history.png")
            app.root.state("normal")
            pump(.3)
            visible(app.history_button)
            # A long history must scroll, and keyboard navigation must select
            # a row only after confirmation rather than changing on focus.
            for number in range(16):
                app.conversation.new()
                app.conversation.begin(f"archive-{number}", f"项目讨论 {number + 1}", kind="conversation")
                app.conversation.finish(f"archive-{number}", "completed", "保留这段交流。")
            app._refresh_conversation()
            app.history_button.invoke()
            pump()
            assert app.history_list.scrollbar.winfo_ismapped()
            assert app.history_list.scrollbar.winfo_height() > app.history_list.row_height
            last_id = app.conversation.snapshot()["id"]
            app.history_list.canvas.focus_force()
            app.history_list.canvas.event_generate("<Down>")
            pump()
            assert app.conversation.snapshot()["id"] == last_id
            app.history_list.canvas.event_generate("<Return>")
            pump()
            assert app.conversation.snapshot()["id"] != last_id
            assert not app._history_open
            app._select_conversation(second_id)

            class DelayedDesktop:
                def respond(self, *args):
                    assert gate.wait(8), "Routing test timed out"
                    return AssistantDecision("desktop", "离线执行流程")

            app.controller.assistant = DelayedDesktop()
            app.return_button.invoke()
            pump(.4)
            app.edge.expand()
            pump(.4)
            opened = []
            original_open = app.edge.open_workspace

            def track_open(**kwargs):
                opened.append(app._phase)
                original_open(**kwargs)

            app.edge.open_workspace = track_open
            app.edge.composer.insert("1.0", "整理本周资料")
            app.edge.submit()
            pump(.4)
            assert app.edge.mode == "compact" and app._phase == "thinking"
            assert not opened, opened
            assert "disabled" in app.history_new.state()
            app._select_conversation(first_id)
            assert app.conversation.snapshot()["id"] == second_id
            gate.set()
            until(lambda: app._phase == "observing")
            assert app.edge.mode == "compact" and not opened, opened
            capture_window(app.root, output / "03-edge-command.png")
            app.controller.stop()
            until(lambda: not app.controller.active)
            pump()

            class Greeting:
                def respond(self, *args):
                    return AssistantDecision("reply", "你好，我们接着聊。")

            app.controller.assistant = Greeting()
            app.edge.expand()
            pump(.4)
            app.edge.composer.insert("1.0", "你好")
            app.edge.submit()
            until(lambda: app._phase == "completed" and not app.controller.active)
            pump()
            assert app.edge.mode == "workspace"
            assert opened == ["completed"], opened
            assert "你好，我们接着聊。" in app.chat_text.get("1.0", "end")
            visible(app.history_button)
            print("PASS: idle docking; direct small-window session creation/restoration; wide navigation; no workspace detour while routing/executing; chat reply remains readable.")
        finally:
            gate.set()
            app.controller.stop()
            deadline = time.monotonic() + 3
            while app.controller.active and time.monotonic() < deadline:
                pump(.03)
            app.edge.close()
            app.session.close()
            app.root.destroy()


if __name__ == "__main__":
    main()
