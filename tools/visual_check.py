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
            capture_output=True,
            text=True,
            env=dict(os.environ, CLIQUE_HOME=str(SANDBOX), CLIQUE_TMUX_SOCKET=SOCKET),
            cwd=str(Path(__file__).resolve().parents[1]),
        )
        _cached_token = next(
            line.strip() for line in made.stdout.splitlines() if line.strip().startswith("mxp_")
        )
    return _cached_token


def _scratch_session() -> str:
    repo = SANDBOX / "work"
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "symbolic-ref", "HEAD", "refs/heads/visual"],
        check=True,
        capture_output=True,
    )
    (repo / "note.md").write_text("hello from a click\n", encoding="utf-8")
    return _api(
        "/api/sessions", "POST", {"cli": "shell", "cwd": str(repo), "name": "visual check"}
    )["id"]


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
        f"command    = {sys.executable!r}\n"
        f"args       = [{str(fake)!r}]\n"
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
        cwd=str(Path(__file__).resolve().parents[1]),
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


def _run(panel) -> int:
    from playwright.sync_api import sync_playwright

    # A cookie the sandbox panel will accept, signed with its own key.
    auth = Auth(PASSWORD, SANDBOX / "secret")
    mine = _scratch_session()

    with sync_playwright() as play:
        browser = play.chromium.launch()
        context = browser.new_context(
            viewport={"width": 1400, "height": 900},
            permissions=["clipboard-read", "clipboard-write"],
        )
        # Pin the canvas renderer for the suite. What it exercises is CLIque's
        # click/copy/scroll *logic*, which is renderer-independent by design (it
        # maps from getBoundingClientRect, not the GPU). Headless Chromium's
        # software WebGL (SwiftShader) is not a real GPU: readPixels stalls and
        # context churn make interaction flaky under a full run's load. The
        # WebGL path itself is verified in isolation by tools/webgl_check.py.
        context.add_init_script("try { localStorage.setItem('clique.gpu', '0'); } catch (e) {}")
        context.add_cookies(
            [
                {
                    "name": COOKIE_NAME,
                    "value": auth.issue(),
                    "domain": "127.0.0.1",
                    "path": "/",
                }
            ]
        )
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
        check(
            "and it is large",
            bool(mark_box) and mark_box["width"] >= 100 and mark_box["height"] >= 100,
            mark_box,
        )
        if mark_box:
            hit = page.evaluate(
                """([x, y]) => {
                  const e = document.elementFromPoint(x, y);
                  return !!(e && e.closest('.sidebar-mark'));
                }""",
                [mark_box["x"] + mark_box["width"] / 2, mark_box["y"] + mark_box["height"] / 2],
            )
            check("the mark does not steal a click", hit is False, hit)

        print("git on the row")
        row = page.locator(f'.session[data-id="{mine}"]')
        try:
            page.wait_for_function(
                """(id) => {
                  const el = document.querySelector('.session[data-id="' + id + '"]');
                  return !!(el && /visual/.test(el.innerText) && /changed/.test(el.innerText));
                }""",
                arg=mine,
                timeout=8000,
            )
        except Exception as err:  # noqa: BLE001 — the checks below name what failed
            print(f"       git row did not settle: {err}")
        shown = row.inner_text() if row.count() else ""
        check("the row names the branch", "visual" in shown, shown)
        check("and it says the folder is dirty", "changed" in shown, shown)
        page.locator("#sidebar").screenshot(path=str(SHOTS / "sidebar.png"))

        print("controls are actually visible")
        # Icon-only buttons are the ones that fail silently: an icon that did
        # not load leaves a button that is present, clickable and empty.
        for sel, name in [
            ("#moreBtn", "more menu"),
            ("#settingsBtn", "settings"),
            ("#collapse", "hide sidebar"),
            ("#newTab", "new session"),
            ("#lock", "follow output"),
            ("#keysBtn", "shortcuts"),
            ("#fullScr", "full screen"),
            ("#fontMinus", "smaller type"),
            ("#fontPlus", "larger type"),
        ]:
            box = page.locator(sel).bounding_box()
            check(f"{name} has a size", bool(box) and box["width"] > 6 and box["height"] > 6, box)
            icon = page.locator(f"{sel} .ico").first
            if icon.count():
                ibox = icon.bounding_box()
                check(f"{name} drew its icon", bool(ibox) and ibox["width"] > 4, ibox)
        pause = page.locator("#lock .ico-pause")
        if pause.count():
            check("pause glyph stays hidden while following", pause.bounding_box() is None)

        keys_box = page.locator("#keysBtn").bounding_box()
        tab_box = page.locator("#tabbar").bounding_box()
        if keys_box and tab_box:
            check(
                "shortcuts sit in the bottom bar, not the tab strip",
                keys_box["y"] > tab_box["y"] + tab_box["height"] - 1,
                keys_box,
            )
        stats_box = page.locator("#stats").bounding_box()
        if stats_box and tab_box:
            check(
                "stats sit in the bottom bar, not the tab strip",
                stats_box["y"] > tab_box["y"] + tab_box["height"] - 1,
                stats_box,
            )
        font_box = page.locator("#fontSize").bounding_box()
        if font_box and keys_box:
            check(
                "font size sits in the bottom-right, past the shortcuts",
                font_box["x"] > keys_box["x"] + keys_box["width"] - 1,
                font_box,
            )
        size_label = page.locator("#fontSizeVal").inner_text()
        check(
            "the stepper shows a size",
            size_label.strip().isdigit() and 9 <= int(size_label.strip()) <= 28,
            size_label,
        )

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
                    [box["x"] + box["width"] / 2, box["y"] + 12],
                )
                check("and the menu is what a click would land on", on_top)
            page.keyboard.press("Escape")

        print("stats hold still")
        page.wait_for_timeout(200)
        before = page.evaluate(
            "() => document.querySelector('#stats').getBoundingClientRect().width"
        )
        page.evaluate(
            """() => {
              const mem = document.querySelector('#mem .v');
              const cpu = document.querySelector('#cpu .v');
              if (mem) mem.textContent = '0.1/8G';
              if (cpu) cpu.textContent = '100.0%';
            }"""
        )
        after = page.evaluate(
            "() => document.querySelector('#stats').getBoundingClientRect().width"
        )
        check(
            "changing a reading does not resize the stats",
            abs(before - after) < 1,
            {"before": before, "after": after},
        )

        # A stat the machine cannot report is gone, not blank. Most VMs have no
        # temperature sensor and no swap in use, and holding those columns open
        # left two permanent holes in the row.
        gaps = page.evaluate(
            """() => {
              const q = (s) => document.querySelector(s);
              const swap = q('#swap'), temp = q('#temp');
              swap.classList.add('is-off');
              temp.classList.add('is-off');
              const load = q('#load').getBoundingClientRect();
              const up = q('#uptime').getBoundingClientRect();
              const cpu = q('#cpu').getBoundingClientRect();
              const mem = q('#mem').getBoundingClientRect();
              return {
                off: temp.getBoundingClientRect().width + swap.getBoundingClientRect().width,
                between: up.left - load.right,
                normal: mem.left - cpu.right,
              };
            }"""
        )
        check("a stat with no reading takes no width", gaps["off"] < 1, gaps)

        # Nothing in the bar may be cut off mid-word. Readings drop out whole
        # as the row narrows; the plan meters cost it real estate and the first
        # attempt at them left VIEWS reading "VIEW". Asked at three widths
        # because the row is the window minus the sidebar, so collapsing the
        # sidebar has to change the answer.
        OVERHANG = """() => {
          const bar = document.querySelector('#statusbar').getBoundingClientRect();
          const bad = [];
          for (const el of document.querySelectorAll('#stats > .stat')) {
            if (!el.offsetParent) continue;
            const r = el.getBoundingClientRect();
            if (r.right > bar.right + 1 || r.left < bar.left - 1) bad.push(el.id || '?');
          }
          return bad;
        }"""
        for width, label in ((1280, "at a normal window"), (900, "at a narrow one")):
            page.set_viewport_size({"width": width, "height": 860})
            page.wait_for_timeout(350)
            over = page.evaluate(OVERHANG)
            check(f"no reading hangs off the status bar {label}", over == [], over)
        page.evaluate("() => setSidebar(false)")
        page.wait_for_timeout(400)
        check("nor with the sidebar collapsed", page.evaluate(OVERHANG) == [],
              page.evaluate(OVERHANG))
        page.evaluate("() => setSidebar(true)")
        page.set_viewport_size({"width": 1280, "height": 860})
        page.wait_for_timeout(350)
        # No CLI in this check declares a usage probe, so the block stays away
        # rather than sitting there empty.
        check("the plan block is absent for a CLI that cannot report it",
              page.evaluate("() => document.querySelector('#plan').hidden") is True)
        check(
            "and leaves no gap where it was",
            abs(gaps["between"] - gaps["normal"]) < 1,
            gaps,
        )
        shown = page.evaluate(
            """() => {
              const temp = document.querySelector('#temp');
              temp.classList.remove('is-off');
              return temp.getBoundingClientRect().width;
            }"""
        )
        check("a stat that comes back is drawn again", shown > 10, shown)

        print("the theme reached the terminal")
        sessions = page.locator(f'.session[data-id="{mine}"]')
        if sessions.count():
            sessions.first.click()
            page.wait_for_timeout(2500)
            check("a terminal is on screen", page.locator(".xterm").count() > 0)
            painted = page.evaluate(
                "() => { const s = document.querySelector('.xterm-screen, .xterm');"
                " return s ? getComputedStyle(s).backgroundColor : ''; }"
            )
            print(f"       terminal background: {painted or '(none set)'}")

            # The pane's scrollbar was the last raw browser control in the
            # panel. It has to follow the theme like everything else, and it
            # has to keep following when the theme changes.
            bar = page.evaluate(
                """() => {
                  const vp = document.querySelector('.xterm-viewport');
                  if (!vp) return null;
                  const cs = getComputedStyle(vp);
                  return {width: cs.scrollbarWidth, color: cs.scrollbarColor};
                }"""
            )
            check("the pane's scrollbar is thin, not the browser default",
                  bool(bar) and bar["width"] == "thin", bar)
            check("and it is coloured, not auto",
                  bool(bar) and bar["color"] not in ("", "auto"), bar)
            before = bar["color"] if bar else ""
            page.evaluate(
                """async () => {
                  await fetch('/api/settings', {method:'PATCH',
                    headers:{'Content-Type':'application/json'},
                    body: JSON.stringify({theme: 'dracula'})});
                  await refresh();
                }"""
            )
            page.wait_for_timeout(600)
            after = page.evaluate(
                "() => { const v = document.querySelector('.xterm-viewport');"
                " return v ? getComputedStyle(v).scrollbarColor : ''; }"
            )
            check("and it changes with the theme", after and after != before,
                  {"before": before, "after": after})
            page.screenshot(path=str(SHOTS / "terminal-dracula.png"))
            page.evaluate(
                """async () => {
                  await fetch('/api/settings', {method:'PATCH',
                    headers:{'Content-Type':'application/json'},
                    body: JSON.stringify({theme: ''})});
                  await refresh();
                }"""
            )
            page.wait_for_timeout(400)

            # The character a theme carries. Nothing else in this suite can see
            # it: it is a background image on an element with no text, sized as
            # a percentage of a pane, blended into the terminal. Every part of
            # that is invisible to a test that reasons about the code.
            print("working groups, and the band that shows them")
            # Put the strip back afterwards. This test opens tabs and makes one
            # of them active, and everything below drags across whichever pane
            # is in front: leaving a fresh empty shell there broke three copy
            # assertions that had nothing to do with groups.
            was_active = page.evaluate("() => activeId")
            # The band is the whole visual and it only reads as a band if the
            # tabs it runs under are adjacent. Scattered through the strip the
            # same colour is three unrelated pills, which is what the first
            # version of this drew.
            # Two sessions of its own: the sandbox has one, and a group of one
            # cannot show that a band runs across a run of tabs.
            made = page.evaluate(
                """async () => {
                  const post = (u, b) => fetch(u, {method:'POST',
                    headers:{'Content-Type':'application/json'},
                    body: JSON.stringify(b || {})}).then((r) => r.json());
                  const g = await post('/api/groups',
                    {name: 'Visual group', color: '#7aa2f7'});
                  const ids = [];
                  for (const n of ['group one', 'group two']) {
                    const s = await post('/api/sessions',
                      {cli: 'shell', cwd: '/tmp', name: n});
                    if (s.id) { ids.push(s.id); await post(`/api/groups/${g.id}/add`,
                      {session: s.id}); }
                  }
                  await refresh();
                  return {gid: g.id, ids};
                }"""
            )
            page.wait_for_timeout(1500)
            check("the sidebar lists the group",
                  page.evaluate(
                      "() => !!document.querySelector('#groups .group-row')") is True)
            page.evaluate("() => document.querySelector('#groups .group-open').click()")
            page.wait_for_timeout(4000)
            band = page.evaluate(
                """() => {
                  const tabs = [...document.querySelectorAll('#tabs .tab')];
                  const at = tabs.map((t, i) => t.classList.contains('grouped') ? i : -1)
                    .filter((i) => i >= 0);
                  const starts = tabs.filter((t) => t.classList.contains('group-start')).length;
                  const ends = tabs.filter((t) => t.classList.contains('group-end')).length;
                  const one = tabs.find((t) => t.classList.contains('grouped'));
                  const after = one ? getComputedStyle(one, '::after') : null;
                  return {at, starts, ends,
                          h: after ? after.height : '', bg: after ? after.backgroundColor : '',
                          barHeight: document.querySelector('#tabbar').getBoundingClientRect().height};
                }"""
            )
            check("both members are in the group", len(band["at"]) == 2, band)
            check("their tabs sit next to each other",
                  len(band["at"]) == 2 and band["at"][1] == band["at"][0] + 1, band)
            check("and read as one band, not two pills",
                  band["starts"] == 1 and band["ends"] == 1, band)
            check("the band is drawn in the group's colour",
                  band["h"] == "3px" and "122, 162, 247" in band["bg"], band)
            # The point of putting it inside the tab: a phone has no row to give.
            check("and costs the strip no height", band["barHeight"] <= 36, band)
            page.locator("#tabbar").screenshot(path=str(SHOTS / "group-band.png"))
            page.evaluate(
                """async (gid) => {
                  await fetch(`/api/groups/${gid}/delete`, {method:'POST'});
                  await refresh();
                }""", made["gid"])
            page.wait_for_timeout(400)
            check("deleting the group leaves the tabs alone",
                  page.evaluate(
                      "() => document.querySelectorAll('#tabs .tab.grouped').length") == 0)
            page.evaluate(
                """async (ids) => { for (const id of ids)
                     await fetch(`/api/sessions/${id}`, {method:'DELETE'}); }""",
                made["ids"])
            page.wait_for_timeout(800)
            page.evaluate("(id) => { if (id) selectTab(id); }", was_active)
            page.wait_for_timeout(800)
            check("and the pane you were on is back in front",
                  page.evaluate("() => activeId") == was_active)

            print("reloading an installed app")
            # A PWA has no address bar, so there is no reload in it. The button
            # exists for exactly that case and is deliberately absent from a
            # browser tab, where it would duplicate a control the browser
            # already has in a row that is already full.
            check("a browser tab has no reload button",
                  page.evaluate("() => document.querySelector('#reloadBtn').hidden") is True)
            page.evaluate("() => openPalette()")
            page.wait_for_timeout(300)
            page.keyboard.type("reload")
            page.wait_for_timeout(400)
            offered = page.evaluate(
                """() => [...document.querySelectorAll('#palette .pal-row')]
                     .some((r) => r.textContent.includes('Reload the panel'))"""
            )
            check("but the palette offers it anywhere", offered is True)
            page.keyboard.press("Escape")
            page.wait_for_timeout(300)

            print("the CLI's logo in the corner")
            # The pane edge already carries the CLI colour. This is the same
            # question answered for someone who has not learned the colours,
            # and it has to stay faint enough that output crossing it reads.
            mark = page.evaluate(
                """() => {
                  const el = document.querySelector('#cliMark');
                  if (!el) return null;
                  const cs = getComputedStyle(el);
                  const box = el.getBoundingClientRect();
                  const pane = document.querySelector('#termwrap').getBoundingClientRect();
                  return {
                    hidden: el.hidden, opacity: parseFloat(cs.opacity),
                    w: Math.round(box.width), h: Math.round(box.height),
                    mask: cs.maskImage || cs.webkitMaskImage || '',
                    image: cs.backgroundImage || '',
                    tint: cs.backgroundColor, events: cs.pointerEvents,
                    topRight: box.right <= pane.right + 1 && box.top >= pane.top - 1,
                  };
                }"""
            )
            check("the active CLI's logo is drawn", bool(mark) and not mark["hidden"], mark)
            check("it is the icon, one way or the other",
                  bool(mark) and ("url(" in mark["mask"] or "url(" in mark["image"]), mark)
            check("faint enough to read output through",
                  bool(mark) and 0 < mark["opacity"] <= 0.12, mark)
            check("big enough to recognise across a screen",
                  bool(mark) and mark["w"] >= 60 and mark["h"] >= 60, mark)
            check("in the top-right, opposite the theme character",
                  bool(mark) and mark["topRight"], mark)
            check("and it cannot be clicked",
                  bool(mark) and mark["events"] == "none", mark)
            # A single-colour glyph is masked and takes the CLI's own colour,
            # which is the whole reason one file can serve every mode.
            if "url(" in (mark or {}).get("mask", ""):
                check("a mono glyph is tinted, not left grey",
                      mark["tint"] not in ("rgba(0, 0, 0, 0)", "transparent"), mark["tint"])
            gone = page.evaluate(
                """async () => {
                  await fetch('/api/settings', {method:'PATCH',
                    headers:{'Content-Type':'application/json'},
                    body: JSON.stringify({cli_watermark: false})});
                  await refresh();
                  return document.querySelector('#cliMark').hidden;
                }"""
            )
            check("turning it off turns it off", gone is True, gone)
            page.evaluate(
                """async () => {
                  await fetch('/api/settings', {method:'PATCH',
                    headers:{'Content-Type':'application/json'},
                    body: JSON.stringify({cli_watermark: true})});
                  await refresh();
                }"""
            )
            page.wait_for_timeout(300)

            print("the theme's character in the corner")
            wide = page.viewport_size or {"width": 1440, "height": 900}
            page.evaluate(
                """async () => {
                  await fetch('/api/settings', {method:'PATCH',
                    headers:{'Content-Type':'application/json'},
                    body: JSON.stringify({theme: 'plumber', theme_art: true})});
                  await refresh();
                }"""
            )
            page.wait_for_timeout(600)
            art = page.evaluate(
                """() => {
                  const el = document.querySelector('#themeArt');
                  if (!el) return null;
                  const cs = getComputedStyle(el);
                  const box = el.getBoundingClientRect();
                  const pane = document.querySelector('#termwrap').getBoundingClientRect();
                  return {
                    display: cs.display, blend: cs.mixBlendMode,
                    opacity: parseFloat(cs.opacity), image: cs.backgroundImage.slice(0, 40),
                    events: cs.pointerEvents,
                    w: box.width, h: box.height,
                    insidePane: box.right <= pane.right + 1 && box.bottom <= pane.bottom + 1,
                  };
                }"""
            )
            check("a theme with a figure draws one", bool(art) and art["display"] != "none", art)
            check("it is a picture, not an element full of text",
                  bool(art) and "url(" in art["image"], art)
            # A drawing is composited normally: the extreme blends the grid
            # form uses would eat its blacks and whites. What has to hold is
            # that it is faint enough to read straight through, which the
            # next check is.
            check("it is a drawing, sized to the box rather than to a grid",
                  bool(art) and art["blend"] == "normal", art)
            check("faint enough to read through",
                  bool(art) and 0 < art["opacity"] <= 0.14, art)
            check("and it cannot be clicked",
                  bool(art) and art["events"] == "none", art)
            check("it stays inside the pane",
                  bool(art) and art["insidePane"], art)
            check("and it is big enough to be a character, not a speck",
                  bool(art) and art["h"] > 80 and art["w"] > 60, art)
            page.screenshot(path=str(SHOTS / "theme-art.png"))

            # The gate is the pane's width, not the window's. A window query
            # would be wrong by exactly the width of the sidebar, which is the
            # mistake the status bar's readings already made once.
            page.set_viewport_size({"width": 700, "height": wide["height"]})
            page.wait_for_timeout(500)
            narrow = page.evaluate(
                "() => getComputedStyle(document.querySelector('#themeArt')).display"
            )
            check("a narrow pane takes it away", narrow == "none", narrow)
            page.set_viewport_size(wide)
            page.wait_for_timeout(500)
            back = page.evaluate(
                "() => getComputedStyle(document.querySelector('#themeArt')).display"
            )
            check("and gives it back with the room", back != "none", back)

            off = page.evaluate(
                """async () => {
                  await fetch('/api/settings', {method:'PATCH',
                    headers:{'Content-Type':'application/json'},
                    body: JSON.stringify({theme_art: false})});
                  await refresh();
                  const el = document.querySelector('#themeArt');
                  return {hidden: el.hidden,
                          shown: getComputedStyle(el).display !== 'none'};
                }"""
            )
            check("turning it off turns it off", bool(off) and off["hidden"], off)

            plain = page.evaluate(
                """async () => {
                  await fetch('/api/settings', {method:'PATCH',
                    headers:{'Content-Type':'application/json'},
                    body: JSON.stringify({theme: '', theme_art: true})});
                  await refresh();
                  return document.querySelector('#themeArt').hidden;
                }"""
            )
            check("a theme with no figure draws nothing", plain is True, plain)
            page.wait_for_timeout(300)

        print("the session menu, actually clicked")
        # Being defined is not the assertion; the menu working is. Two of these
        # items threw a ReferenceError for twelve releases and the suite stayed
        # green, because a throw inside a click handler goes to a console
        # nobody has open.
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        row = page.locator(f'.session[data-id="{mine}"]').first
        if row.count():
            row.click(button="right")
            page.wait_for_timeout(350)
            labels = page.evaluate(
                "() => [...document.querySelectorAll('#menu button')].map((b) => b.textContent)"
            )
            check("right-click opens the session menu", len(labels) > 4, labels)
            check("and it offers another CLI when one is installed",
                  any("another CLI" in x for x in labels), labels)

            def click_item(needle):
                return page.evaluate(
                    """(needle) => {
                      const b = [...document.querySelectorAll('#menu button')]
                        .find((x) => x.textContent.includes(needle));
                      if (!b) return false;
                      b.click();
                      return true;
                    }""",
                    needle,
                )

            # Opening the submenu is the part that used to throw.
            check("Move to folder opens the folder list", click_item("Move to folder"))
            page.wait_for_timeout(350)
            folders = page.evaluate(
                "() => [...document.querySelectorAll('#menu button')].map((b) => b.textContent)"
            )
            check("and the list has folders in it", len(folders) > 0, folders)
            page.keyboard.press("Escape")
            page.wait_for_timeout(200)

            row.click(button="right")
            page.wait_for_timeout(300)
            check("Open in another CLI lists the others", click_item("another CLI"))
            page.wait_for_timeout(350)
            clis = page.evaluate(
                "() => [...document.querySelectorAll('#menu button')].map((b) => b.textContent)"
            )
            check("and names at least one", len(clis) > 0, clis)
            page.keyboard.press("Escape")
            page.wait_for_timeout(200)
        check("no menu item threw", not errors, errors)

        print("finding a project by name in the new-session dialog")
        # The unit tests prove the walk. This proves the box people actually
        # type into is wired to it: a bare name is not a path, and before this
        # the field answered nothing at all for one.
        page.evaluate("openModal()")
        page.wait_for_timeout(400)
        page.fill('#newForm [name=cwd]', "clique")
        page.wait_for_timeout(1200)
        listed = page.evaluate(
            """() => [...document.querySelectorAll('#cwdList option')]
                     .map((o) => ({value: o.value, label: o.textContent}))"""
        )
        check("typing a name fills the suggestions", len(listed) > 0, listed)
        check("and one of them is this repo",
              any(o["value"].rstrip("/").endswith("/clique") for o in listed), listed)
        check("the value is a path it can launch in",
              all(o["value"].startswith("/") for o in listed), listed)
        check("and a found project shows its name beside the path",
              any(" · " in (o["label"] or "") for o in listed), listed)
        page.fill('#newForm [name=cwd]', "")
        page.wait_for_timeout(200)
        page.evaluate("document.querySelector('#modal').hidden = true")
        page.wait_for_timeout(300)

        print("copy from the pane")
        # A shell has no mouse tracking, so a drag is a selection. The chip
        # and the clipboard are how we know it actually landed.
        term = page.locator("#terminal .xterm").first
        box = term.bounding_box() if term.count() else None
        check("the pane has a size to drag across", bool(box) and box["width"] > 80, box)
        if box:
            page.mouse.click(box["x"] + 24, box["y"] + 24)
            page.wait_for_timeout(150)
            page.mouse.move(box["x"] + 12, box["y"] + 10)
            page.mouse.down()
            page.mouse.move(
                box["x"] + min(box["width"] - 12, 420),
                box["y"] + min(box["height"] - 12, 36),
                steps=10,
            )
            page.mouse.up()
            page.wait_for_timeout(400)
            check("a drag shows the copy button", page.locator("#copySel").is_visible())
            try:
                taken = page.evaluate("() => navigator.clipboard.readText()")
            except Exception as err:  # noqa: BLE001 — the check names the failure
                taken = str(err)
            check(
                "and the selection is on the clipboard",
                isinstance(taken, str) and len(taken.strip()) > 0,
                (taken[:80] if isinstance(taken, str) else taken),
            )
            # Same pane, with the CLI listening for clicks — the case that
            # used to swallow the drag so there was nothing to copy.
            _api(
                "/api/sessions/" + mine + "/send",
                "POST",
                {"text": "printf '\\033[?1000h\\033[?1002h\\033[?1006h'", "enter": True},
            )
            page.wait_for_timeout(500)
            tracking = page.evaluate("() => !!document.querySelector('.xterm.enable-mouse-events')")
            check("the pane is now eating mouse events", tracking)
            page.mouse.click(box["x"] + 8, box["y"] + box["height"] / 2)
            page.wait_for_timeout(150)
            page.mouse.move(box["x"] + 12, box["y"] + 10)
            page.mouse.down()
            page.mouse.move(
                box["x"] + min(box["width"] - 12, 420),
                box["y"] + min(box["height"] - 12, 36),
                steps=10,
            )
            page.mouse.up()
            page.wait_for_timeout(400)
            check(
                "a drag still selects while the CLI is eating clicks",
                page.locator("#copySel").is_visible(),
            )

        print("copy from a boxed CLI")
        # The CLIs people actually copy from turn mouse tracking on. The
        # panel hides that from the browser, a drag is a native selection,
        # and a click is still delivered as SGR.
        boxed = _api(
            "/api/sessions", "POST", {"cli": "boxed", "cwd": "/tmp", "name": "boxed copy"}
        )["id"]
        page.wait_for_timeout(400)
        # Pull the session list before opening it. Half of what the panel does
        # with a pane it reads off the record rather than the terminal --
        # `own_input` is what decides whether a click is forwarded to the CLI at
        # all -- and a session made through the API is not in `state.sessions`
        # until a poll brings it in. The UI refreshes on create; a test that
        # skips it is testing a panel that does not know what it is looking at,
        # and this one silently was from 0.62.0 until 2026-09-02.
        page.evaluate("async (id) => { await refresh(); await openSession(id); }", boxed)
        try:
            page.wait_for_function(
                """() => {
                  const e = terms.get(activeId);
                  if (!e || !e.term) return false;
                  const line = e.term.buffer.active.getLine(e.term.buffer.active.viewportY);
                  const text = line ? line.translateToString(true) : '';
                  return text.includes('BOXED-COPY-LINE');
                }""",
                timeout=8000,
            )
        except Exception as err:  # noqa: BLE001 — the checks below name what failed
            print(f"       boxed pane did not draw: {err}")
        # The *active* pane, not the first one in the DOM. Panes for other
        # sessions stay mounted, so `.first` is whichever was created earliest
        # and clicking its box aims at a session nobody is looking at.
        boxed_box = page.evaluate(
            """() => {
              const e = terms.get(activeId);
              const el = e && e.term && e.term.element;
              if (!el) return null;
              const b = el.getBoundingClientRect();
              return { x: b.x, y: b.y, width: b.width, height: b.height };
            }"""
        )
        tracking = page.evaluate(
            """() => {
              const e = terms.get(activeId);
              return !!(e && e.term && e.term.element &&
                e.term.element.classList.contains('enable-mouse-events'));
            }"""
        )
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
            }"""
        )
        check("the boxed stand-in actually drew", "BOXED-COPY-LINE" in pane_text, pane_text[:120])
        if boxed_box:
            page.mouse.move(boxed_box["x"] + 12, boxed_box["y"] + 10)
            page.mouse.down()
            page.mouse.move(
                boxed_box["x"] + min(boxed_box["width"] - 12, 360), boxed_box["y"] + 18, steps=8
            )
            page.mouse.up()
            page.wait_for_timeout(400)
            check(
                "a drag on a boxed pane shows the copy button",
                page.locator("#copySel").is_visible(),
            )
            try:
                taken = page.evaluate("() => navigator.clipboard.readText()")
            except Exception as err:  # noqa: BLE001 — the check names the failure
                taken = str(err)
            check(
                "and copied the boxed line",
                isinstance(taken, str) and "COPY-LINE" in taken.upper(),
                (taken[:80] if isinstance(taken, str) else taken),
            )
            page.screenshot(path=str(SHOTS / "boxed-copy.png"))
            page.evaluate(
                "() => { const e = terms.get(activeId); if (e && e.term) e.term.clearSelection(); }"
            )
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
                    timeout=6000,
                )
                clicked_ok = True
                after_click = "clicked"
            except Exception as err:  # noqa: BLE001 — the check names what failed
                clicked_ok = False
                after_click = str(err)
            check("a click still reaches the boxed CLI", clicked_ok, after_click)
            print("zooming a boxed pane instead of wrapping it")
            before_cols = page.evaluate(
                "() => { const e = terms.get(activeId); return e && e.term ? e.term.cols : 0; }"
            )
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
                }"""
            )
            check(
                "the grid did not get narrower",
                isinstance(zoomed, dict) and zoomed.get("cols") == before_cols,
                (zoomed, before_cols),
            )
            check(
                "the pane zoomed to fit instead",
                isinstance(zoomed, dict) and "scale(" in (zoomed.get("transform") or ""),
                zoomed,
            )
            check(
                "and the boxed line did not wrap",
                isinstance(zoomed, dict) and "BOXED-COPY-LINE" in (zoomed.get("text") or ""),
                zoomed,
            )
            page.screenshot(path=str(SHOTS / "boxed-zoom.png"))
            page.set_viewport_size({"width": 1400, "height": 900})
            page.wait_for_timeout(400)
        _remove_session(boxed)
        page.evaluate("(id) => openSession(id)", mine)
        page.wait_for_timeout(400)

        print("tabs that do not fit")
        names = [
            "Duchamp Events Dev",
            "CLIque Code Review",
            "Whatbox IPTV Dev",
            "WSG Platform Gen",
            "Sentinel Dev",
            "Duchamp Room Rates",
            "Meridian Nightly",
            "Daily Deck Writer",
            "Prowler Scan",
            "Inbox Agent",
            "Dealophant Shop",
        ]
        extra: list[str] = []
        try:
            for name in names:
                extra.append(
                    _api("/api/sessions", "POST", {"cli": "shell", "cwd": "/tmp", "name": name})[
                        "id"
                    ]
                )
            page.wait_for_timeout(3500)
            page.evaluate(
                """(ids) => { openTabs = ids; activeId = ids[0]; renderTabs(); }""", extra
            )
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
                }"""
            )
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
                }"""
            )
            check(
                "active tab is heavier than its neighbours",
                contrast.get("ok")
                and contrast["activeWeight"] >= 600
                and contrast["activeWeight"] > contrast["otherWeight"],
                contrast,
            )
            check(
                "active tab is not the same colour as an idle one",
                contrast.get("ok") and contrast["activeColor"] != contrast["otherColor"],
                contrast,
            )
        finally:
            for sid in extra:
                _remove_session(sid)

        print("a path you can look at")
        work = SANDBOX / "work"
        sample = work / "clique-visual-file.md"
        sample.write_text("# Hello from a click\n\nNot an editor.\n", encoding="utf-8")
        (work / "sub").mkdir(exist_ok=True)
        (work / "sub" / "inside.md").write_text("nested file\n", encoding="utf-8")
        shutil.copy(
            Path(__file__).resolve().parents[1] / "clique" / "web" / "brand" / "icon-64.png",
            work / "shot.png",
        )
        page.evaluate("([id, p]) => openFileSheet(id, p)", [mine, str(sample)])
        page.wait_for_function(
            """() => {
              const el = document.getElementById('fileText');
              return el && !el.hidden && (el.innerText || '').includes('Hello from a click');
            }""",
            timeout=8000,
        )
        check("the file sheet opens", page.locator("#file").is_visible())
        shown = page.locator("#fileText").inner_text()
        check("and it shows the text", "Hello from a click" in shown, shown[:80])
        page.locator("#file").screenshot(path=str(SHOTS / "file-sheet.png"))

        page.evaluate("([id, p]) => openFileSheet(id, p)", [mine, str(work / "shot.png")])
        page.wait_for_function(
            """() => {
              const el = document.getElementById('fileImg');
              return el && !el.hidden && el.complete && el.naturalWidth > 0;
            }""",
            timeout=8000,
        )
        check("an image opens in the sheet", page.locator("#fileImg").is_visible())
        page.locator("#file").screenshot(path=str(SHOTS / "file-image.png"))

        page.evaluate("([id, p]) => openFileSheet(id, p)", [mine, str(work)])
        page.wait_for_function(
            "() => document.querySelectorAll('#fileList button').length > 0",
            timeout=8000,
        )
        listed = page.locator("#fileList button").all_inner_texts()
        check(
            "a directory lists its files",
            any("clique-visual-file.md" in t for t in listed),
            listed[:8],
        )
        page.locator("#file").screenshot(path=str(SHOTS / "file-dir.png"))
        page.locator("#fileList button").filter(has_text="clique-visual-file.md").first.click()
        page.wait_for_function(
            """() => {
              const el = document.getElementById('fileText');
              return el && !el.hidden && (el.innerText || '').includes('Hello from a click');
            }""",
            timeout=8000,
        )
        check("clicking a listing opens the file", "Hello from a click" in page.locator("#fileText").inner_text())

        page.keyboard.press("Escape")
        page.wait_for_timeout(200)
        check("escape closes it", page.locator("#file").is_hidden())

        page.screenshot(path=str(SHOTS / "panel.png"), full_page=False)

        print("what's new")
        check(
            "the mark is off until there is something to read",
            page.locator("#whatsNew").is_hidden(),
        )
        page.evaluate("() => { state.settings.changelog_seen = '0.1.0'; renderVersion(); }")
        page.wait_for_timeout(150)
        check("an unread release lights the bottom bar", page.locator("#whatsNew").is_visible())
        page.locator("#whatsNew").click()
        page.wait_for_timeout(800)
        check(
            "clicking it opens the notes",
            page.locator("#settings").is_visible()
            and page.locator('.pane[data-pane="changelog"]:not([hidden])').count() > 0,
        )
        check(
            "and the mark goes out once they have been opened",
            page.locator("#whatsNew").is_hidden(),
        )
        articles = page.locator("#changelog .clog-entry")
        try:
            page.wait_for_selector("#changelog .clog-entry", timeout=4000)
        except Exception as err:  # noqa: BLE001 — the checks below name what failed
            print(f"       changelog did not draw: {err}")
        check("the sheet holds the last few releases", articles.count() == 5, articles.count())
        more = page.locator("a.clog-more")
        href = more.get_attribute("href") if more.count() else ""
        check(
            "and the rest is a link to the file on GitHub",
            more.is_visible() and "CHANGELOG.md" in (href or ""),
            href,
        )
        page.screenshot(path=str(SHOTS / "changelog.png"))
        page.screenshot(path=str(SHOTS / "settings.png"))
        check("settings opens", page.locator("#settings").is_visible())

        # The theme maker lives at the bottom of a scrolling pane, which is
        # exactly where the About tab once disappeared. Being in the DOM is
        # not the assertion; being on screen is. Run here, with the sheet
        # already open, so it cannot disturb anything that follows.
        # Earlier tests left the sheet on another pane, and the panes are
        # hidden rather than scrolled past, so ask for Appearance by name.
        page.click('#setTabs button[data-pane="appearance"]')
        page.wait_for_timeout(300)
        page.evaluate(
            "() => { const p = document.querySelector('#settings .panes');"
            " if (p) p.scrollTop = p.scrollHeight; }"
        )
        page.wait_for_timeout(300)
        for sel, name in (
            ("#themePrompt", "the description box"),
            ("#themeGen", "the generate button"),
            ("#themeGenNote", "the note saying what it needs"),
        ):
            node = page.locator(sel)
            box = node.bounding_box() if node.count() else None
            check(f"{name} is on screen in Appearance",
                  bool(box) and box["width"] > 20 and box["height"] > 8, box)
        check("the button is off until a provider is set up",
              page.evaluate("() => document.querySelector('#themeGen').disabled") is True)

        # Which themes come with a character, said in the picker itself. A
        # marker glyph would have needed a legend; a group says it in words.
        # Worth asserting because it is easy to break by adding a theme and
        # not thinking about which group it lands in.
        picker = page.evaluate(
            """() => {
              const sel = document.querySelector('#setTheme');
              if (!sel) return null;
              const groups = [...sel.querySelectorAll('optgroup')].map((g) => ({
                label: g.label,
                items: [...g.querySelectorAll('option')].map((o) => o.value),
              }));
              return {
                groups,
                loose: [...sel.children].filter((c) => c.tagName === 'OPTION').length,
                total: sel.querySelectorAll('option').length,
              };
            }"""
        )
        check("the theme picker is grouped", bool(picker) and len(picker["groups"]) >= 2, picker)
        check("and no theme escapes a group",
              bool(picker) and picker["loose"] == 0, picker)
        named = {g["label"]: g["items"] for g in (picker or {}).get("groups", [])}
        drawn = named.get("With a character", [])
        check("the seven with a figure are grouped as such",
              sorted(drawn) == sorted(
                  ["aincrad", "bricks", "chompy", "drizzt",
                   "fellowship", "plumber", "triforce"]), drawn)
        check("and the plain presets are not in with them",
              "dracula" in named.get("Presets", []), named.get("Presets"))
        check("every theme is still reachable",
              bool(picker) and picker["total"] == page.evaluate(
                  "() => Object.keys(window.CLIQUE_THEMES || {}).length"), picker)

        # The rotation: the same list again as checkboxes, because a phone
        # cannot ctrl-click a multi-select. What matters visually is that the
        # two lists agree, that ticking one actually stores it, and that the
        # schedule fields grey out with the switch rather than sitting there
        # live and doing nothing.
        print("rotating through the themes you like")
        rot = page.evaluate(
            """() => {
              const pool = document.querySelector('#themeRotatePool');
              if (!pool) return null;
              const boxes = [...pool.querySelectorAll('input[type=checkbox]')];
              const rows = [...pool.querySelectorAll('label')];
              return {
                count: boxes.length,
                ids: boxes.map((b) => b.dataset.theme),
                groups: [...pool.querySelectorAll('.rotate-head')].map((h) => h.textContent),
                shortest: Math.min(...rows.map((r) => Math.round(
                  r.getBoundingClientRect().height))),
                hoursOff: document.querySelector('#setThemeRotateHours').disabled,
                atOff: document.querySelector('#setThemeRotateAt').disabled,
              };
            }"""
        )
        every = page.evaluate("() => Object.keys(window.CLIQUE_THEMES || {}).length")
        check("every theme can be put in the rotation", bool(rot) and rot["count"] == every, rot)
        check("grouped the same way the picker is",
              bool(rot) and "With a character" in rot["groups"], rot)
        check("a thumb can hit a row", bool(rot) and rot["shortest"] >= 44, rot)
        check("the schedule is greyed out until it is switched on",
              bool(rot) and rot["hoursOff"] and rot["atOff"], rot)
        stored = page.evaluate(
            """async () => {
              document.querySelector('#setThemeRotate').click();
              await new Promise((r) => setTimeout(r, 400));
              await refresh();
              // Looked up again rather than held across the save: writing a
              // setting re-renders the pool, and a box captured before that is
              // a detached node nothing is listening to.
              const box = document.querySelector('#themeRotatePool input[type=checkbox]');
              box.click();
              await new Promise((r) => setTimeout(r, 400));
              await refresh();
              return {
                pool: state.settings.theme_rotate_pool,
                on: state.settings.theme_rotate,
                live: !document.querySelector('#setThemeRotateAt').disabled,
                wanted: box.dataset.theme,
              };
            }"""
        )
        check("switching it on ungreys the schedule", stored.get("live") is True, stored)
        check("and ticking a theme puts it in the pool",
              stored.get("pool") == [stored.get("wanted")] and stored.get("on") is True, stored)
        page.screenshot(path=str(SHOTS / "theme-rotation.png"))

        page.screenshot(path=str(SHOTS / "theme-maker.png"))

        # Phone build, first pass: a narrow viewport, reloaded so the mobile
        # bootstrap runs (drawer starts closed), then the drawer opened over the
        # pane. Not asserted yet — screenshots to iterate the layout on.
        page.set_viewport_size({"width": 390, "height": 844})
        page.goto(BASE, wait_until="networkidle")
        page.wait_for_timeout(600)
        # Open the session so activeId is set — the key row shows with a pane in front.
        page.evaluate(
            "typeof state !== 'undefined' && state.sessions[0] && openSession(state.sessions[0].id)"
        )
        page.wait_for_timeout(900)
        page.screenshot(path=str(SHOTS / "mobile-closed.png"))

        print("scrolling the pane with a finger")
        # Three separate things had to be true for this to work and none of
        # them was, so it is worth pinning all three. tmux has to keep history
        # (it kept none while the CLI held the alternate screen), the browser
        # has to be showing the buffer that history is in (it was showing the
        # alternate one), and something has to turn a drag into a scroll (the
        # pane only ever listened for a wheel). A phone has no wheel.
        page.evaluate(
            """() => { const e = [...terms.values()][0];
                 for (let i = 0; i < 400; i++) e.term.write('scrollback ' + i + '\\r\\n'); }"""
        )
        page.wait_for_timeout(900)
        state = page.evaluate(
            """() => { const t = [...terms.values()][0].term;
                 const host = document.querySelector('#terminal > div[data-session]');
                 return {kind: t.buffer.active.type, back: t.buffer.active.baseY,
                         touch: host ? getComputedStyle(host).touchAction : ''}; }"""
        )
        check("the pane shows the buffer that holds history",
              state["kind"] == "normal", state)
        check("and there is history in it", state["back"] > 20, state)
        check("the pane claims the gesture rather than the browser",
              state["touch"] == "none", state)

        cdp = context.new_cdp_session(page)
        cdp.send("Emulation.setTouchEmulationEnabled",
                 {"enabled": True, "maxTouchPoints": 1})
        box = page.evaluate(
            """() => { const r = document.querySelector('#terminal').getBoundingClientRect();
                 return {x: Math.round(r.x + r.width / 2),
                         y: Math.round(r.y + r.height / 2)}; }"""
        )

        def finger(dy: int) -> None:
            cdp.send("Input.dispatchTouchEvent",
                     {"type": "touchStart", "touchPoints": [box]})
            for i in range(1, 21):
                cdp.send("Input.dispatchTouchEvent", {
                    "type": "touchMove",
                    "touchPoints": [{"x": box["x"], "y": box["y"] + dy * i // 20}]})
            cdp.send("Input.dispatchTouchEvent",
                     {"type": "touchEnd", "touchPoints": []})
            page.wait_for_timeout(500)

        where = lambda: page.evaluate(          # noqa: E731 - a probe, not a design
            "() => [...terms.values()][0].term.buffer.active.viewportY")
        page.evaluate("() => [...terms.values()][0].term.scrollToBottom()")
        page.wait_for_timeout(300)
        bottom = where()
        finger(300)
        back = where()
        check("dragging down goes back through the scrollback", back < bottom,
              {"from": bottom, "to": back})
        finger(-300)
        check("and dragging up comes forward again", where() > back,
              {"from": back, "to": where()})
        # Typing on a phone, which is a different question from typing on a
        # desktop and was got wrong twice. Touch emulation is on from the
        # scroll test above, so `(pointer: coarse)` matches and this is the
        # real code path rather than a stub.
        print("typing on a phone goes to the box, not the terminal")
        # Its own, because the earlier boxed sessions are gone by now and the
        # only thing left is a shell, which draws no box of its own and so
        # would have had the panel's one anyway. The whole question here is
        # what happens to a CLI that *does* draw one.
        phone_boxed = _api(
            "/api/sessions", "POST", {"cli": "boxed", "cwd": "/tmp", "name": "phone typing"}
        )["id"]
        page.wait_for_timeout(600)
        typing = page.evaluate(
            """async (id) => {
              await refresh();
              const boxed = state.sessions.find((s) => s.id === id && s.own_input);
              if (!boxed) return { skipped: true };
              await openSession(boxed.id);
              await new Promise((r) => setTimeout(r, 700));
              const active = () => document.activeElement;
              const inPane = (el) => !!(el && el.classList
                && el.classList.contains('xterm-helper-textarea'));
              const before = inPane(active());
              const key = document.querySelector('#keyrow [data-key]');
              if (key) key.click();
              await new Promise((r) => setTimeout(r, 300));
              return {
                coarse: matchMedia('(pointer: coarse)').matches,
                ownInput: true,
                boxShown: !document.querySelector('#inputbar').hidden,
                paneTookFocusOnOpen: before,
                paneTookFocusFromKeyRow: inPane(active()),
              };
            }""",
            phone_boxed,
        )
        if typing.get("skipped"):
            print("       the boxed stand-in did not arrive; nothing to test with")
        else:
            check("a phone gets the panel's box even for a CLI with its own",
                  typing["coarse"] and typing["boxShown"], typing)
            # The half of 0.66.0 that was missing: the box was drawn and the
            # pane was handed the keyboard anyway, so Gboard went on typing
            # into the terminal and went on duplicating the line.
            check("opening a session does not hand the keyboard to the pane",
                  typing["paneTookFocusOnOpen"] is False, typing)
            check("and a key-row tap does not take it back",
                  typing["paneTookFocusFromKeyRow"] is False, typing)

        # Everything a phone can only do by long-pressing, and the menu it
        # long-presses into. All three were reported by a second model on
        # 2026-09-02 and confirmed against the code before being believed.
        print("what a phone can actually reach")
        page.evaluate("setSidebar(true)")
        page.wait_for_timeout(500)
        menu = page.evaluate(
            """async () => {
              const row = document.querySelector('#tree .session');
              if (!row) return { skipped: true };
              row.dispatchEvent(new MouseEvent('contextmenu', {
                bubbles: true, cancelable: true, clientX: 40, clientY: 300 }));
              await new Promise((r) => setTimeout(r, 250));
              const el = document.querySelector('#menu');
              const box = el.getBoundingClientRect();
              const cs = getComputedStyle(el);
              return {
                rows: el.querySelectorAll('button').length,
                top: Math.round(box.top),
                bottom: Math.round(box.bottom),
                windowH: innerHeight,
                scrolls: cs.overflowY === 'auto' || cs.overflowY === 'scroll',
                reachable: el.scrollHeight > el.clientHeight
                  ? el.scrollHeight > 0 : true,
              };
            }"""
        )
        if not menu.get("skipped"):
            # It ran off both ends at once: the clamp goes negative when the
            # menu is taller than the window, hiding Open above the screen
            # while Kill sat below it, with nothing to scroll.
            check("the long-press menu starts on screen",
                  menu["top"] >= 0, menu)
            check("and ends on screen", menu["bottom"] <= menu["windowH"] + 1, menu)
            check("scrolling to the rest of it is possible",
                  menu["scrolls"] and menu["reachable"], menu)
        page.evaluate("() => { document.querySelector('#menu').hidden = true; }")

        # Its own group rather than the one the desktop pass made: that one may
        # or may not have survived to here, and a check that quietly skips
        # itself is a check that stops being run.
        page.evaluate(
            """async () => {
              if (document.querySelector('#groups .group-row')) return;
              await fetch('/api/groups', {method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({name: 'Phone group', color: '#7aa2f7'})});
              await refresh();
            }"""
        )
        page.wait_for_timeout(500)
        groups_touch = page.evaluate(
            """async () => {
              const row = document.querySelector('#groups .group-row');
              if (!row) return { skipped: true };
              const at = row.getBoundingClientRect();
              const point = { clientX: at.x + 20, clientY: at.y + 10 };
              row.dispatchEvent(new TouchEvent('touchstart', {
                bubbles: true, cancelable: true,
                touches: [new Touch({ identifier: 1, target: row, ...point })] }));
              await new Promise((r) => setTimeout(r, 800));
              const open = !document.querySelector('#menu').hidden;
              const items = [...document.querySelectorAll('#menu button')]
                .map((b) => b.textContent);
              document.querySelector('#menu').hidden = true;
              return { open, items };
            }"""
        )
        if groups_touch.get("skipped"):
            print("       no group in the sidebar to press")
        else:
            # Rename and delete were right-click only, so from a phone you
            # could launch a working group and never change or remove one.
            check("a working group answers a long press", groups_touch["open"], groups_touch)
            check("and offers more than the Open button already does",
                  any("ename" in t for t in groups_touch["items"]), groups_touch)

        reachable = page.evaluate(
            """async () => {
              const attach = document.querySelector('#attachFile');
              const term = document.querySelector('#terminal');
              const at = term.getBoundingClientRect();
              term.dispatchEvent(new MouseEvent('contextmenu', {
                bubbles: true, cancelable: true,
                clientX: Math.round(at.x + 30), clientY: Math.round(at.y + 30) }));
              await new Promise((r) => setTimeout(r, 250));
              const items = [...document.querySelectorAll('#menu button')]
                .map((b) => b.textContent);
              document.querySelector('#menu').hidden = true;
              return {
                attachShown: !!attach && attach.offsetParent !== null,
                picker: !!document.querySelector('#filePick'),
                paneItems: items,
              };
            }"""
        )
        check("a phone can hand a session a file", reachable["attachShown"]
              and reachable["picker"], reachable)
        # A finger drag is our scroll, so nothing left makes an xterm
        # selection and the Copy chip never appears. Without these you cannot
        # get an error message off the screen at all.
        check("and can copy output off the pane",
              any("Copy" in t for t in reachable["paneItems"]), reachable)

        # The narrowest phone anyone still has. The attach button is one more
        # control in a bar that was already tight, and a row that overflows
        # here puts Run off the edge of the screen.
        page.set_viewport_size({"width": 320, "height": 568})
        page.wait_for_timeout(500)
        narrow = page.evaluate(
            """() => {
              const bar = document.querySelector('#inputbar');
              const run = document.querySelector('#run');
              const clip = run.getBoundingClientRect();
              return {
                over: bar.scrollWidth - bar.clientWidth,
                promptW: Math.round(
                  document.querySelector('#prompt').getBoundingClientRect().width),
                runRight: Math.round(clip.right),
                width: innerWidth,
              };
            }"""
        )
        check("the input bar fits a 320px phone", narrow["over"] <= 0, narrow)
        # It measured 26px before the bar was allowed to wrap: every control
        # around it refuses to shrink and the textarea was the only thing
        # giving. Narrower than one word, on the box a phone types prompts in.
        check("and the prompt box is a box", narrow["promptW"] >= 180, narrow)
        check("and Run is still on the screen",
              narrow["runRight"] <= narrow["width"] + 1, narrow)
        page.set_viewport_size({"width": 390, "height": 844})

        page.wait_for_timeout(300)
        page.screenshot(path=str(SHOTS / "mobile-drawer.png"))

        browser.close()

    print(f"\nscreenshots in {SHOTS}")
    print(f"{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
