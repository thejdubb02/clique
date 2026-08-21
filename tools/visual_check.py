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

from clique import tmux
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
            env=dict(os.environ, CLIQUE_HOME=str(SANDBOX),
                     CLIQUE_TMUX_SOCKET=SOCKET),
            cwd=str(Path(__file__).resolve().parents[1]))
        _cached_token = next(
            line.strip() for line in made.stdout.splitlines()
            if line.strip().startswith("mxp_"))
    return _cached_token


def _scratch_session() -> str:
    repo = SANDBOX / "work"
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "symbolic-ref", "HEAD",
                    "refs/heads/visual"], check=True, capture_output=True)
    (repo / "note.md").write_text("hello from a click\n", encoding="utf-8")
    return _api("/api/sessions", "POST",
                {"cli": "shell", "cwd": str(repo), "name": "visual check"})["id"]


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
        tmux._run(["kill-server"], SOCKET, check=False)
        shutil.rmtree(SANDBOX, ignore_errors=True)


def _sandbox_catalogue() -> None:
    """The real catalogue plus a boxed stand-in, so copy/click can be seen.

    Claude, Grok and Gemini all turn mouse tracking on and draw their own
    prompt. This is that shape without opening a paid CLI in the check.
    """
    root = Path(__file__).resolve().parents[1]
    packaged = root / "clique" / "config" / "clis.toml"
    fake = root / "tools" / "fake_boxed_cli.py"
    body = packaged.read_text(encoding="utf-8")
    body += (
        "\n[cli.boxed]\n"
        'label      = "Boxed"\n'
        f'command    = {sys.executable!r}\n'
        f'args       = [{str(fake)!r}]\n'
        'color      = "#6b7280"\n'
        "own_input  = true\n"
    )
    (SANDBOX / "clis.toml").write_text(body, encoding="utf-8")


def _own_panel() -> subprocess.Popen:
    """A second CLIque, with its own state and tmux socket.

    Own home, own ``--state``, own ``CLIQUE_TMUX_SOCKET``. Missing any one
    of those is how a check used to attach to the pane someone was using.
    """
    shutil.rmtree(SANDBOX, ignore_errors=True)
    SANDBOX.mkdir(parents=True)
    _sandbox_catalogue()
    env = dict(os.environ, CLIQUE_HOME=str(SANDBOX),
               CLIQUE_TMUX_SOCKET=SOCKET)
    proc = subprocess.Popen(
        [sys.executable, "-m", "clique", "--host", "127.0.0.1",
         "--port", str(PORT), "--password", PASSWORD,
         "--state", str(SANDBOX / "state.json")],
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
        context = browser.new_context(
            viewport={"width": 1400, "height": 900},
            permissions=["clipboard-read", "clipboard-write"])
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
        mark = page.locator(".sidebar-mark")
        check("the sidebar mark is in the panel", mark.count() == 1)
        mark_box = mark.bounding_box()
        check("and it is large",
              bool(mark_box) and mark_box["width"] >= 100 and mark_box["height"] >= 100,
              mark_box)
        if mark_box:
            hit = page.evaluate(
                """([x, y]) => {
                  const e = document.elementFromPoint(x, y);
                  return !!(e && e.closest('.sidebar-mark'));
                }""",
                [mark_box["x"] + mark_box["width"] / 2,
                 mark_box["y"] + mark_box["height"] / 2])
            check("the mark does not steal a click", hit is False, hit)

        print("git on the row")
        row = page.locator(f'.session[data-id="{mine}"]')
        try:
            page.wait_for_function(
                """(id) => {
                  const el = document.querySelector('.session[data-id="' + id + '"]');
                  return !!(el && /visual/.test(el.innerText) && /changed/.test(el.innerText));
                }""",
                arg=mine, timeout=8000)
        except Exception as err:  # noqa: BLE001 — the checks below name what failed
            print(f"       git row did not settle: {err}")
        shown = row.inner_text() if row.count() else ""
        check("the row names the branch", "visual" in shown, shown)
        check("and it says the folder is dirty", "changed" in shown, shown)
        page.locator("#sidebar").screenshot(path=str(SHOTS / "sidebar.png"))

        print("controls are actually visible")
        # Icon-only buttons are the ones that fail silently: an icon that did
        # not load leaves a button that is present, clickable and empty.
        for sel, name in [("#newFolder", "new folder"), ("#settingsBtn", "settings"),
                          ("#collapse", "hide sidebar"), ("#newTab", "new session"),
                          ("#lock", "follow output"), ("#keysBtn", "shortcuts"),
                          ("#fullScr", "full screen"),
                          ("#fontMinus", "smaller type"), ("#fontPlus", "larger type")]:
            box = page.locator(sel).bounding_box()
            check(f"{name} has a size", bool(box) and box["width"] > 6 and box["height"] > 6, box)
            icon = page.locator(f"{sel} .ico").first
            if icon.count():
                ibox = icon.bounding_box()
                check(f"{name} drew its icon", bool(ibox) and ibox["width"] > 4, ibox)
        pause = page.locator("#lock .ico-pause")
        if pause.count():
            check("pause glyph stays hidden while following",
                  pause.bounding_box() is None)

        keys_box = page.locator("#keysBtn").bounding_box()
        tab_box = page.locator("#tabbar").bounding_box()
        if keys_box and tab_box:
            check("shortcuts sit in the bottom bar, not the tab strip",
                  keys_box["y"] > tab_box["y"] + tab_box["height"] - 1,
                  keys_box)
        stats_box = page.locator("#stats").bounding_box()
        if stats_box and tab_box:
            check("stats sit in the bottom bar, not the tab strip",
                  stats_box["y"] > tab_box["y"] + tab_box["height"] - 1,
                  stats_box)
        font_box = page.locator("#fontSize").bounding_box()
        if font_box and keys_box:
            check("font size sits in the bottom-right, past the shortcuts",
                  font_box["x"] > keys_box["x"] + keys_box["width"] - 1,
                  font_box)
        size_label = page.locator("#fontSizeVal").inner_text()
        check("the stepper shows a size",
              size_label.strip().isdigit() and 9 <= int(size_label.strip()) <= 28,
              size_label)

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

        print("stats hold still")
        page.wait_for_timeout(200)
        before = page.evaluate(
            "() => document.querySelector('#stats').getBoundingClientRect().width")
        page.evaluate(
            """() => {
              const mem = document.querySelector('#mem .v');
              const cpu = document.querySelector('#cpu .v');
              if (mem) mem.textContent = '0.1/8G';
              if (cpu) cpu.textContent = '100.0%';
            }""")
        after = page.evaluate(
            "() => document.querySelector('#stats').getBoundingClientRect().width")
        check("changing a reading does not resize the stats",
              abs(before - after) < 1, {"before": before, "after": after})

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

        print("copy from the pane")
        # A shell has no mouse tracking, so a drag is a selection. The chip
        # and the clipboard are how we know it actually landed.
        term = page.locator("#terminal .xterm").first
        box = term.bounding_box() if term.count() else None
        check("the pane has a size to drag across",
              bool(box) and box["width"] > 80, box)
        if box:
            page.mouse.click(box["x"] + 24, box["y"] + 24)
            page.wait_for_timeout(150)
            page.mouse.move(box["x"] + 12, box["y"] + 10)
            page.mouse.down()
            page.mouse.move(box["x"] + min(box["width"] - 12, 420),
                            box["y"] + min(box["height"] - 12, 36),
                            steps=10)
            page.mouse.up()
            page.wait_for_timeout(400)
            check("a drag shows the copy button",
                  page.locator("#copySel").is_visible())
            try:
                taken = page.evaluate("() => navigator.clipboard.readText()")
            except Exception as err:  # noqa: BLE001 — the check names the failure
                taken = str(err)
            check("and the selection is on the clipboard",
                  isinstance(taken, str) and len(taken.strip()) > 0,
                  (taken[:80] if isinstance(taken, str) else taken))
            # Same pane, with the CLI listening for clicks — the case that
            # used to swallow the drag so there was nothing to copy.
            _api("/api/sessions/" + mine + "/send", "POST",
                 {"text": "printf '\\033[?1000h\\033[?1002h\\033[?1006h'", "enter": True})
            page.wait_for_timeout(500)
            tracking = page.evaluate(
                "() => !!document.querySelector('.xterm.enable-mouse-events')")
            check("the pane is now eating mouse events", tracking)
            page.mouse.click(box["x"] + 8, box["y"] + box["height"] / 2)
            page.wait_for_timeout(150)
            page.mouse.move(box["x"] + 12, box["y"] + 10)
            page.mouse.down()
            page.mouse.move(box["x"] + min(box["width"] - 12, 420),
                            box["y"] + min(box["height"] - 12, 36),
                            steps=10)
            page.mouse.up()
            page.wait_for_timeout(400)
            check("a drag still selects while the CLI is eating clicks",
                  page.locator("#copySel").is_visible())

        print("copy from a boxed CLI")
        # The CLIs people actually copy from turn mouse tracking on. The
        # panel hides that from the browser, a drag is a native selection,
        # and a click is still delivered as SGR.
        boxed = _api("/api/sessions", "POST",
                     {"cli": "boxed", "cwd": "/tmp", "name": "boxed copy"})["id"]
        page.wait_for_timeout(400)
        page.evaluate("(id) => openSession(id)", boxed)
        try:
            page.wait_for_function(
                """() => {
                  const e = terms.get(activeId);
                  if (!e || !e.term) return false;
                  const line = e.term.buffer.active.getLine(e.term.buffer.active.viewportY);
                  const text = line ? line.translateToString(true) : '';
                  return text.includes('BOXED-COPY-LINE');
                }""",
                timeout=8000)
        except Exception as err:  # noqa: BLE001 — the checks below name what failed
            print(f"       boxed pane did not draw: {err}")
        boxed_host = page.locator("#terminal .xterm").first
        boxed_box = boxed_host.bounding_box() if boxed_host.count() else None
        tracking = page.evaluate(
            """() => {
              const e = terms.get(activeId);
              return !!(e && e.term && e.term.element &&
                e.term.element.classList.contains('enable-mouse-events'));
            }""")
        check("a boxed CLI does not put the browser in mouse mode", not tracking)
        pane_text = page.evaluate(
            """() => {
              const e = terms.get(activeId);
              if (!e || !e.term) return '';
              const buf = e.term.buffer.active;
              const lines = [];
              for (let i = 0; i < e.term.rows; i++) {
                const line = buf.getLine(buf.viewportY + i);
                lines.push(line ? line.translateToString(true) : '');
              }
              return lines.join('\\n');
            }""")
        check("the boxed stand-in actually drew",
              "BOXED-COPY-LINE" in pane_text, pane_text[:120])
        if boxed_box:
            page.mouse.move(boxed_box["x"] + 12, boxed_box["y"] + 10)
            page.mouse.down()
            page.mouse.move(boxed_box["x"] + min(boxed_box["width"] - 12, 360),
                            boxed_box["y"] + 18, steps=8)
            page.mouse.up()
            page.wait_for_timeout(400)
            check("a drag on a boxed pane shows the copy button",
                  page.locator("#copySel").is_visible())
            try:
                taken = page.evaluate("() => navigator.clipboard.readText()")
            except Exception as err:  # noqa: BLE001 — the check names the failure
                taken = str(err)
            check("and copied the boxed line",
                  isinstance(taken, str) and "COPY-LINE" in taken.upper(),
                  (taken[:80] if isinstance(taken, str) else taken))
            page.screenshot(path=str(SHOTS / "boxed-copy.png"))
            page.evaluate(
                "() => { const e = terms.get(activeId); "
                "if (e && e.term) e.term.clearSelection(); }")
            page.wait_for_timeout(150)
            page.mouse.click(boxed_box["x"] + 40, boxed_box["y"] + 12)
            try:
                page.wait_for_function(
                    """() => {
                      const e = terms.get(activeId);
                      if (!e || !e.term) return false;
                      const buf = e.term.buffer.active;
                      for (let i = 0; i < Math.min(8, e.term.rows); i++) {
                        const line = buf.getLine(buf.viewportY + i);
                        const text = line ? line.translateToString(true) : '';
                        if (/clicked/i.test(text)) return true;
                      }
                      return false;
                    }""",
                    timeout=2500)
                clicked_ok = True
                after_click = "clicked"
            except Exception as err:  # noqa: BLE001 — the check names what failed
                clicked_ok = False
                after_click = str(err)
            check("a click still reaches the boxed CLI", clicked_ok, after_click)
            print("zooming a boxed pane instead of wrapping it")
            before_cols = page.evaluate(
                "() => { const e = terms.get(activeId); return e && e.term ? e.term.cols : 0; }")
            page.set_viewport_size({"width": 1200, "height": 720})
            page.wait_for_timeout(700)
            zoomed = page.evaluate(
                """() => {
                  const e = terms.get(activeId);
                  if (!e || !e.term || !e.term.element) return {};
                  const buf = e.term.buffer.active;
                  const line = buf.getLine(buf.viewportY);
                  return {
                    cols: e.term.cols,
                    transform: e.term.element.style.transform || "",
                    text: line ? line.translateToString(true) : ""
                  };
                }""")
            check("the grid did not get narrower",
                  isinstance(zoomed, dict) and zoomed.get("cols") == before_cols,
                  (zoomed, before_cols))
            check("the pane zoomed to fit instead",
                  isinstance(zoomed, dict) and "scale(" in (zoomed.get("transform") or ""),
                  zoomed)
            check("and the boxed line did not wrap",
                  isinstance(zoomed, dict) and "BOXED-COPY-LINE" in (zoomed.get("text") or ""),
                  zoomed)
            page.screenshot(path=str(SHOTS / "boxed-zoom.png"))
            page.set_viewport_size({"width": 1400, "height": 900})
            page.wait_for_timeout(400)
        _remove_session(boxed)
        page.evaluate("(id) => openSession(id)", mine)
        page.wait_for_timeout(400)

        print("tabs that do not fit")
        names = [
            "Duchamp Events Dev", "CLIque Code Review", "Whatbox IPTV Dev",
            "WSG Platform Gen", "Sentinel Dev", "Duchamp Room Rates",
            "Meridian Nightly", "Daily Deck Writer", "Prowler Scan",
            "Inbox Agent", "Dealophant Shop",
        ]
        extra: list[str] = []
        try:
            for name in names:
                extra.append(_api("/api/sessions", "POST",
                                  {"cli": "shell", "cwd": "/tmp", "name": name})["id"])
            page.wait_for_timeout(3500)
            page.evaluate(
                """(ids) => { openTabs = ids; activeId = ids[0]; renderTabs(); }""",
                extra)
            page.wait_for_timeout(250)
            page.locator("#tabbar").screenshot(path=str(SHOTS / "tab-overflow.png"))
            clipped = page.evaluate(
                """() => {
                  const bar = document.querySelector("#tabbar");
                  const edge = bar.getBoundingClientRect().right;
                  return [...document.querySelectorAll("#tabs .tab")]
                    .filter((t) => !t.hidden)
                    .filter((t) => t.getBoundingClientRect().right > edge + 1)
                    .map((t) => t.textContent.trim());
                }""")
            check("no visible tab is clipped by the bar", clipped == [], clipped)
            more = page.locator("#tabOverflow")
            check("the overflow control is on screen", more.is_visible())
            more_box = more.bounding_box()
            bar_box = page.locator("#tabbar").bounding_box()
            if more_box and bar_box:
                check(
                    "and it sits inside the bar",
                    more_box["x"] + more_box["width"] <= bar_box["x"] + bar_box["width"] + 1,
                    more_box,
                )
            more.click()
            page.wait_for_timeout(200)
            check("clicking it lists the rest", page.locator("#menu .tab-more-item").count() > 0)
            page.keyboard.press("Escape")
            contrast = page.evaluate(
                """() => {
                  const active = document.querySelector(".tab.active");
                  const other = document.querySelector(".tab:not(.active)");
                  if (!active || !other) return { ok: false, why: "need two tabs" };
                  const a = getComputedStyle(active);
                  const b = getComputedStyle(other);
                  const al = getComputedStyle(active.querySelector(".label"));
                  const bl = getComputedStyle(other.querySelector(".label"));
                  return {
                    ok: true,
                    activeBg: a.backgroundColor,
                    otherBg: b.backgroundColor,
                    activeWeight: Number(al.fontWeight),
                    otherWeight: Number(bl.fontWeight),
                    activeColor: a.color,
                    otherColor: b.color,
                  };
                }""")
            check("active tab is heavier than its neighbours",
                  contrast.get("ok") and contrast["activeWeight"] >= 600
                  and contrast["activeWeight"] > contrast["otherWeight"],
                  contrast)
            check("active tab is not the same colour as an idle one",
                  contrast.get("ok") and contrast["activeColor"] != contrast["otherColor"],
                  contrast)
        finally:
            for sid in extra:
                _remove_session(sid)

        print("a path you can look at")
        sample = Path("/tmp/clique-visual-file.md")
        sample.write_text("# Hello from a click\n\nNot an editor.\n", encoding="utf-8")
        page.evaluate(
            "(id) => openFileSheet(id, '/tmp/clique-visual-file.md')", mine)
        page.wait_for_timeout(500)
        check("the file sheet opens", page.locator("#file").is_visible())
        shown = page.locator("#fileText").inner_text()
        check("and it shows the text", "Hello from a click" in shown, shown[:80])
        page.locator("#file").screenshot(path=str(SHOTS / "file-sheet.png"))
        page.keyboard.press("Escape")
        page.wait_for_timeout(200)
        check("escape closes it", page.locator("#file").is_hidden())
        sample.unlink(missing_ok=True)

        page.screenshot(path=str(SHOTS / "panel.png"), full_page=False)

        print("what's new")
        check("the mark is off until there is something to read",
              page.locator("#whatsNew").is_hidden())
        page.evaluate(
            "() => { state.settings.changelog_seen = '0.1.0'; renderVersion(); }")
        page.wait_for_timeout(150)
        check("an unread release lights the bottom bar",
              page.locator("#whatsNew").is_visible())
        page.locator("#whatsNew").click()
        page.wait_for_timeout(800)
        check("clicking it opens the notes",
              page.locator("#settings").is_visible()
              and page.locator('.pane[data-pane="changelog"]:not([hidden])').count() > 0)
        check("and the mark goes out once they have been opened",
              page.locator("#whatsNew").is_hidden())
        articles = page.locator("#changelog .clog-entry")
        try:
            page.wait_for_selector("#changelog .clog-entry", timeout=4000)
        except Exception as err:  # noqa: BLE001 — the checks below name what failed
            print(f"       changelog did not draw: {err}")
        check("the sheet holds the last few releases",
              articles.count() == 5, articles.count())
        more = page.locator("a.clog-more")
        href = more.get_attribute("href") if more.count() else ""
        check("and the rest is a link to the file on GitHub",
              more.is_visible() and "CHANGELOG.md" in (href or ""), href)
        page.screenshot(path=str(SHOTS / "changelog.png"))
        page.screenshot(path=str(SHOTS / "settings.png"))
        check("settings opens", page.locator("#settings").is_visible())

        browser.close()

    print(f"\nscreenshots in {SHOTS}")
    print(f"{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
