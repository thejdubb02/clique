"""HTTP layer: static assets, a small JSON API, and the terminal WebSocket.

Stdlib only, on purpose. This process sits beside Claude Code sessions that
each want half a gigabyte; the manager's job is to be invisible in that budget.
`ThreadingHTTPServer` is enough for a handful of tailnet clients and costs a
thread per connection rather than a framework per box.

Authority lives where it belongs: tmux knows what is *running*, the store knows
what things are *called*. Anywhere the two disagree, tmux wins and the store is
corrected — so a session killed behind our back shows as dead instead of
haunting the sidebar forever.
"""

from __future__ import annotations

import contextlib
import json
import mimetypes
import os
import secrets
import threading
import time
import traceback
import urllib.parse
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import sysinfo, tmux, version_string
from .auth import Auth, landing_page, login_page
from .registry import Registry, RegistryError
from .registry import icon_is_full_colour as registry_icon_is_colour
from .store import Session, Store, new_id
from .stream import PtyBridge
from .tokens import TokenStore
from .wsproto import OP_TEXT, WebSocket, handshake_response

WEB = Path(__file__).parent / "web"

#: Proxies drop a silent connection; a terminal is silent whenever you stop
#: typing. Ping often enough to stay under any sane idle timeout.
PING_SECONDS = 25

#: Host headers this server will answer to.
#:
#: Without this, DNS rebinding works: an attacker's page resolves their domain
#: to 127.0.0.1, the browser then treats requests to it as same-origin with
#: *their* page, and every same-origin protection we have is bypassed. The
#: server has to reject the request on the Host header before any handler runs.
#:
#: Loopback literals, the tailnet, and the usual tunnel providers. Anything
#: else has to be named in CLIQUE_ALLOWED_HOSTS.
ALLOWED_HOST_SUFFIXES = (".ts.net", ".trycloudflare.com", ".cfargotunnel.com",
                         ".ngrok.io", ".ngrok-free.app", ".ngrok.app")
ALLOWED_HOST_EXACT = {"localhost", "127.0.0.1", "::1", "[::1]"}


def host_allowed(host: str, extra: set[str]) -> bool:
    """Whether a Host/Origin hostname is one we answer to."""
    name = (host or "").split("://")[-1].rsplit("@", 1)[-1]
    name = name.split("]")[0].lstrip("[") if name.startswith("[") else name.split(":")[0]
    name = name.lower().rstrip(".")
    if not name:
        return False
    if name in ALLOWED_HOST_EXACT or name in extra:
        return True
    if name.replace(".", "").isdigit():
        return True                       # a bare IPv4 literal cannot be rebound
    if any(name.endswith(suffix) for suffix in ALLOWED_HOST_SUFFIXES):
        return True
    return any(name == e.lstrip(".") or name.endswith(e)
               for e in extra if e.startswith("."))


class Panel:
    """Everything the request handlers share. One instance per process."""

    def __init__(self, store: Store, registry: Registry, auth: Auth,
                 tokens: TokenStore) -> None:
        self.store = store
        self.registry = registry
        self.auth = auth
        self.tokens = tokens
        #: Failed logins per client address, for throttling.
        self.failures: dict[str, list[float]] = {}
        self.clients = 0
        self.allowed_hosts = {h.strip().lower() for h in
                              os.environ.get("CLIQUE_ALLOWED_HOSTS", "").split(",")
                              if h.strip()}
        self.history = sysinfo.History()
        self._last_reap = 0.0
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ views

    def live(self) -> dict[str, tmux.Pane]:
        """Every tmux session we might care about, keyed by name.

        One call per socket, not per session: the sidebar polls this and the
        cost must not grow with the number of sessions.
        """
        panes: dict[str, tmux.Pane] = {}
        sockets = {tmux.SOCKET}
        sockets.update(s.socket for s in self.store.sessions if s.socket)
        watched: set[str] = set()
        for socket_name in sockets:
            for pane in tmux.list_sessions(socket_name):
                panes[pane.mux] = pane
            watched |= tmux.attached_via_viewers(socket_name)

        # A browser attaches to a viewer, not to the session itself, so the
        # session's own client count is always zero. Fold the viewers back in
        # or "someone is watching this" is never true.
        for name in watched:
            pane = panes.get(name)
            if pane and not pane.attached:
                panes[name] = replace(pane, attached=True)

        # Reap viewers whose browser has gone. This runs on the sidebar poll
        # rather than on a timer because it needs no scheduling and the poll is
        # already listing every session; the incremental cost is one kill for
        # something that should not exist.
        self._reap_viewers(tuple(sockets))
        return panes

    def _reap_viewers(self, sockets: tuple[str | None, ...]) -> None:
        now = time.time()
        with self._lock:
            if now - self._last_reap < 30:
                return
            self._last_reap = now
        # Housekeeping must never break the session list.
        with contextlib.suppress(tmux.TmuxError):
            tmux.sweep_viewers(sockets, detached_only=True)

    def sessions_view(self) -> list[dict]:
        panes = self.live()
        out = []
        for session in sorted(self.store.sessions, key=lambda s: s.order):
            pane = panes.get(session.mux)
            cli = self.registry.types().get(session.cli)
            out.append({
                "id": session.id,
                "name": session.name,
                "cli": session.cli,
                "cli_label": cli.label if cli else session.cli,
                "color": cli.color if cli else "#8b8b8b",
                "icon": cli.icon if cli else "",
                "icon_full_color": bool(cli and cli.icon
                                        and registry_icon_is_colour(cli.icon)),
                "cwd": session.cwd,
                "project": Path(session.cwd).name or session.cwd,
                "folder": session.folder,
                "mode": session.mode,
                "modes": list(cli.modes) if cli else [],
                "mode_key": cli.mode_key if cli else None,
                "adopted": session.adopted,
                "archived": session.archived,
                "created": session.created,
                "last_seen": session.last_seen,
                "alive": pane is not None,
                "attached": bool(pane and pane.attached),
                "command": pane.command if pane else None,
                "activity": pane.activity if pane else 0,
                # Whether the pane produced output just now. The browser turns
                # a busy->quiet transition into "this one finished", which is
                # what drives tab flashing and the optional chime. Derived from
                # tmux's own activity clock, so it works for any CLI without
                # CLIque knowing anything about it.
                "busy": bool(pane and (time.time() - pane.activity) < 2),
            })
        return out

    def state(self) -> dict:
        return {
            "version": version_string(),
            "folders": [
                {"id": f.id, "name": f.name, "color": f.color,
                 "collapsed": f.collapsed, "order": f.order}
                for f in sorted(self.store.folders, key=lambda f: f.order)
            ],
            "sessions": self.sessions_view(),
            "clis": [c.as_dict() for c in self.registry.types().values()],
            "settings": self.store.settings,
            "stats": sysinfo.snapshot(self.clients),
        }

    # --------------------------------------------------------------- mutation

    def create_session(self, body: dict) -> dict:
        cli_id = body.get("cli") or "shell"
        cwd = body.get("cwd") or "/root"
        name = (body.get("name") or "").strip() or Path(cwd).name or cli_id
        mode = body.get("mode")

        if not Path(cwd).is_dir():
            raise ValueError(f"working directory does not exist: {cwd}")

        session_id = new_id()
        argv = self.registry.launch_argv(
            cli_id, session_id=session_id, name=name, cwd=cwd, mode=mode,
        )
        cli = self.registry.get(cli_id)
        if not cli.installed:
            raise ValueError(f"{cli.label}: '{cli.command}' is not installed on this box")

        mux = tmux.mux_name(session_id)
        tmux.bootstrap()
        tmux.create(mux, cwd, argv, env={
            "CLIQUE": "1",
            "CLIQUE_SESSION": session_id,
        })
        session = self.store.add_session(Session(
            id=session_id, name=name, cli=cli_id, cwd=cwd, mux=mux,
            socket=tmux.SOCKET, mode=mode or cli.default_mode,
            folder=body.get("folder"),
        ))
        return {"id": session.id}

    def adopt(self) -> dict:
        """Take over sessions started by the tool CLIque replaces.

        Adopted sessions stay on their original socket — tmux cannot move a
        session between servers, and killing one to recreate it would destroy
        the work this exists to preserve.
        """
        known = {s.mux for s in self.store.sessions}
        added = []
        for pane in tmux.adoptable():
            if pane.mux in known:
                continue
            guess = pane.command if pane.command in self.registry.types() else "shell"
            session = self.store.add_session(Session(
                id=new_id(), name=Path(pane.cwd).name or pane.mux, cli=guess,
                cwd=pane.cwd, mux=pane.mux, socket=pane.socket,
                created=float(pane.created), adopted=True,
            ))
            added.append(session.name)
        return {"adopted": added}

    def delete_session(self, session_id: str) -> dict:
        session = self.store.session(session_id)
        if not session:
            raise KeyError(session_id)
        # Adopted sessions do not carry our name prefix, so the engine's guard
        # would refuse them. Deleting one is explicit, which is what force means.
        tmux.kill(session.mux, session.socket, force=session.adopted)
        self.store.remove_session(session_id)
        return {"deleted": session_id}


class Handler(BaseHTTPRequestHandler):
    panel: Panel = None  # set by serve()
    server_version = "clique"  # no version: it tells a scanner what to try
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args) -> None:
        # One line per request would drown the journal in terminal polling.
        pass

    # ---------------------------------------------------------------- helpers

    def _send(self, status: int, body: bytes, content_type: str,
              extra: dict[str, str] | None = None, nonce: str = "") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        # A terminal must never be framed by another origin, and nothing here
        # should be cached by an intermediary.
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        # Everything this app loads is its own; nothing is fetched from a CDN.
        # That makes a strict policy free to adopt and worth having: it turns
        # any future injected <script src> into a blocked request rather than
        # code execution. 'unsafe-inline' for style only, because themes are
        # applied as inline custom properties and custom CSS is a feature.
        #
        # Scripts get a per-response nonce rather than 'unsafe-inline'. Three
        # inline scripts are load-bearing and cannot become files: each one
        # resolves the mount path, and an external <script src> would need the
        # very path resolution it is there to perform. A hash list was the
        # other option and is worse — editing a comment inside one of those
        # scripts would silently blank the app, which is precisely how this
        # was found.
        script_src = f"'self' 'nonce-{nonce}'" if nonce else "'self'"
        self.send_header(
            "Content-Security-Policy",
            f"default-src 'self'; script-src {script_src}; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; connect-src 'self' ws: wss:; font-src 'self'; "
            "object-src 'none'; base-uri 'self'; frame-ancestors 'self'; form-action 'self'",
        )
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _json(self, obj, status: int = 200) -> None:
        self._send(status, json.dumps(obj).encode(), "application/json")

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            return {}

    @property
    def _bearer(self):
        """The API token presented on this request, if any."""
        header = self.headers.get("Authorization") or ""
        if not header.lower().startswith("bearer "):
            return None
        return self.panel.tokens.verify(header[7:].strip())

    @property
    def _cookie_authed(self) -> bool:
        token = self.panel.auth.token_from_cookies(self.headers.get("Cookie"))
        return self.panel.auth.valid(token)

    @property
    def _authed(self) -> bool:
        return self._cookie_authed or self._bearer is not None

    def _same_origin(self) -> bool:
        """Whether a browser-initiated request came from our own page.

        This is the CSRF check. A cookie is attached by the browser to *any*
        request to this origin, including one triggered by a page the user did
        not write, so a cookie alone does not prove intent. An Origin header
        that matches does.

        Token requests skip this: browsers never attach an Authorization
        header on their own, so a token proves the caller wrote the request.
        """
        origin = self.headers.get("Origin")
        if not origin:
            # No Origin is sent on same-origin form posts in some browsers, and
            # by non-browser clients. Fall back to Referer, then allow — the
            # alternative breaks curl and every agent that is the point of the
            # API, and those callers use tokens anyway.
            referer = self.headers.get("Referer")
            if not referer:
                return True
            origin = "/".join(referer.split("/")[:3])
        if origin == "null":
            return False        # an opaque origin is a sandboxed frame, not our page
        host = self.headers.get("X-Forwarded-Host") or self.headers.get("Host") or ""
        same = origin.split("://")[-1].split(":")[0] == host.split(":")[0]
        # Either it matches the host we were reached on, or it is a host we
        # would have answered to anyway. The second case covers a tunnel that
        # rewrites Host but not Origin.
        return same or host_allowed(origin, self.panel.allowed_hosts)

    def _may_write(self) -> tuple[bool, str]:
        """(allowed, reason). Writes need auth, scope, and a same-origin check."""
        token = self._bearer
        if token is not None:
            if not token.allows("write"):
                return False, "this API token is read-only"
            return True, ""
        if not self._cookie_authed:
            return False, "unauthorized"
        if not self._same_origin():
            return False, "cross-origin request refused"
        return True, ""

    def _secure(self) -> bool:
        return self.headers.get("X-Forwarded-Proto", "").lower() == "https"

    def _host_ok(self) -> bool:
        """Runs before anything else, including auth.

        A rebound host has to be refused before a handler sees the request —
        after that point the browser already considers the page same-origin
        with ours and every other check is arguing with a decision the browser
        has already made.
        """
        host = self.headers.get("X-Forwarded-Host") or self.headers.get("Host") or ""
        return host_allowed(host, self.panel.allowed_hosts)

    def _route(self) -> tuple[str, dict]:
        parsed = urllib.parse.urlparse(self.path)
        query = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}
        return parsed.path.rstrip("/") or "/", query

    # ------------------------------------------------------------------- GET

    def do_GET(self) -> None:
        if not self._host_ok():
            return self._send(403, b"host not allowed", "text/plain")
        path, query = self._route()

        if path == "/ws":
            return self._websocket(query)
        if not self._authed:
            if path.startswith("/api"):
                return self._json({"error": "unauthorized"}, 401)
            nonce = secrets.token_urlsafe(16)
            return self._send(200, login_page(nonce=nonce),
                              "text/html; charset=utf-8", nonce=nonce)

        try:
            if path == "/api/state":
                return self._json(self.panel.state())
            if path == "/api/stats":
                return self._json(sysinfo.snapshot(self.panel.clients))
            if path == "/api/stats/history":
                minutes = max(1, min(int(query.get("minutes") or 60), 180))
                return self._json(self.panel.history.series(minutes))
            if path == "/api/adoptable":
                known = {s.mux for s in self.panel.store.sessions}
                return self._json([p.as_dict() for p in tmux.adoptable()
                                   if p.mux not in known])
            if path.startswith("/api"):
                return self._json({"error": "not found"}, 404)
            return self._static(path)
        except Exception as exc:  # noqa: BLE001
            return self._fail(exc)

    def _static(self, path: str) -> None:
        name = "index.html" if path == "/" else path.lstrip("/")
        target = (WEB / name).resolve()
        # Path traversal check: resolve() first, then confirm containment.
        if not str(target).startswith(str(WEB.resolve())) or not target.is_file():
            return self._send(404, b"not found", "text/plain")
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type.endswith("javascript"):
            content_type += "; charset=utf-8"
        # Vendored assets are immutable; our own must not be cached, or a fix
        # ships and the browser keeps the bug.
        cache = "public, max-age=604800" if "/vendor/" in path else "no-store"
        body = target.read_bytes()
        nonce = ""
        if target.name == "index.html":
            nonce = secrets.token_urlsafe(16)
            body = body.replace(b"__NONCE__", nonce.encode())
        self._send(200, body, content_type, {"Cache-Control": cache}, nonce=nonce)

    # ------------------------------------------------------------------ POST

    def do_POST(self) -> None:
        if not self._host_ok():
            return self._send(403, b"host not allowed", "text/plain")
        path, _ = self._route()

        if path == "/":
            length = int(self.headers.get("Content-Length") or 0)
            form = urllib.parse.parse_qs(self.rfile.read(length).decode())
            attempt = (form.get("password") or [""])[0]
            who = self.client_address[0] if self.client_address else "?"
            throttled = self._throttled(who)
            # Check the password even when throttled, so a correct one always
            # gets through. Behind a tunnel every request arrives from the same
            # loopback address, so a per-IP lockout would lock out the only
            # legitimate user along with the attacker — which is a denial of
            # service dressed as a protection.
            if not self.panel.auth.check_password(attempt):
                if throttled:
                    self._record_failure(who)
                    time.sleep(2.0)
                    nonce = secrets.token_urlsafe(16)
                    return self._send(429,
                                      login_page("Too many attempts. Wait a minute.", nonce),
                                      "text/html; charset=utf-8", nonce=nonce)
                # Deliberately slow, and counted: this endpoint is reachable by
                # anyone who can reach the tunnel, and guessing should be both
                # expensive and self-limiting.
                self._record_failure(who)
                time.sleep(1.0)
                nonce = secrets.token_urlsafe(16)
                return self._send(401, login_page("Wrong password.", nonce),
                                  "text/html; charset=utf-8", nonce=nonce)
            self.panel.failures.pop(who, None)
            token = self.panel.auth.issue()
            cookie = self.panel.auth.cookie_header(token, self._secure())
            # 200 with a landing page, not a 3xx. See auth.LANDING for why a
            # Location header cannot be made correct from inside this process.
            nonce = secrets.token_urlsafe(16)
            return self._send(200, landing_page(nonce), "text/html; charset=utf-8",
                              {"Set-Cookie": cookie}, nonce=nonce)

        allowed, reason = self._may_write()
        if not allowed:
            return self._json({"error": reason}, 401 if reason == "unauthorized" else 403)

        try:
            body = self._body()
            if path == "/api/sessions":
                return self._json(self.panel.create_session(body), 201)
            if path == "/api/sessions/adopt":
                return self._json(self.panel.adopt())
            if path == "/api/folders":
                folder = self.panel.store.add_folder(body.get("name") or "New folder",
                                                     body.get("color"))
                return self._json({"id": folder.id}, 201)
            if path == "/api/reorder":
                self.panel.store.reorder_sessions(body.get("sessions") or [])
                return self._json({"ok": True})
            if path.startswith("/api/sessions/") and path.endswith("/send"):
                return self._send_input(path.split("/")[3], body)
            if path.startswith("/api/sessions/") and path.endswith("/seen"):
                found = self.panel.store.touch_session(path.split("/")[3])
                if not found:
                    return self._json({"error": "no such session"}, 404)
                return self._json({"ok": True, "last_seen": found.last_seen})
            return self._json({"error": "not found"}, 404)
        except (ValueError, RegistryError, tmux.TmuxError) as exc:
            return self._json({"error": str(exc)}, 400)
        except Exception as exc:  # noqa: BLE001
            return self._fail(exc)

    def _send_input(self, session_id: str, body: dict) -> None:
        session = self.panel.store.session(session_id)
        if not session:
            return self._json({"error": "no such session"}, 404)
        if body.get("key"):
            tmux.send_key(session.mux, body["key"], session.socket)
        else:
            tmux.send_text(session.mux, body.get("text", ""), session.socket,
                           enter=bool(body.get("enter", True)))
        return self._json({"ok": True})

    # --------------------------------------------------------- PATCH / DELETE

    def do_PATCH(self) -> None:
        if not self._host_ok():
            return self._send(403, b"host not allowed", "text/plain")
        path, _ = self._route()
        allowed, reason = self._may_write()
        if not allowed:
            return self._json({"error": reason}, 401 if reason == "unauthorized" else 403)
        body = self._body()
        parts = path.strip("/").split("/")
        try:
            if len(parts) == 2 and parts[1] == "settings":
                return self._json(self.panel.store.update_settings(body))
            if len(parts) == 3 and parts[1] == "sessions":
                # Only fields the caller actually sent. `folder: null` is a
                # real value (drag to Ungrouped), so "absent" and "null" have
                # to stay distinguishable — passing body.get() for every field
                # would make a rename silently unfile the session.
                allowed = {"name", "folder", "mode", "archived"}
                fields = {k: v for k, v in body.items() if k in allowed}
                updated = self.panel.store.update_session(parts[2], **fields)
                return self._json({"ok": bool(updated)}, 200 if updated else 404)
            if len(parts) == 3 and parts[1] == "folders":
                updated = self.panel.store.update_folder(
                    parts[2], name=body.get("name"), color=body.get("color"),
                    collapsed=body.get("collapsed"),
                )
                return self._json({"ok": bool(updated)}, 200 if updated else 404)
            return self._json({"error": "not found"}, 404)
        except Exception as exc:  # noqa: BLE001
            return self._fail(exc)

    def do_DELETE(self) -> None:
        if not self._host_ok():
            return self._send(403, b"host not allowed", "text/plain")
        path, _ = self._route()
        allowed, reason = self._may_write()
        if not allowed:
            return self._json({"error": reason}, 401 if reason == "unauthorized" else 403)
        parts = path.strip("/").split("/")
        try:
            if len(parts) == 3 and parts[1] == "sessions":
                return self._json(self.panel.delete_session(parts[2]))
            if len(parts) == 3 and parts[1] == "folders":
                ok = self.panel.store.remove_folder(parts[2])
                return self._json({"ok": ok}, 200 if ok else 404)
            return self._json({"error": "not found"}, 404)
        except KeyError:
            return self._json({"error": "no such session"}, 404)
        except tmux.TmuxError as exc:
            return self._json({"error": str(exc)}, 400)
        except Exception as exc:  # noqa: BLE001
            return self._fail(exc)

    #: Wrong passwords tolerated per address before a cooling-off period.
    MAX_FAILURES = 8
    FAILURE_WINDOW = 300

    def _throttled(self, who: str) -> bool:
        cutoff = time.time() - self.FAILURE_WINDOW
        recent = [t for t in self.panel.failures.get(who, []) if t > cutoff]
        self.panel.failures[who] = recent
        return len(recent) >= self.MAX_FAILURES

    def _record_failure(self, who: str) -> None:
        self.panel.failures.setdefault(who, []).append(time.time())

    def _fail(self, exc: Exception) -> None:
        traceback.print_exc()
        self._json({"error": f"{type(exc).__name__}: {exc}"}, 500)

    # -------------------------------------------------------------- terminal

    def _websocket(self, query: dict) -> None:
        """Attach one browser to one tmux session for as long as it stays.

        The PTY is created here and destroyed in the finally block, which is
        the entire resource story: no viewer, no process.
        """
        self.close_connection = True

        # Cross-Site WebSocket Hijacking. A WebSocket handshake is not subject
        # to CORS and SameSite=Lax does not cover it, so a hostile page can
        # open a socket to this origin and the browser will attach the session
        # cookie. Without this check that page gets a live root terminal. The
        # Origin header is the only thing separating "our page" from "any page
        # the user happened to visit", and it must be checked before the
        # handshake completes.
        origin = self.headers.get("Origin")
        if origin is not None and not self._same_origin():
            return self._send(403, b"cross-site websocket blocked", "text/plain")

        if not self._authed:
            return self._send(401, b"unauthorized", "text/plain")

        key = self.headers.get("Sec-WebSocket-Key")
        if not key:
            return self._send(400, b"not a websocket request", "text/plain")

        session = self.panel.store.session(query.get("id", ""))
        if not session:
            return self._send(404, b"no such session", "text/plain")

        if not tmux.exists(session.mux, session.socket):
            return self._send(409, b"session is no longer running", "text/plain")

        cols = int(query.get("cols") or 120)
        rows = int(query.get("rows") or 32)

        self.wfile.write(handshake_response(key))
        self.wfile.flush()
        ws = WebSocket(self.connection)

        with self.panel._lock:
            self.panel.clients += 1

        bridge: PtyBridge | None = None
        viewer: str | None = None
        try:
            # Scrollback first, then attach. History only — tmux redraws the
            # visible frame itself on attach, and sending it twice reads as the
            # CLI having repeated its last screen.
            try:
                history = tmux.capture(session.mux, session.socket,
                                       history_only=True)
                if history.strip():
                    ws.send(history.replace("\n", "\r\n").encode())
            except tmux.TmuxError:
                pass  # a session that died between the check and here

            # Attach to a private view of the session rather than the session
            # itself, so this browser's window size is its own. Without it, a
            # phone attaching would shrink the same session on a desktop — and
            # for an adopted session, under the tool still running it.
            viewer = tmux.create_viewer(session.mux, session.socket)
            bridge = PtyBridge(
                tmux.attach_argv(viewer, session.socket),
                on_output=ws.send,
                on_exit=ws.close,
                cols=cols, rows=rows,
            )
            bridge.start()

            stop = threading.Event()
            threading.Thread(target=self._keepalive, args=(ws, stop),
                             daemon=True).start()
            try:
                while True:
                    message = ws.recv()
                    if message is None:
                        break
                    opcode, payload = message
                    if opcode == OP_TEXT:
                        self._control(session, bridge, payload)
                    else:
                        bridge.write(payload)
            finally:
                stop.set()
        finally:
            if bridge:
                bridge.close()
            if viewer:
                # The view goes; the session it looked at does not.
                with contextlib.suppress(tmux.TmuxError):
                    tmux.kill(viewer, session.socket)
            ws.close()
            with self.panel._lock:
                self.panel.clients = max(self.panel.clients - 1, 0)

    def _control(self, session, bridge: PtyBridge, payload: bytes) -> None:
        """Text frames are control; binary frames are keystrokes."""
        try:
            message = json.loads(payload)
        except ValueError:
            return
        kind = message.get("type")
        if kind == "resize":
            bridge.resize(int(message.get("cols", 120)), int(message.get("rows", 32)))
        elif kind == "key":
            tmux.send_key(session.mux, str(message["key"]), session.socket)
        elif kind == "run":
            tmux.send_text(session.mux, str(message.get("text", "")), session.socket,
                           enter=bool(message.get("enter", True)))

    @staticmethod
    def _keepalive(ws: WebSocket, stop: threading.Event) -> None:
        while not stop.wait(PING_SECONDS):
            if ws.closed:
                return
            ws.ping()


def serve(host: str, port: int, panel: Panel) -> None:
    Handler.panel = panel
    panel.history.start()
    tmux.bootstrap()
    # A crash leaves viewer sessions behind. They hold no work, so clearing
    # them at startup is free and keeps `tmux ls` honest.
    stale = tmux.sweep_viewers((tmux.SOCKET, *tmux.FOREIGN_SOCKETS))
    if stale:
        print(f"cleared {stale} stale viewer session(s)", flush=True)
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.daemon_threads = True
    print(f"clique {version_string()} on http://{host}:{port}", flush=True)
    httpd.serve_forever()
