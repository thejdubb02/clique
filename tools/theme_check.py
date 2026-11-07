#!/usr/bin/env python3
"""Verify generated themes: derivation, contrast, storage, deletion.

The thing worth protecting here is that a theme can never arrive unreadable.
The settings sheet that would let you pick a different one is drawn in the
theme you are wearing, so a foreground that vanishes into its background is
not a bad theme, it is a panel you cannot navigate to change back. Every seed
therefore goes through the same contrast ladder, whether a model produced it
or somebody posted it by hand.

    python3 tools/theme_check.py

Its own home and port, no tmux and no browser. 0 on pass, 1 on fail.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from clique import themegen
from clique.auth import COOKIE_NAME, Auth

PASSWORD = "theme-check"  # noqa: S105 — a throwaway panel on loopback
PORT = 3308
BASE = f"http://127.0.0.1:{PORT}"
HOME = Path("/tmp/clique-theme-check-home")

SEED = {
    "label": "Autumn Forest",
    "base": "dark",
    "bg": "#1a1512",
    "fg": "#e8dcc8",
    "accent": "#d98555",
    "red": "#c4463a",
    "green": "#7a8b3f",
    "yellow": "#d4a13c",
    "blue": "#5b7f8c",
    "magenta": "#a06a8a",
    "cyan": "#6a9b8f",
}

passed = failed = 0


def check(label: str, ok: bool, detail: object = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  ok   {label}")
    else:
        failed += 1
        print(f"  FAIL {label} {detail}")


def _panel() -> subprocess.Popen:
    shutil.rmtree(HOME, ignore_errors=True)
    HOME.mkdir(parents=True)
    env = dict(os.environ, CLIQUE_HOME=str(HOME), CLIQUE_TMUX_SOCKET="clique-theme-check")
    subprocess.run(
        [sys.executable, "-m", "clique", "password"],
        input=f"{PASSWORD}\n{PASSWORD}\n",
        text=True,
        env=env,
        cwd=str(ROOT),
        capture_output=True,
    )
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "clique",
            "--host",
            "127.0.0.1",
            "--port",
            str(PORT),
            "--state",
            str(HOME / "state.json"),
        ],
        env=env,
        cwd=str(ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for _ in range(80):
        try:
            urllib.request.urlopen(BASE + "/healthz", timeout=2).read()
            return proc
        except (urllib.error.URLError, OSError):
            time.sleep(0.25)
    raise SystemExit(f"the check's own panel never came up on {PORT}")


def call(
    path: str, body: dict | None = None, cookie: str = "", method: str = ""
) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        BASE + path, data=data, method=method or ("POST" if data is not None else "GET")
    )
    req.add_header("Content-Type", "application/json")
    req.add_header("Cookie", f"{COOKIE_NAME}={cookie}")
    req.add_header("Origin", BASE)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            return exc.code, json.loads(raw or b"{}")
        except ValueError:
            return exc.code, {"error": raw.decode("utf-8", "replace")[:200]}



# The `art` grids in web/themes.js. Parsed with a regex rather than a JS engine
# because that is the whole dependency saved, and the shape being checked is
# the shape of the literal: a palette, a width, a height, and rows of one
# character per cell. A row one cell short shifts every pixel after it and
# draws a figure that is wrong in a way nothing else here would notice, since
# the panel renders a ragged grid perfectly happily.
ART = re.compile(r'art:\s*\{\s*src:\s*"([^"]+)"')
# Split on the theme headers first, so a failure names the theme it is in.
BLOCK = re.compile(r"^  \"?(\w*)\"?:\s*\{$", re.M)

# A theme figure is a PNG we ship. Four things can go wrong with one and none
# of them is visible from inside the panel: the file is not there at all, it
# has no transparency and hangs a white card in the corner of the terminal, it
# still has the flat colour it was drawn on around the edges, or it is a
# four-megabyte export nobody looked at. The last one matters more here than
# it looks: the entire argument for this tool is that it is small.
MAX_KB = 400
MAX_EDGE = 1200


def check_art() -> None:
    text = (ROOT / "clique" / "web" / "themes.js").read_text()
    heads = list(BLOCK.finditer(text))
    found = 0
    for i, head in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        match = ART.search(text, head.end(), end)
        if not match:
            continue
        name = head.group(1) or "(default)"
        found += 1
        src = match.group(1)
        path = ROOT / "clique" / "web" / src
        check(f"{name}: {src} is there", path.is_file(), path)
        if not path.is_file():
            continue
        size = path.stat().st_size // 1024
        check(f"{name}: {size}KB, under {MAX_KB}", size <= MAX_KB, size)
        head_bytes = path.read_bytes()[:26]
        check(f"{name}: it is a PNG", head_bytes.startswith(b"\x89PNG\r\n\x1a\n"), head_bytes[:8])
        # Colour type 6 is RGBA and 3 is palette, which carries alpha in a tRNS
        # chunk. Either can be transparent; 0 and 2 cannot be, ever.
        colour_type = head_bytes[25] if len(head_bytes) > 25 else -1
        check(f"{name}: it can hold transparency", colour_type in (3, 4, 6), colour_type)
        width = int.from_bytes(head_bytes[16:20], "big")
        height = int.from_bytes(head_bytes[20:24], "big")
        check(f"{name}: {width}x{height}, within {MAX_EDGE}",
              0 < width <= MAX_EDGE and 0 < height <= MAX_EDGE, (width, height))
    check("themes.js carries the character art", found >= 7, found)
    _check_packaged()


def _check_packaged() -> None:
    """The wheel has to actually contain them.

    package-data is an allow-list of globs, so a new directory under web/ is
    silently left out and the panel serves 404s for it on somebody else's
    machine while working perfectly here. That has already happened once with
    web/ itself, which is why the comment above that block exists.
    """
    spec = (ROOT / "pyproject.toml").read_text()
    # The list, not the section header: the header is itself in square
    # brackets, so splitting on the first "]" finds nothing but the header.
    after = spec[spec.index("[tool.setuptools.package-data]"):]
    listing = after[after.index("clique = ["):]
    listing = listing[: listing.index("\n]")]
    check("the wheel is told to ship web/art", '"web/art/*"' in listing, listing[-120:])


def _relative(hexval: str) -> float:
    """WCAG relative luminance, the same definition app.js uses."""
    parts = [int(hexval[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in parts]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def main() -> int:
    proc = _panel()
    print("the drawings a theme can carry")
    check_art()

    try:
        cookie = Auth(PASSWORD, HOME / "secret").issue()

        print("a fresh panel ships no themes of its own")
        status, listing = call("/api/themes", cookie=cookie)
        check("the list is reachable", status == 200, status)
        check("and starts empty", listing.get("themes") == [], listing.get("themes"))
        check("and says generating is not set up yet", listing.get("can_generate") is False)

        print("a seed becomes a whole theme")
        status, made = call("/api/themes", SEED, cookie)
        check("posting a seed creates one", status == 201, (status, made))
        check("it is named", made.get("label") == "Autumn Forest", made.get("label"))
        check("it has an id", str(made.get("id", "")).startswith("t-"), made.get("id"))
        panel_keys = {"bg", "panel", "row", "sel", "field", "fg", "dim", "line", "accent"}
        check(
            "every panel token is filled in",
            panel_keys <= set(made.get("panel") or {}),
            sorted(panel_keys - set(made.get("panel") or {})),
        )
        # The whole point of deriving: all sixteen ANSI colours, brights and all,
        # which is exactly what a hand-written theme forgets.
        ansi = {c for c in ("black", "red", "green", "yellow", "blue", "magenta", "cyan", "white")}
        wanted = (
            ansi
            | {"bright" + c.capitalize() for c in ansi}
            | {"background", "foreground", "cursor", "selectionBackground"}
        )
        check(
            "and all sixteen ANSI colours, brights included",
            wanted <= set(made.get("term") or {}),
            sorted(wanted - set(made.get("term") or {})),
        )
        check("nothing in it is unreadable", themegen.audit(made) == [], themegen.audit(made))

        print("an unreadable seed is fixed, not stored or refused")
        dark_on_dark = {**SEED, "fg": "#1c1714", "green": "#12180a"}
        status, fixed = call("/api/themes", dark_on_dark, cookie)
        check("it is still accepted", status == 201, (status, fixed))
        check(
            "the text was pushed until it could be read",
            fixed.get("panel", {}).get("fg") != "#1c1714",
            fixed.get("panel", {}).get("fg"),
        )
        check(
            "and the whole thing audits clean", themegen.audit(fixed) == [], themegen.audit(fixed)
        )

        print("a seed that is not a theme is refused")
        status, err = call("/api/themes", {**SEED, "bg": "octarine"}, cookie)
        check("a colour that is not a colour is a 400", status == 400, (status, err))
        check("and says which", "octarine" in str(err.get("error", "")), err)
        status, err = call("/api/themes", {k: v for k, v in SEED.items() if k != "cyan"}, cookie)
        check("a missing hue is a 400", status == 400, (status, err))
        check("and names it", "cyan" in str(err.get("error", "")), err)
        status, err = call("/api/themes", {**SEED, "base": "beige"}, cookie)
        check("so is a base that is neither light nor dark", status == 400, (status, err))

        print("generating needs a provider, and says so")
        status, err = call("/api/themes/generate", {"prompt": "a quiet winter morning"}, cookie)
        check("it is refused without one", status == 400, (status, err))
        check("with a sentence pointing at Settings", "Settings" in str(err.get("error", "")), err)
        status, err = call("/api/themes/generate", {"prompt": "   "}, cookie)
        check("and an empty description is refused first", status == 400, (status, err))

        print("themes are kept, worn, and can be dropped")
        status, listing = call("/api/themes", cookie=cookie)
        check("both are in the list", len(listing.get("themes") or []) == 2, listing)
        call("/api/settings", {"theme": made["id"]}, cookie, "PATCH")
        _, state = call("/api/state", cookie=cookie)
        check("one can be worn", state.get("settings", {}).get("theme") == made["id"])
        status, gone = call(f"/api/themes/{made['id']}/delete", {}, cookie)
        check("deleting works", status == 200 and gone.get("ok"), (status, gone))
        _, state = call("/api/state", cookie=cookie)
        # Leaving the setting pointing at a theme that is gone is a panel that
        # comes back unpainted, with no readable way to choose another.
        check(
            "and deleting the one you are wearing falls back to the default",
            state.get("settings", {}).get("theme") == "",
            state.get("settings", {}).get("theme"),
        )
        status, listing = call("/api/themes", cookie=cookie)
        check("it is out of the list", len(listing.get("themes") or []) == 1, listing)
        status, err = call("/api/themes/t-nosuch/delete", {}, cookie)
        check("deleting one that never existed is a 404", status == 404, (status, err))

        print("the list cannot grow without limit")
        for i in range(45):
            call("/api/themes", {**SEED, "label": f"bulk {i}"}, cookie)
        status, listing = call("/api/themes", cookie=cookie)
        kept = listing.get("themes") or []
        check("it stops at the ceiling", len(kept) == 40, len(kept))
        check(
            "and it is the newest that survive",
            kept[-1].get("label") == "bulk 44",
            kept[-1].get("label"),
        )
    finally:
        proc.terminate()
        shutil.rmtree(HOME, ignore_errors=True)

    print(f"\n{passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
