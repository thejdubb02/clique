<img src="clique/web/brand/lockup.svg" alt="CLIque — Your private clique of CLIs" width="420">

Folder-organised, CLI-agnostic coding sessions in a browser, persisted in tmux.

A replacement for Codeman + CodemanPanel: the same sidebar-and-tabs shape, but
the session engine is ours and it makes no assumption about which CLI is
running. Claude Code, Grok CLI or anything else is a block in
[`config/clis.toml`](config/clis.toml), not a code change.

**Live at https://example.invalid/clique** (tailnet only).
Password is in Vaultwarden as *CLIque (devbox)*.

## What works today (v0.16.0)

| | |
|---|---|
| **Command palette** | `Ctrl`/`Cmd`+`K` — fuzzy jump between sessions, most-recently-used first. `>` for commands, `@` for sessions, `~` for past conversations. `Ctrl`+`Shift`+`P` opens straight into commands. |
| **History** | Every conversation your CLIs have kept, found from a location the registry declares, filed by the directory it belongs to, and resumable in one click. Repeated runs of the same scheduled agent fold into one row. |
| Sidebar | Folder tree, drag-drop between folders, double-click rename, right-click menu, search, collapse to a rail (`Ctrl`/`Cmd`+`B`), resizable |
| Tabs | Drag to reorder, `Alt`+`1`–`9` to jump, close (the session keeps running), per-tab menu |
| Terminal | Live output, full scrollback on reattach, resize, auto-reconnect, themed to match the panel |
| **Paste an image** | `Ctrl`/`Cmd`+`V` with a screenshot on the clipboard saves it into the session's own directory and puts the path where you were typing. Nothing is sent until you press enter; text paste is untouched |
| **Scroll lock** | Scroll up and the view detaches from the stream, so output cannot drag it away mid-read. A badge says how far behind you are; the bottom, the lock button or `Ctrl`/`Cmd`+`Shift`+`L` re-attaches |
| **Shortcuts** | Every binding in one reference — `?` in the tab bar, `Ctrl`/`Cmd`+`Shift`+`/`, or the palette |
| Markers | One mark per session: the CLI's own logo, carrying the status colour, pulsing while it works |
| Themes | Nine presets including **Trinity**, light/dark/system, custom CSS in three slots, independent font sizes |
| Input bar | Mode pill, prompt box, Run / Shell split, repeat stepper, snippets in both input paths |
| Stats | CPU, memory, swap, disk, load, connected terminals, with an hour of history |
| Sessions | Create with CLI + directory + folder, resume a past conversation, archive, kill with confirmation |
| Adoption | Take over sessions started by another tool — CLI detected from the process tree, names and folders carried across. Safe to run twice; it repairs earlier runs |
| Security | Password login (scrypt), API tokens, CSRF, `Origin` and `Host` checks, CSP with per-response nonces. See [SECURITY.md](SECURITY.md) |
| Mobile | Installable as a PWA with a full icon set. The layout is not responsive yet |

Not built, and deliberately: subagent visualisation, respawn controller,
multi-host, Ralph loop.

- **What is being built next, in order** — [docs/next.md](docs/next.md)
- **Why it is ranked that way, and what is deliberately refused** —
  [ROADMAP.md](ROADMAP.md), ordered by where five independent feature lists
  agreed without seeing each other's work
- **Raised but not committed to** — [docs/ideas-inbox.md](docs/ideas-inbox.md)

## Why it is stdlib-only

The box already carries ~500 MB of RAM per Claude Code session, so the
manager's job is to disappear into that budget. No framework, no
`node_modules`, no build step — `tmux`, `pty` and `tomllib` are enough.
Measured: **24 MB resident**, against Codeman's 253 MB.

That also means the real ceiling on concurrent sessions is Claude Code, not
this. On a 16 GB box with ~9 GB free that is roughly 12–15 at once.

## Support the dev

CLIque is free and staying that way. If it saved you an afternoon, there is a
tip jar.

**[Buy me a coffee](https://buymeacoffee.com/jdubb)**

| | |
|---|---|
| **BTC** — Bitcoin network | `3A3nA8BQFmXdvyUQokHhPd8HAd99wRDYFQ` |
| **SHIB** — Ethereum network | `0x6b5DEd92946692D50642dC3af169727225E32D3b` |
| **DOGE** — Dogecoin network | `DNiJeUJUVaVTDuteLXCtP7JVgvdL2NqoYp` |

## Adding a CLI

Add a block to `config/clis.toml` and reload — no restart, no code:

```toml
[cli.grok]
label   = "Grok CLI"
command = "grok"
args    = []
color   = "#6f42c1"
```

There is no per-CLI branch anywhere in the codebase. If adding one ever needs
a code change, the design has failed.

### The autonomy pill

The pill above the prompt box belongs to whichever session you are looking at,
and it comes entirely from that CLI's block. Four keys:

```toml
[cli.claude]
modes      = ["auto", "default", "acceptEdits", "plan"]
mode_key   = "S-Tab"                                # what cycles it
mode_label = "{mode} mode on (shift+tab to cycle)"  # how it reads
```

- **`modes`** is what makes the pill appear at all. Omit it and the pill is
  hidden — which is right for a CLI whose approval is a launch flag rather
  than something you cycle, like Grok's `--always-approve`.
- **`mode_key`** is cycled by clicking the pill *and* watched for coming out
  of the keyboard, so cycling by hand inside the pane moves the pill too.
  `S-Tab`, `Tab` and `C-<letter>` are understood; anything else means CLIque
  can send the key but cannot notice you pressing it, and the pill will drift.
- **`mode_label`** is that CLI's own wording. It used to be hardcoded to
  Claude Code's, which read as a lie on anything else.

**What the pill knows, and does not.** It tracks the mode CLIque *last saw
set* — by you clicking it, or by the cycle key going through the pane. It
cannot read the CLI's actual state, because doing that would mean parsing
someone's terminal output, and that is the line this project does not cross.
In practice the two agree, because those are the only two ways the mode moves.

Only Claude Code and Gemini CLI declare modes today. The others in
`clis.toml` are catalogue entries — none of them is installed on this box, so
nobody has verified which key cycles what, and **a pill that names the wrong
mode is worse than no pill**. Adding one is four lines and no code, once
someone has actually watched it work.

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
