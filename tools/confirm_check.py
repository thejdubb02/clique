#!/usr/bin/env python3
"""Verify the destructive-command confirm, in a real (headless) browser.

Feature: a command sent from the prompt or a broadcast that matches one of the
configurable `destructive_patterns` raises a one-click confirm first — a guard
against a fat-fingered `rm -rf /`, not a block.

SAFETY: this never sends a real destructive command to a shell. It sets the
match list to one harmless sentinel and drives an `echo <sentinel>` command,
which trips the guard on the way in but only prints text if it ever lands.

    ~/.cache/clique-visual/bin/python tools/confirm_check.py

Its own home, port and tmux socket. Exit status is 0 on pass, 1 on fail.
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

PASSWORD = "confirm-check"  # noqa: S105 — a throwaway panel on loopback
PORT = 3288
BASE = f"http://127.0.0.1:{PORT}"
HOME = Path("/tmp/clique-confirm-check-home")
SOCKET = "clique-confirm-check"
SENTINEL = "danger_sentinel_xyz"  # harmless: only ever echoed, never a real command


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
                body: JSON.stringify({cli:'shell', cwd:'/tmp', name:'guardme'})});
              const id = (await r.json()).id; await refresh(); openSession(id); await refresh();
              return id;
            }""")
            mux = "sm-" + sid.replace("-", "")[:8]
            page.evaluate(
                "async (p) => { await saveSettings({destructive_patterns: [p]}); }", SENTINEL
            )
            page.wait_for_timeout(1800)

            danger = f"echo {SENTINEL}_lands"

            def fire(cmd):
                page.fill("#prompt", cmd)
                # Fire-and-forget: run() awaits its own confirm, so awaiting the
                # promise here would deadlock the test on the dialog it must click.
                page.evaluate("(c) => { run(c); }", cmd)
                page.wait_for_timeout(500)

            fire(danger)
            res["confirm_shown"] = page.evaluate(
                "() => !document.getElementById('confirmSheet').hidden"
            )
            res["names_the_pattern"] = SENTINEL in page.evaluate(
                "() => document.getElementById('confirmMsg').textContent"
            )
            res["shows_the_command"] = (
                page.evaluate("() => document.getElementById('confirmDetail').textContent")
                == danger
            )

            page.locator("#confirmNo").click()
            page.wait_for_timeout(800)
            res["cancel_closes"] = page.evaluate(
                "() => document.getElementById('confirmSheet').hidden"
            )
            res["cancel_blocks_send"] = f"{SENTINEL}_lands" not in _cap(mux)

            fire(danger)
            page.locator("#confirmOk").click()
            page.wait_for_timeout(1400)
            res["allow_sends"] = f"{SENTINEL}_lands" in _cap(mux)

            fire("echo SAFE_COMMAND_OK")
            res["safe_no_confirm"] = page.evaluate(
                "() => document.getElementById('confirmSheet').hidden"
            )
            page.wait_for_timeout(900)
            res["safe_sends"] = "SAFE_COMMAND_OK" in _cap(mux)

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
    print("confirm_check:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
