"""How much of a plan a CLI has spent, read from a probe it declares itself.

Nothing here knows what Anthropic is, or Google, or xAI. A CLI's block in
`clis.toml` says where its token lives, which URL reports usage, and which
fields in the reply are the numbers. This runs that description. Teaching the
panel about a second vendor is a block of TOML, which is the same bargain
`clis.toml` already makes for launching a CLI at all.

That matters beyond tidiness. The rule is that the core deals in filesystem,
tmux and process state and does not learn a vendor's protocol; a declarative
probe keeps every vendor-shaped fact in config where a user can read, change or
delete it.

Two things are deliberate and worth keeping:

**The token never leaves this process.** It is read, spent on one request, and
dropped. What reaches the browser is a percentage and a reset time. A panel on
a phone should not be able to walk away with the credentials of the machine it
is driving.

**Failure is silence.** No token, an expired one, no network, a reply in a
shape we did not expect: the bar shows nothing rather than an error. This is a
convenience on a status bar, and a panel that shouts because it could not reach
an API it does not need is worse than one that says nothing.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

#: How long a reading stays good. The windows it reports move over hours, and
#: every browser watching shares this, so the interval is about being a polite
#: client rather than about freshness.
TTL = 300

#: A probe that has not answered by now is not going to. Held short because a
#: status bar waits on it.
TIMEOUT = 8

#: Refuse a reply larger than this. A status number is a few hundred bytes; a
#: megabyte means something has gone wrong at the far end.
MAX_BYTES = 64 * 1024

_lock = threading.Lock()
_cache: dict[str, tuple[float, dict]] = {}


def _dig(data: object, path: str) -> object:
    """`a.b.c` out of nested dicts, or None the moment the path stops matching.

    Deliberately forgiving: a probe describes somebody else's API, and that API
    changing shape should cost a missing number rather than a traceback in a
    poll every browser is waiting on.
    """
    for step in str(path).split("."):
        if not isinstance(data, dict):
            return None
        data = data.get(step)
    return data


def _token(spec: dict) -> str | None:
    """The credential a probe needs, out of a file the CLI already wrote.

    Read fresh every time rather than held: these rotate, and a cached one
    turns into a silent 401 an hour later that nobody can explain.
    """
    where = spec.get("token_file")
    if not where:
        return None
    try:
        raw = json.loads(Path(where).expanduser().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    found = _dig(raw, spec.get("token_field") or "")
    return found if isinstance(found, str) and found else None


def _fetch(spec: dict, guard) -> dict | None:
    url = str(spec.get("url") or "")
    if not url:
        return None
    try:
        guard(url)  # the same check outbound model calls get
    except Exception:  # noqa: BLE001 — a refused URL is "no usage", not a crash
        return None
    token = _token(spec)
    if not token:
        return None
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    for key, value in (spec.get("headers") or {}).items():
        headers[str(key)] = str(value)
    # Suppressed on both lines below: `guard` has already refused anything that
    # is not http(s) to a public host, which is the check S310 is asking for.
    request = urllib.request.Request(url, headers=headers)  # noqa: S310
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:  # noqa: S310
            if response.status != 200:
                return None
            body = response.read(MAX_BYTES + 1)
    except (urllib.error.URLError, OSError, ValueError):
        return None
    if len(body) > MAX_BYTES:
        return None
    try:
        parsed = json.loads(body)
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _windows(spec: dict, payload: dict) -> list[dict]:
    """The declared windows, keeping only the ones that produced a number.

    A percentage is clamped rather than trusted. It is going into a bar as a
    width, and an API that answers 140 should not paint past the end of it.
    """
    out = []
    for window in spec.get("window") or []:
        if not isinstance(window, dict):
            continue
        raw = _dig(payload, window.get("percent") or "")
        if not isinstance(raw, (int, float)):
            continue
        resets = _dig(payload, window.get("resets") or "")
        out.append(
            {
                "label": str(window.get("label") or "")[:8],
                "percent": max(0.0, min(100.0, float(raw))),
                "resets_at": resets if isinstance(resets, str) else None,
            }
        )
    return out


def read(cli_id: str, spec: dict, guard, *, force: bool = False) -> dict | None:
    """One CLI's usage, cached. None when there is nothing honest to show.

    Every browser attached to this panel shares the cache, so twenty open tabs
    are still one request every few minutes.
    """
    if not spec or not spec.get("url"):
        return None
    now = time.time()
    with _lock:
        hit = _cache.get(cli_id)
        if hit and not force and now - hit[0] < TTL:
            return hit[1] or None

    payload = _fetch(spec, guard)
    windows = _windows(spec, payload) if payload else []
    result = {"cli": cli_id, "windows": windows, "checked": int(now)} if windows else {}
    with _lock:
        # A failure is cached too, for the same TTL. Otherwise a box with no
        # credentials retries on every poll forever.
        _cache[cli_id] = (now, result)
    return result or None


def forget(cli_id: str | None = None) -> None:
    """Drop what is cached, so the next read goes out for real."""
    with _lock:
        if cli_id is None:
            _cache.clear()
        else:
            _cache.pop(cli_id, None)
