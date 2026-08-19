"""Images an agent left behind, so they can be seen without leaving the panel.

A terminal cannot draw a picture. Pasting already crosses that gap in one
direction — the browser holds the bytes, the pane holds the CLI, and a path is
the only thing able to travel between them. This is the other direction: an
agent writes a screenshot, and until now looking at it meant leaving.

Nothing here understands an agent, a protocol or a vendor. It lists image files
in a working directory, which is the only kind of state this product reads.
"""

from __future__ import annotations

import os
from pathlib import Path

#: Worth listing. The bytes are checked again before anything is served; this
#: is only the cheap first pass over a directory listing.
EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"})

#: Where to look, relative to the session's working directory; "." is that
#: directory itself. Deliberately shallow and deliberately short — a recursive
#: walk finds node_modules, and a longer list of guesses finds every asset the
#: repo has ever committed.
DEFAULT_DIRS = (".", ".claude-images", "screenshots", ".playwright-mcp", "artifacts")

#: Newest N. Someone who has had an agent running all day wants what it just
#: made, not the whole day.
MAX_LISTED = 30

#: Entries examined per directory. A stop, not a page: it keeps a pathological
#: directory from turning a poll into a stall.
SCAN_LIMIT = 2000

#: Ceilings on a configured list. Long enough for anyone's layout, short enough
#: that scanning it stays free.
MAX_DIRS = 12
MAX_DIR_CHARS = 128


def inside(child: Path, parent: Path) -> bool:
    """Whether `child` is `parent` or sits under it. Both must be resolved."""
    return child == parent or parent in child.parents


def clean_dirs(value) -> list[str]:
    """Validate a configured directory list; drop what is wrong, never raise.

    An entry that is absolute or climbs with `..` is a configuration mistake,
    and the honest thing is to ignore it here rather than let it reach a
    filesystem call and be caught there. An empty list is allowed and means
    exactly what it says: look nowhere.
    """
    if not isinstance(value, list):
        return list(DEFAULT_DIRS)
    out: list[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        name = item.strip()[:MAX_DIR_CHARS]
        if not name or "\x00" in name:
            continue
        candidate = Path(name)
        if candidate.is_absolute() or ".." in candidate.parts:
            continue
        if name not in out:
            out.append(name)
        if len(out) >= MAX_DIRS:
            break
    return out


def scan(cwd: str, dirs=DEFAULT_DIRS, since: float = 0.0,
         limit: int = MAX_LISTED) -> list[dict]:
    """Images in `dirs` under `cwd`, newest first.

    `since` is what makes this a list of what the agent did rather than a list
    of what the project contains: a checked-out repo's images were written
    before the session started, and an agent's screenshot was not.
    """
    try:
        root = Path(cwd).resolve(strict=True)
    except OSError:
        return []
    if not root.is_dir():
        return []

    found: list[dict] = []
    seen: set[Path] = set()
    for name in dirs:
        target = root if name in ("", ".") else (root / name).resolve()
        if not inside(target, root) or target in seen:
            continue
        seen.add(target)
        found.extend(_listing(target, root, since))
    found.sort(key=lambda row: row["mtime"], reverse=True)
    return found[:limit]


def _listing(target: Path, root: Path, since: float) -> list[dict]:
    out: list[dict] = []
    try:
        with os.scandir(target) as entries:
            for count, entry in enumerate(entries):
                if count >= SCAN_LIMIT:
                    break
                # A symlink is another directory's file wearing this one's
                # name, and following one is how a listing leaves containment.
                if entry.is_symlink() or not entry.is_file(follow_symlinks=False):
                    continue
                if Path(entry.name).suffix.lower() not in EXTENSIONS:
                    continue
                try:
                    info = entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                if info.st_mtime < since or not info.st_size:
                    continue
                out.append({
                    "name": entry.name,
                    # `rel` is what comes back to fetch it; `path` is what an
                    # agent can open, which is what paste already hands out.
                    "rel": str(Path(entry.path).relative_to(root)),
                    "path": entry.path,
                    "size": info.st_size,
                    "mtime": info.st_mtime,
                })
    except OSError:
        return []
    return out


def locate(cwd: str, relative: str) -> Path | None:
    """The absolute path behind a listed artifact, or None if it is not one.

    Everything the browser sends is a claim, so this re-derives the answer from
    the working directory instead of trusting the string. Containment is
    checked *after* resolution, which is the only order a symlinked working
    directory cannot beat.
    """
    if not relative or "\x00" in relative:
        return None
    candidate = Path(relative)
    if candidate.is_absolute() or ".." in candidate.parts:
        return None
    try:
        root = Path(cwd).resolve(strict=True)
        target = (root / candidate).resolve(strict=True)
    except OSError:
        return None
    if not inside(target, root) or not target.is_file():
        return None
    if target.suffix.lower() not in EXTENSIONS:
        return None
    return target
