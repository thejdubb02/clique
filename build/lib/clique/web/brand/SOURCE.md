# Brand assets

**Do not hand-edit anything in this directory.** Every file is generated:

```bash
python3 tools/make_brand.py
```

The mark is defined once, as geometry, in `tools/make_brand.py`. The SVGs are
written from it and the PNGs are *drawn* from the same numbers rather than
rasterised from the SVGs — so there is no renderer to install (no Inkscape, no
rsvg) and no way for the vector and the raster to drift apart.

## The mark

Two chevrons: a large one, with a smaller one tucked into its opening. A
prompt, and then a second prompt — many CLIs, one place.

Traced from the original drawing and then regularised, so the arms are
symmetric and the proportions are exact rather than approximately right.

## Colours

| | |
|---|---|
| Ink | `#0E1116` — near-black with a trace of blue, so it sits with the panel instead of punching a hole in it |
| Gradient | `#A855F7` violet → `#22D3EE` cyan, running along the chevrons |
| Solid | `#5FA8F5`, for anywhere a gradient cannot go |

The gradient is stretched across the span the mark actually occupies on the
diagonal, which is measured from the geometry rather than assumed. Ramped
corner to corner instead, both endpoints land in the empty corners of the tile
and the whole mark is painted out of the middle third of the ramp — which
renders as one flat blue.

## Files

| File | Use |
|---|---|
| `logo.svg` | the full mark: chevrons on the ink tile |
| `mark.svg` | chevrons alone, gradient, transparent — the favicon and the sidebar |
| `mark-mono.svg` | chevrons alone in `currentColor`, for one-colour contexts |
| `lockup.svg` | mark plus wordmark and tagline, for a README header |
| `icon-{16..512}.png` | raster sizes. 16px drops the tile and the second chevron and thickens the stroke — at that size the full mark is mud |
| `apple-touch-icon.png` | 180px, iOS home screen |
| `icon-maskable-512.png` | full-bleed ink, mark at 60%, for Android launchers that crop |
| `favicon.ico` | 16/32/48/64, each drawn at its own scale rather than downsampled from one source |
| `social-preview.png` | 1280×640, for GitHub's social preview setting |

`favicon.ico` is also copied to `clique/web/favicon.ico`, because browsers ask
for it at the root whatever the markup says.
