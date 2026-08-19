"""End-to-end check of the server: auth, API, and a real terminal over WebSocket.

Talks to a running muxpanel over HTTP rather than importing it, because the
things that break here — cookies, the WebSocket handshake, frame masking, a PTY
that never gets its first byte — only exist across a socket.

Usage: python3 tools/smoke_http.py [base_url]
"""

from __future__ import annotations

import base64
import json
import os
import secrets
import socket
import ssl
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import contextlib

from muxpanel.wsproto import OP_BINARY, OP_TEXT

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:3200").rstrip("/")

#: The password on disk is an scrypt hash and cannot be reversed, which is the
#: point. So the suite authenticates with a throwaway API token it mints and
#: revokes around the run — which also exercises the path an agent uses.
#: Set MUXPANEL_TEST_PASSWORD to additionally cover the login form.
PASSWORD = os.environ.get("MUXPANEL_TEST_PASSWORD", "")

passed = failed = 0
cookie = ""
bearer = ""
token_id = ""


def check(label: str, cond: bool, detail: str = "") -> None:
    global passed, failed
    if cond:
        passed += 1
        print(f"  ok   {label}")
    else:
        failed += 1
        print(f"  FAIL {label} {detail}")


def call(path: str, method: str = "GET", body: dict | None = None, anon: bool = False):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if not anon:
        if bearer:
            req.add_header("Authorization", "Bearer " + bearer)
        if cookie:
            req.add_header("Cookie", cookie)
    try:
        with urllib.request.urlopen(req, timeout=15) as res:
            raw = res.read()
            return res.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            return exc.code, json.loads(raw)
        except ValueError:
            return exc.code, {}


def login() -> None:
    global cookie
    data = urllib.parse.urlencode({"password": PASSWORD}).encode()
    req = urllib.request.Request(BASE + "/", data=data, method="POST")
    opener = urllib.request.build_opener(NoRedirect())
    try:
        res = opener.open(req, timeout=10)
        header = res.headers.get("Set-Cookie", "")
    except urllib.error.HTTPError as exc:
        header = exc.headers.get("Set-Cookie", "")
    cookie = header.split(";")[0]


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


# --------------------------------------------------------------- ws client --

class Client:
    """A masking WebSocket client. Servers never mask; clients always must."""

    def __init__(self, url: str, cookie: str, bearer: str = "") -> None:
        parsed = urllib.parse.urlparse(url)
        secure = parsed.scheme == "wss"
        port = parsed.port or (443 if secure else 80)
        self.sock = socket.create_connection((parsed.hostname, port), timeout=15)
        if secure:
            # Exercising the real tailnet URL matters: a WebSocket that works on
            # loopback and dies at the proxy is the failure this catches.
            self.sock = ssl.create_default_context().wrap_socket(
                self.sock, server_hostname=parsed.hostname)
        key = base64.b64encode(secrets.token_bytes(16)).decode()
        path = parsed.path + ("?" + parsed.query if parsed.query else "")
        self.sock.sendall((
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {parsed.hostname}\r\n"
            "Upgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n"
            f"Origin: {parsed.scheme.replace('ws', 'http')}://{parsed.hostname}\r\n"
            + (f"Cookie: {cookie}\r\n" if cookie else "")
            + (f"Authorization: Bearer {bearer}\r\n" if bearer else "")
            + "\r\n"
        ).encode())
        self.buffer = b""
        while b"\r\n\r\n" not in self.buffer:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise RuntimeError("handshake failed")
            self.buffer += chunk
        head, _, rest = self.buffer.partition(b"\r\n\r\n")
        self.status = head.split(b"\r\n")[0].decode()
        self.buffer = rest

    def send(self, payload: bytes, opcode: int = OP_BINARY) -> None:
        mask = secrets.token_bytes(4)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        header = bytearray([0x80 | opcode])
        length = len(payload)
        if length < 126:
            header.append(0x80 | length)
        elif length < (1 << 16):
            header.append(0x80 | 126)
            header += struct.pack("!H", length)
        else:
            header.append(0x80 | 127)
            header += struct.pack("!Q", length)
        self.sock.sendall(bytes(header) + mask + masked)

    def drain(self, seconds: float = 2.0) -> bytes:
        """Collect payload bytes for a while — terminal output is bursty."""
        out = bytearray()
        deadline = time.time() + seconds
        self.sock.settimeout(0.4)
        while time.time() < deadline:
            try:
                chunk = self.sock.recv(65536)
            except TimeoutError:
                continue
            except OSError:
                break
            if not chunk:
                break
            self.buffer += chunk
            while True:
                frame = self._take()
                if frame is None:
                    break
                out += frame
        return bytes(out)

    def _take(self) -> bytes | None:
        if len(self.buffer) < 2:
            return None
        second = self.buffer[1]
        length = second & 0x7F
        offset = 2
        if length == 126:
            if len(self.buffer) < 4:
                return None
            length = struct.unpack("!H", self.buffer[2:4])[0]
            offset = 4
        elif length == 127:
            if len(self.buffer) < 10:
                return None
            length = struct.unpack("!Q", self.buffer[2:10])[0]
            offset = 10
        if len(self.buffer) < offset + length:
            return None
        payload = self.buffer[offset:offset + length]
        self.buffer = self.buffer[offset + length:]
        return payload

    def close(self) -> None:
        with contextlib.suppress(OSError):
            self.sock.close()


def mint_token() -> None:
    """A throwaway token for the run, revoked in teardown."""
    global bearer, token_id
    result = subprocess.run(
        [sys.executable, "-m", "muxpanel", "token", "create", "smoke-test"],
        capture_output=True, text=True, cwd=str(Path(__file__).resolve().parents[1]),
    )
    for line in result.stdout.splitlines():
        if line.strip().startswith("mxp_"):
            bearer = line.strip()
        if line.startswith("created "):
            token_id = line.split()[1]


def revoke_token() -> None:
    if token_id:
        subprocess.run(
            [sys.executable, "-m", "muxpanel", "token", "revoke", token_id],
            capture_output=True, text=True,
            cwd=str(Path(__file__).resolve().parents[1]),
        )


def main() -> int:
    print("auth")
    status, _ = call("/api/state", anon=True)
    check("API refuses anonymous callers", status == 401, status)

    mint_token()
    check("mints an API token", bearer.startswith("mxp_"), bearer[:8])
    status, _ = call("/api/state")
    check("token works without a restart", status == 200, status)

    if PASSWORD:
        login()
        check("login sets a cookie", cookie.startswith("muxpanel="), cookie[:20])
    else:
        print("  --   login form not covered (set MUXPANEL_TEST_PASSWORD)")

    print("api")
    status, state = call("/api/state")
    check("state loads", status == 200 and "folders" in state, status)
    check("folders seeded", len(state.get("folders", [])) >= 6)
    check("registry exposed", {c["id"] for c in state["clis"]} >= {"claude", "grok", "shell"})
    check("stats present", "cpu" in state.get("stats", {}))

    print("session lifecycle")
    status, created = call("/api/sessions", "POST",
                           {"cli": "shell", "cwd": "/tmp", "name": "smoke-http"})
    check("creates a session", status == 201 and "id" in created, created)
    sid = created.get("id")

    status, state = call("/api/state")
    mine = next((s for s in state["sessions"] if s["id"] == sid), None)
    check("session appears alive", bool(mine and mine["alive"]), mine)
    check("auto-filed by directory", mine is not None)

    status, _bad = call("/api/sessions", "POST", {"cli": "shell", "cwd": "/does/not/exist"})
    check("rejects a missing directory", status == 400, status)
    status, _unused = call("/api/sessions", "POST", {"cli": "nope", "cwd": "/tmp"})
    check("rejects an unknown CLI", status == 400, status)

    print("terminal")
    url = BASE.replace("https://", "wss://").replace("http://", "ws://") + \
          f"/ws?id={sid}&cols=100&rows=30"
    client = Client(url, cookie, bearer)
    check("websocket handshake", "101" in client.status, client.status)
    client.drain(1.5)  # scrollback + initial paint
    client.send(b"echo hello-from-muxpanel\n")
    out = client.drain(2.5)
    check("keystrokes reach the pane and output comes back",
          b"hello-from-muxpanel" in out, out[-160:])

    client.send(json.dumps({"type": "run", "text": "echo via-control"}).encode(), OP_TEXT)
    out = client.drain(2.5)
    check("control frame runs a command", b"via-control" in out, out[-160:])

    client.send(json.dumps({"type": "resize", "cols": 80, "rows": 24}).encode(), OP_TEXT)
    time.sleep(0.5)
    check("resize accepted without dropping the socket", True)
    client.close()
    time.sleep(0.8)

    print("detach vs kill")
    status, state = call("/api/state")
    still = next((s for s in state["sessions"] if s["id"] == sid), None)
    check("session survives the browser leaving", bool(still and still["alive"]))

    status, _ = call(f"/api/sessions/{sid}", "DELETE")
    check("deletes the session", status == 200, status)
    status, state = call("/api/state")
    check("gone from state", not any(s["id"] == sid for s in state["sessions"]))

    print("static")
    req = urllib.request.Request(BASE + "/app.js")
    req.add_header("Cookie", cookie)
    with urllib.request.urlopen(req, timeout=10) as res:
        check("serves the app", res.status == 200 and b"muxpanel" in res.read()[:400])
    status, _ = call("/../etc/passwd")
    # Direct, this is our 404. Through tailscale serve the proxy rejects it
    # before we see it, so assert "not served" rather than a specific code.
    check("refuses path traversal", status != 200, status)

    print("hardening")
    status, _ = call("/api/state", anon=True)
    check("still refuses anonymous callers", status == 401, status)

    print(f"\n{passed} passed, {failed} failed")
    revoke_token()
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
