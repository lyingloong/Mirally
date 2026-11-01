"""Start the Mirally personal agent framework."""

from __future__ import annotations

import sys


def main() -> None:
    if sys.platform != "win32":
        raise SystemExit("Mirally currently runs on Windows only.")

    from mirally.framework_host import main as framework_main
    raise SystemExit(framework_main())


if __name__ == "__main__":
    main()
