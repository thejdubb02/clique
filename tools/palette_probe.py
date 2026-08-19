"""How much of a CLI's colour a theme can actually own.

Theming a terminal is not one question, it is four, and which one applies is
decided by the application rather than by us:

  default        it printed no colour at all               — the theme owns it
  16 ANSI        "the terminal's idea of red"              — the theme owns it
  greyscale      232-255, "a shade near the background"    — relative, may tint
  cube           16-231, an exact colour from a fixed grid — absolute, hands off
  truecolor      38;2;r;g;b, an exact RGB                  — absolute, hands off

The ladder is specificity. The more precisely an application named a colour,
the less business anyone has changing it: an app asking for colour 82 wants
*that* green, and one asking for red wants whatever red means here. The
greyscale ramp is the interesting middle — it expresses a relationship to the
background rather than a colour, so honouring it against the theme's own
background is closer to the intent than neutral grey.

This measures which of those a CLI actually uses, so the answer to "will my
theme skin it" is observed rather than assumed. Nothing here is per-vendor
knowledge; it counts escape sequences.

    python3 tools/palette_probe.py [cli ...]
"""

from __future__ import annotations

import re
import sys
import time
import uuid
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from clique import tmux
from clique.__main__ import config_path
from clique.registry import Registry

SOCKET = "clique-palette"
SGR = re.compile(r"\x1b\[([0-9;]*)m")


def classify(codes: str) -> set[str]:
    """Which colour mechanisms one SGR sequence uses."""
    parts = [p for p in codes.split(";") if p != ""]
    found: set[str] = set()
    i = 0
    while i < len(parts):
        try:
            n = int(parts[i])
        except ValueError:
            i += 1
            continue
        if n in (38, 48) and i + 1 < len(parts):
            kind = parts[i + 1]
            if kind == "2":
                found.add("truecolor")
                i += 5
                continue
            if kind == "5" and i + 2 < len(parts):
                try:
                    index = int(parts[i + 2])
                except ValueError:
                    index = -1
                if index < 16:
                    found.add("16 ANSI")
                elif index < 232:
                    found.add("cube")
                else:
                    found.add("greyscale")
                i += 3
                continue
        elif 30 <= n <= 37 or 40 <= n <= 47 or 90 <= n <= 97 or 100 <= n <= 107:
            found.add("16 ANSI")
        i += 1
    return found


def probe(cli_id: str, reg: Registry, seconds: float = 5.0) -> tuple[Counter, int]:
    cli = reg.get(cli_id)
    where = cli.resolve()
    if not where:
        raise FileNotFoundError(cli.command)
    # A real uuid, not a placeholder: a CLI handed a malformed session id
    # exits immediately, and the probe then reports "not probed" for a reason
    # that has nothing to do with colour.
    argv = reg.launch_argv(cli_id, session_id=str(uuid.uuid4()), name="probe", cwd="/tmp")
    mux = f"sm-probe-{cli_id}"
    tmux.create(mux, "/tmp", argv, socket=SOCKET, width=100, height=30)
    time.sleep(seconds)
    text = tmux.capture(mux, SOCKET, lines=200, styled=True)
    tmux.kill(mux, SOCKET, force=True)

    tally: Counter = Counter()
    for codes in SGR.findall(text):
        for kind in classify(codes):
            tally[kind] += 1
    return tally, len(text)


def main() -> int:
    reg = Registry(config_path(None))
    wanted = sys.argv[1:] or [k for k, v in reg.types().items() if v.resolve()]
    tmux._run(["kill-server"], SOCKET, check=False)
    tmux.bootstrap(SOCKET, history_limit=2000)

    order = ["16 ANSI", "greyscale", "cube", "truecolor"]
    print(f"{'cli':10} " + "".join(f"{k:>11}" for k in order) + "   theme reaches")
    print("-" * 74)
    for cli_id in wanted:
        try:
            tally, _size = probe(cli_id, reg)
        except (FileNotFoundError, tmux.TmuxError) as err:
            print(f"{cli_id:10} — not probed ({type(err).__name__})")
            continue
        row = "".join(f"{tally.get(k, 0):>11}" for k in order)
        owned = tally.get("16 ANSI", 0) + tally.get("greyscale", 0)
        fixed = tally.get("cube", 0) + tally.get("truecolor", 0)
        total = owned + fixed
        share = "everything" if not fixed else (
            f"{round(100 * owned / total)}% of what it paints" if total else "n/a")
        if not total:
            share = "everything (paints nothing)"
        print(f"{cli_id:10} {row}   {share}")

    tmux._run(["kill-server"], SOCKET, check=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
