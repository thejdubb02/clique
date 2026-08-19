<img src="clique/web/brand/lockup.svg" alt="CLIque — Your private clique of CLIs" width="420">

Folder-organised, CLI-agnostic coding sessions in a browser, persisted in tmux.

A replacement for Codeman + CodemanPanel: the same sidebar-and-tabs shape, but
the session engine is ours and it makes no assumption about which CLI is
running. Claude Code, Grok CLI or anything else is a block in
[`config/clis.toml`](config/clis.toml), not a code change.

**Live at https://example.invalid/clique** (tailnet only).
Password is in Vaultwarden as *CLIque (devbox)*.

## What works today (v0.9.1)

| | |
|---|---|
| **Command palette** | `Ctrl`/`Cmd`+`K` — fuzzy jump between sessions, most-recently-used first. `>` for commands, `@` for sessions, `~` for past conversations. `Ctrl`+`Shift`+`P` opens straight into commands. |
| **History** | Every conversation your CLIs have kept, found from a location the registry declares, filed by the directory it belongs to, and resumable in one click. Repeated runs of the same scheduled agent fold into one row. |
| Sidebar | Folder tree, drag-drop between folders, double-click rename, right-click menu, search, collapse to a rail (`Ctrl`/`Cmd`+`B`), resizable |
| Tabs | Drag to reorder, `Alt`+`1`–`9` to jump, close (the session keeps running), per-tab menu |
| Terminal | Live output, full scrollback on reattach, resize, auto-reconnect, themed to match the panel |
| Markers | One mark per session: the CLI's own logo, carrying the status colour, pulsing while it works |
| Themes | Nine presets including **Trinity**, light/dark/system, custom CSS in three slots, independent font sizes |
| Input bar | Mode pill, prompt box, Run / Shell split, repeat stepper, snippets in both input paths |
| Stats | CPU, memory, swap, disk, load, connected terminals, with an hour of history |
| Sessions | Create with CLI + directory + folder, resume a past conversation, archive, kill with confirmation |
| Adoption | Take over sessions started by another tool — CLI detected from the process tree, names and folders carried across. Safe to run twice; it repairs earlier runs |
| Security | Password login (scrypt), API tokens, CSRF, `Origin` and `Host` checks, CSP with per-response nonces. See [SECURITY.md](SECURITY.md) |
| Mobile | Installable as a PWA with a full icon set. The layout is not responsive yet |

Not built, and deliberately: subagent visualisation, respawn controller,
multi-host, Ralph loop. What is planned, and what is deliberately refused, is
in [ROADMAP.md](ROADMAP.md) — ranked by where five independent feature lists
agreed without seeing each other's work.

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
