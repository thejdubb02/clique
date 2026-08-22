"""Read a path a pane pointed at, so a click is not a dead end.

The terminal already turns http(s) into a click. A path like ``docs/foo.md``
was still inert, which is the thing you actually want to copy, drop in a
prompt, or glance at. This is that glance: read-only, capped, no editor.

Relative paths are against the session's working directory. Absolute paths
and ``~/`` are allowed because anyone who can reach the panel already has a
shell as this user — a sandbox here would protect nobody and hide the
ordinary case of an agent printing an absolute path.
"""

from __future__ import annotations

import re
from pathlib import Path

#: How much text the sheet will hold. Bigger than that is still copyable;
#: we just will not dump it into the browser.
TEXT_CAP = 256 * 1024

#: A printed path with a compiler-style suffix: ``src/app.js:42`` or
#: ``src/app.js:42:7``. The file is the part before that.
_LINECOL = re.compile(r":\d+(?::\d+)?$")


def _looks_image(head: bytes) -> bool:
    """Same families the artifact route serves, decided from bytes not names."""
    if head.startswith(b"\x89PNG") or head.startswith(b"\xff\xd8\xff"):
        return True
    if head.startswith(b"GIF87a") or head.startswith(b"GIF89a") or head.startswith(b"BM"):
        return True
    return head[:4] == b"RIFF" and head[8:12] == b"WEBP"


def clean(raw: str) -> str:
    """The path a click meant, minus the line number a CLI stuck on the end."""
    text = str(raw or "").strip().replace("\x00", "")[:1024]
    text = _LINECOL.sub("", text)
    return text.rstrip(".,;:!?)\"'")


def resolve(cwd: str, raw: str) -> Path:
    """Turn what was printed into an absolute path.

    ``~/`` follows the user that started the panel, not a username. Relative
    is against ``cwd``. ``..`` is resolved, not refused — the shell would
    too.
    """
    text = clean(raw)
    if not text:
        raise ValueError("empty path")
    if text.startswith("~/"):
        return (Path.home() / text[2:]).resolve()
    path = Path(text)
    if path.is_absolute():
        return path.resolve()
    return (Path(cwd) / path).resolve()


def inspect(cwd: str, raw: str) -> dict:
    """What the sheet needs: kind, size, and text when it is safe to show."""
    asked = clean(raw)
    out = {
        "asked": asked,
        "path": "",
        "name": Path(asked).name if asked else "",
        "kind": "missing",
        "size": 0,
        "text": "",
        "truncated": False,
    }
    if not asked:
        return out
    try:
        target = resolve(cwd, asked)
    except (OSError, RuntimeError, ValueError):
        return out
    out["path"] = str(target)
    out["name"] = target.name
    try:
        if target.is_dir():
            out["kind"] = "dir"
            out["size"] = 0
            return out
        if not target.is_file():
            return out
        size = target.stat().st_size
        out["size"] = size
        # Read only what is shown, not the whole file: read_bytes() on a
        # 30 GB build artifact would load it all into RAM before slicing.
        with target.open("rb") as fh:
            head = fh.read(TEXT_CAP + 1)
    except OSError:
        return out

    if _looks_image(head):
        out["kind"] = "image"
        return out
    if b"\x00" in head[:1024]:
        out["kind"] = "binary"
        return out
    out["kind"] = "text"
    sample = head[:TEXT_CAP]
    out["truncated"] = size > TEXT_CAP
    out["text"] = sample.decode("utf-8", errors="replace")
    return out
