"""What is already happening in a directory, asked before you start something.

Spinning up a second agent in a folder where one is already working is the
cheap mistake with the expensive recovery: two of them editing the same files,
neither aware of the other, and an afternoon spent working out which change
came from where. Nothing here prevents it — no locks, no enforced worktrees,
no refusing to start. It is a sentence before the fact, which is all that was
ever missing.

Everything it reports comes from the three sources the product is allowed to
read: CLIque's own session list, the filesystem, and git where a repo happens
to be there. Nothing asks a CLI anything, and a directory that is not a repo
simply reports less.

Pulled, never polled. This runs when somebody has the new-session dialog open
and has named a directory — a handful of times a day, not a handful of times a
minute — so it can afford to touch the disk, and an idle panel still costs
what it always did.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

#: Files whose age still counts as "somebody is working in here right now".
#: Long enough to catch an agent that paused to think, short enough that
#: yesterday's work does not raise a flag today.
RECENT = 15 * 60

#: Directory entries looked at before giving up. A node_modules is not worth
#: walking to answer a question this small, and the answer barely changes:
#: whatever is being written is almost always near the top.
SCAN_LIMIT = 400

#: Never interesting, always enormous.
SKIP = frozenset({".git", "node_modules", "__pycache__", ".venv", "venv",
                  ".mypy_cache", ".ruff_cache", "dist", "build", ".next",
                  "target", ".tox"})

TIMEOUT = 3


def _git(cwd: Path, *args: str) -> str | None:
    """A git command in `cwd`, or None if git cannot answer.

    Optional by design: no repo, no git installed, or a repo so large the
    command times out all mean the same thing here — this part of the answer
    is unavailable, and the rest is still worth having.
    """
    try:
        done = subprocess.run(                       # noqa: S603 — argv list, no shell
            ["git", "-C", str(cwd), *args],          # noqa: S607 — git from PATH, as documented
            capture_output=True, text=True, timeout=TIMEOUT, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout if done.returncode == 0 else None


def touched(cwd: Path, since: float) -> int:
    """How many files under `cwd` were written after `since`.

    A count rather than a list: the question is "is somebody in here", and
    naming the files would be a directory listing dressed as a warning.
    """
    seen = 0
    hits = 0
    stack = [cwd]
    while stack and seen < SCAN_LIMIT:
        try:
            entries = list(stack.pop().iterdir())
        except OSError:
            continue
        for entry in entries:
            seen += 1
            if seen > SCAN_LIMIT:
                break
            name = entry.name
            if name.startswith(".") and name != ".git":
                continue
            try:
                info = entry.stat()
            except OSError:
                continue
            if entry.is_dir():
                if name not in SKIP:
                    stack.append(entry)
                continue
            if info.st_mtime > since:
                hits += 1
    return hits


def make(cwd: str) -> tuple[bool, str]:
    """Create a directory. Returns (worked, why not).

    Creating a directory is filesystem state, which is the one kind of state
    this product is allowed to write — and the alternative was a dead end: the
    dialog said "there is no directory at that path" and left you to go and
    find a shell to fix it in.

    Parents are created, because the path someone types for a new project is
    usually a new leaf under a tree that exists and refusing that would be
    pedantry. Nothing is ever created implicitly: this only runs because
    somebody pressed a button naming the path they were about to make.

    No sandbox, deliberately. Anyone who can reach the panel already has a
    shell as this user, so a restriction here would protect nobody while
    breaking the ordinary case of working outside your home directory.
    """
    text = (cwd or "").strip()
    if not text:
        return False, "no path given"
    try:
        path = Path(text).expanduser()
    except (RuntimeError, ValueError):
        return False, "that is not a path"
    if not path.is_absolute():
        # A relative path would land wherever the server happens to be running,
        # which is never what the person typing it meant.
        return False, "give a full path, starting from /"
    if path.exists():
        return (True, "") if path.is_dir() else (False, "something else is already there")
    try:
        path.mkdir(parents=True)
    except OSError as err:
        return False, err.strerror or "could not create it"
    return True, ""


def look(cwd: str, sessions) -> dict:
    """What is going on in this directory, as far as we are allowed to know.

    `sessions` is the live session list; the caller supplies it rather than
    this reaching into the store, so the rule about what may be read stays
    visible at the call site.
    """
    out: dict = {"cwd": cwd, "exists": False, "sessions": [],
                 "dirty": 0, "branch": "", "touched": 0}
    try:
        path = Path(cwd).expanduser().resolve()
    except (OSError, RuntimeError, ValueError):
        return out
    if not path.is_dir():
        return out
    out["cwd"] = str(path)
    out["exists"] = True

    # Sessions already living here. Compared resolved, so /tmp and a symlink
    # to it are not reported as two unrelated places.
    for session in sessions:
        try:
            here = Path(session.cwd).expanduser().resolve()
        except (OSError, RuntimeError, ValueError):
            continue
        if here == path:
            out["sessions"].append({"id": session.id, "name": session.name,
                                    "cli": session.cli})

    status = _git(path, "status", "--porcelain")
    if status is not None:
        out["dirty"] = len([line for line in status.splitlines() if line.strip()])
        branch = _git(path, "rev-parse", "--abbrev-ref", "HEAD")
        out["branch"] = (branch or "").strip()

    out["touched"] = touched(path, time.time() - RECENT)
    return out
