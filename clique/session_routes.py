"""Session peek, attention, group, checkpoint, export and notes helpers for the HTTP request handler."""

from __future__ import annotations

import dataclasses
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any, Callable

from . import attention, gitinfo, notes, tmux

#: A peek is a glance, not a window. Enough lines to see a question and the
#: line before it; not so many that a tooltip becomes a second terminal.
PEEK_DEFAULT, PEEK_MIN, PEEK_MAX = 6, 2, 40

#: Scrollback searched to find those lines. Larger than what is shown, because
#: the frame is dropped afterwards and a pane can be almost entirely frame.
PEEK_WINDOW = 160

#: How many panes keep a cached peek. Small on purpose — this is a convenience
#: for the handful of rows a pointer actually crosses, not an index.
PEEK_CACHE = 64

#: mux -> ((mux, activity, lines), rows). Module level, because a request
#: handler is built and thrown away per request and a cache on one would be a
#: cache of exactly one lookup.
_PEEKED: dict[str, tuple[tuple, list[str]]] = {}
_PEEK_LOCK = threading.Lock()


def _peek_lines(session, lines: int) -> list[str]:
    """Capture a pane and reduce it to the lines that actually said something.

    The one implementation of that reduction, called by `/peek` and by
    `Panel.briefing()`. It used to exist only inline in `_peek` below; pulled
    out because this exact filter has already cost one subtle bug from being
    reasoned about instead of read — the `\xa0` case in `attention.is_rule`'s
    own docstring. A second inline copy is a second place for the next one of
    those to hide.

    `/peek`'s own cache against the pane's activity clock stays with it below
    rather than moving in here: a caller like `briefing()` asks once per row
    per request and has no "same pointer, same row, a moment later" to cache
    against.

    A capture failure is raised, not swallowed into `[]` here — `[]` also
    means "captured fine, pane said nothing," and a caller that cannot tell
    those apart caches a transient failure as if it were that legitimate
    answer. `/peek` needs the distinction to skip its cache write on failure;
    `briefing()` does not cache at all, so it catches this and moves on."""
    text = tmux.capture(session.mux, session.socket, lines=PEEK_WINDOW, styled=False)
    return attention.content_lines(text)[-lines:]


class SessionRoutes(BaseHTTPRequestHandler):
    """Per-session methods mixed into :class:`clique.app.Handler`."""

    # Provided by clique.app.Handler.
    panel: Any
    _json: Callable[..., Any]

    def _peek(self, session_id: str, want: str) -> None:
        """The last few lines of a pane, without opening it.

        The question this answers is "is that one waiting on me", asked about a
        session you are not looking at. Answering it today means opening the
        tab, which changes what you are looking at — and then changes it back.

        Deliberately a *pull*. Nothing captures a pane until somebody hovers
        over a row and stays there, so a sidebar of twenty sessions costs
        exactly nothing until it is asked a question. That is the same bargain
        the attention ladder makes, and it is why neither of them shows up in
        an idle panel's cost.

        Colour is stripped rather than rendered. A peek is a glance at what the
        text says; carrying ANSI across the wire would mean a second terminal
        renderer in the panel for something that is eight lines in a tooltip.
        """
        session = self.panel.store.session(session_id)
        if not session:
            return self._json({"error": "no such session"}, 404)
        try:
            lines = max(PEEK_MIN, min(int(want), PEEK_MAX))
        except (TypeError, ValueError):
            lines = PEEK_DEFAULT

        pane = self.panel.live().get(session.mux)
        if pane is None:
            return self._json({"lines": [], "alive": False, "activity": 0})

        # Cached against the pane's own activity clock, exactly as the
        # attention tier is: the entry is invalid the moment anything is
        # printed and valid forever while nothing is. Hovering back and forth
        # across a quiet sidebar is one capture, not one per pass.
        activity = int(pane.activity or 0)
        key = (session.mux, activity, lines)
        with _PEEK_LOCK:
            hit = _PEEKED.get(session.mux)
            if hit and hit[0] == key:
                return self._json({"lines": hit[1], "alive": True, "activity": activity})

        # A generous window is captured, then filtered down to `lines`, and a
        # modern CLI's pane is mostly frame — box rules, separators, an input
        # box drawn around nothing — so the decoration is dropped before the
        # trim rather than after: capturing only what is to be shown was
        # wrong once that started, since on a pane that is mostly box drawing
        # the last few raw lines can contain none of what it actually said.
        # See `_peek_lines` for the capture and the filter, shared with
        # `Panel.briefing()` so there is one place either can go wrong.
        #
        # A failed capture is not cached: it is not the same fact as "the pane
        # is alive and said nothing," and caching it as one would freeze a
        # transient failure in place until the pane's activity clock next
        # ticks, retrying nothing in between.
        try:
            rows = _peek_lines(session, lines)
        except (tmux.TmuxError, OSError):
            return self._json({"lines": [], "alive": True, "activity": activity})
        with _PEEK_LOCK:
            _PEEKED[session.mux] = (key, rows)
            # Bounded, because a panel that runs for weeks creates and destroys
            # sessions all day and every one of them would leave an entry.
            if len(_PEEKED) > PEEK_CACHE:
                for stale in list(_PEEKED)[:-PEEK_CACHE]:
                    del _PEEKED[stale]
        return self._json({"lines": rows, "alive": True, "activity": activity})

    def _attention(self, session_id: str, body: dict) -> None:
        """Let a session say, itself, that it is stuck.

        The top tier of the attention ladder, and the only one that is not a
        guess. A user wires this to whatever hook their CLI already offers —
        a Claude Code hook, a shell trap, the last line of a script — and
        CLIque believes what arrives without ever learning what sent it.

        That inversion is the point: the vendor-specific knowledge lives in
        someone's own hook config, where they can fix it the day it changes,
        instead of in this codebase where it would be a release.
        """
        session = self.panel.store.session(session_id)
        if not session:
            return self._json({"error": "no such session"}, 404)
        state = str(body.get("state") or "").strip().lower()
        if state not in ("waiting", "error", "clear"):
            return self._json({"error": 'state must be "waiting", "error" or "clear"'}, 400)
        # Optional "why", bounded — a hook's matcher name, not free text.
        note = str(body.get("note") or "").strip().lower()[:16]

        pane = self.panel.live().get(session.mux)
        # Stamped with the pane's own clock rather than the wall clock, so the
        # staleness test downstream compares like with like.
        updated = self.panel.store.update_session(
            session_id,
            signal="" if state == "clear" else state,
            signal_at=float(pane.activity if pane else 0),
            signal_note="" if state == "clear" else note,
        )
        return self._json({"ok": True, "signal": updated.signal if updated else ""})

    def _open_group(self, group_id: str, recreate: bool) -> None:
        """Start everything in a group and say what happened to each member.

        Opening tabs is the browser's job; this is the half that has to happen
        on the server, and it is a route rather than something only the panel
        can do so that a script can open a working group too.

        Three things can be true of a member and they are reported separately
        rather than folded into a count. It was already running. It was stopped
        and has been started. Or its session is gone, in which case it is only
        recreated when the caller asks: a group quietly spawning a session
        somebody deleted on purpose is worse than one that says a member is
        missing and leaves the decision alone.
        """
        group = self.panel.store.group(group_id)
        if not group:
            return self._json({"error": "no such group"}, 404)
        opened, started, missing, failed = [], [], [], []
        for member in group.members:
            sid = member.get("session") or ""
            found = self.panel.store.session(sid)
            if found and not found.archived:
                if found.mux in self.panel.live():
                    opened.append(sid)
                    continue
                result = self.panel.start_session(sid)
                if result.get("error"):
                    failed.append({"session": sid, "error": result["error"]})
                else:
                    started.append(sid)
                    opened.append(sid)
                continue
            if not recreate:
                missing.append(member)
                continue
            made = self.panel.create_session(
                {
                    "cli": member.get("cli") or "",
                    "cwd": member.get("cwd") or "",
                    "name": member.get("name") or "",
                }
            )
            if made.get("error"):
                failed.append({"session": sid, "error": made["error"]})
                continue
            # The group now points at the new session rather than the ghost,
            # or the next open would strand it again.
            self.panel.store.group_remove_session(group_id, sid)
            self.panel.store.group_add_session(group_id, made["id"])
            opened.append(made["id"])
            started.append(made["id"])
        return self._json(
            {
                "group": dataclasses.asdict(self.panel.store.group(group_id) or group),
                "sessions": opened,
                "started": started,
                "missing": missing,
                "failed": failed,
            }
        )

    def _checkpoint(self, session_id: str, body: dict) -> None:
        """Save a before-you-run snapshot of the session's git checkout.

        A record, not a lock. `gitinfo.checkpoint` reads the current HEAD, branch
        and uncommitted diff; this writes them to a timestamped file under
        `.clique-checkpoints/` so you can see — or `git apply -R` — exactly what
        an agent changed after you turned it loose. 400 when the directory is not
        a git repository."""
        session = self.panel.store.session(session_id)
        if not session or not session.cwd:
            return self._json({"error": "no such session"}, 404)
        snap = gitinfo.checkpoint(session.cwd)
        if snap is None:
            return self._json({"error": "not a git repository"}, 400)
        stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
        head = snap["head"] or "unborn"
        lines = [
            f"# CLIque checkpoint — {stamp} UTC",
            f"# branch {snap['branch']}, HEAD {head}",
            f"# {snap['shortstat'] or 'no uncommitted changes'}",
            "# Uncommitted changes are below; `git apply -R <this file>` undoes them.",
            "",
        ]
        if snap["stat"]:
            lines += [snap["stat"], ""]
        text = "\n".join(lines) + (snap["diff"] or "")
        cpdir = Path(session.cwd) / ".clique-checkpoints"
        try:
            cpdir.mkdir(parents=True, exist_ok=True)
            target = cpdir / f"checkpoint-{stamp}-{secrets.token_hex(3)}.diff"
            target.write_text(text, encoding="utf-8")
        except OSError as exc:
            return self._json({"error": f"could not save: {exc}"}, 500)
        try:
            rel = str(target.relative_to(Path(session.cwd).resolve()))
        except ValueError:
            rel = target.name
        return self._json(
            {
                "path": str(target),
                "relative": rel,
                "head": head,
                "branch": snap["branch"],
                "shortstat": snap["shortstat"],
                "empty": snap["empty"],
            },
            201,
        )

    def _export_scrollback(self, session_id: str, body: dict) -> None:
        """Write the session's scrollback to a timestamped text file.

        tmux already holds the history; this captures it unstyled (a clean log,
        no colour codes) and drops it under `.clique-exports/`, so a run can be
        kept, searched or shared after the fact. 400 when there is nothing to
        capture, which is what a session that is not running looks like: no pane
        to read."""
        session = self.panel.store.session(session_id)
        if not session or not session.cwd:
            return self._json({"error": "no such session"}, 404)
        text = tmux.capture(session.mux, session.socket, lines=50000, styled=False)
        if not text.strip():
            return self._json({"error": "nothing to export"}, 400)
        stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
        header = (
            "# CLIque scrollback export\n"
            f"# session {session.name} ({session.cli})\n"
            f"# {session.cwd}\n"
            f"# {stamp} UTC\n\n"
        )
        content = header + text
        if not content.endswith("\n"):
            content += "\n"
        outdir = Path(session.cwd) / ".clique-exports"
        try:
            outdir.mkdir(parents=True, exist_ok=True)
            target = outdir / f"scrollback-{stamp}-{secrets.token_hex(3)}.txt"
            target.write_text(content, encoding="utf-8")
        except OSError as exc:
            return self._json({"error": f"could not save: {exc}"}, 500)
        try:
            rel = str(target.relative_to(Path(session.cwd).resolve()))
        except ValueError:
            rel = target.name
        return self._json(
            {
                "path": str(target),
                "relative": rel,
                "bytes": len(content.encode("utf-8")),
                "lines": text.count("\n") + 1,
            },
            201,
        )

    def _notes_path(self, session_id: str) -> Path:
        """Where a session's notes outline lives: a sidecar `.json` under the
        panel's home, keyed by session id. Kept out of the project directory on
        purpose, so a note never shows up as an untracked file in the repo it is
        about. A pre-outline `.md` note is migrated here on first read."""
        return notes.path_for(self.panel.store.path.parent, session_id)

    def _get_notes(self, session_id: str) -> None:
        if not self.panel.store.session(session_id):
            return self._json({"error": "no such session"}, 404)
        return self._json(notes.load(self._notes_path(session_id)))

    def _save_notes(self, session_id: str, body: dict) -> None:
        """Replace (or, when emptied, delete) a session's notes outline.

        The browser owns the tree and sends the whole thing; the server's job is
        to bound it, keep the reminded flags it already knows about, and persist.
        An emptied outline is a deleted file, the same rule the blob note had."""
        if not self.panel.store.session(session_id):
            return self._json({"error": "no such session"}, 404)
        items = notes.sanitize({"items": body.get("items")})
        if len(notes.dumps(items).encode("utf-8")) > notes.MAX_BYTES:
            return self._json({"error": "notes too long"}, 413)
        path = self._notes_path(session_id)
        try:
            with notes.lock_for(path):
                if items:
                    notes.merge_reminded(notes.load(path)["items"], items)
                    notes.save(path, items)
                else:
                    for stale in (path, path.with_suffix(".md")):
                        if stale.exists():
                            stale.unlink()  # an emptied outline is a deleted note
        except OSError as exc:
            return self._json({"error": f"could not save: {exc}"}, 500)
        return self._json({"ok": True, "count": len(items)})

    def _share_cwds(self) -> set:
        """Every working directory that might hold shared files, deduped.

        Archived sessions included on purpose: their scratch files are on disk
        just the same, and the storage readout and its purge would be lying if
        they ignored them."""
        return {s.cwd for s in self.panel.store.sessions if s.cwd}

    def _send_input(self, session_id: str, body: dict) -> None:
        session = self.panel.store.session(session_id)
        if not session:
            return self._json({"error": "no such session"}, 404)
        if body.get("key"):
            tmux.send_key(session.mux, body["key"], session.socket)
        else:
            tmux.send_text(
                session.mux,
                body.get("text", ""),
                session.socket,
                enter=bool(body.get("enter", True)),
            )
        return self._json({"ok": True})

    # --------------------------------------------------------- PATCH / DELETE
