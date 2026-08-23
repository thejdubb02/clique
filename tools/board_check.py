#!/usr/bin/env python3
"""Verify the board — sessions grouped by what they are doing — in a browser.

A session marked waiting lands under "Needs you"; a killed one lands under
"Stopped". The columns fill from workState, the same authoritative status the
sidebar uses, so this checks a card ends up in the right place.

Its own home, port and tmux socket; nothing touches a panel you are using.

    ~/.cache/clique-visual/bin/python tools/board_check.py

Exit status is 0 on pass, 1 on fail.
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
from clique.auth import COOKIE_NAME, Auth

PASSWORD = "board-check"  # noqa: S105 — a throwaway panel on loopback
PORT = 3280
BASE = f"http://127.0.0.1:{PORT}"
HOME = Path("/tmp/clique-board-check-home")
SOCKET = "clique-board-check"


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
    proc.kill()
    raise SystemExit(f"the check's own panel never came up on {PORT}")


def main() -> int:
    from playwright.sync_api import sync_playwright

    proc = _panel()
    res: dict[str, object] = {}
    try:
        auth = Auth(PASSWORD, HOME / "secret")
        with sync_playwright() as play:
            browser = play.chromium.launch()
            ctx = browser.new_context(viewport={"width": 1400, "height": 900})
            ctx.add_cookies(
                [{"name": COOKIE_NAME, "value": auth.issue(), "domain": "127.0.0.1", "path": "/"}]
            )
            page = ctx.new_page()
            errs: list[str] = []
            page.on("pageerror", lambda e: errs.append(str(e)))
            page.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
            page.goto(BASE, wait_until="networkidle")
            page.wait_for_timeout(1200)

            page.evaluate("""async () => {
              const r = await fetch('/api/sessions', {method:'POST',
                headers:{'Content-Type':'application/json'},
                body: JSON.stringify({cli:'shell', cwd:'/tmp', name:'waiter'})});
              const id = (await r.json()).id;
              await fetch('/api/sessions/'+id+'/attention', {method:'POST',
                headers:{'Content-Type':'application/json'},
                body: JSON.stringify({state:'waiting'})});
              return id;
            }""")
            page.evaluate("""async () => {
              const r = await fetch('/api/sessions', {method:'POST',
                headers:{'Content-Type':'application/json'},
                body: JSON.stringify({cli:'shell', cwd:'/tmp', name:'dead'})});
              const id = (await r.json()).id;
              await fetch('/api/sessions/'+id+'/kill', {method:'POST', body:'{}'});
              return id;
            }""")
            page.wait_for_timeout(1500)
            page.evaluate("async () => { await refresh(); }")
            page.wait_for_timeout(400)

            page.locator("#moreBtn").click()
            page.wait_for_timeout(150)
            page.get_by_role("button", name="Board", exact=True).click()
            page.wait_for_timeout(400)
            res["board_open"] = page.evaluate("() => !document.getElementById('board').hidden")
            # Map each column's label -> the card names it holds.
            cols = page.evaluate("""() => {
              const out = {};
              for (const col of document.querySelectorAll('#boardCols .board-col')) {
                const head = (col.querySelector('.board-col-head')||{}).textContent || '';
                const label = head.split(' · ')[0];
                out[label] = [...col.querySelectorAll('.board-card-name')].map(e => e.textContent);
              }
              return out;
            }""")
            res["columns"] = cols
            res["waiter_in_needs_you"] = "waiter" in (cols.get("Needs you") or [])
            res["dead_in_stopped"] = "dead" in (cols.get("Stopped") or [])
            res["errors"] = errs[:4]
            browser.close()
    finally:
        proc.terminate()
        subprocess.run(
            ["tmux", "-L", SOCKET, "kill-server"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    ok = (
        res.get("board_open")
        and res.get("waiter_in_needs_you")
        and res.get("dead_in_stopped")
        and not res.get("errors")
    )
    for key, value in res.items():
        good = (not value) if key == "errors" else bool(value)
        print(f"  {'ok  ' if good else 'FAIL'} {key}: {value}")
    print("board_check:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
