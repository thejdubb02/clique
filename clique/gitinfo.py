"""Git branch and dirty state for a working directory.

The folder tree has been multi-repo since day one and said so nowhere. This
is that sentence, on the row: three ``git -C`` calls, cached per repo, so
the sidebar poll does not pay for git every three seconds.

No repo, no git installed, or a timeout all mean the same thing — this row
has nothing extra to say. Optional by design, and never asked of a CLI.
"""

from __future__ import annotations

import subprocess
import threading
import time
from pathlib import Path

TIMEOUT = 1.0
TTL = 8.0
TTL_MISS = 30.0
MAX_CACHE = 128

_lock = threading.Lock()
_cache: dict[str, tuple[float, dict]] = {}
_pending: set[str] = set()


def _git(cwd: Path, *args: str) -> str | None:
    """A git command in ``cwd``, or None if git cannot answer."""
    try:
        done = subprocess.run(                       # noqa: S603 — argv list, no shell
            ["git", "-C", str(cwd), *args],          # noqa: S607 — git from PATH, as documented
            capture_output=True, text=True, timeout=TIMEOUT, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout if done.returncode == 0 else None


def _norm(cwd: str) -> str:
    try:
        return str(Path(cwd).expanduser().resolve())
    except (OSError, RuntimeError, ValueError):
        return (cwd or "").strip()


def probe(cwd: str) -> dict:
    """Ask git now. Three calls: toplevel, branch, porcelain.

    Used by the new-session dialog, which is a pull, and by the cache filler.
    The sidebar never waits on this — it reads :func:`of`.
    """
    empty = {"branch": "", "dirty": 0, "root": ""}
    try:
        path = Path(cwd).expanduser().resolve()
    except (OSError, RuntimeError, ValueError):
        return empty
    if not path.is_dir():
        return empty
    top = _git(path, "rev-parse", "--show-toplevel")
    if not top:
        return empty
    root = top.strip()
    # symbolic-ref still names a branch that has no commits yet.
    # rev-parse --abbrev-ref HEAD does not — it dies on an unborn HEAD,
    # which is every brand-new repo, which is most of what this panel starts.
    branch = (_git(path, "symbolic-ref", "--short", "HEAD") or "").strip()
    if not branch:
        branch = "detached"
    status = _git(path, "status", "--porcelain")
    dirty = 0
    if status is not None:
        dirty = len([line for line in status.splitlines() if line.strip()])
    return {"branch": branch, "dirty": dirty, "root": root}


def of(cwd: str) -> dict:
    """Last known git for this directory. Never waits on git.

    A miss returns empty and queues a lookup; the next poll has it. A stale
    hit is returned while a refresh runs, so a row that already knew the
    branch does not blank out for eight seconds.
    """
    key = _norm(cwd)
    if not key:
        return {"branch": "", "dirty": 0}
    now = time.monotonic()
    with _lock:
        hit = _cache.get(key)
        if hit and hit[0] > now:
            info = hit[1]
            return {"branch": info["branch"], "dirty": info["dirty"]}
        stale = hit[1] if hit else None
        if key not in _pending:
            _pending.add(key)
            threading.Thread(target=_fill, args=(cwd, key), daemon=True).start()
        info = stale or {"branch": "", "dirty": 0}
        return {"branch": info.get("branch", ""), "dirty": info.get("dirty", 0)}


def _fill(cwd: str, key: str) -> None:
    info = probe(cwd)
    ttl = TTL if (info["branch"] or info["root"]) else TTL_MISS
    expires = time.monotonic() + ttl
    with _lock:
        _put(key, expires, info)
        if info["root"]:
            _put(_norm(info["root"]), expires, info)
        _pending.discard(key)


def _put(key: str, expires: float, info: dict) -> None:
    _cache[key] = (expires, info)
    if len(_cache) <= MAX_CACHE:
        return
    oldest = min(_cache, key=lambda k: _cache[k][0])
    _cache.pop(oldest, None)


def clear() -> None:
    """Drop the cache. Tests, not the panel."""
    with _lock:
        _cache.clear()
        _pending.clear()


# --------------------------------------------------------------- worktrees
#: Isolate an agent's checkout so several can run on one repo at once without
#: touching each other's files. CLIque creates the worktree, runs the session
#: inside it, and removes it when the session is deleted and nothing is lost.


def repo_root(cwd: str) -> str | None:
    """The top of the working tree ``cwd`` sits in, or None if it is not one."""
    try:
        path = Path(cwd).expanduser()
    except (OSError, RuntimeError, ValueError):
        return None
    top = _git(path, "rev-parse", "--show-toplevel")
    return top.strip() if top else None


def _slug(branch: str) -> str:
    keep = "".join(c if (c.isalnum() or c in "-._") else "-" for c in branch)
    return keep.strip("-.") or "work"


def worktree_path(repo: str, branch: str) -> Path:
    """Where a worktree goes: a sibling of the repo, never inside it — a
    worktree nested in its own repo confuses git and everything that walks the
    tree. One directory per repo holds them, named by branch."""
    root = Path(repo)
    return root.parent / f"{root.name}-worktrees" / _slug(branch)


def add_worktree(repo: str, branch: str) -> tuple[str | None, str]:
    """Create a worktree on ``branch`` and return ``(path, "")``. A new branch
    is made; if it already exists it is checked out instead. Returns
    ``(None, reason)`` if git refuses."""
    path = worktree_path(repo, branch)
    if path.exists():
        if (path / ".git").exists():
            return str(path), ""
        return None, f"{path} already exists and is not a worktree"
    path.parent.mkdir(parents=True, exist_ok=True)
    made = _git(Path(repo), "worktree", "add", "-b", branch, str(path))
    if made is None:                       # branch may already exist
        made = _git(Path(repo), "worktree", "add", str(path), branch)
    if made is None:
        return None, f"git could not create a worktree for {branch!r}"
    return str(path), ""


def worktree_clean(path: str) -> bool:
    """True only when the worktree has no uncommitted changes — safe to remove
    without losing work. A missing or non-git path is not clean."""
    status = _git(Path(path), "status", "--porcelain")
    return status is not None and status.strip() == ""


def _main_worktree(path: str) -> str | None:
    """The repo a worktree belongs to. ``worktree list`` names the main
    worktree first, and ``worktree remove`` must be run from it, not from the
    checkout being removed."""
    out = _git(Path(path), "worktree", "list", "--porcelain")
    if not out:
        return None
    for line in out.splitlines():
        if line.startswith("worktree "):
            return line[len("worktree "):].strip()
    return None


def remove_worktree(path: str) -> bool:
    """Remove a worktree checkout. The caller decides whether it is safe; this
    only does the removal, run from the main worktree so git allows it."""
    main = _main_worktree(path)
    if not main:
        return False
    return _git(Path(main), "worktree", "remove", str(path)) is not None
