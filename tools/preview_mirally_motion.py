"""Record the visible edge transition against a generated desktop fixture.

Uses the real desktop compositor, unlike PrintWindow. Only the fixture-covered
rectangle is recorded; no model, user input injection or real task is involved.
Recording samples at 30 Hz and is not a display refresh-rate benchmark.
"""
from __future__ import annotations

from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
import argparse
import ctypes
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import tkinter as tk

import win32con
import win32gui
import win32ui
from PIL import Image, ImageChops, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from openassist.shell import OpenAssistShell

BACKGROUND = "#dae6df"


@contextmanager
def desktop_region(rect):
    """Reuse a GDI bitmap so recording does not repeatedly allocate screen DCs."""
    x, y, width, height = rect
    source = memory = bitmap = previous = None
    try:
        source = win32ui.CreateDCFromHandle(win32gui.CreateDC("DISPLAY", None, None))
        memory = source.CreateCompatibleDC()
        bitmap = win32ui.CreateBitmap()
        bitmap.CreateCompatibleBitmap(source, width, height)
        previous = memory.SelectObject(bitmap)

        def grab():
            ctypes.windll.dwmapi.DwmFlush()
            memory.BitBlt((0, 0), (width, height), source, (x, y), win32con.SRCCOPY | 0x40000000)  # CAPTUREBLT
            return Image.frombytes("RGB", (width, height), bitmap.GetBitmapBits(True), "raw", "BGRX", 0, 1)

        yield grab
    finally:
        if memory and previous:
            memory.SelectObject(previous)
        if bitmap:
            win32gui.DeleteObject(bitmap.GetHandle())
        if memory:
            memory.DeleteDC()
        if source:
            source.DeleteDC()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--working", action="store_true", help="Exercise the native layered/click-through working window")
    args = parser.parse_args()
    output = ROOT / ".artifacts" / ("mirally-motion-working" if args.working else "mirally-motion")
    output.mkdir(parents=True, exist_ok=True)
    samples, errors = [], []
    with tempfile.TemporaryDirectory(prefix="mirally-motion-") as directory:
        app = OpenAssistShell(Path(directory), demo=True, startup=False)
        fixture = tk.Tk()
        fixture.overrideredirect(True)
        fixture.configure(bg=BACKGROUND)
        fixture.attributes("-topmost", True)
        app.root.winfo_pointerxy = lambda: (-10000, -10000)
        app.root.report_callback_exception = lambda *error: errors.append(str(error))

        def pump(seconds):
            until = time.perf_counter() + seconds
            while time.perf_counter() < until:
                app.root.update()
                time.sleep(.001)
            assert not errors, errors

        try:
            app.controller.mode = "desktop"
            app._on_state({"phase": "executing", "title": "正在整理本周资料", "detail": "离线界面验证"})
            app.edge.dock()
            pump(.4)
            width, height, x, y = app.edge._rect(True)
            left, top, right, bottom = app.session.work_area()
            x0, y0 = max(left, x - 6), max(top, y - 6)
            width, height = min(right, x + width + 6) - x0, min(bottom, y + height + 6) - y0
            compact_width, compact_height, compact_x, compact_y = app.edge._rect(False)
            # This rectangle stays inside both shapes for the whole transition.
            # Checking only the outer bbox misses a briefly erased interior.
            core = (compact_x - x0 + 40, compact_y - y0 + 35,
                    compact_x - x0 + compact_width - 40, compact_y - y0 + compact_height - 35)
            fixture.geometry(f"{width}x{height}+{x0}+{y0}")
            fixture.update()
            fixture.lift()
            app.root.lift()
            if args.working:
                app.session.demo = False  # Native window flags, still no agent.
                app.session.external_window = int(app.session.user32.GetAncestor(fixture.winfo_id(), 2))
                app.session.apply("working")
            pump(.15)
            plain = Image.new("RGB", (width, height), BACKGROUND)
            stop = threading.Event()
            stage = ["compact"]
            native_edges = []
            original_set_rect = app.edge._set_rect
            canvas_items = tuple(app.edge.canvas.find_all())
            native_bounds = app.session.window_rect()
            child_bounds = tuple((widget.winfo_width(), widget.winfo_height(), widget.winfo_x(), widget.winfo_y())
                                 for widget in (app.edge.canvas, app.edge.panel, app.edge.composer))

            def record_rect(rect):
                original_set_rect(rect)
                assert app.session.window_rect() == native_bounds, "Reveal resized or moved the native surface"
                assert tuple((widget.winfo_width(), widget.winfo_height(), widget.winfo_x(), widget.winfo_y())
                             for widget in (app.edge.canvas, app.edge.panel, app.edge.composer)) == child_bounds, "Reveal relaid out controls"
                w, _, left_edge, _ = app.session.window_rect()
                native_edges.append(left_edge + w)

            app.edge._set_rect = record_rect

            def record():
                # Screen capture can wait for the compositor. Keep it off Tk's
                # thread so the measurement does not serialize each UI frame.
                with desktop_region((x0, y0, width, height)) as grab:
                    while not stop.is_set():
                        now, label = time.perf_counter(), stage[0]
                        picture = grab()
                        bounds = ImageChops.difference(picture, plain).getbbox()
                        assert bounds, "No visible edge window in the generated fixture"
                        samples.append((now, label, picture, bounds))
                        stop.wait(max(0, 1 / 30 - (time.perf_counter() - now)))

            with ThreadPoolExecutor(max_workers=1) as executor:
                recording = executor.submit(record)
                try:
                    for label, action, seconds in (
                        ("compact", None, .3),
                        ("expand", app.edge.expand, .55),
                        ("working", None, .6),
                        ("collapse", app.edge.collapse, .55),
                        ("compact-end", None, .3),
                    ):
                        stage[0] = label
                        if action:
                            action()
                        pump(seconds)
                finally:
                    stop.set()
                    recording.result()
            assert tuple(app.edge.canvas.find_all()) == canvas_items, "Animation rebuilt the header instead of moving existing items"
            assert max(native_edges) - min(native_edges) == 0, f"Native right edge drifted: {native_edges}"
            assert not errors, errors
            # Invisible parts of the fixed surface must not block the desktop.
            app.session.apply("interactive")
            native_x, native_y = native_bounds[2:]
            clipped_point = (native_x + 10, native_y + native_bounds[1] - 30)
            hit = win32gui.WindowFromPoint(clipped_point)
            assert win32gui.GetAncestor(hit, 2) != app.session.hwnd, "Clipped-out surface intercepted input"
        finally:
            app.session.apply("interactive")
            fixture.destroy()
            app.edge.close()
            app.session.close()
            app.root.destroy()

    # Compare actual composited silhouettes, not merely native geometry calls.
    metrics = {"sampling_hz": 30, "native_working": args.working, "frames": len(samples), "transitions": {}}
    metrics["native_right_edge_drift_px"] = max(native_edges) - min(native_edges)
    visible_rights = [bounds[2] for _, _, _, bounds in samples]
    metrics["visible_right_edge_drift_px"] = max(visible_rights) - min(visible_rights)
    findings = []
    if metrics["visible_right_edge_drift_px"] > 1:
        findings.append(f"Visible right edge drifted: {visible_rights}")
    coverage = []
    for _, _, picture, _ in samples:
        interior = ImageChops.difference(picture.crop(core), plain.crop(core)).convert("L")
        histogram = interior.histogram()
        coverage.append(sum(histogram[13:]) / (interior.width * interior.height))
    metrics["min_interior_coverage"] = round(min(coverage), 5)
    if min(coverage) < .99:
        findings.append(f"Window interior flashed/erased: minimum coverage {min(coverage):.3f}")
    for label, growing in (("expand", True), ("collapse", False)):
        group = [sample for sample in samples if sample[1] == label]
        sizes = [(box[2] - box[0], box[3] - box[1]) for _, _, _, box in group]
        if len(set(sizes)) < 5:
            findings.append(f"Too few visible transition steps for {label}: {sizes}")
        heights = [size[1] for size in sizes]
        if not all((b >= a if growing else b <= a) for a, b in zip(heights, heights[1:])):
            findings.append(f"Non-monotonic visible height for {label}: {sizes}")
        for index, (_, _, picture, _) in enumerate(group):
            picture.save(output / f"{label}-{index:02d}.png")
        metrics["transitions"][label] = {"visible_sizes": len(set(sizes)), "sizes": sizes}
    working = [sample for sample in samples if sample[1] == "working"]
    assert len({sample[2].tobytes() for sample in working}) >= 5, "Expanded activity is visually frozen"

    durations = [max(10, round((b[0] - a[0]) * 1000)) for a, b in zip(samples, samples[1:])] + [33]
    frames = [sample[2] for sample in samples]
    frames[0].save(output / "edge-motion.gif", save_all=True, append_images=frames[1:],
                   duration=durations, loop=0, optimize=False)
    selected = []
    for label in ("expand", "collapse"):
        group = [sample for sample in samples if sample[1] == label]
        selected.extend(group[min(len(group) - 1, index)] for index in (0, 2, 4, 6, 9, 13))
    cell_width, cell_height = 320, round(frames[0].height / frames[0].width * 320) + 28
    sheet = Image.new("RGB", (cell_width * 6, cell_height * 2), "white")
    draw = ImageDraw.Draw(sheet)
    for index, (stamp, label, picture, _) in enumerate(selected):
        px, py = (index % 6) * cell_width, (index // 6) * cell_height
        preview = picture.copy()
        preview.thumbnail((cell_width, cell_height - 28))
        sheet.paste(preview, (px, py + 28))
        draw.text((px + 8, py + 8), f"{label} / {round((stamp - samples[0][0]) * 1000)} ms", fill="#192b3a")
    sheet.save(output / "edge-motion-frames.png")
    (output / "verification.json").write_text(json.dumps(metrics, indent=2) + "\n", encoding="utf-8")
    assert not findings, findings
    print("PASS: visible transitions are progressive; interior stays painted; expanded activity remains visible.")
    print(json.dumps({"frames": len(samples), "visible_sizes": {label: row["visible_sizes"]
                      for label, row in metrics["transitions"].items()}, "min_interior_coverage": metrics["min_interior_coverage"],
                      "native_right_edge_drift_px": metrics["native_right_edge_drift_px"],
                      "visible_right_edge_drift_px": metrics["visible_right_edge_drift_px"]}))


if __name__ == "__main__":
    main()
