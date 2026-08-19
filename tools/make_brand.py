"""Generate every CLIque brand asset from one definition of the mark.

The mark is two chevrons — a large one with a smaller one tucked into its
opening. A prompt, and then a second prompt: many CLIs, one place. The geometry
below was traced from the original drawing and then regularised, so the shapes
are symmetric and the proportions are exact rather than approximately right.

Everything downstream comes from `CHEVRONS`: the SVGs are written from it, and
the PNGs are drawn from the same numbers rather than rasterised from the SVGs.
That means there is no renderer dependency (no Inkscape, no rsvg) and no way
for the vector and the raster to drift apart.

Usage:  python3 tools/make_brand.py
"""

from __future__ import annotations

import math
import struct
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parents[1] / "clique" / "web" / "brand"

# --------------------------------------------------------------------- palette

#: Near-black with a trace of blue, so it sits with the panel rather than
#: punching a pure-black hole in it.
INK = "#0E1116"
#: Violet to cyan, running along the chevrons. Chosen because developer tooling
#: is almost uniformly blue — this reads as itself at a glance in a crowded tab
#: strip, and both ends stay legible on light and on dark.
GRAD_FROM = "#A855F7"
GRAD_TO = "#22D3EE"
#: The single colour to use where a gradient cannot go (a 16px favicon glyph,
#: a one-colour print). Sits between the two stops.
SOLID = "#5FA8F5"

#: Corner radius of the squircle, in the 100-unit space.
RADIUS = 22.0

# -------------------------------------------------------------------- geometry

#: (apex_x, apex_y, arm_end_x, arm_half_height, stroke_width) in a 100x100 box.
#: The small chevron's tip stops well short of the large one's inner edge; the
#: gap is part of the mark, not slack.
CHEVRONS = [
    (72.0, 50.0, 36.5, 26.8, 10.0),
    (36.5, 61.9, 23.3, 11.15, 6.9),
]

#: Corners are softened by stroking the outline in its own fill. One number
#: rounds every corner of both chevrons, which is what keeps them looking like
#: one drawing rather than two.
ROUND = 0.26


def _unit(p, q):
    dx, dy = q[0] - p[0], q[1] - p[1]
    length = math.hypot(dx, dy)
    return dx / length, dy / length


def _offset(p, q, dist):
    """The line p->q shifted `dist` along its right normal."""
    ux, uy = _unit(p, q)
    nx, ny = uy, -ux
    return (p[0] + nx * dist, p[1] + ny * dist), (ux, uy)


def _cross(p1, d1, p2, d2):
    den = d1[0] * d2[1] - d1[1] * d2[0]
    t = ((p2[0] - p1[0]) * d2[1] - (p2[1] - p1[1]) * d2[0]) / den
    return (p1[0] + t * d1[0], p1[1] + t * d1[1])


def _y_at(p, d, x):
    return p[1] + (x - p[0]) / d[0] * d[1]


def chevron(apex_x, apex_y, end_x, half_h, width):
    """One chevron as a closed polygon, ends cut vertically.

    Built by offsetting each arm's centreline to both sides and intersecting
    the offsets — which is what puts the outer tip further right than the
    centreline apex and the inner notch further left, exactly as a real stroke
    behaves. Drawing it as two overlapping quadrilaterals instead leaves a
    visible seam at the join at large sizes.
    """
    half = (width - width * ROUND) / 2
    upper, apex, lower = (end_x, apex_y - half_h), (apex_x, apex_y), (end_x, apex_y + half_h)

    up_out, up_dir = _offset(upper, apex, -half)
    up_in, _ = _offset(upper, apex, half)
    lo_out, lo_dir = _offset(apex, lower, half)
    lo_in, _ = _offset(apex, lower, -half)

    return [
        (end_x, _y_at(up_out, up_dir, end_x)),
        _cross(up_out, up_dir, lo_out, lo_dir),
        (end_x, _y_at(lo_out, lo_dir, end_x)),
        (end_x, _y_at(lo_in, lo_dir, end_x)),
        _cross(up_in, up_dir, lo_in, lo_dir),
        (end_x, _y_at(up_in, up_dir, end_x)),
    ]


def polygons(scale=1.0, dx=0.0, dy=0.0, only=None, bold=1.0):
    """The chevrons as polygons.

    `only=1` keeps just the large one, which is all that survives at favicon
    size. `bold` thickens the stroke — the mark is drawn at its true weight
    everywhere except the very small sizes, where a 2px stroke reads as a wire
    outline rather than as a shape.
    """
    out = []
    for apex_x, apex_y, end_x, half_h, width in CHEVRONS[:only]:
        width *= bold
        pts = chevron(apex_x, apex_y, end_x, half_h, width)
        out.append(([(x * scale + dx, y * scale + dy) for x, y in pts], width * scale * ROUND))
    return out


# ------------------------------------------------------------------------ SVG


def _path(points):
    head = f"M{points[0][0]:.2f} {points[0][1]:.2f}"
    rest = "".join(f"L{x:.2f} {y:.2f}" for x, y in points[1:])
    return head + rest + "Z"


def svg_mark(*, square: bool, gradient: bool, size: int = 100) -> str:
    """The mark. `square` adds the tile behind it; `gradient` colours it."""
    fill = "url(#cq)" if gradient else "currentColor"
    defs = ""
    if gradient:
        defs = (
            "\n  <defs>\n"
            f'    <linearGradient id="cq" gradientUnits="userSpaceOnUse"'
            f' x1="{DIAGONAL[0] * 100:.1f}" y1="{DIAGONAL[0] * 100:.1f}"'
            f' x2="{DIAGONAL[1] * 100:.1f}" y2="{DIAGONAL[1] * 100:.1f}">\n'
            f'      <stop offset="0" stop-color="{GRAD_FROM}"/>\n'
            f'      <stop offset="1" stop-color="{GRAD_TO}"/>\n'
            "    </linearGradient>\n"
            "  </defs>"
        )
    tile = f'\n  <rect width="100" height="100" rx="{RADIUS}" fill="{INK}"/>' if square else ""
    shapes = "".join(
        f'\n  <path d="{_path(pts)}" fill="{fill}" stroke="{fill}" '
        f'stroke-width="{round_w:.2f}" stroke-linejoin="round"/>'
        for pts, round_w in polygons()
    )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" '
        f'width="{size}" height="{size}" role="img" aria-label="CLIque">'
        f"{defs}{tile}{shapes}\n</svg>\n"
    )


def svg_lockup() -> str:
    """Mark plus wordmark, for a README header. Text is a system stack, not a
    font file: a lockup that needs a download is a lockup that renders wrong."""
    mark = "".join(
        f'\n    <path d="{_path(pts)}" fill="url(#cq)" stroke="url(#cq)" '
        f'stroke-width="{round_w:.2f}" stroke-linejoin="round"/>'
        for pts, round_w in polygons()
    )
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 420 100" width="420" height="100" role="img" aria-label="CLIque: a folder for every CLI on the box">
  <defs>
    <linearGradient id="cq" gradientUnits="userSpaceOnUse" x1="{DIAGONAL[0] * 100:.1f}" y1="{DIAGONAL[0] * 100:.1f}" x2="{DIAGONAL[1] * 100:.1f}" y2="{DIAGONAL[1] * 100:.1f}">
      <stop offset="0" stop-color="{GRAD_FROM}"/>
      <stop offset="1" stop-color="{GRAD_TO}"/>
    </linearGradient>
  </defs>
  <g>
    <rect width="100" height="100" rx="{RADIUS}" fill="{INK}"/>{mark}
  </g>
  <text x="122" y="52" font-family="ui-sans-serif,-apple-system,'Segoe UI',Inter,system-ui,sans-serif"
        font-size="38" font-weight="700" letter-spacing="-0.5" fill="currentColor">CLIque</text>
  <text x="123" y="76" font-family="ui-sans-serif,-apple-system,'Segoe UI',Inter,system-ui,sans-serif"
        font-size="15" fill="currentColor" opacity="0.62">Your private clique of CLIs</text>
</svg>
'''


# --------------------------------------------------------------------- raster

SS = 8  # supersampling factor; the mark has long shallow diagonals


def _rgb(value: str):
    return tuple(int(value[i : i + 2], 16) for i in (1, 3, 5))


def _diagonal_span():
    """How far along the leading diagonal the mark actually reaches.

    Measured from the geometry, not from the tile. The mark never comes near
    the tile's corners, so a gradient ramped corner-to-corner paints the whole
    drawing out of the middle third of the ramp and renders as one flat blue —
    which is exactly what the first attempt did. Deriving it here also means
    the colours stay correct if the geometry is ever adjusted.
    """
    us = [(x + y) / 200 for pts, _ in polygons() for x, y in pts]
    return min(us), max(us)


DIAGONAL = _diagonal_span()


def _gradient(size: int):
    """Violet to cyan along the leading diagonal, as an RGB array."""
    axis = np.linspace(0, 1, size)
    ramp = np.add.outer(axis, axis) / 2
    lo, hi = DIAGONAL
    ramp = np.clip((ramp - lo) / (hi - lo), 0, 1)
    a, b = np.array(_rgb(GRAD_FROM), float), np.array(_rgb(GRAD_TO), float)
    return (a + (b - a) * ramp[..., None]).astype(np.uint8)


def _mark_alpha(size: int, scale: float, dx: float, dy: float, only=None, bold=1.0):
    """Coverage mask for the chevrons, anti-aliased by supersampling."""
    big = Image.new("L", (size * SS, size * SS), 0)
    pen = ImageDraw.Draw(big)
    for pts, round_w in polygons(scale=scale * SS, dx=dx * SS, dy=dy * SS,
                                 only=only, bold=bold):
        pen.polygon(pts, fill=255)
        # Round the corners the same way the SVG does: stroke the outline in
        # its own colour with a joined pen.
        pen.line([*pts, pts[0]], fill=255, width=max(1, int(round_w)), joint="curve")
        for x, y in pts:
            r = round_w / 2
            pen.ellipse([x - r, y - r, x + r, y + r], fill=255)
    return big.resize((size, size), Image.LANCZOS)


def _squircle_alpha(size: int, radius: float):
    big = Image.new("L", (size * SS, size * SS), 0)
    ImageDraw.Draw(big).rounded_rectangle(
        [0, 0, size * SS - 1, size * SS - 1], radius=radius * SS, fill=255
    )
    return big.resize((size, size), Image.LANCZOS)


def icon_png(size: int, *, tile: bool = True, inset: float = 0.0) -> Image.Image:
    """One square icon. `inset` shrinks the artwork inside the canvas, which is
    what Android's maskable icons need — they crop to a circle on some
    launchers and to a squircle on others, so the mark has to live well inside
    the safe area rather than at the edge of the tile."""
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    scale = size / 100.0
    if tile:
        keep = 1.0 - inset * 2
        tile_px = size
        radius = RADIUS
        if inset:
            tile_px = int(size * keep)
        art = Image.new("RGBA", (tile_px, tile_px), (0, 0, 0, 0))
        art.paste(
            Image.new("RGBA", (tile_px, tile_px), (*_rgb(INK), 255)),
            (0, 0),
            _squircle_alpha(tile_px, radius),
        )
        grad = Image.fromarray(_gradient(tile_px)).convert("RGBA")
        art.paste(grad, (0, 0), _mark_alpha(tile_px, tile_px / 100.0, 0, 0))
        off = (size - tile_px) // 2
        canvas.paste(art, (off, off), art)
    else:
        grad = Image.fromarray(_gradient(size)).convert("RGBA")
        canvas.paste(grad, (0, 0), _mark_alpha(size, scale, 0, 0))
    return canvas


def small_icon_png(size: int, pad: float = 0.06) -> Image.Image:
    """The favicon treatment: the whole mark, no tile, filling the canvas.

    Dropping the tile is what buys the room — at 16px its rounding eats the
    corners and the mark shrinks to nothing inside it. Transparent also sits
    on any tab colour, light or dark, which a fixed dark tile does not.

    Framed by measuring the geometry rather than by hand-tuned offsets, which
    is how the first version ended up with its tip cropped off the edge.
    """
    #: A third heavier, because at 16px the true weight is barely two pixels
    #: and reads as a wire outline. An earlier version also dropped the small
    #: chevron for legibility, and the result was a tab icon that did not match
    #: the one in the window — worse than a slightly busy 16px.
    bold = 1.35
    pts, round_w = polygons(only=1, bold=bold)[0]
    half = round_w / 2
    xs = [x for x, _ in pts]
    ys = [y for _, y in pts]
    x0, x1 = min(xs) - half, max(xs) + half
    y0, y1 = min(ys) - half, max(ys) + half

    inner = size * (1 - pad * 2)
    zoom = inner / max(x1 - x0, y1 - y0)
    dx = (size - (x1 - x0) * zoom) / 2 - x0 * zoom
    dy = (size - (y1 - y0) * zoom) / 2 - y0 * zoom

    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    grad = Image.fromarray(_gradient(size)).convert("RGBA")
    img.paste(grad, (0, 0), _mark_alpha(size, zoom, dx, dy, only=1, bold=bold))
    return img


def maskable_png(size: int) -> Image.Image:
    """Full-bleed ink with the mark at 60%, so any launcher's crop still lands
    on the whole drawing."""
    canvas = Image.new("RGBA", (size, size), (*_rgb(INK), 255))
    art = int(size * 0.60)
    offset = (size - art) // 2
    # The ramp is built at the *mark's* size, not the canvas's. Building it at
    # canvas size would paint a 60% mark out of the middle of the ramp and wash
    # the colours out — the same mistake DIAGONAL exists to prevent.
    grad = Image.fromarray(_gradient(art)).convert("RGBA")
    canvas.paste(grad, (offset, offset), _mark_alpha(art, art / 100.0, 0, 0))
    return canvas


def _radial_glow(width, height, cx, cy, rx, ry, color, alpha):
    """Soft ellipse of `color` at `alpha` (0-1), falloff squared."""
    y, x = np.ogrid[:height, :width]
    t = ((x - cx) / rx) ** 2 + ((y - cy) / ry) ** 2
    a = np.clip(1.0 - t, 0, 1) ** 2 * alpha
    layer = np.zeros((height, width, 4), np.uint8)
    layer[..., 0], layer[..., 1], layer[..., 2] = color
    layer[..., 3] = (a * 255).astype(np.uint8)
    return Image.fromarray(layer, "RGBA")


def social_png(width=1280, height=640) -> Image.Image:
    """GitHub social preview and the site OG card. 1280x640.

    Same lockup energy as the marketing page: ink, a violet→cyan glow, the
    mark, the current headline, a short CLI strip. Important type stays inside
    a padded safe area so crops (Twitter, iMessage) don't eat the chevrons.
    """
    bar_h = 8
    canvas = Image.new("RGB", (width, height), _rgb(INK))
    rgba = canvas.convert("RGBA")
    rgba = Image.alpha_composite(
        rgba,
        _radial_glow(width, height, width * 0.38, height * 0.18, 520, 280, _rgb(GRAD_FROM), 0.22),
    )
    rgba = Image.alpha_composite(
        rgba,
        _radial_glow(width, height, width * 0.72, height * 0.78, 480, 240, _rgb(GRAD_TO), 0.14),
    )
    canvas = rgba.convert("RGB")
    pen = ImageDraw.Draw(canvas)

    bar = np.linspace(0, 1, width)[None, :, None]
    a, b = np.array(_rgb(GRAD_FROM), float), np.array(_rgb(GRAD_TO), float)
    strip = np.repeat((a + (b - a) * bar).astype(np.uint8), bar_h, axis=0)
    canvas.paste(Image.fromarray(strip), (0, height - bar_h))

    def font(path, size):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            return ImageFont.load_default()

    title_f = font("/usr/share/fonts/truetype/lato/Lato-Black.ttf", 108)
    line_f = font("/usr/share/fonts/truetype/lato/Lato-Semibold.ttf", 34)
    meta_f = font("/usr/share/fonts/truetype/lato/Lato-Regular.ttf", 22)
    url_f = font("/usr/share/fonts/truetype/lato/Lato-Medium.ttf", 22)

    def size_of(text, fnt):
        box = pen.textbbox((0, 0), text, font=fnt)
        return box[2] - box[0], box[3] - box[1]

    def draw(text, fnt, fill, x, y):
        y0 = pen.textbbox((0, 0), text, font=fnt)[1]
        pen.text((x, y - y0), text, font=fnt, fill=fill)
        return pen.textbbox((0, 0), text, font=fnt)[3] - y0

    title = "CLIque"
    pre, hi, post = "A ", "folder", " for every CLI on the box."
    clis = "Claude Code  ·  Grok  ·  Gemini  ·  Codex  ·  OpenCode"
    url = "useclique.dev"

    tw, th = size_of(title, title_f)
    line_w = size_of(pre, line_f)[0] + size_of(hi, line_f)[0] + size_of(post, line_f)[0]
    cw, ch = size_of(clis, meta_f)
    uw, uh = size_of(url, url_f)
    gap1, gap2, gap3 = 14, 22, 18
    text_w = max(tw, line_w, cw, uw)
    line_h = size_of(hi, line_f)[1]
    text_h = th + gap1 + line_h + gap2 + ch + gap3 + uh

    icon_size = 268
    icon = icon_png(icon_size, tile=False)
    gap = 56
    group_w = icon_size + gap + text_w
    group_h = max(icon_size, text_h)
    ox = (width - group_w) // 2
    oy = (height - bar_h - group_h) // 2

    canvas.paste(icon, (ox, oy + (group_h - icon_size) // 2), icon)
    tx = ox + icon_size + gap
    ty = oy + (group_h - text_h) // 2

    y = ty
    y += draw(title, title_f, (255, 255, 255), tx, y) + gap1
    x = tx
    x += size_of(pre, line_f)[0]
    draw(pre, line_f, (200, 206, 214), tx, y)
    draw(hi, line_f, _rgb(SOLID), x, y)
    draw(post, line_f, (200, 206, 214), x + size_of(hi, line_f)[0], y)
    y += line_h + gap2
    y += draw(clis, meta_f, (120, 128, 142), tx, y) + gap3
    draw(url, url_f, _rgb(SOLID), tx, y)
    return canvas


def write_ico(path: Path, sizes=(16, 32, 48, 64)) -> None:
    """Hand-rolled ICO: Pillow's writer re-encodes from one source and softens
    the small sizes. Each size is drawn at its own scale instead, which is the
    difference between a legible 16px favicon and a smudge."""
    images = []
    for size in sizes:
        # Below 20px the tile and its rounding eat the mark, so the small sizes
        # drop the tile and draw the chevrons alone, edge to edge.
        img = small_icon_png(size) if size <= 20 else icon_png(size)
        images.append(img)

    entries, blobs, offset = [], [], 6 + 16 * len(images)
    for size, img in zip(sizes, images, strict=True):
        rgba = img.tobytes("raw", "BGRA")
        rows = b"".join(
            rgba[(size - 1 - y) * size * 4 : (size - y) * size * 4] for y in range(size)
        )
        mask = b"\x00" * (((size + 31) // 32) * 4 * size)
        header = struct.pack(
            "<IiiHHIIiiII", 40, size, size * 2, 1, 32, 0, len(rows) + len(mask), 0, 0, 0, 0
        )
        blob = header + rows + mask
        entries.append(
            struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(blob), offset)
        )
        offset += len(blob)
        blobs.append(blob)
    path.write_bytes(struct.pack("<HHH", 0, 1, len(images)) + b"".join(entries) + b"".join(blobs))


def write_png(path: Path, img: Image.Image) -> None:
    img.save(path, "PNG", optimize=True)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)

    # Vector, in the three shapes anything ever needs.
    (OUT / "logo.svg").write_text(svg_mark(square=True, gradient=True))
    (OUT / "mark.svg").write_text(svg_mark(square=False, gradient=True))
    (OUT / "mark-mono.svg").write_text(svg_mark(square=False, gradient=False))
    (OUT / "lockup.svg").write_text(svg_lockup())

    # Raster, for the places that cannot take an SVG.
    for size in (16, 32, 64, 128, 180, 192, 256, 512):
        art = small_icon_png(size) if size <= 20 else icon_png(size)
        write_png(OUT / f"icon-{size}.png", art)
    write_png(OUT / "apple-touch-icon.png", icon_png(180))
    write_png(OUT / "icon-maskable-512.png", maskable_png(512))
    write_png(OUT / "social-preview.png", social_png())
    write_ico(OUT / "favicon.ico")
    # Browsers ask for /favicon.ico at the root whatever the markup says, and
    # behind `tailscale serve` that request never reaches a subdirectory. Keep
    # a copy where they will actually look for it.
    (OUT.parent / "favicon.ico").write_bytes((OUT / "favicon.ico").read_bytes())

    for f in sorted(OUT.iterdir()):
        print(f"  {f.name:28} {f.stat().st_size:>7,} bytes")


if __name__ == "__main__":
    main()
