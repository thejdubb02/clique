#!/usr/bin/env python3
"""Verify drag-and-drop file upload, in a real (headless) browser.

Feature: drop a file — an image, a document, anything named — onto the window
and it lands in the open session's `.clique-drops` folder, its path dropped into
the prompt, exactly like a pasted screenshot. This drives the real drop path (a
synthetic DragEvent carrying a real File, through the document handler and
`dropFiles`) and confirms the security edges the write shares with a read:

- an ordinary file drops onto disk and its path is reported;
- the drop veil shows on dragover and clears after the drop;
- a credential name (`.env`) is refused and never written;
- a traversing name (`../escape.txt`) is reduced to a basename and stays inside.

    ~/.cache/clique-visual/bin/python tools/filedrop_check.py

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

PASSWORD = "filedrop-check"  # noqa: S105 — a throwaway panel on loopback
PORT = 3292
BASE = f"http://127.0.0.1:{PORT}"
HOME = Path("/tmp/clique-filedrop-check-home")
SOCKET = "clique-filedrop-check"
WORK = Path("/tmp/clique-filedrop-check-work")

# Dispatch a drag over the window and then a drop carrying one named file, the
# way a browser does it: a DataTransfer holding a real File, so dataTransfer.types
# is ["Files"] and our isFileDrag guard fires. Returns whether the veil showed.
_DROP_JS = """
async ({name, text}) => {
  const dt = new DataTransfer();
  dt.items.add(new File([text], name, {type: 'application/octet-stream'}));
  const mk = (type) => new DragEvent(type, {bubbles: true, cancelable: true, dataTransfer: dt});
  document.dispatchEvent(mk('dragenter'));
  document.dispatchEvent(mk('dragover'));
  const veilShown = !document.getElementById('dropveil').hidden;
  document.dispatchEvent(mk('drop'));
  return veilShown;
}
"""


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
    drops = WORK / ".clique-drops"
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
            # A JS exception is a failure; a network 4xx logged to the console is
            # not — this check deliberately drives refusals (a credential name is
            # meant to come back 400), and those surface as "Failed to load
            # resource". Keep real script errors, drop the expected refusals.
            page.on(
                "console",
                lambda m: (
                    errs.append(m.text)
                    if m.type == "error" and "Failed to load resource" not in m.text
                    else None
                ),
            )
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
            page.wait_for_timeout(500)
            page.evaluate("(id) => openSession(id)", sid)
            page.wait_for_timeout(600)

            # An ordinary document: veil shows, file lands, path is reported.
            res["veil_on_dragover"] = page.evaluate(
                _DROP_JS, {"name": "notes.txt", "text": "dropped body\n"}
            )
            page.wait_for_timeout(900)
            landed = drops / "notes.txt"
            res["doc_landed"] = landed.is_file() and landed.read_text() == "dropped body\n"
            res["path_reported"] = "notes.txt" in (
                page.evaluate("() => document.getElementById('toast').textContent") or ""
            )
            res["veil_cleared"] = page.evaluate("() => document.getElementById('dropveil').hidden")

            # A credential name is refused before it is written.
            page.evaluate(_DROP_JS, {"name": ".env", "text": "SECRET=1\n"})
            page.wait_for_timeout(700)
            res["credential_refused"] = not (drops / ".env").exists()

            # A traversing name is reduced to a basename that stays inside.
            page.evaluate(_DROP_JS, {"name": "../escape.txt", "text": "nope\n"})
            page.wait_for_timeout(700)
            res["traversal_contained"] = (drops / "escape.txt").is_file() and not (
                WORK.parent / "escape.txt"
            ).exists()

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
    print("filedrop_check:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
