"""Whether a session is waiting on a person, in three tiers that degrade.

The question the whole product exists to answer is *which of these needs me*,
and a session waiting for an answer looks exactly like one that finished. The
tmux activity clock cannot tell them apart — it only knows output stopped.

Three tiers, each optional, each falling back to the one below:

1. **The activity clock.** Shipped, works for any CLI, and only ever says
   working or quiet.
2. **Patterns over the pane.** Regexes declared per CLI in ``clis.toml``,
   matched against the last lines of the pane once it goes quiet. A CLI with
   no patterns simply skips this tier.
3. **The session says so itself.** ``POST /api/sessions/<id>/attention`` —
   wired by the user to whatever hook their own CLI already offers. Exact
   where the tier above is a guess.

Note what is *not* here: any knowledge of a vendor. Tier 2 reads config a
person can edit; tier 3 receives an assertion and believes it. The line to
hold is that patterns stay strings in a TOML file. The moment this parses a
vendor's structured output, it has crossed.

Cost matters, because this runs against every session on a poll. A pane is
only captured when a session has *gone quiet* and has not been looked at since
its last output — a session producing output is not waiting on anyone, and a
session that has been quiet for an hour has not changed its mind. In practice
that is one capture per session per stop, not one per poll.
"""

from __future__ import annotations

import re
import threading

from . import tmux

#: How far back to look. A prompt asking for an answer is the last thing on
#: the pane; scanning further finds the *previous* prompt and reports a
#: question that has already been answered.
LINES = 40

#: Compiled patterns, keyed by the strings they came from, so a hot-reloaded
#: clis.toml does not mean recompiling on every poll.
_compiled: dict[tuple[str, ...], list[re.Pattern]] = {}

#: (mux, activity) -> verdict. The activity clock is the cache key, which is
#: what makes this cheap: the entry is invalid the moment the pane produces
#: anything, and valid forever while it does not.
_seen: dict[str, tuple[int, str]] = {}
_lock = threading.Lock()


#: Characters a line can be made *entirely* of and still be saying nothing:
#: box drawing, block elements, rules, separators and bare prompt marks. A line
#: with one real word in it is never dropped, however much frame surrounds it.
_RULE_CHARS = frozenset(
    " \t─━│┃┄┅┆┇┈┉┊┋┌┐└┘├┤┬┴┼╌╍╎╏═║╔╗╚╝╠╣╦╩╬▀▄█▌▐░▒▓▁▂▃▅▆▇"
    "-_=~*.·•+<>|/\\[](){}"
    # The lookalikes are the point: these are the prompt glyphs CLIs actually
    # draw, and a line that is only one of them is a bare prompt.
    "❯❮›‹»«▸▶◂◀⟩⟨"  # noqa: RUF001
)


def is_rule(line: str) -> bool:
    """Whether this line is frame rather than content."""
    return not (set(line) - _RULE_CHARS)


def content_lines(text: str) -> list[str]:
    """The lines that said something, in order."""
    return [row.rstrip() for row in text.splitlines()
            if row.strip() and not is_rule(row)]


#: How much scrollback to search for a line worth quoting. A pane can be almost
#: entirely frame, so the window is far wider than what is kept.
SAYING_WINDOW = 160

#: (mux) -> (activity, line). Same bargain as the verdict cache above: a pane
#: that has stopped producing output cannot change what it last said.
_said: dict[str, tuple[int, str]] = {}


def saying(mux: str, activity: int, socket: str | None = tmux.SOCKET) -> str:
    """The last thing this pane actually said, for a session that wants you.

    The status ring says a session is waiting; this says what it is waiting
    *for*, which is the difference between knowing you have to go and look and
    not having to. It is one line, in the row, rather than a popup over the
    top of everything — a preview that has to be summoned, positioned and
    layered is three problems, and the answer fits where the path already is.

    Only ever called for sessions that need a person, so this is a handful of
    captures rather than one per session. A waiting pane's activity clock is
    frozen, so the cache holds until something actually happens.
    """
    with _lock:
        cached = _said.get(mux)
        if cached and cached[0] == activity:
            return cached[1]
    try:
        text = tmux.capture(mux, socket, lines=SAYING_WINDOW, styled=False)
    except (tmux.TmuxError, OSError):
        return ""
    rows = content_lines(text)
    line = _worth_quoting(rows)
    with _lock:
        _said[mux] = (activity, line)
        if len(_said) > 512:
            for stale in list(_said)[:256]:
                _said.pop(stale, None)
    return line


#: A line that is a prompt waiting for input rather than something said. Not a
#: vendor's prompt — a terminal's: a short line ending in one of the characters
#: that has meant "your turn" since sh.
_PROMPT = re.compile(r"[#$>❯»›:]\s*$")  # noqa: RUF001 — the lookalikes are the prompts


def _worth_quoting(rows: list[str], back: int = 4) -> str:
    """The last line that says something, skipping the prompt under it.

    The bottom of a pane is usually the cursor sitting on a prompt, and
    quoting that back tells you nothing you did not know — the question is on
    the line above it. Only a few lines are skipped: past that, a run of
    prompt-looking lines is the content.
    """
    for line in reversed(rows[-back:] if len(rows) > back else rows):
        if len(line) < 80 and _PROMPT.search(line):
            continue
        return line[:200]
    return rows[-1][:200] if rows else ""


def _patterns(raw: list[str]) -> list[re.Pattern]:
    key = tuple(raw)
    got = _compiled.get(key)
    if got is None:
        got = []
        for item in raw:
            try:
                got.append(re.compile(item, re.MULTILINE))
            except re.error:
                # A bad regex in someone's config should cost them that one
                # pattern, not the tier and not the panel.
                continue
        _compiled[key] = got
    return got


def detect(mux: str, activity: int, waiting: list[str], errors: list[str],
           socket: str | None = tmux.SOCKET) -> str:
    """"waiting", "error" or "" for a quiet pane. Cached against `activity`.

    Errors win over waiting: a CLI that failed and then offered a prompt is
    reporting the failure, and that is the more useful of the two to surface.
    """
    if not waiting and not errors:
        return ""
    with _lock:
        cached = _seen.get(mux)
        if cached and cached[0] == activity:
            return cached[1]
    try:
        # Unstyled: escape sequences between the words would defeat every
        # pattern anyone would think to write.
        text = tmux.capture(mux, socket, lines=LINES, styled=False)
    except tmux.TmuxError:
        return ""
    tail = "\n".join(text.splitlines()[-LINES:])
    verdict = ""
    if any(p.search(tail) for p in _patterns(errors)):
        verdict = "error"
    elif any(p.search(tail) for p in _patterns(waiting)):
        verdict = "waiting"
    with _lock:
        _seen[mux] = (activity, verdict)
        # Unbounded growth would be a leak in a process that runs for weeks.
        if len(_seen) > 512:
            for stale in list(_seen)[:256]:
                _seen.pop(stale, None)
    return verdict


def forget(mux: str) -> None:
    """Drop a session's cached verdict — called when it is killed or adopted."""
    with _lock:
        _seen.pop(mux, None)
