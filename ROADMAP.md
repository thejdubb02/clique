# CLIque — roadmap

Updated 2026-08-19. **Five** independent feature lists now exist for this tool
— three gathered in July via [`docs/feature-research-prompt.md`](docs/feature-research-prompt.md)
and two more added on 19 August. None of the five saw another's work.

What matters is not any single list. It is **where they agree without
collusion**, and that is what ranks everything below. A `[n/5]` is how many of
the five named it.

---

## The two rules that decide what belongs here

> **1. Every feature should work from the filesystem, tmux, and process state,
> with optional git detection.**

If a feature needs to know which AI vendor is running, understand its
protocol, or interpret what its model "thinks", that is a strong signal it
does not belong in the core product. This is why the working indicator was
built from tmux's activity clock rather than any vendor's API, and it is what
keeps the CLI registry honest.

> **2. This is a driver, not an IDE.**

The job is to make it trivial to jump from an agent to whatever tool already
does the thing well — not to reimplement that tool. Named independently by
four of the five lists, usually while flagging the same trap.

---

## Shipped

**Command palette + fuzzy session jump `[5/5]` — 0.4.0.** Every list ranked it
first or second; it is the only item all five named. `Ctrl`+`K` for sessions
ordered most-recently-used, `>` for commands, `@` for sessions, `Ctrl`+`Shift`+`P`
straight into commands. One list gave the real reason: **it stops the UI
becoming a collection of buttons** as features accumulate.

**Resumable conversation history.** Every transcript a CLI has kept, found
from a registry-declared location, labelled by its first prompt, searchable
with `~` in the palette, and opened by resuming it in the right folder.

**Per-session prompt drafts `[4/5]` — 0.17.0.** Half-typed instructions
survive a tab switch, a reload and a closed laptop. On the server, so they
follow you to another device — the rule set in 0.4.1, applied.

**Scroll lock / follow mode `[3/5]` — 0.13.0.** Two lists ranked it #1
outright. Scrolling up detaches the viewport; the bottom re-attaches; a badge
carries how far behind the pane has fallen. The stream is never frozen, only
the view.

**Paste a screenshot into a session — 0.12.0.** The image lands in the
session's own directory and the path lands where you were typing. No CLI knows
anything about it; every one of them can already read a file.

Also done: session engine on its own tmux server · CLI registry with
auto-detection · folder tree with drag-drop, rename, search, archive, colours ·
numbered tabs · live terminal with scrollback across reattach · per-CLI icons
and colours in four display modes · working pulse and finished flash with
optional chime · eight themes, light/dark/system, custom CSS in three slots ·
independent font sizes · snippets in both input paths · CPU/memory/swap/disk/load
with an hour of history · password login, API tokens, CSRF, throttling, CSP
with per-response nonces · published on the tailnet under systemd.

**Explicit attention states `[4/5]` — 0.26.0.** Three tiers that degrade into
each other: tmux's clock, regexes declared per CLI in `clis.toml`, and a
`POST .../attention` a session fires from a hook the user already has. Nothing
in it knows which vendor is talking — the vendor-specific part lives in *their*
config, where they can fix it the day a prompt changes.

**One outbound webhook — 0.27.0.** The half that makes the above matter: a URL,
POSTed when a session wants you, errors, finishes or dies. ntfy, Gotify,
Discord, Mattermost, Home Assistant and Uptime Kuma push all speak it, so one
field reaches a phone with no app of ours and no per-service settings.

**Seeing what an agent made — 0.23.0**, status as a ring around an untouched
logo — **0.24.0**, the pane telling you which CLI you are in — **0.25.0**, and
clickable URLs — **0.27.1**, clickable file paths — **0.50.12**.

---

## Next, in order

Re-ranked 2026-08-19 after a verified pass over the field. The ordering rule is
**impact on adoption × fit with the two rules ÷ cost** — which is why an
afternoon of packaging now outranks a week of features.

### 1. One command to install it — `uvx clique` `[new]`
Publish to PyPI. `pyproject.toml` exists and there are no dependencies, so the
work is packaging, not code. It outranks every feature here: it sits at the top
of the funnel, and it is the one install story nothing else in this category
can match — the alternatives need Docker, a Rust toolchain, .NET, or apt and a
venv. "No dependencies" is a claim in a README; `uvx clique` is a demonstration
of it. Right now the first code block is five steps starting with `git clone`,
which reads as a hobby project no matter what the code is.
*An afternoon · no runtime cost at all*

### 2. Make the phone claim true `[4/5]`
The layout at 390px, and an on-screen row for `Esc`, `Tab`, `Ctrl`, arrows and
`Ctrl-C`. This moved up because 0.27.0 made it load-bearing: the panel can now
push a notification to a phone, and if the phone is not somewhere you can act,
the notification is a taunt. The README currently admits the gap directly under
the headline promise.
*Front end · a week, and the least fun week here*

### 3. Repo, branch and dirty state on the row — **0.50.14**
`git -C <cwd>` three times, cached per directory with a short TTL. Rated low by
the original lists and it should not have been: the leader of this category is
rooted in one repo and its users are openly asking for exactly this, while
CLIque's folder tree has been multi-repo since day one and says so nowhere.

### 4. Hover preview of the last few lines `[3/5]`
On sidebar rows and tabs, and on long-press for touch. Glance without
switching. Now worth more than it was: it turns an attention ring from a colour
into an answer — *waiting, on a permission prompt about `rm`*. Shares its pane
capture with the attention patterns, so it costs nothing extra.
*Both · hours*

### 5. Searchable prompt history `[4/5]`
Every prompt sent, per session and globally, fuzzy-searchable, one click to
reuse or edit. **Snippets are for deliberate reuse; history is for accidental
reuse** — they do not replace each other, and snippets already shipped.
*Both · a day*

### 6. Optional worktree launch, with the setup hook `[new]`
Not because worktrees drive adoption here — they drive adoption at a tool that
already has a year of polish on them. Because *"does it do worktrees?"* is the
first question every visitor from that world asks, and "no" ends the
conversation before they see the browser, the phone, or the 24 MB.

The rule goes in `CLAUDE.md` before any code: **CLIque may create a worktree
and may forget one. It must never merge one.** And it does not ship without the
setup hook — a fresh worktree has no `.env`, no `node_modules`, no venv, so the
agent's first act is a failing test run and the user concludes CLIque broke
their repo. That hook is the top open feature request on the category leader,
and it is the only reason to build this rather than skip it.
*Both · a week, most of it in cleanup and failure states*

---

## The rest of the backlog

**Daily friction**
- Jump to the last prompt when a session opens — you currently land wherever
  the stream left you `[3/5]` *(both · hours)*
- Copy last output block / copy visible / copy last N lines `[2/5]` *(developer · hours)*
- Smart focus: prompt box on tab switch, `Esc` back to the terminal `[3/5]` *(both · hours)*
- Drag a path from a file tree into the prompt `[2/5]` *(both · a day)*
- Unread activity and a since-last-viewed separator `[2/5]` *(both · hours)*

**Awareness**
- Session status line: elapsed, last activity, cwd, process state, git branch `[3/5]` *(both · hours)*
- Per-session CPU/memory share, to catch one agent starving the box `[3/5]` *(developer · hours)*
- Turn counter per session `[2/5]` *(vibe · hours)*
- Quieter notifications — pattern-matched rather than "output stopped" `[1/5]` *(developer · a day)*
- Activity timeline — jump between notable events in long scrollback `[1/5]` *(developer · a day)*
- Agent-is-thinking animation on the session icon `[1/5]` *(vibe · hours)*

**Organisation**
- Session templates: CLI + directory + starter prompt + name pattern `[3/5]` *(both · a day)*
- Pin / favourite, floating above recency `[2/5]` *(both · hours)*
- Duplicate / fork a session — same directory, fresh CLI `[2/5]` *(developer · hours)*
- Multi-select for bulk archive, move, colour `[2/5]` *(both · hours)*
- Per-session notes, a sidecar `.md` `[2/5]` *(both · a day)*
- Tags as a second axis alongside folders `[2/5]` *(developer · a day)*
- Recent sessions as a virtual folder `[1/5]` *(both · hours)*
- Auto-titled sessions from the first prompt `[2/5]` *(both · hours)*
- Git branch and dirty state in the sidebar `[1/5]` *(developer · a day)*

**Safety**
- Confirm-on-close when a session is producing output or has an unsent draft `[3/5]` *(both · hours)*
- Read-only lock on the prompt box while reviewing `[2/5]` *(vibe · hours)*
- Soft interrupt (`Ctrl-C`) that marks the session paused `[2/5]` *(both · hours)*
- Export scrollback to timestamped text or markdown `[2/5]` *(developer · hours)*
- Detach-by-default, kill explicit, with a short undo `[2/5]` *(both · hours)* —
  already the model, but the undo is missing
- Configurable destructive-command confirmation `[1/5]` *(both · a day)*
- Checkpoint button: `git diff --stat` plus current HEAD to a file, before you
  let an agent loose `[1/5]` *(developer · hours)*

**Files** — asked for directly, and only one of the five lists covered it
- Drag-and-drop upload into a session's working directory
- Paste text *and images* into the terminal
- Download a file from the session without an scp dance
- A light file browser for the session's directory

**Mobile** — asked for directly, covered by none of the five
- Responsive layout that survives a phone: sidebar, tab bar, touch targets
- An on-screen key row for the terminal — `Esc`, `Tab`, `Ctrl`, arrows
- Installable PWA: manifest, service worker, the icon set from the rebrand

**Delight**
- Zen mode: terminal and a floating prompt, nothing else `[2/5]` *(vibe · hours)*
- Editable keyboard map `[2/5]` *(both · a day)*
- Folder-level prompt launchers — folders as workspaces `[1/5]` *(both · a day)*
- Terminal marks you can jump between `[1/5]` *(developer · hours)*
- Bring-your-own-key LLM assistant in a right-hand panel *(later, by request)*.
  Under the estate rules that gets its own key, a registry entry, and a
  monitor — three things, not one.

---

## Traps — named independently by more than one list

1. **Agent orchestration or broadcast-to-many-sessions** `[3/5]`. It demos
   beautifully — type "git pull" once, four agents obey — and then produces
   four divergent code states you cannot reconcile. It also drags in durable
   job state, which means a database, which breaks rule 1. Be excellent at
   *"I have twelve agents, help me drive them"* and refuse *"I have twelve
   agents, decide for me."*
2. **A built-in diff viewer, file-tree editor, or git UI** `[3/5]`. It stops
   being CLI-agnostic, duplicates what the agent already does well, and is a
   large surface that will rot. The boundary that works: make it trivial to
   *jump to* the tool that already does this.
3. **LLM-generated session summaries** `[3/5]`. Scrollback is full of ANSI
   noise, so it is expensive, slow and mediocre — and it breaks the
   vendor-agnostic rule. A deterministic "what happened" view built from
   timestamps, prompts and exit status gets most of the value for nothing. The
   cheaper answer already exists: ask the agent to summarise its own work.
4. **Full-text search across all historical scrollback** `[2/5]`. It forces
   either a database or heavy scanning to answer a question asked twice a
   week, on a box already running several AI CLIs.
5. **Browser-side split panes / nested tmux visualisation** `[1/5]`. Demos
   well; plain JS plus resource limits make a good implementation expensive
   and fragile. Tabs are already the right abstraction.

### Replacing tmux: asked and answered, 2026-08-28

Raised after a run of pane-sizing bugs: tmux feels rigid, is there a better
engine? Two outside models were asked independently, given the architecture and
the four non-negotiables, and told to name what would break before recommending
anything. They agreed with each other, and the answer is no.

**The constraint is the kernel, not tmux.** A Unix PTY has one `winsize`. Every
process on that terminal sees one COLUMNS by LINES. Nothing layered on top,
tmux or otherwise, can hand two interactive clients honest independent grids of
the *same* process. One of them is always being lied to: cropped, padded,
scaled or reflowed. Every option is a choice of which lie.

- **abduco / dtach** have no terminal emulation at all, so no server-side
  buffer and no redraw on reattach. That trades a sizing annoyance for a blank
  screen every time a browser reconnects, and loses grouping, per-browser
  current window and copy-mode. Same shared size regardless.
- **screen** is the same one-size-per-window model. Its `fit` force-resizes to
  the current display and leaves the larger one padded, which is our dot-fill
  problem in a different hat.
- **zellij** needs a Rust runtime, which fails the stdlib rule on its own. The
  tell is that 0.45, shipped this month, added per-*tab* sizing rather than
  per-client, because per-client is the part that cannot be done. That is
  roughly what our session groups already give us.
- **Our own Python supervisor** gets you abduco in a few hundred lines, and
  then the real bill: per-client rendering needs a real VT server-side. CSI and
  OSC parsing, DEC private modes, scroll regions, truecolor, CJK widths,
  bracketed paste, mouse modes. Several thousand lines to be merely adequate,
  subtly wrong in exactly the box drawing these CLIs use, CPU-hot in pure
  Python, and it still does not produce two honest widths. SSH attach also
  stops being a real terminal on a live session and becomes a custom client,
  which breaks the third non-negotiable outright.

**Keep tmux.** One shared window size is a fair price for a C multiplexer that
survives weeks, attaches from plain SSH, and costs nothing to ship. The tell is
that of the bugs actually hit on 2026-08-28, every one was in our own layout
layer. The multiplexer was doing the only thing the kernel allows.

**What came out of it that is worth doing** is in [docs/next.md](docs/next.md):
`aggressive-resize`, attaching background viewers with `ignore-size`, and
saying the shared size out loud in the pane header instead of leaving people to
guess why their pane looks wrong.

### The one genuine disagreement

One list proposes **detecting diff blocks in the scrollback and rendering them
side-by-side in a new tab**. Another list names a built-in diff viewer as a
trap outright.

They are both right about different things, and the resolution is rule 2. A
diff *viewer* is read-only, disposable, and does not touch the filesystem —
that is a legitimate jump-to-the-right-tool move. A diff *editor* — accept,
reject, apply a hunk — crosses into being an IDE, has to handle patch
application and filesystem races, and will rot.

**If it is ever built: rendering yes, applying never.**
