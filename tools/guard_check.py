#!/usr/bin/env python3
"""The resource guard: does it warn at the right time, and never block?

The guard is a soft read on whether the box is stretched for the number of
live sessions. These assertions feed ``sysinfo.guard`` synthetic mem / swap /
load so every signal — the RAM floor, swap climbing, load past the cores, and
the session count past a derived soft ceiling — is exercised without needing an
actually-overloaded box. The ceiling is measured from real per-session memory,
so that is checked too.

Then, if the Playwright venv is present, it renders the banner in a real page
and screenshots it — a green DOM check has passed on a visually-broken layout
here before, so the guard's one net-new strip gets a real eyeball.

    python3 tools/guard_check.py                            # logic only
    ~/.cache/clique-visual/bin/python tools/guard_check.py  # + screenshot

Exit status is 0 on pass, 1 on fail.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from clique import sysinfo


def _mem(total_mb: int, used_mb: int) -> dict:
    pct = round(100.0 * used_mb / total_mb, 1) if total_mb else 0.0
    return {"total_mb": total_mb, "used_mb": used_mb, "percent": pct}


def _load(one: float, cores: int) -> dict:
    return {
        "one": one,
        "five": one,
        "fifteen": one,
        "cores": cores,
        "ratio": round(one / cores, 2) if cores else 0.0,
    }


def _swap(used_mb: int) -> dict:
    return {"used_mb": used_mb, "total_mb": 4096, "percent": 0.0}


def _calm_swap() -> None:
    """Force the swap signal quiet, so a test isolates the signal it means to.

    ``_swap_growing`` re-baselines against wall-clock; the tests that are not
    about swap monkeypatch it off, and the one that is, forces it on.
    """
    sysinfo._swap_growing = lambda used: False  # type: ignore[assignment]


def logic() -> dict:
    res: dict[str, object] = {}
    _calm_swap()

    # A roomy box with a few sessions says nothing at all.
    big = sysinfo.guard(
        3,
        [600 * 1024] * 3,
        mem=_mem(32768, 8000),
        swap_info=_swap(0),
        load_info=_load(1.0, 8),
    )
    res["calm_box_ok"] = big["level"] == "ok" and big["headline"] == ""
    res["calm_ceiling_roomy"] = big["ceiling"] > 20

    # Ceiling is measured from real session memory, not a fixed guess.
    res["avg_measured"] = (
        850
        <= sysinfo.guard(
            2,
            [800 * 1024, 1000 * 1024],
            mem=_mem(32768, 8000),
            swap_info=_swap(0),
            load_info=_load(0.5, 8),
        )["avg_session_mb"]
        <= 950
    )
    res["avg_default_when_empty"] = (
        sysinfo.guard(
            0,
            [],
            mem=_mem(32768, 8000),
            swap_info=_swap(0),
            load_info=_load(0.1, 8),
        )["avg_session_mb"]
        == sysinfo._DEFAULT_SESSION_MB
    )

    # Session count past the soft ceiling → a strong warning.
    tight = sysinfo.guard(
        8,
        [600 * 1024] * 8,
        mem=_mem(4096, 1500),
        swap_info=_swap(0),
        load_info=_load(1.0, 4),
    )
    res["over_ceiling_high"] = tight["level"] == "high"
    res["over_ceiling_reason"] = any("past a comfortable" in r for r in tight["reasons"])
    res["over_ceiling_headline"] = bool(tight["headline"])

    # Sitting right at the ceiling → a soft heads-up, not the strong one.
    at = sysinfo.guard(
        tight["ceiling"],
        [600 * 1024] * tight["ceiling"],
        mem=_mem(4096, 1200),
        swap_info=_swap(0),
        load_info=_load(1.0, 4),
    )
    res["at_ceiling_watch"] = at["level"] == "watch"

    # Available RAM under the floor → high, whatever the session count.
    low = sysinfo.guard(
        2,
        [600 * 1024] * 2,
        mem=_mem(8192, 7800),
        swap_info=_swap(0),
        load_info=_load(0.5, 8),
    )
    res["ram_floor_high"] = low["level"] == "high"
    res["ram_floor_reason"] = any("RAM free" in r for r in low["reasons"])

    # Load sustained over the cores → high at 2x, watch at 1x.
    res["load_2x_high"] = (
        sysinfo.guard(
            1,
            [600 * 1024],
            mem=_mem(32768, 8000),
            swap_info=_swap(0),
            load_info=_load(9.0, 4),
        )["level"]
        == "high"
    )
    res["load_1x_watch"] = (
        sysinfo.guard(
            1,
            [600 * 1024],
            mem=_mem(32768, 8000),
            swap_info=_swap(0),
            load_info=_load(5.0, 4),
        )["level"]
        == "watch"
    )

    # Swap actively climbing → high, even with RAM and load calm.
    sysinfo._swap_growing = lambda used: True  # type: ignore[assignment]
    swp = sysinfo.guard(
        1,
        [600 * 1024],
        mem=_mem(32768, 8000),
        swap_info=_swap(300),
        load_info=_load(0.5, 8),
    )
    res["swap_growing_high"] = swp["level"] == "high"
    res["swap_growing_reason"] = any("swap is climbing" in r for r in swp["reasons"])
    _calm_swap()

    # The whole contract: it advises, it never blocks. No allow/deny key, and
    # the ceiling is always at least one so the form never reads "runs ~0".
    tiny = sysinfo.guard(
        1,
        [],
        mem=_mem(512, 400),
        swap_info=_swap(0),
        load_info=_load(0.1, 1),
    )
    res["ceiling_never_zero"] = tiny["ceiling"] >= 1
    res["no_block_key"] = not ({"block", "deny", "allowed", "blocked"} & set(big))
    res["levels_are_advisory"] = all(
        g["level"] in ("ok", "watch", "high") for g in (big, tight, at, low, swp, tiny)
    )

    # The reap window makes it into the sentence, so the lever is named.
    reaped = sysinfo.guard(
        8,
        [600 * 1024] * 8,
        mem=_mem(4096, 1500),
        swap_info=_swap(0),
        load_info=_load(1.0, 4),
        reap_hours=6,
    )
    res["headline_names_reap"] = "auto-reap in 6h" in reaped["headline"]
    return res


def visual() -> dict:
    """Render the banner in a real page and screenshot it. Skipped without the
    Playwright venv; the logic above is what always runs."""
    try:
        from playwright.sync_api import sync_playwright
    except ModuleNotFoundError:
        return {}

    import tools.visual_check as vc
    from clique.auth import COOKIE_NAME, Auth

    res: dict[str, object] = {}
    panel = vc._own_panel()
    try:
        auth = Auth(vc.PASSWORD, vc.SANDBOX / "secret")
        shot = Path("/tmp/clique-visual")  # where the other visual checks land
        shot.mkdir(parents=True, exist_ok=True)
        with sync_playwright() as play:
            browser = play.chromium.launch()
            ctx = browser.new_context(viewport={"width": 1200, "height": 760})
            ctx.add_init_script("try { localStorage.setItem('clique.gpu','0'); } catch (e) {}")
            ctx.add_cookies(
                [{"name": COOKIE_NAME, "value": auth.issue(), "domain": "127.0.0.1", "path": "/"}]
            )
            page = ctx.new_page()
            errs: list[str] = []
            page.on("pageerror", lambda e: errs.append(str(e)))
            page.goto(vc.BASE, wait_until="networkidle")

            # A calm box draws nothing.
            page.evaluate(
                "() => { state.stats = state.stats || {}; "
                "state.stats.guard = {level:'ok', headline:''}; renderGuard(); }"
            )
            res["hidden_when_ok"] = page.evaluate("() => document.getElementById('guard').hidden")

            # A stretched box draws the strip, on screen, with the sentence.
            page.evaluate(
                "() => { state.stats.guard = {level:'high', sessions:12, ceiling:8, "
                "free_mb:1228, reasons:['12 sessions, past a comfortable ~8 for this box'], "
                "headline:'12 sessions, ~1.2 GB free — heavy for this box. "
                "Idle ones auto-reap in 6h.'}; renderGuard(); }"
            )
            g = page.locator("#guard")
            res["shown_when_high"] = not page.evaluate(
                "() => document.getElementById('guard').hidden"
            )
            box = g.bounding_box() or {}
            res["on_screen"] = bool(box) and box.get("y", -1) >= 0 and box.get("width", 0) > 200
            res["carries_headline"] = "heavy for this box" in (g.inner_text() or "")
            res["level_styled"] = page.evaluate(
                "() => document.getElementById('guard').dataset.level === 'high'"
            )
            page.screenshot(path=str(shot / "guard.png"))

            # Dismiss hides it; the same situation stays gone.
            page.evaluate("() => document.querySelector('#guard .guard-x').click()")
            res["dismiss_hides"] = page.evaluate(
                "() => { renderGuard(); return document.getElementById('guard').hidden; }"
            )
            res["no_console_errors"] = not errs
            if errs:
                res["errors"] = errs[:4]
    finally:
        panel.terminate()
    return res


def main() -> int:
    res = logic()
    res.update(visual())
    ok = all(v for k, v in res.items() if k != "errors")
    for key, value in res.items():
        print(f"  {'ok  ' if value or key == 'errors' else 'FAIL'} {key}: {value}")
    print("guard_check:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
