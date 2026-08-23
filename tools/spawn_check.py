#!/usr/bin/env python3
"""Verify fleet-spawn — several sessions from one request — against a real panel.

1. Plain: spawn count=3 → three sessions, numbered names.
2. Worktree: spawn count=2 with a worktree in a git repo → two sessions, each on
   its own branch (no collision).

Its own home, port and tmux socket; nothing touches a panel you are using.

    python3 tools/spawn_check.py

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
PASSWORD = "spawn-check"  # noqa: S105 — a throwaway panel on loopback
PORT = 3279
BASE = f"http://127.0.0.1:{PORT}"
HOME = Path("/tmp/clique-spawn-check-home")
SOCKET = "clique-spawn-check"
REPO = Path("/tmp/clique-spawn-check-repo")


def _repo() -> None:
    shutil.rmtree(REPO, ignore_errors=True)
    REPO.mkdir(parents=True)
    for args in (
        ["init", "-q", "-b", "main"],
        ["config", "user.email", "a@a"],
        ["config", "user.name", "a"],
    ):
        subprocess.run(["git", "-C", str(REPO), *args], capture_output=True)
    (REPO / "f.txt").write_text("x\n")
    subprocess.run(["git", "-C", str(REPO), "add", "-A"], capture_output=True)
    subprocess.run(["git", "-C", str(REPO), "commit", "-q", "-m", "init"], capture_output=True)


def _panel() -> tuple[subprocess.Popen, str]:
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
    _repo()
    proc, token = _panel()

    def api(path, method="GET", body=None):
        req = urllib.request.Request(
            BASE + path,
            data=(json.dumps(body).encode() if body is not None else None),
            method=method,
            headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read() or "{}")

    res: dict[str, object] = {}
    try:
        plain = api(
            "/api/sessions/spawn",
            "POST",
            {"cli": "shell", "cwd": "/tmp", "name": "fleet", "count": 3},
        )
        res["plain_created"] = len(plain.get("created") or [])
        res["plain_no_errors"] = not plain.get("errors")
        time.sleep(1.0)
        names = {s["name"] for s in api("/api/state")["sessions"]}
        res["numbered_names"] = {"fleet 1", "fleet 2", "fleet 3"} <= names

        wt = api(
            "/api/sessions/spawn",
            "POST",
            {
                "cli": "shell",
                "cwd": str(REPO),
                "name": "agent",
                "count": 2,
                "worktree": True,
                "branch": "feat",
            },
        )
        res["wt_created"] = len(wt.get("created") or [])
        res["wt_no_errors"] = not wt.get("errors")
        made = set(wt.get("created") or [])
        branches: set = set()
        # gitinfo fills a session's branch lazily in the background, so poll.
        for _ in range(20):
            branches = {s.get("branch") for s in api("/api/state")["sessions"] if s["id"] in made}
            if branches == {"feat-1", "feat-2"}:
                break
            time.sleep(0.5)
        res["distinct_branches"] = branches == {"feat-1", "feat-2"}
    finally:
        proc.terminate()
        subprocess.run(
            ["tmux", "-L", SOCKET, "kill-server"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        shutil.rmtree(REPO, ignore_errors=True)
        shutil.rmtree(REPO.parent / (REPO.name + "-worktrees"), ignore_errors=True)

    ok = (
        res.get("plain_created") == 3
        and res.get("plain_no_errors")
        and res.get("numbered_names")
        and res.get("wt_created") == 2
        and res.get("wt_no_errors")
        and res.get("distinct_branches")
    )
    for key, value in res.items():
        print(f"  {'ok  ' if value else 'FAIL'} {key}: {value}")
    print("spawn_check:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
