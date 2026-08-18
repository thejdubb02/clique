# muxpanel

Folder-organised, CLI-agnostic coding sessions in a browser, persisted in tmux.

A replacement for Codeman + CodemanPanel: the same sidebar-and-tabs shape, but
the session engine is ours and it makes no assumption about which CLI is
running. Claude Code, Grok CLI or anything else is a block in
[`config/clis.toml`](config/clis.toml), not a code change.

**Status: phase 1 of 6.** Session engine and CLI registry done and tested; no
server or UI yet. See [PLAN.md](PLAN.md).

## Why it is stdlib-only

The box this runs on is already carrying ~500 MB of RAM per Claude Code
session, so the manager's job is to disappear into the noise. No framework, no
`node_modules`, no build step. `tmux`, `pty` and `tomllib` are enough.

## Design notes that are easy to get wrong

- **Own tmux server** (`tmux -L muxpanel`). Isolates us from Codeman's sessions
  and lets us set server-wide options without touching anyone else's work.
- **A PTY exists only while a browser is attached.** An idle session costs this
  process nothing; cost scales with open tabs, not with session count.
- **Session targets and pane targets are different tmux syntax** — `=name` vs
  `=name:`. Getting it wrong fails as "can't find pane".
- **`kill()` refuses sessions that are not ours** unless forced.

## Running the tests

```
python3 tools/smoke.py
```

Runs against a real tmux server on a throwaway socket. Not mocked: the failure
modes worth catching only exist when tmux is actually running.
