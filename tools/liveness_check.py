#!/usr/bin/env python3
"""Prove a websocket peer that stops answering gets dropped, and one that
answers does not.

Why this exists. A phone whose app is killed leaves the TCP connection open
with nobody behind it: the server's `recv` parks forever, and with it a PTY and
a tmux viewer that still reads as attached, so the reaper skips it and it goes
on pinning the shared tmux window to phone size. The desktop then shows a 53x47
pane because a phone that is switched off still holds the size.

Two facts, both over a real socket pair so the shutdown that unparks a blocked
`recv` is the real one:

    python3 tools/liveness_check.py

Exit status is 0 on pass, 1 on fail.
"""

from __future__ import annotations

import os
import socket
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from clique import app
from clique.wsproto import WebSocket

#: Short enough that the whole check runs in a couple of seconds, long enough
#: that scheduling jitter on a loaded box cannot read as a missed pong.
TICK = 0.1


def _park(ws: WebSocket, out: list) -> threading.Thread:
    """The handler's read loop, near enough: a thread parked in `recv`."""
    thread = threading.Thread(target=lambda: out.append(ws.recv()), daemon=True)
    thread.start()
    return thread


def _pong(sock: socket.socket) -> None:
    """A client pong. Every frame from a client is masked, empty payload."""
    sock.sendall(bytes([0x8A, 0x80, 0, 0, 0, 0]))


def _keepalive(ws: WebSocket) -> tuple[threading.Thread, threading.Event]:
    stop = threading.Event()
    thread = threading.Thread(target=app.Handler._keepalive, args=(ws, stop), daemon=True)
    thread.start()
    return thread, stop


def silent_peer_is_dropped() -> None:
    server, client = socket.socketpair()
    ws = WebSocket(server)
    parked = []
    reader = _park(ws, parked)
    thread, stop = _keepalive(ws)
    try:
        # Nothing is read off `client`, and nothing is written to it: this is a
        # peer that went away without saying so.
        thread.join(timeout=TICK * 20)
        assert not thread.is_alive(), "keepalive never gave up on a silent peer"
        assert ws.closed, "a silent peer was left connected"
        reader.join(timeout=TICK * 10)
        assert not reader.is_alive(), "the parked recv was not unblocked"
        assert parked == [None], f"recv should end with None, got {parked}"
    finally:
        stop.set()
        client.close()
        server.close()


def answering_peer_is_kept() -> None:
    server, client = socket.socketpair()
    ws = WebSocket(server)
    parked = []
    reader = _park(ws, parked)
    _, stop = _keepalive(ws)
    answering = threading.Event()

    def answer() -> None:
        # Drain whatever the server sends and pong each time, which is what a
        # browser and OkHttp both do for us without being asked.
        while not answering.is_set():
            try:
                if not client.recv(4096):
                    return
            except OSError:
                return
            _pong(client)

    responder = threading.Thread(target=answer, daemon=True)
    responder.start()
    try:
        # Well past the deadline, so a peer that is answering has to survive
        # several intervals rather than one lucky one.
        time.sleep(TICK * 12)
        assert not ws.closed, "an answering peer was dropped"
        assert reader.is_alive(), "recv ended on a live connection"
    finally:
        answering.set()
        stop.set()
        client.close()
        server.close()


def main() -> int:
    app.PING_SECONDS = TICK
    for check in (silent_peer_is_dropped, answering_peer_is_kept):
        try:
            check()
        except AssertionError as exc:
            print(f"FAIL {check.__name__}: {exc}")
            return 1
        print(f"ok   {check.__name__}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
