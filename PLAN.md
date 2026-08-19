# Session Manager — implementation plan (pre-code review)

Status: **built and shipped as v0.1.0 on 2026-08-19.** Kept as the record of what
was decided before implementation, and why. See README.md and CHANGELOG.md for
what exists now.
Reviewed against CodemanPanel and Codeman v1.18.4 on 2026-08-18.

---

## 1. Conflicts in the brief

### 1.1 CodemanPanel is not a Node app, and has no tab bar or terminal

The brief asks to "reuse CodemanPanel's existing component structure" **and** to build on
"Node.js + Fastify, matching what Codeman itself uses". Those are two different codebases.

| | CodemanPanel | Codeman |
|---|---|---|
| Language | Python 3.12, **stdlib only** (no framework, on purpose) | TypeScript / Node 22 |
| Server | `http.server.ThreadingHTTPServer` | Fastify 5 + `@fastify/websocket` |
| Frontend | Vanilla JS + CSS, no build step (1,124 lines JS) | xterm.js 6, bundled |
| Size | ~4,400 lines, zero dependencies | 34 MB src, **789 MB node_modules** |
| Terminal | none | node-pty + xterm |
| Tab bar | **none — explicitly refused** ("two strips for one set of tabs is redundant chrome") | yes |
| tmux logic | none | `tmux-manager.ts`, 3,604 lines |

So the screenshot is **two products stitched together**: the sidebar is CodemanPanel, the tab
bar / terminal / input bar are Codeman. Roughly half of "reuse CodemanPanel" is not available
because that half does not exist there.

### 1.2 What is genuinely reusable (and it is a lot)

Lifted or adapted, not rebuilt:

- `auth.py` (133 lines) — password + signed cookie + login page. Take almost verbatim.
- `store.py` (158 lines) — folder store, colour palette, **auto-filing by directory prefix**
  (directory prefix → a named folder). Adapt.
- `data/groups.json` — the folder schema he already uses, with his six real groups and their
  colours. Reuse as the seed file.
- `prefix_proxy.py:143` — a working hand-rolled WebSocket relay over stdlib sockets. This is
  the piece that makes a Python backend viable for terminal streaming.
- `web/app.css` (318 lines) + the sidebar half of `web/app.js` — search box, folder tree,
  drag-drop between groups, double-click inline rename, collapse-to-rail, theme tokens.

Not reusable, must be new: tmux engine, PTY bridge, tab bar, terminal panel, input bar,
CLI registry, stats bar.

### 1.3 The stack decision this forces — **recommendation: Python**

Two coherent options; picking one is the only thing blocking the build.

**A. Python, stdlib-only** (recommended). Same shape as CodemanPanel, so the sidebar, auth
and WebSocket code come across as-is. `pty` and `tomllib` are in the standard library, so
there are no compiled dependencies, no `node_modules`, no build step. Expected footprint
**~30–50 MB RSS**. Cost: the Fastify patterns from Codeman don't transfer — but the parts
worth transferring are `tmux send-keys` / `capture-pane` shell calls, which are shell, not
framework.

**B. Node + Fastify.** Matches Codeman, so its tmux and xterm patterns port directly. Cost:
the sidebar is rewritten from zero, plus ~790 MB of `node_modules` and a build step. The
running Codeman web process is **253 MB RSS** today; Fastify + node-pty lands in that range.

Given "lightweight, so I can run many tmux sessions", A wins on his stated constraint.

### 1.4 The real resource ceiling is not the manager

Measured on this box right now:

- 4 CPUs, 16 GB RAM, **9 GB available**, 6 Claude sessions live.
- Each `claude` process: **~480–550 MB RSS**. Six of them ≈ 3 GB.
- Codeman's web server: 253 MB.

So the session manager is rounding error; **Claude Code itself sets the ceiling at roughly
12–15 concurrent sessions** on this box regardless of what we build. What the manager *can*
do is refuse to add per-session overhead — see §4.

### 1.5 Minor

- No screenshot reached this session. The plan is built from the written description only;
  if the image has layout detail the text doesn't, it may change the frontend section.
- Ports 3000/3001 are Codeman (claude/grok), 3100/3101 are CodemanPanel. Proposing **3200**.

---

## 2. Checklist answers

| Item | Answer |
|---|---|
| Confirm existing frontend framework and reuse it | **No framework.** Vanilla JS + CSS, no build step. Reuse it as-is — introducing React here would be the regression. |
| Any backend tmux/session logic worth keeping? | **None in CodemanPanel** — it shells out to Codeman's HTTP API (`codeman.py`) and never touches tmux. Codeman's `tmux-manager.ts` is 3,604 lines and worth reading for technique, not porting. |
| What does the brief duplicate? | Search, folder tree, drag-drop, inline rename, create/delete folders, per-folder colour dot, session counts, elapsed time, password auth — **all already built and working** in CodemanPanel. Also already solved there and worth keeping: auto-filing new sessions by directory rule, and collapse-to-rail. |
| CLI registry format | TOML config file — see §3. Adding Grok is a 5-line block, no code change. |
| tmux name collision | **Safe.** Codeman uses `codeman-<8hex>`; we use `sm-<8hex>`. Verified Codeman only ever kills sessions present in its own `state.json` (`tmux-manager.ts:2431-2446`), so it will not touch ours, and we will only touch `sm-*`. Both can run side by side through the transition. |
| Implementation plan / file structure | §4 and §5. |

---

## 3. CLI type registry

`config/clis.toml`, read with stdlib `tomllib`. Hot-reloaded on change — no restart.

```toml
[cli.claude]
label   = "Claude Code"
command = "claude"
args    = ["--permission-mode", "{mode}", "--session-id", "{uuid}", "--name", "{name}"]
resume  = ["--permission-mode", "{mode}", "--resume", "{cli_session_id}"]
color   = "#c7915b"
modes      = ["default", "acceptEdits", "plan", "auto"]   # drives the mode pill
mode_key   = "S-Tab"                                      # what the pill sends into the pane
mode_label = "{mode} mode on (shift+tab to cycle)"

[cli.grok]
label   = "Grok CLI"
command = "grok"
args    = []
color   = "#6f42c1"
# no `modes` key -> the mode pill is hidden for this CLI automatically

[cli.shell]
label   = "Shell"
command = "bash"
args    = ["-l"]
color   = "#8b8b8b"
```

**Adding a new CLI = adding a block.** Placeholders (`{uuid}`, `{name}`, `{mode}`,
`{cwd}`, `{cli_session_id}`) are substituted at launch; unknown placeholders are an error at
load time, not a mystery at runtime. Absence of `modes` is what hides the autonomy pill, so
that requirement falls out of the config rather than needing a per-CLI code branch.

---

## 4. Architecture — the lightweight part

- **One tmux session per CLI instance**, `sm-<8hex>`, full UUID kept in state. tmux is the
  only thing that persists; the manager holds no session state in memory it can't rebuild.
- **PTY only while a browser is watching.** On WebSocket connect, fork a PTY running
  `tmux attach -t sm-xxxx`; on disconnect, kill it. An idle session costs the backend
  **zero** — cost scales with open tabs, not with session count. This is the single decision
  that makes "many sessions" cheap.
- **Reattach shows scrollback** via one `capture-pane -p -e -J -S -5000` at connect, then live
  stream. No persistent `pipe-pane` log files, so nothing grows on disk.
- **Sidebar polling is one subprocess total** — a single `tmux list-sessions -F '#{...}'` every
  3s for every session's status, not one call per session.
- **Input** goes back as `send-keys -l` + `Enter` (Codeman's approach, and the reliable one).
- **State**: single `data/state.json`, atomic write + `.bak`, same as CodemanPanel.
- **Auth**: password + signed cookie, Tailscale-only bind. No QR.

---

## 5. File structure

```
<repo>/
├── pyproject.toml
├── README.md
├── config/
│   └── clis.toml               # CLI registry (§3)
├── data/
│   └── state.json              # sessions + folders; seeded from CodemanPanel groups.json
├── deploy/
│   └── <name>.service          # systemd user unit, pattern from CodemanPanel deploy/
└── <pkg>/
    ├── __main__.py             # serve --host --port 3200
    ├── app.py                  # routing, static, JSON API, WS upgrade    [pattern: CodemanPanel app.py]
    ├── auth.py                 # password + cookie                        [LIFT ~as-is]
    ├── store.py                # folders, sessions, auto-file rules       [ADAPT]
    ├── registry.py             # clis.toml loader + arg templating        [NEW]
    ├── tmux.py                 # create / list / attach / kill / send / capture  [NEW]
    ├── stream.py               # PTY <-> WebSocket bridge                 [NEW, WS framing from prefix_proxy.py]
    └── web/
        ├── index.html
        ├── app.css             # sidebar [ADAPT] + tab bar / terminal [NEW]
        ├── app.js              # sidebar [ADAPT] + tabs / term / input [NEW]
        └── vendor/             # xterm.js + fit addon, vendored (no CDN, no build step)
```

---

## 6. Build order

Unchanged from the brief; it is the right order.

1. `tmux.py` + `registry.py` — engine and CLI config, exercised from a CLI smoke test
2. `app.py` + `stream.py` — API and live streaming, proven end to end with Claude Code
3. Terminal panel + tab bar
4. Sidebar (ported from CodemanPanel) — folder tree, drag-drop, rename, search
5. Add Grok as a registry block only — if it needs a code change, the registry design failed
6. Polish: stats bar, run/shell split, new-session modal

Phase 2, per the brief: repeat stepper, voice input.
