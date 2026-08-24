#!/usr/bin/env python3
"""Verify in-CLIque file editing, in a real (headless) browser.

Feature: the file viewer gains Edit → Save, which writes the text back to disk
through the same gate as a read — fenced to the session's directory, credentials
refused, symlinks resolved, existing regular files only. This drives the real
thing and, crucially, confirms the security edges:

- an editable text file shows Edit, and a save actually lands on disk;
- a credential file (.env) cannot even be opened to edit, and stays untouched.

    ~/.cache/clique-visual/bin/python tools/fileedit_check.py

Its own home, port, tmux socket and work directory. 0 on pass, 1 on fail.
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

PASSWORD = "fileedit-check"  # noqa: S105 — a throwaway panel on loopback
PORT = 3291
BASE = f"http://127.0.0.1:{PORT}"
HOME = Path("/tmp/clique-fileedit-check-home")
SOCKET = "clique-fileedit-check"
WORK = Path("/tmp/clique-fileedit-check-work")


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
    (WORK / "README.md").write_text("# Original\nhello\n", encoding="utf-8")
    (WORK / ".env").write_text("SECRET=1\n", encoding="utf-8")

    proc = _panel()
    res: dict[str, object] = {}
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
            page.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
            page.goto(BASE, wait_until="networkidle")
            page.wait_for_timeout(800)

            sid = page.evaluate(
                """async (cwd) => {
              const r = await fetch('/api/sessions', {method:'POST',
                headers:{'Content-Type':'application/json'},
                body: JSON.stringify({cli:'shell', cwd, name:'w'})});
              const id = (await r.json()).id; await refresh(); return id;
            }""",
                str(WORK),
            )
            page.wait_for_timeout(600)

            page.evaluate("(id) => openFileSheet(id, 'README.md')", sid)
            page.wait_for_timeout(700)
            res["edit_offered"] = not page.evaluate(
                "() => document.getElementById('fileEditBtn').hidden"
            )
            page.locator("#fileEditBtn").click()
            page.wait_for_timeout(300)
            res["editor_opens"] = not page.evaluate(
                "() => document.getElementById('fileEdit').hidden"
            ) and not page.evaluate("() => document.getElementById('fileSave').hidden")
            page.fill("#fileEdit", "# Edited via CLIque\nnew body\n")
            page.locator("#fileSave").click()
            page.wait_for_timeout(900)
            res["saved_to_disk"] = (
                WORK / "README.md"
            ).read_text() == "# Edited via CLIque\nnew body\n"
            res["back_to_preview"] = page.evaluate(
                "() => document.getElementById('fileEdit').hidden "
                "&& !document.getElementById('fileText').hidden"
            )

            page.evaluate("(id) => openFileSheet(id, '.env')", sid)
            page.wait_for_timeout(600)
            res["credential_not_editable"] = page.evaluate(
                "() => document.getElementById('fileEditBtn').hidden"
            )
            res["credential_untouched"] = (WORK / ".env").read_text() == "SECRET=1\n"

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
    print("fileedit_check:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
