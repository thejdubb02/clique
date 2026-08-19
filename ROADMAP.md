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

Also done: session engine on its own tmux server · CLI registry with
auto-detection · folder tree with drag-drop, rename, search, archive, colours ·
numbered tabs · live terminal with scrollback across reattach · per-CLI icons
and colours in four display modes · working pulse and finished flash with
optional chime · eight themes, light/dark/system, custom CSS in three slots ·
independent font sizes · snippets in both input paths · CPU/memory/swap/disk/load
with an hour of history · password login, API tokens, CSRF, throttling, CSP
with per-response nonces · published on the tailnet under systemd.

---

## Next five, in order

### 1. Scroll lock / follow mode `[3/5]`
Detach the viewport from the stream so a new token cannot yank you to the
bottom mid-read. **Two lists ranked this #1 outright**, and the argument is
unanswerable: *if you cannot pause the output you physically cannot read or
copy from it.* It is the prerequisite for every review feature below it, and
it is hours of work.
*Developer · hours*

### 2. Per-session prompt drafts `[4/5]`
Half-typed instructions survive a tab switch, a reload, and a closed laptop.
Pure loss-prevention, fires dozens of times a day, and one list called it "the
single cheapest high-frequency win". Under the sync rule established in 0.4.1
this belongs **on the server**, not in localStorage: a draft is about the work.
*Both · hours*

### 3. Explicit attention states — waiting, error `[4/5]`
Beyond busy/quiet: **working / waiting / needs attention / error / quiet**.
"Which of my eighteen agents needs me?" is the question this product exists to
answer. Partly built — the busy pulse and finished-flash landed in 0.2.0. What
is missing is *waiting-on-input* and *error*, which need output-pattern
matching. Must degrade cleanly when a CLI is silent or non-standard.
*Both · a day*

### 4. Hover preview of the last few lines `[3/5]`
On sidebar rows and on tabs. Glance without switching. Named the highest-value
awareness win after the badge, and it costs hours.
*Both · hours*

### 5. Searchable prompt history `[4/5]`
Every prompt sent, per session and globally, fuzzy-searchable, one click to
reuse or edit. The distinction one list drew is worth keeping: **snippets are
for deliberate reuse; history is for accidental reuse.** They do not replace
each other, and snippets already shipped.
*Both · a day*

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
