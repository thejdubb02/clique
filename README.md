<img src="clique/web/brand/lockup.svg" alt="CLIque — Your private clique of CLIs" width="420">

Folder-organised, CLI-agnostic coding sessions in a browser, persisted in tmux.

A replacement for Codeman + CodemanPanel: the same sidebar-and-tabs shape, but
the session engine is ours and it makes no assumption about which CLI is
running. Claude Code, Grok CLI or anything else is a block in
[`config/clis.toml`](config/clis.toml), not a code change.

**Live at https://example.invalid/clique** (tailnet only).
Password is in Vaultwarden as *CLIque (devbox)*.

## What works today (v0.1.0)

| | |
|---|---|
| Sidebar | Folder tree, drag-drop between folders, double-click rename, right-click menu, search, collapse to a rail (`Ctrl`/`Cmd`+`B`) |
| Tabs | Numbered, `Alt`+`1`–`9` to jump, close and per-tab menu, `+` to start a session |
| Terminal | Live output, full scrollback on reattach, resize, auto-reconnect |
| Input bar | Mode pill, prompt box, Run / Shell split, repeat stepper |
| Stats | CPU, memory, connected terminals |
| Sessions | Create with CLI type + directory + folder, kill with confirmation, adopt sessions from another tool |

Not built, and deliberately: subagent visualisation, respawn controller, mobile
layout, multi-host, Ralph loop. Voice input is still to come.

## Why it is stdlib-only

The box already carries ~500 MB of RAM per Claude Code session, so the
manager's job is to disappear into that budget. No framework, no
`node_modules`, no build step — `tmux`, `pty` and `tomllib` are enough.
Measured: **24 MB resident**, against Codeman's 253 MB.

That also means the real ceiling on concurrent sessions is Claude Code, not
this. On a 16 GB box with ~9 GB free that is roughly 12–15 at once.

## Adding a CLI

Add a block to `config/clis.toml` and reload — no restart, no code:

```toml
[cli.grok]
label   = "Grok CLI"
command = "grok"
args    = []
color   = "#6f42c1"
```

Declaring `modes = [...]` is what makes the autonomy pill appear for that CLI.
Omit it and the pill is hidden. There is no per-CLI branch anywhere in the
codebase; if adding one ever needs a code change, the design has failed.

## Design notes that are easy to get wrong

- **Own tmux server** (`tmux -L clique`). Isolates us from Codeman's sessions
  and lets us set server-wide options without touching anyone else's work.
- **A PTY exists only while a browser is attached.** An idle session costs this
  process nothing; cost scales with open tabs, not with session count.
- **Each viewer gets its own grouped tmux session.** Plain `attach` makes every
  client share one size, so tmux shrinks the pane to the smallest one watching.
- **Session targets and pane targets are different tmux syntax** — `=name` vs
  `=name:`. Getting it wrong fails as "can't find pane".
- **`kill()` refuses sessions that are not ours** unless forced.
- **Closing a tab is not killing a session.** Detach sends SIGHUP; tmux and the
  CLI carry on.

## Tests

```bash
python3 tools/smoke.py                 # engine, against a real tmux server
python3 tools/smoke_http.py            # server, over real HTTP + WebSocket
python3 tools/smoke_http.py https://example.invalid/clique
ruff check .
```

Neither suite is mocked. The failure modes worth catching — tmux quoting, frame
masking, a PTY that never gets its first byte — only exist across a real socket.
They still will not catch a UI that renders wrong; open the page for that.

## Deploying

See [deploy/README.md](deploy/README.md).
