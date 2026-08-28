#!/usr/bin/env python3
"""Verify the pane settles to the right size after a layout change.

The bug this guards is old and kept coming back in new clothes: opening the
side panel, the sidebar or zen mode changes the terminal's width, and if the
refit runs before the browser has finished laying that out it measures the
*old* box. The pane comes back scaled wrong, or the right size locally while
tmux is still painting the other one, and the mismatch shows as dead space,
a stray scrollbar, or a screen of dots.

It used to be papered over with `setTimeout(settle, 80)`. This asserts the
thing the timeout was guessing at, so the guess is not needed:

- one toggle produces exactly one settle, on a frame, not a timer;
- after it, the terminal's columns match the tmux window's, both ways round;
- `refresh` reaches tmux and repaints only the browser that asked.

    ~/.cache/clique-visual/bin/python tools/redraw_check.py

Its own home, port and tmux socket. 0 on pass, 1 on fail.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from clique import tmux as tmux_mod
from clique.auth import COOKIE_NAME, Auth

PASSWORD = "redraw-check"  # noqa: S105 — a throwaway panel on loopback
PORT = 3303
BASE = f"http://127.0.0.1:{PORT}"
HOME = Path("/tmp/clique-redraw-check-home")
SOCKET = "clique-redraw-check"
WORK = Path("/tmp/clique-redraw-check-work")

# Count the settles instead of trusting a screenshot. One toggle is one
# settle: the old pair fired twice, once too early to be right.
COUNT_SETTLES = """() => {
  window.__settles = 0;
  const real = window.settlePane;
  window.settlePane = function () { window.__settles += 1; return real.apply(this, arguments); };
}"""


def _panel() -> subprocess.Popen:
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


def main() -> int:
    from playwright.sync_api import sync_playwright

    shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir(parents=True)

    proc = _panel()
    res: dict[str, object] = {}
    try:
        auth = Auth(PASSWORD, HOME / "secret")
        with sync_playwright() as play:
            browser = play.chromium.launch()
            ctx = browser.new_context(viewport={"width": 1280, "height": 860})
            ctx.add_cookies(
                [{"name": COOKIE_NAME, "value": auth.issue(), "domain": "127.0.0.1", "path": "/"}]
            )
            page = ctx.new_page()
            errs: list[str] = []
            page.on("pageerror", lambda e: errs.append(str(e)))
            page.goto(BASE, wait_until="domcontentloaded")
            page.wait_for_timeout(700)

            sid = page.evaluate(
                """async (cwd) => {
                  const r = await fetch('/api/sessions', {method:'POST',
                    headers:{'Content-Type':'application/json'},
                    body: JSON.stringify({cli:'shell', cwd, name:'redraw'})});
                  const id = (await r.json()).id;
                  await refresh(); await openSession(id); return id;
                }""",
                str(WORK),
            )
            page.wait_for_timeout(1500)
            # The tmux session name, which is not the CLIque id and is not
            # sent to the browser. One session was made here, so this is it.
            mux = next(
                s.mux
                for s in tmux_mod.list_sessions(SOCKET, prefix=tmux_mod.PREFIX)
                if not s.mux.startswith(tmux_mod.VIEW_PREFIX)
            )

            def cols() -> int:
                return page.evaluate(
                    "(id) => { const e = terms.get(id); return e && e.term ? e.term.cols : 0; }",
                    sid,
                )

            def tmux_cols() -> int:
                # list-windows, not display-message: the latter wants a client
                # and there is none on the session itself, only on the view.
                out = tmux_mod._run(
                    ["list-windows", "-t", f"={mux}", "-F", "#{window_width}"], SOCKET
                )
                return int(out.split()[0]) if out.split() else 0

            wide = cols()
            res["terminal_opened"] = wide > 40 and bool(mux)
            res["tmux_matches_at_rest"] = tmux_cols() == wide

            # One toggle, one settle — and the columns it lands on are the ones
            # tmux is painting, not the ones the pane had before the panel.
            page.evaluate(COUNT_SETTLES)
            page.evaluate("() => openPanel('notes')")
            page.wait_for_timeout(250)
            narrow = cols()
            res["panel_narrows_pane"] = narrow < wide
            res["tmux_matches_with_panel"] = tmux_cols() == narrow
            res["one_settle_per_toggle"] = page.evaluate("() => window.__settles") == 1

            # A settle is a frame, not a timer. Two frames is well inside the
            # 80ms this replaces, so a short wait has to be enough on its own.
            page.evaluate("() => { window.__settles = 0; closePanel(); }")
            page.wait_for_timeout(60)
            res["settles_within_two_frames"] = cols() == wide and tmux_cols() == wide

            # Toggling twice in a frame collapses to one settle rather than
            # queueing a second that fits against a box already gone.
            page.evaluate("() => { window.__settles = 0; openPanel('notes'); closePanel(); }")
            page.wait_for_timeout(250)
            res["rapid_toggle_settles_once"] = page.evaluate("() => window.__settles") == 1
            res["rapid_toggle_ends_wide"] = cols() == wide and tmux_cols() == wide

            # The repaint reaches tmux, finds this browser's own client, and
            # finds nothing on the session itself — so it cannot hit anyone else.
            views = [s.mux for s in tmux_mod.list_sessions(SOCKET, prefix=tmux_mod.VIEW_PREFIX)]
            res["viewer_exists"] = len(views) == 1
            if views:
                clients = tmux_mod._run(
                    ["list-clients", "-t", f"={views[0]}", "-F", "#{client_tty}"], SOCKET
                ).split()
                res["viewer_has_one_client"] = len(clients) == 1
                # tmux 3.4 errors rather than printing nothing when a target
                # session has no clients, so both shapes mean "none".
                try:
                    own = tmux_mod._run(
                        ["list-clients", "-t", f"={mux}", "-F", "#{client_tty}"], SOCKET
                    ).strip()
                except tmux_mod.TmuxError:
                    own = ""
                res["session_has_no_client"] = not own
                tmux_mod.refresh_client(views[0], SOCKET)
                res["refresh_client_survives"] = tmux_mod.exists(mux, SOCKET)

            # And the socket takes the message the browser actually sends.
            page.evaluate("() => repaintPane()")
            page.wait_for_timeout(300)
            res["repaint_keeps_socket"] = page.evaluate(
                "(id) => { const e = terms.get(id); return Boolean(e && e.ws && e.ws.readyState === 1); }",
                sid,
            )

            res["errors"] = errs[:6]
            browser.close()
    finally:
        proc.terminate()
        subprocess.run(
            ["tmux", "-L", SOCKET, "kill-server"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        shutil.rmtree(HOME, ignore_errors=True)
        shutil.rmtree(WORK, ignore_errors=True)

    ok = all(v for k, v in res.items() if k != "errors") and not res.get("errors")
    for key, value in res.items():
        good = (not value) if key == "errors" else bool(value)
        print(f"  {'ok  ' if good else 'FAIL'} {key}: {value}")
    print("redraw_check:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
