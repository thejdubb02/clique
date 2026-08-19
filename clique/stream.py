"""Pseudo-terminal bridge: one PTY running `tmux attach`, piped to one browser.

This is the module that decides clique's resource cost. A PTY exists only
while a browser is actually watching a session, and dies when that browser
goes away. An idle session — the common case, with twenty tabs open and one in
focus — costs this process nothing but a dict entry. Cost scales with viewers,
not with sessions, which is the whole reason the box can hold many sessions.

Detaching is not killing. Closing the browser tears down the PTY; the tmux
session and the CLI inside it carry on, which is the persistence promise.
"""

from __future__ import annotations

import contextlib
import errno
import fcntl
import os
import pty
import signal
import struct
import termios
import threading
from collections.abc import Callable

#: Big enough that a burst of tmux output is one read, small enough to stay
#: responsive. Terminal output arrives in bursts, not a steady stream.
READ_SIZE = 65536


class PtyBridge:
    def __init__(
        self,
        argv: list[str],
        on_output: Callable[[bytes], None],
        on_exit: Callable[[], None] | None = None,
        cols: int = 120,
        rows: int = 32,
    ) -> None:
        self.argv = argv
        self.on_output = on_output
        self.on_exit = on_exit
        self.cols = cols
        self.rows = rows
        self.pid: int | None = None
        self.fd: int | None = None
        self._reader: threading.Thread | None = None
        self._stopping = threading.Event()

    # ----------------------------------------------------------------- start

    def start(self) -> None:
        pid, fd = pty.fork()
        if pid == 0:
            # Child. Nothing may run here but exec — this process is a fork of
            # a threaded interpreter and anything else is undefined behaviour.
            try:
                os.environ["TERM"] = "xterm-256color"
                os.execvp(self.argv[0], self.argv)
            except Exception:  # noqa: BLE001 - child must never return
                os._exit(127)

        self.pid = pid
        self.fd = fd
        # Set the size before the first output, so the CLI's initial paint is
        # laid out for the window the user actually has.
        self.resize(self.cols, self.rows)
        self._reader = threading.Thread(target=self._pump, name=f"pty-{pid}", daemon=True)
        self._reader.start()

    # ------------------------------------------------------------------ pump

    def _pump(self) -> None:
        assert self.fd is not None
        while not self._stopping.is_set():
            try:
                data = os.read(self.fd, READ_SIZE)
            except OSError as exc:
                # EIO is how a PTY reports the far end closed. It is the normal
                # end of a session, not an error worth surfacing.
                if exc.errno not in (errno.EIO, errno.EBADF):
                    raise
                break
            if not data:
                break
            try:
                self.on_output(data)
            except Exception:  # noqa: BLE001 - a dead browser must not kill the pump
                break
        self._finish()

    def _finish(self) -> None:
        self.close()
        if self.on_exit:
            # The callback closes the WebSocket. A browser that vanished
            # mid-write must not take the pump thread down with it.
            with contextlib.suppress(Exception):
                self.on_exit()

    # ------------------------------------------------------------------ write

    def write(self, data: bytes) -> None:
        if self.fd is None:
            return
        try:
            os.write(self.fd, data)
        except OSError:
            self.close()

    def resize(self, cols: int, rows: int) -> None:
        """Tell the PTY its new size; tmux picks it up via SIGWINCH.

        tmux sizes a session to its smallest attached client, so this is what
        stops one small window squeezing every other viewer's terminal.
        """
        if self.fd is None:
            return
        self.cols, self.rows = max(cols, 2), max(rows, 2)
        with contextlib.suppress(OSError):
            fcntl.ioctl(self.fd, termios.TIOCSWINSZ,
                        struct.pack("HHHH", self.rows, self.cols, 0, 0))

    # ------------------------------------------------------------------ close

    def close(self) -> None:
        """Tear down the viewer. The tmux session behind it is untouched."""
        if self._stopping.is_set():
            return
        self._stopping.set()

        fd, pid = self.fd, self.pid
        self.fd = None
        if fd is not None:
            with contextlib.suppress(OSError):
                os.close(fd)
        if pid:
            # SIGHUP is what a detaching terminal sends; `tmux attach` treats it
            # as a detach and leaves the session running. SIGKILL here would
            # also work, but only by accident of tmux's design.
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid, signal.SIGHUP)
            with contextlib.suppress(ChildProcessError):
                os.waitpid(pid, os.WNOHANG)
            self.pid = None
