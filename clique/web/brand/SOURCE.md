# Brand assets

| File | Use |
|---|---|
| `icon.svg` | the favicon — carries a `prefers-color-scheme` rule so the neutral adapts to the browser theme |
| `icon-light-bg.svg` / `icon-dark-bg.svg` | fixed-colour variants, for places that cannot run CSS |
| `favicon.ico` | fallback for browsers without SVG favicon support; uses a mid tone that survives either background |
| `icon-{16..512}.png` | raster sizes |
| `lockup.svg` / `lockup-dark-bg.svg` | icon plus wordmark, for a README header |
| `social-preview.png` | 1280x640, for GitHub's social preview setting |

Colours: accent `#39afec`, neutral `#545454` (`#8b949e` on dark).

## Regenerating

```bash
python3 docs/brand/make_assets.py
```

The mark is seven rounded rectangles defined once in `SHAPES`, shared by the SVG
writer and the Pillow renderer so the vector and raster versions cannot drift.
Pillow is the only dependency — no SVG rasteriser is assumed to exist.

The original design came from Gemini as a single flat contact sheet
(`source/` in the project's Nextcloud folder). It was rebuilt as vector rather
than cropped, because cropping would have baked in the transparency
checkerboard and JPEG artefacts.

---

## Reused here

These files came from `/root/platform/CodemanPanel/docs/brand/`, unchanged.
Same author, same estate, and CLIque is the tool that replaces the pair —
carrying the mark over is continuity, not appropriation. `make_assets.py` in
the original repo regenerates them all from the SVG if the mark ever changes.
