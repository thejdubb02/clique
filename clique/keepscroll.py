"""Run a CLI with its "erase scrollback" requests taken out.

`python -m clique.keepscroll -- claude --flags` sits between tmux and the CLI.
It passes every byte both ways except one: ``ESC [ 3 J``, "erase saved lines".

Why it exists: tmux obeys that sequence by deleting the pane's history, and
Claude Code sends it with every full redraw (measured 2026-10-29: 82 lines of
history to 0 on one ``printf '\\033[3J'``; most live Claude panes held 0 lines).
Reattaching a browser or the phone replays that history, so with none there was
nothing above the current screen to scroll back to.

Opt-in per CLI with ``keep_scrollback = true`` in clis.toml. Only the erase is
dropped; ``ESC [ 2 J`` still clears the screen, and tmux's ``scroll-on-clear``
moves what was there into history first, which is the behaviour wanted.
"""

from __future__ import annotations

import contextlib
import errno
import fcntl
import os
import pty
import select
import signal
import sys
import termios
import tty

ERASE = b"\x1b[3J"


def strip(data: bytes, held: bytes) -> tuple[bytes, bytes]:
    """`data` with every ERASE removed, plus a tail that may be the start of one.

    The tail is held back for the next read, so a sequence split across two
    reads is still caught. Pure, for the self-check below.
    """
    data = (held + data).replace(ERASE, b"")
    for n in range(len(ERASE) - 1, 0, -1):
        if data.endswith(ERASE[:n]):
            return data[:-n], data[-n:]
    return data, b""


def _copy_size(src: int, dst: int) -> None:
    # No size to copy when the source is not a terminal; the pane keeps its own.
    with contextlib.suppress(OSError):
        fcntl.ioctl(dst, termios.TIOCSWINSZ, fcntl.ioctl(src, termios.TIOCGWINSZ, b"\0" * 8))


def _write_all(fd: int, data: bytes) -> None:
    while data:
        data = data[os.write(fd, data) :]


def main(argv: list[str]) -> int:
    if argv[:1] == ["--"]:
        argv = argv[1:]
    if not argv:
        print("usage: python -m clique.keepscroll -- command [args...]", file=sys.stderr)
        return 2
    pid, master = pty.fork()
    if pid == 0:
        try:
            os.execvp(argv[0], argv)
        except OSError as err:
            print(f"{argv[0]}: {err.strerror}", file=sys.stderr)
            os._exit(127)

    stdin, stdout = sys.stdin.fileno(), sys.stdout.fileno()
    interactive = os.isatty(stdin)
    saved = termios.tcgetattr(stdin) if interactive else None
    if interactive:
        _copy_size(stdin, master)
        tty.setraw(stdin)
        signal.signal(signal.SIGWINCH, lambda *_: _copy_size(stdin, master))
    held = b""
    inputs = [master, stdin]
    try:
        while True:
            try:
                ready, _, _ = select.select(inputs, [], [], 0.03 if held else None)
            except InterruptedError:
                continue
            if not ready and held:
                _write_all(stdout, held)  # nothing followed, so it was not an erase
                held = b""
            if master in ready:
                try:
                    chunk = os.read(master, 65536)
                except OSError as err:
                    if err.errno != errno.EIO:
                        raise
                    chunk = b""
                if not chunk:
                    break
                out, held = strip(chunk, held)
                _write_all(stdout, out)
            if stdin in ready:
                chunk = os.read(stdin, 65536)
                if chunk:
                    _write_all(master, chunk)
                else:
                    inputs.remove(stdin)
        _write_all(stdout, held)
    finally:
        if saved is not None:
            termios.tcsetattr(stdin, termios.TCSAFLUSH, saved)
    _, status = os.waitpid(pid, 0)
    return os.waitstatus_to_exitcode(status)


def _check() -> None:
    cases = [
        ((b"a\x1b[3Jb", b""), (b"ab", b"")),
        ((b"a\x1b[", b""), (b"a", b"\x1b[")),
        ((b"3Jb", b"\x1b["), (b"b", b"")),
        ((b"\x1b[2J", b""), (b"\x1b[2J", b"")),
        ((b"x\x1b", b""), (b"x", b"\x1b")),
        ((b"[31m", b"\x1b"), (b"\x1b[31m", b"")),
    ]
    for args, want in cases:
        if strip(*args) != want:
            raise SystemExit(f"strip{args} = {strip(*args)}, want {want}")

if __name__ == "__main__":
    if sys.argv[1:] == ["--check"]:
        _check()
        print("ok")
        sys.exit(0)
    sys.exit(main(sys.argv[1:]))
