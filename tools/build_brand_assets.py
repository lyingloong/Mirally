"""Export the selected Mirally mark to PNG/ICO sizes without altering its art."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
SIZES = (16, 20, 24, 32, 40, 48, 64, 96, 128, 256, 512)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=ROOT / "openassist" / "assets")
    args = parser.parse_args()
    source = args.source.resolve()
    destination = args.out.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination / "mirally.png")
    with Image.open(source) as image:
        mark = image.convert("RGBA")
        if mark.width != mark.height:
            raise SystemExit("The selected mark must retain its square framing.")
        for size in SIZES:
            mark.resize((size, size), Image.Resampling.LANCZOS).save(destination / f"mirally-{size}.png")
        mark.save(destination / "mirally.ico", format="ICO", sizes=[(size, size) for size in SIZES if size <= 256])
    print(f"Exported {len(SIZES)} PNG sizes and one multi-resolution ICO to {destination}")


if __name__ == "__main__":
    main()
