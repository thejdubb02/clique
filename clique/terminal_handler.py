"""WebSocket terminal handling for the HTTP request handler."""

from __future__ import annotations

import contextlib
import json
import threading
import time
from typing import Any, Callable
from http.server import BaseHTTPRequestHandler

from . import termstrip, tmux
from .stream import PtyBridge
from .wsproto import OP_TEXT, WebSocket, handshake_response

#: A terminal is at most this big. Unbounded values arrive from a query string
#: and from control frames — both are the client talking — and both end up in
#: an ioctl and in `tmux resize-window`.
MAX_COLS, MAX_ROWS = 500, 300

#: How long a phone keeps the shared window after it last said anything.
#: This is only the backstop for a phone that disappears without releasing -
#: a killed tab, a dead battery, a tunnel. The ordinary case is an explicit
#: `release` the moment the screen goes dark, so this never has to be waited
#: out. A visible phone refreshes it every poll with `hold`.
#: The backstop only drops the claim so a desktop may size again; it does
#: not restore a remembered desktop size. Restoring is `release`. A phone
#: that dies without releasing is still handled by this timer, just not
#: instantly; that is the whole backstop, not a second mechanism.
HANDHELD_HOLD = 90.0
#: mux name -> when a handheld last claimed the window.
_handheld: dict[str, float] = {}
#: mux name -> last (cols, rows, when) a non-handheld client asked for.
_desktop_size: dict[str, tuple[int, int, float]] = {}
_handheld_lock = threading.Lock()


def _may_size_window(mux: str, handheld: bool) -> bool:
    """Whether this client gets to resize the *shared* tmux window.

    A tmux window has one size and every client attached sees it, so two
    panels of different shapes cannot both be right. Until now both simply
    asserted their own size on every poll, three seconds apart, forever: the
    CLI reflowed to 162 columns, then to 42, then back, and a phone was
    unusable for as long as a desktop panel was open somewhere.

    The rule is that a handheld wins, and it is a decision about how the thing
    is used rather than a heuristic. A phone is picked up to do the thing that
    could not wait; a desktop panel is often just open. So while a phone is
    claiming, a desktop's resize still sizes its own PTY - it keeps drawing at
    its own shape - but it does not move the window under the phone.

    It has to be decided here because neither client can decide it. A browser
    cannot see the other one, and `document.hasFocus()` is per window: a
    desktop on one machine and a phone in your hand both report true, because
    both are true. The server is the only party that sees both.

    A phone in a pocket never gets here at all: a backgrounded tab or a dark
    screen is `document.hidden`, and the panel does not claim while hidden.
    """
    now = time.time()
    with _handheld_lock:
        # Anything older than the hold is finished, and pruning here keeps a
        # long-lived panel from accumulating an entry per session it ever
        # opened on a phone.
        for name in [k for k, at in _handheld.items() if now - at > HANDHELD_HOLD]:
            del _handheld[name]
        # Remembered desktop sizes prune the same way, so a long-lived panel
        # does not accumulate an entry per session it ever opened. Kept while
        # a phone still holds that mux: that size is what `release` restores.
        for name in [
            k
            for k, rec in _desktop_size.items()
            if now - rec[2] > HANDHELD_HOLD and k not in _handheld
        ]:
            del _desktop_size[name]
        if handheld:
            _handheld[mux] = now
            return True
        return mux not in _handheld


def _terminal_size(cols, rows) -> tuple[int, int]:
    """Clamp a client-supplied terminal size, tolerating rubbish."""

    def one(value, fallback: int, ceiling: int) -> int:
        try:
            return max(2, min(int(value), ceiling))
        except (TypeError, ValueError):
            return fallback

    return one(cols, 120, MAX_COLS), one(rows, 32, MAX_ROWS)


class TerminalHandler(BaseHTTPRequestHandler):
    """Terminal methods mixed into :class:`clique.app.Handler`."""

    # Provided by clique.app.Handler.
    panel: Any
    _send: Callable[..., Any]
    _same_origin: Callable[..., Any]
    _may_read: Callable[..., Any]
    _may_write: Callable[..., Any]
    _bearer: Any

    def _websocket(self, query: dict) -> None:
        """Attach one browser to one tmux session for as long as it stays.

        The PTY is created here and destroyed in the finally block, which is
        the entire resource story: no viewer, no process.
        """
        self.close_connection = True

        # Cross-Site WebSocket Hijacking. A WebSocket handshake is not subject
        # to CORS and SameSite=Lax does not cover it, so a hostile page can
        # open a socket to this origin and the browser will attach the session
        # cookie. Without this check that page gets a live root terminal. The
        # Origin header is the only thing separating "our page" from "any page
        # the user happened to visit", and it must be checked before the
        # handshake completes.
        origin = self.headers.get("Origin")
        if origin is not None and not self._same_origin():
            return self._send(403, b"cross-site websocket blocked", "text/plain")

        if not self._may_read():
            return self._send(401, b"unauthorized", "text/plain")

        # A cookie without an Origin header is not a browser. Every browser
        # sends Origin on a WebSocket handshake without exception, so a
        # cookie-authenticated handshake that lacks one did not come from a
        # page — and the cookie is the credential a hostile page would be
        # borrowing. Token callers are exempt: an Authorization header is
        # never attached by a browser on its own, so it proves intent.
        if self._bearer is None and origin is None:
            return self._send(403, b"cross-site websocket blocked", "text/plain")

        # Read-only tokens may *watch*. They may not type.
        #
        # This gate was missing entirely: the socket checked only that the
        # caller was authenticated, and control frames send keystrokes, so a
        # token issued as read-only could open a terminal and run anything.
        # Every other write path in the server is scoped; this one was the
        # hole under it.
        may_write, _reason = self._may_write()

        key = self.headers.get("Sec-WebSocket-Key")
        if not key:
            return self._send(400, b"not a websocket request", "text/plain")

        session = self.panel.store.session(query.get("id", ""))
        if not session:
            return self._send(404, b"no such session", "text/plain")

        if not tmux.exists(session.mux, session.socket):
            return self._send(409, b"session is no longer running", "text/plain")

        cols, rows = _terminal_size(query.get("cols"), query.get("rows"))

        self.wfile.write(handshake_response(key))
        self.wfile.flush()
        ws = WebSocket(self.connection)

        with self.panel._lock:
            self.panel.clients += 1

        bridge: PtyBridge | None = None
        viewer: str | None = None
        try:
            # Size the window before anything is drawn into it.
            #
            # This used to happen *after* the attach, and that is where the
            # overlapping text came from. A session is created at 200x50 and a
            # browser is rarely that; attaching first made tmux paint a whole
            # frame at the old width, which the terminal wrapped at the new
            # one, and the correct redraw then landed on top of the wreckage.
            # An interactive menu was the reliable way to see it — two cursors,
            # a duplicated choice, and fragments of one line inside another.
            #
            # The window is shared with every other client on it and its size
            # is one number, so this also claims it for the browser that just
            # opened rather than inheriting whatever the last tool left —
            # otherwise an adopted session opens as a small pane in a sea of
            # tmux dot-fill.
            #
            # `passive=1` is a background viewer: same size as the window
            # already is, and it must not become the thing that resizes it.
            # A hidden tab warming up in a second window used to steal the
            # pane the first window was looking at.
            #
            # The window's size is `manual`, so attaching a PTY cannot move
            # it — only `resize-window` can. We still skip that call for a
            # passive viewer, and still birth the PTY at the window's
            # current size so this client is not drawing a different grid
            # than the one tmux is painting.
            if query.get("passive") not in {"1", "true", "yes"}:
                tmux.resize_window(session.mux, cols, rows, session.socket)
            else:
                pane = self.panel.live().get(session.mux)
                if pane and pane.width >= 2 and pane.height >= 2:
                    cols, rows = pane.width, pane.height

            # Then scrollback, which is now captured at the width it will be
            # displayed at, and then the attach. History only — tmux redraws
            # the visible frame itself, and sending it twice reads as the CLI
            # having repeated its last screen.
            cli = self.panel.registry.types().get(session.cli)
            # Boxed CLIs turn mouse tracking on. Hidden from the browser so
            # drag-select still works; a click we encode ourselves still
            # reaches the program in tmux.
            # Always a filter now. The difference is only whether mouse
            # reporting goes with it: the alternate screen is hidden from every
            # session, because tmux ignores it to keep history and a browser
            # that honoured it would sit in a buffer that has none.
            filt = termstrip.boxed_stream() if (cli and cli.own_input) else termstrip.plain_stream()

            def outbound(data: bytes, _filt=filt) -> None:
                if _filt is not None:
                    data = _filt.feed(data)
                    if not data:
                        return
                ws.send(data)

            try:  # nosemgrep: wsg-swallowed-exception (a session that died mid-attach has nothing to replay)
                # As deep as a phone's xterm keeps (terminal.html), so a
                # reattach can scroll back past the last screenful or two.
                history = tmux.capture(session.mux, session.socket, lines=20000, history_only=True)
                if history.strip():
                    outbound(history.replace("\n", "\r\n").encode())
            except tmux.TmuxError:
                pass  # a session that died between the check and here

            # Attach through a private view rather than the session itself, so
            # this browser detaching does not detach anyone else and two
            # browsers can sit on different windows.
            viewer = tmux.create_viewer(session.mux, session.socket)
            bridge = PtyBridge(
                tmux.attach_argv(viewer, session.socket),
                on_output=outbound,
                on_exit=ws.close,
                cols=cols,
                rows=rows,
            )
            bridge.start()

            stop = threading.Event()
            threading.Thread(target=self._keepalive, args=(ws, stop), daemon=True).start()
            try:
                while True:
                    message = ws.recv()
                    if message is None:
                        break
                    opcode, payload = message
                    if opcode == OP_TEXT:
                        self._control(session, bridge, payload, may_write, viewer)
                    elif may_write:
                        bridge.write(payload)
            finally:
                stop.set()
        finally:
            if bridge:
                bridge.close()
            if viewer:
                # The view goes; the session it looked at does not.
                with contextlib.suppress(tmux.TmuxError):
                    tmux.kill(viewer, session.socket)
            ws.close()
            with self.panel._lock:
                self.panel.clients = max(self.panel.clients - 1, 0)

    def _control(
        self,
        session,
        bridge: PtyBridge,
        payload: bytes,
        may_write: bool = True,
        viewer: str | None = None,
    ) -> None:
        """Text frames are control; binary frames are keystrokes.

        Everything here is attacker-shaped: it is JSON from a socket, so a
        missing key or a string where a number belongs must be ignored rather
        than raise. An exception in this call unwinds the read loop and drops
        a working terminal.
        """
        try:
            message = json.loads(payload)
        except ValueError:
            return
        if not isinstance(message, dict):
            return
        kind = message.get("type")
        if kind == "resize":
            cols, rows = _terminal_size(message.get("cols"), message.get("rows"))
            bridge.resize(cols, rows)
            # The PTY resize only tells tmux how big *this client* is. The
            # window is `manual` and shared, so it also has to be told, or
            # the browser gets a small pane padded out with dots.
            # A read-only viewer does not get to resize what others are
            # looking at — its own view, yes; the shared window, no.
            # Tiny sizes are a collapsed or hidden tab measuring itself,
            # not a phone — those still clear 20x8. Applying them is how
            # a background reconnect left a screen of dots.
            handheld = bool(message.get("handheld"))
            if may_write and not handheld:
                # Last size a desktop asked for, whether or not it was
                # allowed to move the window *then*. A desktop refused while
                # a phone held is exactly the one whose size needs restoring
                # on `release`. A read-only viewer is still excluded: it does
                # not get to size the shared window later any more than now.
                with _handheld_lock:
                    _desktop_size[session.mux] = (cols, rows, time.time())
            if may_write and _may_size_window(session.mux, handheld):
                tmux.resize_window(session.mux, cols, rows, session.socket)
        elif kind == "hold":
            # A handheld saying it is still here and still awake. Cheap on
            # purpose: no tmux call, no resize, just the timestamp. Needed
            # because a phone that already owns the window has nothing to
            # resize and would otherwise let the hold lapse under itself.
            if may_write:
                _may_size_window(session.mux, True)
        elif kind == "release":
            # A phone going into a pocket. The timer below is the backstop for
            # one that vanishes without saying so; this is the ordinary case,
            # and it is what stops a desktop waiting out a two-minute lockout
            # after you put the phone down.
            if may_write:
                remembered = None
                with _handheld_lock:
                    _handheld.pop(session.mux, None)
                    remembered = _desktop_size.get(session.mux)
                if remembered:
                    tmux.resize_window(session.mux, remembered[0], remembered[1], session.socket)
        elif kind == "refresh":
            # Repaint this browser's own view and nobody else's. The pane can
            # come back from a layout change the same size it went in at, and
            # then tmux has no reason to send a frame and the terminal keeps
            # showing what it drew before. Read-only viewers get this too:
            # asking for your own screen back is not writing to the session.
            if viewer:
                tmux.refresh_client(viewer, session.socket)
        elif not may_write:
            return  # watching is allowed; typing is not
        elif kind == "key":
            tmux.send_key(session.mux, str(message.get("key") or ""), session.socket)
        elif kind == "run":
            tmux.send_text(
                session.mux,
                str(message.get("text", "")),
                session.socket,
                enter=bool(message.get("enter", True)),
            )

    @staticmethod
    def _keepalive(ws: WebSocket, stop: threading.Event) -> None:
        """Ping, and drop a peer that has stopped answering.

        A peer that vanishes without closing its TCP connection — a phone whose
        app was killed or whose network went away, a laptop that slept — leaves
        `recv` parked on a socket that will never deliver another byte, and
        behind that thread sit a PTY and a tmux viewer. The viewer still counts
        as attached, so the reaper skips it forever, and because every session
        in a group shares one window size, a closed phone goes on pinning the
        desktop's pane to 53x47. A pong is the only proof the other end is
        still there, so two missed ones end the connection and the handler's
        own cleanup takes the viewer with it.
        """
        # Kept in app for import and monkeypatch compatibility.
        from .app import PING_SECONDS

        while not stop.wait(PING_SECONDS):
            if ws.closed:
                return
            if time.monotonic() - ws.last_recv > PING_SECONDS * 3:
                ws.drop()
                return
            ws.ping()
