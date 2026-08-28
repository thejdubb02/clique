"""One outbound webhook, so the panel can reach you when it is not open.

An attention state you only see while looking at the panel is decoration. This
is the part that taps you on the shoulder.

Deliberately one URL and one shape, not a list of services. ntfy, Gotify,
Discord, Mattermost, Home Assistant and Uptime Kuma's push monitors are all
"POST some JSON to this address", so a single field covers every one of them
with no SDK, no OAuth, and no settings sheet full of logos. Adding the second
integration is the decision that creates a permanent "please add mine" queue,
so there is no first one.

Not Web Push, either. VAPID needs ECDSA P-256 signing that the standard
library does not have, which would cost a dependency — and ntfy's own app
already delivers real push to a phone for free.

Cost is why the watcher only exists when a URL is set. With no webhook
configured nothing here runs at all, and an idle CLIque still costs what it
always did.
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import ipaddress
import json
import queue
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from . import notes

#: A notification is worth one attempt. A retry queue means durable state,
#: which means a database, which ends the argument this product is making —
#: and a dropped "your session is waiting" is superseded by the next poll
#: anyway.
TIMEOUT = 5

#: How often the watcher looks when nobody has the panel open. Slower than the
#: browser's poll on purpose: this exists for the case where you are away, and
#: nobody away from their desk needs to be told within three seconds.
INTERVAL = 10

#: A session younger than this cannot be reported as having finished. Long
#: enough to cover a CLI drawing its opening screen and settling.
YOUNG = 30


#: Never a legitimate webhook target, and the classic way a "POST to a URL"
#: feature becomes a credential leak: every major cloud serves instance
#: credentials from a link-local address to anything that asks.
#:
#: Private and loopback addresses are deliberately *not* blocked. A self-hoster
#: pointing this at ntfy on 127.0.0.1 or Gotify on their LAN is the normal
#: case, and refusing it would break the feature to protect an admin from
#: their own machine. What is blocked is the range with no honest use.
_FORBIDDEN = (
    ipaddress.ip_network("169.254.0.0/16"),  # cloud metadata, incl. 169.254.169.254
    ipaddress.ip_network("fe80::/10"),  # the v6 equivalent
)


def allowed(url: str) -> bool:
    """Whether this URL is one we are willing to POST to.

    Checked here as well as when the setting is saved. Validation at the edge
    is right, but a value can also arrive from a state file written by an older
    version, or by hand, and the place that opens the socket is the last place
    that can still say no.
    """
    try:
        parsed = urllib.parse.urlparse(url)
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return False
    try:
        infos = socket.getaddrinfo(parsed.hostname, None)
    except (socket.gaierror, UnicodeError, ValueError):
        return False  # cannot resolve it, so cannot vouch for it
    for info in infos:
        try:
            address = ipaddress.ip_address(info[4][0])
        except ValueError:
            return False
        # ::ffff:169.254.169.254 is the metadata address wearing a v6 hat, and
        # it is not "in" the v4 network — the comparison returns False and the
        # check waves it through. Unwrap before testing.
        mapped = getattr(address, "ipv4_mapped", None)
        if mapped is not None:
            address = mapped
        if any(address in net for net in _FORBIDDEN):
            return False
    return True


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    """Turns any redirect into an error instead of a second, unvetted request."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(_NoRedirects)


def sign(body: bytes, secret: str) -> str:
    """`sha256=<hex>` over the exact bytes sent, so a receiver can check it."""
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


#: Deliveries run on a small fixed pool behind a bounded queue, not a thread per
#: call: POST /api/webhook/test reaches here directly, so a caller spamming it
#: could otherwise leave thousands of daemon threads each blocked for the request
#: timeout. A full queue drops the delivery — the model already permits that.
_QUEUE: queue.Queue = queue.Queue(maxsize=64)
_WORKERS_LOCK = threading.Lock()
_workers_started = False


def _worker() -> None:
    while True:
        job = _QUEUE.get()
        try:
            job()
        except Exception:  # noqa: BLE001, S110 — a delivery failing is not the panel's problem
            pass
        finally:
            _QUEUE.task_done()


def _enqueue(job) -> None:
    global _workers_started
    with _WORKERS_LOCK:
        if not _workers_started:
            _workers_started = True
            for _ in range(3):
                threading.Thread(target=_worker, daemon=True).start()
    with contextlib.suppress(queue.Full):
        _QUEUE.put_nowait(job)


def post(url: str, secret: str, payload: dict) -> None:
    """Fire and forget, on a thread, swallowing everything.

    Nothing upstream should slow down or fail because someone's ntfy is down.
    """
    if not allowed(url):
        return

    def send() -> None:
        # Redirects are not followed. `allowed()` vetted the address that was
        # typed in; urllib would happily follow a 302 from that host to
        # 169.254.169.254 and the check would have decided nothing. A webhook
        # receiver has no legitimate reason to redirect — it is being handed a
        # notification, not asked for a page.
        body = json.dumps(payload).encode()
        # Checked by allowed() above: scheme is http(s) and the host does not
        # resolve into a forbidden range.
        request = urllib.request.Request(url, data=body, method="POST")  # noqa: S310
        request.add_header("Content-Type", "application/json")
        request.add_header("User-Agent", "clique")
        if secret:
            request.add_header("X-CLIque-Signature", sign(body, secret))
        try:
            # allowed() has vetted the scheme and resolved the host already.
            with _opener().open(request, timeout=TIMEOUT):
                pass
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            pass

    _enqueue(send)


class Watcher:
    """Notices what changed between two looks, and says so once.

    The transitions worth a notification are the ones you would have wanted to
    know about while you were not looking:

      waiting  — it wants an answer
      error    — it stopped badly
      finished — it was working and now it is not
      died     — the process is gone

    Each fires on the *edge*. A session that has been waiting for an hour is
    not news every ten seconds, and a notifier that repeats itself is one you
    mute.
    """

    def __init__(self, panel) -> None:
        self.panel = panel
        self._before: dict[str, tuple[str, bool, bool]] = {}
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        #: Bumped on every start and stop; see ensure().
        self._generation = 0

    # ----------------------------------------------------------------- loop

    def ensure(self) -> None:
        """Start the loop if a webhook is configured, stop it if not.

        Clearing a URL sets the stop Event; setting one again clears it. A
        thread that was parked in `wait()` across both never sees the stop and
        carries on, so pasting a URL, removing it and pasting it back left two
        watchers running and every notification arriving twice. The generation
        counter is what makes the old one retire.
        """
        wanted = bool(self.panel.store.settings.get("webhook_url"))
        running = self._thread is not None and self._thread.is_alive()
        if wanted and not running:
            self._generation += 1
            mine = self._generation
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, args=(mine,), daemon=True)
            self._thread.start()
        elif not wanted and running:
            self._generation += 1
            self._stop.set()
            self._thread = None

    def _loop(self, mine: int) -> None:
        while not self._stop.wait(INTERVAL):
            if not self.panel.store.settings.get("webhook_url"):
                return
            if mine != self._generation:
                return
            try:
                self.tick()
            except (OSError, ValueError, KeyError, RuntimeError):
                # Narrow on purpose. A watcher that dies on one bad poll is
                # worse than one that misses a notification, but swallowing
                # *everything* also swallows the bug that would have told us
                # why — so this catches what a poll can plausibly raise and
                # lets anything else surface.
                continue
            try:
                self.reminders()
            except (OSError, ValueError, KeyError, RuntimeError):
                continue

    # ---------------------------------------------------------------- edges

    def tick(self) -> None:
        settings = self.panel.store.settings
        url = str(settings.get("webhook_url") or "")
        if not url:
            return
        secret = str(settings.get("webhook_secret") or "")
        panel_url = str(settings.get("panel_url") or "").rstrip("/")

        now: dict[str, tuple[str, bool, bool]] = {}
        for row in self.panel.sessions_view():
            state = (row.get("signal") or "", bool(row.get("busy")), bool(row.get("alive")))
            now[row["id"]] = state
            was = self._before.get(row["id"])
            if was is None:
                continue  # first look is a baseline, not news
            for event in self._events(was, state):
                # A session that has just started produces its opening screen,
                # then settles — which is a busy->quiet edge, and "it finished"
                # about something twelve seconds old is never what you wanted
                # to be told. It is the only event that can fire from a session
                # simply having drawn itself.
                if event == "finished" and time.time() - (row.get("created") or 0) < YOUNG:
                    continue
                post(
                    url,
                    secret,
                    {
                        "event": event,
                        "at": int(time.time()),
                        "session": {
                            "id": row["id"],
                            "name": row.get("name", ""),
                            "cli": row.get("cli", ""),
                            "cli_label": row.get("cli_label", ""),
                            "folder": row.get("folder"),
                            "cwd": row.get("cwd", ""),
                        },
                        "text": self._text(event, row),
                        "url": f"{panel_url}/?session={row['id']}" if panel_url else "",
                    },
                )

        # A session that disappeared entirely: deleted, not died. No event, and
        # its history goes with it so a re-created id starts clean.
        self._before = now

    # ------------------------------------------------------------- reminders

    def reminders(self) -> None:
        """Fire the note reminders that have come due, once each.

        Runs on the same slow loop as the edge watcher and, like it, only when a
        webhook is configured — a reminder that can only pop in a panel you are
        not looking at is the case this is for. Each fired item is stamped
        ``reminded`` and the file rewritten, so a nudge is delivered once even
        across restarts. A reminder outlives its session on purpose: the note is
        yours, and the thing it was about does not stop mattering because the CLI
        exited.
        """
        settings = self.panel.store.settings
        url = str(settings.get("webhook_url") or "")
        if not url:
            return
        secret = str(settings.get("webhook_secret") or "")
        panel_url = str(settings.get("panel_url") or "").rstrip("/")
        home = self.panel.store.path.parent
        ndir = notes.dir_for(home)
        if not ndir.is_dir():
            return
        stamp = time.time()
        for path in sorted(ndir.glob("*.json")):
            tree = notes.load(path)
            fired = notes.due(tree, stamp)
            if not fired:
                continue
            session_id = path.stem
            session = self.panel.store.session(session_id)
            name = session.name if session else "A note"
            for item in fired:
                post(
                    url,
                    secret,
                    {
                        "event": "reminder",
                        "at": int(stamp),
                        "session": {
                            "id": session_id,
                            "name": name,
                            "cli": getattr(session, "cli", "") if session else "",
                            "cli_label": "",
                            "folder": None,
                            "cwd": getattr(session, "cwd", "") if session else "",
                        },
                        "text": _reminder_text(item.get("text", "")),
                        "url": f"{panel_url}/?session={session_id}" if panel_url else "",
                    },
                )
                item["reminded"] = True
            notes.save(path, tree)

    @staticmethod
    def _events(was: tuple, now: tuple) -> list[str]:
        old_signal, old_busy, old_alive = was
        new_signal, new_busy, new_alive = now
        out = []
        if old_alive and not new_alive:
            return ["died"]  # nothing else about a dead session is news
        if new_signal and new_signal != old_signal:
            out.append(new_signal)
        elif old_busy and not new_busy and not new_signal:
            # Only when nothing louder is being said: "it stopped" is the
            # weakest of these, and saying it alongside "it is waiting" would
            # be two notifications for one moment.
            out.append("finished")
        return out

    @staticmethod
    def _text(event: str, row: dict) -> str:
        name = row.get("name") or row.get("cli_label") or "A session"
        return {
            "waiting": f"{name} is waiting for you",
            "error": f"{name} stopped on an error",
            "finished": f"{name} finished",
            "died": f"{name} is gone",
        }.get(event, f"{name}: {event}")


def _reminder_text(text: str) -> str:
    """The webhook body for a due reminder: its first line, kept short."""
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    first = lines[0][:120] if lines else ""
    return f"Reminder: {first}" if first else "You have a reminder due"
