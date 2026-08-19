# muxpanel — roadmap

Updated 2026-08-19. Three LLMs were asked independently for a feature list
(`docs/feature-research-prompt.md`). What matters is not any one answer but
**where they agreed without seeing each other's work** — that convergence is
the ranking below.

---

## The rule that decides what belongs here

> **Every feature should work from the filesystem, tmux, and process state,
> with optional git detection.**

If a feature needs to know which AI vendor is running, understand its protocol,
or interpret what its model "thinks", that is a strong signal it does not
belong in the core product. This rule is why the working indicator was built
from tmux's activity clock rather than any vendor's API, and it is what keeps
the CLI registry honest.

Second rule, from the same source and just as useful: this is a **driver, not
an IDE**. The job is to make it trivial to jump from an agent to whatever tool
already does the thing well — not to reimplement that tool.

---

## Consensus top five

All three lists independently ranked these highest. Numbers in brackets are how
many of the three named it.

### 1. Command palette + fuzzy quick-switch (3/3) — **shipped in 0.4.0**
`Ctrl`/`Cmd`+`K` for every action, and `Ctrl`+`P`-style jumping between
sessions with most-recently-used first. Numbered tabs work at five sessions and
become friction at thirty. Every list ranked this first or second, and one
observed the real reason: **it stops the UI becoming a collection of buttons**
as features accumulate. Copy VS Code's model outright.
*Both · a day*

### 2. Explicit session attention states (3/3)
Beyond busy/quiet: **working / waiting / needs attention / error / quiet**,
derived from generic terminal and process signals. "Which of my eighteen agents
needs me?" is the question this product exists to answer.
Partly built — the busy pulse and finished-flash landed in 0.2.0. What is
missing is *waiting-on-input* and *error*, which need output-pattern matching.
*Both · more than a day*

### 3. Prompt history, searchable (3/3)
Every prompt sent, per session and globally, fuzzy-searchable, one click to
reuse or edit. The distinction one list drew is exactly right and worth
keeping: **snippets are for deliberate reuse; history is for accidental
reuse.** They do not replace each other.
*Both · a day*

### 4. Unread activity and a "since last viewed" marker (2/3)
A separator in the scrollback showing where you stopped reading, and an unread
mark on sessions that produced output while you were away. Flashing says
*something happened*; this says *what you have not seen*.
*Both · hours*

### 5. Per-session prompt drafts (2/3)
Half-typed instructions survive a tab switch, a reload, and a closed laptop.
Cheap, and it fires dozens of times a day.
*Both · hours*

---

## The rest of the backlog

**Daily friction**
- Scroll lock / follow-mode toggle — one list ranked this #1 outright: if you
  cannot pause a stream you cannot read or copy from it *(developer · hours)*
- Copy last output block, copy visible, copy last N lines *(developer · hours)*
- Jump-to-last-prompt on opening a session *(both · hours)*
- Smart focus: prompt box on tab switch, `Esc` back to the terminal *(both · hours)*
- Drag a path from a file tree into the prompt *(both · a day)*

**Awareness**
- Hover preview of the last few lines, on sidebar rows and tabs *(both · hours)*
- Session status line: elapsed, last activity, cwd, process state, git branch *(both · hours)*
- Per-session CPU/memory share, to catch one agent starving the box *(developer · hours)*
- Activity timeline — jump between notable events in long scrollback *(developer · a day)*
- Turn counter per session *(vibe · hours)*

**Organisation**
- Session templates: CLI + directory + starter prompt + name pattern *(both · a day)*
- Pin / favourite, floating above recency *(both · hours)*
- Recent sessions as a virtual folder *(both · hours)*
- Duplicate / fork a session — same directory, fresh CLI *(developer · hours)*
- Multi-select for bulk archive, move, colour *(both · hours)*
- Per-session notes, a sidecar `.md` *(both · a day)*
- Tags as a second axis alongside folders *(developer · a day)*
- Git branch and dirty state in the sidebar *(developer · a day)*
- Auto-titled sessions from the first prompt *(both · hours)*

**Safety**
- Detach-by-default, kill explicit, with a short undo *(both · hours)* —
  already the model, but the undo is missing
- Confirm-on-close when a session is still producing output *(both · hours)*
- Configurable destructive-command confirmation *(both · a day)*
- Soft interrupt (`Ctrl-C`) that marks the session paused *(both · hours)*
- Export scrollback to timestamped text *(developer · hours)*
- Read-only lock on the prompt box while reviewing *(vibe · hours)*

**Files** — asked for directly, and the one area none of the LLM lists covered
- Drag-and-drop upload into a session's working directory
- Paste text *and images* into the terminal
- Download a file from the session without an scp dance
- A light file browser for the session's directory — "better than a standard
  terminal, especially for vibe coders"

**Delight**
- Zen mode: terminal and a floating prompt, nothing else *(vibe · hours)*
- Editable keyboard map *(both · a day)*
- Folder-level prompt launchers — folders as workspaces, not just containers *(both · a day)*
- Terminal marks you can jump between *(developer · hours)*
- Bring-your-own-key LLM assistant in a right-hand panel *(later, by request)*.
  Note: under the estate rules that gets its own key, a registry entry, and a
  monitor — three things, not one.

---

## Traps — named by more than one list, independently

1. **A built-in diff viewer, file tree editor, or git UI.** All three flagged
   it. It stops being CLI-agnostic, duplicates what the agent already does
   well, and is a large surface that will rot. The boundary that works: make it
   trivial to *jump to* the tool that already does this.
2. **LLM-generated session summaries.** Two flagged it. Scrollback is full of
   ANSI noise, so it is expensive, slow and mediocre — and it breaks the
   vendor-agnostic rule. A deterministic "what happened" view built from
   timestamps, prompts and exit status gets most of the value for nothing. If
   an LLM summary is ever added, it sits on top of that, not instead of it.
3. **Agent orchestration or broadcast-to-many-sessions.** Two flagged it. It
   demos beautifully and then produces four divergent code states you cannot
   reconcile. It also drags in durable job state, which means a database, which
   breaks the first rule. The product should be excellent at *"I have twelve
   agents, help me drive them"* and refuse *"I have twelve agents, decide for
   me."*
4. **Full-text search across all historical scrollback.** One flagged it, and
   it is right for this box: it forces either a database or heavy scanning to
   answer a question asked twice a week.

---

## Done

Command palette on `Ctrl`+`K` — fuzzy, most-recently-used first, every action
in one box · Session engine on its own tmux server · CLI registry with auto-detection ·
folder tree with drag-drop, rename, search, archive, colours · numbered tabs ·
live terminal with scrollback across reattach · per-CLI icons and colours,
four display modes · working pulse and finished flash with optional chime ·
eight themes, light/dark/system, custom CSS in three slots · independent font
sizes · snippets in both input paths · CPU/memory/swap/disk/load with an hour
of history · password login, API tokens, CSRF and throttling · published on the
tailnet under systemd.
