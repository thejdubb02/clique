"""End-to-end check of the session engine against a real tmux server.

Runs on a throwaway socket so it can never see, touch or kill a live session.
Deliberately not mocked: the failure modes worth catching here (quoting, pane
history limits, send-keys interpreting a prompt as key names) only exist when
tmux is actually running.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from clique import notify, services, tmux
from clique.__main__ import config_path
from clique.registry import Registry, RegistryError

SOCKET = "clique-smoke"
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
    # The same file the app resolves to, asked for the same way. Naming the
    # path here by hand is what let it rot when the catalogue moved into the
    # package: the suite went on passing against a stale copy left on disk
    # while CI, which has no stale copy, failed.
    reg = Registry(config_path(None))
    types = reg.types()
    check("loads clis.toml", set(types) >= {"claude", "grok", "shell"}, sorted(types))
    check("mode pill on for claude", types["claude"].has_modes)
    check("mode pill off for grok", not types["grok"].has_modes)
    argv = reg.launch_argv("shell", session_id="a" * 32, name="smoke", cwd="/tmp")
    # argv[0] is the *resolved* path, not the bare name: a CLI installed
    # outside the service's PATH still has to launch. This is what grok needed.
    check("renders argv with a resolved binary",
          argv[0].endswith("/bash") and Path(argv[0]).is_file(), argv)
    check("detects an installed CLI", types["shell"].installed)
    check("reports a missing CLI as absent",
          not any(c.installed for c in types.values() if c.command == "definitely-not-here"),
          "")

    from clique.registry import parse as parse_registry
    absent = parse_registry({"cli": {"nope": {"command": "definitely-not-here-9x"}}})["nope"]
    check("resolve() returns None for a binary that is not here",
          absent.resolve() is None and not absent.installed)

    print("engine")
    tmux.bootstrap(SOCKET, history_limit=9000)
    check("server bootstraps", tmux.list_sessions(SOCKET) == [])

    sid = "1234abcd-0000-0000-0000-000000000000"
    mux = tmux.mux_name(sid)
    check("name is short and ours", mux == "sm-1234abcd", mux)

    tmux.create(mux, "/tmp", ["bash", "--norc", "-i"], socket=SOCKET,
                env={"CLIQUE": "1", "CLIQUE_SESSION": sid})
    check("session exists", tmux.exists(mux, SOCKET))

    panes = tmux.list_sessions(SOCKET)
    check("lists one session", len(panes) == 1, panes)
    check("reports cwd", panes and panes[0].cwd == "/tmp", panes[0].cwd if panes else "")
    check("marked as ours", panes and panes[0].ours)

    hist = tmux._run(["display-message", "-p", "-t", mux, "#{history_limit}"], SOCKET).strip()
    check("history-limit applied to pane", hist == "9000", hist)

    env = tmux._run(["show-environment", "-t", mux, "CLIQUE_SESSION"], SOCKET).strip()
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

    print("notification edges")
    # Pure function, so every case is worth asserting: what fires and what
    # stays quiet is the difference between a notifier you keep and one you
    # mute after a day.
    edges = notify.Watcher._events
    quiet = ("", False, True)
    busy = ("", True, True)
    waiting = ("waiting", False, True)
    failed_ = ("error", False, True)
    gone = ("", False, False)

    check("working then quiet is 'finished'", edges(busy, quiet) == ["finished"])
    check("quiet staying quiet says nothing", edges(quiet, quiet) == [])
    check("becoming waiting says so", edges(quiet, waiting) == ["waiting"])
    check("staying waiting says it once", edges(waiting, waiting) == [])
    check("waiting outranks 'finished'", edges(busy, waiting) == ["waiting"])
    check("an error is its own event", edges(busy, failed_) == ["error"])
    check("waiting then error is news again", edges(waiting, failed_) == ["error"])
    check("dying says only that", edges(busy, gone) == ["died"])
    check("a dead session stays quiet", edges(gone, gone) == [])
    check("going back to work says nothing", edges(waiting, busy) == [])

    print("webhook targets")
    # The feature is "POST to a URL someone typed", which is the exact shape
    # that turns into a credential leak on a cloud box. These assertions are
    # the boundary: what a self-hoster legitimately points this at, and what
    # has no honest use at all.
    allow = notify.allowed
    check("a public endpoint is fine", allow("https://ntfy.sh/my-topic"))
    check("so is ntfy on this very box", allow("http://127.0.0.1:8080/hook"))
    check("and something on the LAN", allow("http://192.168.1.10:2586/message"))
    check("cloud metadata is refused", not allow("http://169.254.169.254/latest/meta-data/"))
    check("link-local v6 too", not allow("http://[fe80::1]/x"))
    check("file: is not a webhook", not allow("file:///etc/passwd"))
    check("nor is gopher:", not allow("gopher://example.com/1"))
    check("a name that does not resolve is refused",
          not allow("http://clique-no-such-host.invalid/x"))
    check("and so is nonsense", not allow("not a url at all"))

    print("the prompt box decides itself")
    # The distinction is about what is on screen, not about what reads input.
    # A shell reads input and is not doubled by anything; the panel's box is
    # the only place Run, the repeat counter and a draft live there.
    check("a boxed CLI says so", types["claude"].own_input)
    check("a shell does not", not types["shell"].own_input)
    check("and it reaches the browser", reg.get("claude").as_dict()["own_input"] is True)

    print("service status")
    # The two things that make this feature honest rather than a widget: it
    # only ever asks about a CLI you actually have running, and it says
    # nothing at all when everyone is up.
    class _Store:
        settings: ClassVar[dict] = {"service_status": True}
        sessions: ClassVar[list] = []

    class _Panel:
        store = _Store()
        registry = reg

    svc = services.Services(_Panel())
    _Store.sessions = [SimpleNamespace(cli="claude"),
                       SimpleNamespace(cli="shell")]
    asked = sorted(svc.wanted())
    check("asks only about CLIs with a session open", asked == ["claude"], asked)
    check("and never about one with no feed", "shell" not in asked, asked)

    _Store.sessions = []
    check("an idle panel asks nothing at all", svc.wanted() == {}, svc.wanted())

    # A reading is only shown while it is a problem and while it is fresh.
    now = int(time.time())
    svc._seen = {
        "claude": {"cli": "claude", "label": "Claude Code", "indicator": "none",
                   "description": "All Systems Operational", "url": "", "checked": now},
    }
    check("an operational service is not news", svc.snapshot() == [], svc.snapshot())
    svc._seen["claude"]["indicator"] = "major"
    check("a real outage is", len(svc.snapshot()) == 1, svc.snapshot())
    svc._seen["claude"]["checked"] = now - services.STALE - 1
    check("and a reading nobody could refresh goes quiet rather than stale",
          svc.snapshot() == [], svc.snapshot())

    # Worst first, so the bar leads with the thing that matters.
    svc._seen = {
        "a": {"cli": "a", "label": "A", "indicator": "minor", "description": "",
              "url": "", "checked": now},
        "b": {"cli": "b", "label": "B", "indicator": "critical", "description": "",
              "url": "", "checked": now},
    }
    check("worst first", [r["cli"] for r in svc.snapshot()] == ["b", "a"],
          [r["cli"] for r in svc.snapshot()])

    # The fetcher refuses the same addresses the webhook refuses.
    check("a status feed cannot be pointed at cloud metadata",
          services.read("http://169.254.169.254/api/v2/status.json") is None)
    check("nor at a host that does not resolve",
          services.read("https://clique-no-such-host.invalid/api/v2/status.json") is None)

    print("front end")
    # There is no build step, which is the point — and it also means nothing
    # between a typo and the browser. A syntax error in app.js does not fail a
    # Python test suite; it fails silently, in front of the user, as a panel
    # that loads and then does nothing. `node --check` parses without running.
    node = shutil.which("node") or shutil.which("nodejs")
    if not node:
        print("  · no node; skipping the parse check")
    else:
        for name in ("app.js", "themes.js"):
            script = ROOT / "clique" / "web" / name
            done = subprocess.run([node, "--check", str(script)],
                                  capture_output=True, text=True)
            check(f"{name} parses", done.returncode == 0,
                  done.stderr.strip().splitlines()[-1] if done.stderr.strip() else "")

    print("icons")
    # The sprite is generated, and a hand-edit or a half-finished rename would
    # otherwise show up as an invisible button rather than a failure.
    done = subprocess.run([sys.executable, str(ROOT / "tools" / "build_icons.py"), "--check"],
                          capture_output=True, text=True)
    check("the sprite matches the icon list", done.returncode == 0,
          (done.stderr or done.stdout).strip()[:120])

    page = (ROOT / "clique" / "web" / "index.html").read_text()
    script = (ROOT / "clique" / "web" / "app.js").read_text()
    used = set(re.findall(r'href="#i-([a-z-]+)"', page + script))
    # Names inside an icon() call, including the ternary in the folder caret —
    # pull the whole argument list, then every quoted string out of it.
    # Only the first argument — the icon name. The second is a CSS class, and
    # the function's own definition has no quoted first argument at all, so it
    # contributes nothing.
    for call in re.findall(r"icon\(([^)]*)\)", script):
        used |= set(re.findall(r"""['"]([a-z-]+)['"]""", call.split(",")[0]))
    have = set(re.findall(r'<symbol id="i-([a-z-]+)"', page))
    check("every icon drawn has a symbol behind it", used <= have, sorted(used - have))

    print("mounted under a path prefix")
    # CLIque is documented as running behind `tailscale serve` at /clique,
    # which strips the prefix before the server sees it — so only the browser
    # knows where the app is mounted, and every request has to be resolved
    # against <base href>. One absolute path is enough to break a feature for
    # everyone who followed the README, and to work perfectly on localhost.
    # That is exactly how the changelog tab shipped broken.
    script = (ROOT / "clique" / "web" / "app.js").read_text()
    absolute = re.findall(r"""(?:api|fetch)\(\s*['"`]/[^'"`]*""", script)
    check("no API call escapes the mount point", not absolute, absolute[:3])

    print("teardown")
    tmux.kill(mux, SOCKET)
    check("kills our own session", not tmux.exists(mux, SOCKET))
    tmux._run(["kill-server"], SOCKET, check=False)
    check("empty socket lists nothing", tmux.list_sessions(SOCKET) == [])

    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
