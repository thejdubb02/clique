"""The session engine: tmux in, plain Python out.

Design decisions worth knowing before changing anything here:

**We run our own tmux server** (``tmux -L clique``) rather than sharing the
default one. That buys three things: ``list-sessions`` returns only our
sessions, server-wide options like ``history-limit`` can be set without
touching anyone else's sessions, and a CLIque bug cannot reach Codeman's
work while both are installed. Sessions adopted from another tool stay on
whatever socket they were born on, so every call takes a ``socket``.

**Nothing goes through a shell.** Every call is an argv list to
``subprocess.run``, so a session name or a path with a quote in it is data,
not a command.

**Killing is guarded.** ``kill()`` refuses any session that is not ours unless
explicitly forced, because the same box is running work we did not create.
"""

from __future__ import annotations

import contextlib
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass

#: Our own tmux server. Codeman and anything hand-rolled use the default one.
SOCKET = "clique"

#: Session names we created. Codeman uses ``codeman-``.
PREFIX = "sm-"

#: Per-viewer sessions. Never the thing a user opened — always a window onto it.
VIEW_PREFIX = f"{PREFIX}view-"

#: Scrollback tmux keeps per pane. Reattach can only show what tmux still has.
HISTORY_LIMIT = 20000

#: tmux is fast; a call that blocks this long is wedged, not slow.
TIMEOUT = 10

_FIELDS = (
    "session_name", "session_created", "session_attached", "session_windows",
    # Both clocks, because they measure different things and only one of them
    # is the one anyone means. `session_activity` moves when a *client* does
    # something — attaching, detaching, a keystroke — and stands still while a
    # detached session produces output for an hour. `window_activity` is the
    # one that tracks the pane. Reading only the first is why a session with no
    # browser attached never looked busy and never went unread.
    "session_activity", "window_activity",
    "pane_pid", "pane_current_path", "pane_current_command",
    # Which session group this belongs to, if any. Viewers are grouped onto
    # the session they show, and this is the only way to tell that a browser
    # watching through a viewer means the underlying session is being watched.
    "session_group",
    # The window's size. Every client attached to a session shares one, so a
    # second browser — or a phone — resizes this one's pane, and a client that
    # is drawing at a different size gets a screen padded out with dots. This
    # is what lets a browser notice.
    "window_width", "window_height",
)


class TmuxError(Exception):
    """A tmux call failed, carrying tmux's own stderr rather than a rewrite."""


@dataclass(frozen=True)
class Pane:
    """One tmux session as the rest of CLIque sees it."""

    mux: str
    socket: str | None
    created: int
    attached: bool
    windows: int
    activity: int
    pid: int
    cwd: str
    command: str
    group: str = ""
    #: The shared window's size, which is not necessarily what any one client
    #: is drawing at.
    width: int = 0
    height: int = 0

    @property
    def ours(self) -> bool:
        return self.mux.startswith(PREFIX)

    def as_dict(self) -> dict:
        return {
            "mux": self.mux, "socket": self.socket, "created": self.created,
            "attached": self.attached, "windows": self.windows,
            "activity": self.activity, "pid": self.pid, "cwd": self.cwd,
            "command": self.command, "group": self.group,
        }


def _session_target(mux: str) -> str:
    """Exact-match session target. The ``=`` stops tmux prefix-matching a
    different session whose name merely starts the same way."""
    return f"={mux}"


def _pane_target(mux: str) -> str:
    """Exact-match *pane* target — the trailing colon is required.

    ``=name`` is a session target and tmux rejects it where a pane is expected
    ("can't find pane"), which is why these are two functions and not one.
    """
    return f"={mux}:"


def mux_name(session_id: str) -> str:
    """``sm-`` plus the first 8 of the uuid — short enough to read in `tmux ls`."""
    return f"{PREFIX}{session_id.replace('-', '')[:8]}"


def available() -> bool:
    return shutil.which("tmux") is not None


def _argv(socket: str | None, args: list[str]) -> list[str]:
    return ["tmux", *(["-L", socket] if socket else []), *args]


def _run(
    args: list[str],
    socket: str | None = SOCKET,
    *,
    check: bool = True,
    timeout: int = TIMEOUT,
) -> str:
    if not available():
        raise TmuxError("tmux not found. Install: sudo apt install tmux")
    argv = _argv(socket, args)
    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise TmuxError(f"tmux timed out after {timeout}s: {' '.join(args[:2])}") from exc
    if check and proc.returncode != 0:
        raise TmuxError(proc.stderr.strip() or f"tmux exited {proc.returncode}")
    return proc.stdout


def bootstrap(socket: str | None = SOCKET, history_limit: int = HISTORY_LIMIT) -> None:
    """Start our server and set the options new panes inherit.

    Two things make this one invocation rather than several. tmux defaults to
    ``exit-empty on``, so a server with no sessions dies the instant it starts
    and separate calls would each find nothing running. And a pane takes its
    history-limit at creation time, so the option has to be in place before the
    first session or scrollback silently caps at tmux's default 2000 lines.
    """
    args = [
        "start-server",
        # Keep the server alive with no sessions, so these options survive
        # killing the last one and a reattach does not race a cold server.
        ";", "set-option", "-g", "exit-empty", "off",
        ";", "set-option", "-g", "history-limit", str(history_limit),
        # A CLI's bell should not steal focus in a browser tab, and we render
        # our own tab bar, so tmux's status line is duplicate chrome.
        ";", "set-option", "-g", "status", "off",
        ";", "set-option", "-g", "bell-action", "none",
        # Size to the most recent client, not the smallest. Without this, a
        # phone attaching to a session squeezes the same session on a desktop.
        ";", "set-option", "-g", "window-size", "latest",
    ]
    # A server that is still shutting down from a previous run answers
    # "server exited unexpectedly" once, then comes up clean. Retrying beats
    # making every caller handle a restart race they did not cause.
    for attempt in range(3):
        try:
            _run(args, socket)
            return
        except TmuxError as exc:
            if attempt == 2 or "exited unexpectedly" not in str(exc):
                raise
            time.sleep(0.25)


def sweep_viewers(
    sockets: tuple[str | None, ...] = (SOCKET,),
    *,
    detached_only: bool = False,
    min_age: int = 30,
) -> int:
    """Delete viewer sessions nothing is watching.

    A viewer is disposable by construction — it holds no work, only one
    client's view of someone else's session — so removing a stale one can never
    lose anything.

    Two callers. At startup, everything goes: no browser can be attached yet.
    While running, ``detached_only`` reaps viewers whose client has gone, which
    is the case a restart creates — the server dies before its cleanup runs,
    the browser reconnects and builds a new viewer, and the old one lingers
    forever. ``min_age`` keeps that from racing a viewer created moments ago
    and not yet attached.

    Note the explicit prefix on ``list_sessions``: the default listing hides
    viewers precisely because they are plumbing, and forgetting that here is
    what let them accumulate ten-to-three against real clients.
    """
    removed = 0
    now = time.time()
    for socket_name in sockets:
        for pane in list_sessions(socket_name, prefix=VIEW_PREFIX):
            if detached_only and (pane.attached or now - pane.created < min_age):
                continue
            kill(pane.mux, socket_name)
            removed += 1
    return removed


def create_viewer(target: str, socket: str | None = SOCKET) -> str:
    """A grouped session sharing ``target``'s windows.

    Grouping gives this client its own *current window* — so two browsers can
    look at different windows of the same session — and it keeps a browser
    detaching from detaching everyone.

    It does **not** give it an independent size, and an earlier version of this
    docstring claimed it did. A window is shared by every session in the group,
    so tmux has exactly one size for it, decided by `window-size`. Two clients
    of different sizes means one of them sees the pane padded out with dots.
    That is why `resize_window` exists and why the browser calls it on every
    resize: whoever is actually being looked at should win.
    """
    name = f"{VIEW_PREFIX}{uuid.uuid4().hex[:6]}"
    _run(["new-session", "-d", "-t", _session_target(target), "-s", name], socket)
    _no_status(name, socket)
    return name


def resize_window(mux: str, cols: int, rows: int, socket: str | None = SOCKET) -> None:
    """Set the shared window's size, so the browser is not shown a padded pane.

    `window-size latest` sizes a window to the most recently *active* client,
    and a browser that has resized but not yet typed is not active. With
    another tool still attached to an adopted session, that leaves the browser
    rendering tmux's dot-fill over most of the viewport.

    So the size is set explicitly rather than inferred. This does resize the
    other client's view — which is the correct trade for a tool whose whole
    job is to be the thing you are looking at.
    """
    with contextlib.suppress(TmuxError, OSError):
        _run(["resize-window", "-t", _session_target(mux),
              "-x", str(max(cols, 2)), "-y", str(max(rows, 2))], socket)


def list_sessions(socket: str | None = SOCKET, prefix: str | None = None) -> list[Pane]:
    """Every session on a socket. One subprocess for all of them, deliberately.

    The sidebar polls this a few times a minute; per-session calls would make
    the cost grow with the number of sessions, which is the thing we are trying
    to keep cheap.
    """
    fmt = "\t".join(f"#{{{f}}}" for f in _FIELDS)
    try:
        out = _run(["list-sessions", "-F", fmt], socket)
    except TmuxError as exc:
        # No server running is the normal empty case, not a failure.
        if "no server running" in str(exc) or "No such file" in str(exc):
            return []
        raise

    panes: list[Pane] = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) != len(_FIELDS):
            continue
        name = parts[0]
        if prefix and not name.startswith(prefix):
            continue
        if name.startswith(VIEW_PREFIX) and prefix != VIEW_PREFIX:
            continue  # plumbing, not a session anyone opened
        panes.append(Pane(
            mux=name, socket=socket,
            created=int(parts[1] or 0), attached=parts[2] == "1",
            windows=int(parts[3] or 1),
            # The later of the two: a client interacting and a pane producing
            # output are both activity, and whichever happened last is the
            # answer to "when did anything happen here".
            activity=max(int(parts[4] or 0), int(parts[5] or 0)),
            pid=int(parts[6] or 0), cwd=parts[7], command=parts[8],
            group=parts[9],
            width=int(parts[10] or 0), height=int(parts[11] or 0),
        ))
    return panes


def descendants(pid: int, depth: int = 4) -> list[tuple[str, str]]:
    """Every process under `pid`, as (command name, full argv).

    A pane's `pane_current_command` is whatever is in the foreground of its
    shell, and a CLI started from a wrapper script or a login shell is a child
    of that shell rather than the shell itself. Codeman's sessions all report
    `bash` for exactly this reason, and adopting them by their pane command
    filed five Claude Code sessions as plain shells.

    Process state, which rule 1 allows. Nothing here knows what any particular
    CLI is — it returns what is running and lets the registry decide.
    """
    found: list[tuple[str, str]] = []
    frontier = [pid]
    for _ in range(depth):
        if not frontier:
            break
        try:
            out = subprocess.run(
                ["ps", "-o", "pid=,comm=,args=", "--ppid", ",".join(map(str, frontier))],
                capture_output=True, text=True, timeout=TIMEOUT,
            ).stdout
        except (OSError, subprocess.SubprocessError):
            return found
        frontier = []
        for line in out.splitlines():
            parts = line.strip().split(None, 2)
            if len(parts) < 2:
                continue
            child_pid, comm = parts[0], parts[1]
            found.append((comm, parts[2] if len(parts) > 2 else comm))
            with contextlib.suppress(ValueError):
                frontier.append(int(child_pid))
    return found


def exists(mux: str, socket: str | None = SOCKET) -> bool:
    if not available():
        return False
    argv = _argv(socket, ["has-session", "-t", _session_target(mux)])
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=TIMEOUT)
    except (subprocess.TimeoutExpired, OSError):
        # A wedged tmux is not an answer of "yes". Every other call goes
        # through _run, which handles this; this one called subprocess
        # directly and let the exception out into a request handler.
        return False
    return proc.returncode == 0


def create(
    mux: str,
    cwd: str,
    argv: list[str],
    *,
    socket: str | None = SOCKET,
    width: int = 200,
    height: int = 50,
    env: dict[str, str] | None = None,
) -> None:
    """Start a detached session running ``argv``. Detached is the whole point:
    the CLI is alive whether or not a browser is looking at it."""
    if exists(mux, socket):
        raise TmuxError(f"session {mux} already exists")

    args = ["new-session", "-d", "-s", mux, "-c", cwd, "-x", str(width), "-y", str(height)]
    for key, value in (env or {}).items():
        args += ["-e", f"{key}={value}"]
    args += argv
    _run(args, socket)
    _no_status(mux, socket)


def _no_status(mux: str, socket: str | None) -> None:
    """Turn tmux's status line off for this session specifically.

    It is already off globally, set when the server starts — and that was not
    enough. A user's own `~/.tmux.conf` is read by this server too, deliberately
    so their keybindings work, and a config with a status-line plugin in it
    switches the bar back on for each new session. The result was CLIque's own
    plumbing on screen: a viewer session announcing itself as `sm-view-ba434f`
    at the bottom of a pane, which is an internal name no user should ever
    see.

    A session-local value beats the global one, and unlike `-f /dev/null` it
    costs the user nothing else they had configured.

    Its own call rather than a `;`-chained one: after `new-session` has been
    given a shell command, tmux folds a following separator into that command
    rather than treating it as one. Never fatal — a status bar is ugly, not
    broken — so a failure here is swallowed.
    """
    with contextlib.suppress(TmuxError, OSError):
        # A third target form, and not interchangeable with the other two:
        # `set-option` rejects the bare `=name` session target outright, while
        # `=name:` is accepted and still exact. Dropping the `=` would work and
        # would also let tmux prefix-match its way onto somebody else's
        # session.
        _run(["set-option", "-t", _pane_target(mux), "status", "off"], socket)


def kill(mux: str, socket: str | None = SOCKET, *, force: bool = False) -> None:
    """Kill a session. Refuses anything not ours unless forced.

    The guard is not paperwork: this box runs sessions CLIque did not create,
    and a wrong target here destroys someone's in-flight work.
    """
    if not mux.startswith(PREFIX) and not force:
        raise TmuxError(
            f"refusing to kill {mux!r}: not a CLIque session "
            f"(expected {PREFIX}*); pass force=True if this was adopted"
        )
    if exists(mux, socket):
        _run(["kill-session", "-t", _session_target(mux)], socket)


def send_text(mux: str, text: str, socket: str | None = SOCKET, *, enter: bool = True) -> None:
    """Type text into the pane.

    ``send-keys -l`` sends it literally in one call, so a prompt containing
    ``Enter``, a semicolon or a brace arrives as characters rather than being
    interpreted as key names. Enter goes as a separate call for the same reason.
    """
    if text:
        _run(["send-keys", "-t", _pane_target(mux), "-l", "--", text], socket)
    if enter:
        _run(["send-keys", "-t", _pane_target(mux), "Enter"], socket)


def send_key(mux: str, key: str, socket: str | None = SOCKET) -> None:
    """Send a named key (``S-Tab``, ``C-c``, ``Escape``) rather than literal text."""
    _run(["send-keys", "-t", _pane_target(mux), key], socket)


def capture(
    mux: str,
    socket: str | None = SOCKET,
    *,
    lines: int = 5000,
    styled: bool = True,
    history_only: bool = False,
) -> str:
    """Scrollback for a reattaching browser.

    This is what makes reattach show history instead of a blank screen until
    the next keystroke. ``-J`` rejoins wrapped lines; ``-e`` keeps colour.

    ``history_only`` stops at the line above the visible frame. Attaching makes
    tmux redraw that frame itself, so capturing it too would print the last
    screenful twice — which reads as the CLI having repeated itself.
    """
    args = ["capture-pane", "-p", "-J", "-S", f"-{lines}", "-t", _pane_target(mux)]
    if history_only:
        args += ["-E", "-1"]
    if styled:
        args.insert(3, "-e")
    return _run(args, socket, timeout=TIMEOUT * 2)


def attach_argv(mux: str, socket: str | None = SOCKET) -> list[str]:
    """argv for the PTY bridge to exec.

    Spawned when a browser attaches and killed when it leaves, so an unwatched
    session costs this process nothing at all.
    """
    return _argv(socket, ["attach-session", "-t", _session_target(mux)])


def attached_via_viewers(socket: str | None = SOCKET) -> set[str]:
    """Session groups that have a browser watching through a viewer.

    Needed because a viewer is its own tmux session: attaching to it leaves the
    session it mirrors reporting zero clients. Without this, "someone is
    watching this" was never true and every session sat permanently on the
    unwatched colour.
    """
    watching: set[str] = set()
    for pane in list_sessions(socket, prefix=VIEW_PREFIX):
        if pane.attached and pane.group:
            watching.add(pane.group)
    return watching


#: Where Codeman keeps its sessions. Verified on this box: it runs its own
#: servers too, so the default socket is not where its work lives.
#: Sockets that belong to another tool, scanned so its sessions can be taken
#: over rather than abandoned. "muxpanel" is this tool's own former name —
#: sessions started before the rename live on that socket and are still
#: running, so they are adoptable rather than lost.
FOREIGN_SOCKETS = ("codeman", "codeman-grok", "muxpanel")


def adoptable(
    sockets: tuple[str, ...] = FOREIGN_SOCKETS,
    # Our own PREFIX is here too, because on a *foreign* socket it is not ours
    # — it is the pre-rename tool's, and those sessions are still running.
    prefixes: tuple[str, ...] = ("codeman-", PREFIX),
) -> list[Pane]:
    """Sessions on another tool's socket that CLIque could take over.

    Used once, at migration: Codeman's sessions are ordinary tmux sessions, so
    switching tools should not cost anyone their running work. Adopted sessions
    stay on their original socket — moving a session between tmux servers is
    not possible, and killing one to recreate it would defeat the point.
    """
    found: list[Pane] = []
    for socket in sockets:
        found += [p for p in list_sessions(socket) if p.mux.startswith(prefixes)]
    return found
