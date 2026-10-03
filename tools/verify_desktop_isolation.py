"""Verify the real capture backend with a disposable, generated test desktop.

No LLM, no real application actions. Saves only synthetic fixture crops.
"""
from __future__ import annotations

import ctypes
import os
import sys
import tempfile
import time
import tkinter as tk
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "UFO"))

from openassist.config import configure_ufo_config_root
from openassist.shell import OpenAssistShell
from PIL import ImageChops


def main():
    configure_ufo_config_root(ROOT)
    os.chdir(ROOT / "UFO")
    from ufo.automator.ui_control.screenshot import PhotographerFacade
    output = ROOT / ".artifacts" / "interface"
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="openassist-isolation-") as directory:
        home = Path(directory)
        (home / "config" / "openassist").mkdir(parents=True)
        app = OpenAssistShell(home, demo=False, startup=False)
        # Independent toplevel: hiding the app must not hide the fixture as an
        # owned child window. It represents a separate target application.
        fixture = tk.Tk()
        fixture.title("OpenAssist generated capture fixture")
        fixture.configure(bg="#DAE6DF")
        fixture.geometry(f"{round(560 * app.scale)}x{round(830 * app.scale)}+20+20")
        tk.Label(fixture, text="OpenAssist capture verification\nGenerated test content only", bg="#DAE6DF", fg="#243B2E", font=("Segoe UI", 18)).pack(pady=60)
        app.root.geometry(f"{round(500 * app.scale)}x{round(740 * app.scale)}+40+60")
        app.root.update()
        time.sleep(0.15)
        app.root.update()
        app.session.external_window = int(app.session.user32.GetAncestor(fixture.winfo_id(), 2))
        fixture.lift()
        app.root.lift()
        app.root.update()
        geometry = app.root.geometry()
        x, y = app.root.winfo_rootx(), app.root.winfo_rooty()
        crop = (x, y, x + app.root.winfo_width(), y + app.root.winfo_height())
        photographer = PhotographerFacade()
        def grab():
            return photographer.capture_desktop_screen_screenshot(all_screens=False).crop(crop).convert("RGB")
        try:
            app.session.capture_begin(force_hide=True)
            baseline = grab()
            app.session.capture_end()
            app.root.update()
            ctypes.windll.dwmapi.DwmFlush()
            visible = grab()
            assert ImageChops.difference(baseline, visible).getbbox(), "Fixture did not detect visible UI"
            app.session.apply("working")
            app.root.update()
            assert app.root.attributes("-alpha") == 1.0
            style = app.session.user32.GetWindowLongPtrW(app.session.hwnd, -20)
            assert style & 0x08000020 == 0x08000020, hex(style)
            app.session.capture_begin()
            native = not app.session.capture_hidden
            if native:
                assert app.session.user32.IsWindowVisible(app.session.hwnd), "Native exclusion hid the UI"
            isolated = grab()
            baseline.save(output / "isolation-baseline.png")
            isolated.save(output / "isolation-observed.png")
            difference = ImageChops.difference(baseline, isolated).getbbox()
            assert difference is None, f"Capture differs from generated fixture: {difference}"
            app.session.capture_end()
            app.root.update()
            assert app.session.user32.GetWindowLongPtrW(app.session.hwnd, -20) & 0x08000020 == 0x08000020
            app.session.apply("interactive")
            app.root.update()
            assert app.root.geometry() == geometry, "Isolation changed window geometry"
            assert not (app.session.user32.GetWindowLongPtrW(app.session.hwnd, -20) & 0x08000020)
            isolated.save(output / "isolation-fixture.png")
            # Verify both borderless forms, including interactive typing, with
            # the exact same real capture backend and a synthetic background.
            for form in ("compact", "expanded", "typing"):
                if form == "compact":
                    app.edge.dock()
                    app.session.apply("working")
                elif form == "expanded":
                    app.edge.expand()
                else:
                    app.edge.set_ready(app._begin_composing())
                app.edge.settle()
                app.root.update()
                flags = app.session.user32.GetWindowLongPtrW(app.session.hwnd, -20) & 0x08000020
                assert flags == (0 if form == "typing" else 0x08000020), f"{form} input ownership lost"
                x, y = app.root.winfo_rootx(), app.root.winfo_rooty()
                width, height = app.root.winfo_width(), app.root.winfo_height()
                fixture.geometry(f"{width + 100}x{height + 100}+{x - 40}+{y - 50}")
                fixture.lift()
                app.root.lift()
                app.root.update()
                crop = (x, y, x + width, y + height)
                app.session.capture_begin(force_hide=True)
                expected = grab()
                app.session.capture_end()
                app.root.update()
                app.session.capture_begin()
                assert not app.session.capture_hidden, "Native capture exclusion unavailable for edge window"
                assert app.session.user32.IsWindowVisible(app.session.hwnd)
                observed = grab()
                assert ImageChops.difference(expected, observed).getbbox() is None, f"{form} appeared in native capture"
                observed.save(output / f"isolation-edge-{form}.png")
                app.session.capture_end()
                app.session.prefer_native_capture = False
                app.session.user32.SetWindowDisplayAffinity(app.session.hwnd, 0)
                app.session.capture_begin()
                assert app.session.capture_hidden, "Fallback did not hide window"
                assert ImageChops.difference(expected, grab()).getbbox() is None, f"{form} fallback polluted capture"
                app.session.capture_end()
                app.session.prefer_native_capture = True
            print(f"PASS: actual UFO capture excludes frontend (native={native}); input styles restore; geometry and alpha remain stable.")
        finally:
            app.session.apply("interactive")
            fixture.destroy()
            app.edge.close()
            app.session.close()
            app.root.destroy()


if __name__ == "__main__":
    main()
