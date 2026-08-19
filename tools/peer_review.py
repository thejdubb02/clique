"""Send the code to a second and third pair of eyes, and print what they say.

A review by the person who wrote the code finds the bugs that person is capable
of imagining. This asks models that were not in the room. It reports; it never
edits — the output is prose for a human to judge, not a patch to apply.

Cheap models on purpose. This is a wide, shallow pass over a small codebase
looking for the obvious things a fresh reader notices, not a deep audit, and
paying frontier prices for it would be paying for the wrong thing.

Usage:
    OPENROUTER_API_KEY=... python3 tools/peer_review.py [--model M] [--part N]

The key is a capped child key (`orkeys create`), never a raw account key.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"

#: Cheapest first, in the order the house rules put them. Both are strong on
#: code and both take a context large enough to hold this whole codebase.
MODELS = ("google/gemini-2.5-flash", "deepseek/deepseek-chat")

#: Reviewed in groups rather than all at once. A model given eight thousand
#: lines returns eight general observations; a model given one subsystem
#: returns specifics, which is the only kind of finding worth having.
PARTS: dict[str, tuple[str, ...]] = {
    "server": ("clique/app.py",),
    "protocol": ("clique/wsproto.py", "clique/stream.py", "clique/tmux.py"),
    "identity": ("clique/auth.py", "clique/tokens.py", "clique/store.py"),
    "features": (
        "clique/notify.py",
        "clique/artifacts.py",
        "clique/attention.py",
        "clique/registry.py",
        "clique/history.py",
        "clique/sysinfo.py",
    ),
    "frontend": ("clique/web/app.js",),
}

BRIEF = """You are reviewing CLIque: a browser-based, CLI-agnostic tmux session
manager. Python standard library only — no framework, no dependencies, no build
step. It is MIT and self-hosted by strangers, so a hole here is a hole on their
machines.

Report only. Do not write patches, do not rewrite the code, do not suggest
adding a framework or a dependency — the absence of those is the product.

Threat model, so you judge against the right bar:
- Anyone who reaches the panel gets a shell as the user who started it. That is
  stated in the README and is not a finding. What matters is whether someone
  who should NOT reach it can, or whether a read-only caller can act like a
  full one.
- It binds loopback and expects a tunnel in front of it.
- Auth is a scrypt password plus HMAC-signed session cookies, and bearer API
  tokens that are either full-access or read-only.

Deliberate, do not report as findings — but DO report a way to defeat one:
- `tmux` and `git` run from PATH. Argv is always a list; no shell is involved.
- SHA-1 in the WebSocket handshake is mandated by RFC 6455.
- The webhook may target loopback and private addresses on purpose; link-local
  is refused.
- clis.toml lets a user define arbitrary launch commands. That is the product.

Look for, in this order of value:
1. An unauthenticated or read-only caller doing more than they should: auth
   bypass, scope escape, CSRF, cross-site WebSocket hijacking, path traversal,
   SSRF, injection.
2. Crashes, hangs, deadlocks, and leaks — descriptors, threads, processes,
   unbounded memory. This runs for weeks without a restart.
3. Races. It is a ThreadingHTTPServer with background threads for the PTY
   pump, a keepalive, and a webhook watcher.
4. Logic that does not do what its own comment claims.
5. Errors swallowed in a way that leaves a state the user cannot see or fix.

For each finding: file and line, what is wrong in one or two sentences, a
concrete failing sequence, your confidence, and what you would do.

Most severe first. Say plainly when you are unsure instead of padding — a short
accurate report beats a long speculative one. If an area is clean, say so.
Judge the code, not the comments: the gap between the two is where several real
bugs have already been found."""


def collect(paths: tuple[str, ...]) -> str:
    out = []
    for name in paths:
        text = (ROOT / name).read_text()
        out.append(f"===== {name} =====\n{text}")
    return "\n\n".join(out)


def ask(model: str, prompt: str, key: str) -> str:
    body = json.dumps(
        {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.1,  # a review is not a place for invention
        }
    ).encode()
    request = urllib.request.Request(ENDPOINT, data=body, method="POST")
    request.add_header("Content-Type", "application/json")
    request.add_header("Authorization", f"Bearer {key}")
    request.add_header("X-Title", "clique-review")
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            payload = json.loads(response.read())
    except urllib.error.HTTPError as err:
        return f"[{model}] refused: {err.code} {err.read()[:400].decode(errors='replace')}"
    except (urllib.error.URLError, TimeoutError, ValueError) as err:
        return f"[{model}] did not answer: {type(err).__name__} {err}"
    choices = payload.get("choices") or []
    if not choices:
        return f"[{model}] returned nothing: {json.dumps(payload)[:400]}"
    return choices[0]["message"]["content"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", action="append", help="repeatable; defaults to both")
    parser.add_argument(
        "--part", action="append", choices=sorted(PARTS), help="repeatable; defaults to all"
    )
    parser.add_argument("--out", default="", help="write to a file instead of stdout")
    args = parser.parse_args()

    key = os.environ.get("OPENROUTER_API_KEY", "")
    if not key:
        print("OPENROUTER_API_KEY is not set", file=sys.stderr)
        return 1

    models = tuple(args.model or MODELS)
    parts = tuple(args.part or sorted(PARTS))
    chunks = []
    for part in parts:
        code = collect(PARTS[part])
        prompt = f"{BRIEF}\n\nThe code to review ({part}):\n\n{code}"
        for model in models:
            print(f"asking {model} about {part}…", file=sys.stderr)
            answer = ask(model, prompt, key)
            chunks.append(f"\n\n{'=' * 72}\n{model} — {part}\n{'=' * 72}\n\n{answer}")

    report = "".join(chunks)
    if args.out:
        Path(args.out).write_text(report)
        print(f"wrote {args.out}", file=sys.stderr)
    else:
        print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
