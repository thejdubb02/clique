"""Hand a token to a device nobody wants to type forty characters into.

An API token is `mxp_` plus forty-odd random characters. On a laptop that is a
copy and paste. On a phone, or a TV, or anything with a soft keyboard, it is a
transcription error waiting to happen, and it is the reason a native client
has been impossible to set up nicely.

WHY THIS IS NOT THE THING app.py WARNS ABOUT. Tokens are deliberately minted
on the box, never over the network, because an endpoint that creates
credentials turns any other hole into permanent access. That rule holds here.
The *authorisation* still happens on the box: somebody already inside the
panel asks for a code, and the code is shown there. What crosses the network
is only the redemption of a short secret that the box chose and displayed, and
it is single use, expires in two minutes, and there is never more than one
outstanding. An attacker who could redeem a code could already read the screen
it was printed on.

The alphabet has no 0/O, 1/I/L or U. Those are the characters people
mistranscribe, and a pairing code that fails because a zero looked like an O
teaches nobody anything. Shown in two groups of four; the groups are cosmetic
and the separator is ignored on the way back in.
"""

from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass, field

ALPHABET = "23456789ABCDEFGHJKMNPQRSTVWXYZ"
LENGTH = 8

#: Long enough to walk to the other device, short enough that a code left on a
#: screen is not a standing invitation.
LIFETIME = 120.0

#: Wrong guesses that burn the code. Typing it wrong twice is a person; five
#: times is not, and the cost of being wrong is that they ask for a new code.
MAX_TRIES = 5

#: A ceiling across all codes, so guessing cannot be parallelised by asking
#: for code after code. Attempts, not codes: the expensive thing to allow is
#: the guess.
WINDOW = 60.0
WINDOW_TRIES = 12


def _normalise(text: str) -> str:
    """Accept what a person actually types: spaces, dashes, lower case."""
    return "".join(c for c in (text or "").upper() if c in ALPHABET)


@dataclass
class Pending:
    code: str
    expires: float
    tries: int = 0


@dataclass
class Desk:
    """One outstanding pairing code, and the counters that keep it honest."""

    _lock: threading.Lock = field(default_factory=threading.Lock)
    _open: Pending | None = None
    _attempts: list[float] = field(default_factory=list)

    def start(self) -> tuple[str, int]:
        """Mint a code, replacing any code already waiting.

        One at a time on purpose. A pile of live codes is a bigger target for
        no benefit: pairing a second device is a second trip to the panel,
        which takes a moment and happens rarely.
        """
        code = "".join(secrets.choice(ALPHABET) for _ in range(LENGTH))
        with self._lock:
            self._open = Pending(code=code, expires=time.time() + LIFETIME)
        return code, int(LIFETIME)

    def waiting(self) -> int:
        """Seconds left on the outstanding code, or 0 if there is not one."""
        with self._lock:
            if not self._open:
                return 0
            return max(0, int(self._open.expires - time.time()))

    def cancel(self) -> None:
        with self._lock:
            self._open = None

    def redeem(self, typed: str) -> bool:
        """True exactly once, for the right code, before it expires."""
        offered = _normalise(typed)
        now = time.time()
        with self._lock:
            self._attempts = [t for t in self._attempts if now - t < WINDOW]
            if len(self._attempts) >= WINDOW_TRIES:
                return False
            self._attempts.append(now)

            pending = self._open
            if not pending or now >= pending.expires:
                self._open = None
                return False
            # Compared in constant time even though the code is short-lived:
            # the timing of a comparison does not care how long the secret
            # lives, and this is the one place a remote caller gets to probe.
            if not secrets.compare_digest(offered, pending.code):
                pending.tries += 1
                if pending.tries >= MAX_TRIES:
                    self._open = None
                return False
            self._open = None  # single use
            return True


def grouped(code: str) -> str:
    """`ABCDEFGH` -> `ABCD-EFGH`, for reading aloud and typing."""
    return code[:4] + "-" + code[4:] if len(code) == LENGTH else code
