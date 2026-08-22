"""Drop TUI mouse-tracking (and a few related) sequences from a live stream.

Boxed CLIs turn mouse reporting on so they can see clicks. xterm then stops
selecting text, and copy from the pane dies. The program in tmux still gets
those codes — this filter only hides them from the browser, so drag-select
works and a click we send ourselves still reaches the CLI.

Sequences that arrive split across reads are held until they are complete.
A half-finished CSI at the end of a chunk must not leak through.
"""

from __future__ import annotations

#: DEC private modes that put the browser into mouse tracking.
_MOUSE = frozenset({1000, 1002, 1003, 1005, 1006, 1007, 1015, 1016})
#: Alternate-screen switches. Ink TUIs repaint in place; parking xterm in
#: the alt buffer hides scrollback. The program still runs in tmux.
_ALT = frozenset({47, 1047, 1048, 1049})
_DROP_DEC = _MOUSE | _ALT


def boxed_stream() -> StreamFilter:
    """The filter a CLI with its own prompt box should sit behind."""
    return StreamFilter(drop_dec=_DROP_DEC, drop_scrollback_erase=True)


class StreamFilter:
    def __init__(
        self,
        *,
        drop_dec: frozenset[int] | None = None,
        drop_scrollback_erase: bool = False,
    ) -> None:
        self._drop_dec = drop_dec or frozenset()
        self._drop_erase = drop_scrollback_erase
        self._hold = b""

    def feed(self, chunk: bytes) -> bytes:
        data = self._hold + chunk
        self._hold = b""
        out = bytearray()
        i = 0
        n = len(data)
        while i < n:
            esc = data.find(0x1B, i)
            if esc < 0:
                out += data[i:]
                break
            out += data[i:esc]
            taken, piece = self._take(data[esc:])
            if taken is None:
                self._hold = data[esc:]
                if len(self._hold) > 65_536:  # 64 KiB: a real CSI is never this long
                    # No final byte in that much data means it is not a control
                    # sequence — pass it through and recover rather than grow.
                    out += self._hold
                    self._hold = b""
                break
            out += piece
            i = esc + taken
        return bytes(out)

    def _take(self, data: bytes) -> tuple[int | None, bytes]:
        """How many bytes this ESC sequence is, and what to emit for it.

        ``taken is None`` means the sequence is unfinished — hold it.
        """
        if data[0] != 0x1B:
            return 1, data[:1]
        if len(data) < 2:
            return None, b""
        if data[1] != 0x5B:  # [
            return 2, data[:2]
        j = 2
        priv = None
        if j < len(data) and data[j] in b"<=>?":
            priv = data[j]
            j += 1
        param_at = j
        while j < len(data) and (0x30 <= data[j] <= 0x39 or data[j] == 0x3B):
            j += 1
        while j < len(data) and 0x20 <= data[j] <= 0x2F:
            j += 1
        if j >= len(data):
            return None, b""
        final = data[j]
        if not (0x40 <= final <= 0x7E):
            return 1, data[:1]
        n = j + 1
        seq = data[:n]
        rewritten = self._rewrite(priv, data[param_at:j], final, seq)
        return n, rewritten

    def _rewrite(self, priv: int | None, raw: bytes, final: int, seq: bytes) -> bytes:
        if self._drop_erase and priv is None and final == 0x4A:  # J
            params = _params(raw)
            if params == [3]:
                return b""
        if priv != 0x3F or final not in (0x68, 0x6C):  # ? h/l
            return seq
        if not self._drop_dec:
            return seq
        params = _params(raw)
        if not params:
            return seq
        kept = [p for p in params if p not in self._drop_dec]
        if len(kept) == len(params):
            return seq
        if not kept:
            return b""
        body = b";".join(str(p).encode() for p in kept)
        return b"\x1b[?" + body + bytes([final])


def _params(raw: bytes) -> list[int]:
    if not raw:
        return []
    out: list[int] = []
    for part in raw.split(b";"):
        if not part:
            continue
        try:
            out.append(int(part))
        except ValueError:
            return []
    return out
