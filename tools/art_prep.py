#!/usr/bin/env python3
"""Turn a generated or supplied illustration into a shippable theme figure.

Keys out the flat background, crops to what is actually drawn, scales it to
something a corner watermark can use, and strips the metadata. What comes back
from an image model is a megabyte of 1408x768 with the figure floating in the
middle of a magenta field; what belongs in the wheel is about 40KB of exactly
the character.

    python3 tools/art_prep.py in.png out.png [--key ff00ff] [--height 560]

Needs Pillow, which the panel does not: this runs once when art arrives, and
the thing it produces is a plain PNG. Nothing at runtime imports it.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    from PIL import Image
except ImportError:  # pragma: no cover - a developer tool, not the product
    sys.exit("art_prep needs Pillow: pip install Pillow")

# How far a pixel may sit from the key colour and still count as background.
# Generous, because a JPEG-ish encode leaves a halo of near-key pixels around
# every edge and a tight threshold keeps them as a magenta fringe.
TOLERANCE = 60
# Below this alpha a pixel is background outright. Above it, the pixel keeps
# its colour but gets pulled away from the key, which is what stops a magenta
# rim appearing wherever the figure was antialiased against it.
EDGE = 200


def key_out(im: Image.Image, key: tuple[int, int, int]) -> Image.Image:
    im = im.convert("RGBA")
    out = []
    kr, kg, kb = key
    for r, g, b, a in im.get_flattened_data():
        near = max(abs(r - kr), abs(g - kg), abs(b - kb))
        if near <= TOLERANCE:
            out.append((r, g, b, 0))
        elif near <= TOLERANCE * 3:
            # An edge pixel: keep it, but unmix the key out of it so the
            # figure does not ship wearing a pink outline.
            f = (near - TOLERANCE) / (TOLERANCE * 2)
            out.append((
                min(255, int(r - kr * (1 - f) * 0.55)),
                min(255, int(g - kg * (1 - f) * 0.55)),
                min(255, int(b - kb * (1 - f) * 0.55)),
                int(255 * f),
            ))
        else:
            out.append((r, g, b, a))
    im.putdata(out)
    return im


def prep(src: Path, dst: Path, key: tuple[int, int, int], height: int,
         colors: int) -> None:
    im = key_out(Image.open(src), key)
    box = im.getbbox()
    if box:
        im = im.crop(box)
    if im.height > height:
        im = im.resize(
            (max(1, round(im.width * height / im.height)), height), Image.LANCZOS
        )
    # Quantised on purpose. These are cel-shaded illustrations: flat fills,
    # hard edges, a handful of tones per region, which is exactly the case a
    # palette handles without showing. Full colour came out at 2.2MB across
    # the seven, against about 400KB here for a picture that is drawn at nine
    # percent opacity behind somebody's terminal output. `colors` is the one
    # knob worth touching if a future drawing does have a gradient in it.
    flat = Image.new("RGBA", im.size)
    flat.putdata(list(im.get_flattened_data()))
    alpha = flat.getchannel("A")
    small = flat.convert("RGB").quantize(colors=colors, method=Image.MEDIANCUT, dither=Image.NONE)
    small = small.convert("RGBA")
    small.putalpha(alpha)
    small.save(dst, "PNG", optimize=True)
    print(f"  {dst.name:<16} {im.width}x{im.height}  {dst.stat().st_size // 1024} KB")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("src", type=Path)
    ap.add_argument("dst", type=Path)
    ap.add_argument("--key", default="ff00ff", help="background colour to remove")
    ap.add_argument("--height", type=int, default=420)
    ap.add_argument("--colors", type=int, default=96)
    args = ap.parse_args()
    raw = args.key.lstrip("#")
    prep(args.src, args.dst, tuple(int(raw[i:i + 2], 16) for i in (0, 2, 4)),
         args.height, args.colors)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
