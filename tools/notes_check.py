#!/usr/bin/env python3
"""Verify per-session notes, end to end, in a real (headless) browser.

Feature: each session has a notes outline in the docked side panel — a nested
checklist with checkboxes, reminders and send-to-terminal, persisted as a
sidecar `.json` under the panel's home. This drives the real UI (the rail, the
Add-note button, contenteditable rows, Tab to nest, the checkbox, the reminder
popover) and confirms:

- adding and typing a note saves to `<CLIQUE_HOME>/notes/<id>.json`;
- Enter adds a sibling and Tab nests it (the tree persists);
- the checkbox marks an item done in the file;
- the reminder popover writes a `remindAt`;
- the outline reloads from disk after a full page reload;
- switching sessions mid-edit saves the edit to the session that was edited,
  and leaves the session switched *to* with its own notes intact;
- the rail switches panes and the close button hides the panel.

    ~/.cache/clique-visual/bin/python tools/notes_check.py

Its own home, port and tmux socket. 0 on pass, 1 on fail.
"""

from __future__ import annotations

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
sys.path.insert(0, str(ROOT))
from clique import notes as notes_mod
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
            # Make it the session in front, so the panel has something to scope to.
            page.evaluate("(id) => openSession(id)", sid)
            page.wait_for_timeout(500)

            note_file = HOME / "notes" / f"{sid}.json"

            def read_items():
                if not note_file.is_file():
                    return None
                return json.loads(note_file.read_text()).get("items")

            # Open the panel to Notes via the rail.
            page.click('.railr-btn[data-pane="notes"]')
            page.wait_for_timeout(400)
            res["panel_shown"] = not page.evaluate(
                "() => document.getElementById('sidepanel').hidden"
            )
            res["rail_active"] = page.evaluate(
                "() => document.querySelector('.railr-btn[data-pane=\"notes\"]')"
                ".classList.contains('active')"
            )

            # Add a note and type into it (the new row is focused for us).
            page.click('button:has-text("Add note")')
            page.wait_for_timeout(150)
            page.keyboard.type(NOTE)
            # Enter for a sibling, then Enter + Tab to nest a child under it.
            page.keyboard.press("Enter")
            page.keyboard.type("second")
            page.keyboard.press("Enter")
            page.keyboard.press("Tab")
            page.keyboard.type("child")
            page.wait_for_timeout(900)  # past the debounced save
            page.screenshot(path=str(out_png))

            items = read_items()
            res["file_written"] = bool(items) and items[0]["text"] == NOTE
            res["sibling_added"] = bool(items) and len(items) == 2 and items[1]["text"] == "second"
            res["child_nested"] = (
                bool(items)
                and len(items) == 2
                and len(items[1]["children"]) == 1
                and items[1]["children"][0]["text"] == "child"
            )

            # The checkbox marks the first item done.
            page.hover("#notesList .note-item")
            page.click("#notesList .note-check")
            page.wait_for_timeout(900)
            items = read_items()
            res["checkbox_done"] = bool(items) and items[0]["done"] is True

            # The reminder popover writes a remindAt on the first item.
            page.hover("#notesList .note-item")
            page.click("#notesList .note-item:first-child .note-row .note-act >> nth=1")
            page.wait_for_timeout(200)
            res["remind_popover"] = not page.evaluate(
                "() => document.getElementById('remindPop').hidden"
            )
            page.fill("#remindAt", "2030-06-01T09:00")
            page.click("#remindSet")
            page.wait_for_timeout(900)
            items = read_items()
            res["reminder_set"] = (
                bool(items) and isinstance(items[0]["remindAt"], int) and items[0]["remindAt"] > 0
            )

            # Switching sessions inside the 600ms debounce window. The edit
            # belongs to the session it was made in, and the session switched to
            # keeps the notes it already had on disk. Getting this wrong used to
            # send an empty outline to the new session, which deletes its file.
            other = _new_session(page, WORK)
            other_file = HOME / "notes" / f"{other}.json"
            notes_mod.save(other_file, notes_mod.sanitize([{"text": "do not clobber me"}]))
            page.hover("#notesList .note-item")
            page.click("#notesList .note-check")  # queues a save for this session
            page.evaluate("(id) => openSession(id)", other)  # well inside the debounce
            page.wait_for_timeout(1500)
            survived = (
                json.loads(other_file.read_text()).get("items") if other_file.is_file() else None
            )
            res["switch_kept_other"] = bool(survived) and survived[0]["text"] == "do not clobber me"
            items = read_items()
            res["switch_saved_edit"] = bool(items) and items[0]["done"] is False
            page.evaluate("(id) => openSession(id)", sid)
            page.wait_for_timeout(700)

            # A full reload restores the panel (localStorage) and reloads from disk.
            page.reload(wait_until="domcontentloaded")
            page.wait_for_timeout(1200)
            res["reloaded_open"] = not page.evaluate(
                "() => document.getElementById('sidepanel').hidden"
            )
            res["reloaded_text"] = (
                page.evaluate(
                    "() => (document.querySelector('#notesList .note-text')||{}).textContent"
                )
                == NOTE
            )

            # The rail switches panes, and Close hides the panel.
            page.click('.railr-btn[data-pane="git"]')
            page.wait_for_timeout(300)
            res["pane_switch"] = (
                page.evaluate("() => document.getElementById('panelTitle').textContent") == "Git"
            )
            page.click("#panelClose")
            page.wait_for_timeout(300)
            res["panel_closed"] = page.evaluate("() => document.getElementById('sidepanel').hidden")

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
