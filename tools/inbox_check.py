#!/usr/bin/env python3
"""Verify the "needs you" inbox and answer-back, in a real (headless) browser.

Feature: a session waiting on the user surfaces three ways — a count on the tab
title, a lit bell with a badge, and an inbox sheet listing it — and can be
answered from that sheet without opening the pane (a typed reply, or an empty
send to accept the default). This drives all of it against a real panel:

1. Mark a session waiting (as a hook would) and confirm the badge, the lit
   bell, and the "(1) CLIque" tab title.
2. Open the inbox and confirm the session is listed.
3. Type a reply, send it, and confirm it reached the session's shell and ran.

Its own home, port and tmux socket; nothing touches a panel you are using.

    ~/.cache/clique-visual/bin/python tools/inbox_check.py

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

PASSWORD = "inbox-check"  # noqa: S105 — a throwaway panel on loopback
PORT = 3285
BASE = f"http://127.0.0.1:{PORT}"
HOME = Path("/tmp/clique-inbox-check-home")
SOCKET = "clique-inbox-check"


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
    sid = ""
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

            sid = page.evaluate("""async () => {
              const r = await fetch('/api/sessions', {method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({cli: 'shell', cwd: '/tmp', name: 'needs-me'})});
              return (await r.json()).id;
            }""")
            page.wait_for_timeout(800)
            page.evaluate(
                """async (id) => {
              await fetch('/api/sessions/' + id + '/attention', {method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({state: 'waiting', note: 'permission'})});
              await refresh();
            }""",
                sid,
            )
            page.wait_for_timeout(600)

            res["badge"] = page.evaluate("""() => ({
              text: document.getElementById('inboxCount').textContent,
              hidden: document.getElementById('inboxCount').hidden,
              lit: document.getElementById('inboxBtn').classList.contains('lit'),
            })""")
            res["title"] = page.evaluate("() => document.title")
            page.locator("#inboxBtn").click()
            page.wait_for_timeout(400)
            res["sheet_open"] = page.evaluate("() => !document.getElementById('inbox').hidden")
            res["row_name"] = page.evaluate(
                "() => { const r = document.querySelector('#inboxList .inbox-name'); return r ? r.textContent : ''; }"
            )
            # A permission prompt (note='permission') offers Approve/Deny.
            res["approve_shown"] = page.evaluate(
                "() => !!document.querySelector('#inboxList .inbox-approve') "
                "&& !!document.querySelector('#inboxList .inbox-deny')"
            )
            page.locator("#inboxList .inbox-reply").first.fill("echo inbox-reply-works")
            page.locator("#inboxList .inbox-send").first.click()
            page.wait_for_timeout(1200)
            res["errors"] = errs[:4]
            browser.close()

        cap = subprocess.run(
            ["tmux", "-L", SOCKET, "capture-pane", "-p", "-t", "sm-" + sid.replace("-", "")[:8]],
            capture_output=True,
            text=True,
        ).stdout
        res["reply_landed"] = "inbox-reply-works" in cap
    finally:
        proc.terminate()
        subprocess.run(
            ["tmux", "-L", SOCKET, "kill-server"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    badge = res.get("badge") or {}
    ok = (
        badge.get("text") == "1"
        and badge.get("hidden") is False
        and badge.get("lit") is True
        and res.get("title") == "(1) CLIque"
        and res.get("sheet_open")
        and res.get("row_name") == "needs-me"
        and res.get("approve_shown")
        and res.get("reply_landed")
        and not res.get("errors")
    )
    for key, value in res.items():
        good = (not value) if key == "errors" else bool(value)
        print(f"  {'ok  ' if good else 'FAIL'} {key}: {value}")
    print("inbox_check:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
