"""Whether a pane is actually doing something, rather than merely redrawing.

tmux's activity clock is the honest primitive this product is built on: it
works for every CLI without knowing anything about any of them. It has one
blind spot, and it is a bad one — **the clock counts a redraw as output**.

A CLI that animates while it waits therefore looks identical to a CLI that is
thinking. Measured on a real Grok CLI sitting completely idle with nothing
asked of it: the clock ticked roughly every two seconds for as long as it was
watched, while the captured pane was byte-identical the entire time. With the
browser's hold bridging the gaps between ticks, the working indicator span
forever on a session that had never done anything. An indicator that is always
on says nothing at all.

So the clock still decides *cheaply*, and content decides *when the clock has
been saying yes for a while*:

  - Quiet clock — not working. No capture, no cost. This is almost every
    session almost all of the time.
  - Ticking for less than SETTLE seconds — working. A short burst is obviously
    real, and paying to verify it would be paying on the common path.
  - Ticking for longer than that — capture the visible screen and compare. Text
    that has not changed in STILL seconds is not work, whatever the clock says.

One capture per suspicious session per poll, about 6 ms each: with eight
sessions all stuck in the pathological state that is under two per cent of one
core, and with none of them it is nothing. Only the broken case pays.

The trade, stated plainly: a CLI that is genuinely busy while redrawing a
*byte-identical* screen will be reported idle. A spinner is not that — a
spinner changes characters — and neither is streamed output. It costs the rare
case to fix the common one, and the alternative was an indicator nobody could
believe.
"""

from __future__ import annotations

import hashlib
import threading
import time

from . import tmux

#: How recently the clock must have ticked. Unchanged from when this was the
#: whole of the answer.
WINDOW = 2.0

#: How long a pane may claim to be busy before it has to prove it. Long enough
#: that ordinary bursty output is never verified, short enough that a stuck
#: indicator settles within a poll or two of someone noticing.
SETTLE = 8.0

#: How long identical content still counts as work. A CLI can legitimately
#: pause mid-render; this is the grace before "unchanged" means "idle".
STILL = 4.0

#: Lines captured to compare. The visible screen is what changes when anything
#: is happening, and capturing scrollback would be paying for history that by
#: definition is not moving.
LINES = 0

_lock = threading.Lock()
_since: dict[str, float] = {}            # mux -> when it started looking busy
_seen: dict[str, tuple[str, float]] = {}  # mux -> (digest, when it last changed)


def _digest(mux: str, socket: str | None) -> str | None:
    try:
        text = tmux.capture(mux, socket, lines=LINES, styled=False)
    except (tmux.TmuxError, OSError):
        return None
    return hashlib.sha1(text.encode(), usedforsecurity=False).hexdigest()


def _forget(mux: str) -> None:
    _since.pop(mux, None)
    _seen.pop(mux, None)


def busy(pane, socket: str | None, now: float | None = None) -> bool:
    """Whether this pane is doing something a person would call working."""
    now = time.time() if now is None else now
    if now - (pane.activity or 0) >= WINDOW:
        with _lock:
            _forget(pane.mux)
        return False

    with _lock:
        started = _since.setdefault(pane.mux, now)
        if now - started < SETTLE:
            return True

    digest = _digest(pane.mux, socket)
    if digest is None:
        return True          # cannot tell; the clock is the only witness left

    with _lock:
        previous, changed = _seen.get(pane.mux, (None, now))
        if digest != previous:
            _seen[pane.mux] = (digest, now)
            return True
        _seen[pane.mux] = (digest, changed)
        return now - changed < STILL


def settled(pane, now: float | None = None) -> bool:
    """Whether it is time to look at what the pane is *saying*.

    The activity clock is a bad witness for this. A permission prompt that
    blinks, or a choice list whose selected row pulses, ticks the clock the
    same way streamed output does — so "output in the last two seconds" is
    also the animated-question case. After SETTLE we already capture to
    verify work; that is also when a question is allowed to surface. Before
    that, a burst is just a burst.
    """
    now = time.time() if now is None else now
    if now - (pane.activity or 0) >= WINDOW:
        return True
    with _lock:
        started = _since.get(pane.mux)
    if started is None:
        return False
    return now - started >= SETTLE


def forget(live: set[str]) -> None:
    """Drop everything about sessions that are gone.

    Called with the set of panes that still exist. Without it these two maps
    are keyed by session name in a process that is meant to run for weeks.
    """
    with _lock:
        for mux in [m for m in _since if m not in live]:
            _forget(mux)
        for mux in [m for m in _seen if m not in live]:
            _forget(mux)
