#!/usr/bin/env python3
"""BYOK provider endpoints on a real panel: encrypted, redacted, scoped.

Spins its own throwaway panel (its own home, port and tmux socket — it never
touches a panel you are using) and drives the provider API the way the UI will:
create a provider, list it, test it, update it, delete it. What it proves is the
security contract, not just the happy path:

- the plaintext key is never returned by any endpoint and never appears in
  state.json on disk — only its ciphertext (``gcm1:``) does;
- providers ride no read poll: /api/state carries neither the key nor the record;
- a read-only token may list providers but not create one; the attention-scoped
  hook token is refused the whole surface;
- bad input (unknown kind, missing key, non-http URL) is a clean 4xx, and a
  test against an unreachable provider is a structured failure, not a 500.

    python3 tools/llm_api_check.py

Exit status is 0 on pass, 1 on fail.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PASSWORD = "llm-api-check"  # noqa: S105 — a throwaway panel on loopback
PORT = 3282
BASE = f"http://127.0.0.1:{PORT}"
HOME = Path("/tmp/clique-llm-api-home")
SOCKET = "clique-llm-api-check"
SECRET = "sk-PLAINTEXT-DO-NOT-LEAK-9f3a"  # noqa: S105 — the value we assert never leaks


def _mint(env, *args) -> str:
    out = subprocess.run(
        [sys.executable, "-m", "clique", "token", "create", *args],
        env=env,
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    ).stdout
    return next((ln.strip() for ln in out.splitlines() if ln.strip().startswith("mxp_")), "")


def _panel() -> tuple[subprocess.Popen, str, str]:
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
    admin = _mint(env, "admin")
    readonly = _mint(env, "watcher", "--read-only")
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
            return proc, admin, readonly
        except (urllib.error.URLError, OSError):
            time.sleep(0.25)
    proc.kill()
    raise SystemExit(f"the check's own panel never came up on {PORT}")


def req(token: str, method: str, path: str, body=None) -> tuple[int, str]:
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(
        BASE + path,
        data=data,
        method=method,
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(r, timeout=20) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def main() -> int:
    proc, admin, readonly = _panel()
    res: dict[str, object] = {}
    try:
        attn = ""
        # Creating a session writes hook.token; we need the attention token it
        # mints to prove that token is shut out of the provider surface too.
        req(admin, "POST", "/api/sessions", {"cli": "shell", "cwd": "/tmp", "name": "t"})
        for _ in range(40):
            try:
                attn = (HOME / "hook.token").read_text().strip()
            except OSError:
                attn = ""
            if attn:
                break
            time.sleep(0.25)

        # Create a provider with a real-looking secret.
        status, raw = req(
            admin,
            "POST",
            "/api/llm/providers",
            {
                "label": "Test OR",
                "kind": "openai",
                "base_url": "http://127.0.0.1:9/v1",
                "model": "test/model",
                "key": SECRET,
            },
        )
        created = json.loads(raw)
        pid = created.get("id", "")
        res["create_201"] = status == 201 and pid.startswith("lp-")
        res["create_key_set"] = created.get("key_set") is True
        res["create_hides_key"] = (
            "key" not in created and "key_enc" not in created and SECRET not in raw
        )

        # List: redacted, and still no key material.
        listed = json.loads(req(admin, "GET", "/api/llm/providers")[1])
        res["list_shows_provider"] = any(p["id"] == pid for p in listed["providers"])
        res["list_encryption_flag"] = listed.get("encryption") is True
        res["list_hides_key"] = SECRET not in json.dumps(listed) and all(
            "key_enc" not in p for p in listed["providers"]
        )

        # On disk: ciphertext, never the plaintext.
        disk = (HOME / "state.json").read_text()
        res["disk_has_ciphertext"] = "gcm1:" in disk
        res["disk_no_plaintext"] = SECRET not in disk

        # The poll never carries the key or the provider record.
        state = req(admin, "GET", "/api/state")[1]
        res["state_no_key"] = SECRET not in state and "key_enc" not in state

        # Scope: a read token lists but cannot create; attention is shut out.
        res["readonly_lists"] = req(readonly, "GET", "/api/llm/providers")[0] == 200
        res["readonly_cannot_create"] = (
            req(
                readonly,
                "POST",
                "/api/llm/providers",
                {
                    "kind": "openai",
                    "base_url": "https://x/v1",
                    "model": "m",
                    "key": "sk-x",
                },
            )[0]
            == 403
        )
        res["attn_get_401"] = req(attn, "GET", "/api/llm/providers")[0] == 401
        res["attn_post_refused"] = req(
            attn,
            "POST",
            "/api/llm/providers",
            {
                "kind": "openai",
                "base_url": "https://x/v1",
                "model": "m",
                "key": "sk-x",
            },
        )[0] in (401, 403)

        # Update: change the model, blank key keeps the stored one.
        upd = json.loads(req(admin, "POST", f"/api/llm/providers/{pid}", {"model": "test/v2"})[1])
        res["update_model"] = upd.get("model") == "test/v2"
        res["update_keeps_key"] = upd.get("key_set") is True

        # Bad input is a clean 4xx, not a 500.
        res["bad_kind_400"] = (
            req(
                admin,
                "POST",
                "/api/llm/providers",
                {"kind": "wat", "base_url": "https://x", "model": "m", "key": "k"},
            )[0]
            == 400
        )
        res["no_key_400"] = (
            req(
                admin,
                "POST",
                "/api/llm/providers",
                {"kind": "openai", "base_url": "https://x/v1", "model": "m"},
            )[0]
            == 400
        )
        res["bad_url_400"] = (
            req(
                admin,
                "POST",
                "/api/llm/providers",
                {"kind": "openai", "base_url": "ftp://x", "model": "m", "key": "k"},
            )[0]
            == 400
        )

        # Routes: point the inbox feature at this provider, and guard the edges.
        state2 = json.loads(req(admin, "GET", "/api/llm/providers")[1])
        res["routes_feature_listed"] = "inbox" in state2.get("features", [])
        setr = req(admin, "POST", "/api/llm/routes", {"feature": "inbox", "provider_id": pid})
        res["route_set"] = setr[0] == 200 and json.loads(setr[1])["routes"].get("inbox") == pid
        res["route_in_list"] = (
            json.loads(req(admin, "GET", "/api/llm/providers")[1])["routes"].get("inbox") == pid
        )
        res["route_unknown_feature_400"] = (
            req(admin, "POST", "/api/llm/routes", {"feature": "nope", "provider_id": pid})[0] == 400
        )
        res["route_unknown_provider_400"] = (
            req(admin, "POST", "/api/llm/routes", {"feature": "inbox", "provider_id": "lp-nope"})[0]
            == 400
        )
        res["route_readonly_refused"] = (
            req(readonly, "POST", "/api/llm/routes", {"feature": "inbox", "provider_id": pid})[0]
            == 403
        )

        # Test an unreachable provider → structured failure, HTTP 200.
        tstatus, traw = req(admin, "POST", f"/api/llm/providers/{pid}/test")
        tbody = json.loads(traw)
        res["test_structured_failure"] = (
            tstatus == 200 and tbody.get("ok") is False and bool(tbody.get("error"))
        )

        # Delete, then it is gone and un-testable.
        res["delete_ok"] = (
            json.loads(req(admin, "POST", f"/api/llm/providers/{pid}/delete")[1]).get("ok") is True
        )
        res["gone_after_delete"] = not any(
            p["id"] == pid
            for p in json.loads(req(admin, "GET", "/api/llm/providers")[1])["providers"]
        )
        res["test_missing_404"] = req(admin, "POST", f"/api/llm/providers/{pid}/test")[0] == 404
        # Deleting the provider drops the route that pointed at it — no dangle.
        res["route_dropped_on_delete"] = "inbox" not in json.loads(
            req(admin, "GET", "/api/llm/providers")[1]
        ).get("routes", {})
    finally:
        proc.terminate()
        subprocess.run(
            ["tmux", "-L", SOCKET, "kill-server"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        shutil.rmtree(HOME, ignore_errors=True)

    ok = all(res.values())
    for key, value in res.items():
        print(f"  {'ok  ' if value else 'FAIL'} {key}: {value}")
    print("llm_api_check:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
