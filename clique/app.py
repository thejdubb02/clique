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

import base64
import binascii
import contextlib
import dataclasses
import json
import mimetypes
import os
import secrets
import shlex
import sys
import threading
import time
import traceback
import urllib.parse
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import (
    artifacts,
    attention,
    changelog,
    files,
    gitinfo,
    llm,
    migrate,
    notify,
    secretbox,
    services,
    sysinfo,
    termstrip,
    tmux,
    version_string,
    working,
    workspace,
)
from .auth import Auth, landing_page, login_page
from .history import History as ConversationHistory
from .registry import Registry, RegistryError
from .registry import icon_is_full_colour as registry_icon_is_colour
from .store import Session, Store, auto_folder, new_id
from .stream import PtyBridge
from .tokens import TokenStore
from .wsproto import OP_TEXT, WebSocket, handshake_response

WEB = Path(__file__).parent / "web"

#: The reporter a hook-speaking CLI runs to report its state (clique/hook.py).
#: Standard-library only, so any python can run it as a plain script.
HOOK = Path(__file__).parent / "hook.py"


def _hooks_settings(python: str) -> str:
    """A Claude Code ``--settings`` JSON string that wires the state hooks.

    A permission or idle Notification means the session is genuinely waiting on
    the person. Stop only means a turn ended — for an autonomous session that is
    a boundary, not a request — so it *clears* rather than nagging; a real wait
    still surfaces from the idle Notification (~60s of no input) or the pane
    heuristic. UserPromptSubmit means the person answered — working again, so
    clear. Each event runs the reporter with the state as its one argument;
    the reporter reads the pane's ``CLIQUE_*`` env and POSTs it to the
    attention endpoint. Loaded with ``--settings`` so it merges on top of the
    user's own Claude config for these sessions and touches nothing on disk."""

    def cmd(state: str, note: str = "") -> dict:
        tail = f" {shlex.quote(note)}" if note else ""
        return {
            "type": "command",
            "command": f"{shlex.quote(python)} {shlex.quote(str(HOOK))} {state}{tail}",
        }

    # The Notification matcher carries the "why": a permission prompt wants an
    # approval, an idle prompt is a question or a finished turn. Both are
    # "waiting"; the note is what lets the inbox offer Approve/Deny vs a reply.
    config = {
        "hooks": {
            "Notification": [
                {"matcher": "permission_prompt", "hooks": [cmd("waiting", "permission")]},
                {"matcher": "idle_prompt", "hooks": [cmd("waiting", "idle")]},
            ],
            "Stop": [{"hooks": [cmd("clear")]}],
            "UserPromptSubmit": [{"hooks": [cmd("clear")]}],
        }
    }
    return json.dumps(config, separators=(",", ":"))


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
ALLOWED_HOST_SUFFIXES = (
    ".ts.net",
    ".trycloudflare.com",
    ".cfargotunnel.com",
    ".ngrok.io",
    ".ngrok-free.app",
    ".ngrok.app",
)
ALLOWED_HOST_EXACT = {"localhost", "127.0.0.1", "::1", "[::1]"}


def host_allowed(host: str, extra: set[str]) -> bool:
    """Whether a Host/Origin hostname is one we answer to."""
    raw = (host or "").split("://")[-1]
    # A userinfo "@" is not something a browser puts in a Host, and taking the
    # part after it read "Host: evil.com@127.0.0.1" as the safe 127.0.0.1.
    if "@" in raw:
        return False
    name = raw.split("]")[0].lstrip("[") if raw.startswith("[") else raw.split(":")[0]
    name = name.lower().rstrip(".")
    if not name:
        return False
    if name in ALLOWED_HOST_EXACT or name in extra:
        return True
    if name.replace(".", "").isdigit():
        return True  # a bare IPv4 literal cannot be rebound
    if any(name.endswith(suffix) for suffix in ALLOWED_HOST_SUFFIXES):
        return True
    return any(name == e.lstrip(".") or name.endswith(e) for e in extra if e.startswith("."))


#: When this process came up, for the uptime in /healthz. Process-local by
#: design: a restart resetting it is exactly what a monitor wants to see.
_STARTED = time.time()


class Panel:
    """Everything the request handlers share. One instance per process."""

    def __init__(self, store: Store, registry: Registry, auth: Auth, tokens: TokenStore) -> None:
        self.store = store
        self.registry = registry
        self.auth = auth
        self.tokens = tokens
        #: Where this server is listening, filled in by serve(). Until then the
        #: port is 0, which reads as "no URL to hand a hook", so hook wiring is
        #: skipped rather than pointed at a guess (e.g. under a test Panel).
        self.host = ""
        self.port = 0
        #: The persistent token the hooks authenticate with, made on first use.
        self._hook_token = ""
        #: Past conversations, discovered on demand and cached. Deliberately
        #: NOT `self.history` — that name was already the cpu/memory series,
        #: and taking it silently replaced the stats endpoint's backing object
        #: with one that has no `series`.
        self.conversations = ConversationHistory(registry)
        #: Failed logins per client address, for throttling.
        self.failures: dict[str, list[float]] = {}
        #: Watches for the transitions worth a notification. Only actually
        #: runs while a webhook URL is set, so a panel nobody has configured
        #: one on still costs nothing when idle.
        self.watcher = notify.Watcher(self)
        self.watcher.ensure()
        #: Whether the service behind a running CLI is having a bad day. Same
        #: bargain as the watcher: a thread only while it is switched on, and
        #: it only asks about CLIs that have a session open right now.
        self.services = services.Services(self)
        self.services.ensure()
        self.clients = 0
        self.allowed_hosts = {
            h.strip().lower()
            for h in os.environ.get("CLIQUE_ALLOWED_HOSTS", "").split(",")
            if h.strip()
        }
        #: Believe X-Forwarded-* only when the operator confirms a proxy sets
        #: them. Otherwise any client could send X-Forwarded-Host and step past
        #: the DNS-rebinding gate on Host. Set behind tailscale / caddy / nginx.
        self.trust_proxy = os.environ.get("CLIQUE_TRUST_PROXY", "").strip().lower() in (
            "1",
            "true",
            "yes",
        )
        self.history = sysinfo.History()
        self._last_reap = 0.0
        self._last_idle_reap = 0.0
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
        self._reap_idle(panes)
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

    def _reap_idle(self, panes: dict) -> None:
        """Free the memory of an idle session by stopping its process, keeping
        the record and greying its tab. Nothing is lost: the state is on disk
        and resumable, so clicking the tab runs the CLI's resume and it is back
        where it was. Never touches a session a browser is attached to, one that
        is busy, or one that could not be brought back."""
        try:
            hours = float(self.store.settings.get("reap_idle_hours", 0) or 0)
        except (TypeError, ValueError):
            return
        if hours <= 0:
            return
        now = time.time()
        with self._lock:
            if now - self._last_idle_reap < 60:
                return
            self._last_idle_reap = now
        threshold = hours * 3600
        for session in list(self.store.sessions):
            pane = panes.get(session.mux)
            if pane is None or pane.attached:
                continue  # already stopped, or in view
            if now - pane.activity < threshold:
                continue  # not idle long enough
            cli = self.registry.types().get(session.cli)
            if not (cli and cli.resume):
                continue  # nothing to resume -> keep running
            args = " ".join(cli.args)
            if not (session.cli_session_id or "{id}" in args or "{uuid}" in args):
                continue  # no resume key -> do not lose it
            if working.busy(pane, session.socket, now):
                continue  # do not interrupt work in progress
            with contextlib.suppress(tmux.TmuxError):
                tmux.kill(session.mux, session.socket, force=session.adopted)

    def health(self, detailed: bool = False) -> dict:
        """A monitor's-eye view of the panel.

        Answers without a login on purpose. Uptime Kuma, Gatus, Healthchecks
        and every other self-hosted monitor want one URL that returns 200, and
        anything that makes them carry a credential first is a thing that ends
        up unmonitored. So an anonymous caller gets liveness and nothing else:
        no version — the `Server` header withholds that for the same reason —
        no names, no paths, no counts.

        Sign in, or send a token, and it fills in the numbers worth graphing.
        """
        body: dict = {"ok": True}
        if not detailed:
            return body
        panes = self.live()
        sessions = self.store.sessions
        body.update(
            {
                "version": version_string(),
                "uptime": int(time.time() - _STARTED),
                "tmux": tmux.available(),
                "sessions": len(sessions),
                "alive": sum(1 for x in sessions if x.mux in panes),
                "attached": self.clients,
            }
        )
        return body

    def resource_guard(self, panes: dict | None = None) -> dict:
        """Soft read on whether the box is stretched for its live sessions.

        Never blocks a new session — it just surfaces the levers (idle-reap,
        Reclaim) at the moment they start to matter. Measures the real
        per-session cost from the live panes rather than a fixed guess.
        """
        panes = self.live() if panes is None else panes
        rss_map = sysinfo.rss_by_root([p.pid for p in panes.values()])
        return sysinfo.guard(
            len(panes),
            rss_map.values(),
            reap_hours=float(self.store.settings.get("reap_idle_hours", 0) or 0),
        )

    def sessions_view(self, panes: dict | None = None) -> list[dict]:
        panes = self.live() if panes is None else panes
        rss_map = sysinfo.rss_by_root([p.pid for p in panes.values()])
        now = time.time()
        # Sessions that have gone stop being remembered by the busy check.
        working.forget(set(panes))
        out = []
        for session in sorted(self.store.sessions, key=lambda s: s.order):
            pane = panes.get(session.mux)
            cli = self.registry.types().get(session.cli)
            # Busy first: `_signal` asks working.settled(), which reads the
            # clock `_since` that busy() stamps. A permission prompt that
            # blinks looks like work until that stamp is in place.
            busy = bool(pane and working.busy(pane, session.socket, now))
            signal = self._signal(session, pane, cli)
            git = gitinfo.of(session.cwd)
            out.append(
                {
                    "id": session.id,
                    "name": session.name,
                    "cli": session.cli,
                    "cli_label": cli.label if cli else session.cli,
                    "color": cli.color if cli else "#8b8b8b",
                    "icon": cli.icon if cli else "",
                    "icon_full_color": bool(cli and cli.icon and registry_icon_is_colour(cli.icon)),
                    # Whether this CLI draws its own input box, so the panel can
                    # stop drawing a second one under it.
                    "own_input": bool(cli and cli.own_input),
                    "cwd": session.cwd,
                    "project": Path(session.cwd).name or session.cwd,
                    # Git, when this directory is a repo. Cached and filled in
                    # the background so a slow `status` cannot stall the poll.
                    # Empty / zero where there is no repo, no git, or we have
                    # not asked yet.
                    "branch": git["branch"],
                    "dirty": git["dirty"],
                    "folder": session.folder,
                    "mode": session.mode,
                    "modes": list(cli.modes) if cli else [],
                    "mode_key": cli.mode_key if cli else None,
                    "mode_seq": cli.mode_seq if cli else "",
                    "mode_label": cli.mode_label if cli else "",
                    "adopted": session.adopted,
                    "archived": session.archived,
                    "pinned": session.pinned,
                    "draft": session.draft,
                    "created": session.created,
                    "last_seen": session.last_seen,
                    "alive": pane is not None,
                    "attached": bool(pane and pane.attached),
                    # On the alternate screen a full-screen app owns its own scroll,
                    # so the browser forwards the wheel to it instead of scrolling a
                    # pane scrollback that tmux only ever redraws in place.
                    "alt": bool(pane and pane.alternate_on),
                    "command": pane.command if pane else None,
                    # Resident memory of the whole process tree, so you can see
                    # which tab is expensive before deciding what to do with it.
                    "rss": (rss_map.get(pane.pid, 0) * 1024) if pane else 0,
                    "activity": pane.activity if pane else 0,
                    # The pane's real size, so a browser can notice when it has
                    # drifted from what it is drawing. A tmux window has one size
                    # and every client attached to it shares that one — so a second
                    # browser, or a phone, resizes this one's pane out from under
                    # it, and until now nothing ever told it.
                    "cols": pane.width if pane else 0,
                    "rows": pane.height if pane else 0,
                    # Whether the pane produced output just now. The browser turns
                    # a busy->quiet transition into "this one finished", which is
                    # what drives tab flashing and the optional chime. Derived from
                    # tmux's own activity clock, so it works for any CLI without
                    # CLIque knowing anything about it.
                    # Not the raw clock any more: a CLI that animates while it
                    # waits ticks it forever. See clique/working.py — the clock
                    # still decides cheaply, and content decides when the clock
                    # has been saying yes for a while.
                    "busy": busy,
                    # What it last said — but only for a session that is actually
                    # asking for a person. Working sessions change it every
                    # moment, and idle-and-read ones are not asking anything, so
                    # for those it is a capture spent on a line nobody reads.
                    "saying": (
                        attention.saying(session.mux, pane.activity, session.socket)
                        if pane and signal
                        else ""
                    ),
                    # "waiting" or "error", from whichever tier of the attention
                    # ladder could answer. See Panel._signal.
                    "signal": signal,
                    # The "why" behind a live authoritative signal, so the inbox
                    # can offer Approve/Deny for a permission prompt rather than
                    # a plain reply. Empty once the signal is stale or a guess.
                    "signal_note": (
                        session.signal_note if self._authoritative(session, pane) else ""
                    ),
                    # One word for what the session is doing, for a caller that
                    # wants the answer rather than the ingredients: "stopped" (no
                    # process), "working" (producing output), "waiting"/"error"
                    # (needs a person), or "idle" (up, quiet, nothing pending).
                    "state": (
                        "stopped"
                        if pane is None
                        else self._authoritative(session, pane)
                        or ("working" if busy else (signal or "idle"))
                    ),
                }
            )
        return out

    def _authoritative(self, session, pane) -> str:
        """The session's own declared state — from a hook or the attention
        endpoint — when no output has arrived to supersede it, else "".

        This outranks the activity guess wherever a single word for the state
        is formed, because it is the session *saying* what the busy heuristic is
        only trying to infer. A spinner that ticks the activity clock forever
        would otherwise read as "working" while the hook already knows it is
        waiting on a person; here the hook wins. Stale the moment output lands
        after it (pane.activity > signal_at), and then the heuristic takes over
        again — the same freshness test the signal tier already uses."""
        if pane and session.signal and pane.activity <= session.signal_at:
            return session.signal
        return ""

    def session_state(self, session) -> str:
        """One word for what a session is doing, the same vocabulary the
        sidebar uses: stopped, working, waiting, error, idle. Computed fresh so
        it is usable off the poll -- by the wait endpoint below, and by an agent
        orchestrating CLIque over the API."""
        pane = self.live().get(session.mux)
        if pane is None:
            return "stopped"
        authoritative = self._authoritative(session, pane)
        if authoritative:
            return authoritative
        if working.busy(pane, session.socket):
            return "working"
        cli = self.registry.types().get(session.cli)
        return self._signal(session, pane, cli) or "idle"

    def wait_for_state(self, session_id: str, wanted: set, timeout: float) -> dict:
        """Block until a session reaches one of ``wanted`` states, or timeout.

        The primitive an agent needs to drive CLIque: start work, then wait for
        it to finish or come back asking, instead of polling the whole panel.
        The timeout is capped so a handler thread cannot be held forever; a
        caller that wants longer calls again."""
        if not self.store.session(session_id):
            raise KeyError(session_id)
        timeout = max(1.0, min(float(timeout), 300.0))
        start = time.time()
        deadline = start + timeout
        while True:
            session = self.store.session(session_id)
            if session is None:
                return {
                    "id": session_id,
                    "state": "gone",
                    "matched": False,
                    "waited": round(time.time() - start, 1),
                }
            state = self.session_state(session)
            if state in wanted or time.time() >= deadline:
                return {
                    "id": session_id,
                    "state": state,
                    "matched": state in wanted,
                    "waited": round(time.time() - start, 1),
                }
            time.sleep(1.0)

    def _signal(self, session, pane, cli) -> str:
        """Whether this session is waiting on a person, and how we know.

        Three tiers, in order of how much they can be trusted:

        1. What the session *said*, via the attention endpoint. Believed —
           until output arrives after it, which means the session moved on and
           the signal is describing a moment that has passed.
        2. Patterns from ``clis.toml``, matched against a pane that has gone
           quiet. A guess, and a good one, and only as good as the config.
        3. Nothing. Most sessions, most of the time.

        A pane in a real output burst never reaches tier 2: capturing it
        would cost a subprocess per poll to learn it is not waiting. A pane
        that has been "busy" past SETTLE is the other case — an animated
        prompt ticks the clock forever, and that *is* a question.
        """
        if not pane:
            return ""
        if session.signal and pane.activity <= session.signal_at:
            return session.signal
        if not working.settled(pane):
            return ""
        waiting = cli.waiting_patterns if cli else []
        errors = cli.error_patterns if cli else []
        return attention.detect(session.mux, pane.activity, waiting, errors, session.socket)

    # ------------------------------------------------------------ llm providers
    #
    # Bring-your-own-key model providers. The key is encrypted at rest by
    # secretbox and never returned to a client (only whether one is set); a
    # provider is validated here, and reachability is proved by an actual probe
    # rather than trusted. Kept off /api/state — fetched only on demand.

    _PROVIDER_KINDS = ("openai", "anthropic")

    def _secret_key_path(self) -> Path:
        return self.store.path.parent / "secret.key"

    def _redact_provider(self, p: dict) -> dict:
        """What a client is allowed to see: everything but the key itself."""
        return {
            "id": p.get("id"),
            "label": p.get("label", ""),
            "kind": p.get("kind", ""),
            "base_url": p.get("base_url", ""),
            "model": p.get("model", ""),
            "key_set": bool(p.get("key_enc")),
        }

    def _provider_fields(self, body: dict, existing: dict | None = None) -> dict:
        base_of = existing or {}
        kind = str(body.get("kind") or base_of.get("kind") or "").strip().lower()
        if kind not in self._PROVIDER_KINDS:
            raise ValueError(f"kind must be one of: {', '.join(self._PROVIDER_KINDS)}")
        base = str(body.get("base_url") or base_of.get("base_url") or "").strip()[:2048]
        if not base.startswith(("http://", "https://")):
            raise ValueError("base URL must be http or https")
        model = str(body.get("model") or base_of.get("model") or "").strip()[:200]
        if not model:
            raise ValueError("a model is required")
        label = str(body.get("label") or base_of.get("label") or "").strip()[:80]
        return {"kind": kind, "base_url": base, "model": model, "label": label or kind}

    def _encrypt_key(self, key: str) -> str:
        if not secretbox.available():
            raise ValueError(f"encryption is unavailable — {secretbox.SecretsUnavailable.HINT}")
        return secretbox.encrypt(key, self._secret_key_path())

    def llm_list(self) -> dict:
        return {
            "providers": [self._redact_provider(p) for p in self.store.llm_providers()],
            "encryption": secretbox.available(),
        }

    def llm_create(self, body: dict) -> dict:
        fields = self._provider_fields(body)
        key = str(body.get("key") or "")
        if not key:
            raise ValueError("an API key is required")
        record = {"id": "lp-" + secrets.token_hex(4), **fields, "key_enc": self._encrypt_key(key)}
        return self._redact_provider(self.store.save_provider(record))

    def llm_update(self, provider_id: str, body: dict) -> dict | None:
        existing = self.store.provider(provider_id)
        if not existing:
            return None
        record = {**existing, **self._provider_fields(body, existing)}
        key = str(body.get("key") or "")
        if key:  # a blank key on update means "keep the stored one"
            record["key_enc"] = self._encrypt_key(key)
        return self._redact_provider(self.store.save_provider(record))

    def llm_delete(self, provider_id: str) -> dict | None:
        return {"ok": True} if self.store.remove_provider(provider_id) else None

    def llm_test(self, provider_id: str) -> dict | None:
        p = self.store.provider(provider_id)
        if not p:
            return None
        key = ""
        if p.get("key_enc"):
            if not secretbox.available():
                return {
                    "ok": False,
                    "error": f"encryption unavailable — {secretbox.SecretsUnavailable.HINT}",
                }
            try:
                key = secretbox.decrypt(p["key_enc"], self._secret_key_path())
            except Exception:  # noqa: BLE001 — a corrupt/tampered key is a failed test, not a 500
                return {"ok": False, "error": "the stored key could not be decrypted"}
        profile = {"kind": p["kind"], "base_url": p["base_url"], "model": p["model"], "key": key}
        return llm.test(profile)

    def state(self, *, reveal_urls: bool = False) -> dict:
        panes = self.live()
        stats = sysinfo.snapshot(self.clients)
        stats["guard"] = self.resource_guard(panes)
        return {
            "version": version_string(),
            "folders": [
                {
                    "id": f.id,
                    "name": f.name,
                    "color": f.color,
                    "collapsed": f.collapsed,
                    "order": f.order,
                }
                for f in sorted(self.store.folders, key=lambda f: f.order)
            ],
            "sessions": self.sessions_view(panes),
            "clis": [c.as_dict() for c in self.registry.types().values()],
            # Almost always empty, which is the point — this carries the
            # exceptions, not a running commentary on four status pages being
            # fine. It rides on the poll rather than taking a second one: the
            # value changes every five minutes and costs nothing to send.
            "services": self.services.snapshot(),
            # The secret never goes back out. /api/state needs only `_authed`,
            # so a read-only token was receiving it — and with it the ability
            # to forge a signature the receiver trusts. Whether one is set is
            # all the UI ever needed to know.
            "settings": redacted(self.store.settings, reveal_urls=reveal_urls),
            "stats": stats,
        }

    # --------------------------------------------------------------- mutation

    def _self_url(self) -> str:
        """The panel's own URL a hook running on this box can POST back to.

        Loopback whenever the bind host is a wildcard or loopback, the real
        host otherwise. Empty until serve() has set the port — which is the
        signal to skip hook wiring rather than invent a URL nothing answers."""
        if not self.port:
            return ""
        host = self.host
        if host in ("", "0.0.0.0", "::", "localhost", "127.0.0.1"):  # noqa: S104 — comparison, not a bind
            host = "127.0.0.1"
        return f"http://{host}:{self.port}"

    def _hook_bearer(self) -> str:
        """A persistent, narrowly-scoped token the state hooks authenticate with.

        This token lives in every session's environment, so its scope is the
        whole of its safety: ``attention`` only, which permits a status nudge to
        the attention endpoint and nothing else. A prompt-injected agent that
        reads it cannot spawn a shell or drive another session — the escalation
        a ``write`` token here would have handed it. Made once and kept beside
        the token store (created with 0600, not chmod-ed after) so a restart
        does not orphan the copies already handed to running panes."""
        if self._hook_token:
            return self._hook_token
        path = self.tokens.path.parent / "hook.token"
        try:
            raw = path.read_text(encoding="utf-8").strip()
            if raw and self.tokens.verify(raw):
                self._hook_token = raw
                return raw
        except OSError:
            pass
        _token, raw = self.tokens.create("clique-hooks", ["attention"])
        # Restricted at creation, never world-readable for a window: the raw
        # token is the sensitive half (the store keeps only its hash).
        with contextlib.suppress(OSError):
            fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(raw)
        self._hook_token = raw
        return raw

    def _pane_env(self, session_id: str) -> dict:
        """The environment every session's pane gets. CLIQUE_SESSION is the id a
        hook or script reports against; CLIQUE_URL and CLIQUE_TOKEN are where and
        how. The last two wait on a known port, so nothing is aimed at a URL
        that is not listening."""
        env = {"CLIQUE": "1", "CLIQUE_SESSION": session_id}
        url = self._self_url()
        if url:
            env["CLIQUE_URL"] = url
            env["CLIQUE_TOKEN"] = self._hook_bearer()
        return env

    def _hooks_argv(self, cli) -> list[str]:
        """The ``--settings`` flag that wires a hook-speaking CLI to report its
        own state, or nothing for a CLI that does not speak it (or when there is
        no URL to report to)."""
        if not getattr(cli, "hooks", False) or not self._self_url():
            return []
        return ["--settings", _hooks_settings(sys.executable)]

    def create_session(self, body: dict) -> dict:
        cli_id = body.get("cli") or "shell"
        # The home of whoever started the server, not a path left over from the
        # machine this was written on. A self-hoster who omits cwd should get
        # somewhere that exists and is theirs.
        cwd = body.get("cwd") or str(Path.home())
        # A worktree isolates this agent's checkout so several can run on one
        # repo at once. Made now, run in, and remembered so deleting the
        # session can clean it up. The name and everything below then see the
        # worktree as the working directory, because it is.
        worktree = ""
        if body.get("worktree"):
            repo = gitinfo.repo_root(cwd)
            if not repo:
                raise ValueError("working directory is not a git repository")
            branch = (body.get("branch") or "").strip()
            if not branch:
                raise ValueError("a branch name is required to make a worktree")
            made, why = gitinfo.add_worktree(repo, branch)
            if not made:
                raise ValueError(why)
            cwd = worktree = made
        name = (body.get("name") or "").strip() or Path(cwd).name or cli_id
        mode = body.get("mode")
        # Resuming a past conversation is the same code path as starting a new
        # one, differing only in which argv the registry hands back. Nothing
        # here knows what resuming means for any particular CLI.
        prior = (body.get("cli_session_id") or "").strip() or None

        if not Path(cwd).is_dir():
            raise ValueError(f"working directory does not exist: {cwd}")

        session_id = new_id()
        argv = self.registry.launch_argv(
            cli_id,
            session_id=session_id,
            name=name,
            cwd=cwd,
            mode=mode,
            cli_session_id=prior,
        )
        cli = self.registry.get(cli_id)
        if not cli.installed:
            raise ValueError(f"{cli.label}: '{cli.command}' is not installed on this box")

        mux = tmux.mux_name(session_id)
        tmux.bootstrap()
        tmux.create(
            mux,
            cwd,
            argv + self._hooks_argv(cli),
            env=self._pane_env(session_id),
        )
        session = self.store.add_session(
            Session(
                id=session_id,
                name=name,
                cli=cli_id,
                cwd=cwd,
                mux=mux,
                socket=tmux.SOCKET,
                mode=mode or cli.default_mode,
                # Deliberately not auto-filed. A session you started yourself
                # lands in Ungrouped, at the top, where you will see it — filing
                # it is a decision made afterwards, if at all. Auto-filing by
                # directory stays where it earns its keep: adoption, where there
                # is nobody to ask.
                folder=body.get("folder") or None,
                cli_session_id=prior,
                worktree=worktree,
            )
        )
        return {"id": session.id, "worktree": worktree}

    def spawn(self, body: dict) -> dict:
        """Start several sessions from one request — a fleet in a click. Each is
        an ordinary create_session with a numbered name; with a worktree, each
        gets its own branch so they never collide. Bounded to 20, and one that
        fails is reported rather than sinking the rest."""
        try:
            count = int(body.get("count") or 1)
        except (TypeError, ValueError):
            count = 1
        count = max(1, min(count, 20))
        if count == 1:
            return {"created": [self.create_session(body)["id"]], "errors": []}
        base_name = (body.get("name") or "").strip()
        base_branch = (body.get("branch") or "").strip() or "agent"
        worktree = bool(body.get("worktree"))
        created: list[str] = []
        errors: list[str] = []
        for i in range(1, count + 1):
            sub = dict(body)
            sub["name"] = f"{base_name} {i}" if base_name else ""
            if worktree:
                sub["branch"] = f"{base_branch}-{i}"
            try:
                created.append(self.create_session(sub)["id"])
            except (ValueError, RegistryError, tmux.TmuxError) as exc:
                errors.append(str(exc))
        return {"created": created, "errors": errors}

    def broadcast(self, body: dict) -> dict:
        """Send one message to every live session at once, optionally narrowed to
        a folder or to an explicit list of session ids (`ids`). The thing a
        cockpit driving many agents most wants: one
        instruction, everyone hears it. Text goes in as a typed prompt (Enter by
        default, so a boxed CLI submits it); a `key` sends a single keypress to
        all instead. Dead sessions are skipped — nothing to type into — and one
        session's failure does not sink the rest."""
        folder = body.get("folder") or None
        ids = body.get("ids")
        only = {str(i) for i in ids} if isinstance(ids, list) else None
        key = str(body.get("key") or "")
        text = str(body.get("text", ""))
        enter = bool(body.get("enter", True))
        sent = []
        for session in self.store.sessions:
            if only is not None and session.id not in only:
                continue
            if folder and session.folder != folder:
                continue
            if not tmux.exists(session.mux, session.socket):
                continue
            try:
                if key:
                    tmux.send_key(session.mux, key, session.socket)
                else:
                    tmux.send_text(session.mux, text, session.socket, enter=enter)
                sent.append(session.id)
            except tmux.TmuxError:
                pass
        return {"count": len(sent), "sent": sent}

    def detect_cli(self, pane) -> str:
        """Which registered CLI is running in this pane.

        The pane's own `pane_current_command` is checked *last*, not first.
        A CLI launched from a wrapper or a login shell is a child of that
        shell, so the pane reports `bash` — and `bash` is a registered command
        (it is what `shell` runs), so trusting the pane first filed five live
        Claude Code sessions as plain shells.

        Nothing here knows anything about any particular CLI. It asks the
        registry which commands it recognises and looks for those.
        """
        by_command = {
            cli.command: cli_id
            for cli_id, cli in self.registry.types().items()
            if cli_id != "shell"
        }
        for comm, _args in tmux.descendants(pane.pid):
            if comm in by_command:
                return by_command[comm]
        if pane.command in by_command:
            return by_command[pane.command]
        return "shell"

    def adopt(self) -> dict:
        """Take over sessions started by the tool CLIque replaces.

        Adopted sessions stay on their original socket — tmux cannot move a
        session between servers, and killing one to recreate it would destroy
        the work this exists to preserve.

        An adopted session arrives fully furnished: the CLI it is actually
        running, the name its owner gave it, and the folder its directory
        belongs in. Adoption that lands thirty sessions in Ungrouped under
        their directory basenames is not a migration, it is a second job.
        """
        known = {s.mux: s for s in self.store.sessions}
        names = migrate.codeman_names()
        cwds = migrate.codeman_cwds()
        added, updated = [], []
        for pane in tmux.adoptable():
            cwd = pane.cwd or cwds.get(pane.mux, "")
            existing = known.get(pane.mux)
            if existing:
                if self._reconcile(existing, pane, cwd, names):
                    updated.append(existing.name)
                continue
            session = self.store.add_session(
                Session(
                    id=new_id(),
                    name=names.get(pane.mux) or Path(cwd).name or pane.mux,
                    cli=self.detect_cli(pane),
                    cwd=cwd,
                    mux=pane.mux,
                    socket=pane.socket,
                    folder=auto_folder(cwd, self.store.folders),
                    created=float(pane.created),
                    adopted=True,
                )
            )
            added.append(session.name)
            tmux.lock_size(session.mux, session.socket)
        return {"adopted": added, "updated": updated}

    def _reconcile(self, session: Session, pane, cwd: str, names: dict) -> bool:
        """Bring an already-adopted session up to what we can now detect.

        Adoption is not a one-shot: the first run of it filed five live Claude
        Code sessions as plain shells, under their directory basenames, in no
        folder — and once a session is known, adopting again used to skip it
        forever. Running Adopt a second time now repairs that rather than
        reporting nothing to do.

        The rule for names is the careful part: only a name that is still the
        one *we* derived gets replaced. Anything the user typed is theirs, and
        a migration that renames someone's session is worse than one that
        leaves a bad name alone.
        """
        changes = {}

        detected = self.detect_cli(pane)
        if session.cli == "shell" and detected != "shell":
            changes["cli"] = detected

        better = names.get(pane.mux)
        auto = {Path(cwd).name, pane.mux, ""}
        if better and session.name in auto and better != session.name:
            changes["name"] = better

        if not session.folder:
            folder = auto_folder(cwd, self.store.folders)
            if folder:
                changes["folder"] = folder

        if not changes:
            return False
        self.store.update_session(session.id, **changes)
        return True

    def stop_session(self, session_id: str) -> dict:
        """Stop the process for good. The record stays, so it can be started
        again. Kills, then *verifies*, then force-kills anything that survived,
        and reports whether it is actually dead -- a tmux hiccup or an adopted
        session on a foreign prefix must not be reported as stopped when it is
        still running, which is how a killed tab left a live session behind."""
        session = self.store.session(session_id)
        if not session:
            raise KeyError(session_id)
        with contextlib.suppress(tmux.TmuxError):
            if tmux.exists(session.mux, session.socket):
                tmux.kill(session.mux, session.socket, force=session.adopted)
        # If it is still there, it was adopted/foreign-prefixed or the first
        # kill did not take -- force it. Suppressed so the endpoint never 500s
        # on a wedged tmux; the returned `alive` carries the real outcome.
        if tmux.exists(session.mux, session.socket):
            with contextlib.suppress(tmux.TmuxError):
                tmux.kill(session.mux, session.socket, force=True)
        return {"id": session.id, "alive": tmux.exists(session.mux, session.socket)}

    def orphans(self) -> list[dict]:
        """Live sessions on our own socket that no record points to.

        A record removed without killing its tmux leaks the process and its
        memory: it keeps running, but nothing in the panel can see or stop it.
        A just-started session has its tmux a beat before its record, so only
        sessions past a short grace window count -- never one mid-creation.
        Heaviest first, so the memory worth reclaiming is at the top."""
        known = {sess.mux for sess in self.store.sessions}
        now = time.time()
        loose = [
            p
            for p in tmux.list_sessions(tmux.SOCKET, prefix=tmux.PREFIX)
            if p.mux not in known and not p.mux.startswith("sm-view-") and now - p.created > 120
        ]
        if not loose:
            return []
        rss_map = sysinfo.rss_by_root([p.pid for p in loose])
        return [
            {
                "mux": p.mux,
                "command": p.command,
                "pid": p.pid,
                "idle": round(now - p.activity),
                "rss": rss_map.get(p.pid, 0) * 1024,
            }
            for p in sorted(loose, key=lambda p: -rss_map.get(p.pid, 0))
        ]

    def reap_orphans(self, muxes=None) -> dict:
        """Kill leaked sessions and reclaim their memory. With no list, every
        orphan; otherwise only the named ones that are still orphans. A mux
        that belongs to a real record is never killed, whatever was asked."""
        known = {sess.mux for sess in self.store.sessions}
        targets = {o["mux"] for o in self.orphans()}
        if muxes is not None:
            targets &= set(muxes)
        killed = []
        for mux in sorted(targets):
            if mux in known:
                continue
            with contextlib.suppress(tmux.TmuxError):
                tmux.kill(mux, tmux.SOCKET)
                killed.append(mux)
        return {"killed": killed}

    def start_session(self, session_id: str) -> dict:
        """Start a stopped session again. Same id, name, folder, directory.

        When the CLI was first launched with our session id (Claude's
        ``--session-id``), that is also the resume key. Other CLIs resume
        only if we already stored ``cli_session_id``. A shell has nothing
        to resume — it starts again in the same place.
        """
        session = self.store.session(session_id)
        if not session:
            raise KeyError(session_id)
        if tmux.exists(session.mux, session.socket):
            raise ValueError("session is already running")
        if not Path(session.cwd).is_dir():
            raise ValueError(f"working directory does not exist: {session.cwd}")
        cli = self.registry.get(session.cli)
        if not cli.installed:
            raise ValueError(f"{cli.label}: '{cli.command}' is not installed on this box")
        prior = session.cli_session_id
        if not prior and cli.resume:
            # Only treat our id as theirs if the first launch handed it over.
            tokens = " ".join(cli.args)
            if "{id}" in tokens or "{uuid}" in tokens:
                prior = session.id
        argv = self.registry.launch_argv(
            session.cli,
            session_id=session.id,
            name=session.name,
            cwd=session.cwd,
            mode=session.mode,
            cli_session_id=prior,
        )
        tmux.bootstrap()
        tmux.create(
            session.mux,
            session.cwd,
            argv + self._hooks_argv(cli),
            socket=session.socket,
            env=self._pane_env(session.id),
        )
        if prior and not session.cli_session_id:
            self.store.update_session(session.id, cli_session_id=prior)
        return {"id": session.id}

    def delete_session(self, session_id: str) -> dict:
        session = self.store.session(session_id)
        if not session:
            raise KeyError(session_id)
        # Adopted sessions do not carry our name prefix, so the engine's guard
        # would refuse them. Deleting one is explicit, which is what force means.
        if tmux.exists(session.mux, session.socket):
            tmux.kill(session.mux, session.socket, force=session.adopted)
        self.store.remove_session(session_id)
        # A worktree we made is ours to clean up -- but never at the cost of
        # someone's uncommitted work: if the checkout is dirty it stays, and so
        # does the branch, rather than deleting changes out from under them.
        removed = False
        if session.worktree and gitinfo.worktree_clean(session.worktree):
            removed = gitinfo.remove_worktree(session.worktree)
        return {"deleted": session_id, "worktree_removed": removed}


class Server(ThreadingHTTPServer):
    """The HTTP server, with a listen backlog that fits one page load.

    Python's default `request_queue_size` is 5. A single page load is well
    past that — the document, the stylesheet, three scripts, the brand mark,
    the favicon, the manifest, then /api/state, /api/resumable and a WebSocket
    upgrade. Overflowing the backlog drops connections, and the symptom is an
    app that opens to an empty sidebar and quietly fills in three seconds
    later when the poll comes round.

    Both values have to be class attributes: `__init__` binds and listens, so
    setting them on the instance afterwards is too late.
    """

    daemon_threads = True
    request_queue_size = 128


#: Where a pasted image lands: inside the session's own working directory, so
#: the CLI can read it with a relative path and it travels with the project.
#: The name matches what the tool CLIque replaces used, so existing habits and
#: any .gitignore already covering it keep working.
PASTE_DIR = ".claude-images"

#: Biggest image accepted. A screenshot is well under a megabyte; ten is
#: generous and stops a paste being a memory attack.
MAX_PASTE_BYTES = 10 * 1024 * 1024

#: And the ceiling on any request body, base64 overhead included.
MAX_BODY_BYTES = MAX_PASTE_BYTES * 4 // 3 + 8192

#: Leading bytes -> extension. The type comes from the *content*, never from a
#: filename or a Content-Type the browser supplied: this route writes a file
#: into a working directory, so what it is has to be established here.
IMAGE_MAGIC = (
    (b"\x89PNG\r\n\x1a\n", ".png"),
    (b"\xff\xd8\xff", ".jpg"),
    (b"GIF87a", ".gif"),
    (b"GIF89a", ".gif"),
    (b"BM", ".bmp"),
)


#: Containment, defined once. The paste route and the artifact routes are the
#: two places a working directory is touched, and they must agree about what
#: "inside it" means.
_inside = artifacts.inside


#: Extension -> the type it is served as. Beside IMAGE_MAGIC so the two cannot
#: drift: nothing is ever served as a type the magic did not establish.
IMAGE_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
    ".webp": "image/webp",
}


def image_extension(data: bytes) -> str:
    """The extension for these bytes, or "" if they are not an image we know."""
    for magic, ext in IMAGE_MAGIC:
        if data.startswith(magic):
            return ext
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return ""


#: Paths served without a session. Brand images, the favicon and the web app
#: manifest, and nothing else.
#:
#: The sign-in page asks for a password and then tries to draw the logo above
#: the field — and the logo was behind the password. Same for the favicon a
#: browser fetches for the login tab, and for the manifest an installed PWA
#: re-reads on launch. None of the three carries information: they are the same
#: files anyone sees on the README.
#:
#: Deliberately a prefix allowlist and not "any file", because everything else
#: under web/ — the app shell, its JavaScript, its stylesheet — describes the
#: panel to someone who has not signed in.
PUBLIC_ASSETS = ("/brand/", "/favicon.ico", "/manifest.webmanifest")


#: Settings that are credentials, not preferences. They go to the browser as a
#: boolean so the UI can say "configured" without ever holding the value.
SECRET_SETTINGS = ("webhook_secret",)

#: URL settings whose path can itself carry a credential — a Discord/Slack/ntfy
#: webhook token lives in the URL. Shown to the cookie operator who set them,
#: withheld from API tokens: the panel cannot tell a token reading back its own
#: config from one exfiltrating it, and a read-only token should see neither.
URL_SETTINGS = ("webhook_url",)


def redacted(settings: dict, *, reveal_urls: bool = False) -> dict:
    out = dict(settings)
    for key in SECRET_SETTINGS:
        out[f"{key}_set"] = bool(out.get(key))
        out[key] = ""
    for key in URL_SETTINGS:
        out[f"{key}_set"] = bool(out.get(key))
        if not reveal_urls:
            out[key] = ""
    return out


#: A password form is a few hundred bytes. This is the only body an
#: unauthenticated caller gets to send, so it is capped hard and separately.
MAX_LOGIN_BYTES = 8 * 1024

#: A terminal is at most this big. Unbounded values arrive from a query string
#: and from control frames — both are the client talking — and both end up in
#: an ioctl and in `tmux resize-window`.
MAX_COLS, MAX_ROWS = 500, 300

#: A peek is a glance, not a window. Enough lines to see a question and the
#: line before it; not so many that a tooltip becomes a second terminal.
PEEK_DEFAULT, PEEK_MIN, PEEK_MAX = 6, 2, 40

#: Scrollback searched to find those lines. Larger than what is shown, because
#: the frame is dropped afterwards and a pane can be almost entirely frame.
PEEK_WINDOW = 160

#: How many panes keep a cached peek. Small on purpose — this is a convenience
#: for the handful of rows a pointer actually crosses, not an index.
PEEK_CACHE = 64

#: mux -> ((mux, activity, lines), rows). Module level, because a request
#: handler is built and thrown away per request and a cache on one would be a
#: cache of exactly one lookup.
_PEEKED: dict[str, tuple[tuple, list[str]]] = {}
_PEEK_LOCK = threading.Lock()


def _terminal_size(cols, rows) -> tuple[int, int]:
    """Clamp a client-supplied terminal size, tolerating rubbish."""

    def one(value, fallback: int, ceiling: int) -> int:
        try:
            return max(2, min(int(value), ceiling))
        except (TypeError, ValueError):
            return fallback

    return one(cols, 120, MAX_COLS), one(rows, 32, MAX_ROWS)


def _is_public_asset(path: str) -> bool:
    """Whether this may be served before login.

    Only what the login page itself draws. This was a `startswith("/brand/")`,
    and `urlparse` does not collapse `..` — so `/brand/../app.js` passed the
    test and `_static` happily resolved it back to the application shell,
    which is the one thing the check exists to keep behind the password.

    Resolve first, then ask whether the answer is still inside the directory.
    """
    target = (WEB / path.lstrip("/")).resolve()
    return artifacts.inside(target, (WEB / "brand").resolve())


class Handler(BaseHTTPRequestHandler):
    panel: Panel = None  # set by serve()
    server_version = "clique"  # no version: it tells a scanner what to try
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args) -> None:
        # One line per request would drown the journal in terminal polling.
        pass

    # ---------------------------------------------------------------- helpers

    def _send(
        self,
        status: int,
        body: bytes,
        content_type: str,
        extra: dict[str, str] | None = None,
        nonce: str = "",
    ) -> None:
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
            "img-src 'self' data:; connect-src 'self'; font-src 'self'; "
            "worker-src 'self'; "
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
        # A Content-Length that is not a number is a malformed request, not a
        # 500. `int("abc")` here used to take the whole response down.
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return {}
        # <= 0 covers a negative length too: rfile.read(-1) reads to EOF and
        # hangs the thread until the connection closes.
        if length <= 0:
            return {}
        # Bounded, because a pasted image arrives through here and an unbounded
        # Content-Length is a one-request way to exhaust memory.
        if length > MAX_BODY_BYTES:
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
            return False  # an opaque origin is a sandboxed frame, not our page
        host = self._fwd_host()
        # Compare host AND port, parsed with urlsplit so [::1] stays ::1 rather
        # than "[". A different *port* of the same host is a different origin —
        # the CSWSH this check exists to stop — and the old split-on-":" merged
        # http://127.0.0.1:9999 with our :3200, and mangled every IPv6 literal.
        # Scheme is deliberately not compared: behind a tunnel the panel cannot
        # always tell its own, and host + port already separates the origins
        # that matter here.
        try:
            o = urllib.parse.urlsplit(origin)
            h = urllib.parse.urlsplit("//" + host)  # a Host header carries no scheme
        except ValueError:
            return False
        if o.hostname and o.hostname == h.hostname and o.port == h.port:
            return True
        # A hostname the operator explicitly named in CLIQUE_ALLOWED_HOSTS is
        # trusted as an origin too — they chose it. Empty by default, and never
        # host_allowed(): that allowlist accepts any ts.net / ngrok / bare IPv4
        # for DNS-rebinding on the *Host*, which as an Origin test would count
        # anyone's free subdomain as our own page.
        return bool(o.hostname) and o.hostname in self.panel.allowed_hosts

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

    def _may_read(self) -> bool:
        """Read access: a cookie session, or a bearer carrying read or write.

        The ``attention``-only hook token lives in every session's environment,
        so a prompt-injected agent can read it. It is shut out here — it may
        report a status to POST /attention and nothing else. Every operator
        token carries ``read`` (tokens default to read+write, and even
        ``--read-only`` keeps read), so this narrows the read surface to exactly
        the hook token it was meant to exclude."""
        if self._cookie_authed:
            return True
        token = self._bearer
        return bool(token and (token.allows("read") or token.allows("write")))

    def _fwd_host(self) -> str:
        """The host the browser reached us on. A forwarded host is believed only
        behind a trusted proxy; otherwise Host is the truth and X-Forwarded-Host
        is a header any client can forge past the DNS-rebinding gate."""
        if self.panel.trust_proxy:
            forwarded = self.headers.get("X-Forwarded-Host")
            if forwarded:
                return forwarded
        return self.headers.get("Host") or ""

    def _secure(self) -> bool:
        return (
            self.panel.trust_proxy and self.headers.get("X-Forwarded-Proto", "").lower() == "https"
        )

    def _host_ok(self) -> bool:
        """Runs before anything else, including auth.

        A rebound host has to be refused before a handler sees the request —
        after that point the browser already considers the page same-origin
        with ours and every other check is arguing with a decision the browser
        has already made.
        """
        host = self._fwd_host()
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
        # Before the auth gate: see Panel.health for why, and for what an
        # anonymous caller is and is not told.
        if path == "/healthz":
            return self._json(self.panel.health(self._authed))
        if not self._may_read():
            if path.startswith("/api"):
                return self._json({"error": "unauthorized"}, 401)
            if _is_public_asset(path):
                return self._static(path)
            nonce = secrets.token_urlsafe(16)
            return self._send(200, login_page(nonce=nonce), "text/html; charset=utf-8", nonce=nonce)

        try:
            if path == "/api/state":
                return self._json(self.panel.state(reveal_urls=self._cookie_authed))
            if path == "/api/stats":
                snap = sysinfo.snapshot(self.panel.clients)
                snap["guard"] = self.panel.resource_guard()
                return self._json(snap)
            if path == "/api/stats/history":
                minutes = max(1, min(int(query.get("minutes") or 60), 180))
                return self._json(self.panel.history.series(minutes))
            if path == "/api/resumable":
                # The folder is worked out here, not in the browser: the rule
                # for which directory belongs where lives in the store and
                # there should be exactly one copy of it.
                folders = self.panel.store.folders
                out = []
                for conv in self.panel.conversations.conversations():
                    row = conv.as_dict()
                    row["folder"] = auto_folder(conv.cwd, folders)
                    out.append(row)
                return self._json(out)
            if path == "/api/prompts":
                # Individual prompts, newest first, for the palette's prompt
                # search. Read from each CLI's own history — nothing is logged
                # twice — and bounded: a tail of each recent transcript, not the
                # whole thing. See clique/history.py.
                try:
                    limit = max(1, min(int(query.get("limit") or 400), 400))
                except (TypeError, ValueError):
                    limit = 400
                return self._json(self.panel.conversations.prompts(limit=limit))
            if path == "/api/changelog":
                # Parsed from the same CHANGELOG.md the repo ships, so the
                # release notes in the app cannot drift from the ones on disk.
                return self._json(changelog.entries())
            if path == "/api/adoptable":
                known = {s.mux for s in self.panel.store.sessions}
                return self._json([p.as_dict() for p in tmux.adoptable() if p.mux not in known])
            if path == "/api/orphans":
                return self._json(self.panel.orphans())
            if path == "/api/llm/providers":
                return self._json(self.panel.llm_list())
            if path == "/api/browse":
                return self._json({"dirs": workspace.complete(query.get("path") or "")})
            if path == "/api/workspace":
                return self._json(
                    workspace.look(
                        query.get("cwd") or "",
                        [
                            x
                            for x in self.panel.store.sessions
                            if not x.archived and x.mux in self.panel.live()
                        ],
                    )
                )
            if path.startswith("/api/sessions/") and path.endswith("/peek"):
                return self._peek(path.split("/")[3], query.get("lines") or "")
            if path.startswith("/api/sessions/") and path.endswith("/artifacts"):
                return self._artifacts(path.split("/")[3])
            if path.startswith("/api/sessions/") and path.endswith("/artifact"):
                return self._artifact(path.split("/")[3], query.get("rel") or "")
            if path.startswith("/api/sessions/") and path.endswith("/file"):
                if query.get("raw") in {"1", "true", "yes"}:
                    return self._file_raw(path.split("/")[3], query.get("path") or "")
                return self._file(path.split("/")[3], query.get("path") or "")
            if path.startswith("/api/sessions/") and path.endswith("/transcript"):
                return self._transcript(path.split("/")[3])
            if path.startswith("/api/sessions/") and path.endswith("/diff"):
                return self._diff(path.split("/")[3])
            if path.startswith("/api/sessions/") and path.endswith("/usage"):
                return self._usage(path.split("/")[3])
            if path.startswith("/api/sessions/") and path.endswith("/wait"):
                wanted = {w for w in (query.get("for") or "idle").split(",") if w}
                try:
                    timeout = float(query.get("timeout") or 60)
                except (TypeError, ValueError):
                    timeout = 60.0
                try:
                    return self._json(
                        self.panel.wait_for_state(path.split("/")[3], wanted, timeout)
                    )
                except KeyError:
                    return self._json({"error": "not found"}, 404)
            if path.startswith("/api"):
                return self._json({"error": "not found"}, 404)
            return self._static(path)
        except Exception as exc:  # noqa: BLE001
            return self._fail(exc)

    def _transcript(self, session_id: str) -> None:
        """The conversation for a session, for scroll-back on a CLI that keeps
        none. The transcript's own id is the one we resumed if this was a
        resume, otherwise the id we launched it with -- Claude's --session-id is
        our own session id, which is why a fresh session needs nothing stored."""
        session = self.panel.store.session(session_id)
        if not session:
            return self._json({"error": "not found"}, 404)
        tid = session.cli_session_id or session.id
        turns = self.panel.conversations.session_transcript(session.cli, tid)
        return self._json({"cli": session.cli, "name": session.name, "turns": turns})

    def _diff(self, session_id: str) -> None:
        """What a session's agent has changed but not committed, for review.

        ``repo: false`` when the checkout is not a git repository; ``empty: true``
        when it is clean. The diff itself is git's, unified, computed on demand
        rather than on the poll — see gitinfo.diff for what it includes."""
        session = self.panel.store.session(session_id)
        if not session:
            return self._json({"error": "not found"}, 404)
        info = gitinfo.diff(session.cwd)
        if info is None:
            return self._json({"repo": False, "name": session.name})
        return self._json({"repo": True, "name": session.name, **info})

    def _usage(self, session_id: str) -> None:
        """Tokens a session has spent, from its own transcript. `has_data` is
        false for a CLI that keeps no usage (or none logged yet); the transcript
        id is the one we resumed, or our own — the same rule as /transcript."""
        session = self.panel.store.session(session_id)
        if not session:
            return self._json({"error": "not found"}, 404)
        tid = session.cli_session_id or session.id
        data = self.panel.conversations.session_usage(session.cli, tid)
        return self._json({"name": session.name, "cli": session.cli, **data})

    def _peek(self, session_id: str, want: str) -> None:
        """The last few lines of a pane, without opening it.

        The question this answers is "is that one waiting on me", asked about a
        session you are not looking at. Answering it today means opening the
        tab, which changes what you are looking at — and then changes it back.

        Deliberately a *pull*. Nothing captures a pane until somebody hovers
        over a row and stays there, so a sidebar of twenty sessions costs
        exactly nothing until it is asked a question. That is the same bargain
        the attention ladder makes, and it is why neither of them shows up in
        an idle panel's cost.

        Colour is stripped rather than rendered. A peek is a glance at what the
        text says; carrying ANSI across the wire would mean a second terminal
        renderer in the panel for something that is eight lines in a tooltip.
        """
        session = self.panel.store.session(session_id)
        if not session:
            return self._json({"error": "no such session"}, 404)
        try:
            lines = max(PEEK_MIN, min(int(want), PEEK_MAX))
        except (TypeError, ValueError):
            lines = PEEK_DEFAULT

        pane = self.panel.live().get(session.mux)
        if pane is None:
            return self._json({"lines": [], "alive": False, "activity": 0})

        # Cached against the pane's own activity clock, exactly as the
        # attention tier is: the entry is invalid the moment anything is
        # printed and valid forever while nothing is. Hovering back and forth
        # across a quiet sidebar is one capture, not one per pass.
        activity = int(pane.activity or 0)
        key = (session.mux, activity, lines)
        with _PEEK_LOCK:
            hit = _PEEKED.get(session.mux)
            if hit and hit[0] == key:
                return self._json({"lines": hit[1], "alive": True, "activity": activity})

        try:
            # A generous window, then filtered down to `lines`. Capturing only
            # what is to be shown was wrong once the frame started being
            # dropped: on a pane that is mostly box drawing, eight captured
            # lines can contain one that says anything.
            text = tmux.capture(session.mux, session.socket, lines=PEEK_WINDOW, styled=False)
        except (tmux.TmuxError, OSError):
            return self._json({"lines": [], "alive": True, "activity": activity})

        # What a peek is for is the last thing that *said* something, and a
        # modern CLI's pane is mostly frame: box rules, separators, an input
        # box drawn around nothing. Showing eight raw lines of that buries the
        # one line that answers the question under seven that do not.
        #
        # So the decoration is dropped. Not by knowing anything about any CLI —
        # a line is dropped when every character in it is a box-drawing glyph,
        # a rule or whitespace, which is a property of the text and true of
        # every tool that draws a frame.
        rows = attention.content_lines(text)
        rows = rows[-lines:]
        with _PEEK_LOCK:
            _PEEKED[session.mux] = (key, rows)
            # Bounded, because a panel that runs for weeks creates and destroys
            # sessions all day and every one of them would leave an entry.
            if len(_PEEKED) > PEEK_CACHE:
                for stale in list(_PEEKED)[:-PEEK_CACHE]:
                    del _PEEKED[stale]
        return self._json({"lines": rows, "alive": True, "activity": activity})

    def _static(self, path: str) -> None:
        name = "index.html" if path == "/" else path.lstrip("/")
        target = (WEB / name).resolve()
        # Path traversal check: resolve() first, then confirm real containment —
        # a string prefix would also match a sibling like ".../web-evil".
        base = WEB.resolve()
        if (target != base and base not in target.parents) or not target.is_file():
            return self._send(404, b"not found", "text/plain")
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type.endswith("javascript"):
            content_type += "; charset=utf-8"
        # Vendored assets are immutable; our own must not be cached, or a fix
        # ships and the browser keeps the bug.
        cache = "public, max-age=604800" if "/vendor/" in path else "no-store"
        try:
            body = target.read_bytes()
        except OSError:
            # Deleted or made unreadable between is_file() and here. A missing
            # asset is a 404; it is not worth a stack trace and a 500.
            return self._send(404, b"not found", "text/plain")
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
                    return self._send(
                        429,
                        login_page("Too many attempts. Wait a minute.", nonce),
                        "text/html; charset=utf-8",
                        nonce=nonce,
                    )
                # Deliberately slow, and counted: this endpoint is reachable by
                # anyone who can reach the tunnel, and guessing should be both
                # expensive and self-limiting.
                self._record_failure(who)
                time.sleep(1.0)
                nonce = secrets.token_urlsafe(16)
                return self._send(
                    401,
                    login_page("Wrong password.", nonce),
                    "text/html; charset=utf-8",
                    nonce=nonce,
                )
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

        # /attention is the one write a state hook makes, and its token is scoped
        # to exactly that — so an `attention`-scoped bearer is let through here
        # and nowhere else. Every other write, and any other token, still faces
        # the full write-and-same-origin gate below.
        token = self._bearer
        attention_only = (
            path.startswith("/api/sessions/")
            and path.endswith("/attention")
            and token is not None
            and token.allows("attention")
        )
        if not attention_only:
            allowed, reason = self._may_write()
            if not allowed:
                return self._json({"error": reason}, 401 if reason == "unauthorized" else 403)

        try:
            body = self._body()
            if path == "/api/sessions":
                return self._json(self.panel.create_session(body), 201)
            if path == "/api/sessions/adopt":
                return self._json(self.panel.adopt())
            if path == "/api/broadcast":
                return self._json(self.panel.broadcast(body))
            if path == "/api/sessions/spawn":
                return self._json(self.panel.spawn(body), 201)
            if path == "/api/orphans/reap":
                return self._json(self.panel.reap_orphans(body.get("muxes")))
            if path == "/api/llm/providers":
                return self._json(self.panel.llm_create(body), 201)
            if path.startswith("/api/llm/providers/"):
                parts = path.split("/")
                provider_id = parts[4]
                action = parts[5] if len(parts) > 5 else ""
                if action == "test":
                    result = self.panel.llm_test(provider_id)
                elif action == "delete":
                    result = self.panel.llm_delete(provider_id)
                elif not action:
                    result = self.panel.llm_update(provider_id, body)
                else:
                    return self._json({"error": "not found"}, 404)
                if result is None:
                    return self._json({"error": "no such provider"}, 404)
                return self._json(result)
            if path == "/api/workspace":
                # A directory that does not exist yet is the commonest reason a
                # new session fails, and "go and find a shell" is a poor answer
                # from a tool whose whole job is running shells.
                target = body.get("cwd") or ""
                made, why = workspace.make(target)
                if not made:
                    return self._json({"error": why}, 400)
                return self._json(
                    workspace.look(
                        target,
                        [
                            x
                            for x in self.panel.store.sessions
                            if not x.archived and x.mux in self.panel.live()
                        ],
                    ),
                    201,
                )
            if path == "/api/folders":
                folder = self.panel.store.add_folder(
                    body.get("name") or "New folder", body.get("color")
                )
                # The whole record, not just the id. A caller that asked for a
                # colour has no other way to learn that the one it sent was
                # refused, and "the API is the whole surface" means a script
                # should be able to see what it created without a second GET.
                return self._json(dataclasses.asdict(folder), 201)
            if path == "/api/reorder":
                sessions = body.get("sessions") or []
                folders = body.get("folders") or []
                if sessions:
                    self.panel.store.reorder_sessions(sessions)
                if folders:
                    self.panel.store.reorder_folders(folders)
                return self._json({"ok": True})
            if path.startswith("/api/sessions/") and path.endswith("/send"):
                return self._send_input(path.split("/")[3], body)
            if path.startswith("/api/sessions/") and path.endswith("/paste"):
                return self._paste_image(path.split("/")[3], body)
            if path == "/api/webhook/test":
                # Every webhook UI needs this and the reason is always the
                # same: you paste a URL and you want to know it works now, not
                # the next time something finishes at three in the morning.
                settings = self.panel.store.settings
                url = str(settings.get("webhook_url") or "")
                if not url:
                    return self._json({"error": "no webhook URL set"}, 400)
                notify.post(
                    url,
                    str(settings.get("webhook_secret") or ""),
                    {
                        "event": "test",
                        "at": int(time.time()),
                        "session": {
                            "id": "",
                            "name": "Test",
                            "cli": "",
                            "cli_label": "",
                            "folder": None,
                            "cwd": "",
                        },
                        "text": "CLIque is wired up.",
                        "url": str(settings.get("panel_url") or ""),
                    },
                )
                return self._json({"ok": True})
            if path.startswith("/api/sessions/") and path.endswith("/attention"):
                return self._attention(path.split("/")[3], body)
            if path.startswith("/api/sessions/") and path.endswith("/kill"):
                return self._json(self.panel.stop_session(path.split("/")[3]))
            if path.startswith("/api/sessions/") and path.endswith("/start"):
                return self._json(self.panel.start_session(path.split("/")[3]))
            if path.startswith("/api/sessions/") and path.endswith("/seen"):
                found = self.panel.store.touch_session(path.split("/")[3])
                if not found:
                    return self._json({"error": "no such session"}, 404)
                return self._json({"ok": True, "last_seen": found.last_seen})
            return self._json({"error": "not found"}, 404)
        except KeyError:
            return self._json({"error": "no such session"}, 404)
        except (ValueError, RegistryError, tmux.TmuxError) as exc:
            return self._json({"error": str(exc)}, 400)
        except Exception as exc:  # noqa: BLE001
            return self._fail(exc)

    def _attention(self, session_id: str, body: dict) -> None:
        """Let a session say, itself, that it is stuck.

        The top tier of the attention ladder, and the only one that is not a
        guess. A user wires this to whatever hook their CLI already offers —
        a Claude Code hook, a shell trap, the last line of a script — and
        CLIque believes what arrives without ever learning what sent it.

        That inversion is the point: the vendor-specific knowledge lives in
        someone's own hook config, where they can fix it the day it changes,
        instead of in this codebase where it would be a release.
        """
        session = self.panel.store.session(session_id)
        if not session:
            return self._json({"error": "no such session"}, 404)
        state = str(body.get("state") or "").strip().lower()
        if state not in ("waiting", "error", "clear"):
            return self._json({"error": 'state must be "waiting", "error" or "clear"'}, 400)
        # Optional "why", bounded — a hook's matcher name, not free text.
        note = str(body.get("note") or "").strip().lower()[:16]

        pane = self.panel.live().get(session.mux)
        # Stamped with the pane's own clock rather than the wall clock, so the
        # staleness test downstream compares like with like.
        updated = self.panel.store.update_session(
            session_id,
            signal="" if state == "clear" else state,
            signal_at=float(pane.activity if pane else 0),
            signal_note="" if state == "clear" else note,
        )
        return self._json({"ok": True, "signal": updated.signal if updated else ""})

    def _file(self, session_id: str, asked: str) -> None:
        """Read-only glance at a path the pane printed.

        Relative to the session's working directory, or absolute / ``~/``.
        Text is capped; images are described here and served by `_file_raw`.
        """
        session = self.panel.store.session(session_id)
        if not session:
            return self._json({"error": "no such session"}, 404)
        return self._json(files.inspect(session.cwd, asked))

    def _file_raw(self, session_id: str, asked: str) -> None:
        """The bytes of an image the pane pointed at."""
        session = self.panel.store.session(session_id)
        if not session:
            return self._json({"error": "no such session"}, 404)
        info = files.inspect(session.cwd, asked)
        if info["kind"] != "image" or not info["path"]:
            return self._json({"error": "not an image"}, 415)
        target = Path(info["path"])
        try:
            if target.stat().st_size > MAX_PASTE_BYTES:
                return self._json({"error": "image too large"}, 413)
            raw = target.read_bytes()
        except OSError:
            return self._json({"error": "could not read it"}, 404)
        kind = IMAGE_TYPES.get(image_extension(raw))
        if not kind:
            return self._json({"error": "not an image this understands"}, 415)
        return self._send(200, raw, kind, {"Cache-Control": "no-store"})

    def _artifacts(self, session_id: str) -> None:
        """What this session's working directory has to show.

        The other half of paste. A terminal cannot draw a picture, so an agent
        that takes a screenshot has, until it is listed here, produced
        something you have to leave the panel to look at.
        """
        session = self.panel.store.session(session_id)
        if not session:
            return self._json({"error": "no such session"}, 404)
        settings = self.panel.store.settings
        if not settings.get("artifacts_show", True):
            return self._json([])
        return self._json(
            artifacts.scan(
                session.cwd,
                settings.get("artifact_dirs") or [],
                # Only what appeared while this session has been running. A
                # directory full of committed images is the project, not the work.
                since=session.created,
            )
        )

    def _artifact(self, session_id: str, relative: str) -> None:
        """Serve one image out of a session's working directory.

        The type comes from the bytes, never from the extension: this reads a
        file a browser asked for by name, and what a name claims about its
        contents is not evidence.
        """
        session = self.panel.store.session(session_id)
        if not session:
            return self._json({"error": "no such session"}, 404)
        target = artifacts.locate(session.cwd, relative)
        if target is None:
            return self._json({"error": "not an artifact of this session"}, 404)
        try:
            if target.stat().st_size > MAX_PASTE_BYTES:
                return self._json({"error": "image too large"}, 413)
            raw = target.read_bytes()
        except OSError:
            return self._json({"error": "could not read it"}, 404)
        kind = IMAGE_TYPES.get(image_extension(raw))
        if not kind:
            return self._json({"error": "not an image this understands"}, 415)
        # No-store, because the file behind a name can be overwritten by the
        # next screenshot and a cached one would be a lie.
        return self._send(200, raw, kind, {"Cache-Control": "no-store"})

    def _paste_image(self, session_id: str, body: dict) -> None:
        """Write a pasted image into the session's directory and say where.

        Sharing a screenshot with an agent is otherwise impossible: a terminal
        has no way to carry an image, so the only thing that can cross is a
        path. The browser has the bytes, the pane has the CLI, and this is the
        bridge — it saves the file and hands back a path the CLI can open.

        Containment is checked *after* resolution, not before, because that is
        the only order that survives a symlinked working directory. The write
        can only ever land inside `<cwd>/.claude-images`.
        """
        session = self.panel.store.session(session_id)
        if not session:
            return self._json({"error": "no such session"}, 404)

        try:
            raw = base64.b64decode(body.get("data") or "", validate=True)
        except (ValueError, binascii.Error):
            return self._json({"error": "not valid base64"}, 400)
        if not raw:
            return self._json({"error": "empty paste"}, 400)
        if len(raw) > MAX_PASTE_BYTES:
            return self._json({"error": "image too large"}, 413)

        ext = image_extension(raw)
        if not ext:
            return self._json({"error": "not an image this understands"}, 415)

        try:
            root = Path(session.cwd).resolve(strict=True)
        except OSError:
            return self._json({"error": "working directory is gone"}, 409)
        if not root.is_dir():
            return self._json({"error": "working directory is gone"}, 409)

        target_dir = (root / PASTE_DIR).resolve()
        if target_dir != root / PASTE_DIR and not _inside(target_dir, root):
            return self._json({"error": "refused: escapes the working directory"}, 403)
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
            target_dir = target_dir.resolve(strict=True)
            if not _inside(target_dir, root):
                return self._json({"error": "refused: escapes the working directory"}, 403)
            name = f"paste-{int(time.time() * 1000)}-{secrets.token_hex(4)}{ext}"
            target = target_dir / name
            # Exclusive create: never overwrite something already there, even
            # if a name somehow collides.
            with open(target, "xb") as fh:
                fh.write(raw)
        except OSError as exc:
            return self._json({"error": f"could not save: {exc}"}, 500)

        return self._json(
            {
                "path": str(target),
                "relative": f"{PASTE_DIR}/{name}",
                "bytes": len(raw),
            },
            201,
        )

    def _send_input(self, session_id: str, body: dict) -> None:
        session = self.panel.store.session(session_id)
        if not session:
            return self._json({"error": "no such session"}, 404)
        if body.get("key"):
            tmux.send_key(session.mux, body["key"], session.socket)
        else:
            tmux.send_text(
                session.mux,
                body.get("text", ""),
                session.socket,
                enter=bool(body.get("enter", True)),
            )
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
                updated = self.panel.store.update_settings(body)
                # Setting a webhook URL starts the watcher; clearing it stops
                # the thread rather than leaving it spinning over a URL that
                # is no longer there.
                self.panel.watcher.ensure()
                # Same for the status feeds: turning them off should stop the
                # thread now, not at the next restart.
                self.panel.services.ensure()
                # Redact on the way out too: GET already hides webhook_secret,
                # and PATCH returning it raw handed a write token the secret.
                return self._json(redacted(updated, reveal_urls=self._cookie_authed))
            if len(parts) == 3 and parts[1] == "sessions":
                # Only fields the caller actually sent. `folder: null` is a
                # real value (drag to Ungrouped), so "absent" and "null" have
                # to stay distinguishable — passing body.get() for every field
                # would make a rename silently unfile the session.
                allowed = {"name", "folder", "mode", "archived", "draft", "pinned"}
                fields = {k: v for k, v in body.items() if k in allowed}
                updated = self.panel.store.update_session(parts[2], **fields)
                return self._json({"ok": bool(updated)}, 200 if updated else 404)
            if len(parts) == 3 and parts[1] == "folders":
                updated = self.panel.store.update_folder(
                    parts[2],
                    name=body.get("name"),
                    color=body.get("color"),
                    collapsed=body.get("collapsed"),
                )
                # The record, for the same reason POST returns it: a colour
                # that failed validation is kept, and `{"ok": true}` is not a
                # way to find that out.
                if not updated:
                    return self._json({"ok": False}, 404)
                return self._json(dataclasses.asdict(updated))
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
        # Full traceback to the server's stderr only; the client gets a generic
        # line, because str(exc) on an OSError carries absolute host paths.
        traceback.print_exc()
        self._json({"error": "internal error"}, 500)

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

        if not self._may_read():
            return self._send(401, b"unauthorized", "text/plain")

        # A cookie without an Origin header is not a browser. Every browser
        # sends Origin on a WebSocket handshake without exception, so a
        # cookie-authenticated handshake that lacks one did not come from a
        # page — and the cookie is the credential a hostile page would be
        # borrowing. Token callers are exempt: an Authorization header is
        # never attached by a browser on its own, so it proves intent.
        if self._bearer is None and origin is None:
            return self._send(403, b"cross-site websocket blocked", "text/plain")

        # Read-only tokens may *watch*. They may not type.
        #
        # This gate was missing entirely: the socket checked only that the
        # caller was authenticated, and control frames send keystrokes, so a
        # token issued as read-only could open a terminal and run anything.
        # Every other write path in the server is scoped; this one was the
        # hole under it.
        may_write, _reason = self._may_write()

        key = self.headers.get("Sec-WebSocket-Key")
        if not key:
            return self._send(400, b"not a websocket request", "text/plain")

        session = self.panel.store.session(query.get("id", ""))
        if not session:
            return self._send(404, b"no such session", "text/plain")

        if not tmux.exists(session.mux, session.socket):
            return self._send(409, b"session is no longer running", "text/plain")

        cols, rows = _terminal_size(query.get("cols"), query.get("rows"))

        self.wfile.write(handshake_response(key))
        self.wfile.flush()
        ws = WebSocket(self.connection)

        with self.panel._lock:
            self.panel.clients += 1

        bridge: PtyBridge | None = None
        viewer: str | None = None
        try:
            # Size the window before anything is drawn into it.
            #
            # This used to happen *after* the attach, and that is where the
            # overlapping text came from. A session is created at 200x50 and a
            # browser is rarely that; attaching first made tmux paint a whole
            # frame at the old width, which the terminal wrapped at the new
            # one, and the correct redraw then landed on top of the wreckage.
            # An interactive menu was the reliable way to see it — two cursors,
            # a duplicated choice, and fragments of one line inside another.
            #
            # The window is shared with every other client on it and its size
            # is one number, so this also claims it for the browser that just
            # opened rather than inheriting whatever the last tool left —
            # otherwise an adopted session opens as a small pane in a sea of
            # tmux dot-fill.
            #
            # `passive=1` is a background viewer: same size as the window
            # already is, and it must not become the thing that resizes it.
            # A hidden tab warming up in a second window used to steal the
            # pane the first window was looking at.
            #
            # The window's size is `manual`, so attaching a PTY cannot move
            # it — only `resize-window` can. We still skip that call for a
            # passive viewer, and still birth the PTY at the window's
            # current size so this client is not drawing a different grid
            # than the one tmux is painting.
            if query.get("passive") not in {"1", "true", "yes"}:
                tmux.resize_window(session.mux, cols, rows, session.socket)
            else:
                pane = self.panel.live().get(session.mux)
                if pane and pane.width >= 2 and pane.height >= 2:
                    cols, rows = pane.width, pane.height

            # Then scrollback, which is now captured at the width it will be
            # displayed at, and then the attach. History only — tmux redraws
            # the visible frame itself, and sending it twice reads as the CLI
            # having repeated its last screen.
            cli = self.panel.registry.types().get(session.cli)
            # Boxed CLIs turn mouse tracking on. Hidden from the browser so
            # drag-select still works; a click we encode ourselves still
            # reaches the program in tmux.
            filt = termstrip.boxed_stream() if (cli and cli.own_input) else None

            def outbound(data: bytes, _filt=filt) -> None:
                if _filt is not None:
                    data = _filt.feed(data)
                    if not data:
                        return
                ws.send(data)

            try:
                history = tmux.capture(session.mux, session.socket, history_only=True)
                if history.strip():
                    outbound(history.replace("\n", "\r\n").encode())
            except tmux.TmuxError:
                pass  # a session that died between the check and here

            # Attach through a private view rather than the session itself, so
            # this browser detaching does not detach anyone else and two
            # browsers can sit on different windows.
            viewer = tmux.create_viewer(session.mux, session.socket)
            bridge = PtyBridge(
                tmux.attach_argv(viewer, session.socket),
                on_output=outbound,
                on_exit=ws.close,
                cols=cols,
                rows=rows,
            )
            bridge.start()

            stop = threading.Event()
            threading.Thread(target=self._keepalive, args=(ws, stop), daemon=True).start()
            try:
                while True:
                    message = ws.recv()
                    if message is None:
                        break
                    opcode, payload = message
                    if opcode == OP_TEXT:
                        self._control(session, bridge, payload, may_write)
                    elif may_write:
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

    def _control(self, session, bridge: PtyBridge, payload: bytes, may_write: bool = True) -> None:
        """Text frames are control; binary frames are keystrokes.

        Everything here is attacker-shaped: it is JSON from a socket, so a
        missing key or a string where a number belongs must be ignored rather
        than raise. An exception in this call unwinds the read loop and drops
        a working terminal.
        """
        try:
            message = json.loads(payload)
        except ValueError:
            return
        if not isinstance(message, dict):
            return
        kind = message.get("type")
        if kind == "resize":
            cols, rows = _terminal_size(message.get("cols"), message.get("rows"))
            bridge.resize(cols, rows)
            # The PTY resize only tells tmux how big *this client* is. The
            # window is `manual` and shared, so it also has to be told, or
            # the browser gets a small pane padded out with dots.
            # A read-only viewer does not get to resize what others are
            # looking at — its own view, yes; the shared window, no.
            # Tiny sizes are a collapsed or hidden tab measuring itself,
            # not a phone — those still clear 20x8. Applying them is how
            # a background reconnect left a screen of dots.
            if may_write:
                tmux.resize_window(session.mux, cols, rows, session.socket)
        elif not may_write:
            return  # watching is allowed; typing is not
        elif kind == "key":
            tmux.send_key(session.mux, str(message.get("key") or ""), session.socket)
        elif kind == "run":
            tmux.send_text(
                session.mux,
                str(message.get("text", "")),
                session.socket,
                enter=bool(message.get("enter", True)),
            )

    @staticmethod
    def _keepalive(ws: WebSocket, stop: threading.Event) -> None:
        while not stop.wait(PING_SECONDS):
            if ws.closed:
                return
            ws.ping()


def serve(host: str, port: int, panel: Panel) -> None:
    Handler.panel = panel
    # So a session's pane can be told where to report its state back to.
    panel.host, panel.port = host, port
    panel.history.start()
    tmux.bootstrap()
    # A crash leaves viewer sessions behind. They hold no work, so clearing
    # them at startup is free and keeps `tmux ls` honest.
    stale = tmux.sweep_viewers((tmux.SOCKET, *tmux.FOREIGN_SOCKETS))
    if stale:
        print(f"cleared {stale} stale viewer session(s)", flush=True)
    httpd = Server((host, port), Handler)
    print(f"clique {version_string()} on http://{host}:{port}", flush=True)
    httpd.serve_forever()
