#!/usr/bin/env python3
"""The Models settings tab in a real browser: add a provider, and no key leaks.

Headless DOM checks have shipped on visually-broken layouts here before, so the
BYOK UI gets a real eyeball: open Settings → Models, add a provider through the
form, and confirm it lands in the list, that its key field is a password input,
and — the point — that the plaintext key never appears anywhere in the settings
DOM after it is submitted. Then delete it and confirm it's gone.

Needs the Playwright venv (with the crypto extra, so the panel can encrypt):

    ~/.cache/clique-visual/bin/python tools/llm_ui_check.py

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

SECRET = "sk-UI-PLAINTEXT-DO-NOT-LEAK-4b7e"  # noqa: S105 — asserted absent from the DOM


def main() -> int:
    from playwright.sync_api import sync_playwright

    panel = vc._own_panel()
    res: dict[str, object] = {}
    try:
        auth = Auth(vc.PASSWORD, vc.SANDBOX / "secret")
        shot = Path("/tmp/clique-visual")
        shot.mkdir(parents=True, exist_ok=True)
        with sync_playwright() as play:
            browser = play.chromium.launch()
            ctx = browser.new_context(viewport={"width": 1200, "height": 820})
            ctx.add_init_script("try { localStorage.setItem('clique.gpu','0'); } catch (e) {}")
            ctx.add_cookies(
                [{"name": COOKIE_NAME, "value": auth.issue(), "domain": "127.0.0.1", "path": "/"}]
            )
            page = ctx.new_page()
            errs: list[str] = []
            page.on("pageerror", lambda e: errs.append(str(e)))
            page.on("dialog", lambda d: d.accept())  # auto-accept the delete confirm
            page.goto(vc.BASE, wait_until="networkidle")

            # Open Settings → Models.
            page.evaluate("() => openSettings()")
            page.click('#setTabs button[data-pane="models"]')
            page.wait_for_selector("#llmForm", state="visible")
            res["crypto_available"] = page.evaluate(
                "() => document.getElementById('llmCryptoWarn').hidden"
            )
            res["key_field_is_password"] = page.evaluate(
                "() => document.querySelector('#llmForm input[name=key]').type === 'password'"
            )

            # Add a provider through the form.
            page.fill('#llmForm input[name="label"]', "UI OpenRouter")
            page.fill('#llmForm input[name="base_url"]', "http://127.0.0.1:9/v1")
            page.fill('#llmForm input[name="model"]', "test/ui-model")
            page.fill('#llmForm input[name="key"]', SECRET)
            page.click('#llmForm button[type="submit"]')
            page.wait_for_selector("#llmProviders .llm-row", timeout=5000)

            row = page.locator("#llmProviders .llm-row").first
            res["row_shows_label"] = "UI OpenRouter" in (row.inner_text() or "")
            res["row_shows_model"] = "test/ui-model" in (row.inner_text() or "")

            # The whole point: the plaintext key is nowhere in the settings DOM.
            settings_html = page.evaluate("() => document.getElementById('settings').innerHTML")
            res["key_absent_from_dom"] = SECRET not in settings_html
            res["form_cleared"] = page.evaluate(
                "() => document.querySelector('#llmForm input[name=key]').value === ''"
            )

            # The auto-test resolves to a status (fake endpoint → a failure, which
            # is fine; what matters is the flow reports something, not nothing).
            for _ in range(20):
                txt = page.locator("#llmProviders .llm-row .llm-status").first.inner_text()
                if txt and "testing" not in txt:
                    break
                time.sleep(0.25)
            res["test_reports_status"] = bool(
                page.locator("#llmProviders .llm-row .llm-status").first.inner_text().strip()
            )

            page.screenshot(path=str(shot / "llm.png"))

            # Delete it (dialog auto-accepted) and confirm it's gone.
            row.locator("button.danger").click()
            for _ in range(20):
                if page.locator("#llmProviders .llm-row").count() == 0:
                    break
                time.sleep(0.25)
            res["deleted_row_gone"] = page.locator("#llmProviders .llm-row").count() == 0

            res["no_console_errors"] = not errs
            if errs:
                res["errors"] = errs[:4]
    finally:
        panel.terminate()

    ok = all(v for k, v in res.items() if k != "errors")
    for key, value in res.items():
        print(f"  {'ok  ' if value or key == 'errors' else 'FAIL'} {key}: {value}")
    print("llm_ui_check:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
