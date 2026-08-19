"""The changelog, parsed out of CHANGELOG.md for the settings sheet.

The file on disk stays the single copy. A release note written twice — once
for the repo and once for the app — is a release note that will disagree with
itself by the third release, and the one in the app is the one nobody thinks
to update.

Markdown comes back as *structure*, not HTML: blocks of spans the browser
turns into elements. No string of markup crosses the wire, so there is no
innerHTML at the other end and nothing for the CSP to argue with. The subset
is the one the file actually uses — paragraphs, bullets, bold, italic, code.
"""

from __future__ import annotations

import re
import threading
from pathlib import Path

#: Repo checkout first (how CLIque runs), then beside the package, for the day
#: someone installs it somewhere the repo root is not two directories up.
_CANDIDATES = (
    Path(__file__).resolve().parent.parent / "CHANGELOG.md",
    Path(__file__).resolve().parent / "CHANGELOG.md",
)

# "## 0.5.0 — 2026-08-18 20:29 PDT — **now CLIque**"
_HEAD = re.compile(
    r"^## (?P<ver>\S+) — (?P<date>\d{4}-\d{2}-\d{2})"
    r"(?: (?P<time>\d{2}:\d{2}) (?P<zone>[A-Z]{2,5}))?"
    r"(?: — (?P<extra>.+))?$"
)
_INLINE = re.compile(r"(\*\*.+?\*\*|`[^`]+`|\*[^*]+\*)")

_lock = threading.Lock()
_cache: tuple[float, list[dict]] | None = None


def _spans(text: str) -> list[dict]:
    """Split a line into runs of plain, bold, italic and code."""
    out: list[dict] = []
    for piece in _INLINE.split(text):
        if not piece:
            continue
        if piece.startswith("**") and piece.endswith("**"):
            out.append({"t": piece[2:-2], "b": True})
        elif piece.startswith("`") and piece.endswith("`"):
            out.append({"t": piece[1:-1], "c": True})
        elif piece.startswith("*") and piece.endswith("*"):
            out.append({"t": piece[1:-1], "i": True})
        else:
            out.append({"t": piece})
    return out


def _blocks(lines: list[str]) -> list[dict]:
    """Wrapped lines back into paragraphs and bullets.

    The file is hard-wrapped at eighty columns; a paragraph is however many
    lines it took, and a bullet runs until the next blank line or the next
    dash. Rejoining with a space is what un-wraps it.
    """
    out: list[dict] = []
    kind: str | None = None
    buf: list[str] = []

    def flush() -> None:
        nonlocal kind, buf
        if kind and buf:
            out.append({"kind": kind, "spans": _spans(" ".join(buf))})
        kind, buf = None, []

    for raw in lines:
        line = raw.strip()
        if not line:
            flush()
        elif line.startswith("- "):
            flush()
            kind, buf = "li", [line[2:]]
        elif kind:
            buf.append(line)
        else:
            kind, buf = "p", [line]
    flush()
    return out


def _parse(text: str) -> list[dict]:
    entries: list[dict] = []
    current: dict | None = None
    body: list[str] = []

    for line in text.splitlines():
        head = _HEAD.match(line)
        if head:
            if current:
                current["blocks"] = _blocks(body)
                entries.append(current)
            current = {
                "version": head.group("ver"),
                "date": head.group("date"),
                "time": head.group("time") or "",
                "zone": head.group("zone") or "",
                "extra": _spans(head.group("extra")) if head.group("extra") else [],
            }
            body = []
        elif current is not None:
            body.append(line)
    if current:
        current["blocks"] = _blocks(body)
        entries.append(current)
    return entries


def entries() -> list[dict]:
    """Every release, newest first. Re-read only when the file changes."""
    global _cache
    path = next((p for p in _CANDIDATES if p.exists()), None)
    if path is None:
        return []
    stamp = path.stat().st_mtime
    with _lock:
        if _cache and _cache[0] == stamp:
            return _cache[1]
        parsed = _parse(path.read_text(encoding="utf-8"))
        _cache = (stamp, parsed)
        return parsed
