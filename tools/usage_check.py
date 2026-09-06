#!/usr/bin/env python3
"""Verify the plan-usage probe: it reads a description, and only a description.

The point of `clique/usage.py` is that the panel learns nothing about any
vendor. A block of TOML says where the token is, which URL answers, and which
fields hold the numbers; this runs that and nothing else. So the checks here
are mostly about a probe pointed at a local server that is not anybody's real
API, which is the strongest evidence the code is genuinely generic.

The rest is about failing quietly. A status bar that shouts because it could
not reach an API it does not need is worse than one that says nothing.

    python3 tools/usage_check.py

Its own throwaway HTTP server. 0 on pass, 1 on fail.
"""

from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from clique import usage
from clique.registry import Registry

passed = failed = 0
seen: list[str] = []


def check(label: str, ok: bool, detail: object = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  ok   {label}")
    else:
        failed += 1
        print(f"  FAIL {label} {detail}")


REPLY = {
    "alpha": {"used": 12.5, "until": "2026-09-01T00:00:00+00:00"},
    "beta": {"used": 140, "until": None},
    "gamma": {"used": "not a number"},
}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        seen.append(self.headers.get("Authorization", ""))
        if self.path == "/huge":
            body = json.dumps({"pad": "x" * (usage.MAX_BYTES + 1000)}).encode()
        elif self.path == "/notjson":
            body = b"<html>nope</html>"
        elif self.path == "/teapot":
            self.send_response(418)
            self.end_headers()
            return
        else:
            body = json.dumps(REPLY).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:
        pass


def allow_all(url: str) -> None:
    """Stand-in for the SSRF guard. The real one refuses loopback for models;
    a probe on 127.0.0.1 is exactly what this check needs."""
    if not url.startswith("http"):
        raise ValueError("not http")


def main() -> int:
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"

    home = Path("/tmp/clique-usage-check")
    home.mkdir(parents=True, exist_ok=True)
    creds = home / "creds.json"
    creds.write_text(json.dumps({"outer": {"inner": {"tok": "SECRET-TOKEN"}}}))

    spec = {
        "url": base + "/usage",
        "token_file": str(creds),
        "token_field": "outer.inner.tok",
        "headers": {"X-Probe": "yes"},
        "window": [
            {"label": "A", "percent": "alpha.used", "resets": "alpha.until"},
            {"label": "B", "percent": "beta.used", "resets": "beta.until"},
            {"label": "C", "percent": "gamma.used"},
            {"label": "D", "percent": "delta.nothing"},
        ],
    }

    print("a probe is a description, and nothing here knows whose it is")
    got = usage.read("fake", spec, allow_all, force=True)
    check("it reads", bool(got), got)
    labels = [w["label"] for w in (got or {}).get("windows", [])]
    check("every window that produced a number is kept", labels == ["A", "B"], labels)
    check("a field holding something that is not a number is dropped", "C" not in labels)
    check("and a path that matches nothing is dropped", "D" not in labels)
    first = (got or {}).get("windows", [{}])[0]
    check("the number comes through", first.get("percent") == 12.5, first)
    check("so does the reset time", first.get("resets_at", "").startswith("2026-09-01"), first)
    over = (got or {}).get("windows", [{}, {}])[1]
    # A width on a bar, so an API answering 140 must not paint past the end.
    check("a percentage over a hundred is clamped", over.get("percent") == 100.0, over)
    check("a missing reset time is null, not a guess", over.get("resets_at") is None, over)

    print("the token is spent, not spread around")
    check("it went out as a bearer token", seen and seen[-1] == "Bearer SECRET-TOKEN", seen[-1:])
    check("and never rides the result", "SECRET-TOKEN" not in json.dumps(got), got)

    print("everything that can go wrong goes quiet")
    usage.forget()
    check(
        "no token file, no reading",
        usage.read("f", {**spec, "token_file": str(home / "nope.json")}, allow_all, force=True)
        is None,
    )
    usage.forget()
    check(
        "a token field that is not there, no reading",
        usage.read("f", {**spec, "token_field": "outer.missing"}, allow_all, force=True) is None,
    )
    usage.forget()
    check(
        "a refused URL, no reading",
        usage.read("f", {**spec, "url": "file:///etc/passwd"}, allow_all, force=True) is None,
    )
    usage.forget()
    check(
        "a reply that is not JSON, no reading",
        usage.read("f", {**spec, "url": base + "/notjson"}, allow_all, force=True) is None,
    )
    usage.forget()
    check(
        "a reply that is too big, no reading",
        usage.read("f", {**spec, "url": base + "/huge"}, allow_all, force=True) is None,
    )
    usage.forget()
    check(
        "a bad status, no reading",
        usage.read("f", {**spec, "url": base + "/teapot"}, allow_all, force=True) is None,
    )
    check("no probe declared, no reading", usage.read("f", {}, allow_all, force=True) is None)

    print("the cache is shared, so extra browsers are free")
    usage.forget()
    before = len(seen)
    for _ in range(5):
        usage.read("fake", spec, allow_all)
    check("five reads are one request", len(seen) - before == 1, len(seen) - before)
    usage.forget("fake")
    usage.read("fake", spec, allow_all)
    check("and forgetting one goes out again", len(seen) - before == 2, len(seen) - before)
    usage.forget()
    fails = len(seen)
    for _ in range(3):
        usage.read("f", {**spec, "url": base + "/teapot"}, allow_all)
    check(
        "a failure is cached too, so a box with no key stops asking",
        len(seen) - fails == 1,
        len(seen) - fails,
    )

    print("the shipped catalogue still says what it said")
    claude = Registry(ROOT / "clique" / "config" / "clis.toml").types()["claude"]
    # A sub-table opened in the wrong place silently swallows every bare key
    # after it. That happened while this feature was being written, and these
    # are the keys it ate.
    check("claude still owns its input", claude.own_input is True)
    check("claude still speaks hooks", claude.hooks is True)
    check("claude still has a status page", "url" in claude.status, claude.status)
    check("claude still has its attention patterns", "waiting" in claude.attention)
    check("and it declares a usage probe", "url" in claude.usage, list(claude.usage))
    check("with both windows", [w["label"] for w in claude.usage.get("window", [])] == ["5H", "7D"])

    server.shutdown()
    print(f"\n{passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
