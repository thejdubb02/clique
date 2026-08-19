"""Password gate.

Adapted from CodemanPanel's auth, with two deliberate differences.

**The password is mandatory.** CodemanPanel could run open because the worst a
stranger could do was read session titles. This serves a *terminal* running as
root, so an open instance is a remote shell for anyone on the tailnet. Refusing
to start is the only honest default.

**The signing secret is persisted.** CodemanPanel mints a fresh secret per
process, so a restart logs everyone out — fine for a tool you open occasionally,
wrong for one under systemd with `Restart=on-failure`, where a crash at 3am
would silently log you out of your phone.

A login page rather than HTTP Basic: Basic cannot be styled, cannot be logged
out of, and pops a dialog that behaves badly on phones.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path

COOKIE_NAME = "muxpanel"
DEFAULT_TTL = 30 * 24 * 3600  # a month; personal tool, reached over a tailnet


class AuthDisabled(Exception):
    """Raised at startup rather than serving a root terminal to the world."""


def _load_secret(path: Path) -> bytes:
    """Read the signing secret, creating it 0600 on first run."""
    try:
        data = path.read_bytes()
        if len(data) >= 32:
            return data
    except OSError:
        pass
    secret = secrets.token_bytes(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Written before the chmod would matter, so create it restricted.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(secret)
    return secret


class Auth:
    def __init__(self, password: str, secret_path: Path, ttl: int = DEFAULT_TTL) -> None:
        if not password:
            raise AuthDisabled(
                "muxpanel serves a terminal running as root and will not start "
                "without a password. Set MUXPANEL_PASSWORD or pass --password."
            )
        self.password = password
        self.ttl = ttl
        self._secret = _load_secret(secret_path)

    def issue(self) -> str:
        payload = json.dumps({"exp": int(time.time()) + self.ttl}).encode()
        body = base64.urlsafe_b64encode(payload).decode().rstrip("=")
        signature = hmac.new(self._secret, body.encode(), hashlib.sha256).hexdigest()
        return f"{body}.{signature}"

    def valid(self, token: str | None) -> bool:
        if not token or "." not in token:
            return False
        body, _, signature = token.partition(".")
        expected = hmac.new(self._secret, body.encode(), hashlib.sha256).hexdigest()
        # compare_digest, not ==, so a timing side channel cannot leak the mac.
        if not hmac.compare_digest(signature, expected):
            return False
        try:
            padding = "=" * (-len(body) % 4)
            claims = json.loads(base64.urlsafe_b64decode(body + padding))
            return int(claims.get("exp", 0)) > time.time()
        except (ValueError, TypeError):
            return False

    def check_password(self, attempt: str) -> bool:
        return hmac.compare_digest(attempt or "", self.password)

    @staticmethod
    def token_from_cookies(header: str | None) -> str | None:
        for part in (header or "").split(";"):
            name, _, value = part.strip().partition("=")
            if name == COOKIE_NAME:
                return value
        return None

    def cookie_header(self, token: str, secure: bool, path: str = "/") -> str:
        flags = (f"{COOKIE_NAME}={token}; HttpOnly; SameSite=Lax; "
                 f"Path={path}; Max-Age={self.ttl}")
        return flags + ("; Secure" if secure else "")


LOGIN_PAGE = """<!doctype html>
<html lang="en">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>muxpanel</title>
<script>
  // muxpanel is mounted under /mux by `tailscale serve`, which strips the
  // prefix before the request arrives — so the server genuinely believes it is
  // at the root and cannot build a correct absolute URL. Only the browser knows
  // where this page lives, which is why the form target is resolved here.
  //
  // Without it, signing in from ".../mux" (no trailing slash) posted to "/" and
  // the redirect landed on Codeman, which is exactly what it looked like: the
  // panel "opening Codeman".
  (function () {
    var path = location.pathname;
    if (!path.endsWith("/")) path += "/";
    document.write('<base href="' + path.replace(/"/g, "") + '">');
  })();
</script>
<style>
  :root { color-scheme: dark; }
  body { margin:0; min-height:100vh; display:grid; place-items:center;
         background:#1e1e1e; color:#ccc;
         font:14px/1.6 -apple-system,"Segoe UI",system-ui,sans-serif; }
  form { width:min(320px,88vw); }
  h1 { font-size:15px; font-weight:600; margin:0 0 4px; letter-spacing:.02em; }
  p  { margin:0 0 18px; color:#8b8b8b; font-size:13px; }
  input,button { width:100%; font:inherit; border-radius:3px; }
  input { padding:9px 10px; background:#3c3c3c; color:#ccc;
          border:1px solid transparent; margin-bottom:10px; }
  input:focus { outline:none; border-color:#0078d4; }
  button { padding:9px; background:#0078d4; color:#fff; border:0; cursor:pointer; }
  .err { color:#f85149; font-size:13px; margin:0 0 12px; }
</style>
<form method="post" action="./">
  <h1>muxpanel</h1>
  <p>Sign in to reach your sessions.</p>
  __ERROR__
  <input type="password" name="password" placeholder="Password"
         autocomplete="current-password" autofocus required>
  <button type="submit">Sign in</button>
</form>
</html>
"""


def login_page(error: str = "") -> bytes:
    marker = f'<p class="err">{error}</p>' if error else ""
    return LOGIN_PAGE.replace("__ERROR__", marker).encode()


#: Served on a successful login instead of a 3xx.
#:
#: A Location header is resolved by the browser against the request URL, and
#: the server cannot help it: `tailscale serve` strips the /mux prefix, so this
#: process genuinely believes it is mounted at the root. From ".../mux" a
#: relative "./" resolves to "/" — the site root, which is Codeman. That is not
#: a redirect we can fix from here with any value.
#:
#: So the landing is done in the browser, which is the only party that knows
#: the real path, and it normalises the trailing slash on the way.
LANDING = """<!doctype html>
<html lang="en">
<meta charset="utf-8">
<title>muxpanel</title>
<script>
  (function () {
    var path = location.pathname;
    if (!path.endsWith("/")) path += "/";
    location.replace(path);
  })();
</script>
<noscript>Signed in. <a href="./">Continue</a></noscript>
</html>
"""


def landing_page() -> bytes:
    return LANDING.encode()
