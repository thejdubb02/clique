"""End-to-end check of the session engine against a real tmux server.

Runs on a throwaway socket so it can never see, touch or kill a live session.
Deliberately not mocked: the failure modes worth catching here (quoting, pane
history limits, send-keys interpreting a prompt as key names) only exist when
tmux is actually running.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from clique import app as app_mod
from clique import attention, files, gitinfo, notify, services, termstrip, tmux, working
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

    print("isolation")
    # A test that shares the live socket or the live state file is how a
    # /tmp shell lands on the tab someone is looking at. These two are the
    # whole wall; if either fails, the rest of the suite is not safe to run.
    stripped = {k: v for k, v in os.environ.items() if k != "CLIQUE_TMUX_SOCKET"}
    default_socket = subprocess.run(
        [sys.executable, "-c", "from clique import tmux; print(tmux.SOCKET)"],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        env=stripped,
    )
    check(
        "the engine defaults to its own socket",
        default_socket.stdout.strip() == "clique",
        default_socket.stdout,
    )
    named = subprocess.run(
        [sys.executable, "-c", "from clique import tmux; print(tmux.SOCKET)"],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        env={**os.environ, "CLIQUE_TMUX_SOCKET": "clique-env-check"},
    )
    check(
        "CLIQUE_TMUX_SOCKET is how a test gets a different one",
        named.stdout.strip() == "clique-env-check",
        named.stdout,
    )
    sandbox = "/tmp/clique-state-home-test"
    state_out = subprocess.run(
        [
            sys.executable,
            "-c",
            "from clique.__main__ import default_state_path; print(default_state_path())",
        ],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        env={**os.environ, "CLIQUE_HOME": sandbox},
    )
    check(
        "a test home does not inherit the live state file",
        state_out.stdout.strip() == sandbox + "/state.json",
        state_out.stdout.strip() or state_out.stderr[-200:],
    )

    print("boxed CLIs do not steal the mouse from the browser")
    filt = termstrip.boxed_stream()
    check("plain text is untouched", filt.feed(b"hello") == b"hello")
    check("mouse tracking on is hidden", filt.feed(b"\x1b[?1000h\x1b[?1006hhi") == b"hi")
    check("mouse tracking off is hidden too", filt.feed(b"\x1b[?1000lbye") == b"bye")
    check(
        "bracketed paste stays when mixed with mouse",
        filt.feed(b"\x1b[?1000;2004h") == b"\x1b[?2004h",
    )
    check("colour is not a mouse code", filt.feed(b"\x1b[31mred") == b"\x1b[31mred")
    split = termstrip.boxed_stream()
    check("a split sequence is held", split.feed(b"\x1b[?100") == b"")
    check("and dropped once it completes", split.feed(b"0hOK") == b"OK")
    check("the alt screen switch is hidden", filt.feed(b"\x1b[?1049hview") == b"view")
    check("wiping scrollback is hidden", filt.feed(b"\x1b[3Jkeep") == b"keep")
    check("a visible clear still happens", filt.feed(b"\x1b[2J") == b"\x1b[2J")
    passthrough = termstrip.StreamFilter()
    check("a shell keeps mouse tracking", passthrough.feed(b"\x1b[?1000h") == b"\x1b[?1000h")

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
    check(
        "renders argv with a resolved binary",
        argv[0].endswith("/bash") and Path(argv[0]).is_file(),
        argv,
    )
    check("detects an installed CLI", types["shell"].installed)
    check(
        "reports a missing CLI as absent",
        not any(c.installed for c in types.values() if c.command == "definitely-not-here"),
        "",
    )

    from clique.registry import parse as parse_registry

    absent = parse_registry({"cli": {"nope": {"command": "definitely-not-here-9x"}}})["nope"]
    check(
        "resolve() returns None for a binary that is not here",
        absent.resolve() is None and not absent.installed,
    )

    print("files")
    # A click on a printed path is a filesystem read, so these are real files
    # in a throwaway directory, not mocks.
    import tempfile

    png = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00"
        b"\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc``"
        b"\x00\x00\x00\x04\x00\x01\xf6\x178U\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    tmp = Path(tempfile.mkdtemp(prefix="clique-files-"))
    (tmp / "note.md").write_text("hello\n", encoding="utf-8")
    (tmp / "shot.png").write_bytes(png)
    (tmp / "bin.dat").write_bytes(b"\x00\x01\x02")
    (tmp / "sub").mkdir()
    (tmp / "big.txt").write_bytes(b"x" * (files.TEXT_CAP + 8))
    check("strips a compiler suffix", files.clean("src/app.js:42:7") == "src/app.js")
    check("strips trailing punctuation", files.clean("docs/foo.md.") == "docs/foo.md")
    text = files.inspect(str(tmp), "note.md")
    check("reads a relative text file", text["kind"] == "text" and text["text"] == "hello\n", text)
    check(
        "and a :line suffix still finds it", files.inspect(str(tmp), "note.md:12")["kind"] == "text"
    )
    check(
        "an image is an image from its bytes",
        files.inspect(str(tmp), "shot.png")["kind"] == "image",
    )
    check(
        "a nul in the first block is binary", files.inspect(str(tmp), "bin.dat")["kind"] == "binary"
    )
    check("a directory is a directory", files.inspect(str(tmp), "sub")["kind"] == "dir")
    check(
        "missing stays missing, not an error",
        files.inspect(str(tmp), "nope.md")["kind"] == "missing",
    )
    # Reads are fenced to the session directory by default now, so climbing out
    # with `..` is refused. Opting out (CLIQUE_FENCE_READS=0) restores the old
    # trusted-local behaviour where the path simply resolves.
    climbed = files.inspect(str(tmp / "sub"), "../note.md")
    check(
        ".. outside the session dir is refused by the default fence",
        climbed["kind"] == "missing",
        climbed,
    )
    # Credential and key material is refused even inside the session dir, and by
    # its whole family / key extensions — .env.local and a .pem, not just .env.
    for secret in (".env", ".env.local", "server.pem", ".bw-session", ".npmrc"):
        (tmp / secret).write_text("SECRET=1\n", encoding="utf-8")
        check(
            f"a credential file is refused: {secret}",
            files.inspect(str(tmp), secret)["kind"] == "missing",
            files.inspect(str(tmp), secret),
        )
    # Realpath containment: a symlink inside the dir that resolves outside it is
    # refused, because resolution happens before the containment check.
    outside = Path(tempfile.mkdtemp(prefix="clique-outside-"))
    (outside / "secret.txt").write_text("out\n", encoding="utf-8")
    try:
        (tmp / "escape").symlink_to(outside / "secret.txt")
        check(
            "a symlink resolving outside the session dir is refused",
            files.inspect(str(tmp), "escape")["kind"] == "missing",
            files.inspect(str(tmp), "escape"),
        )
    except OSError:
        pass  # a filesystem without symlinks — skip rather than fail
    finally:
        shutil.rmtree(outside, ignore_errors=True)
    _saved_fence = files._FENCE
    try:
        files._FENCE = False
        opened = files.inspect(str(tmp / "sub"), "../note.md")
        check(
            ".. resolves again when the fence is off",
            opened["kind"] == "text" and opened["text"] == "hello\n",
            opened,
        )
        # ...but the credential block is not the fence: it holds regardless.
        check(
            "a credential is still refused with the fence off",
            files.inspect(str(tmp), ".env")["kind"] == "missing",
        )
    finally:
        files._FENCE = _saved_fence
    big = files.inspect(str(tmp), "big.txt")
    check(
        "caps the text it will dump in a browser",
        big["truncated"] and len(big["text"]) == files.TEXT_CAP,
        big["size"],
    )

    # files.write: the same gate as a read, plus overwrite-existing-only.
    (tmp / "note.md").chmod(0o640)
    n = files.write(str(tmp), "note.md", "edited\n")
    check(
        "saves edited text back to an existing file",
        n == 7 and (tmp / "note.md").read_text() == "edited\n",
    )
    check("preserves the file's mode on save", ((tmp / "note.md").stat().st_mode & 0o777) == 0o640)

    def _refused(reason, *args):
        try:
            files.write(*args)
        except (ValueError, OSError):
            return True
        return False

    check("save refuses a credential file", _refused("cred", str(tmp), ".env", "x"))
    check(
        "save refuses outside the session dir",
        _refused("escape", str(tmp / "sub"), "../note.md", "x"),
    )
    check("save will not create a new file", _refused("new", str(tmp), "brand-new.md", "x"))
    check("save refuses a directory", _refused("dir", str(tmp), "sub", "x"))
    outside2 = Path(tempfile.mkdtemp(prefix="clique-outside2-"))
    (outside2 / "keep.txt").write_text("out\n", encoding="utf-8")
    try:
        (tmp / "esc").symlink_to(outside2 / "keep.txt")
        check(
            "save refuses a symlink that resolves out of the dir",
            _refused("symesc", str(tmp), "esc", "hacked"),
        )
        check("and the symlink target is untouched", (outside2 / "keep.txt").read_text() == "out\n")
    except OSError:
        pass  # no symlinks on this fs — skip
    finally:
        shutil.rmtree(outside2, ignore_errors=True)
    shutil.rmtree(tmp, ignore_errors=True)

    print("tokens")
    from clique.tokens import TokenStore

    tok_dir = Path(tempfile.mkdtemp(prefix="clique-tokens-"))
    store = TokenStore(tok_dir / "tokens.json")
    op, _ = store.create("an-operator-token", ["read", "write"])
    bound, _ = store.create("hook:abc", ["attention"], session="sess-1")
    check("a bound token records its session", bound.session == "sess-1")
    check("an operator token has no session", op.session == "")
    check(
        "per-session tokens are hidden from the operator listing",
        [t["id"] for t in store.listing()] == [op.id],
    )
    check(
        "revoke_session drops the bound token and reports it",
        store.revoke_session("sess-1") == 1 and all(t.session != "sess-1" for t in store.tokens),
    )
    check(
        "revoke_session on an unknown session drops nothing",
        store.revoke_session("nope") == 0 and len(store.tokens) == 1,
    )
    shutil.rmtree(tok_dir, ignore_errors=True)

    print("gitinfo")
    import tempfile

    gitinfo.clear()
    plain = Path(tempfile.mkdtemp(prefix="clique-git-plain-"))
    check(
        "a directory that is not a repo says nothing",
        gitinfo.probe(str(plain))["branch"] == "" and gitinfo.probe(str(plain))["dirty"] == 0,
    )
    repo = Path(tempfile.mkdtemp(prefix="clique-git-repo-"))
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "symbolic-ref", "HEAD", "refs/heads/visual"],
        check=True,
        capture_output=True,
    )
    info = gitinfo.probe(str(repo))
    check("an empty repo still has a branch", info["branch"] == "visual", info)
    check("and is clean", info["dirty"] == 0, info)
    (repo / "a.txt").write_text("x\n", encoding="utf-8")
    info = gitinfo.probe(str(repo))
    check("an untracked file is dirty", info["dirty"] == 1, info)
    gitinfo.clear()
    started = time.time()
    gitinfo.of(str(repo))
    check("the sidebar read returns without waiting on git", time.time() - started < 0.25)
    got = {"branch": "", "dirty": 0}
    deadline = time.time() + 3
    while time.time() < deadline:
        got = gitinfo.of(str(repo))
        if got["branch"] == "visual":
            break
        time.sleep(0.05)
    check("and the next read has the branch", got["branch"] == "visual" and got["dirty"] == 1, got)
    shutil.rmtree(plain, ignore_errors=True)
    shutil.rmtree(repo, ignore_errors=True)

    print("who owns the shared tmux window")
    # A tmux window has one size and every attached client sees it, so two
    # panels of different shapes cannot both be right. This used to be settled
    # by document.hasFocus(), which is per browser window: a desktop on one
    # machine and a phone in your hand both report true, so both claimed it
    # every poll and the CLI reflowed between 162 and 42 columns forever.
    from clique.app import _handheld, _may_size_window

    _handheld.clear()
    check("with no phone about, a desktop sizes the window",
          _may_size_window("sm-test", False) is True)
    check("a phone always may", _may_size_window("sm-test", True) is True)
    check("and once it has, the desktop may not",
          _may_size_window("sm-test", False) is False)
    check("the phone still may, repeatedly",
          _may_size_window("sm-test", True) and _may_size_window("sm-test", True))
    check("another session is unaffected",
          _may_size_window("sm-other", False) is True)

    # Releasing is the ordinary way out: a phone going into a pocket should
    # not lock a desktop out until a timer expires.
    _handheld.pop("sm-test", None)
    check("after the phone lets go, the desktop may again",
          _may_size_window("sm-test", False) is True)

    # The backstop, for a phone that vanishes without saying so.
    _handheld.clear()
    _may_size_window("sm-test", True)
    _handheld["sm-test"] = time.time() - (app_mod.HANDHELD_HOLD + 1)
    check("a claim older than the hold has expired",
          _may_size_window("sm-test", False) is True)
    check("and the stale entry is pruned", "sm-test" not in _handheld, dict(_handheld))
    _handheld.clear()

    print("finding a project by name")
    import tempfile

    from clique import projects

    sand = Path(tempfile.mkdtemp(prefix="clique-projects-"))
    (sand / "work" / "wsg-sentinel").mkdir(parents=True)
    (sand / "work" / "wsg-sentinel" / ".git").mkdir()
    (sand / "work" / "notes").mkdir()
    # A repo inside a repo, which is the shape that broke the first version of
    # the walk: treating a project root as a leaf made every client directory
    # inside a client repo invisible.
    (sand / "clients" / ".git").mkdir(parents=True)
    (sand / "clients" / "acme-carwash").mkdir()
    (sand / "clients" / "acme-carwash" / "package.json").write_text("{}", encoding="utf-8")
    # The things a walk must not wander into. `.cache` is the real one: on the
    # box this was written for it is 11GB.
    (sand / ".cache" / "junk" / "pyproject.toml").parent.mkdir(parents=True)
    (sand / ".cache" / "junk" / "pyproject.toml").write_text("", encoding="utf-8")
    (sand / "work" / "node_modules" / "left-pad").mkdir(parents=True)
    (sand / "work" / "node_modules" / "left-pad" / "package.json").write_text(
        "{}", encoding="utf-8"
    )

    projects.forget()
    found, partial = projects.index(home=sand)
    names = sorted(p.name for p in found)
    check("it finds the repos", "wsg-sentinel" in names and "clients" in names, names)
    check(
        "including a project inside a project",
        "acme-carwash" in names,
        names,
    )
    check("a directory with no marker is not a project", "notes" not in names, names)
    check("it does not walk into a hidden directory", "junk" not in names, names)
    check("or into node_modules", "left-pad" not in names, names)
    check("and it finished", partial is False)

    hit = projects.search("sentinel", home=sand)
    check(
        "searching by name finds the path",
        [x["path"] for x in hit["projects"]] == [str(sand / "work" / "wsg-sentinel")],
        hit,
    )
    check("and says what kind it is", hit["projects"][0]["kind"] == "git", hit)
    kinds = {x["name"]: x["kind"] for x in projects.search("", home=sand)["projects"]}
    check("a manifest with no repo still counts", kinds.get("acme-carwash") == "node", kinds)
    # The ranking is the part somebody notices: the directory *called* the
    # thing has to beat the one that merely contains it in its path.
    (sand / "work" / "sentinel-old").mkdir()
    (sand / "work" / "sentinel-old" / ".git").mkdir()
    projects.forget()
    order = [x["name"] for x in projects.search("sentinel-old", home=sand)["projects"]]
    check("an exact name outranks a path match", order[:1] == ["sentinel-old"], order)
    check("nothing matches nonsense", projects.search("zzzz", home=sand)["projects"] == [])

    projects.forget()
    narrow = projects.search("", [str(sand / "clients")], home=sand)
    check(
        "naming a root narrows the walk to it",
        all(x["path"].startswith(str(sand / "clients")) for x in narrow["projects"]),
        narrow,
    )
    projects.forget()
    check(
        "a root inside another root is not walked twice",
        len(projects._roots([str(sand), str(sand / "work")], sand)) == 1,
    )
    shutil.rmtree(sand, ignore_errors=True)
    projects.forget()

    print("engine")
    tmux.bootstrap(SOCKET, history_limit=9000)
    check("server bootstraps", tmux.list_sessions(SOCKET) == [])
    tmux._run(["set-option", "-g", "history-limit", "2000"], SOCKET)
    tmux.bootstrap(SOCKET, history_limit=9000)
    again = tmux._run(["show-options", "-g", "history-limit"], SOCKET)
    check("a second bootstrap updates global options", "9000" in again, again.strip())

    sid = "1234abcd-0000-0000-0000-000000000000"
    mux = tmux.mux_name(sid)
    check("name is short and ours", mux == "sm-1234abcd", mux)

    tmux.create(
        mux,
        "/tmp",
        ["bash", "--norc", "-i"],
        socket=SOCKET,
        env={"CLIQUE": "1", "CLIQUE_SESSION": sid},
    )
    check("session exists", tmux.exists(mux, SOCKET))
    size_opt = tmux._run(["show-window-options", "-t", mux, "window-size"], SOCKET)
    check("the window does not autoscale from attach", "manual" in size_opt, size_opt.strip())
    tmux._run(["set-window-option", "-t", mux, "window-size", "latest"], SOCKET)
    tmux.bootstrap(SOCKET, history_limit=9000)
    relocked = tmux._run(["show-window-options", "-t", mux, "window-size"], SOCKET)
    check(
        "a restart relocks windows born before the option", "manual" in relocked, relocked.strip()
    )

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
    check(
        "literal text survives send-keys", 'quote" ; semi $VAR {brace} Enter C-c' in out, out[-120:]
    )

    for i in range(40):
        tmux.send_text(mux, f"echo line{i}", SOCKET)
    time.sleep(1.5)
    scroll = tmux.capture(mux, SOCKET, lines=5000, styled=False)
    check("scrollback survives past the visible frame", "line0" in scroll and "line39" in scroll)

    check(
        "attach argv targets our socket",
        tmux.attach_argv(mux, SOCKET)[:4] == ["tmux", "-L", SOCKET, "attach-session"],
    )

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
    check(
        "a name that does not resolve is refused", not allow("http://clique-no-such-host.invalid/x")
    )
    check("and so is nonsense", not allow("not a url at all"))

    print("the prompt box decides itself")
    # The distinction is about what is on screen, not about what reads input.
    # A shell reads input and is not doubled by anything; the panel's box is
    # the only place Run, the repeat counter and a draft live there.
    check("a boxed CLI says so", types["claude"].own_input)
    check("a shell does not", not types["shell"].own_input)
    check("and it reaches the browser", reg.get("claude").as_dict()["own_input"] is True)

    print("working, or only redrawing")
    # The blind spot in tmux's activity clock, and the whole reason
    # clique/working.py exists: a redraw counts as output, so a CLI that
    # animates while it waits ticks the clock forever. Two panes that tick it
    # identically, one of which is doing nothing.
    tmux.create(
        "sm-still",
        "/tmp",
        ["bash", "-c", "while true; do printf '\\033[H\\033[2Jwaiting > '; sleep 0.4; done"],
        socket=SOCKET,
    )
    tmux.create(
        "sm-moving",
        "/tmp",
        [
            "bash",
            "-c",
            "i=0; while true; do i=$((i+1)); printf '\\033[H\\033[2Jline %s\\n' $i; sleep 0.4; done",
        ],
        socket=SOCKET,
    )
    time.sleep(1.0)
    panes = {p.mux: p for p in tmux.list_sessions(SOCKET)}
    check(
        "both panes tick the activity clock",
        all(time.time() - panes[n].activity < 2 for n in ("sm-still", "sm-moving")),
    )
    check(
        "and both are called working at first",
        working.busy(panes["sm-still"], SOCKET) and working.busy(panes["sm-moving"], SOCKET),
    )

    # Polled rather than sampled once, because "unchanged" is not a property
    # of one observation — the first capture after SETTLE has nothing to
    # compare against and correctly says nothing. The panel polls every three
    # seconds; this does the same for long enough to decide.
    deadline = time.time() + working.SETTLE + working.STILL + 12
    verdicts = {}
    while time.time() < deadline:
        panes = {p.mux: p for p in tmux.list_sessions(SOCKET)}
        for name in ("sm-still", "sm-moving"):
            verdicts[name] = working.busy(panes[name], SOCKET)
        if not verdicts["sm-still"]:
            break
        time.sleep(2)
    check("a pane redrawing the same screen settles to not working", not verdicts["sm-still"])
    check("a pane whose output changes stays working", verdicts["sm-moving"])

    working.forget(set())
    check(
        "and everything about a session that is gone is dropped",
        not working._since and not working._seen,
    )
    for name in ("sm-still", "sm-moving"):
        tmux.kill(name, SOCKET)

    print("generic question prompts")
    # These have to fire for a CLI with no [attention] table — that is how
    # Codex, Cursor, Gemini and the rest surface a permission prompt without
    # CLIque knowing anything about those vendors.
    check(
        "(y/n) is a question",
        attention.verdict_text("Allow this command (y/n)", [], []) == "waiting",
    )
    check(
        "a capitalised default (Y/n) is too",
        attention.verdict_text("Apply this change? (Y/n)", [], []) == "waiting",
    )
    check(
        "a [y/N] bracket prompt is",
        attention.verdict_text("Overwrite the file [y/N]", [], []) == "waiting",
    )
    check(
        "a short question-mark line is",
        attention.verdict_text("Do you want to continue?", [], []) == "waiting",
    )
    check(
        "a Codex-style permission question is",
        attention.verdict_text("Allow Codex to run `npm test`?", [], []) == "waiting",
    )
    check(
        "a numbered choice is",
        attention.verdict_text("  ❯ 1. Allow\n    2. Deny", [], []) == "waiting",  # noqa: RUF001
    )
    check(
        "a menu drawn with another pointer glyph is",
        attention.verdict_text("  › 2. No, keep it", [], []) == "waiting",  # noqa: RUF001
    )
    check(
        "an arrow-key menu hint is",
        attention.verdict_text("Choose one: (Use arrow keys)", [], []) == "waiting",
    )
    check(
        "a traceback is an error, not a question",
        attention.verdict_text("Traceback (most recent call last):\n  File", [], []) == "error",
    )
    # False positives are the failure mode that erodes trust in the inbox, so
    # the finished-turn shapes that actually caused one must stay silent.
    check(
        "ordinary output is neither",
        attention.verdict_text("wrote 12 files\nrunning tests", [], []) == "",
    )
    check(
        "a finished-turn summary is not a question",
        attention.verdict_text("● Done — 109.3 GB back, verified on disk", [], []) == "",
    )
    check(
        "a spinner status line is not a question",
        attention.verdict_text("✻ Sautéed for 9m 17s · 1 shell still running", [], []) == "",
    )
    check("a bare prompt glyph is not a question", attention.verdict_text("❯", [], []) == "")  # noqa: RUF001
    check(
        "a plain numbered list is not a menu",
        attention.verdict_text("1. First step\n2. Second step", [], []) == "",
    )
    check(
        "a ternary in code is not a question",
        attention.verdict_text("  const x = ok ? 1 : 2;", [], []) == "",
    )
    check(
        "a long prose line ending in ? is not a prompt",
        attention.verdict_text(
            "this is a long explanatory sentence that trails off into a "
            "rhetorical question aimed straight at the reader?",
            [],
            [],
        )
        == "",
    )

    print("service status")

    # The two things that make this feature honest rather than a widget: it
    # only ever asks about a CLI you actually have running, and it says
    # nothing at all when everyone is up.
    class _Store:
        settings: ClassVar[dict] = {"service_status": True, "open_tabs": ["s1"]}
        sessions: ClassVar[list] = []

    class _Panel:
        store = _Store()
        registry = reg

    svc = services.Services(_Panel())
    _Store.sessions = [
        SimpleNamespace(cli="claude", id="s1"),
        SimpleNamespace(cli="shell", id="s2"),
    ]
    asked = sorted(svc.wanted())
    check("asks only about CLIs with a tab open", asked == ["claude"], asked)
    check("and never about one with no feed", "shell" not in asked, asked)

    _Store.settings = {"service_status": True, "open_tabs": []}
    check("a session with no tab is not asked about", svc.wanted() == {}, svc.wanted())

    _Store.settings = {"service_status": True, "open_tabs": ["s1"]}
    _Store.sessions = []
    check("an idle panel asks nothing at all", svc.wanted() == {}, svc.wanted())

    # A reading is only shown while it is a problem and while it is fresh.
    now = int(time.time())
    svc._seen = {
        "claude": {
            "cli": "claude",
            "label": "Claude Code",
            "indicator": "none",
            "description": "All Systems Operational",
            "url": "",
            "checked": now,
        },
    }
    check("an operational service is not news", svc.snapshot() == [], svc.snapshot())
    svc._seen["claude"]["indicator"] = "major"
    check("a real outage is", len(svc.snapshot()) == 1, svc.snapshot())
    svc._seen["claude"]["checked"] = now - services.STALE - 1
    check(
        "and a reading nobody could refresh goes quiet rather than stale",
        svc.snapshot() == [],
        svc.snapshot(),
    )

    # Worst first, so the bar leads with the thing that matters.
    svc._seen = {
        "a": {
            "cli": "a",
            "label": "A",
            "indicator": "minor",
            "description": "",
            "url": "",
            "checked": now,
        },
        "b": {
            "cli": "b",
            "label": "B",
            "indicator": "critical",
            "description": "",
            "url": "",
            "checked": now,
        },
    }
    check(
        "worst first",
        [r["cli"] for r in svc.snapshot()] == ["b", "a"],
        [r["cli"] for r in svc.snapshot()],
    )

    # The fetcher refuses the same addresses the webhook refuses.
    check(
        "a status feed cannot be pointed at cloud metadata",
        services.read("http://169.254.169.254/api/v2/status.json") is None,
    )
    check(
        "nor at a host that does not resolve",
        services.read("https://clique-no-such-host.invalid/api/v2/status.json") is None,
    )

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
            done = subprocess.run([node, "--check", str(script)], capture_output=True, text=True)
            check(
                f"{name} parses",
                done.returncode == 0,
                done.stderr.strip().splitlines()[-1] if done.stderr.strip() else "",
            )

        # Every handler a menu item points at has to exist.
        #
        # `node --check` parses app.js and is perfectly happy with a call to a
        # function nobody wrote: the reference is only resolved when somebody
        # clicks. That is how "Move to folder…" spent twelve releases throwing
        # a ReferenceError into a console nobody had open, and it is a whole
        # class of bug that costs one regex to close. Arrow-wrapped calls are
        # the menu idiom throughout, so that is what this reads.
        source = (ROOT / "clique" / "web" / "app.js").read_text(encoding="utf-8")
        declared = set(re.findall(r"(?:function|const|let|var)\s+([A-Za-z_$][\w$]*)", source))
        called = set(re.findall(r"\(\)\s*=>\s*([A-Za-z_$][\w$]*)\s*\(", source))
        # Things that are legitimately not ours: globals, and methods reached
        # through an object rather than by bare name.
        ambient = {
            "alert", "confirm", "fetch", "close", "open", "print", "reload",
            "Boolean", "Number", "String",       # builtins used as callbacks
        }
        missing = sorted(called - declared - ambient)
        check("every menu handler app.js calls is defined in it", not missing, missing)

        # The decisions inside app.js, tested without a browser. See
        # tools/frontend_check.js for why that is possible without a build.
        done = subprocess.run(
            [node, str(ROOT / "tools" / "frontend_check.js")], capture_output=True, text=True
        )
        for line in done.stdout.splitlines():
            if line.strip().startswith(("ok", "FAIL")):
                print("  " + line.strip())
        tally = done.stdout.strip().splitlines()[-1] if done.stdout.strip() else "no output"
        check(f"front-end logic: {tally}", done.returncode == 0, done.stderr.strip()[:200])

    print("the unit that keeps sessions alive")
    # One line in a file nobody reads, and the entire promise of the product
    # rests on it. systemd's default KillMode signals every process in the
    # unit's cgroup, and the tmux server is a child of the panel — so with the
    # default, upgrading CLIque kills every session it exists to protect.
    unit = (ROOT / "deploy" / "clique.service").read_text()
    check(
        "the service unit does not take tmux down with it",
        "KillMode=process" in unit,
        "KillMode is missing — a restart will kill every session",
    )

    print("icons")
    # The sprite is generated, and a hand-edit or a half-finished rename would
    # otherwise show up as an invisible button rather than a failure.
    done = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "build_icons.py"), "--check"],
        capture_output=True,
        text=True,
    )
    check(
        "the sprite matches the icon list",
        done.returncode == 0,
        (done.stderr or done.stdout).strip()[:120],
    )

    page = (ROOT / "clique" / "web" / "index.html").read_text()
    script = (ROOT / "clique" / "web" / "app.js").read_text()
    used = set(re.findall(r'href="#i-([a-z0-9-]+)"', page + script))
    # Names inside an icon() call, including the ternary in the folder caret —
    # pull the whole argument list, then every quoted string out of it.
    # Only the first argument — the icon name. The second is a CSS class, and
    # the function's own definition has no quoted first argument at all, so it
    # contributes nothing.
    for call in re.findall(r"icon\(([^)]*)\)", script):
        used |= set(re.findall(r"""['"]([a-z-]+)['"]""", call.split(",")[0]))
    have = set(re.findall(r'<symbol id="i-([a-z0-9-]+)"', page))
    check("every icon drawn has a symbol behind it", used <= have, sorted(used - have))

    # The whole reason these are inline SVG rather than an image or a font: a
    # theme changes `color` and the icons follow. One `fill="#333"` from a
    # future regenerate would silently opt that icon out, and it would only
    # show up as an invisible control on somebody's light theme.
    block = page.split("<!-- icons:start -->", 1)[1].split("<!-- icons:end -->", 1)[0]
    painted = set(re.findall(r'(?:fill|stroke)="([^"]+)"', block))
    check(
        "no icon carries a colour of its own", painted <= {"none", "currentColor"}, sorted(painted)
    )

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
