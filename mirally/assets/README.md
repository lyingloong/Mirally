# Mirally application artwork

The selected mark is **相伴 M / Companion M**, based on the user's blue-cyan
two-person reference. Two circular heads and connected flowing bodies form an M:
Mirally / MIRA, with the figures expressing companionship and teamwork.
This replaces the earlier `01-ally-wing-v2` mark, preserved in Git history.

- Brand and icon notes: `docs/design/brand.md`.
- Canonical vector master: `mirally.svg` (paths, ellipses and gradients; no embedded bitmap).
- Transparent runtime export: `mirally.png` (1254 × 1254 RGBA).
- Window / taskbar icon: `mirally.ico` (16, 20, 24, 32, 40, 48, 64, 96, 128, 256 px).
- Resized PNGs: `mirally-{size}.png`, including a 512 px export.

The production vector fits the selected reference. The original generation, prompts and fitting notes are retained in Git history; the application now uses exports of the refined vector.

The production master was opened and checked for the selected silhouette,
blue-cyan palette, and actual alpha transparency on light and dark backgrounds. PNG sizes and the ICO are
mechanical exports, not independent model generations. To rebuild:

```powershell
.\.venv\Scripts\python.exe tools\render_brand_vector.py
```

The renderer uses installed Microsoft Edge at 3x resolution, then downsamples
with Lanczos before exporting all sizes. It requires Pillow only at build time;
the runtime needs no SVG renderer. The tracked SVG is sufficient to rebuild from
a fresh checkout. Use `--out` for an alternate export directory and `--browser`
for another Chromium executable. The UI, floating panel, startup and taskbar
share these assets.

The Mirally desktop resolves these assets relative to its Python module;
`mirally.desktop_surface` supplies startup and native surface resources,
`mirally.framework_shell` loads the window icon, and the product Host serves the
retained presentation assets. Do not refer to the output
directory from application code: the runtime may start with a different working
directory, and the output directory is for design provenance.
