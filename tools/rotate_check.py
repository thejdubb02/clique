#!/usr/bin/env python3
"""Verify the theme rotation against a real panel.

Wear a different theme every so often, chosen at random from the ones you
ticked. What it holds down:

1. "Change now" puts on a different theme, and never the one already on.
2. A slot that has passed rotates on the next poll, and only once.
3. A slot that has not been reached yet does not.
4. Switched off, nothing happens however long ago the last slot was.
5. An empty pool is a 400 rather than a silent no-op, because a person who
   turned the rotation on and picked nothing has not finished.
6. The built-in dark theme's id is the empty string, and it is a choice like
   any other. Reading it as "nothing" is a bug this has now had twice.
7. A time that is not a time is refused and the old one kept, because a
   rotation anchored on nonsense would just quietly never fire.

Its own home, port and tmux socket; nothing touches a panel you are using.

    python3 tools/rotate_check.py

Exit status is 0 on pass, 1 on fail.
"""

from __future__ import annotations

import datetime
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PASSWORD = "rotate-check"  # noqa: S105 — a throwaway panel on loopback
PORT = 3288
BASE = f"http://127.0.0.1:{PORT}"
HOME = Path("/tmp/clique-rotate-check-home")
SOCKET = "clique-rotate-check"

#: Real preset ids from web/themes.js. The server never reads that file, so
#: these are only ids to it — but using real ones keeps the check honest about
#: what a person would actually tick.
POOL = ["dracula", "nord", "gruvbox", "tokyonight"]


def _panel() -> tuple[subprocess.Popen, str]:
    shutil.rmtree(HOME, ignore_errors=True)
    HOME.mkdir(parents=True)
    env = dict(os.environ, CLIQUE_HOME=str(HOME), CLIQUE_TMUX_SOCKET=SOCKET)
    subprocess.run(
        [sys.executable, "-m", "clique", "password"],
        input=f"{PASSWORD}\n{PASSWORD}\n",
        text=True,
        env=env,
        cwd=str(ROOT),
        capture_output=True,
    )
    mint = subprocess.run(
        [sys.executable, "-m", "clique", "token", "create", "admin"],
        env=env,
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    ).stdout
    token = next((ln.strip() for ln in mint.splitlines() if ln.strip().startswith("mxp_")), "")
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
            return proc, token
        except (urllib.error.URLError, OSError):
            time.sleep(0.25)
    proc.kill()
    raise SystemExit(f"the check's own panel never came up on {PORT}")


def _hhmm(offset_minutes: int) -> str:
    """A wall-clock time this many minutes from now, on the server's clock."""
    when = datetime.datetime.now() + datetime.timedelta(minutes=offset_minutes)
    return when.strftime("%H:%M")


def main() -> int:
    proc, token = _panel()

    def api(path, method="GET", body=None):
        req = urllib.request.Request(
            BASE + path,
            data=(json.dumps(body).encode() if body is not None else None),
            method=method,
            headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                return json.loads(r.read() or "{}"), r.status
        except urllib.error.HTTPError as err:
            return json.loads(err.read() or "{}"), err.code

    def settings(**fields):
        api("/api/settings", "PATCH", fields)

    def theme():
        return api("/api/state")[0]["settings"]["theme"]

    res: dict[str, object] = {}
    try:
        # Nothing ticked yet: the button has to say so rather than shrug.
        _, status = api("/api/themes/rotate", "POST", {})
        res["an_empty_pool_is_refused"] = status == 400

        settings(theme_rotate_pool=POOL, theme="dracula", theme_rotate=False)
        picked, status = api("/api/themes/rotate", "POST", {})
        res["change_now_works_with_it_switched_off"] = status == 200
        res["change_now_changed_the_theme"] = theme() == picked.get("theme") != "dracula"
        res["and_it_chose_from_the_pool"] = picked.get("theme") in POOL

        # A slot an hour ago, never acted on: the next poll should take it.
        settings(
            theme="dracula",
            theme_rotate=True,
            theme_rotate_hours=24,
            theme_rotate_at=_hhmm(-60),
            theme_rotate_last=0,
        )
        api("/api/state")
        res["a_slot_that_passed_rotates"] = theme() != "dracula"

        # ... and only once. A second poll in the same slot changes nothing.
        settled = theme()
        api("/api/state")
        res["and_does_not_rotate_again_in_the_same_slot"] = theme() == settled

        # A slot still to come today, with the interval a day: nothing yet.
        settings(theme="dracula", theme_rotate_at=_hhmm(90), theme_rotate_last=int(time.time()))
        api("/api/state")
        res["a_slot_still_to_come_waits"] = theme() == "dracula"

        # Switched off, with a slot long past and never acted on.
        settings(
            theme="dracula", theme_rotate=False, theme_rotate_at=_hhmm(-60), theme_rotate_last=0
        )
        api("/api/state")
        res["switched_off_stays_put"] = theme() == "dracula"

        # The built-in dark theme's id is the empty string, which is a real
        # choice and not a missing value. Dropping it as falsy kept the first
        # theme in the list out of the rotation entirely.
        api("/api/settings", "PATCH", {"theme_rotate_pool": ["", "nord"], "theme": "nord"})
        kept = api("/api/state")[0]["settings"]["theme_rotate_pool"]
        res["the_built_in_can_be_in_the_pool"] = kept == ["", "nord"]
        picked, _ = api("/api/themes/rotate", "POST", {})
        res["and_can_be_rotated_onto"] = picked.get("theme") == "" and theme() == ""

        # A time that is not a time is refused, and the old one is kept.
        settings(theme_rotate_at="25:99")
        res["a_bad_time_is_refused"] = (
            api("/api/state")[0]["settings"]["theme_rotate_at"] != "25:99"
        )
    finally:
        proc.terminate()
        subprocess.run(
            ["tmux", "-L", SOCKET, "kill-server"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    ok = all(bool(v) for v in res.values())
    for key, value in res.items():
        print(f"  {'ok  ' if value else 'FAIL'} {key}: {value}")
    print("rotate_check:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
