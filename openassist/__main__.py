"""Start the OpenAssist desktop session window."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def main() -> None:
    if sys.platform != "win32":
        raise SystemExit("OpenAssist currently runs on Windows only.")

    product_root = Path(__file__).resolve().parents[1]
    ufo_root = product_root / "UFO"
    if not (ufo_root / "ufo" / "agents" / "single_agent" / "runner.py").is_file():
        raise SystemExit(f"UFO agent kernel was not found under {ufo_root}")

    # UFO resolves configuration, prompts, screenshots, and logs relative to its
    # repository root. Keep the product package importable from the workspace.
    sys.path.insert(0, str(ufo_root))
    os.chdir(ufo_root)

    from openassist.desktop import OpenAssistWindow

    OpenAssistWindow(product_root=product_root, ufo_root=ufo_root).run()


if __name__ == "__main__":
    main()
