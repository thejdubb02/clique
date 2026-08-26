#!/usr/bin/env python3
"""Verify per-session notes, end to end, in a real (headless) browser.

Feature: each session has a scratchpad note, edited in a sheet and persisted as
a sidecar `.md` under the panel's home. This drives the real UI (openNote, the
textarea, the Save button) and confirms:

- a note saves to `<CLIQUE_HOME>/notes/<id>.md` and reloads on reopen;
- emptying the note deletes the file;
- the sheet shows and clears correctly.

    ~/.cache/clique-visual/bin/python tools/notes_check.py

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
from clique.auth import COOKIE_NAME, Auth

PASSWORD = "notes-check"  # noqa: S105 — a throwaway panel on loopback
PORT = 3301
BASE = f"http://127.0.0.1:{PORT}"
HOME = Path("/tmp/clique-notes-check-home")
SOCKET = "clique-notes-check"
WORK = Path("/tmp/clique-notes-check-work")
NOTE = "TODO: wire the widget. Left off at store.py line 40. #b3a91f"


def _new_session(page, cwd: Path) -> str:
    return page.evaluate(
        """async (cwd) => {
      const r = await fetch('/api/sessions', {method:'POST',
        headers:{'Content-Type':'application/json'},
        body: JSON.stringify({cli:'shell', cwd, name:'w'})});
      const id = (await r.json()).id; await refresh(); return id;
    }""",
        str(cwd),
    )


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

    shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir(parents=True)

    proc = _panel()
    res: dict[str, object] = {}
    out_png = Path("/tmp/clique-notes-check.png")
    try:
        auth = Auth(PASSWORD, HOME / "secret")
        with sync_playwright() as play:
            browser = play.chromium.launch()
            ctx = browser.new_context(viewport={"width": 1200, "height": 850})
            ctx.add_cookies(
                [{"name": COOKIE_NAME, "value": auth.issue(), "domain": "127.0.0.1", "path": "/"}]
            )
            page = ctx.new_page()
            errs: list[str] = []
            page.on("pageerror", lambda e: errs.append(str(e)))
            page.on(
                "console",
                lambda m: (
                    errs.append(m.text)
                    if m.type == "error" and "Failed to load resource" not in m.text
                    else None
                ),
            )
            page.goto(BASE, wait_until="domcontentloaded")
            page.wait_for_timeout(700)

            sid = _new_session(page, WORK)
            page.wait_for_timeout(400)

            # Open the note sheet, type, screenshot, save+close.
            page.evaluate("(id) => openNote(session(id))", sid)
            page.wait_for_timeout(500)
            res["sheet_shown"] = not page.evaluate(
                "() => document.getElementById('noteSheet').hidden"
            )
            page.fill("#noteText", NOTE)
            page.screenshot(path=str(out_png))
            page.click("#noteSave")
            page.wait_for_timeout(500)
            res["status_saved"] = (
                page.evaluate("() => document.getElementById('noteStatus').textContent") == "Saved"
            )

            note_file = HOME / "notes" / f"{sid}.md"
            res["file_written"] = note_file.is_file() and note_file.read_text() == NOTE

            # Close, reopen, and confirm it reloads from disk.
            page.evaluate("() => closeNote()")
            page.wait_for_timeout(200)
            res["sheet_closed"] = page.evaluate("() => document.getElementById('noteSheet').hidden")
            page.evaluate("(id) => openNote(session(id))", sid)
            page.wait_for_timeout(500)
            res["reloaded"] = (
                page.evaluate("() => document.getElementById('noteText').value") == NOTE
            )

            # Emptying the note deletes the file.
            page.fill("#noteText", "")
            page.click("#noteSave")
            page.wait_for_timeout(500)
            res["emptied_deletes"] = not note_file.exists()

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
    print(f"screenshot: {out_png}")
    print("notes_check:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
