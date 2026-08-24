#!/usr/bin/env python3
"""Verify the token-scope boundary on a real panel — the read surface is scoped.

The ``attention`` token is handed to every session's environment so a state
hook can nudge its own status. It must reach POST /attention and nothing else.
Before this was enforced, the read surface (GET /api/*, the /ws attach) checked
only "is there a valid token", so that attention token could read every
session's terminal, transcripts, prompts and arbitrary host files. This proves
the gate:

- attention token: refused on GET /api/state, /file, /transcript and /ws (401);
  still accepted on POST /attention (200); still refused write (create → 403).
- read-only token: reads /api/state (200), passes the /ws auth gate (400, no
  handshake), but is refused a write (403).
- file reads are fenced to the session directory by default: an absolute host
  path reads back "missing" even for a read token; an in-directory path reads.
- webhook_url is withheld from an API token (blanked + *_set), so a read token
  cannot lift a Discord/Slack URL that is itself a credential.

Its own home, port and tmux socket; nothing touches a panel you are using.

    python3 tools/scope_check.py

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
PASSWORD = "scope-check"  # noqa: S105 — a throwaway panel on loopback
PORT = 3281
BASE = f"http://127.0.0.1:{PORT}"
HOME = Path("/tmp/clique-scope-check-home")
SOCKET = "clique-scope-check"
NOTE = Path("/tmp/clique-scope-note.txt")


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
        with urllib.request.urlopen(r, timeout=15) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def _pane_token(sid: str) -> str:
    """The per-session attention token, read from the pane's own environment.

    It is no longer written to a shared file: each session gets its own, handed
    to its pane via `tmux new-session -e`, so this is where a hook would find it.
    """
    mux = "sm-" + sid.replace("-", "")[:8]
    for _ in range(40):
        out = subprocess.run(
            ["tmux", "-L", SOCKET, "show-environment", "-t", mux, "CLIQUE_TOKEN"],
            capture_output=True,
            text=True,
        ).stdout.strip()
        if out.startswith("CLIQUE_TOKEN="):
            val = out.split("=", 1)[1].strip()
            if val:
                return val
        time.sleep(0.25)
    return ""


def main() -> int:
    proc, admin, readonly = _panel()
    NOTE.write_text("hi\n", encoding="utf-8")
    res: dict[str, object] = {}
    try:
        sid = json.loads(
            req(admin, "POST", "/api/sessions", {"cli": "shell", "cwd": "/tmp", "name": "target"})[
                1
            ]
        )["id"]
        # Each session carries its own attention token in its pane environment.
        attn = _pane_token(sid)
        # A second session, to prove one session's token cannot nudge another.
        sid2 = json.loads(
            req(admin, "POST", "/api/sessions", {"cli": "shell", "cwd": "/tmp", "name": "other"})[1]
        )["id"]
        req(admin, "PATCH", "/api/settings", {"webhook_url": "https://hooks.example.com/T/abc123"})
        time.sleep(0.3)

        # Operator tokens read; a read-only token cannot write.
        res["admin_reads_state"] = req(admin, "GET", "/api/state")[0] == 200
        res["readonly_reads_state"] = req(readonly, "GET", "/api/state")[0] == 200
        res["readonly_write_refused"] = (
            req(readonly, "POST", f"/api/sessions/{sid}/send", {"text": "x"})[0] == 403
        )

        # The attention token is shut out of the whole read surface.
        res["attn_state_401"] = req(attn, "GET", "/api/state")[0] == 401
        res["attn_file_401"] = (
            req(attn, "GET", f"/api/sessions/{sid}/file?path=/etc/hostname")[0] == 401
        )
        res["attn_transcript_401"] = req(attn, "GET", f"/api/sessions/{sid}/transcript")[0] == 401
        # /ws checks scope before the handshake: attention is blocked (401),
        # a read token passes the gate and only then fails for lack of a
        # WebSocket key (400) — proof it may attach.
        res["attn_ws_401"] = req(attn, "GET", f"/ws?id={sid}")[0] == 401
        res["readonly_ws_passes_auth"] = req(readonly, "GET", f"/ws?id={sid}")[0] == 400

        # ...but it still does its one job for its OWN session, and cannot write.
        res["attn_attention_ok"] = (
            req(attn, "POST", f"/api/sessions/{sid}/attention", {"state": "waiting"})[0] == 200
        )
        res["attn_create_refused"] = (
            req(attn, "POST", "/api/sessions", {"cli": "shell", "cwd": "/tmp", "name": "x"})[0]
            == 403
        )
        # A session-bound token is refused when aimed at a *different* session:
        # a token exfiltrated from one pane cannot spoof another's state.
        res["attn_bound_to_own_session"] = bool(attn) and (
            req(attn, "POST", f"/api/sessions/{sid2}/attention", {"state": "error"})[0]
            in (401, 403)
        )

        # webhook_url is withheld from a token; only whether one is set.
        settings = json.loads(req(admin, "GET", "/api/state")[1])["settings"]
        res["url_withheld_from_token"] = (
            settings.get("webhook_url") == "" and settings.get("webhook_url_set") is True
        )

        # Reads are fenced to the session directory by default.
        absfile = json.loads(
            req(readonly, "GET", f"/api/sessions/{sid}/file?path=/etc/hostname")[1]
        )
        res["fence_blocks_absolute"] = absfile.get("kind") == "missing"
        incwd = json.loads(req(readonly, "GET", f"/api/sessions/{sid}/file?path={NOTE.name}")[1])
        res["fence_allows_in_dir"] = incwd.get("kind") == "text" and incwd.get("text") == "hi\n"
    finally:
        proc.terminate()
        subprocess.run(
            ["tmux", "-L", SOCKET, "kill-server"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        shutil.rmtree(HOME, ignore_errors=True)
        NOTE.unlink(missing_ok=True)

    ok = all(res.values())
    for key, value in res.items():
        print(f"  {'ok  ' if value else 'FAIL'} {key}: {value}")
    print("scope_check:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
