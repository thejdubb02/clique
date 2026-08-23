#!/usr/bin/env python3
"""Report a Claude Code hook event to CLIque, as one word of state.

CLIque launches Claude Code with a ``--settings`` block whose hooks run this
script — ``<python> -m clique.hook <state>`` — on the events that mean the
session's state changed: Notification and Stop say *waiting*, UserPromptSubmit
says *clear*. The script reads the session id, panel URL and token that CLIque
put in the pane's environment and POSTs the state to the attention endpoint,
which is the authoritative tier of the sidebar's status — no guessing from
terminal output.

Three rules, because a hook runs inside someone's coding session:

* It never fails loudly. Any problem — no network, panel down, bad state — is
  swallowed and the exit code is 0. A status report is never worth breaking the
  CLI a person is working in.
* It does nothing unless CLIque launched the session (the ``CLIQUE_SESSION``
  env var is how it knows). A stray copy of these hooks in someone's own config
  is a silent no-op, not a stream of errors.
* It is standard-library only and finishes in well under a second, because it
  is on the path of every turn.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
import urllib.request

#: The states the attention endpoint accepts. Anything else is a caller bug, so
#: it is dropped rather than sent and rejected.
STATES = {"waiting", "error", "clear"}


def main(argv: list[str]) -> int:
    state = (argv[1] if len(argv) > 1 else "").strip().lower()
    # Optional "why" (permission / idle), from the hook's matcher. Reported so
    # the inbox can tell a permission prompt from a plain question.
    note = (argv[2] if len(argv) > 2 else "").strip().lower()
    session = os.environ.get("CLIQUE_SESSION", "").strip()
    base = os.environ.get("CLIQUE_URL", "").strip().rstrip("/")
    token = os.environ.get("CLIQUE_TOKEN", "").strip()

    # Drain stdin — Claude Code pipes the hook's JSON payload in, and leaving it
    # unread can hand the writer an EPIPE. We do not need it yet (state comes in
    # on argv), but reading it keeps the contract clean and leaves the door open
    # for a future "why" (permission vs idle vs done) taken from the payload.
    with contextlib.suppress(OSError, ValueError):
        sys.stdin.read()

    if state not in STATES or not session or not base:
        return 0  # not a CLIque-launched session, or nothing worth sending

    payload = {"state": state}
    if note:
        payload["note"] = note
    body = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(  # noqa: S310 — our own loopback URL, POSTed to below
        f"{base}/api/sessions/{session}/attention",
        data=body,
        method="POST",
        headers=headers,
    )
    # A hook must never break the CLI a person is working in, so any failure —
    # no network, panel down, timeout — is swallowed whole.
    with contextlib.suppress(Exception):
        urllib.request.urlopen(req, timeout=3).read()  # noqa: S310 — our own loopback URL
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
