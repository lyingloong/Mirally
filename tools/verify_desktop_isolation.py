"""Verify the real capture backend with a disposable, generated test desktop.

No LLM, no real application actions. Saves only synthetic fixture crops.
"""
from __future__ import annotations

import ctypes
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import tkinter as tk
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "UFO"))

from openassist.config import configure_ufo_config_root
from openassist.shell import OpenAssistShell
from PIL import ImageChops
from openassist.runtime.ufo.compat import native_compatibility


def main():
    os.chdir(ROOT / "UFO")
    output = ROOT / ".artifacts" / "interface"
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="openassist-isolation-") as directory:
        home = Path(directory)
        config = home / "config" / "openassist" / "ufo"
        config.mkdir(parents=True)
        for source in (ROOT / "config" / "openassist" / "ufo").iterdir():
            if source.name == "agents.yaml":
                continue  # Never read deployment credentials.
            if source.suffix == ".yaml" or source.name == "agents.yaml.template":
                name = "agents.yaml" if source.name.endswith(".template") else source.name
                shutil.copyfile(source, config / name)
        configure_ufo_config_root(home, ufo_root=ROOT / "UFO")
        with native_compatibility():
            from ufo.automator.ui_control.screenshot import PhotographerFacade
            from openassist.runtime.ufo.observation import observation_isolation
        app = OpenAssistShell(home, demo=False, startup=False)
        app.root.winfo_pointerxy = lambda: (-10000, -10000)
        left, top, right, bottom = app.session.work_area()
        # Isolation now excludes every window in the product PID. The target
        # therefore must be a separate process, rather than another Tk root.
        fixture = subprocess.Popen(
            [sys.executable, str(ROOT / "tools" / "capture_fixture.py"),
             f"{right-left}x{bottom-top}+{left}+{top}"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        fixture_hwnd = 0
        extra = None
        try:
            with ThreadPoolExecutor(max_workers=1) as reader:
                line = reader.submit(fixture.stdout.readline).result(timeout=10)
            fixture_hwnd = json.loads(line)["hwnd"]
        except Exception:
            fixture.terminate()
            fixture.wait(timeout=5)
            app.session.close()
            app.root.destroy()
            raise
        app.root.geometry(f"{round(500 * app.scale)}x{round(740 * app.scale)}+40+60")
        app.root.update()
        time.sleep(0.15)
        app.root.update()
        app.session.external_window = fixture_hwnd
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
            assert baseline.getpixel((10, 10)) == (218, 230, 223), "Generated fixture is not visible behind the app"
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
                app.root.lift()
                app.root.update()
                crop = (x, y, x + width, y + height)
                app.session.capture_begin(force_hide=True)
                expected = grab()
                assert expected.getpixel((10, 10)) == (218, 230, 223), f"{form} fixture not visible"
                app.session.capture_end()
                app.root.update()
                app.session.capture_begin()
                assert not app.session.capture_hidden, "Native capture exclusion unavailable for edge window"
                assert app.session.user32.IsWindowVisible(app.session.hwnd)
                observed = grab()
                expected.save(output / f"isolation-{form}-expected.png")
                observed.save(output / f"isolation-{form}-observed.png")
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
            # Exercise the actual runtime leaf hook without manually wrapping
            # the screenshot. Multi-window bookkeeping is covered separately
            # by the native WindowSession tests because Tk may repaint a newly
            # created same-process toplevel asynchronously on some desktops.
            services = SimpleNamespace(
                cancelled=lambda: False,
                capture=lambda stage: app.session.capture_begin() if stage == "before" else app.session.capture_end(),
            )
            for force_hide in (False, True):
                app.session.prefer_native_capture = not force_hide
                with native_compatibility(), observation_isolation(services):
                    observed = grab()
                assert ImageChops.difference(expected, observed).getbbox() is None, "Runtime capture hook leaked a product window"
                assert not app.session.capture_active
            print(f"PASS: actual UFO capture and runtime leaf hook exclude all product windows (native={native}); fallback and input styles restore.")
        finally:
            app.session.prefer_native_capture = True
            if app.session.capture_active:
                app.session.capture_end()
            app.session.apply("interactive")
            if fixture_hwnd and app.session.user32.IsWindow(fixture_hwnd):
                app.session.user32.PostMessageW(fixture_hwnd, 0x0010, 0, 0)
            try:
                fixture.wait(timeout=5)
            except subprocess.TimeoutExpired:
                fixture.terminate()  # Only the disposable child we just created.
                fixture.wait(timeout=5)
            app.edge.close()
            app.session.close()
            app.root.destroy()


if __name__ == "__main__":
    main()
