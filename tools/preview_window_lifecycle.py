"""Verify the real taskbar recovery path and record own-window resize frames.

An isolated AppUserModelID ensures the only taskbar button clicked belongs to
this test process. A generated full-work-area fixture covers unrelated apps.
"""
from concurrent.futures import ThreadPoolExecutor
import ctypes
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import tkinter as tk
import uuid

from PIL import ImageChops
from pywinauto import Desktop

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import openassist.shell as shell
from preview_mirally_motion import desktop_region


def main():
    output = ROOT / ".artifacts" / "window-lifecycle"
    output.mkdir(parents=True, exist_ok=True)
    appid = "Mirally.LifecycleTest." + uuid.uuid4().hex
    shell.prepare_process_identity = lambda: ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(appid)
    with tempfile.TemporaryDirectory(prefix="mirally-lifecycle-") as directory:
        preferences = Path(directory) / "config" / "openassist"
        preferences.mkdir(parents=True)
        (preferences / "interface.json").write_text(json.dumps({"theme": "graphite"}), encoding="utf-8")
        app = shell.OpenAssistShell(Path(directory), demo=True, startup=True)
        app.root.winfo_pointerxy = lambda: (-10000, -10000)
        errors, records, frames = [], [], []
        app.root.report_callback_exception = lambda *error: errors.append(str(error))
        fixture = None

        def pump(seconds=.35):
            end = time.perf_counter() + seconds
            while time.perf_counter() < end:
                app.root.update()
                time.sleep(.002)
            assert not errors, errors

        def taskbar_button():
            buttons = Desktop(backend="uia").window(class_name="Shell_TrayWnd").descendants(control_type="Button")
            result = [button for button in buttons if appid in button.element_info.automation_id]
            assert len(result) == 1, f"Expected exactly our test taskbar button, got {len(result)}"
            return result[0]

        def record_resize(prefix):
            start = len(records)
            stop = threading.Event()
            stage = [prefix + "normal"]

            def record():
                with desktop_region((left, top, right-left, bottom-top)) as grab:
                    while not stop.is_set():
                        stamp = time.perf_counter()
                        label = stage[0]
                        picture = grab()
                        r, g, b = picture.split()
                        minimum = ImageChops.darker(ImageChops.darker(r, g), b)
                        histogram = minimum.histogram()
                        white = sum(histogram[245:]) / (picture.width * picture.height)
                        records.append({"stage": label, "white_fraction": white})
                        picture.thumbnail((700, 500))
                        frames.append(picture)
                        stop.wait(max(0, 1/30-(time.perf_counter()-stamp)))

            normal_geometry = app.root.geometry()
            with ThreadPoolExecutor(max_workers=1) as pool:
                capture = pool.submit(record)
                try:
                    pump(.2)
                    for label, action in (
                        ("maximize", lambda: app.root.state("zoomed")),
                        ("restore", lambda: app.root.state("normal")),
                        ("fullscreen", lambda: app.root.attributes("-fullscreen", True)),
                        ("exit-fullscreen", lambda: app.root.attributes("-fullscreen", False)),
                    ):
                        stage[0] = prefix + label
                        action()
                        pump(.6)
                finally:
                    stop.set()
                    capture.result()
            assert len(records)-start >= 12, "Too few compositor samples"
            maximum = max(row["white_fraction"] for row in records[start:])
            assert maximum < .04, f"Unexpected white surface in {prefix}: {maximum:.1%}"
            assert app.root.geometry() == normal_geometry
            print(f"PASS: {prefix} resize; maximum white coverage {maximum:.3%}")

        try:
            pump(3.1)
            # Cold start MUST precede any dock, theme switch or native resize.
            assert app.edge.mode == "workspace" and app.edge._workspace is None
            assert app.session.user32.GetClassLongPtrW(app.session.hwnd, -10) == app.session._background_brush
            assert app.session._transitions_disabled_for == app.session.hwnd
            left, top, right, bottom = app.session.work_area()
            fixture = tk.Toplevel(app.root)
            fixture.overrideredirect(True)
            fixture.configure(bg="#13232f")
            fixture.geometry(f"{right-left}x{bottom-top}+{left}+{top}")
            fixture.attributes("-topmost", True)
            fixture.update_idletasks()
            app.root.lift()
            pump()
            record_resize("cold-")
            taskbar_button()
            app.edge.dock()
            pump(.5)
            taskbar_button()
            app.edge.expand()
            app.edge.composer.insert("1.0", "Unsent draft")
            pump()
            taskbar_button()
            # Exercise real layered/no-activate flags without running an agent.
            app.session.demo = False
            app.session.apply("working")
            pump()
            taskbar_button()
            app.edge.minimize()
            pump()
            assert app.session.is_minimized()
            app.session.capture_begin(force_hide=True)
            app.session.capture_end()
            assert app.session.is_minimized()
            # Windows 11's taskbar does not always implement UIA Invoke.
            # Click only the item verified against our unique test AppID.
            taskbar_button().click_input()
            pump(.6)
            assert not app.session.is_minimized(), "Actual taskbar click failed to restore the window"
            assert app.edge.composer.get("1.0", "end-1c") == "Unsent draft"
            app.session.apply("interactive")
            app.edge.open_workspace(interaction=True)
            pump()
            taskbar_button()
            print("PASS: real taskbar entry in workspace/compact/expanded/working/minimized; taskbar click restores draft")

            app.root.lift()
            pump()
            assert app.session.user32.GetClassLongPtrW(app.session.hwnd, -10) == app.session._background_brush
            assert app.session._transitions_disabled_for == app.session.hwnd
            record_resize("after-dock-")
            maximum_white = max(row["white_fraction"] for row in records)
            # Graphite content and fixture are dark; allow tiny text/borders,
            # but reject a bright unpainted pane or a white whole window.
            assert maximum_white < .04, f"Unexpected white surface: {maximum_white:.1%}"
            frames[0].save(output / "resize.gif", save_all=True, append_images=frames[1:], duration=33, loop=0)
            result = {"frames": len(records), "max_white_fraction": maximum_white,
                      "taskbar_recovery": True, "samples": records}
            (output / "verification.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
            print(f"PASS: {len(records)} resize samples; maximum white coverage {maximum_white:.3%}; geometry restored")
        finally:
            if fixture:
                fixture.destroy()
            app.edge.close()
            app.session.close()
            app.root.destroy()


if __name__ == "__main__":
    main()
