"""End-to-end check of the server: auth, API, and a real terminal over WebSocket.

Talks to a running CLIque over HTTP rather than importing it, because the
things that break here — cookies, the WebSocket handshake, frame masking, a PTY
that never gets its first byte — only exist across a socket.

Usage: python3 tools/smoke_http.py [base_url]
"""

from __future__ import annotations

import base64
import http.server
import json
import os
import re
import secrets
import socket
import ssl
import struct
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import contextlib

from clique import notify
from clique.wsproto import OP_BINARY, OP_TEXT

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:3200").rstrip("/")

#: The password on disk is an scrypt hash and cannot be reversed, which is the
#: point. So the suite authenticates with a throwaway API token it mints and
#: revokes around the run — which also exercises the path an agent uses.
#: Set CLIQUE_TEST_PASSWORD to additionally cover the login form.
PASSWORD = os.environ.get("CLIQUE_TEST_PASSWORD", "")

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
        [sys.executable, "-m", "clique", "token", "create", "smoke-test"],
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
            [sys.executable, "-m", "clique", "token", "revoke", token_id],
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
        check("login sets a cookie", cookie.startswith("clique="), cookie[:20])
    else:
        print("  --   login form not covered (set CLIQUE_TEST_PASSWORD)")

    print("api")
    status, state = call("/api/state")
    check("state loads", status == 200 and "folders" in state, status)
    check("folders seeded", len(state.get("folders", [])) >= 1)
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
    client.send(b"echo hello-from-clique\n")
    out = client.drain(2.5)
    check("keystrokes reach the pane and output comes back",
          b"hello-from-clique" in out, out[-160:])

    client.send(json.dumps({"type": "run", "text": "echo via-control"}).encode(), OP_TEXT)
    out = client.drain(2.5)
    check("control frame runs a command", b"via-control" in out, out[-160:])

    client.send(json.dumps({"type": "resize", "cols": 80, "rows": 24}).encode(), OP_TEXT)
    time.sleep(0.5)
    check("resize accepted without dropping the socket", True)
    client.close()
    time.sleep(0.8)

    print("hardening: what must not be reachable")
    # Each of these was a real finding. They are asserted here so that fixing
    # them once is the same as fixing them for good.
    status, state = call("/api/state")
    check("the webhook secret never comes back",
          state.get("settings", {}).get("webhook_secret", "") == "",
          "settings carried a secret")
    check("but the UI can still tell one is set",
          "webhook_secret_set" in state.get("settings", {}),
          sorted(state.get("settings", {}))[:6])

    # /brand/ is served before login; urlparse does not collapse "..", so a
    # startswith() test let the whole application shell out unauthenticated.
    # Falling through to the login page is the right answer; what must never
    # come back is the application itself. So the assertion is about *what*
    # was served, not about the status code — a 200 carrying the password form
    # is correct, and a 200 carrying app.js is the bug.
    for probe, forbidden, label in (
            (f"{BASE}/brand/../app.js", b"CLIque front end", "the app script"),
            (f"{BASE}/brand/../index.html", b'id="tabbar"', "the app page")):
        request = urllib.request.Request(probe)   # deliberately no credentials
        try:
            with urllib.request.urlopen(request, timeout=10) as res:
                served = res.read()
            check(f"anonymous cannot climb out of /brand/ to {label}",
                  forbidden not in served, served[:80])
        except urllib.error.HTTPError as err:
            check(f"anonymous cannot climb out of /brand/ to {label}",
                  err.code in (401, 403, 404), err.code)

    # A folder colour is written straight into a `style` attribute in the
    # sidebar, so anything that is not a hex triple is a way to put script on
    # the next person's screen. The setter refuses it and keeps what was there.
    status, folder = call("/api/folders", "POST",
                          {"name": "smoke-colour", "color": 'red" onmouseover="x'})
    check("a folder is created", status == 201 and folder.get("id"), folder)
    if folder.get("id"):
        check("a bad colour never reaches the sidebar",
              re.fullmatch(r"#[0-9a-fA-F]{3}|#[0-9a-fA-F]{6}", folder.get("color", "")),
              folder.get("color"))
        status, patched = call("/api/folders/" + folder["id"], "PATCH",
                               {"color": "javascript:alert(1)"})
        check("and a bad one cannot be patched in later",
              patched.get("color") == folder.get("color"), patched.get("color"))
        call("/api/folders/" + folder["id"], "DELETE")

    status, _ = call("/", "POST", None)
    oversize = urllib.request.Request(BASE + "/", data=b"x" * 200,
                                      method="POST")
    oversize.add_header("Content-Length", "999999999")
    try:
        urllib.request.urlopen(oversize, timeout=10)
        check("login refuses an absurd Content-Length", False, "it was accepted")
    except urllib.error.HTTPError as err:
        check("login refuses an absurd Content-Length", err.code == 413, err.code)
    except (urllib.error.URLError, OSError) as err:
        check("login refuses an absurd Content-Length", True, str(err)[:40])

    print("read-only tokens")
    # The bypass this exists to stop: every write route is scoped, and the
    # WebSocket was not — so a token issued read-only could open a terminal
    # and type into it, which is every permission the scope exists to withhold.
    made = subprocess.run(
        [sys.executable, "-m", "clique", "token", "create", "smoke-readonly",
         "--read-only"],
        capture_output=True, text=True, cwd=str(Path(__file__).resolve().parents[1]))
    ro_token = ro_id = ""
    for line in made.stdout.splitlines():
        if line.strip().startswith("mxp_"):
            ro_token = line.strip()
        if line.startswith("created "):
            ro_id = line.split()[1]
    check("mints a read-only token", bool(ro_token), made.stdout[-120:])

    status, _ = call("/api/state", body=None)
    ro_req = urllib.request.Request(BASE + "/api/sessions", method="POST",
                                    data=json.dumps({"cli": "shell", "cwd": "/tmp"}).encode())
    ro_req.add_header("Content-Type", "application/json")
    ro_req.add_header("Authorization", "Bearer " + ro_token)
    try:
        urllib.request.urlopen(ro_req, timeout=10)
        check("refuses a write over HTTP", False, "the POST succeeded")
    except urllib.error.HTTPError as err:
        check("refuses a write over HTTP", err.code == 403, err.code)

    # The oracle is a file on disk, not anything the terminal renders. Reading
    # the pane back proves nothing here: a fresh socket is sent history-only
    # scrollback, so a command sitting on the visible screen does not appear in
    # it, and the check passes whether or not the command ran. A file either
    # exists or it does not.
    proof_keys = Path("/tmp") / f"clique-ro-keys-{secrets.token_hex(4)}"
    proof_ctl = Path("/tmp") / f"clique-ro-ctl-{secrets.token_hex(4)}"

    ro_client = Client(url, "", ro_token)
    check("may still open a socket to watch", "101" in ro_client.status, ro_client.status)
    ro_client.drain(1.5)
    ro_client.send(f"touch {proof_keys}\n".encode())
    ro_client.send(json.dumps({"type": "run", "text": f"touch {proof_ctl}"}).encode(), OP_TEXT)
    ro_client.drain(2.5)
    ro_client.close()
    time.sleep(1.0)

    check("keystrokes from a read-only token do not run",
          not proof_keys.exists(), f"{proof_keys} was created")
    check("nor does a control frame",
          not proof_ctl.exists(), f"{proof_ctl} was created")
    for leftover in (proof_keys, proof_ctl):
        with contextlib.suppress(OSError):
            leftover.unlink()

    subprocess.run([sys.executable, "-m", "clique", "token", "revoke", ro_id],
                   capture_output=True, cwd=str(Path(__file__).resolve().parents[1]))

    print("detach vs kill")
    status, state = call("/api/state")
    still = next((s for s in state["sessions"] if s["id"] == sid), None)
    check("session survives the browser leaving", bool(still and still["alive"]))

    status, _ = call(f"/api/sessions/{sid}", "DELETE")
    check("deletes the session", status == 200, status)
    status, state = call("/api/state")
    check("gone from state", not any(s["id"] == sid for s in state["sessions"]))

    print("workspace")
    _, before = call("/api/state")
    kept = {k: before["settings"][k]
            for k in ("open_tabs", "active_tab", "views_collapsed")}
    status, saved = call("/api/settings", "PATCH", {
        "open_tabs": ["a", "b", "a", "c"],     # the repeat must be dropped
        "active_tab": "b",
        "views_collapsed": ["__archived", "__running"],
    })
    check("stores the open tabs, in order, without repeats",
          status == 200 and saved["open_tabs"] == ["a", "b", "c"],
          saved.get("open_tabs"))
    check("stores which tab was in front", saved["active_tab"] == "b")
    check("stores the shut view-groups",
          saved["views_collapsed"] == ["__archived", "__running"])
    _, again = call("/api/state")
    check("comes back on the next panel to load",
          again["settings"]["open_tabs"] == ["a", "b", "c"])
    call("/api/settings", "PATCH", kept)

    print("health")
    status, health = call("/healthz", anon=True)
    check("answers a monitor without a login", status == 200 and health["ok"], status)
    check("tells an anonymous caller nothing else", list(health) == ["ok"], list(health))
    status, health = call("/healthz")
    check("fills in for a signed-in caller",
          status == 200 and health.get("tmux") and health.get("sessions") is not None,
          health)

    print("changelog")
    status, log = call("/api/changelog")
    newest = log[0] if status == 200 and log else {}
    check("serves parsed release notes", status == 200 and len(log) > 1, status)
    check("every entry carries a wall-clock time",
          bool(log) and all(e["time"] and e["zone"] for e in log),
          next((e["version"] for e in log if not e["time"]), ""))
    _, running = call("/api/state")
    version = str(running.get("version", "")).split("+")[0]
    check("newest first", bool(newest) and newest["version"] == version, version)
    check("markdown came back as structure, not markup",
          bool(newest.get("blocks")) and "spans" in newest["blocks"][0])

    print("webhook")
    # A real receiver on a real socket. The failure modes worth catching here
    # — a body that is not what a receiver expects, a signature computed over
    # different bytes than were sent — do not exist against a mock.
    caught: list[dict] = []

    class Catcher(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            caught.append({
                "body": json.loads(raw or b"{}"),
                "signature": self.headers.get("X-CLIque-Signature", ""),
                "raw": raw,
            })
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *a):
            pass

    receiver = http.server.HTTPServer(("127.0.0.1", 0), Catcher)
    threading.Thread(target=receiver.serve_forever, daemon=True).start()
    hook = f"http://127.0.0.1:{receiver.server_address[1]}/hook"

    status, saved = call("/api/settings", "PATCH",
                         {"webhook_url": hook, "webhook_secret": "s3cret"})
    check("stores a webhook URL", saved.get("webhook_url") == hook, saved.get("webhook_url"))
    status, saved = call("/api/settings", "PATCH", {"webhook_url": "file:///etc/passwd"})
    check("refuses a non-http scheme", saved.get("webhook_url") == "", saved.get("webhook_url"))
    call("/api/settings", "PATCH", {"webhook_url": hook})

    # Delivery is checked through the test button rather than by waiting for a
    # real session to change state: the transitions themselves are a pure
    # function and are exercised exhaustively in smoke.py, where they cost
    # nothing. What only exists across a socket is this — the body, the
    # headers, and a signature computed over the exact bytes that went out.
    call("/api/settings", "PATCH", {"webhook_secret": "s3cret"})
    status, _sent = call("/api/webhook/test", "POST", {})
    check("the test button fires", status == 200, status)

    deadline = time.time() + 10
    while not caught and time.time() < deadline:
        time.sleep(0.2)
    check("and it arrives", bool(caught), "nothing arrived in 10s")
    if caught:
        first = caught[0]
        check("the event says what happened", first["body"].get("event") == "test",
              first["body"].get("event"))
        check("body carries readable text", bool(first["body"].get("text")), first["body"])
        check("signature is over the bytes actually sent",
              first["signature"] == notify.sign(first["raw"], "s3cret"),
              first["signature"][:24])

    call("/api/settings", "PATCH", {"webhook_url": "", "webhook_secret": ""})
    status, _none = call("/api/webhook/test", "POST", {})
    check("refuses a test with no URL set", status == 400, status)
    receiver.shutdown()

    print("attention")
    status, note = call("/api/sessions", "POST",
                        {"cli": "shell", "cwd": "/tmp", "name": "smoke-attention"})
    att_id = note.get("id", "")
    check("session for the attention checks", status == 201 and bool(att_id), note)

    status, said = call(f"/api/sessions/{att_id}/attention", "POST", {"state": "waiting"})
    check("a session can say it is waiting",
          status == 200 and said.get("signal") == "waiting", said)
    _, state = call("/api/state")
    row = next((x for x in state["sessions"] if x["id"] == att_id), {})
    check("and the panel repeats it back", row.get("signal") == "waiting", row.get("signal"))

    status, said = call(f"/api/sessions/{att_id}/attention", "POST", {"state": "error"})
    check("an error outranks a question", said.get("signal") == "error", said)
    status, _bad = call(f"/api/sessions/{att_id}/attention", "POST", {"state": "vibes"})
    check("refuses a state it does not know", status == 400, status)
    status, said = call(f"/api/sessions/{att_id}/attention", "POST", {"state": "clear"})
    check("and can be cleared", said.get("signal") == "", said)

    # A signal describes a moment. Output after it means the session carried
    # on, and a stale "waiting" stuck to a working session is worse than none.
    call(f"/api/sessions/{att_id}/attention", "POST", {"state": "waiting"})
    # tmux's activity clock counts in whole seconds, so the output has to land
    # in a later one than the signal did or there is nothing to compare.
    time.sleep(1.2)
    call(f"/api/sessions/{att_id}/send", "POST", {"text": "echo moved-on", "enter": True})
    time.sleep(1.5)
    _, state = call("/api/state")
    row = next((x for x in state["sessions"] if x["id"] == att_id), {})
    check("a signal goes stale once the session moves on",
          row.get("signal") == "", row.get("signal"))

    call(f"/api/sessions/{att_id}", "DELETE")

    print("clock zone")
    status, saved = call("/api/settings", "PATCH", {"clock_zone": "Europe/Lisbon"})
    check("stores a real zone", saved.get("clock_zone") == "Europe/Lisbon",
          saved.get("clock_zone"))
    # A name the browser would reject has to die here: Intl throws on a bad
    # zone, and that would take the pane down rather than degrade.
    status, saved = call("/api/settings", "PATCH", {"clock_zone": "Mars/Olympus"})
    check("refuses a zone that does not exist", saved.get("clock_zone") == "Europe/Lisbon",
          saved.get("clock_zone"))
    status, saved = call("/api/settings", "PATCH", {"clock_zone": ""})
    check("blank means the browser's own", saved.get("clock_zone") == "", saved.get("clock_zone"))

    print("per-CLI colours")
    status, saved = call("/api/settings", "PATCH",
                         {"cli_colors": {"claude": "#ABCDEF", "grok": "javascript:x",
                                         "shell": "#f0f"}})
    colours = saved.get("cli_colors", {})
    check("stores a colour, lower-cased", colours.get("claude") == "#abcdef", colours)
    check("takes the short form too", colours.get("shell") == "#f0f", colours)
    # This value is written into a style attribute, so anything that is not a
    # colour has to die here rather than downstream.
    check("drops anything that is not a colour", "grok" not in colours, colours)
    status, saved = call("/api/settings", "PATCH", {"cli_colors": {"claude": None}})
    check("null restores the shipped colour",
          "claude" not in saved.get("cli_colors", {}), saved.get("cli_colors"))
    check("and leaves the others alone",
          saved.get("cli_colors", {}).get("shell") == "#f0f", saved.get("cli_colors"))
    call("/api/settings", "PATCH", {"cli_colors": {"shell": None}})

    print("artifacts")
    # A real directory with a real PNG in it: the whole feature is a filesystem
    # read, so mocking the filesystem would test nothing that can break.
    art_dir = Path("/tmp") / f"clique-art-{secrets.token_hex(4)}"
    art_dir.mkdir()
    status, art_session = call("/api/sessions", "POST",
                               {"cli": "shell", "cwd": str(art_dir), "name": "smoke-art"})
    art_id = art_session.get("id", "")
    check("session for the artifact checks", status == 201 and bool(art_id), art_session)
    status, listing = call(f"/api/sessions/{art_id}/artifacts")
    check("a fresh directory has nothing to show", status == 200 and listing == [], listing)

    # Smallest valid PNG: a 1x1 image, so the magic-byte check has something
    # true to agree with rather than a file that merely ends in .png.
    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
    (art_dir / "shot.png").write_bytes(png)
    (art_dir / "notes.txt").write_bytes(b"not an image")
    status, listing = call(f"/api/sessions/{art_id}/artifacts")
    check("finds an image written after the session started",
          status == 200 and [row["rel"] for row in listing] == ["shot.png"], listing)
    check("hands back both paths a caller needs",
          bool(listing) and listing[0]["path"] == str(art_dir / "shot.png"), listing)

    def fetch_raw(path: str):
        req = urllib.request.Request(BASE + path)
        req.add_header("Cookie", cookie)
        if bearer:
            req.add_header("Authorization", "Bearer " + bearer)
        try:
            with urllib.request.urlopen(req, timeout=10) as res:
                return res.status, res.headers.get("Content-Type", ""), res.read()
        except urllib.error.HTTPError as err:
            return err.code, err.headers.get("Content-Type", ""), err.read()

    status, kind, body = fetch_raw(f"/api/sessions/{art_id}/artifact?rel=shot.png")
    check("serves the image itself", status == 200 and body == png, status)
    check("typed from its bytes, not its name", kind == "image/png", kind)

    # The three ways a path can try to leave the working directory. All of them
    # are re-derived server-side, so none of them depends on the browser.
    for label, rel in (("climbing out", "../etc/passwd"),
                       ("an absolute path", "/etc/hostname"),
                       ("a file that is not an image", "notes.txt")):
        status, _kind, _body = fetch_raw(
            f"/api/sessions/{art_id}/artifact?rel={urllib.parse.quote(rel)}")
        check(f"refuses {label}", status == 404, status)

    call(f"/api/sessions/{art_id}", "DELETE")
    with contextlib.suppress(OSError):
        (art_dir / "shot.png").unlink()
        (art_dir / "notes.txt").unlink()
        art_dir.rmdir()

    print("static")
    req = urllib.request.Request(BASE + "/app.js")
    req.add_header("Cookie", cookie)
    with urllib.request.urlopen(req, timeout=10) as res:
        check("serves the app", res.status == 200 and b"CLIque" in res.read()[:400])
    # Every inline <script> must carry a nonce the CSP header actually allows.
    # This exists because curl does not enforce CSP: the policy shipped with a
    # bare `script-src 'self'`, every inline script on every page was silently
    # blocked, and the whole suite stayed green while the app served a white
    # screen to the first person who tried to log in.
    # Both pages live at "/" — the cookie is what decides which one is served,
    # and both carry an inline script that has to survive the policy.
    for name, signed_in in (("app", True), ("login", False)):
        req = urllib.request.Request(BASE + "/")
        if signed_in:
            req.add_header("Cookie", cookie)
        with urllib.request.urlopen(req, timeout=10) as res:
            html = res.read().decode("utf-8", "replace")
            policy = res.headers.get("Content-Security-Policy", "")
        allowed = re.findall(r"'nonce-([A-Za-z0-9_-]+)'", policy)
        inline = re.findall(r"<script(?![^>]*\ssrc=)([^>]*)>", html)
        ok = all(any(f'nonce="{n}"' in tag for n in allowed) for tag in inline)
        check(f"{name} page: inline scripts carry an allowed nonce",
              bool(inline) and bool(allowed) and ok,
              f"{len(inline)} inline, {len(allowed)} nonce(s)")

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
