"""Collect what people are saying about CLIque into one file worth reading.

Requests arrive as issues, as discussions, and — most easily missed — as
replies buried in a thread opened weeks ago. This pulls all three into
`docs/feedback-inbox.md`, marks what is new since the last run, and prints one
line saying so.

Official channels only, on purpose. What people say about CLIque elsewhere is
interesting, and it is not a queue. Mixing the two would mean triaging opinions
alongside requests, and the requests are what lose.

It is deliberately not a bug tracker. GitHub is the tracker; this is the
triage board in front of it, which is the thing that decides what gets built.

Authentication is `gh`'s, not ours — this shells out rather than holding a
token, so there is no credential here to leak or rotate.

Usage: python3 tools/feedback.py [--quiet]
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

REPO = "thejdubb02/clique"
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "feedback-inbox.md"
#: Outside the repo on purpose: what has been seen is this machine's memory,
#: not a fact about the project, and it should not show up in a diff.
SEEN = Path.home() / ".clique" / "feedback-seen.json"
ZONE = ZoneInfo("America/Los_Angeles")
#: Long enough to be specific to this project, short enough that someone who
#: only wrote the name still gets caught.
MENTIONS = (REPO, "thejdubb02/clique")


def gh(*args: str) -> object:
    """`gh api ...`, or None if gh is missing, logged out, or unhappy."""
    try:
        out = subprocess.run(["gh", *args], capture_output=True, text=True,
                             timeout=30, check=True).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    try:
        return json.loads(out or "null")
    except json.JSONDecodeError:
        return None


def issues() -> list[dict]:
    rows = gh("api", f"repos/{REPO}/issues?state=open&per_page=50") or []
    out = []
    for row in rows:
        if "pull_request" in row:
            continue          # a PR is work arriving, not work being asked for
        out.append({
            "key": f"issue:{row['number']}",
            "kind": "issue",
            "title": row.get("title", ""),
            "who": (row.get("user") or {}).get("login", "?"),
            "when": row.get("created_at", ""),
            "url": row.get("html_url", ""),
            "extra": ", ".join(label["name"] for label in row.get("labels", [])),
            "comments": row.get("comments", 0),
        })
    return out


def issue_comments() -> list[dict]:
    """Every recent comment on every issue, in one call.

    Here because the request in an issue is often not the one in its title.
    Someone opens "the tabs are confusing", three people argue underneath it,
    and the thing worth building is in the fourth reply. Watching only the
    openings misses all of that.
    """
    rows = gh("api", f"repos/{REPO}/issues/comments"
                     "?per_page=50&sort=created&direction=desc") or []
    out = []
    for row in rows:
        body = " ".join((row.get("body") or "").split())
        out.append({
            "key": f"comment:{row.get('id')}",
            "kind": "comment",
            "title": body[:140] or "(empty)",
            "who": (row.get("user") or {}).get("login", "?"),
            "when": row.get("created_at", ""),
            "url": row.get("html_url", ""),
            "extra": "on #" + str(row.get("issue_url", "")).rsplit("/", 1)[-1],
            "comments": 0,
        })
    return out


def discussions() -> list[dict]:
    """Open discussions. Only reachable over GraphQL, hence the query."""
    query = """
    query($owner:String!,$name:String!){
      repository(owner:$owner,name:$name){
        discussions(first:50, orderBy:{field:CREATED_AT, direction:DESC}){
          nodes{ number title url createdAt category{name} author{login}
                 comments(last:20){ totalCount
                   nodes{ url createdAt bodyText author{login} } } }
        }
      }
    }"""
    owner, name = REPO.split("/")
    data = gh("api", "graphql", "-f", f"query={query}",
              "-F", f"owner={owner}", "-F", f"name={name}")
    nodes = (((data or {}).get("data") or {}).get("repository") or {})
    nodes = (nodes.get("discussions") or {}).get("nodes") or []
    out = [{
        "key": f"discussion:{n['number']}",
        "kind": "discussion",
        "title": n.get("title", ""),
        "who": (n.get("author") or {}).get("login", "?"),
        "when": n.get("createdAt", ""),
        "url": n.get("url", ""),
        "extra": (n.get("category") or {}).get("name", ""),
        "comments": (n.get("comments") or {}).get("totalCount", 0),
    } for n in nodes]
    # Replies come back on the same round trip, so watching them costs nothing.
    for n in nodes:
        for c in (n.get("comments") or {}).get("nodes") or []:
            body = " ".join((c.get("bodyText") or "").split())
            out.append({
                "key": f"dcomment:{c.get('url', '')}",
                "kind": "comment",
                "title": body[:140] or "(empty)",
                "who": (c.get("author") or {}).get("login", "?"),
                "when": c.get("createdAt", ""),
                "url": c.get("url", ""),
                "extra": f"on discussion #{n['number']}",
                "comments": 0,
            })
    return out


def when(row: dict) -> str:
    """An ISO timestamp as a local date, because that is how anyone reads it."""
    try:
        return datetime.fromisoformat(
            row["when"].replace("Z", "+00:00")).astimezone(ZONE).strftime("%Y-%m-%d")
    except (ValueError, KeyError, AttributeError):
        return "—"


def table(rows: list[dict]) -> str:
    if not rows:
        return "_Nothing._\n"
    out = ["| | What | Who | When | |", "|---|---|---|---|---|"]
    for row in sorted(rows, key=lambda r: r.get("when") or "", reverse=True):
        title = row["title"].replace("|", "\\|").strip()[:110] or "(untitled)"
        note = row["extra"] or ""
        if row["comments"]:
            note = (note + " " if note else "") + f"({row['comments']} replies)"
        out.append(f"| {row['kind']} | [{title}]({row['url']}) | {row['who']} "
                   f"| {when(row)} | {note} |")
    return "\n".join(out) + "\n"


def main() -> int:
    quiet = "--quiet" in sys.argv
    rows = issues() + issue_comments() + discussions()

    try:
        seen = set(json.loads(SEEN.read_text()))
    except (OSError, ValueError):
        seen = set()
    fresh = [r for r in rows if r["key"] not in seen]

    stamp = datetime.now(ZONE)
    body = f"""# Feedback inbox

Generated by `tools/feedback.py` — **do not hand-edit**, it is overwritten.
Decisions live in [ideas-inbox.md](ideas-inbox.md) and
[../ROADMAP.md](../ROADMAP.md); this is only what arrived.

Last collected {stamp:%Y-%m-%d %H:%M %Z}.

## New since the last run

{table(fresh)}
## Open on GitHub

{table([r for r in rows if r['kind'] in ('issue', 'discussion')])}
## Replies

Someone answering their own thread three days later is where the actual
requirement usually turns up.

{table([r for r in rows if r['kind'] == 'comment'])}"""

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(body)
    SEEN.parent.mkdir(parents=True, exist_ok=True)
    SEEN.write_text(json.dumps(sorted(r["key"] for r in rows)))

    if not quiet or fresh:
        print(f"{len(rows)} tracked, {len(fresh)} new -> {OUT}")
        for row in fresh:
            print(f"  {row['kind']:10} {row['title'][:70]}  {row['url']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
