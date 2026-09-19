#!/usr/bin/env python3
"""Verify the sidebar search, in a real (headless) browser.

Feature: the search box above the session tree. Two things about it have been
wrong in ways nothing else caught, so they are asserted here:

- a search finds a session even when the running-only filter is on, and even
  when the match is a stopped one, a filed one, an archived one, or a past
  conversation from history rather than a live session;
- the clear x fires on the press. It is a small target beside a text input, and
  a click needs the press and the release to land on the same element, so a
  drifting mouse used to eat it and it read as intermittent.

    ~/.cache/clique-visual/bin/python tools/search_check.py

Its own home, port and tmux socket. 0 on pass, 1 on fail.
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

PASSWORD = "search-check"  # noqa: S105 — a throwaway panel on loopback
PORT = 3302
BASE = f"http://127.0.0.1:{PORT}"
HOME = Path("/tmp/clique-search-check-home")
SOCKET = "clique-search-check"
WORK = Path("/tmp/clique-search-check-work")

# Injected in the same turn as the assertion: the poll rewrites `resumable`
# from the server, and a fake history entry would not survive a round trip.
INJECT = """([folder, query]) => {
  const box = document.getElementById('q');
  box.value = query; syncQClear();
  resumable = [
    {cli_session_id:'hx1', label:'narwhal from history', cwd:'/tmp/x',
     folder: folder, updated: Math.floor(Date.now()/1000), cli:'claude', repeats:1},
    {cli_session_id:'hx2', label:'seal unfiled history', cwd:'/tmp/y',
     folder: null, updated: Math.floor(Date.now()/1000), cli:'claude', repeats:1},
  ];
  treeFp = ''; renderTree();
  return document.getElementById('tree').innerText;
}"""


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
            page.goto(BASE, wait_until="domcontentloaded")
            page.wait_for_timeout(700)

            def new_session(name: str) -> str:
                return page.evaluate(
                    """async ([cwd, name]) => {
                      const r = await fetch('/api/sessions', {method:'POST',
                        headers:{'Content-Type':'application/json'},
                        body: JSON.stringify({cli:'shell', cwd, name})});
                      const id = (await r.json()).id; await refresh(); return id;
                    }""",
                    [str(WORK), name],
                )

            def type_query(text: str) -> None:
                page.evaluate(
                    "() => { const q=document.getElementById('q'); q.value=''; "
                    "syncQClear(); treeFp=''; renderTree(); }"
                )
                page.click("#q")
                page.keyboard.type(text)
                page.wait_for_timeout(400)

            def on_screen(session_id: str) -> bool:
                return page.evaluate(
                    '(id) => Boolean(document.querySelector(`.session[data-id="${id}"]`))',
                    session_id,
                )

            new_session("zebra alive")
            stopped = new_session("walrus stopped")
            folder = page.evaluate(
                """async () => {
                  const r = await fetch('/api/folders', {method:'POST',
                    headers:{'Content-Type':'application/json'},
                    body: JSON.stringify({name:'Projects', color:'#888888'})});
                  const f = await r.json(); await refresh();
                  return f.id || (f.folders || []).slice(-1)[0].id;
                }"""
            )
            filed = new_session("badger filed")
            archived = new_session("otter archived")
            page.wait_for_timeout(400)
            page.evaluate(
                """async ([folder, filed, archived, stopped]) => {
                  const patch = (id, body) => fetch(`/api/sessions/${id}`, {method:'PATCH',
                    headers:{'Content-Type':'application/json'}, body: JSON.stringify(body)});
                  const kill = (id) => fetch(`/api/sessions/${id}/kill`, {method:'POST'});
                  await patch(filed, {folder});
                  await patch(archived, {archived: true});
                  for (const id of [filed, archived, stopped]) await kill(id);
                  await fetch('/api/settings', {method:'PATCH',
                    headers:{'Content-Type':'application/json'},
                    body: JSON.stringify({history_in_sidebar: true})});
                  await refresh();
                }""",
                [folder, filed, archived, stopped],
            )
            page.wait_for_timeout(1200)
            res["stopped_is_stopped"] = page.evaluate(
                "(id) => { const s = state.sessions.find(x => x.id === id); "
                "return Boolean(s) && !s.alive; }",
                stopped,
            )

            # Running-only on. Nothing stopped shows until something is typed.
            page.evaluate("() => { if (!activeOnly) toggleActiveOnly(); }")
            page.wait_for_timeout(300)
            res["filter_on"] = page.evaluate("() => activeOnly")
            res["stopped_hidden_unsearched"] = not on_screen(stopped)

            type_query("walrus")
            res["stopped_found"] = on_screen(stopped)
            type_query("badger")
            res["filed_found"] = on_screen(filed)
            type_query("otter")
            res["archived_found"] = on_screen(archived)

            # History rows are part of what a search can find. A folder whose
            # only match is a past conversation used to be dropped whole.
            res["history_in_folder_found"] = "narwhal from history" in page.evaluate(
                INJECT, [folder, "narwhal"]
            )
            res["history_unfiled_found"] = "seal unfiled history" in page.evaluate(
                INJECT, [folder, "seal"]
            )

            # The clear x, pressed and released 40px apart.
            type_query("walrus")
            box = page.evaluate(
                """() => { const b = document.getElementById('qClear');
                  if (!b || b.hidden) return null;
                  const r = b.getBoundingClientRect();
                  return {x: r.x, y: r.y, w: r.width, h: r.height}; }"""
            )
            res["clear_shown"] = bool(box)
            res["clear_target_big_enough"] = bool(box) and box["w"] >= 24 and box["h"] >= 24
            if box:
                page.mouse.move(box["x"] + box["w"] / 2, box["y"] + box["h"] / 2)
                page.mouse.down()
                page.mouse.move(box["x"] - 40, box["y"] + box["h"] / 2 + 30)
                page.mouse.up()
                page.wait_for_timeout(300)
                res["clear_survives_drift"] = page.evaluate(
                    "() => document.getElementById('q').value === ''"
                )
                res["clear_hides_itself"] = page.evaluate(
                    "() => document.getElementById('qClear').hidden"
                )

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
    print("search_check:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
