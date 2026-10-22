"""Render the canonical SVG with Edge at 3x and export application PNG/ICO assets."""
from __future__ import annotations

import argparse
import html
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=ROOT/'mirally/assets/mirally.svg')
    parser.add_argument('--out', type=Path, default=ROOT/'mirally/assets')
    parser.add_argument('--browser', type=Path)
    args = parser.parse_args()
    browser = args.browser
    if browser is None:
        for variable in ('ProgramFiles(x86)', 'ProgramFiles', 'LOCALAPPDATA'):
            candidate = Path(os.environ.get(variable, ''))/'Microsoft/Edge/Application/msedge.exe'
            if candidate.is_file():
                browser = candidate
                break
    if browser is None or not browser.is_file():
        parser.error('Microsoft Edge not found; specify --browser with a Chromium executable.')
    source = args.source.resolve(strict=True)
    cache = ROOT/'.artifacts'
    cache.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='brand-render-', dir=cache, ignore_cleanup_errors=True) as temporary:
        work = Path(temporary)
        page = work/'render.html'
        page.write_text('<!doctype html><style>html,body{margin:0;background:transparent;overflow:hidden}'
                        'img{display:block;width:100vw;height:100vh}</style>'
                        f'<img src="{html.escape(source.as_uri(), quote=True)}">', encoding='utf-8')
        rendered = work/'render-3x.png'
        subprocess.run([
            str(browser), '--headless', '--disable-gpu', '--no-first-run',
            '--no-default-browser-check', '--hide-scrollbars', '--force-device-scale-factor=1',
            '--user-data-dir='+str(work/'profile'), '--virtual-time-budget=2500',
            '--default-background-color=00000000', '--window-size=3762,3762',
            '--screenshot='+str(rendered), page.as_uri(),
        ], check=True, timeout=60, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
        master = work/'mirally.png'
        with Image.open(rendered) as image:
            mark = image.convert('RGBA')
            if mark.size != (3762, 3762) or mark.getchannel('A').getextrema() != (0, 255):
                raise RuntimeError('Unexpected SVG render size or transparency.')
            mark.resize((1254, 1254), Image.Resampling.LANCZOS).save(master)
        subprocess.run([sys.executable, str(ROOT/'tools/build_brand_assets.py'),
                        '--source', str(master), '--out', str(args.out.resolve())], check=True)


if __name__ == '__main__':
    main()
