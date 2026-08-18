"""End-to-end check of the session engine against a real tmux server.

Runs on a throwaway socket so it can never see, touch or kill a live session.
Deliberately not mocked: the failure modes worth catching here (quoting, pane
history limits, send-keys interpreting a prompt as key names) only exist when
tmux is actually running.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from muxpanel import tmux
from muxpanel.registry import Registry, RegistryError

SOCKET = "muxpanel-smoke"
ROOT = Path(__file__).resolve().parents[1]

passed = failed = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global passed, failed
    if cond:
        passed += 1
        print(f"  ok   {label}")
    else:
        failed += 1
        print(f"  FAIL {label} {detail}")


def main() -> int:
    if not tmux.available():
        print("tmux not installed — cannot run engine smoke test")
        return 1

    tmux._run(["kill-server"], SOCKET, check=False)

    print("registry")
    reg = Registry(ROOT / "config" / "clis.toml")
    types = reg.types()
    check("loads clis.toml", set(types) >= {"claude", "grok", "shell"}, sorted(types))
    check("mode pill on for claude", types["claude"].has_modes)
    check("mode pill off for grok", not types["grok"].has_modes)
    argv = reg.launch_argv("shell", session_id="a" * 32, name="smoke", cwd="/tmp")
    check("renders argv", argv[0] == "bash", argv)

    print("engine")
    tmux.bootstrap(SOCKET, history_limit=9000)
    check("server bootstraps", tmux.list_sessions(SOCKET) == [])

    sid = "1234abcd-0000-0000-0000-000000000000"
    mux = tmux.mux_name(sid)
    check("name is short and ours", mux == "sm-1234abcd", mux)

    tmux.create(mux, "/tmp", ["bash", "--norc", "-i"], socket=SOCKET,
                env={"MUXPANEL": "1", "MUXPANEL_SESSION": sid})
    check("session exists", tmux.exists(mux, SOCKET))

    panes = tmux.list_sessions(SOCKET)
    check("lists one session", len(panes) == 1, panes)
    check("reports cwd", panes and panes[0].cwd == "/tmp", panes[0].cwd if panes else "")
    check("marked as ours", panes and panes[0].ours)

    hist = tmux._run(["display-message", "-p", "-t", mux, "#{history_limit}"], SOCKET).strip()
    check("history-limit applied to pane", hist == "9000", hist)

    env = tmux._run(["show-environment", "-t", mux, "MUXPANEL_SESSION"], SOCKET).strip()
    check("session id in pane env", env.endswith(sid), env)

    # The literal-send path is the one that breaks first: a prompt full of
    # punctuation must land as characters, not as tmux key names.
    nasty = "echo 'quote\" ; semi $VAR {brace} Enter C-c'"
    tmux.send_text(mux, nasty, SOCKET)
    time.sleep(1.2)
    out = tmux.capture(mux, SOCKET, styled=False)
    check("literal text survives send-keys",
          "quote\" ; semi $VAR {brace} Enter C-c" in out, out[-120:])

    for i in range(40):
        tmux.send_text(mux, f"echo line{i}", SOCKET)
    time.sleep(1.5)
    scroll = tmux.capture(mux, SOCKET, lines=5000, styled=False)
    check("scrollback survives past the visible frame", "line0" in scroll and "line39" in scroll)

    check("attach argv targets our socket",
          tmux.attach_argv(mux, SOCKET)[:4] == ["tmux", "-L", SOCKET, "attach-session"])

    print("guards")
    try:
        tmux.kill("codeman-2be07f26", SOCKET)
        check("refuses to kill a foreign session", False, "no error raised")
    except tmux.TmuxError as exc:
        check("refuses to kill a foreign session", "refusing" in str(exc))

    try:
        tmux.create(mux, "/tmp", ["bash"], socket=SOCKET)
        check("refuses duplicate session", False, "no error raised")
    except tmux.TmuxError:
        check("refuses duplicate session", True)

    try:
        reg.get("nope")
        check("rejects unknown CLI type", False)
    except RegistryError:
        check("rejects unknown CLI type", True)

    print("teardown")
    tmux.kill(mux, SOCKET)
    check("kills our own session", not tmux.exists(mux, SOCKET))
    tmux._run(["kill-server"], SOCKET, check=False)
    check("empty socket lists nothing", tmux.list_sessions(SOCKET) == [])

    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
