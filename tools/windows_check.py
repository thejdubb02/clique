#!/usr/bin/env python3
"""Two browser windows: open clean, hand sessions across, collect on close.

Same-origin windows talk over a BroadcastChannel (no server), so a session's tab
can move between windows — one monitor to the next. This drives two real pages
in one browser (which is what shares the channel) and checks the whole handoff:

- a first window restores the shared workspace; a *second* window opens CLEAN,
  so a second screen is a fresh desk, not a copy of the first;
- presence, the window-number chip, and the "Move to … window" menu item;
- a move opens the session in the target and closes it in the source;
- closing a window collects its tabs into the remaining (primary) window.

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
        # Seed the shared workspace with one tab, as a prior single window would.
        vc._api("/api/settings", "PATCH", {"open_tabs": [alpha], "active_tab": alpha})

        with sync_playwright() as play:
            browser = play.chromium.launch()
            ctx = browser.new_context(viewport={"width": 1200, "height": 760})
            ctx.add_init_script("try { localStorage.setItem('clique.gpu','0'); } catch (e) {}")
            ctx.add_cookies(
                [{"name": COOKIE_NAME, "value": auth.issue(), "domain": "127.0.0.1", "path": "/"}]
            )
            errs: list[str] = []

            # Window 1 opens first and restores the seeded strip.
            p1 = ctx.new_page()
            p1.on("pageerror", lambda e: errs.append("p1:" + str(e)))
            p1.goto(vc.BASE, wait_until="networkidle")
            for _ in range(30):
                if p1.evaluate("() => openTabs.length") > 0:
                    break
                time.sleep(0.2)
            res["primary_restores_seed"] = p1.evaluate("(id) => openTabs.includes(id)", alpha)

            # Window 2 opens second and should start CLEAN.
            p2 = ctx.new_page()
            p2.on("pageerror", lambda e: errs.append("p2:" + str(e)))
            p2.goto(vc.BASE, wait_until="networkidle")
            time.sleep(2.0)
            res["secondary_opens_clean"] = p2.evaluate("() => openTabs.length") == 0
            res["presence_both_see_two"] = (
                p2.evaluate("() => winPeers.size") >= 2 and p1.evaluate("() => winPeers.size") >= 2
            )
            res["labels_distinct"] = {
                p1.evaluate("() => windowLabel(winId)"),
                p2.evaluate("() => windowLabel(winId)"),
            } == {1, 2}
            res["chip_shown_both"] = p1.evaluate(
                "() => !document.getElementById('winTag').hidden"
            ) and p2.evaluate("() => !document.getElementById('winTag').hidden")
            res["menu_offers_move"] = (
                p1.evaluate("(id) => windowMoveItems(session(id)).length", alpha) == 1
            )

            # Move alpha from window 1 to window 2.
            w2 = p2.evaluate("() => winId")
            p1.evaluate("([id, w]) => moveSessionToWindow(id, w)", [alpha, w2])
            time.sleep(1.0)
            res["move_source_released"] = not p1.evaluate("(id) => openTabs.includes(id)", alpha)
            res["move_target_received"] = p2.evaluate("(id) => openTabs.includes(id)", alpha)
            res["move_target_flashed"] = p2.evaluate(
                "() => document.body.classList.contains('win-flash')"
            )

            # Close window 2 — window 1 (primary) collects its tab back.
            time.sleep(0.7)  # let window 2 persist its strip
            p2.close()
            time.sleep(3.5)  # leave + collect grace
            res["collect_on_close"] = p1.evaluate("(id) => openTabs.includes(id)", alpha)

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
