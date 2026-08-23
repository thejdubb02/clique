#!/usr/bin/env python3
"""Verify the review-changes diff and comment-back, in a real (headless) browser.

Feature: for a session in a git repo, "Review changes" shows the agent's
uncommitted diff — tracked edits and new files alike — and a comment sent from
that sheet becomes the agent's next message. This drives all of it:

1. A repo with a tracked edit and a brand-new file, opened as a session.
2. Open the diff and confirm both files render, with added and removed lines.
3. Type a comment, send it, and confirm it reached the session's shell.

Its own home, port, tmux socket and throwaway repo; nothing touches a panel you
are using.

    ~/.cache/clique-visual/bin/python tools/diff_check.py

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

PASSWORD = "diff-check"  # noqa: S105 — a throwaway panel on loopback
PORT = 3284
BASE = f"http://127.0.0.1:{PORT}"
HOME = Path("/tmp/clique-diff-check-home")
SOCKET = "clique-diff-check"
REPO = Path("/tmp/clique-diff-check-repo")


def _git(*args: str) -> None:
    subprocess.run(["git", "-C", str(REPO), *args], capture_output=True)


def _repo() -> None:
    shutil.rmtree(REPO, ignore_errors=True)
    REPO.mkdir(parents=True)
    _git("init", "-q", "-b", "main")
    _git("config", "user.email", "d@d")
    _git("config", "user.name", "d")
    (REPO / "app.py").write_text("def hello():\n    return 1\n")
    _git("add", "-A")
    _git("commit", "-q", "-m", "init")
    (REPO / "app.py").write_text("def hello():\n    return 2  # changed by agent\n")
    (REPO / "newfile.py").write_text("# a brand new file the agent wrote\nX = 42\n")


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

    _repo()
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
            page.on("pageerror", lambda e: errs.append(getattr(e, "stack", "") or str(e)))
            page.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
            page.goto(BASE, wait_until="networkidle")
            page.wait_for_timeout(1000)

            sid = page.evaluate(
                """async (cwd) => {
              const r = await fetch('/api/sessions', {method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({cli: 'shell', cwd, name: 'reviewme'})});
              return (await r.json()).id;
            }""",
                str(REPO),
            )
            page.wait_for_timeout(1000)
            page.evaluate("async () => { await refresh(); }")
            page.wait_for_timeout(400)

            res["has_branch"] = page.evaluate(
                "(id) => { const s = session(id); return s ? !!s.branch : false; }", sid
            )
            page.evaluate("(id) => openDiff(session(id))", sid)
            page.wait_for_timeout(800)
            res["sheet_open"] = page.evaluate("() => !document.getElementById('diff').hidden")
            res["files"] = page.evaluate(
                "() => [...document.querySelectorAll('#diffBody .diff-file-head')].map(e => e.textContent)"
            )
            res["has_add"] = page.evaluate(
                "() => !!document.querySelector('#diffBody .diff-line.add')"
            )
            res["has_del"] = page.evaluate(
                "() => !!document.querySelector('#diffBody .diff-line.del')"
            )
            res["foot_shown"] = page.evaluate("() => !document.getElementById('diffFoot').hidden")
            page.locator("#diffComment").fill("echo diff-comment-reached")
            page.locator("#diffSend").click()
            page.wait_for_timeout(1200)
            res["errors"] = errs[:4]
            browser.close()

        cap = subprocess.run(
            ["tmux", "-L", SOCKET, "capture-pane", "-p", "-t", "sm-" + sid.replace("-", "")[:8]],
            capture_output=True,
            text=True,
        ).stdout
        res["comment_reached"] = "diff-comment-reached" in cap
    finally:
        proc.terminate()
        subprocess.run(
            ["tmux", "-L", SOCKET, "kill-server"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    files = res.get("files") or []
    ok = (
        res.get("has_branch")
        and res.get("sheet_open")
        and any("app.py" in f for f in files)
        and any("newfile.py" in f for f in files)
        and res.get("has_add")
        and res.get("has_del")
        and res.get("foot_shown")
        and res.get("comment_reached")
        and not res.get("errors")
    )
    for key, value in res.items():
        good = (not value) if key == "errors" else bool(value)
        print(f"  {'ok  ' if good else 'FAIL'} {key}: {value}")
    print("diff_check:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
