# Mirally application artwork

The selected mark is **相伴 M / Companion M**, based on the user's blue-cyan
two-person reference. Two circular heads and connected flowing bodies form an M:
Mirally / MIRA, with the figures expressing companionship and teamwork.
This replaces the earlier `01-ally-wing-v2` mark, preserved in design history.

- Reference: `output/imagegen/mirally-companion-m/reference.png`.
- Selected generation: `output/imagegen/mirally-companion-m/companion-m-clean.png`.
- Transparent production master: `mirally.png` (1254 × 1254 RGBA).
- Window / taskbar icon: `mirally.ico` (16, 20, 24, 32, 40, 48, 64, 96, 128, 256 px).
- Resized PNGs: `mirally-{size}.png`, including a 512 px export.

The original remains unchanged. The production master removes the pale rounded
tile, ambient glow and shadow using the user-authorized imagegen skill CLI and
`gpt-image-2.5-sunburst`, with `--quality high --size auto --background transparent
--no-augment`. The complete prompt is recorded in
`docs/design-concepts/mirally/companion-m/production-clean.txt`.

The production master was opened and checked for the selected silhouette,
blue-cyan palette, and actual alpha transparency on light and dark backgrounds. PNG sizes and the ICO are
mechanical exports, not independent model generations. To rebuild:

```powershell
.\.venv\Scripts\python.exe tools\build_brand_assets.py --source output\imagegen\mirally-companion-m\companion-m-clean.png
```

For a fresh checkout without the ignored design output directory, use
`mirally.png` as the source and export to a separate directory; do not copy the
file onto itself. The UI, floating panel, startup and taskbar share these assets.

Use `openassist.branding` to resolve these assets. Do not refer to the output
directory from application code: the runtime may start with a different working
directory, and the output directory is for design provenance.
