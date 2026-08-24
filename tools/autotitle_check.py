#!/usr/bin/env python3
"""Verify auto-title-from-first-prompt, in a real (headless) browser.

Feature: a session whose name is still auto-derived (the directory basename, the
CLI, or empty) is renamed from the first prompt you send it, so a tray of "tmp"
and "shell" becomes the work you are doing. A session you named yourself is left
alone, a too-thin prompt does not title, and it never re-titles.

    ~/.cache/clique-visual/bin/python tools/autotitle_check.py

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

PASSWORD = "autotitle-check"  # noqa: S105 — a throwaway panel on loopback
PORT = 3289
BASE = f"http://127.0.0.1:{PORT}"
HOME = Path("/tmp/clique-autotitle-check-home")
SOCKET = "clique-autotitle-check"


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
            ctx = browser.new_context(viewport={"width": 1200, "height": 800})
            ctx.add_cookies(
                [{"name": COOKIE_NAME, "value": auth.issue(), "domain": "127.0.0.1", "path": "/"}]
            )
            page = ctx.new_page()
            errs: list[str] = []
            page.on("pageerror", lambda e: errs.append(str(e)))
            page.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
            page.goto(BASE, wait_until="networkidle")
            page.wait_for_timeout(900)

            def mk_open(name=None):
                body = {"cli": "shell", "cwd": "/tmp"}
                if name is not None:
                    body["name"] = name
                return page.evaluate(
                    """async (b) => {
                  const r = await fetch('/api/sessions', {method:'POST',
                    headers:{'Content-Type':'application/json'}, body: JSON.stringify(b)});
                  const id = (await r.json()).id; await refresh(); openSession(id); await refresh();
                  return id;
                }""",
                    body,
                )

            def name_of(sid):
                return page.evaluate("(id) => (state.sessions.find(x=>x.id===id)||{}).name", sid)

            def send(cmd):
                page.fill("#prompt", cmd)
                page.evaluate("(c) => { run(c); }", cmd)
                page.wait_for_timeout(800)

            a = mk_open()
            page.wait_for_timeout(1500)
            res["generic_starts_as_dir"] = name_of(a) == "tmp"
            send("fix the login bug in auth.py")
            res["titled_from_prompt"] = name_of(a) == "fix the login bug in auth.py"
            send("now do something else entirely different")
            res["does_not_retitle"] = name_of(a) == "fix the login bug in auth.py"

            b = mk_open("keep me")
            page.wait_for_timeout(1200)
            send("this must not rename the session")
            res["user_name_kept"] = name_of(b) == "keep me"

            c = mk_open()
            page.wait_for_timeout(1200)
            send("y")
            res["thin_prompt_no_title"] = name_of(c) == "tmp"

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
    print("autotitle_check:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
