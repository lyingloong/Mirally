"""Start the owned Mirally framework, with an explicit legacy comparison entry."""

from __future__ import annotations

import sys
import argparse
from pathlib import Path


def main() -> None:
    if sys.platform != "win32":
        raise SystemExit("Mirally currently runs on Windows only.")

    product_root = Path(__file__).resolve().parents[1]
    if "--openclaw" in sys.argv[1:]:
        parser = argparse.ArgumentParser(description="Mirally legacy OpenClaw maintenance entry")
        parser.add_argument("--openclaw", action="store_true")
        parser.parse_args()
        from mirally.control_ui_shell import ControlUIShell
        ControlUIShell(product_root).run()
    else:
        from mirally.framework_host import main as framework_main
        raise SystemExit(framework_main())


if __name__ == "__main__":
    main()
