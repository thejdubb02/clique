#!/usr/bin/env python3
"""A boxed CLI stand-in for visual checks: mouse on, no alt-screen, a prompt box.

Claude/Grok/Gemini look like this. The panel hides those mouse codes from the
browser so a drag can select; a click is sent back as SGR. This program is how
we see that both still work, without opening a real paid CLI.
"""
from __future__ import annotations

import os
import select
import shutil
import signal
import sys
import termios
import tty

MARKER = "BOXED-COPY-LINE"


def size() -> tuple[int, int]:
    return shutil.get_terminal_size((80, 24))


def paint() -> None:
    cols, rows = size()
    sys.stdout.write("\033[H")
    sys.stdout.write(f"{MARKER} unique text you can drag\033[K\n")
    bar = "─" * max(8, min(cols, 40))
    sys.stdout.write(f"\033[{max(2, rows - 3)};1H{bar}\033[K\n")
    sys.stdout.write(" Type your message\033[K\n")
    sys.stdout.write(bar + "\033[K")
    sys.stdout.flush()


def read_sgr(buf: bytes) -> tuple[int, int] | None:
    if not buf.startswith(b"\x1b[<"):
        return None
    try:
        body = buf[3:].split(b"M")[0].split(b"m")[0]
        _btn, col, row = body.split(b";")
        return int(col), int(row)
    except (ValueError, IndexError):
        return None


def main() -> None:
    sys.stdout.write("\033[?25l\033[2J")
    sys.stdout.write("\033[?1000h\033[?1002h\033[?1006h")
    paint()
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    tty.setraw(fd)
    signal.signal(signal.SIGWINCH, lambda *_: paint())
    buf = b""
    try:
        while True:
            ready, _, _ = select.select([fd], [], [], 0.5)
            if not ready:
                continue
            chunk = os.read(fd, 64)
            if not chunk or chunk in (b"\x03", b"q"):
                break
            buf += chunk
            if b"M" in buf or b"m" in buf:
                at = read_sgr(buf)
                buf = b""
                if at:
                    sys.stdout.write(f"\033[2;1Hclicked {at[0]},{at[1]}\033[K")
                    sys.stdout.flush()
            elif len(buf) > 32:
                buf = b""
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        sys.stdout.write("\033[?1000l\033[?1002l\033[?1006l\033[?25h\033[2J\033[H")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
