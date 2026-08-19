"""API tokens, so a coding agent can drive the panel without a browser.

Why these are not the login password
------------------------------------
The password exists to let a human through a login form. A token exists to let
a *program* call the API unattended. Sharing one credential between the two
means an agent's config file holds the key to the human's session, and
revoking a leaked agent key logs the human out of their phone. So: separate
credentials, separately revocable, and a token can be read-only.

Storage
-------
Only a SHA-256 of each token is written to disk. A leaked `tokens.json` then
gives an attacker a list of names and dates, not a working key — the same
reason a password file stores hashes. The plaintext is shown exactly once, at
creation, because there is no way to recover it later and pretending otherwise
would invite storing it somewhere worse.

Tokens are sent as `Authorization: Bearer <token>`. Browsers never attach that
header automatically, which is precisely why token-authenticated requests are
exempt from the CSRF origin check that cookie requests must pass.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

PREFIX = "mxp_"
SCOPES = ("read", "write")


@dataclass
class Token:
    id: str
    name: str
    hash: str
    scopes: list[str] = field(default_factory=lambda: ["read", "write"])
    created: float = 0.0
    last_used: float = 0.0

    def allows(self, scope: str) -> bool:
        return scope in self.scopes


def _digest(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


class TokenStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.tokens: list[Token] = []
        self._load()

    def _load(self) -> None:
        try:
            raw = json.loads(self.path.read_text())
        except (OSError, ValueError):
            raw = []
        self.tokens = [
            Token(**{k: v for k, v in t.items() if k in Token.__annotations__})
            for t in raw if isinstance(t, dict)
        ]

    def _write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        # 0600 from the moment it exists: this file names every integration
        # that can reach the API.
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            json.dump([asdict(t) for t in self.tokens], fh, indent=2)
        tmp.replace(self.path)

    def create(self, name: str, scopes: list[str] | None = None) -> tuple[Token, str]:
        """Mint a token. The plaintext is returned once and never stored."""
        raw = PREFIX + secrets.token_urlsafe(32)
        token = Token(
            id="tk_" + secrets.token_hex(4),
            name=name.strip()[:60] or "unnamed",
            hash=_digest(raw),
            scopes=[s for s in (scopes or list(SCOPES)) if s in SCOPES] or ["read"],
            created=time.time(),
        )
        self.tokens.append(token)
        self._write()
        return token, raw

    def revoke(self, token_id: str) -> bool:
        found = next((t for t in self.tokens if t.id == token_id), None)
        if not found:
            return False
        self.tokens.remove(found)
        self._write()
        return True

    def verify(self, raw: str | None) -> Token | None:
        """Match a presented token. Constant-time, and it records the use."""
        if not raw or not raw.startswith(PREFIX):
            return None
        presented = _digest(raw)
        for token in self.tokens:
            # compare_digest over the hashes, so a timing side channel cannot
            # be used to walk a valid token out of the server.
            if hmac.compare_digest(token.hash, presented):
                token.last_used = time.time()
                # Deliberately not written on every call — last_used is a
                # convenience, and a disk write per API request is not.
                return token
        return None

    def listing(self) -> list[dict]:
        """Safe to show: names and dates, never a hash."""
        return [
            {"id": t.id, "name": t.name, "scopes": t.scopes,
             "created": t.created, "last_used": t.last_used}
            for t in sorted(self.tokens, key=lambda t: t.created)
        ]

    def touch(self) -> None:
        """Persist last_used timestamps. Called on shutdown, not per request."""
        self._write()
