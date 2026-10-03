"""Render the actual Tk startup into a local storyboard and animated preview."""
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from PIL import Image
from openassist.shell import OpenAssistShell
from openassist.startup import StartupReveal
from ui_preview_capture import capture_window


def main():
    output = ROOT / "output" / "mirally-startup"
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="mirally-startup-") as directory:
        app = OpenAssistShell(Path(directory), demo=True, startup=False)
        errors = []
        app.root.report_callback_exception = lambda *error: errors.append(str(error))
        app.root.winfo_pointerxy = lambda: (-10000, -10000)
        try:
            app.root.update()
            reveal = app.startup = StartupReveal(app.root, app.theme, scale=app.scale, body_font=app.body_font)
            reveal.start()
            app.root.after_cancel(reveal._after_id)
            reveal._after_id = None
            items = reveal._canvas.find_all()
            frames = []
            for index in range(56):
                elapsed = index * .05
                reveal._begin = time.perf_counter() - elapsed
                reveal._render(elapsed)
                app.root.update_idletasks()
                target = output / "frame.png"
                capture_window(app.root, target)
                with Image.open(target) as image:
                    picture = image.convert("RGB")
                    picture.thumbnail((540, 720), Image.Resampling.LANCZOS)
                    frames.append(picture)
                if index in (6, 16, 28, 40, 50):
                    picture.save(output / f"stage-{index:02d}.png")
                assert reveal._canvas.find_all() == items, "Startup rebuilt canvas items"
            reveal.finish()
            app.root.update_idletasks()
            capture_window(app.root, output / "workspace.png")
            with Image.open(output / "workspace.png") as image:
                picture = image.convert("RGB")
                picture.thumbnail((540, 720), Image.Resampling.LANCZOS)
                frames.append(picture)
            frames[0].save(output / "startup.gif", save_all=True, append_images=frames[1:],
                           duration=[50]*56+[1000], loop=0, optimize=False)
            assert not errors, errors
            print("PASS: 56 startup frames, stable canvas items, clean completion; output/mirally-startup/startup.gif")
        finally:
            if app.startup:
                app.startup.cancel()
            app.edge.close()
            app.session.close()
            app.root.destroy()


if __name__ == "__main__":
    main()
