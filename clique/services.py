"""Whether the service behind a CLI is having a bad day.

An agent that has gone quiet is indistinguishable from an agent whose provider
is down, and the difference decides whether you debug your prompt for twenty
minutes or go and make coffee. The provider already publishes the answer; the
panel is simply the place you were already looking.

Three rules keep this from becoming a vendor integration:

**One shape.** Anthropic, OpenAI, GitHub and Cursor all run Atlassian
Statuspage, whose `/api/v2/status.json` is a fixed two-field document. That is
the only format understood. A second parser here would be the first step
towards a directory of per-vendor scrapers, and the moment a CLI needs Python
to be added, the design in `CLAUDE.md` has failed.

**Config, not code.** The feed is a `status` block in `clis.toml` beside the
launch command. Adding one is two lines of TOML by anybody, including for a
CLI we have never heard of.

**Only what is running.** A feed is fetched for a CLI that has a live session
right now — not for the sixteen in the catalogue, and not at all when nothing
is open. An idle panel makes no requests, which is the same promise the
webhook watcher makes.

This reads a public status page over HTTPS. It sends no identifier, no session
name and no query string, and it is one setting away from never running. It is
still an outbound connection from a self-hosted box, so it is stated plainly
in the README rather than left to be discovered in a firewall log.
"""

from __future__ import annotations

import contextlib
import json
import threading
import time
import urllib.error
import urllib.request

from . import notify

#: Statuspage's own vocabulary. Ordered by how much it should interrupt you;
#: "none" is the overwhelmingly common case and draws nothing at all.
LEVELS = ("none", "maintenance", "minor", "major", "critical")

#: How often a feed is re-read. An outage lasts tens of minutes and is
#: announced in minutes, so anything faster is politeness spent on nobody's
#: behalf — and these are somebody else's servers.
INTERVAL = 300

#: Short. A status page that is itself struggling must not hold a thread.
TIMEOUT = 6

#: A reading this old is dropped rather than shown. Better to say nothing than
#: to leave yesterday's outage on the screen because the box lost DNS.
STALE = 3600


def read(url: str) -> tuple[str, str] | None:
    """(indicator, description) from a Statuspage endpoint, or None.

    None means "no answer", which is deliberately different from "operational".
    A feed that cannot be reached must not be reported as fine.
    """
    if not notify.allowed(url):
        return None
    request = urllib.request.Request(url, method="GET")  # noqa: S310 — vetted by notify.allowed
    request.add_header("User-Agent", "clique")
    request.add_header("Accept", "application/json")
    try:
        # Redirects are refused for the same reason the webhook refuses them:
        # the address that was vetted is the only address that was vetted.
        # status.anthropic.com redirects to status.claude.com, which is why
        # clis.toml carries the destination rather than the older name.
        with notify._opener().open(request, timeout=TIMEOUT) as response:
            if response.status != 200:
                return None
            payload = json.loads(response.read(64 * 1024))
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return None
    status = payload.get("status")
    if not isinstance(status, dict):
        return None
    indicator = str(status.get("indicator") or "")
    if indicator not in LEVELS:
        return None
    return indicator, str(status.get("description") or "")


class Services:
    """Polls the feeds of CLIs that are actually running, and caches the answer.

    Nothing here is ever on the path of a request. `/api/state` reads whatever
    the last poll left behind, so a status page that hangs slows down a
    background thread and nothing else.
    """

    def __init__(self, panel) -> None:
        self.panel = panel
        self._lock = threading.Lock()
        self._seen: dict[str, dict] = {}     # cli id -> last reading
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        #: Bumped on every start and stop; see ensure().
        self._generation = 0

    # ----------------------------------------------------------------- loop

    def on(self) -> bool:
        return self.panel.store.settings.get("service_status") is not False

    def ensure(self) -> None:
        """Start the loop if the setting is on, stop it if not.

        The generation counter is what makes off-then-on safe. Stopping sets
        an Event; starting has to clear it — and a thread that was still
        sitting in `wait()` when it was cleared would carry on as if it had
        never been told to stop, leaving two loops polling the same feeds.
        Toggling a checkbox twice is enough to do it. Each loop instead
        remembers which generation it belongs to and retires the moment it is
        not the current one.
        """
        running = self._thread is not None and self._thread.is_alive()
        if self.on() and not running:
            self._generation += 1
            mine = self._generation
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, args=(mine,),
                                            daemon=True, name="clique-services")
            self._thread.start()
        elif not self.on() and running:
            self._generation += 1
            self._stop.set()
            self._thread = None
            with self._lock:
                self._seen.clear()

    def _loop(self, mine: int) -> None:
        # Once at startup, then on the interval: waiting five minutes to find
        # out that the thing you just opened is down is five minutes of
        # debugging your own prompt.
        while True:
            if not self.on() or mine != self._generation:
                return
            # Narrow, matching the webhook watcher: a poll that fails is not
            # a reason to lose the thread, but a bug should still be allowed
            # to surface rather than being swallowed forever.
            with contextlib.suppress(OSError, ValueError, KeyError, RuntimeError):
                self.tick()
            if self._stop.wait(INTERVAL) or mine != self._generation:
                return

    # ---------------------------------------------------------------- feeds

    def wanted(self) -> dict[str, object]:
        """CLI types with a feed and at least one live session right now."""
        try:
            types = self.panel.registry.types()
        except Exception:  # noqa: BLE001 — a broken clis.toml is reported elsewhere
            return {}
        live = {s.cli for s in self.panel.store.sessions if s.cli}
        return {k: v for k, v in types.items() if v.status and k in live}

    def tick(self) -> None:
        wanted = self.wanted()
        for cli_id, cli in wanted.items():
            answer = read(str(cli.status.get("url") or ""))
            if answer is None:
                continue          # keep the previous reading until it goes stale
            indicator, description = answer
            with self._lock:
                self._seen[cli_id] = {
                    "cli": cli_id,
                    "label": cli.label,
                    "indicator": indicator,
                    "description": description,
                    "url": str(cli.status.get("page") or ""),
                    "checked": int(time.time()),
                }
        # A CLI with no session left is a CLI we stop speaking for.
        with self._lock:
            for cli_id in [k for k in self._seen if k not in wanted]:
                del self._seen[cli_id]

    def snapshot(self) -> list[dict]:
        """Only what is worth interrupting for, worst first.

        An empty list is the answer almost every time, and it is the answer the
        UI draws nothing for. "All systems operational" is not news, and a
        banner that is always there is a banner nobody reads on the day it
        finally says something.
        """
        now = int(time.time())
        with self._lock:
            rows = [dict(r) for r in self._seen.values()]
        fresh = [r for r in rows
                 if r["indicator"] != "none" and now - r["checked"] < STALE]
        fresh.sort(key=lambda r: (-LEVELS.index(r["indicator"]), r["label"]))
        return fresh
