#!/usr/bin/env python3
"""Verify the Claude Code state-hook wiring, end to end, without a real CLI.

Feature: a hook-speaking CLI is launched with a ``--settings`` block whose
Notification / Stop / UserPromptSubmit hooks POST the session's real state to
the attention endpoint, so the sidebar knows "waiting on you" for certain
instead of guessing from output. This checks the three joints:

1. The settings block is well-formed and only claude (not shell) opts in.
2. A launched claude session's pane really carries ``--settings`` + the
   ``CLIQUE_*`` env the reporter needs.
3. Running the reporter with that env drives the session's state to ``waiting``
   and back to idle on ``clear`` — the authoritative signal outranking the
   activity guess.

Its own home, port and tmux socket; nothing touches a panel you are using.
Real Claude accepting the settings and firing the hooks is verified separately
(it needs the CLI and a token); this covers everything up to that.

    python3 tools/hooks_check.py

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
sys.path.insert(0, str(ROOT))
from clique.app import HOOK, _hooks_settings
from clique.registry import Registry

PASSWORD = "hooks-check"  # noqa: S105 — a throwaway panel on loopback
PORT = 3286
BASE = f"http://127.0.0.1:{PORT}"
HOME = Path("/tmp/clique-hooks-check-home")
SOCKET = "clique-hooks-check"


def _run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def main() -> int:
    # 1. Settings block + registry opt-in (in-process, no launch).
    cfg = json.loads(_hooks_settings(sys.executable))["hooks"]
    settings_ok = (
        {"Notification", "Stop", "UserPromptSubmit"} <= set(cfg)
        # Stop is a turn boundary, not a request — it clears rather than nagging.
        and "clear" in cfg["Stop"][0]["hooks"][0]["command"]
        and "waiting" not in cfg["Stop"][0]["hooks"][0]["command"]
        # A genuine wait still reports waiting, via the idle/permission Notification.
        and "waiting" in cfg["Notification"][1]["hooks"][0]["command"]
        and "clear" in cfg["UserPromptSubmit"][0]["hooks"][0]["command"]
        and str(HOOK) in cfg["Notification"][0]["hooks"][0]["command"]
    )
    reg = Registry(ROOT / "clique" / "config" / "clis.toml").types()
    optin_ok = reg["claude"].hooks is True and reg["shell"].hooks is False
    print(f"  {'ok  ' if settings_ok else 'FAIL'} settings block well-formed")
    print(f"  {'ok  ' if optin_ok else 'FAIL'} claude opts in, shell does not")

    # 2 + 3. Real panel: launch injection, then the reporter drives state.
    shutil.rmtree(HOME, ignore_errors=True)
    HOME.mkdir(parents=True)
    env = dict(os.environ, CLIQUE_HOME=str(HOME), CLIQUE_TMUX_SOCKET=SOCKET)
    _run(
        [sys.executable, "-m", "clique", "password"],
        input=f"{PASSWORD}\n{PASSWORD}\n",
        env=env,
        cwd=str(ROOT),
    )
    mint = _run(
        [sys.executable, "-m", "clique", "token", "create", "check"], env=env, cwd=str(ROOT)
    ).stdout
    apitok = next((ln.strip() for ln in mint.splitlines() if ln.strip().startswith("mxp_")), "")
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

    def api(path, method="GET", body=None):
        req = urllib.request.Request(
            BASE + path,
            data=(json.dumps(body).encode() if body is not None else None),
            method=method,
            headers={"Authorization": "Bearer " + apitok, "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read() or "{}")

    launch_ok = report_ok = False
    try:
        for _ in range(80):
            try:
                urllib.request.urlopen(BASE + "/healthz", timeout=2).read()
                break
            except (urllib.error.URLError, OSError):
                time.sleep(0.25)

        cid = api("/api/sessions", "POST", {"cli": "claude", "cwd": "/tmp", "name": "hooked"})["id"]
        time.sleep(1.0)
        mux = "sm-" + cid.replace("-", "")[:8]
        start_cmd = _run(
            ["tmux", "-L", SOCKET, "list-panes", "-a", "-F", "#{pane_start_command}"]
        ).stdout
        pane_env = _run(["tmux", "-L", SOCKET, "show-environment", "-t", mux]).stdout
        launch_ok = (
            "--settings" in start_cmd
            and "hook.py" in start_cmd
            and "CLIQUE_URL=" in pane_env
            and "CLIQUE_TOKEN=" in pane_env
            and f"CLIQUE_SESSION={cid}" in pane_env
        )
        print(f"  {'ok  ' if launch_ok else 'FAIL'} claude pane gets --settings + CLIQUE_* env")

        # A shell session (not busy long) + the reporter with its pane env.
        sid = api("/api/sessions", "POST", {"cli": "shell", "cwd": "/tmp", "name": "report"})["id"]
        time.sleep(1.0)
        # The reporter authenticates with this session's OWN token, read from its
        # pane environment (there is no shared hook.token any more).
        smux = "sm-" + sid.replace("-", "")[:8]
        senv = _run(["tmux", "-L", SOCKET, "show-environment", "-t", smux, "CLIQUE_TOKEN"]).stdout
        htok = senv.split("=", 1)[1].strip() if "=" in senv else ""
        henv = dict(os.environ, CLIQUE_SESSION=sid, CLIQUE_URL=BASE, CLIQUE_TOKEN=htok)

        def state():
            return api(f"/api/sessions/{sid}/wait?for=waiting,idle,error,working&timeout=1")[
                "state"
            ]

        _run([sys.executable, str(HOOK), "waiting"], input="{}", env=henv)
        time.sleep(0.3)
        after_wait = state()
        _run([sys.executable, str(HOOK), "clear"], input="{}", env=henv)
        time.sleep(0.3)
        after_clear = state()
        report_ok = after_wait == "waiting" and after_clear in ("idle", "working")
        print(
            f"  {'ok  ' if report_ok else 'FAIL'} reporter drives state: "
            f"waiting->{after_wait}, clear->{after_clear}"
        )
    finally:
        proc.terminate()
        _run(["tmux", "-L", SOCKET, "kill-server"])

    ok = settings_ok and optin_ok and launch_ok and report_ok
    print("hooks_check:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
