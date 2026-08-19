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

import hashlib
import hmac
import json
import threading
import time
import urllib.error
import urllib.request

#: A notification is worth one attempt. A retry queue means durable state,
#: which means a database, which ends the argument this product is making —
#: and a dropped "your session is waiting" is superseded by the next poll
#: anyway.
TIMEOUT = 5

#: How often the watcher looks when nobody has the panel open. Slower than the
#: browser's poll on purpose: this exists for the case where you are away, and
#: nobody away from their desk needs to be told within three seconds.
INTERVAL = 10


def sign(body: bytes, secret: str) -> str:
    """`sha256=<hex>` over the exact bytes sent, so a receiver can check it."""
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def post(url: str, secret: str, payload: dict) -> None:
    """Fire and forget, on a thread, swallowing everything.

    Nothing upstream should slow down or fail because someone's ntfy is down.
    """
    def send() -> None:
        body = json.dumps(payload).encode()
        request = urllib.request.Request(url, data=body, method="POST")
        request.add_header("Content-Type", "application/json")
        request.add_header("User-Agent", "clique")
        if secret:
            request.add_header("X-CLIque-Signature", sign(body, secret))
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT):
                pass
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            pass

    threading.Thread(target=send, daemon=True).start()


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

    # ----------------------------------------------------------------- loop

    def ensure(self) -> None:
        """Start the loop if a webhook is configured, stop it if not."""
        wanted = bool(self.panel.store.settings.get("webhook_url"))
        running = self._thread is not None and self._thread.is_alive()
        if wanted and not running:
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()
        elif not wanted and running:
            self._stop.set()
            self._thread = None

    def _loop(self) -> None:
        while not self._stop.wait(INTERVAL):
            if not self.panel.store.settings.get("webhook_url"):
                return
            try:
                self.tick()
            except Exception:  # noqa: BLE001
                # A watcher that can kill itself on one bad poll is worse than
                # one that misses a notification.
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
                continue          # first look is a baseline, not news
            for event in self._events(was, state):
                post(url, secret, {
                    "event": event,
                    "at": int(time.time()),
                    "session": {
                        "id": row["id"], "name": row.get("name", ""),
                        "cli": row.get("cli", ""), "cli_label": row.get("cli_label", ""),
                        "folder": row.get("folder"), "cwd": row.get("cwd", ""),
                    },
                    "text": self._text(event, row),
                    "url": f"{panel_url}/?session={row['id']}" if panel_url else "",
                })

        # A session that disappeared entirely: deleted, not died. No event, and
        # its history goes with it so a re-created id starts clean.
        self._before = now

    @staticmethod
    def _events(was: tuple, now: tuple) -> list[str]:
        old_signal, old_busy, old_alive = was
        new_signal, new_busy, new_alive = now
        out = []
        if old_alive and not new_alive:
            return ["died"]       # nothing else about a dead session is news
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
