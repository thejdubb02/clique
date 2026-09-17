#!/usr/bin/env python3
"""Verify device pairing against a real panel.

An API token is forty-odd characters. Nobody types that into a phone, which is
why a native client has been impractical to set up. Pairing trades a short
code, chosen and displayed by the box, for a real token.

What this holds down, in the order it matters:

1. A correct code returns a token, and that token actually works.
2. The code works exactly once.
3. A wrong code is refused, and enough wrong guesses burn the code, so the
   short length is not a weakness.
4. Minting a code needs authentication. Only redeeming one is open, and it is
   open because the device doing it has no credential yet.
5. Every refusal reads the same. Distinguishing wrong from expired from none
   outstanding tells a guesser where they are.
6. Cancelling takes a code back off the screen.

Its own home, port and tmux socket; nothing touches a panel you are using.

    python3 tools/pair_check.py

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
PASSWORD = "pair-check"  # noqa: S105 — a throwaway panel on loopback
PORT = 3289
BASE = f"http://127.0.0.1:{PORT}"
HOME = Path("/tmp/clique-pair-check-home")
SOCKET = "clique-pair-check"


def _panel() -> tuple[subprocess.Popen, str]:
    shutil.rmtree(HOME, ignore_errors=True)
    HOME.mkdir(parents=True)
    env = dict(os.environ, CLIQUE_HOME=str(HOME), CLIQUE_TMUX_SOCKET=SOCKET)
    subprocess.run(
        [sys.executable, "-m", "clique", "password"],
        input=f"{PASSWORD}\n{PASSWORD}\n",
        text=True, env=env, cwd=str(ROOT), capture_output=True,
    )
    mint = subprocess.run(
        [sys.executable, "-m", "clique", "token", "create", "admin"],
        env=env, cwd=str(ROOT), capture_output=True, text=True,
    ).stdout
    token = next((ln.strip() for ln in mint.splitlines() if ln.strip().startswith("mxp_")), "")
    proc = subprocess.Popen(
        [sys.executable, "-m", "clique", "--host", "127.0.0.1", "--port", str(PORT),
         "--state", str(HOME / "state.json")],
        env=env, cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
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
    proc, admin = _panel()

    def call(path, method="GET", body=None, token=None):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + token
        req = urllib.request.Request(
            BASE + path,
            data=(json.dumps(body).encode() if body is not None else None),
            method=method, headers=headers,
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                return json.loads(r.read() or "{}"), r.status
        except urllib.error.HTTPError as err:
            return json.loads(err.read() or "{}"), err.code

    res: dict[str, object] = {}
    try:
        # Minting is not open. Only redeeming is.
        _, status = call("/api/pair", "POST")
        res["minting_a_code_needs_authentication"] = status == 401

        started, status = call("/api/pair", "POST", token=admin)
        code = str(started.get("code", ""))
        res["an_authenticated_caller_gets_a_code"] = status == 200 and len(code) == 9
        res["and_it_is_shown_in_two_groups"] = code[4:5] == "-"
        res["with_no_characters_people_mistype"] = not (set(code) & set("01IOLU"))

        # A phone types it with the dash and in whatever case it feels like.
        paired, status = call(
            "/api/pair/claim", "POST", {"code": code.lower(), "name": "Justin's Pixel"}
        )
        handed = str(paired.get("token", ""))
        res["a_correct_code_hands_over_a_token"] = status == 201 and handed.startswith("mxp_")
        res["and_the_token_is_named_after_the_device"] = paired.get("name") == "Justin's Pixel"

        # The whole point: it has to actually work.
        state, status = call("/api/state", token=handed)
        res["and_that_token_really_works"] = status == 200 and "sessions" in state

        again, status = call("/api/pair/claim", "POST", {"code": code})
        res["a_code_cannot_be_used_twice"] = status == 403

        # A wrong code, and then enough wrong codes to burn a live one.
        fresh = str(call("/api/pair", "POST", token=admin)[0].get("code", ""))
        wrong, wrong_status = call("/api/pair/claim", "POST", {"code": "ZZZZ-ZZZZ"})
        res["a_wrong_code_is_refused"] = wrong_status == 403
        res["and_says_nothing_about_why"] = wrong.get("error") == again.get("error")
        for _ in range(5):
            call("/api/pair/claim", "POST", {"code": "ZZZZ-ZZZZ"})
        _, status = call("/api/pair/claim", "POST", {"code": fresh})
        res["guessing_burns_the_code"] = status == 403

        # Cancelling takes it back off the screen.
        third = str(call("/api/pair", "POST", token=admin)[0].get("code", ""))
        res["a_live_code_reports_its_time_left"] = call("/api/pair", token=admin)[0][
            "expires_in"
        ] > 100
        call("/api/pair", "DELETE", token=admin)
        res["cancelling_clears_it"] = call("/api/pair", token=admin)[0]["expires_in"] == 0
        _, status = call("/api/pair/claim", "POST", {"code": third})
        res["and_a_cancelled_code_no_longer_works"] = status == 403
    finally:
        proc.terminate()
        subprocess.run(["tmux", "-L", SOCKET, "kill-server"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    ok = all(bool(v) for v in res.values())
    for key, value in res.items():
        print(f"  {'ok  ' if value else 'FAIL'} {key}: {value}")
    print("pair_check:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
