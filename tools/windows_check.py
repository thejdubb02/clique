#!/usr/bin/env python3
"""Two browser windows find each other and hand a session between them.

Same-origin windows talk over a BroadcastChannel (no server) so a session's tab
can be moved from one window to another — one monitor to the next. This drives
two real pages in one browser (which is what shares the channel) and checks the
whole path: presence, the window-number chip, the "Move to … window" menu item,
and that a move actually opens the session in the target and closes it in the
source.

Needs the Playwright venv:

    ~/.cache/clique-visual/bin/python tools/windows_check.py

Exit status is 0 on pass, 1 on fail.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import tools.visual_check as vc
from clique.auth import COOKIE_NAME, Auth


def main() -> int:
    from playwright.sync_api import sync_playwright

    panel = vc._own_panel()
    res: dict[str, object] = {}
    try:
        auth = Auth(vc.PASSWORD, vc.SANDBOX / "secret")
        alpha = vc._api("/api/sessions", "POST", {"cli": "shell", "cwd": "/tmp", "name": "alpha"})[
            "id"
        ]
        vc._api("/api/sessions", "POST", {"cli": "shell", "cwd": "/tmp", "name": "bravo"})
        with sync_playwright() as play:
            browser = play.chromium.launch()
            ctx = browser.new_context(viewport={"width": 1200, "height": 760})
            ctx.add_init_script("try { localStorage.setItem('clique.gpu','0'); } catch (e) {}")
            ctx.add_cookies(
                [{"name": COOKIE_NAME, "value": auth.issue(), "domain": "127.0.0.1", "path": "/"}]
            )
            errs: list[str] = []
            p1 = ctx.new_page()
            p1.on("pageerror", lambda e: errs.append("p1:" + str(e)))
            p2 = ctx.new_page()
            p2.on("pageerror", lambda e: errs.append("p2:" + str(e)))
            p1.goto(vc.BASE, wait_until="networkidle")
            p2.goto(vc.BASE, wait_until="networkidle")

            def peers(p) -> int:
                return p.evaluate("() => winPeers.size")

            for _ in range(40):
                if peers(p1) >= 2 and peers(p2) >= 2:
                    break
                time.sleep(0.25)

            res["presence_both_see_two"] = peers(p1) >= 2 and peers(p2) >= 2
            l1 = p1.evaluate("() => windowLabel(winId)")
            l2 = p2.evaluate("() => windowLabel(winId)")
            res["labels_distinct"] = {l1, l2} == {1, 2}
            res["chip_shown_both"] = p1.evaluate(
                "() => !document.getElementById('winTag').hidden"
            ) and p2.evaluate("() => !document.getElementById('winTag').hidden")
            res["menu_offers_move"] = (
                p1.evaluate("(id) => windowMoveItems(session(id)).length", alpha) == 1
            )

            w2 = p2.evaluate("() => winId")
            p1.evaluate("(id) => openSession(id)", alpha)
            time.sleep(0.6)
            had_before = p1.evaluate("(id) => openTabs.includes(id)", alpha)
            p1.evaluate("([id, w]) => moveSessionToWindow(id, w)", [alpha, w2])
            time.sleep(1.0)
            res["source_released"] = had_before and not p1.evaluate(
                "(id) => openTabs.includes(id)", alpha
            )
            res["target_received"] = p2.evaluate("(id) => openTabs.includes(id)", alpha)
            res["target_flashed"] = p2.evaluate(
                "() => document.body.classList.contains('win-flash')"
            )
            res["no_console_errors"] = not errs
            if errs:
                res["errors"] = errs[:4]
    finally:
        panel.terminate()

    ok = all(v for k, v in res.items() if k != "errors")
    for key, value in res.items():
        print(f"  {'ok  ' if value or key == 'errors' else 'FAIL'} {key}: {value}")
    print("windows_check:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
