"""Build a complete theme from a handful of colours, and keep it readable.

A theme in `web/themes.js` is about twenty-seven values: nine panel tokens and
eighteen terminal ones, all sixteen ANSI colours included. Asking a model for
all of that is asking for a missing `brightYellow` and a foreground you cannot
read on the background it was given.

So a model is asked for the nine that need taste — light or dark, a
background, a foreground, an accent and six hues — and everything else is
worked out here. That is the same bargain `derived()` and `termTokens()` make
in the front end: a theme is one small block and the mechanical parts are
computed rather than remembered.

The contrast pass is the reason this is worth doing server-side. A theme whose
foreground disappears into its background is not a bad theme, it is a lockout:
the settings sheet that would let you pick a different one is drawn in the same
colours. So every colour that carries meaning is pushed away from the
background until it can be read, before the theme is ever stored.
"""

from __future__ import annotations

import re

#: The hues a caller has to supply. Black and white are derived from the
#: background and foreground, because a terminal's black *is* its background
#: in every theme worth having.
HUES = ("red", "green", "yellow", "blue", "magenta", "cyan")

#: Body text has to clear WCAG AA for small text with room to spare; a
#: terminal is small text on a large surface for hours at a time.
FG_CONTRAST = 7.0
#: Secondary text: timestamps, paths, the dim half of the sidebar.
DIM_CONTRAST = 4.5
#: ANSI colours. Held lower on purpose — forcing a green to 4.5 on a dark
#: background turns every theme's green into the same pale mint.
HUE_CONTRAST = 3.5

_HEX = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


class ThemeError(ValueError):
    """The seed could not be turned into a theme, with the reason."""


# ------------------------------------------------------------------- colour


def parse(value: str) -> tuple[int, int, int]:
    """`#abc` or `#aabbcc` to a triple. Anything else is an error, not a guess."""
    if not isinstance(value, str) or not _HEX.match(value.strip()):
        raise ThemeError(f"not a hex colour: {value!r}")
    text = value.strip().lstrip("#")
    if len(text) == 3:
        text = "".join(c * 2 for c in text)
    return tuple(int(text[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def hexof(rgb: tuple[float, float, float]) -> str:
    """A triple back to hex. Takes floats, because every mix produces them."""
    return "#" + "".join(f"{max(0, min(255, round(c))):02x}" for c in rgb)


def mix(a: str, b: str, amount: float) -> str:
    """`amount` of the way from `a` to `b`."""
    x, y = parse(a), parse(b)
    return hexof(tuple(x[i] + (y[i] - x[i]) * amount for i in range(3)))


def luminance(value: str) -> float:
    """Relative luminance, the WCAG definition."""
    channels = []
    for c in parse(value):
        s = c / 255
        channels.append(s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4)
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def contrast(a: str, b: str) -> float:
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def readable(colour: str, background: str, target: float) -> str:
    """Push `colour` away from `background` until it can be read on it.

    Away means toward white on a dark background and toward black on a light
    one, in small steps, keeping as much of the original hue as the target
    allows. A colour that already clears the bar is returned untouched, which
    is the common case: this only fires on the ones a model got wrong.
    """
    if contrast(colour, background) >= target:
        return colour
    goal = "#ffffff" if luminance(background) < 0.5 else "#000000"
    best = colour
    for step in range(1, 21):
        best = mix(colour, goal, step / 20)
        if contrast(best, background) >= target:
            return best
    return best


# -------------------------------------------------------------- derivation


def _panel(base: str, bg: str, fg: str, accent: str) -> dict:
    """The nine panel tokens, as steps between the background and the text.

    Light and dark use the same ladder because "toward the foreground" already
    means darker on a light theme and lighter on a dark one. `field` is the
    exception: an input on a light theme is the page's own white, while on a
    dark one it has to lift off the surface to look like somewhere you type.
    """
    light = base == "light"
    return {
        "bg": bg,
        "panel": mix(bg, fg, 0.05),
        "row": mix(bg, fg, 0.09),
        "sel": mix(accent, bg, 0.65 if light else 0.72),
        "field": bg if light else mix(bg, fg, 0.14),
        "fg": fg,
        "dim": readable(mix(bg, fg, 0.60), bg, DIM_CONTRAST),
        "line": mix(bg, fg, 0.13),
        "accent": accent,
    }


def _term(base: str, bg: str, fg: str, accent: str, hues: dict) -> dict:
    """The eighteen terminal values, brights included.

    Brights are each hue carried a third of the way toward the foreground,
    which lightens them on a dark theme and darkens them on a light one, so a
    single rule covers both. `black` and `white` are the ends of the ramp
    rather than colours in their own right, which is what every hand-written
    theme in `themes.js` already does.
    """
    light = base == "light"
    out = {
        "background": bg,
        "foreground": fg,
        "cursor": fg,
        "selectionBackground": mix(accent, bg, 0.55 if light else 0.62),
        "black": fg if light else mix(bg, "#000000", 0.55),
        "white": mix(fg, bg, 0.45) if light else mix(fg, "#ffffff", 0.25),
    }
    for name in HUES:
        colour = readable(hues[name], bg, HUE_CONTRAST)
        out[name] = colour
        bright = mix(colour, fg, 0.33)
        out["bright" + name.capitalize()] = readable(bright, bg, HUE_CONTRAST)
    out["brightBlack"] = readable(mix(bg, fg, 0.45), bg, DIM_CONTRAST)
    out["brightWhite"] = fg if light else mix(fg, "#ffffff", 0.45)
    return out


def build(seed: dict) -> dict:
    """A seed to a complete theme, or `ThemeError` saying which part was wrong.

    The seed is what a person or a model supplies: `label`, `base`, `bg`, `fg`,
    `accent` and the six hues. Everything else follows, and the foreground is
    forced to clear the background before anything is derived from it — every
    other token is a step along that line, so fixing it first fixes the rest.
    """
    if not isinstance(seed, dict):
        raise ThemeError("a theme seed has to be an object")
    base = str(seed.get("base") or "dark").strip().lower()
    if base not in {"light", "dark"}:
        raise ThemeError(f"base has to be 'light' or 'dark', not {base!r}")

    label = " ".join(str(seed.get("label") or "").split())[:40] or "Generated"
    bg = hexof(parse(seed.get("bg")))
    fg = readable(hexof(parse(seed.get("fg"))), bg, FG_CONTRAST)
    accent = readable(hexof(parse(seed.get("accent"))), bg, HUE_CONTRAST)

    missing = [name for name in HUES if not seed.get(name)]
    if missing:
        raise ThemeError("missing colours: " + ", ".join(missing))
    hues = {name: hexof(parse(seed[name])) for name in HUES}

    return {
        "label": label,
        "base": base,
        "panel": _panel(base, bg, fg, accent),
        "term": _term(base, bg, fg, accent, hues),
    }


def audit(theme: dict) -> list[str]:
    """Everything in a finished theme that still cannot be read. Empty is good.

    `build` already forces each of these, so a non-empty list means either a
    bug in the ladder above or a theme that arrived some other way. It is what
    the checks assert against, and what refuses a hand-posted theme.
    """
    panel, term = theme.get("panel") or {}, theme.get("term") or {}
    bad = []
    if contrast(panel.get("fg", "#000"), panel.get("bg", "#fff")) < FG_CONTRAST:
        bad.append("panel text on the panel background")
    if contrast(panel.get("dim", "#000"), panel.get("bg", "#fff")) < DIM_CONTRAST:
        bad.append("dimmed text on the panel background")
    background = term.get("background", "#fff")
    if contrast(term.get("foreground", "#000"), background) < FG_CONTRAST:
        bad.append("terminal text on the terminal background")
    for name in HUES:
        for key in (name, "bright" + name.capitalize()):
            if contrast(term.get(key, "#000"), background) < HUE_CONTRAST:
                bad.append(f"{key} on the terminal background")
    return bad
