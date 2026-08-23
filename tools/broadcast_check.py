#!/usr/bin/env python3
"""Verify broadcast — one message to every live session — against a real panel.

1. Two shell sessions, one in a folder; broadcast an echo to all → both run it,
   and the response count is 2.
2. Broadcast scoped to the folder → only that session runs it, count is 1.

Its own home, port and tmux socket; nothing touches a panel you are using.

    python3 tools/broadcast_check.py

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
PASSWORD = "broadcast-check"  # noqa: S105 — a throwaway panel on loopback
PORT = 3282
BASE = f"http://127.0.0.1:{PORT}"
HOME = Path("/tmp/clique-broadcast-check-home")
SOCKET = "clique-broadcast-check"


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
    proc, token = _panel()

    def api(path, method="GET", body=None):
        req = urllib.request.Request(
            BASE + path,
            data=(json.dumps(body).encode() if body is not None else None),
            method=method,
            headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read() or "{}")

    def pane(sid):
        return subprocess.run(
            ["tmux", "-L", SOCKET, "capture-pane", "-p", "-t", "sm-" + sid.replace("-", "")[:8]],
            capture_output=True,
            text=True,
        ).stdout

    res: dict[str, object] = {}
    try:
        fid = api("/api/folders", "POST", {"name": "Team", "color": "#5FA8F5"})["id"]
        a = api(
            "/api/sessions", "POST", {"cli": "shell", "cwd": "/tmp", "name": "a", "folder": fid}
        )["id"]
        b = api("/api/sessions", "POST", {"cli": "shell", "cwd": "/tmp", "name": "b"})["id"]
        time.sleep(1.2)

        r_all = api("/api/broadcast", "POST", {"text": "echo bcast-ALL", "enter": True})
        time.sleep(1.0)
        res["all_count"] = r_all.get("count")
        res["a_got_all"] = "bcast-ALL" in pane(a)
        res["b_got_all"] = "bcast-ALL" in pane(b)

        r_folder = api(
            "/api/broadcast", "POST", {"text": "echo bcast-FOLDER", "enter": True, "folder": fid}
        )
        time.sleep(1.0)
        res["folder_count"] = r_folder.get("count")
        res["a_got_folder"] = "bcast-FOLDER" in pane(a)
        res["b_got_folder"] = "bcast-FOLDER" in pane(b)  # should be False
    finally:
        proc.terminate()
        subprocess.run(
            ["tmux", "-L", SOCKET, "kill-server"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    ok = (
        res.get("all_count") == 2
        and res.get("a_got_all")
        and res.get("b_got_all")
        and res.get("folder_count") == 1
        and res.get("a_got_folder")
        and res.get("b_got_folder") is False
    )
    for key, value in res.items():
        want_false = key == "b_got_folder"
        good = (value is False) if want_false else bool(value)
        print(f"  {'ok  ' if good else 'FAIL'} {key}: {value}")
    print("broadcast_check:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
