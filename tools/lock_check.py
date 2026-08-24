#!/usr/bin/env python3
"""Verify the read-only review lock, in a real (headless) browser.

Feature: locking a session for review holds back *all* input to it — the prompt,
Run/Shell, the mobile keys, and live terminal typing — so reading a pane cannot
accidentally send into it. Locking shows on three surfaces (a lit lock button, a
dimmed prompt, a "read-only" tag on the terminal, and a lock on the tab), and it
is per-session and remembered in the browser.

This drives the real thing and proves the part that matters — a send while
locked does not reach the pane, and does again once unlocked:

    ~/.cache/clique-visual/bin/python tools/lock_check.py

Its own home, port and tmux socket; nothing touches a panel you are using.
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

PASSWORD = "lock-check"  # noqa: S105 — a throwaway panel on loopback
PORT = 3287
BASE = f"http://127.0.0.1:{PORT}"
HOME = Path("/tmp/clique-lock-check-home")
SOCKET = "clique-lock-check"


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


DOM = """(id) => {
  const tab = document.querySelector('.tab[data-id="' + id + '"]');
  return {
    inputbar: document.getElementById('inputbar').classList.contains('locked'),
    prompt: document.getElementById('prompt').readOnly,
    button: document.getElementById('reviewLock').classList.contains('on'),
    termwrap: document.getElementById('termwrap').classList.contains('review-locked'),
    run: document.getElementById('run').disabled,
    tab: tab ? tab.className.includes('locked') : false,
    state: reviewLockedOf(id),
  };
}"""


def _cap(mux: str) -> str:
    return subprocess.run(
        ["tmux", "-L", SOCKET, "capture-pane", "-p", "-t", mux],
        capture_output=True,
        text=True,
    ).stdout


def main() -> int:
    from playwright.sync_api import sync_playwright

    proc = _panel()
    res: dict[str, object] = {}
    try:
        auth = Auth(PASSWORD, HOME / "secret")
        with sync_playwright() as play:
            browser = play.chromium.launch()
            ctx = browser.new_context(viewport={"width": 1300, "height": 850})
            ctx.add_cookies(
                [{"name": COOKIE_NAME, "value": auth.issue(), "domain": "127.0.0.1", "path": "/"}]
            )
            page = ctx.new_page()
            errs: list[str] = []
            page.on("pageerror", lambda e: errs.append(str(e)))
            page.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
            page.goto(BASE, wait_until="networkidle")
            page.wait_for_timeout(900)

            sid = page.evaluate("""async () => {
              const r = await fetch('/api/sessions', {method:'POST',
                headers:{'Content-Type':'application/json'},
                body: JSON.stringify({cli:'shell', cwd:'/tmp', name:'reviewme'})});
              const id = (await r.json()).id; await refresh(); openSession(id); await refresh();
              return id;
            }""")
            mux = "sm-" + sid.replace("-", "")[:8]
            page.wait_for_timeout(2000)

            res["unlocked_clean"] = all(v is False for v in page.evaluate(DOM, sid).values())

            page.locator("#reviewLock").click()
            page.wait_for_timeout(400)
            res["locked_all_surfaces"] = all(page.evaluate(DOM, sid).values())

            # The proof: a send while locked must not reach the pane.
            page.evaluate("() => run('echo BLOCKED_WHILE_LOCKED')")
            page.wait_for_timeout(1200)
            res["locked_send_blocked"] = "BLOCKED_WHILE_LOCKED" not in _cap(mux)

            # Unlock: state clears, and the same send now lands.
            page.locator("#reviewLock").click()
            page.wait_for_timeout(300)
            res["unlock_clears"] = all(v is False for v in page.evaluate(DOM, sid).values())
            page.evaluate("() => run('echo NOW_UNLOCKED')")
            page.wait_for_timeout(1500)
            res["unlocked_send_lands"] = "NOW_UNLOCKED" in _cap(mux)

            # The lock is remembered across a reload.
            page.locator("#reviewLock").click()
            page.wait_for_timeout(300)
            page.reload(wait_until="networkidle")
            page.wait_for_timeout(1500)
            res["survives_reload"] = page.evaluate("(id) => reviewLockedOf(id)", sid) is True

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

    ok = all(v for k, v in res.items() if k != "errors") and not res.get("errors")
    for key, value in res.items():
        good = (not value) if key == "errors" else bool(value)
        print(f"  {'ok  ' if good else 'FAIL'} {key}: {value}")
    print("lock_check:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
