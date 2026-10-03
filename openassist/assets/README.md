# Mirally application artwork

The selected mark is **01-ally-wing-v2**: the user chose its subtle A silhouette,
sky-blue folded wing, and embracing shape as Mirally's final identity.

- Selected original: `output/imagegen/mirally-a-monogram/01-ally-wing-v2.png`.
- Transparent production master: `mirally.png` (1254 × 1254 RGBA).
- Window / taskbar icon: `mirally.ico` (16, 20, 24, 32, 40, 48, 64, 96, 128, 256 px).
- Resized PNGs: `mirally-{size}.png`, including a 512 px export.

The original remains unchanged. The production master removes its nearly white
background using the user-authorized imagegen skill CLI and
`gpt-image-2.5-sunburst`, with `--quality high --size auto --background transparent
--no-augment`. The complete prompt is recorded in
`docs/design-concepts/mirally/a-monogram/production-transparent.txt`.

The production master was opened and checked for the selected silhouette,
sky-blue palette, and actual alpha transparency. PNG sizes and the ICO are
mechanical exports, not independent model generations. To rebuild:

```powershell
.\.venv\Scripts\python.exe tools\build_brand_assets.py --source output\imagegen\mirally-a-monogram\01-ally-wing-production-transparent.png
```

Use `openassist.branding` to resolve these assets. Do not refer to the output
directory from application code: the runtime may start with a different working
directory, and the output directory is for design provenance.
