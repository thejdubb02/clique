"""Per-session outline notes: a small nested checklist kept beside the panel state.

The browser owns the outline model and sends the whole tree back on every change;
the server validates it, persists it, and (when a webhook is configured) fires the
reminders that have come due. Two calls, read and write, rather than a verb per
edit: for a single-user panel with a note-sized payload that is the honest trade,
and last-write-wins is what one person editing their own notes wants anyway.

One JSON file per session under the panel home's ``notes/`` directory, so a note
never lands in the project's git status. A pre-outline ``<id>.md`` note from an
earlier build is migrated into items the first time its session's notes are read.

An item is::

    {id, text, done, collapsed, created, updated, remindAt, reminded, children[]}

``reminded`` is the server's alone: the browser never sends it, and merge_reminded
carries it across a save so a reminder that has already fired is not fired again.
"""

from __future__ import annotations

import contextlib
import json
import os
import secrets
import time
from collections.abc import Iterator
from pathlib import Path

VERSION = 1

#: Ceilings. A notes file past any of these is a misuse or a bug, not a note, and
#: the point of a cap is to fail a runaway write rather than persist it.
MAX_BYTES = 200_000
MAX_ITEMS = 2_000
MAX_DEPTH = 6
MAX_TEXT = 10_000


def dir_for(home: Path) -> Path:
    return home / "notes"


def path_for(home: Path, session_id: str) -> Path:
    return dir_for(home) / f"{session_id}.json"


def _new_id() -> str:
    return "n" + secrets.token_hex(5)


def _epoch_or_none(value: object) -> int | None:
    try:
        n = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def _blank(text: str, now: int) -> dict:
    return {
        "id": _new_id(),
        "text": text[:MAX_TEXT],
        "done": False,
        "collapsed": False,
        "created": now,
        "updated": now,
        "remindAt": None,
        "reminded": False,
        "children": [],
    }


def _clean(raw: object, depth: int, counter: list[int]) -> dict | None:
    if not isinstance(raw, dict):
        return None
    counter[0] += 1
    if counter[0] > MAX_ITEMS:
        return None
    now = int(time.time())
    item = {
        "id": str(raw.get("id") or "")[:32] or _new_id(),
        "text": str(raw.get("text") or "")[:MAX_TEXT],
        "done": bool(raw.get("done")),
        "collapsed": bool(raw.get("collapsed")),
        "created": _epoch_or_none(raw.get("created")) or now,
        "updated": _epoch_or_none(raw.get("updated")) or now,
        "remindAt": _epoch_or_none(raw.get("remindAt")),
        "reminded": bool(raw.get("reminded")),
        "children": [],
    }
    if depth < MAX_DEPTH:
        for child in raw.get("children") or []:
            if counter[0] >= MAX_ITEMS:
                break
            cleaned = _clean(child, depth + 1, counter)
            if cleaned is not None:
                item["children"].append(cleaned)
    return item


def sanitize(raw: object) -> list[dict]:
    """Coerce whatever the client sent into a well-formed item list.

    Enforces the ceilings and fills every field, so nothing downstream has to
    guess whether a key is present. A tree deeper than ``MAX_DEPTH`` keeps its
    first levels and drops the rest rather than failing the whole save.
    """
    counter = [0]
    source = raw.get("items") if isinstance(raw, dict) else raw
    items: list[dict] = []
    for entry in source or []:
        if counter[0] >= MAX_ITEMS:
            break
        cleaned = _clean(entry, 0, counter)
        if cleaned is not None:
            items.append(cleaned)
    return items


def _walk(items: list[dict]) -> Iterator[dict]:
    for it in items:
        yield it
        yield from _walk(it.get("children") or [])


def migrate_blob(text: str) -> list[dict]:
    """Turn a pre-outline plain-text note into items, one per non-empty line."""
    now = int(time.time())
    items = [_blank(line.rstrip(), now) for line in text.splitlines() if line.strip()]
    if not items and text.strip():
        items.append(_blank(text.strip(), now))
    return items


def dumps(items: list[dict]) -> str:
    return json.dumps(
        {"version": VERSION, "items": items, "updated": int(time.time())},
        ensure_ascii=False,
    )


def save(path: Path, tree_or_items: object) -> int:
    """Write items atomically, 0600. Accepts a tree dict or a bare item list."""
    items = tree_or_items.get("items") if isinstance(tree_or_items, dict) else tree_or_items
    data = dumps(list(items or []))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(data, encoding="utf-8")
    with contextlib.suppress(OSError):
        os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    return len(data.encode("utf-8"))


def load(path: Path) -> dict:
    """Read a notes tree, migrating a sibling ``.md`` blob on first read.

    Never raises: a missing, unreadable or malformed file reads as empty, which
    is what an untouched session's notes are anyway.
    """
    if path.is_file():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"version": VERSION, "items": [], "updated": 0}
        updated = _epoch_or_none(raw.get("updated")) if isinstance(raw, dict) else None
        return {"version": VERSION, "items": sanitize(raw), "updated": updated or 0}
    legacy = path.with_suffix(".md")
    if legacy.is_file():
        try:
            items = migrate_blob(legacy.read_text(encoding="utf-8"))
        except OSError:
            items = []
        if items:
            save(path, items)
            with contextlib.suppress(OSError):
                legacy.unlink()
            return {"version": VERSION, "items": items, "updated": int(time.time())}
    return {"version": VERSION, "items": [], "updated": 0}


def due(tree_or_items: object, now: float | None = None) -> list[dict]:
    """Items whose reminder time has arrived and that have not yet fired.

    Done items are skipped: a checked-off line does not need a nudge.
    """
    now = time.time() if now is None else now
    items = tree_or_items.get("items") if isinstance(tree_or_items, dict) else tree_or_items
    out = []
    for it in _walk(list(items or [])):
        when = it.get("remindAt")
        if when and not it.get("done") and not it.get("reminded") and when <= now:
            out.append(it)
    return out


def merge_reminded(old_items: list[dict], new_items: list[dict]) -> None:
    """Carry the ``reminded`` flag onto matching items in an incoming tree.

    The browser never sends ``reminded``; without this, saving any unrelated edit
    would reset it and fire the same reminder a second time. Matched by id, and
    only while the reminder time is unchanged, because moving the time is the one
    edit that deliberately asks for a fresh reminder.
    """
    prior = {
        it["id"]: it["remindAt"]
        for it in _walk(old_items or [])
        if it.get("reminded") and it.get("remindAt")
    }
    for it in _walk(new_items or []):
        when = prior.get(it.get("id"))
        if when is not None and it.get("remindAt") == when:
            it["reminded"] = True
