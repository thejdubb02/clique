#!/usr/bin/env python3
"""Every modal sheet is a real centered overlay — not an unstyled block in flow.

inbox, diff, board and broadcast are dialogs that open over the app. They each
need the overlay rule (`position: fixed`, a scrim, centred) that #settings and
#keys have. They once shipped without it and rendered as full-width unstyled
strips at the foot of the page — the board fell entirely below the fold — yet
the per-feature Playwright checks passed, because Playwright drives an element
by DOM ref no matter where it renders. This asserts the CSS itself, so that
class of "the button does nothing / breaks the UI" bug cannot come back unseen.

Needs the Playwright venv:

    ~/.cache/clique-visual/bin/python tools/overlays_check.py

Exit status is 0 on pass, 1 on fail.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import tools.visual_check as vc
from clique.auth import COOKIE_NAME, Auth

SHEETS = ("inbox", "diff", "board", "broadcast")


def main() -> int:
    from playwright.sync_api import sync_playwright

    panel = vc._own_panel()
    res: dict[str, object] = {}
    try:
        auth = Auth(vc.PASSWORD, vc.SANDBOX / "secret")
        vc._scratch_session()
        with sync_playwright() as play:
            browser = play.chromium.launch()
            ctx = browser.new_context(viewport={"width": 1400, "height": 900})
            ctx.add_init_script("try { localStorage.setItem('clique.gpu','0'); } catch (e) {}")
            ctx.add_cookies(
                [{"name": COOKIE_NAME, "value": auth.issue(), "domain": "127.0.0.1", "path": "/"}]
            )
            page = ctx.new_page()
            page.goto(vc.BASE, wait_until="networkidle")
            page.wait_for_timeout(800)
            probe = page.evaluate(
                """() => {
                  const out = {};
                  for (const id of %s) {
                    const el = document.getElementById(id);
                    if (!el) { out[id] = { missing: true }; continue; }
                    el.hidden = false;
                    const cs = getComputedStyle(el);
                    const sheet = el.querySelector('.sheet');
                    const sr = sheet ? sheet.getBoundingClientRect() : null;
                    out[id] = {
                      position: cs.position,
                      scrim: cs.backgroundColor,
                      centered: sr ? (sr.top > 0 && sr.bottom < window.innerHeight + 1) : false,
                    };
                    el.hidden = true;
                  }
                  return out;
                }"""
                % ("['" + "','".join(SHEETS) + "']")
            )
    finally:
        panel.terminate()

    for sid in SHEETS:
        info = probe.get(sid, {"missing": True})
        ok = (
            not info.get("missing")
            and info.get("position") == "fixed"
            and info.get("scrim") not in ("rgba(0, 0, 0, 0)", "transparent")
            and info.get("centered")
        )
        res[sid] = ok
        print(
            f"  {'ok  ' if ok else 'FAIL'} #{sid} is a fixed, scrimmed, on-screen overlay: {info}"
        )

    passed = all(res.values())
    print("overlays_check:", "PASS" if passed else "FAIL")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
