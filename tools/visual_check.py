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

**It never touches a session it did not create.** A tmux window has one size,
so a second browser attaching to a session someone is working in fights them
for it and the loser gets a screen padded out with dots. The first version of
this opened whatever was in the sidebar and did exactly that to a live pane.
It makes its own throwaway session now, and deletes it on the way out.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from clique.__main__ import HOME, read_password
from clique.auth import COOKIE_NAME, Auth

BASE = "http://127.0.0.1:3200"
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
        made = subprocess.run(
            [sys.executable, "-m", "clique", "token", "create", "visual-check"],
            capture_output=True, text=True,
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
    from playwright.sync_api import sync_playwright

    SHOTS.mkdir(parents=True, exist_ok=True)
    # The server's own signing key, so a cookie minted here is one it accepts.
    # No password is typed and none is needed: this can only ever work against
    # the panel running on this machine, from this machine.
    auth = Auth(read_password(None), HOME / "secret")

    # Its own session to look at, never anyone else's. Created before the page
    # loads so it is in the first poll.
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

    _remove_session(mine)
    print(f"\nscreenshots in {SHOTS}")
    print(f"{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
