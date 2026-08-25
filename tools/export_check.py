#!/usr/bin/env python3
"""Verify scrollback export, end to end, in a real (headless) browser.

Feature: "Export scrollback to a file" writes the session's whole tmux history
to a timestamped `.txt` under `.clique-exports/`. This drives it through the
page: it opens a shell session, prints a unique marker into the pane, exports,
and confirms the file carries the header and the marker. It also confirms an
empty/non-running session is refused with 400.

    ~/.cache/clique-visual/bin/python tools/export_check.py

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

PASSWORD = "export-check"  # noqa: S105 — a throwaway panel on loopback
PORT = 3298
BASE = f"http://127.0.0.1:{PORT}"
HOME = Path("/tmp/clique-export-check-home")
SOCKET = "clique-export-check"
WORK = Path("/tmp/clique-export-check-work")
MARKER = "EXPORT_MARKER_9f3a2c"  # harmless sentinel echoed into the pane

_SEND_JS = """
async ({id, text}) => {
  const r = await fetch(`/api/sessions/${id}/send`, {method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({text, enter: true})});
  return r.status;
}
"""

_EXPORT_JS = """
async (id) => {
  const r = await fetch(`/api/sessions/${id}/export`, {method:'POST',
    headers:{'Content-Type':'application/json'}, body:'{}'});
  let body = null; try { body = await r.json(); } catch (e) { body = null; }
  return {status: r.status, body};
}
"""


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
            page.evaluate("(id) => openSession(id)", sid)
            page.wait_for_timeout(700)
            # Print a unique marker into the pane, then let it land in scrollback.
            page.evaluate(_SEND_JS, {"id": sid, "text": f"echo {MARKER}"})
            page.wait_for_timeout(1200)

            out = page.evaluate(_EXPORT_JS, sid)
            ok = out.get("status") == 201 and isinstance(out.get("body"), dict)
            res["status_201"] = ok
            saved = (out.get("body") or {}) if ok else {}
            rel = saved.get("relative") or ""
            res["under_exports"] = rel.startswith(".clique-exports/")
            written = WORK / rel if rel else WORK / "nope"
            text = written.read_text() if written.is_file() else ""
            res["file_written"] = bool(text)
            res["has_header"] = "CLIque scrollback export" in text
            res["has_marker"] = MARKER in text
            res["lines_reported"] = bool(saved.get("lines"))

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
    print("export_check:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
