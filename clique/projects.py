"""Finding a project by name, when you cannot remember where it lives.

The new-session dialog already had two ways to fill in a directory and both of
them assume you know something. The dropdown lists everywhere you have already
worked, which is no help for a repo you have not opened here yet. The path
completion behaves like a shell, which needs you to know the first few
characters of the path. Neither answers "the one called sentinel", and on a box
with forty repos across three parent directories that is the actual question.

So this walks the disk once, finds the things that look like project roots, and
matches a plain name against them. Filesystem only, which keeps it inside the
rule the whole product is built on.

Three things make the walk cheap enough to do on demand:

**It never descends into a hidden directory.** That single rule removes the
caches, the virtualenvs, the package directories and the language toolchains,
which on a developer's home directory is the overwhelming majority of the
bytes. On the box this was written on it takes an 11GB `~/.cache` out of the
walk entirely.

**It keeps going past a project root**, which is the opposite of what this
first did. Stopping there sounds like the obvious optimisation and buys
nothing: the hidden-directory rule and the skip list have already removed
everything expensive, so descending into the repos as well came out inside the
noise on a 121-project home directory. It was also hiding real answers, because
a repo of client directories that is itself a repo is an ordinary thing to
have, and every directory inside one was invisible.

**It is bounded three ways**: a depth, a total, and a wall-clock budget. A walk
that hits any of them returns what it has rather than taking the request with
it. Truncation is reported, never silent, because a search that quietly stopped
looking is worse than one that admits it.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path

#: What makes a directory a project. `.git` first and by a distance: it is the
#: one that means "this is a thing somebody works on" rather than "this happens
#: to contain a manifest". The rest catch a project that is not under version
#: control, which is rarer but is exactly the scratch directory you would
#: struggle to find by memory.
MARKERS: tuple[tuple[str, str], ...] = (
    (".git", "git"),
    ("pyproject.toml", "python"),
    ("package.json", "node"),
    ("Cargo.toml", "rust"),
    ("go.mod", "go"),
    ("pom.xml", "java"),
    ("Gemfile", "ruby"),
    ("composer.json", "php"),
    ("CMakeLists.txt", "cmake"),
)

#: Directories never worth walking into even when they are not hidden. Hidden
#: ones are already excluded wholesale, so this is only the visible offenders.
SKIP = frozenset(
    {
        "node_modules",
        "vendor",
        "venv",
        "env",
        "__pycache__",
        "site-packages",
        "dist",
        "build",
        "target",
        "out",
        "coverage",
        "tmp",
        "temp",
        "Library",
        "Applications",
        "AppData",
        "snap",
        "go",
    }
)

#: How far below a root to look. Four covers `~/code/work/client/repo`, which
#: is deeper than most people nest and shallower than a walk that never ends.
DEPTH = 4
#: Stop after this many. A machine with more projects than this has a naming
#: problem that a search box is not going to solve.
LIMIT = 2000
#: Wall-clock ceiling for one walk, in seconds. The point is that a pathological
#: filesystem costs a slow first search, never a hung panel.
BUDGET = 3.0
#: How long a walk stays good for. Long enough that typing a name is instant
#: after the first one, short enough that a repo cloned five minutes ago turns
#: up without restarting anything.
TTL = 120.0
#: Most matches worth showing in a dropdown somebody is reading.
RESULTS = 30


@dataclass(frozen=True)
class Project:
    path: str
    name: str
    kind: str

    def as_dict(self) -> dict:
        return {"path": self.path, "name": self.name, "kind": self.kind}


_cache: dict[str, object] = {"at": 0.0, "key": "", "found": [], "partial": False}


def _mark(entry: os.DirEntry) -> str:
    """What kind of project this directory is, or "" if it is not one."""
    for marker, kind in MARKERS:
        if os.path.exists(os.path.join(entry.path, marker)):
            return kind
    return ""


def _walk(root: Path, depth: int, deadline: float, out: list[Project]) -> bool:
    """Depth-first from `root`. Returns False if it ran out of room or time."""
    stack: list[tuple[str, int]] = [(str(root), 0)]
    while stack:
        here, level = stack.pop()
        if len(out) >= LIMIT or time.monotonic() > deadline:
            return False
        try:
            entries = sorted(os.scandir(here), key=lambda e: e.name.lower())
        except (OSError, PermissionError):
            continue
        for entry in entries:
            try:
                # follow_symlinks=False: a link back up the tree is how a walk
                # like this turns into an infinite one, and a symlinked repo is
                # still reachable by its real path.
                if not entry.is_dir(follow_symlinks=False):
                    continue
            except OSError:
                continue
            if entry.name.startswith(".") or entry.name in SKIP:
                continue
            kind = _mark(entry)
            if kind:
                out.append(Project(entry.path, entry.name, kind))
            # Deliberately not `continue` here. A project inside a project is
            # ordinary — a repo of client directories, a monorepo of packages —
            # and treating a root as a leaf made every one of those
            # unreachable. Measured at 0.39s against 0.45s for the version that
            # stopped, so there was never anything to buy with it.
            if level + 1 < depth:
                stack.append((entry.path, level + 1))
    return True


def _roots(extra: list[str] | None, home: Path) -> list[Path]:
    """Where to look. The home directory unless told otherwise.

    Home is the default rather than a list of fashionable folder names because
    it is the one answer that is right on a machine nobody has told us about,
    and the walk is cheap enough to make guessing unnecessary.
    """
    named = [Path(p).expanduser() for p in (extra or []) if str(p).strip()]
    roots = [p for p in named if p.is_dir()] or [home]
    # A root inside another root would index everything under it twice.
    kept: list[Path] = []
    for path in sorted({p.resolve() for p in roots}, key=lambda p: len(p.parts)):
        if not any(parent in kept for parent in path.parents):
            kept.append(path)
    return kept


def index(
    extra: list[str] | None = None,
    home: Path | None = None,
    depth: int = DEPTH,
    force: bool = False,
) -> tuple[list[Project], bool]:
    """Every project under the roots, cached. Returns the list and whether it
    had to stop early."""
    home = home or Path.home()
    key = f"{sorted(extra or [])}|{home}|{depth}"
    now = time.monotonic()
    if not force and _cache["key"] == key and now - float(_cache["at"]) < TTL:  # type: ignore[arg-type]
        return list(_cache["found"]), bool(_cache["partial"])  # type: ignore[arg-type]

    found: list[Project] = []
    whole = True
    deadline = now + BUDGET
    for root in _roots(extra, home):
        if not _walk(root, depth, deadline, found):
            whole = False
            break
    _cache.update(at=now, key=key, found=found, partial=not whole)
    return found, not whole


def search(
    q: str,
    extra: list[str] | None = None,
    home: Path | None = None,
    depth: int = DEPTH,
    limit: int = RESULTS,
    force: bool = False,
) -> dict:
    """Projects whose name or path matches `q`, best first.

    Ranked by how the match happened rather than by string distance. Somebody
    typing "sentinel" wants the directory *called* sentinel ahead of the one
    that merely has it somewhere in its path, and no amount of fuzzy scoring
    expresses that as clearly as asking the question in order.
    """
    found, partial = index(extra, home, depth, force)
    needle = (q or "").strip().lower()
    if not needle:
        # No query: the shallowest ones, which is the closest thing to "the
        # projects you would name first" without asking a model.
        ranked = sorted(found, key=lambda p: (p.path.count(os.sep), p.name.lower()))
        return {
            "projects": [p.as_dict() for p in ranked[:limit]],
            "partial": partial,
            "total": len(found),
        }

    def rank(p: Project) -> tuple:
        name = p.name.lower()
        if name == needle:
            tier = 0
        elif name.startswith(needle):
            tier = 1
        elif needle in name:
            tier = 2
        elif needle in p.path.lower():
            tier = 3
        else:
            tier = 9
        return (tier, p.path.count(os.sep), name)

    hits = [p for p in found if rank(p)[0] < 9]
    hits.sort(key=rank)
    return {
        "projects": [p.as_dict() for p in hits[:limit]],
        "partial": partial,
        "total": len(found),
    }


def forget() -> None:
    """Drop the cached walk. For the tests, and for a caller that has just
    made a directory it expects to find."""
    _cache.update(at=0.0, key="", found=[], partial=False)
