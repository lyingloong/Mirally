"""Offline interaction and visual checks for appearance and edge controls."""
import json
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from openassist.shell import OpenAssistShell
from preview_mirally_motion import desktop_region


def main():
    output = ROOT / ".artifacts" / "settings"
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="mirally-settings-") as directory:
        app = OpenAssistShell(Path(directory), demo=True, startup=False)
        pointer = [-10000, -10000]
        app.root.winfo_pointerxy = lambda: tuple(pointer)
        errors = []
        app.root.report_callback_exception = lambda *error: errors.append(str(error))

        def pump(seconds=.12):
            deadline = time.perf_counter() + seconds
            while time.perf_counter() < deadline:
                app.root.update()
                time.sleep(.003)
            assert not errors, errors

        def snapshot(name):
            pump()
            if app.edge.mode == "workspace":
                rect = (app.root.winfo_rootx(), app.root.winfo_rooty(), app.root.winfo_width(), app.root.winfo_height())
            else:
                width, height, x, y = app.edge.visible_rect()
                rect = (x, y, width, height)
            with desktop_region(rect) as grab:
                grab().save(output / name)

        def click(widget):
            widget.event_generate("<ButtonPress-1>", x=20, y=20)
            widget.event_generate("<ButtonRelease-1>", x=20, y=20)
            pump()

        try:
            pump()
            assert app.return_button.cget("text") == "浮窗"
            app._toggle_settings()
            pump()
            for card in app.settings.cards:
                app.settings.viewport.yview_moveto(0)
                pump()
                click(card)
                assert app.theme_key == card.key
                saved = json.loads(app.preference_path.read_text(encoding="utf-8"))
                assert saved["theme"] == card.key
                assert app.session.user32.GetClassLongPtrW(app.session.hwnd, -10) == app.session._background_brush
                snapshot(card.key + ".png")
            # Keyboard selection and switches follow the same commands as mouse.
            app.root.focus_force()
            app.settings.cards[0].focus_set()
            pump()
            app.settings.cards[0].event_generate("<KeyPress-space>")
            pump()
            assert app.theme_key == "sky"
            for switch in app.settings.switches:
                before = switch.variable.get()
                switch.focus_set()
                pump()
                switch.event_generate("<KeyPress-space>")
                pump()
                assert switch.variable.get() != before
                switch.event_generate("<KeyPress-space>")
                pump()
                assert switch.variable.get() == before
            width, height = app.root.minsize()
            app.root.geometry(f"{width}x{height}")
            pump()
            app.settings.switches[-1].focus_set()
            pump()
            assert app.settings.scrollbar.winfo_ismapped()
            last = app.settings.switches[-1]
            assert last.winfo_rooty() + last.winfo_height() <= app.settings.viewport.winfo_rooty() + app.settings.viewport.winfo_height() + 1
            snapshot("minimum-scrolled.png")
            app.root.state("zoomed")
            pump()
            assert app.settings._columns == 4
            snapshot("wide.png")
            app.root.state("normal")
            app._dismiss_drawers()
            for index in range(9):
                app.conversation.begin(str(index), f"Question {index}", kind="conversation")
                app.conversation.finish(str(index), "completed", f"Answer {index}")
            app._refresh_conversation()
            assert "9 项交流记录" in app.memory_var.get()
            assert len(app.conversation.context()["previous_tasks"]) == 8
            app.edge.dock()
            app.edge.settle()
            app.edge.expand()
            app.edge.composer.insert("1.0", "Keep this draft")
            app.edge.settle()
            button = app.edge.minimize_button
            pump()
            snapshot("minimize-normal.png")
            pointer[:] = [button.winfo_rootx()+button.winfo_width()//2, button.winfo_rooty()+button.winfo_height()//2]
            button.event_generate("<Enter>")
            pump()
            assert "active" in button.state()
            snapshot("minimize-hover.png")
            button.event_generate("<ButtonPress-1>", x=20, y=20)
            pump()
            assert "pressed" in button.state()
            snapshot("minimize-pressed.png")
            button.event_generate("<ButtonRelease-1>", x=20, y=20)
            pump()
            assert app.session.is_minimized()
            app.session.restore_minimized()
            pump()
            assert app.edge.composer.get("1.0", "end-1c") == "Keep this draft"
            print("PASS: theme clicks/persistence, keyboard switches, small-window scroll/focus, wide palettes, 9 conversation records, minimize hover/press/click/draft")
        finally:
            app.edge.close()
            app.session.close()
            app.root.destroy()


if __name__ == "__main__":
    main()
