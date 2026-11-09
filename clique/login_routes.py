"""Password and pairing-code sign-in route for the HTTP request handler."""

from __future__ import annotations

import secrets
import time
import urllib.parse
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler
from typing import Any

from .auth import landing_page, login_page

#: A password form is a few hundred bytes. This is the only body an
#: unauthenticated caller gets to send, so it is capped hard and separately.
MAX_LOGIN_BYTES = 8 * 1024


class LoginRoutes(BaseHTTPRequestHandler):
    """Sign-in methods mixed into :class:`clique.app.Handler`."""

    # Provided by clique.app.Handler.
    panel: Any
    _send: Callable[..., Any]
    _secure: Callable[..., Any]

    def _login(self) -> None:
        """POST / : a password or a pairing code, for a browser cookie."""
        # Bounded, and bounded *before* the read. This is the one body an
        # unauthenticated caller can send, so an unbounded Content-Length
        # here is a thread and an allocation anyone can claim.
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length < 0 or length > MAX_LOGIN_BYTES:
            self.close_connection = True
            return self._send(413, b"too large", "text/plain")
        form = urllib.parse.parse_qs(self.rfile.read(length).decode(errors="replace"))
        attempt = (form.get("password") or [""])[0]
        pair_code = (form.get("pair") or [""])[0]
        who = self.client_address[0] if self.client_address else "?"
        throttled = self._reserve_attempt(who)
        # Check even when throttled, so a correct password or code always
        # gets through. Behind a tunnel every request arrives from the same
        # loopback address, so a per-IP lockout would lock out the only
        # legitimate user along with the attacker — which is a denial of
        # service dressed as a protection.
        #
        # Pairing is not new authority: the same code already buys a full
        # bearer token through /api/pair/claim, so redeeming it for a
        # browser cookie grants nothing it did not already grant.
        if not attempt and pair_code:
            accepted = self.panel.pairing.redeem(pair_code)
            refused = "That code is no longer valid."
        else:
            accepted = self.panel.auth.check_password(attempt)
            refused = "Wrong password."
        if not accepted:
            if throttled:
                time.sleep(2.0)
                nonce = secrets.token_urlsafe(16)
                return self._send(
                    429,
                    login_page("Too many attempts. Wait a minute.", nonce),
                    "text/html; charset=utf-8",
                    nonce=nonce,
                )
            # Deliberately slow, and counted: this endpoint is reachable by
            # anyone who can reach the tunnel, and guessing should be both
            # expensive and self-limiting.
            time.sleep(1.0)
            nonce = secrets.token_urlsafe(16)
            return self._send(
                401,
                login_page(refused, nonce),
                "text/html; charset=utf-8",
                nonce=nonce,
            )
        with self.panel.failures_lock:
            self.panel.failures.pop(who, None)
        token = self.panel.auth.issue()
        # Secure whenever the external leg is TLS. A forged X-Forwarded-Proto
        # can only *restrict* where the cookie is sent (https-only), never
        # leak it, so honour it without requiring CLIQUE_TRUST_PROXY — a
        # tunnel operator often has not set that even though the browser
        # reached us over https.
        over_tls = self.headers.get("X-Forwarded-Proto", "").lower() == "https"
        cookie = self.panel.auth.cookie_header(token, self._secure() or over_tls)
        # 200 with a landing page, not a 3xx. See auth.LANDING for why a
        # Location header cannot be made correct from inside this process.
        nonce = secrets.token_urlsafe(16)
        return self._send(
            200,
            landing_page(nonce),
            "text/html; charset=utf-8",
            {"Set-Cookie": cookie},
            nonce=nonce,
        )

    #: Wrong passwords tolerated per address before a cooling-off period.
    MAX_FAILURES = 8
    FAILURE_WINDOW = 300

    def _reserve_attempt(self, who: str) -> bool:
        """Count this attempt as a failure and say whether it was already over
        the limit, in one locked step. Concurrent attempts each see the others'
        reservations; a success clears the entry."""
        now = time.time()
        cutoff = now - self.FAILURE_WINDOW
        with self.panel.failures_lock:
            failures = self.panel.failures
            for addr in [a for a, ts in failures.items() if not ts or ts[-1] <= cutoff]:
                del failures[addr]  # aged out: no entry lingers per address
            recent = [t for t in failures.get(who, []) if t > cutoff]
            over = len(recent) >= self.MAX_FAILURES
            recent.append(now)
            failures[who] = recent
            return over
