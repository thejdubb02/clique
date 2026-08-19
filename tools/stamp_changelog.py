#!/usr/bin/env python3
"""Put a wall-clock time on every changelog heading.

A date alone is useless on a day like 2026-08-19, when ten releases shipped
between breakfast and lunch: twenty-nine entries all claiming the same day
tell you nothing about their order or their spacing. The time does.

The truth about when a release happened is its commit time, so that is where
this reads from, converted to the zone the work actually happened in rather
than left in UTC — a release stamped 01:52Z shipped the *previous* evening in
California, and a changelog that says otherwise is lying about the day.

Idempotent: a heading that already carries a time is left exactly as it is.
Run it after a release commit; unstamped headings get filled in.
"""

from __future__ import annotations

import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
CHANGELOG = ROOT / "CHANGELOG.md"

#: Where the work happens. Not UTC: a changelog is read by a person who
#: remembers what they were doing at four o'clock, not at 23:00Z.
ZONE = ZoneInfo("America/Los_Angeles")

# "## 0.5.0 — 2026-08-19 — **now CLIque**" — version, date, then anything else
# the heading wanted to say, which is preserved untouched.
HEAD = re.compile(r"^## (?P<ver>\S+) — (?P<date>\d{4}-\d{2}-\d{2})(?P<rest>.*)$")
STAMPED = re.compile(r"^ \d{2}:\d{2} ")


def release_times() -> dict[str, datetime]:
    """Version -> when it was committed. Versions live in the subject line."""
    out = subprocess.run(
        ["git", "log", "--format=%cI\x1f%s"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout
    found: dict[str, datetime] = {}
    for line in out.splitlines():
        iso, _, subject = line.partition("\x1f")
        # Releases name their version in parentheses at the end; the very
        # first one predates that habit and just says "v0.1.0".
        match = (re.search(r"\((\d+\.\d+\.\d+)\)\s*$", subject)
                 or re.search(r"\bv(\d+\.\d+\.\d+)\b", subject))
        if not match:
            continue
        # git log is newest-first, so the first sighting of a version is the
        # commit that shipped it and any later one is a rewrite of history.
        found.setdefault(match.group(1), datetime.fromisoformat(iso).astimezone(ZONE))
    return found


def main() -> int:
    text = CHANGELOG.read_text(encoding="utf-8")
    times = release_times()
    now = datetime.now(ZONE)
    lines = text.splitlines()
    changed = 0

    for i, line in enumerate(lines):
        match = HEAD.match(line)
        if not match or STAMPED.match(match.group("rest")):
            continue
        when = times.get(match.group("ver"))
        if when is None:
            # Not committed yet — this is the entry being written right now,
            # and "now" is within a minute of the commit that follows.
            when = now
        lines[i] = (f"## {match.group('ver')} — {when:%Y-%m-%d %H:%M %Z}"
                    f"{match.group('rest')}")
        changed += 1

    if changed:
        CHANGELOG.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"stamped {changed} heading(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
