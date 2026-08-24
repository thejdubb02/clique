#!/usr/bin/env python3
"""Verify duplicate-a-session (fork), in a real (headless) browser.

Feature: "Duplicate — same directory, fresh CLI" makes a second, independent
session with the source's cli, cwd, folder and name, its own live process, and
opens it. No new endpoint — it reuses create_session's own fields.

    ~/.cache/clique-visual/bin/python tools/duplicate_check.py

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

PASSWORD = "duplicate-check"  # noqa: S105 — a throwaway panel on loopback
PORT = 3290
BASE = f"http://127.0.0.1:{PORT}"
HOME = Path("/tmp/clique-duplicate-check-home")
SOCKET = "clique-duplicate-check"


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

            sid = page.evaluate("""async () => {
              const r = await fetch('/api/sessions', {method:'POST',
                headers:{'Content-Type':'application/json'},
                body: JSON.stringify({cli:'shell', cwd:'/tmp', name:'my work'})});
              const id = (await r.json()).id; await refresh(); openSession(id); await refresh();
              return id;
            }""")
            page.wait_for_timeout(1500)
            before = page.evaluate("() => state.sessions.length")
            page.evaluate("(id) => duplicateSession(session(id))", sid)
            page.wait_for_timeout(1800)

            res["one_more_session"] = page.evaluate("() => state.sessions.length") == before + 1
            r = page.evaluate(
                """(id) => {
              const src = state.sessions.find(x => x.id === id);
              const other = state.sessions.find(x => x.id !== id);
              return { cli: other.cli === src.cli, cwd: other.cwd === src.cwd,
                       name: other.name === src.name, distinct: other.id !== src.id,
                       opened: activeId === other.id, alive: !!other.alive };
            }""",
                sid,
            )
            res["same_cli"] = r["cli"]
            res["same_cwd"] = r["cwd"]
            res["same_name"] = r["name"]
            res["distinct_id"] = r["distinct"]
            res["opens_the_fork"] = r["opened"]
            res["fork_is_alive"] = r["alive"]
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
    print("duplicate_check:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
