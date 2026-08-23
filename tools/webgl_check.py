#!/usr/bin/env python3
"""Verify the WebGL renderer in isolation, on a real (headless) browser.

The main visual suite (tools/visual_check.py) pins the canvas renderer so its
interaction logic is deterministic — headless software WebGL is not a real GPU
and gets flaky under a long run. This check covers the piece that pins out:
that the GPU renderer actually attaches to a pane, that a burst of output
renders through it, and that killing the session still closes the tab and ends
the process with the GPU renderer live (a renderer disposed the wrong way
throws and used to abort the teardown).

It runs its own panel on its own port, home and tmux socket — it cannot touch
the panel you are using — and authenticates with a signed cookie, no password.

    ~/.cache/clique-visual/bin/python tools/webgl_check.py

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

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from clique.auth import COOKIE_NAME, Auth

ROOT = Path(__file__).resolve().parents[1]
PASSWORD = "webgl-check"  # noqa: S105 — a throwaway panel on loopback, alive for seconds
PORT = 3293
BASE = f"http://127.0.0.1:{PORT}"
SANDBOX = Path("/tmp/clique-webgl-home")
SOCKET = "clique-webgl-check"


def _panel() -> subprocess.Popen:
    shutil.rmtree(SANDBOX, ignore_errors=True)
    SANDBOX.mkdir(parents=True)
    env = dict(os.environ, CLIQUE_HOME=str(SANDBOX), CLIQUE_TMUX_SOCKET=SOCKET)
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "clique",
            "--host",
            "127.0.0.1",
            "--port",
            str(PORT),
            "--password",
            PASSWORD,
            "--state",
            str(SANDBOX / "state.json"),
        ],
        cwd=str(ROOT),
        env=env,
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
    checks: dict[str, object] = {}
    try:
        auth = Auth(PASSWORD, SANDBOX / "secret")
        with sync_playwright() as play:
            browser = play.chromium.launch()
            ctx = browser.new_context(viewport={"width": 1400, "height": 900})
            ctx.add_cookies(
                [{"name": COOKIE_NAME, "value": auth.issue(), "domain": "127.0.0.1", "path": "/"}]
            )
            page = ctx.new_page()
            errs: list[str] = []
            page.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
            page.on("pageerror", lambda e: errs.append(str(e)))
            page.on("dialog", lambda d: d.accept())  # killSession confirms natively
            page.goto(BASE, wait_until="networkidle")
            page.wait_for_timeout(1200)

            sid = page.evaluate("""async () => {
              const r = await fetch('/api/sessions', {method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({cli: 'shell', cwd: '/tmp', name: 'webgl probe'})});
              return (await r.json()).id;
            }""")
            if not sid:
                raise SystemExit("could not create a session")
            page.wait_for_timeout(700)
            page.locator(f'.session[data-id="{sid}"]').first.click()
            page.wait_for_timeout(1500)

            # The pane's renderer, by the context its canvas actually holds (the
            # addon class name is minified, so ask the canvas, not the class).
            checks["renderer"] = page.evaluate(
                """(id) => {
              const e = terms.get(id);
              const cs = [...((e && e.el) || document).querySelectorAll('canvas')];
              for (const c of cs) { try { if (c.getContext('webgl2')) return 'webgl2'; } catch (_) {} }
              for (const c of cs) { try { if (c.getContext('2d')) return 'canvas2d'; } catch (_) {} }
              return 'DOM';
            }""",
                sid,
            )
            checks["renderer addon set"] = page.evaluate(
                "(id) => !!(terms.get(id) && terms.get(id).term._cliqueRenderer)", sid
            )

            # A burst renders through it — read the buffer, which is renderer
            # independent, so this proves the writes landed.
            page.evaluate(
                """async (id) => {
              await fetch('/api/sessions/' + id + '/send',
                {method: 'POST', headers: {'Content-Type': 'application/json'},
                 body: JSON.stringify({text: 'for i in $(seq 1 40); do echo webgl-burst-$i; done', enter: true})});
            }""",
                sid,
            )
            page.wait_for_timeout(1600)
            checks["burst rendered"] = page.evaluate(
                """(id) => {
              try {
                const b = terms.get(id).term.buffer.active; let s = '';
                for (let i = 0; i < b.length; i++) { const l = b.getLine(i); if (l) s += l.translateToString(true) + '\\n'; }
                return s.includes('webgl-burst-40');
              } catch (e) { return false; }
            }""",
                sid,
            )

            # Kill via the real path and confirm the tab closes and tmux dies,
            # with the GPU renderer live.
            page.evaluate("async (id) => { await killSession(session(id)); }", sid)
            page.wait_for_timeout(1500)
            checks["tab closed"] = page.evaluate("(id) => !terms.get(id)", sid)
            checks["session ended"] = page.evaluate(
                """async (id) => {
              const j = await (await fetch('/api/state')).json();
              const s = (j.sessions || []).find(x => x.id === id);
              return !s || !s.alive;
            }""",
                sid,
            )
            checks["errors"] = errs[:5]
            browser.close()
    finally:
        proc.terminate()
        subprocess.run(
            ["tmux", "-L", SOCKET, "kill-server"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    ok = (
        checks.get("renderer") == "webgl2"
        and checks.get("renderer addon set") is True
        and checks.get("burst rendered") is True
        and checks.get("tab closed") is True
        and checks.get("session ended") is True
        and not checks.get("errors")
    )
    for key, value in checks.items():
        good = (not value) if key == "errors" else bool(value)
        print(f"  {'ok  ' if good else 'FAIL'} {key}: {value}")
    print("webgl_check:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
