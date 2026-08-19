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
import threading
import time
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import __version__, sysinfo, tmux
from .auth import Auth, landing_page, login_page
from .registry import Registry, RegistryError
from .store import Session, Store, new_id
from .stream import PtyBridge
from .wsproto import OP_TEXT, WebSocket, handshake_response

WEB = Path(__file__).parent / "web"

#: Proxies drop a silent connection; a terminal is silent whenever you stop
#: typing. Ping often enough to stay under any sane idle timeout.
PING_SECONDS = 25


class Panel:
    """Everything the request handlers share. One instance per process."""

    def __init__(self, store: Store, registry: Registry, auth: Auth) -> None:
        self.store = store
        self.registry = registry
        self.auth = auth
        self.clients = 0
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
        for socket_name in sockets:
            for pane in tmux.list_sessions(socket_name):
                panes[pane.mux] = pane
        return panes

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
                "cwd": session.cwd,
                "project": Path(session.cwd).name or session.cwd,
                "folder": session.folder,
                "mode": session.mode,
                "modes": list(cli.modes) if cli else [],
                "mode_key": cli.mode_key if cli else None,
                "adopted": session.adopted,
                "created": session.created,
                "alive": pane is not None,
                "attached": bool(pane and pane.attached),
                "command": pane.command if pane else None,
                "activity": pane.activity if pane else 0,
            })
        return out

    def state(self) -> dict:
        return {
            "version": __version__,
            "folders": [
                {"id": f.id, "name": f.name, "color": f.color,
                 "collapsed": f.collapsed, "order": f.order}
                for f in sorted(self.store.folders, key=lambda f: f.order)
            ],
            "sessions": self.sessions_view(),
            "clis": [c.as_dict() for c in self.registry.types().values()],
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
            "MUXPANEL": "1",
            "MUXPANEL_SESSION": session_id,
        })
        session = self.store.add_session(Session(
            id=session_id, name=name, cli=cli_id, cwd=cwd, mux=mux,
            socket=tmux.SOCKET, mode=mode or cli.default_mode,
            folder=body.get("folder"),
        ))
        return {"id": session.id}

    def adopt(self) -> dict:
        """Take over sessions started by the tool muxpanel replaces.

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
    server_version = f"muxpanel/{__version__}"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args) -> None:
        # One line per request would drown the journal in terminal polling.
        pass

    # ---------------------------------------------------------------- helpers

    def _send(self, status: int, body: bytes, content_type: str,
              extra: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        # A terminal must never be framed by another origin, and nothing here
        # should be cached by an intermediary.
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
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
    def _authed(self) -> bool:
        token = self.panel.auth.token_from_cookies(self.headers.get("Cookie"))
        return self.panel.auth.valid(token)

    def _secure(self) -> bool:
        return self.headers.get("X-Forwarded-Proto", "").lower() == "https"

    def _route(self) -> tuple[str, dict]:
        parsed = urllib.parse.urlparse(self.path)
        query = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}
        return parsed.path.rstrip("/") or "/", query

    # ------------------------------------------------------------------- GET

    def do_GET(self) -> None:
        path, query = self._route()

        if path == "/ws":
            return self._websocket(query)
        if not self._authed:
            if path.startswith("/api"):
                return self._json({"error": "unauthorized"}, 401)
            return self._send(200, login_page(), "text/html; charset=utf-8")

        try:
            if path == "/api/state":
                return self._json(self.panel.state())
            if path == "/api/stats":
                return self._json(sysinfo.snapshot(self.panel.clients))
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
        self._send(200, target.read_bytes(), content_type, {"Cache-Control": cache})

    # ------------------------------------------------------------------ POST

    def do_POST(self) -> None:
        path, _ = self._route()

        if path == "/":
            length = int(self.headers.get("Content-Length") or 0)
            form = urllib.parse.parse_qs(self.rfile.read(length).decode())
            attempt = (form.get("password") or [""])[0]
            if not self.panel.auth.check_password(attempt):
                # Deliberately slow: this endpoint is reachable by anyone on
                # the tailnet and guessing should not be free.
                time.sleep(1.0)
                return self._send(401, login_page("Wrong password."),
                                  "text/html; charset=utf-8")
            token = self.panel.auth.issue()
            cookie = self.panel.auth.cookie_header(token, self._secure())
            # 200 with a landing page, not a 3xx. See auth.LANDING for why a
            # Location header cannot be made correct from inside this process.
            return self._send(200, landing_page(), "text/html; charset=utf-8",
                              {"Set-Cookie": cookie})

        if not self._authed:
            return self._json({"error": "unauthorized"}, 401)

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
        path, _ = self._route()
        if not self._authed:
            return self._json({"error": "unauthorized"}, 401)
        body = self._body()
        parts = path.strip("/").split("/")
        try:
            if len(parts) == 3 and parts[1] == "sessions":
                updated = self.panel.store.update_session(
                    parts[2], name=body.get("name"), folder=body.get("folder"),
                    mode=body.get("mode"),
                )
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
        path, _ = self._route()
        if not self._authed:
            return self._json({"error": "unauthorized"}, 401)
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
    tmux.bootstrap()
    # A crash leaves viewer sessions behind. They hold no work, so clearing
    # them at startup is free and keeps `tmux ls` honest.
    stale = tmux.sweep_viewers((tmux.SOCKET, *tmux.FOREIGN_SOCKETS))
    if stale:
        print(f"cleared {stale} stale viewer session(s)", flush=True)
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.daemon_threads = True
    print(f"muxpanel {__version__} on http://{host}:{port}", flush=True)
    httpd.serve_forever()
