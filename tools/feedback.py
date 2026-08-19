"""Collect what people are saying about CLIque into one file worth reading.

Feature requests arrive in three places and none of them is a place anyone
checks daily: GitHub issues, GitHub discussions, and whatever gets said on Hacker
News. This pulls all three into `docs/feedback-inbox.md`, marks what
is new since the last run, and prints one line saying so.

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
import urllib.error
import urllib.parse
import urllib.request
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


def discussions() -> list[dict]:
    """Open discussions. Only reachable over GraphQL, hence the query."""
    query = """
    query($owner:String!,$name:String!){
      repository(owner:$owner,name:$name){
        discussions(first:50, orderBy:{field:CREATED_AT, direction:DESC}){
          nodes{ number title url createdAt comments{totalCount}
                 author{login} category{name} }
        }
      }
    }"""
    owner, name = REPO.split("/")
    data = gh("api", "graphql", "-f", f"query={query}",
              "-F", f"owner={owner}", "-F", f"name={name}")
    nodes = (((data or {}).get("data") or {}).get("repository") or {})
    nodes = (nodes.get("discussions") or {}).get("nodes") or []
    return [{
        "key": f"discussion:{n['number']}",
        "kind": "discussion",
        "title": n.get("title", ""),
        "who": (n.get("author") or {}).get("login", "?"),
        "when": n.get("createdAt", ""),
        "url": n.get("url", ""),
        "extra": (n.get("category") or {}).get("name", ""),
        "comments": (n.get("comments") or {}).get("totalCount", 0),
    } for n in nodes]


#: Sources that did not answer this run. Collected rather than swallowed: an
#: empty section should mean "nobody said anything", and if it can also mean
#: "the source refused us" then the whole file stops being trustworthy.
PROBLEMS: list[str] = []


def fetch(url: str, source: str) -> object:
    req = urllib.request.Request(url, headers={"User-Agent": "clique-feedback/1"})
    try:
        with urllib.request.urlopen(req, timeout=20) as res:
            return json.loads(res.read().decode())
    except urllib.error.HTTPError as err:
        PROBLEMS.append(f"{source} refused the request ({err.code} {err.reason})")
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as err:
        PROBLEMS.append(f"{source} did not answer ({type(err).__name__})")
    # One source being down is not a reason to lose the ones that are up.
    return None


def hackernews() -> list[dict]:
    out = []
    for term in MENTIONS:
        data = fetch("https://hn.algolia.com/api/v1/search_by_date?"
                     + urllib.parse.urlencode({"query": term, "hitsPerPage": 20}),
                     "Hacker News")
        for hit in (data or {}).get("hits", []):
            ident = hit.get("objectID", "")
            out.append({
                "key": f"hn:{ident}",
                "kind": "hn",
                "title": (hit.get("title") or hit.get("story_title")
                          or (hit.get("comment_text") or "")[:120] or "(comment)"),
                "who": hit.get("author", "?"),
                "when": hit.get("created_at", ""),
                "url": f"https://news.ycombinator.com/item?id={ident}",
                "extra": "",
                "comments": hit.get("num_comments") or 0,
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


def problems() -> str:
    if not PROBLEMS:
        return ""
    lines = "\n".join(f"- {p}" for p in sorted(set(PROBLEMS)))
    return ("\n### Sources that did not answer\n\n" + lines
            + "\n\nAn empty section above may mean silence, or it may mean this.\n")


def main() -> int:
    quiet = "--quiet" in sys.argv
    rows = issues() + discussions() + hackernews()

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
## Mentioned elsewhere

{table([r for r in rows if r['kind'] == 'hn'])}
{problems()}"""

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
