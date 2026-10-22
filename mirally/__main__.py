"""Start the Mirally desktop session window."""

from __future__ import annotations

import sys
import argparse
from pathlib import Path


def main() -> None:
    if sys.platform != "win32":
        raise SystemExit("Mirally currently runs on Windows only.")

    product_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Mirally desktop host for the official OpenClaw Control UI")
    parser.add_argument("--openclaw", action="store_true", help="Open the complete official OpenClaw UI in the Mirally desktop host")
    parser.parse_args()
    from mirally.control_ui_shell import ControlUIShell
    ControlUIShell(product_root).run()


if __name__ == "__main__":
    main()
