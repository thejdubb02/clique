"""Look at the panel, because nothing else here can.

Every other check in this repo reasons about the code. None of them can see
that a control is invisible, that one thing is covering another, or that a
theme did not reach the terminal — and that is where the bugs have actually
been. A preview popup layered above the context menu broke right-click on every
sidebar row and the whole suite stayed green through it.

So this drives a real browser: it opens the panel, asserts the things only a
rendered page can answer, and writes screenshots to look at afterwards.

**A development tool, not part of the product.** CLIque is standard library and
no build step; this needs Chromium and Playwright and lives in a virtualenv of
its own, so nothing a user installs is affected:

    python3 -m venv ~/.cache/clique-visual
    ~/.cache/clique-visual/bin/pip install playwright
    ~/.cache/clique-visual/bin/playwright install chromium
    ~/.cache/clique-visual/bin/python tools/visual_check.py

It authenticates by minting a session cookie with the server's own signing key
rather than by typing a password, so it needs no secret and cannot be run
against a panel that is not this machine's.

**It runs its own panel, not yours.** A tmux window has one size, shared by
every client attached to it — so a second browser opening the live panel fights
the first for it and the loser gets a screen padded out with dots. Making a
throwaway *session* was not enough: loading the panel restores the workspace,
which attaches every tab that was open, which is every session someone is
working in.

So this starts a second CLIque on another port with its own state directory and
its own tmux socket, checks that, and tears the whole thing down. Nothing it
does can reach a session anybody is using.
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

#: Its own everything. The port is not 3200 and the home is not ~/.clique on
#: purpose: this must not be able to touch the panel someone is working in.
#: Not a secret: this panel exists for a few seconds, on loopback, holding
#: nothing. It has a password only because the server requires one.
PASSWORD = "visual-check"  # noqa: S105 — a throwaway panel on loopback, alive for seconds

PORT = 3299
BASE = f"http://127.0.0.1:{PORT}"
SANDBOX = Path("/tmp/clique-visual-home")
SOCKET = "clique-visual"
SHOTS = Path("/tmp/clique-visual")

passed = failed = 0


def check(label: str, cond: bool, detail: object = "") -> None:
    global passed, failed
    if cond:
        passed += 1
        print(f"  ok   {label}")
    else:
        failed += 1
        print(f"  FAIL {label} {detail}")


def _api(path: str, method: str = "GET", body: dict | None = None) -> dict:
    import json
    import urllib.request
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(BASE + path, data=data, method=method)
    request.add_header("Content-Type", "application/json")
    request.add_header("Authorization", "Bearer " + _token())
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read() or b"{}")


_cached_token = ""


def _token() -> str:
    """A short-lived token of this tool's own, minted on the box."""
    global _cached_token
    if not _cached_token:
        import subprocess
        # Minted against the sandbox's own store, not the live panel's — the
        # token subcommand reads CLIQUE_HOME like everything else.
        made = subprocess.run(
            [sys.executable, "-m", "clique", "token", "create", "visual-check"],
            capture_output=True, text=True,
            env=dict(os.environ, CLIQUE_HOME=str(SANDBOX)),
            cwd=str(Path(__file__).resolve().parents[1]))
        _cached_token = next(
            line.strip() for line in made.stdout.splitlines()
            if line.strip().startswith("mxp_"))
    return _cached_token


def _scratch_session() -> str:
    return _api("/api/sessions", "POST",
                {"cli": "shell", "cwd": "/tmp", "name": "visual check"})["id"]


def _remove_session(session_id: str) -> None:
    import contextlib
    with contextlib.suppress(Exception):
        _api("/api/sessions/" + session_id, "DELETE")


def main() -> int:

    SHOTS.mkdir(parents=True, exist_ok=True)
    panel = _own_panel()
    try:
        return _run(panel)
    finally:
        panel.terminate()
        try:
            panel.wait(timeout=10)
        except subprocess.TimeoutExpired:
            panel.kill()
        shutil.rmtree(SANDBOX, ignore_errors=True)


def _own_panel() -> subprocess.Popen:
    """A second CLIque, with its own state, so nothing here can reach a live one.

    An empty state directory is the load-bearing part: the panel restores its
    workspace on the first poll, and a workspace with no tabs in it cannot
    attach to a session somebody is working in.
    """
    shutil.rmtree(SANDBOX, ignore_errors=True)
    SANDBOX.mkdir(parents=True)
    env = dict(os.environ, CLIQUE_HOME=str(SANDBOX))
    proc = subprocess.Popen(
        [sys.executable, "-m", "clique", "--host", "127.0.0.1",
         "--port", str(PORT), "--password", PASSWORD],
        cwd=str(Path(__file__).resolve().parents[1]), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(80):
        try:
            urllib.request.urlopen(BASE + "/healthz", timeout=2).read()
            return proc
        except (urllib.error.URLError, OSError):
            time.sleep(0.25)
    proc.kill()
    raise SystemExit(f"the check's own panel never came up on {PORT}")


def _run(panel) -> int:
    from playwright.sync_api import sync_playwright

    # A cookie the sandbox panel will accept, signed with its own key.
    auth = Auth(PASSWORD, SANDBOX / "secret")
    mine = _scratch_session()

    with sync_playwright() as play:
        browser = play.chromium.launch()
        context = browser.new_context(viewport={"width": 1400, "height": 900})
        context.add_cookies([{
            "name": COOKIE_NAME, "value": auth.issue(),
            "domain": "127.0.0.1", "path": "/",
        }])
        page = context.new_page()

        problems: list[str] = []
        page.on("console", lambda m: problems.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: problems.append(str(e)))

        page.goto(BASE, wait_until="networkidle")
        page.wait_for_timeout(1500)

        print("the page")
        check("it is the panel, not the login form", page.locator("#tabbar").is_visible())
        check("nothing threw on the way up", not problems, problems[:2])
        check("the sidebar drew", page.locator("#tree").is_visible())

        print("controls are actually visible")
        # Icon-only buttons are the ones that fail silently: an icon that did
        # not load leaves a button that is present, clickable and empty.
        for sel, name in [("#newFolder", "new folder"), ("#settingsBtn", "settings"),
                          ("#collapse", "hide sidebar"), ("#newTab", "new session"),
                          ("#keysBtn", "shortcuts")]:
            box = page.locator(sel).bounding_box()
            check(f"{name} has a size", bool(box) and box["width"] > 6 and box["height"] > 6, box)
            icon = page.locator(f"{sel} .ico")
            if icon.count():
                ibox = icon.bounding_box()
                check(f"{name} drew its icon", bool(ibox) and ibox["width"] > 4, ibox)

        print("nothing is covering anything")
        rows = page.locator(f'.session[data-id="{mine}"]')
        if rows.count():
            row = rows.first
            row.click(button="right")
            page.wait_for_timeout(250)
            menu = page.locator("#menu")
            check("right-click opens our menu", menu.is_visible())
            box = menu.bounding_box()
            if box:
                # The question the layering bug answered wrongly: at a point
                # inside the menu, is the menu what the pointer would hit?
                on_top = page.evaluate(
                    "([x, y]) => { const e = document.elementFromPoint(x, y);"
                    " return !!(e && e.closest('#menu')); }",
                    [box["x"] + box["width"] / 2, box["y"] + 12])
                check("and the menu is what a click would land on", on_top)
            page.keyboard.press("Escape")

        print("the theme reached the terminal")
        sessions = page.locator(f'.session[data-id="{mine}"]')
        if sessions.count():
            sessions.first.click()
            page.wait_for_timeout(2500)
            check("a terminal is on screen", page.locator(".xterm").count() > 0)
            painted = page.evaluate(
                "() => { const s = document.querySelector('.xterm-screen, .xterm');"
                " return s ? getComputedStyle(s).backgroundColor : ''; }")
            print(f"       terminal background: {painted or '(none set)'}")

        page.screenshot(path=str(SHOTS / "panel.png"), full_page=False)
        page.locator("#settingsBtn").click()
        page.wait_for_timeout(600)
        page.screenshot(path=str(SHOTS / "settings.png"))
        check("settings opens", page.locator("#settings").is_visible())

        browser.close()

    print(f"\nscreenshots in {SHOTS}")
    print(f"{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
