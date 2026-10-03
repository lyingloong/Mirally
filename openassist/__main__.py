"""Start the Mirally desktop session window."""

from __future__ import annotations

import os
import sys
import argparse
from pathlib import Path


def main() -> None:
    if sys.platform != "win32":
        raise SystemExit("Mirally currently runs on Windows only.")

    product_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Mirally desktop companion")
    parser.add_argument("--demo", action="store_true", help="Preview the interface without a model or desktop actions")
    parser.add_argument("--legacy", action="store_true", help="Use the original interface, including native UFO mode")
    args = parser.parse_args()
    if args.demo and args.legacy:
        parser.error("--demo and --legacy cannot be combined")
    if args.demo:
        from openassist.shell import OpenAssistShell
        OpenAssistShell(product_root, demo=True).run()
        return
    ufo_root = product_root / "UFO"
    if not (ufo_root / "ufo" / "agents" / "single_agent" / "runner.py").is_file():
        raise SystemExit(f"UFO agent kernel was not found under {ufo_root}")

    from openassist.config import configure_ufo_config_root

    # UFO resolves prompts, screenshots, and logs relative to its repository
    # root, while configuration is owned by the OpenAssist product layer.
    configure_ufo_config_root(product_root)
    sys.path.insert(0, str(ufo_root))
    os.chdir(ufo_root)

    if args.legacy:
        from openassist.desktop import OpenAssistWindow
        OpenAssistWindow(product_root=product_root, ufo_root=ufo_root).run()
    else:
        from openassist.shell import OpenAssistShell
        OpenAssistShell(product_root).run()


if __name__ == "__main__":
    main()
