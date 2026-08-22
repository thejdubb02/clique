---
name: drive-clique
description: >
  Drive CLIque over its HTTP API to run and coordinate coding agents on a box —
  start sessions, send prompts, read output, and wait for one to finish or come
  back asking. Use when a task means running work across several repos or agents
  at once and collecting the results, rather than doing it all in one session.
---

# Driving CLIque

CLIque runs coding-agent sessions (Claude, Codex, Grok, a shell, …) in tmux and
exposes **every** action as an HTTP call. That is the point: anything the panel
can do, a script or an agent can do. This skill is the orchestration surface.

## Connect

Base URL is the panel's own — `http://127.0.0.1:3200` on the same box, or the
tailscale URL from off it. Authenticate with a **bearer token**, not a cookie:

```bash
CLIQUE_TOKEN=$(python3 -m clique token create my-agent | grep -oE 'mxp_[A-Za-z0-9_-]+')   # shown once
curl -H "Authorization: Bearer $CLIQUE_TOKEN" http://127.0.0.1:3200/api/state
```

Use `--read-only` for a token that may observe but not start or stop anything.

## The one word that matters: `state`

Every session in `GET /api/state` carries a `state`:

| state | meaning |
|---|---|
| `working` | producing output right now |
| `waiting` | stopped and asking a person something (needs input) |
| `error`   | stopped on something that looks like an error |
| `idle`    | up, quiet, nothing pending — a task that finished cleanly |
| `stopped` | no process (killed or never started) |

Orchestration is: start work, then wait for `idle` (done) or `waiting` (it needs
you) — never a tight poll of the whole panel.

## Endpoints

- `GET  /api/state` — every session, each with `id`, `name`, `cli`, `cwd`, `state`, `rss`.
- `POST /api/sessions` — start one. Body: `{cli, cwd, name?, folder?, mode?}`.
  Add `{worktree: true, branch: "<name>"}` to run it in an **isolated git
  worktree** of the repo at `cwd` — the way to put several agents on one repo
  without them clobbering each other. Returns `{id, worktree}`.
- `POST /api/sessions/<id>/send` — `{text, enter: true}` submits a prompt.
- `GET  /api/sessions/<id>/peek?lines=N` — the last N lines of the pane.
- `GET  /api/sessions/<id>/transcript` — the full conversation as turns (Claude).
- `GET  /api/sessions/<id>/wait?for=idle,waiting&timeout=60` — **blocks** until
  the session reaches one of the `for` states or the timeout (max 300s). Returns
  `{state, matched, waited}`. This is the primitive to build on.
- `POST /api/sessions/<id>/kill` — stop it (keeps the record, resumable).
- `DELETE /api/sessions/<id>` — remove it. A worktree CLIque made is removed too,
  but only when it has no uncommitted changes.

## Recipe: one task across many repos

```bash
auth=(-H "Authorization: Bearer $CLIQUE_TOKEN"); base=http://127.0.0.1:3200
for repo in /root/platform/relay /root/platform/sentinel /root/platform/clique; do
  id=$(curl -s "${auth[@]}" $base/api/sessions -d \
    "{\"cli\":\"claude\",\"cwd\":\"$repo\",\"worktree\":true,\"branch\":\"audit\",\"name\":\"audit $(basename $repo)\"}" \
    | python3 -c 'import sys,json;print(json.load(sys.stdin)["id"])')
  curl -s "${auth[@]}" $base/api/sessions/$id/send -d \
    '{"text":"Audit this repo for security issues and write findings to AUDIT.md.","enter":true}' >/dev/null
  echo "$id"                                   # collect the ids
done
# then, for each id: wait for it to finish or come back asking
curl -s "${auth[@]}" "$base/api/sessions/$id/wait?for=idle,waiting,error&timeout=300"
# idle -> done; waiting -> it needs an answer; peek to see what it said:
curl -s "${auth[@]}" "$base/api/sessions/$id/peek?lines=40"
```

## Rules

- **Wait, don't spin.** Use `/wait`, not a loop over `/api/state`. It holds the
  connection until the state changes, capped at 300s — call again for longer.
- **Reads are bounded.** `peek` and `transcript` return a tail, never a whole
  30 MB transcript. Ask for the lines you need.
- **Worktrees for parallel same-repo work.** Two agents in one working tree fight
  over files; one worktree each and they do not. Delete the session to clean up.
- **A shell is not resumable.** Killing a `shell` session loses its state; killing
  a Claude/Codex one does not — it starts again where it was.
