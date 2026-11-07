#!/usr/bin/env python3
"""Verify that a dead resume key cannot leave a tab that does nothing.

A resume key outlives the conversation it points at. Open a session, never
type in it, let the idle reaper stop it, and the CLI has nothing on disk to
come back to: the launch exits inside two seconds and the tab goes quiet with
no pane, no error and nothing to click. One of Justin's sessions had been
doing exactly that for a week when this was found on 2026-09-01.

Three things, against a real panel with a fake CLI that refuses every resume:

1. Starting a stopped session uses the resume form, and says so.
2. When that form dies, the panel notices, drops the key, and starts the
   session clean by itself -- no second click.
3. A session stopped inside the grace window stays stopped. The recovery
   must never resurrect something a person just shut down.

Its own home, port and tmux socket; nothing touches a panel you are using.

    python3 tools/resume_check.py

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
PASSWORD = "resume-check"  # noqa: S105 — a throwaway panel on loopback
PORT = 3287
BASE = f"http://127.0.0.1:{PORT}"
HOME = Path("/tmp/clique-resume-check-home")
SOCKET = "clique-resume-check"

#: The fake CLI's resume takes REFUSE_AFTER seconds to give up, so a stop
#: issued before then is unambiguously a person stopping a session that was
#: still starting. SETTLE is long enough to cover the refusal, the panel
#: noticing, and the clean start that follows.
REFUSE_AFTER = 3
SETTLE = 8.0

# A CLI whose resume always refuses, the way Claude Code refuses a session id
# with no conversation behind it. `args` carries {id}, which is what makes the
# panel treat our own id as the resume key on the next start -- the exact
# promotion this check is about.
CATALOGUE = """
[cli.faker]
label   = "Faker"
command = "faker"
args    = ["fresh", "{id}"]
resume  = ["resume", "{cli_session_id}"]
color   = "#8b8b8b"

[cli.shell]
label   = "Shell"
command = "bash"
args    = ["-l"]
color   = "#8b8b8b"
"""

FAKER = """#!/bin/bash
if [ "$1" = "resume" ]; then
  sleep REFUSE_AFTER
  echo "No conversation found with session ID: $2"
  exit 1
fi
echo "FRESH START $2"
exec sleep 600
"""


def _panel() -> tuple[subprocess.Popen, str]:
    shutil.rmtree(HOME, ignore_errors=True)
    HOME.mkdir(parents=True)
    (HOME / "clis.toml").write_text(CATALOGUE)
    faker = HOME / "faker"
    faker.write_text(FAKER.replace("REFUSE_AFTER", str(REFUSE_AFTER)))
    faker.chmod(0o755)
    env = dict(
        os.environ,
        CLIQUE_HOME=str(HOME),
        CLIQUE_TMUX_SOCKET=SOCKET,
        PATH=f"{HOME}:{os.environ.get('PATH', '')}",
    )
    subprocess.run(
        [sys.executable, "-m", "clique", "password"],
        input=f"{PASSWORD}\n{PASSWORD}\n",
        text=True,
        env=env,
        cwd=str(ROOT),
        capture_output=True,
    )
    mint = subprocess.run(
        [sys.executable, "-m", "clique", "token", "create", "admin"],
        env=env,
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    ).stdout
    token = next((ln.strip() for ln in mint.splitlines() if ln.strip().startswith("mxp_")), "")
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
            return proc, token
        except (urllib.error.URLError, OSError):
            time.sleep(0.25)
    proc.kill()
    raise SystemExit(f"the check's own panel never came up on {PORT}")


def main() -> int:
    proc, token = _panel()

    def api(path, method="GET", body=None):
        req = urllib.request.Request(
            BASE + path,
            data=(json.dumps(body).encode() if body is not None else None),
            method=method,
            headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                return json.loads(r.read() or "{}")
        except urllib.error.HTTPError as err:
            raise SystemExit(f"{method} {path} -> {err.code} {err.read()[:200]!r}") from err

    def mux(sid):
        return "sm-" + sid.replace("-", "")[:8]

    def alive(sid):
        return (
            subprocess.run(
                ["tmux", "-L", SOCKET, "has-session", "-t", "=" + mux(sid)],
                capture_output=True,
            ).returncode
            == 0
        )

    def pane(sid):
        return subprocess.run(
            # No "=" prefix here: exact-match session syntax resolves for
            # has-session but hands capture-pane a target it answers with
            # nothing at all, silently.
            ["tmux", "-L", SOCKET, "capture-pane", "-p", "-t", mux(sid)],
            capture_output=True,
            text=True,
        ).stdout

    def record(sid):
        # Straight off disk: /api/state is the sidebar's view and deliberately
        # does not carry the resume key, which is the field under test.
        rows = json.loads((HOME / "state.json").read_text())["sessions"]
        return next(r for r in rows if r["id"] == sid)

    res: dict[str, object] = {}
    try:
        a = api("/api/sessions", "POST", {"cli": "faker", "cwd": "/tmp", "name": "recovers"})["id"]
        time.sleep(1.5)
        res["first_launch_is_fresh"] = "FRESH START" in pane(a)

        api(f"/api/sessions/{a}/kill", "POST")
        started = api(f"/api/sessions/{a}/start", "POST")
        res["start_reports_a_resume"] = started.get("resumed") is True

        time.sleep(SETTLE)
        res["recovered_by_itself"] = alive(a)
        res["recovered_pane_is_fresh"] = "FRESH START" in pane(a)
        res["dead_key_was_dropped"] = not record(a).get("cli_session_id")

        # And the other direction: stopping inside the grace window has to win.
        b = api("/api/sessions", "POST", {"cli": "faker", "cwd": "/tmp", "name": "stays down"})[
            "id"
        ]
        time.sleep(1.5)
        api(f"/api/sessions/{b}/kill", "POST")
        api(f"/api/sessions/{b}/start", "POST")
        api(f"/api/sessions/{b}/kill", "POST")  # while the resume is still trying
        time.sleep(SETTLE)
        res["a_stopped_session_is_left_alone"] = not alive(b)
    finally:
        proc.terminate()
        subprocess.run(
            ["tmux", "-L", SOCKET, "kill-server"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    ok = all(bool(v) for v in res.values())
    for key, value in res.items():
        print(f"  {'ok  ' if value else 'FAIL'} {key}: {value}")
    print("resume_check:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
