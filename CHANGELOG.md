# Changelog

## 0.50.87 — 2026-08-24 12:37 PDT

**Edit files in place.** Open a text file from a session — click a path, or the
file sheet — and there's now an Edit button: change it and Save writes it back to
disk (Ctrl/Cmd+S saves, Esc backs out). It's held to the same fence as the
viewer: only files inside the session's own directory, never a credential like
`.env` or a key, and only files that already exist — so an edit can't wander off
or overwrite a secret, and it can't create files. A big file that was only shown
in part can't be edited (you'd save back a fraction of it), and the write is
atomic with the file's permissions preserved.

## 0.50.86 — 2026-08-24 12:19 PDT

**Undo a stop.** Stopping a session now leaves a one-click "Undo" in the corner
— start it again right where you were, instead of hunting for the greyed-out
row. (Closing a tab already just detaches and keeps the session running;
stopping is the explicit, confirmed one, and even that keeps the record and its
draft.)

## 0.50.85 — 2026-08-24 12:14 PDT

**Duplicate a session.** A session's menu (right-click, or long-press on a
phone) now has "Duplicate — same directory, fresh CLI": a second, independent
agent on the same work — same folder, same directory, same CLI, its own process.
Handy for running two approaches side by side, or a shell alongside the agent
working the same repo. It shares the source's directory as-is; making a fresh
git worktree is still the separate choice in New Session.

## 0.50.84 — 2026-08-24 12:10 PDT

**A clear × in the session search.** Type to filter the sidebar and a small ×
appears at the right of the box; click it to clear the search and see every
session again. It stays hidden while the box is empty.

## 0.50.83 — 2026-08-24 11:58 PDT

**Sessions name themselves from your first prompt.** Spin up a shell in a
directory and it starts out called "tmp"; send your first real prompt and it
renames itself to what you're actually doing — so a tray of "tmp" and "shell"
becomes the work in front of you. It only ever fills in a name that was
auto-generated: the moment you name a session yourself, this leaves it alone,
and a one-word "y" won't trigger it.

## 0.50.82 — 2026-08-24 11:42 PDT

**A confirm before a command that looks destructive.** Send or broadcast
something matching a short list of catastrophic patterns — `rm -rf /`, `mkfs`, a
force-push, `drop database` — and CLIque asks once before it goes, showing you
the exact command. It's a guard against a fat-fingered slip, not a block: the
pane still has a shell, and everyday `rm -rf ./build` is left alone on purpose,
because a guard that cries wolf gets switched off. The pattern list, and the
whole feature, are yours to edit or turn off in Settings → Notifications →
Command safety.

## 0.50.81 — 2026-08-24 11:18 PDT

**Lock a session read-only while you read it.** Reviewing a pane on a phone —
scrolling its output, resting a thumb on the glass — could send a stray
keystroke straight into the agent. A new lock (the lock button beside Run, or
the session menu) holds back *all* input to that session: the prompt, Run and
Shell, the on-screen keys, and live terminal typing. The tab shows a small lock,
the terminal wears a "read-only" tag so a swallowed keystroke says why, and it
is per-session and remembered across reloads — unlock when you want to type
again.

## 0.50.80 — 2026-08-24 11:01 PDT

**Each session gets its own hook token.** The token a state hook uses to report
"waiting" / "error" used to be one shared secret sitting in every session's
environment — so a token read out of one agent's pane could nudge *any* other
session's status. Now every session is handed its own, bound to its id: it can
report only its own state, is re-minted when the session resumes, and is revoked
outright when the session is deleted. A token exfiltrated from one pane can no
longer speak for another, and it dies with the session that held it.

## 0.50.79 — 2026-08-24 10:47 PDT

**A file preview never hands back a secret.** The click-to-preview that reads a
path an agent printed already stayed inside the session's directory; now the
credential block stands on its own — enforced whether or not that directory
fence is on — and it covers what actually sits on a dev box: `.env` and its
variants, private keys (`.pem`/`.key`/`.p12`), token files like `.npmrc`,
`.pypirc` and `.bw-session`, and whole credential folders (`.ssh`, `.aws`, …).
Containment now resolves symlinks before deciding, so a link inside the
directory can't point out of it to escape the fence.

## 0.50.78 — 2026-08-24 10:32 PDT

**A stopped-on-error session now stands out.** A session waiting on you already
turns its name bold; a session that stopped on an error wore only the small ring
on its icon — so the one state you most want to catch was the quietest thing in
the list. Now an errored session shows a bold name in the "something broke"
colour, in both its tab and the sidebar, exactly as loud as one asking a
question.

## 0.50.77 — 2026-08-23 16:13 PDT

**Bring your own model keys.** A new **Models** tab in Settings lets you add any
OpenAI-compatible provider (OpenRouter, Groq, Together, a local Ollama) or the
Anthropic API, give it a model, and test the key on the spot. This is the
foundation the coming assistant features (an intelligent inbox, a chat skin, a
docked copilot) will run on — but the key handling is worth shipping on its own,
because it's built to a real bar:

- **Encrypted at rest.** Each key is encrypted with AES-256-GCM before it
  touches disk; the data key lives in a separate `0600` file, so a copy of
  `state.json` — from a backup or a sync — is ciphertext, not a key.
- **Never leaves the box, never comes back.** The key is never returned to the
  browser (the panel only tells you whether one is *set*), never logged, and
  never rides the status poll.
- **Can't be pointed anywhere dangerous.** Before a prompt and a key go out, the
  destination is checked: a cloud-metadata or internal-network address is
  refused, so a mistyped or hostile endpoint can't turn the panel into a proxy.
  Local models on loopback still work.

The encryption comes from one optional extra — `pip install 'clique-panel[llm]'`
— so the base install stays 100% dependency-free; without it, the panel refuses
to store a key rather than write it in the clear.

## 0.50.76 — 2026-08-23 15:36 PDT

**A heads-up when the box is getting full — never a blocker.** CLIque already
watches CPU, memory, swap and load; now it weighs them against how many agent
sessions are actually live and, when the box is genuinely stretched, drops a
quiet, dismissible strip at the top: *"12 sessions, ~1.2 GB free — heavy for
this box. Idle ones auto-reap in 6h."* It fires on any of the real signals —
available RAM near the floor, swap climbing, load sustained above the cores, or
the session count past a soft ceiling worked out from this box's RAM and the
**measured** cost of the sessions it's actually running (not a fixed guess).
The New-Session form shows that ceiling when you're near it ("this box
comfortably runs ~8 agents"), and Start is never disabled — the guard warns and
gets out of the way. It ties into the levers that already exist: idle sessions
auto-reap, and Reclaim is offered right there when there's something leaked to
reclaim. All of this is the read-only sampling the status bar already does, so
it costs nothing and nothing leaves the box.

## 0.50.75 — 2026-08-23 14:11 PDT

**A second window opens clean, and closing one collects its tabs.** Opening the
panel in a second window on the same computer used to clone the first window's
strip. Now, when another window is already open, a fresh one starts empty — a
second screen is a fresh desk — and you move over just the sessions you want.
Close a window and its open tabs are handed to the remaining (primary) window
rather than dropped, on a short grace so a reload is never mistaken for a close.
Each window still remembers its own strip across a reload, and only the primary
window writes the shared workspace, so a clean window never wipes the seed a
fresh single window restores from.

## 0.50.74 — 2026-08-23 13:59 PDT

**Move a session to another browser window.** Open the panel in two windows —
one per screen — and they find each other over a BroadcastChannel (no server,
nothing over the wire). A "Move to window …" item on any session's menu (its gear
or a right-click) hands the session to the other window, which flashes its edges
so you can see where it landed; the source lets go of the tab. A small "⧉ N" chip
in the header names which window you're looking at, and each window remembers its
own strip of tabs across a reload. Same-browser for now — cross-*device* (phone
and desktop) would need a server relay.

## 0.50.73 — 2026-08-23 09:28 PDT

**Sharper "needs you" detection for every CLI.** The generic waiting patterns —
the ones that surface a prompt for a CLI CLIque knows nothing about (Codex,
Gemini, Cursor and the rest) — now match case-insensitively, so a default-capital
`(Y/n)` or `[y/N]` is caught; recognise a selection menu drawn with pointers
other than `❯`; and pick up an "(Use arrow keys)" menu hint. New tests lock in
both the prompts that must fire and the finished-turn output that must stay
silent, so the inbox does not cry wolf. ("Working" was already CLI-agnostic — it
reads the activity clock and content, not any vendor.)

## 0.50.72 — 2026-08-23 09:12 PDT

**A finished turn no longer reads as "needs an answer."** Claude Code's `Stop`
hook fires at the end of *every* turn, and it was reporting `waiting` — the same
signal a real question raises — so an autonomous session that had merely finished
a turn showed up in the inbox as needing you. `Stop` now clears instead: a
genuine wait still surfaces, from the idle Notification (~60s of no input) or a
permission prompt, and a finished-but-unopened turn shows as "finished — not
opened yet" rather than "needs an answer".

## 0.50.71 — 2026-08-23 08:56 PDT

**Broadcast to a chosen set, not just everyone.** The broadcast sheet now lists
the live sessions as a checklist grouped by folder — tick "All live sessions", a
whole folder, or any handful, and the count and the Send button follow the
selection. A new optional `ids` on `POST /api/broadcast` restricts the send to
an explicit list of session ids; `folder` and all-sessions still work as before.

## 0.50.70 — 2026-08-23 08:44 PDT

**A leaner sidebar header.** The top of the sidebar had grown to eight controls.
New folder, adopt, broadcast and the board now sit behind a single "⋯" overflow
menu, leaving the bell, the running-only filter, that menu, settings and the
collapse chevron on the bar. Nothing is lost — the occasional actions are one
tap deeper.

## 0.50.69 — 2026-08-23 08:31 PDT

**Scope the read surface — the state-hook token can no longer read the panel.**
The `attention`-scoped token handed to every session (so a state hook can report
its own status) was accepted across the whole read surface: `GET /api/*` and the
`/ws` attach checked only that *a* token was valid, never its scope. An agent
that read its own `$CLIQUE_TOKEN` — including a prompt-injected one — could with
it stream another session's live terminal, read every past prompt and
transcript, and read arbitrary host files. Reads now require a `read` scope (a
cookie counts); that token reaches only `POST …/attention`, the one thing it is
for. Nothing legitimate read with it. Proven by `tools/scope_check.py`.

Also hardened: file previews are now fenced to the session's working directory
by default (credential files blocked; opt out with `CLIQUE_FENCE_READS=0`);
`webhook_url` is withheld from API tokens, since its path can itself be a
credential, while the cookie operator still sees it; `git worktree` and `tmux
send-keys` gain `--` guards so a `-`-leading branch or key cannot inject a flag;
the session cookie is `Secure` whenever the request arrived over HTTPS, even
without `CLIQUE_TRUST_PROXY`; and a 500 no longer echoes internal paths.

**Modal sheets render as modals again.** The board, broadcast, inbox and
review-changes sheets shipped without their overlay rule and drew as unstyled
full-width strips at the foot of the page — the board fell entirely below the
fold, so its button looked dead. They now open centred over a backdrop like
every other dialog, guarded by `tools/overlays_check.py`.

## 0.50.68 — 2026-08-22 21:54 PDT

**Start a fleet in one click.** The new-session form takes a "How many" count now
— up to 20 — and spins them all up at once, each with a numbered name. Turn on a
worktree and each gets its own branch (`<branch>-1`, `-2`, …), so a fleet of
agents can work the same repo without treading on each other's files. New
endpoint `POST /api/sessions/spawn`, reusing the ordinary per-session create;
one that fails to start is reported, not fatal to the rest.

## 0.50.67 — 2026-08-22 21:41 PDT

**A board of the whole fleet.** A new grid button opens every session as a card
in a column for what it is doing — Working, Needs you, Idle, Stopped — filled
from the same authoritative status the sidebar ring reads, so a card moves the
moment a session's state does, and the poll re-ranks it live while the board is
open. Tap a card to jump to that session. The whole-fleet glance the
one-per-line sidebar is not.

## 0.50.66 — 2026-08-22 21:33 PDT

**See what a session has spent.** A "Usage" action on any session that keeps a
transcript (Claude) reads its own log and totals the tokens — input, output, and
the two cache figures — across every assistant message, shown as a monospace
breakdown. Read on demand, never on the poll, via a new
`GET /api/sessions/<id>/usage`. A session with nothing logged yet, or a CLI that
keeps no usage, says so plainly.

## 0.50.65 — 2026-08-22 21:24 PDT

**The inbox tells "wants approval" apart from "asked you something".** A Claude
Code permission prompt and an open question both mean "needs you", but they want
different answers — so the state hooks now report which (the Notification
matcher: permission_prompt vs idle_prompt), carried through the attention
endpoint as an optional `note`. A permission prompt in the inbox gets one-tap
**Approve / Deny** (Enter accepts the highlighted default, Escape cancels); a
question keeps the reply box. Everything else is unchanged.

## 0.50.64 — 2026-08-22 21:14 PDT

**Broadcast: one message to every session at once.** The clearest form of "one
cockpit driving many" — a megaphone in the header opens a composer, you type an
instruction (or leave it empty to send a bare Enter, a "carry on" to all), pick
all sessions or one folder, and it is typed into every live session in one go.
It shows how many it will reach before you send. New endpoint `POST
/api/broadcast` (write-scoped), reusing the existing per-session send path; dead
sessions are skipped and one session failing does not sink the rest.

## 0.50.63 — 2026-08-22 20:33 PDT

**Security hardening of the new state hooks and review diff, and bell polish.**
An independent audit of 0.50.60–0.50.62 turned up one issue that mattered and
several worth fixing:

- **The state-hook token is now `attention`-scoped, not `write`.** It lives in
  every session's environment, and a `write` token there was a real hole — an
  agent, or a prompt-injected one, could read it and spawn a shell or drive
  another session. The new scope permits only the status nudge to the attention
  endpoint; verified that `/send` and `/api/sessions` refuse it.
- **The review diff is bounded and complete.** Total size is capped so a giant
  file cannot freeze the page; new files inside a brand-new directory now show
  (via `git ls-files -z`, which also fixes spaced and unicode names); and a repo
  with no commit yet shows its first staged work.
- **`hook.token` is written 0600 at creation**, not chmod-ed a beat later.
- **"Review changes" shows for a dirty detached HEAD**, not only a named branch.

The bell also gets a touch more space from the filter button and now rings for a
second every ~16s while something is waiting — quiet under reduced-motion.

## 0.50.62 — 2026-08-22 20:13 PDT

**Review what an agent changed, and comment straight back to it.** A session in a
git repo gets a "Review changes" action that shows its uncommitted diff — tracked
edits and the new files it wrote, added and removed lines coloured — and a
comment box that sends what you type as the agent's next message. The review note
and the follow-up are the same message: read the change, say what to fix, done.
No new write-side endpoints; it is git's own diff and the send path that already
existed. Also fixes a long-standing crash in the hover-to-peek handler — a null
dereference when the mouse left empty sidebar space with nothing being peeked.

## 0.50.61 — 2026-08-22 19:57 PDT

**A "needs you" inbox — see who is waiting, and answer from anywhere.** A bell in
the header carries a count of the sessions waiting on you (a question, an error,
or one that just finished), and the browser tab title carries it too — "(3)
CLIque" — so a backgrounded tab says it at a glance. Open the bell for the list,
most-urgent first, and answer right there without opening the pane: type a reply,
or send it empty to accept the highlighted default (which is how a Claude Code
permission prompt says yes). It is one sheet, touch-sized, so the same answer
works from a phone. Built on the authoritative state from 0.50.60 and the send
path that already existed — no new endpoints.

## 0.50.60 — 2026-08-22 19:42 PDT

**Claude Code sessions report their own state now, instead of being guessed at.**
CLIque could not always tell "waiting for you" from "running a slow test" — it
read state from output activity and prompt patterns, and an agent redrawing a
spinner kept the clock looking busy. A Claude Code session now launches with a
`--settings` block whose hooks POST its real state to CLIque: Notification and
Stop mean it yielded and is waiting on you; UserPromptSubmit means you answered
and it is working again. That authoritative signal outranks the activity guess
wherever the state is formed — the sidebar ring and the `/wait` endpoint are
exact, not fuzzy. Nothing to install: the hooks ride in per session via
`--settings`, so they touch neither your global Claude config nor your repo, and
they no-op for any session CLIque did not launch. Other CLIs keep the
pattern-and-activity fallback; `hooks = true` in clis.toml opts a CLI in.

## 0.50.59 — 2026-08-22 19:06 PDT

**GPU rendering stays on by default, and now knows when to bow out.** WebGL
(0.50.58) is a real, felt speed-up, so it stays the default — but a browser
allows only ~16 live GPU contexts, and CLIque is built to hold many panes. Past
that the browser drops the oldest pane's context; that pane, always a
background one, now falls back to canvas on the spot, so nothing you are looking
at ever breaks. If those losses keep happening — a weak GPU, a driver that
resets, far more panes than the cap — CLIque offers, once, to turn the GPU off
for steadier canvas drawing. A **per-device toggle** in Settings › Rendering
does the same by hand (per device, because a desktop has a GPU an old phone may
not, and the choice should not follow you across machines). The frame-batching
from 0.50.58 — one terminal write per animation frame, not one per network
packet — is unchanged.

## 0.50.58 — 2026-08-22 18:06 PDT

**Smoother terminals: GPU rendering, and output batched to the frame.** Three
changes to how panes draw, each felt most with many live sessions at once.

- **A WebGL renderer, with a canvas-then-DOM fallback.** The cell grid now
  repaints on the client's GPU wherever the browser offers a WebGL2 context —
  far less CPU than the canvas renderer with a dozen panes open. A phone, a VM
  or a remote context that refuses or loses the GPU drops to canvas, and then to
  the built-in DOM renderer; WebGL is used where it works and never required.
- **One write per frame, not one per network packet.** A flood of output — a
  `yes` loop, a giant diff, a screen cleared and redrawn — used to mean hundreds
  of parser-and-render passes a second. The bytes are now coalesced and handed
  to the terminal once per animation frame: same output, a fraction of the work,
  and the scroll jank goes with it.
- **Off-screen transcript turns are skipped.** A long transcript only lays out
  and paints the turns in view (`content-visibility`), so opening a big one is
  quick no matter its length.

All safe because closing a tab now disposes the renderer before the terminal
(0.50.57) — the same fix that made it possible to turn WebGL back on.

## 0.50.57 — 2026-08-22 16:59 PDT

**Closing a tab now really closes it, and Kill really kills.** The renderer addon
threw while a terminal was being disposed -- it tried to restore the DOM renderer
against an already-torn-down linkifier -- and that exception aborted closeTab
half-way: the tab stayed on screen, and worse, the kill request that runs right
after it never fired, so the session lived on. closeTab now disposes the renderer
while the terminal is still whole, then tears the terminal down under guards, so
nothing a renderer does can stop a tab from closing. Kill from the tab gear and
Kill from the sidebar right-click both go through this, and both are now reliable.

## 0.50.56 — 2026-08-22 16:30 PDT

**Kill session now actually kills it, every time.** Stopping a session used to
report success unconditionally, even when a tmux hiccup or an adopted session on
a foreign prefix meant the process never died -- so the tab closed and a live
session was left behind. It now kills, verifies, force-kills anything that
survived, and reports the real outcome; the browser surfaces a retry if it ever
truly will not stop. Both the tab's gear menu and the sidebar right-click use
this path, so "Kill session" is now reliable from either one.

## 0.50.55 — 2026-08-22 16:03 PDT

**The leaked-sessions warning is now hard to miss.** It was a correct but quiet
line at the foot of the sidebar, easy to scroll past. Now it is a warn-tinted
bar with a warning mark, a bold count, and a filled Reclaim button, pinned where
it always was but loud enough to notice. Nothing changed about when it appears:
only while there is leaked memory to reclaim, and gone the moment you reclaim it.

## 0.50.54 — 2026-08-22 11:06 PDT

**Google Antigravity is a first-class CLI now.** Its `agy` command is recognised,
selectable in the New Session dialog, and carries its own tinted mark — a session
started as Antigravity is no longer an unlabelled pane. It draws its own screen,
so the alternate-screen scroll fix applies to it as well. Purely config plus an
icon, the way adding a CLI is meant to be.

## 0.50.53 — 2026-08-22 10:58 PDT

**Scrolling works in every CLI now, including Claude and Grok.** A full-screen
app on the alternate screen keeps no terminal scrollback — it owns its own view
— so the wheel was being swallowed into a buffer tmux only ever redraws in
place, and scrolling did nothing. The panel now knows, per session, whether the
pane is on the alternate screen (`alt`), and forwards the wheel to the app as
mouse-wheel events there: it scrolls its own conversation and the redraw streams
back. A shell or any normal-screen output still scrolls the pane's own 20k-line
scrollback exactly as before.

## 0.50.52 — 2026-08-22 10:41 PDT

**Drive CLIque from an agent.** Every session now reports a one-word `state` —
`working`, `waiting`, `error`, `idle`, or `stopped` — and a new
`GET /api/sessions/<id>/wait?for=idle,waiting` blocks until it reaches one of
those or times out, so an agent can start work and wait for it to finish or come
back asking instead of polling the whole panel. With that plus the worktree
option, an agent can run one task across many repos in parallel and collect the
results. The workflow — mint a token, start sessions, send, wait, read — is
written up as a skill in `skills/drive-clique/SKILL.md`.

## 0.50.51 — 2026-08-22 10:32 PDT

**Run an agent in its own git worktree.** Starting a session in a repo now offers
"Run in a new git worktree": CLIque makes an isolated checkout on a fresh branch
and runs the agent there, so several agents can work the same repo at once without
touching each other's files — no more two sessions clobbering one working tree.
Delete the session and the worktree goes with it, but only when it is clean; a
worktree with uncommitted work is left alone so nothing is lost. Scriptable too —
`POST /api/sessions` takes `worktree` and `branch`.

## 0.50.50 — 2026-08-22 09:06 PDT

**Filter the sidebar to just what is running.** A funnel button at the top of
the sidebar hides every stopped session — and any folder left empty by that —
so a list of two dozen collapses to the handful you are actually working in. It
lights up while it is on, and the choice is remembered on this device (like the
sidebar's width and whether it is shown), so it survives a reload without
following you to your phone.

## 0.50.49 — 2026-08-21 20:58 PDT

**Read back a conversation that scrolled away.** Claude draws over the terminal's
alternate screen, so once its output scrolls off it is gone from the pane — but
every turn is on disk. A new "View conversation" on a Claude session's menu opens
its transcript in the file sheet: user prompts and the assistant's prose, oldest
first, the thinking and tool calls left out. Read from a bounded tail of the
transcript, never the whole file, and keyed off the session id Claude was launched
with, so it is always the right conversation. Offered only where there is a full
transcript to show.

## 0.50.48 — 2026-08-21 20:46 PDT

**Leaked sessions are visible and reclaimable.** A tmux session left running
with no record behind it — the record removed without stopping its process —
used to hold its memory invisibly, off the list and past any control. The
sidebar now shows a quiet line when any exist: how many, how much memory, and a
Reclaim button that stops them. Detection skips anything younger than the grace
window, so a session mid-creation is never mistaken for a leak, and a mux that
belongs to a real record is never killed. Completes the memory trio with
per-tab RSS and idle reaping.

## 0.50.47 — 2026-08-21 20:36 PDT

**A directory can no longer be mistaken for the state file.** `Store` now refuses
a path that is a directory instead of quietly renaming it: the atomic save writes
`state.json.bak` beside `state.json`, and pointed at `$CLIQUE_HOME` rather than
`$CLIQUE_HOME/state.json` that rename moved the whole home aside. A caller mistake
now raises on the spot rather than displacing a workspace.

## 0.50.46 — 2026-08-21 20:28 PDT

**Idle sessions reap themselves and resume on click.** A tab left untouched for
six hours has its process stopped — freeing its memory, ~700 MB for an idle
Claude — and its tab greys out. Click it and the session comes back exactly
where it was: the state was on disk the whole time, so nothing is lost. Ten open
tabs now cost what two do. Only a session that can actually be resumed, that no
browser is attached to, and that is not mid-task is ever touched; a shell, which
has nothing to resume, is left alone. The window is the `reap_idle_hours`
setting — set it to `0` to switch reaping off.

## 0.50.45 — 2026-08-21 20:11 PDT

**Every session shows its memory in the sidebar.** A small dim figure on each
row — the resident memory of the CLI and everything it spawned, from one cached
/proc walk for the whole list. It turns "which tab is eating the box" from a
guess into a number: a stack of idle Claude tabs at ~700 MB each is suddenly
visible. Groundwork for reaping the idle ones.

## 0.50.44 — 2026-08-21 19:52 PDT

**Host header with a `userinfo@` part is refused.** `host_allowed` took the
text after an `@`, so `Host: evil.com@127.0.0.1` read as the safe `127.0.0.1`.
Browsers never send that, so any `@` in a Host is now a rejection. Closes the
last of the three-model security review; what remains is defense-in-depth for
exposed self-hosting (a WS-aware slow-header timeout), not a reachable bug.

## 0.50.43 — 2026-08-21 19:50 PDT

**An on-screen key row on the phone.** A soft keyboard cannot send Esc, Tab,
Ctrl+C or the arrow keys a TUI lives on — so a row of them now sits above the
input on a narrow screen, tapping each straight into the pane in front. It
shows only with a session up, only on a touch layout, and scrolls if it runs
out of room. That was the last thing between the phone build and actually
driving an agent from it.

## 0.50.42 — 2026-08-21 19:43 PDT

**Two more from the sweep.** Static files are served with a real containment
check instead of a string prefix, so a sibling directory named `web-anything`
cannot slip through. And webhook deliveries run on a small fixed worker pool
behind a bounded queue rather than a thread per call — so a caller hammering
the test endpoint cannot pile up daemon threads; a full queue drops the
delivery, which the notification model already allows.

## 0.50.41 — 2026-08-21 19:41 PDT

**`X-Forwarded-*` is believed only behind a trusted proxy now.** Host and
scheme were taken from `X-Forwarded-Host`/`X-Forwarded-Proto` no matter who
sent them, so any client could set `X-Forwarded-Host` and walk past the
DNS-rebinding gate that runs on `Host`. They are honored only when
`CLIQUE_TRUST_PROXY=1` declares a proxy sets them; otherwise `Host` is the
truth. This deployment runs behind tailscale, so the flag is set here.

## 0.50.40 — 2026-08-21 19:37 PDT

**Killing a session from the tab closes it cleanly, right away.** Kill closed
the tab silently and then waited on the request before repainting — so the
dead tab and a blank pane hung on screen until the kill came back, and whether
a poll fired in between is what made it feel hit-or-miss. It now closes the
tab, selects the next one, and repaints immediately, then kills in the
background. Same for removing a stopped session.

## 0.50.39 — 2026-08-21 19:06 PDT

**The same-origin check compares the port too, and understands IPv6.** It split
the host on ":" and dropped the port, so a page on another port of the same
host — `http://127.0.0.1:9999` against the panel on `:3200` — counted as the
panel's own origin and could drive it (the CSWSH the check exists to stop),
and IPv6 literals were mangled to `[`. It now parses the origin and compares
host and port, leaving scheme out so a tunnelled panel is never locked out of
itself. A `CLIQUE_ALLOWED_HOSTS` name is still trusted as an origin, as before.

## 0.50.38 — 2026-08-21 18:54 PDT

**A fenced read mode for public deployments.** Set `CLIQUE_FENCE_READS=1` and
the file glance can no longer read outside a session's working directory, and
refuses obvious credential files (`id_rsa`, `.env`, `.aws/`, and the like) even
inside it. Default deployments are unchanged — the trusted-local model still
lets an authenticated user read any path, because they already have a shell as
this user. This is a self-hosting gate: the flag is what makes read tokens safe
to hand out on an exposed instance.

## 0.50.37 — 2026-08-21 18:42 PDT

**Revoking an API token now sticks.** `last_used` was written back to disk on
the request path, so a `token revoke` from the CLI could be undone the instant
the running server persisted its stale in-memory list over it. The timestamp
is now kept in memory and flushed on shutdown only; the file is written on
mint, revoke, and exit — the three moments that change which tokens exist. A
revoke is durable.

## 0.50.36 — 2026-08-21 18:37 PDT

**Security pass, part one.** Six fixes from the three-model review: `PATCH
/api/settings` no longer returns the webhook secret in its response; the file
glance reads only the 256 KB it shows instead of loading the whole file into
memory; a negative `Content-Length` can no longer hang a request thread on a
read-to-EOF; drafts and names are capped so they cannot bloat the state poll;
the terminal filter bounds an unterminated control sequence instead of
buffering it without limit; and the CSP allows WebSockets only to the panel's
own origin. None of these were reachable in the single-user loopback model
without a credential — all of them matter the moment CLIque is self-hosted.

## 0.50.35 — 2026-08-21 17:57 PDT

**Closing and reopening the sidebar no longer leaves the pane the wrong size.**
The toggle refit the terminal at the instant of the change — before the layout
had settled — and never told tmux the new width, so a boxed CLI came back scaled
against the old size, with a stray scrollbar and dead space beside it. It now
refits on the next frame and again once settled, and pushes the new width
through to tmux.

## 0.50.34 — 2026-08-21 17:47 PDT

**The wheel scrolls the pane's history again, even when the CLI grabs the
mouse.** Claude, Grok and anything else that turns on mouse tracking was being
handed every wheel tick by the terminal, so scrolling up did nothing and
output that went off the top was unreachable — on a panel built for watching
output, the one thing you needed. The wheel now scrolls the pane's own 20,000
lines of scrollback instead, and scrolling up pauses following so the view
holds. No Shift required; non-mouse-mode CLIs are unchanged.

## 0.50.33 — 2026-08-21 17:38 PDT

**First pass at a phone layout.** On a narrow screen the sidebar is now an
overlay drawer over a full-width pane, not a column fighting the terminal for
room: the drag-resizer steps aside, a tap on the rail slides the drawer in over
a dimmed pane, and opening a session slides it back out. The rest of the phone
work — an on-screen key row, the input bar's spacing — is still to come; this is
the layout it hangs on.

## 0.50.32 — 2026-08-21 17:17 PDT

**Pin a session and it floats to the top of its group.** Right-click or
long-press a session and "Pin to top" keeps it above the newer ones that
would otherwise push it down — the handful you keep coming back to, marked
with a star and held above recency, in the sidebar and inside its folder.
Unpin drops it back into the flow.

## 0.50.31 — 2026-08-21 16:49 PDT

**Search everything you have typed, and send it again.** The command palette
has a prompt mode — the "Reuse a past prompt" command, or a `"` to open
straight into it — that fuzzy-searches every prompt you have sent, per project
and across all of them, newest first. Click one and it drops into the box to
edit or send; a terminal-mode CLI gets it in its own input, no newline. Nothing
is logged twice: the prompts come from the CLIs' own history — a prompt-log read
whole, a transcript from a bounded tail, so a thirty-megabyte one is never
walked.

## 0.50.30 — 2026-08-21 16:34 PDT

**Changing the terminal font now resizes the CLI to match.** The bottom-right
font stepper (and the settings slider) resized the grid drawn in the pane but
never told tmux, so the CLI kept wrapping at the old width — dead space, and a
pane that looked a different size from one tab to the next. The new size is
pushed to tmux on the change now, the same as a window resize already was.

## 0.50.29 — 2026-08-21 15:08 PDT

**Copy the last lines without dragging across them.** Selection and the
visible screen already copied; the palette now carries "Copy the last 50
lines" — the recent output, scrollback and all, ending where the pane last
wrote, wherever the view is parked. It counts lines rather than guessing
where a reply began: CLIque does not read what the CLI said, so a fixed
window is the honest unit.

## 0.50.28 — 2026-08-21 15:05 PDT

**Switching to a tab lands you in the prompt box; Escape hands the pane
back.** A switch used to drop the cursor into the terminal, so typing a
prompt meant clicking the box first. Now the box takes focus on a switch,
ready for the next prompt — and Escape from the box moves focus to the pane,
to scroll it or type into the CLI's own input. Terminal-mode CLIs, which own
their input, keep the pane focused as before, and a phone is left alone so
the on-screen keyboard does not spring up on every tab.

## 0.50.27 — 2026-08-21 14:56 PDT

**Hover a row or tab and the tooltip shows the last few lines.** A status
ring tells you a session changed; it does not tell you what it said. Rest on
the row — in the sidebar or on the tab bar — and the native tooltip now
carries the last few content lines of the pane, captured only when you stop
there, so a quiet sidebar still costs nothing. No popup: the browser draws
the tooltip, which is why it cannot cover a menu or eat a click. Touch, which
has no hover, keeps its long-press menu.

## 0.50.26 — 2026-08-21 14:34 PDT

**Stopping a busy session now says what you would interrupt.** Kill asked a
flat "Stop this?" whether the session was idle or an agent was mid-task with
an unsent prompt still in the box — the same reflex click either way. It now
names what is live: *"X is still working and has an unsent draft — stop it
anyway?"* Nothing is lost regardless — the session stays in its folder, draft
and all, and starts again — but the question is no longer one you answer
without looking.

## 0.50.25 — 2026-08-21 09:39 PDT

**A login link that wraps is still one link.** Codex (and anything else)
prints a URL that used to split across lines, so a click only got the
first half. The pane now joins those lines. If the login tries to come
back to localhost on this box while you are on another machine, clicking
it backs out of that flow — pick **Sign in with Device Code**, which is
the one that actually works from here.

## 0.50.24 — 2026-08-21 09:30 PDT

**What's new sits on the bottom bar.** After an upgrade, a mark appears
next to the stats. Click it and you are in Settings → Changelog. The
sheet itself holds the last five releases; the rest is a link to the
file on GitHub. Opening the notes clears the mark.

## 0.50.23 — 2026-08-20 21:08 PDT

**Restarting the panel now updates tmux's own settings.** tmux is supposed
to outlive the panel, so a new history length used to stay stuck at
whatever it was when tmux first started. Existing windows also get their
size lock put back, so an old session cannot start autosizing again.

## 0.50.22 — 2026-08-20 21:05 PDT

**Shrinking the window no longer stacks Gemini's box on itself.** Claude,
Grok and Gemini were being told the pane got narrower, so they wrapped
their old chrome into extra copies. The pane now zooms to fit instead —
same conversation, whole thing still on screen. A phone still resizes
for real, because zooming that far would make the type too small.

**Switching tabs no longer blanks the pane.** Hidden terminals stay
painted, just not in front, so Chrome cannot throw the picture away.

**Keys typed while it says Reconnecting… land when it is back**, instead
of vanishing.

## 0.50.21 — 2026-08-20 19:00 PDT

**Copy from a Claude, Grok, or Gemini pane works like selecting on a page.**
Those CLIs were telling the browser “the mouse is mine,” so a drag never
became a selection. The browser no longer hears that. A click still goes
to the CLI. After you copy, the highlight clears so the next Ctrl+C is
interrupt. Ctrl+Shift+C copies whatever is on the screen if nothing is
selected. The command palette can do that too.

## 0.50.20 — 2026-08-20 18:35 PDT

**Drag across the pane to copy.** A CLI that is listening for clicks used
to swallow the drag, so there was nothing to copy — and on a Mac there
was no modifier that would get it back. A drag is a selection now, and
letting go puts it on the clipboard. A click still goes to the CLI.
Ctrl+C and the Copy button still work.

## 0.50.19 — 2026-08-20 18:24 PDT

**Opening a smoke test can no longer take over the window you are in.**
The checks used to talk to this panel and the same tmux server, so a
`/tmp` shell could land on the tab you were looking at. They now run
on their own panel, their own state, and their own tmux socket, and
they cannot see this one.

## 0.50.18 — 2026-08-20 18:13 PDT

**Its own window, no browser around it.** Install as an app — from
Settings → About, the command palette, or the browser’s own Install.
Full screen is the button next to the shortcuts, or Ctrl/Shift+F.
Not Electron: the panel is still 24 MB, and a restart still does not
kill your sessions.

## 0.50.17 — 2026-08-20 18:07 PDT

**Select text in the pane and Ctrl/Cmd+C copies it.** Every CLI, every
tab. With nothing selected, Ctrl+C still interrupts. A Copy button
appears while something is selected, so a phone can do it too.
Right-click on a selection copies. A pane redraw no longer throws the
selection away the moment you go to copy it.

## 0.50.16 — 2026-08-20 18:01 PDT

**The pane you are looking at stays the pane you are looking at.** A
tab warming in the background used to refit this one and leave it
blank — Chrome had thrown the hidden canvas away, and a same-size
fit did nothing. Coming back, or switching tabs, now redraws it.
A full refresh should no longer be the fix.

## 0.50.15 — 2026-08-20 17:52 PDT

**The pane keeps the size of the window you are looking at.** tmux no
longer resizes itself when a background tab reconnects, a second window
opens, or the panel restarts while you are elsewhere. Coming back fills
it. A collapsed measure cannot shrink it to a screen of dots.

## 0.50.14 — 2026-08-20 17:21 PDT

**Each row names the git branch it is on.** And how many files have
changed, when that is not zero. The folder tree was already many repos
and said so nowhere. A directory that is not a repo looks the way it
always did.

## 0.50.13 — 2026-08-20 17:01 PDT

**Coming back fills the pane again.** A restart — or a tab reconnecting
in the background — could leave tmux smaller than this window. Coming
back kept the dots, because the panel thought the sizes already matched.
It now takes this window’s size as soon as you look, and a background
reconnect no longer shrinks the one you had.

## 0.50.12 — 2026-08-20 16:42 PDT

**A path in the pane is a click.** Same as a URL: click it to look,
copy it, or drop it into the prompt. Read-only — this is not an
editor. Ctrl/Cmd+click skips the look and drops the path where you
are typing.

## 0.50.11 — 2026-08-20 16:26 PDT

**The tabs you left are the tabs you get.** Opening the panel at home
used to save an empty strip over the one from work — the first load
had not finished reading yet, and a closing window often killed the
save. It now waits until the workspace is actually back, and a closing
window uses a request that survives the tab dying.

## 0.50.10 — 2026-08-20 16:13 PDT

**Folders stay folders when you open the panel on another computer.** A
session you had filed was being pulled up into Running whenever it had
no tab open, so a fresh browser looked like nothing was organised. The
filing was saved. The tree was hiding it. Running is only the unfiled
inbox now.

## 0.50.9 — 2026-08-20 14:52 PDT

**Killing a session no longer deletes it from the folder.** It stops the
CLI and leaves the row. Click it to start again — Claude and anything
else launched with our session id come back with their own conversation.
A shell starts again in the same place. Remove it from the sidebar is a
separate choice, on a session that is already stopped.

## 0.50.8 — 2026-08-20 14:47 PDT

**Pick a terminal font, and change its size from the bottom-right.** Five
monospace stacks that exist on Windows, Mac and Linux — a missing font
falls back instead of going proportional. `+` and `−` next to the
shortcuts change the size live. Both live on the server, so a reload and
another device keep them.

## 0.50.7 — 2026-08-20 14:24 PDT

**The spinner only turns while something is actually working.** A CLI sitting
on a question used to keep spinning, because the prompt still blinked and
that looked like output. That is not work — work is paused, waiting on you.

Three marks, three facts:

- **spinning** — it is working
- **slow pulse** — it finished, and you have not opened that tab
- **two knocks** — it is asking a question (y/n, Do you want, a numbered
  choice). The same marks work for every CLI, not just Claude and Grok.

## 0.50.6 — 2026-08-20 14:17 PDT

**Coming back to the tab grows the pane again.** Leaving CLIque in a
background browser tab let something else shrink the window. Coming back
kept the dots — the page was not counted as focused yet, so it stayed
quiet. It now takes the size that fits this window as soon as you look
at it.

## 0.50.5 — 2026-08-20 14:13 PDT

**Clicking a tab no longer flashes someone else's pane.** A tab that had
not warmed yet kept showing the one you were on until its terminal caught
up — and if you clicked away in that wait, the late one stole the screen
back. The tab you click is the one you see, immediately.

## 0.50.4 — 2026-08-20 14:07 PDT

**A faint CLIque mark sits at the bottom of the left panel.** Large, quiet,
just branding. It does not take clicks, and the list still scrolls over it.

## 0.50.3 — 2026-08-20 13:44 PDT

**Change color on a folder actually stays open.** Clicking it rebuilt the
menu into the swatch grid, and the same click was then treated as "outside
the menu" and closed it. The picker stays up until you pick a color. It
says Color, and there are twenty-four to pick from.

## 0.50.2 — 2026-08-20 13:37 PDT

**Grok's prompt sits in the window again.** Making the largest view always
win left the pane bigger than this window, so a full-screen CLI drew its
input off the bottom. This window now takes the size that fits *it*, but
only while you are looking at it — an unfocused one stays quiet, which is
what stopped the dotted fight from coming back.

## 0.50.1 — 2026-08-20 13:33 PDT

**The dotted pane stays gone.** Those dots are tmux filling space a smaller
window stole — a second CLIque tab, or the phone. Each side kept asserting
its own size, so a fix that grew the pane back lost the next time the
smaller one spoke. The largest view now wins. A smaller one cannot punch
a hole in this one.

## 0.50.0 — 2026-08-20 13:29 PDT

**The sidebar rearranges like the tab strip.** You could already drop a
session onto a folder to file it. Folders themselves sat still, and sessions
inside one could not be put in an order. Drag a folder or a session and a
line marks where it will land — same gesture as the tabs. Dropping a
session onto another in a different folder files it there too.

## 0.49.2 — 2026-08-20 13:23 PDT

**The outage banner follows the tabs you have open.** It already only
spoke up when a provider was actually down, and never for a CLI with no
feed. It was still asking about every session in the sidebar, so a Claude
session you closed last week could put Claude's outage on the bar. Now it
is the open tabs: close Grok, Grok is not asked about.

Grok and Gemini still have no banner. They do not publish the same status
feed Claude, Codex, Copilot and Cursor do, and inventing a scraper for
them would be a number that goes wrong silently.

## 0.49.1 — 2026-08-20 12:26 PDT

**The view count says what it is.** The number next to the green dot is live
views on the box, not open tabs — so 11 with 6 tabs means a second window
or a phone is still connected. Hover spells that out in the count you
actually have.

## 0.49.0 — 2026-08-20 12:14 PDT

**Switching tabs is a show, not a hook-up.** After a reload the other tabs
used to sit as names until you clicked one, then wait a second while a
terminal, a socket and a tmux viewer spun up. They now warm in the
background once the one in front is up, so the first click already has
the pane. Hidden tabs connect at the window's current size and do not
resize the one you are looking at.

If it feels worse than the wait, it comes out.

## 0.48.0 — 2026-08-20 11:26 PDT

**The sidebar no longer jumps every three seconds.** The list was being
redrawn on every poll, so a long sidebar snapped back to the top and a
folder you had just opened slammed shut under your finger. It now only
rebuilds when something actually changed — a ring, a name, a new
session — and it keeps its place in the list.

**A reload no longer attaches every tab.** Opening the panel used to
spin up a live view for each saved tab, which is why a second window
fought the first for the pane size. The strip still shows them all; only
the one you are looking at is attached. Clicking a background tab, or
Alt+1–9, is what opens it.

**On a phone, the controls are actually there.** Close and settings on a
tab, and the pencil on a folder, used to appear only on hover — which a
finger does not have. They stay visible. A long press on a folder or a
past conversation opens the same menu a right-click does.

**And the follow button keeps its icon.** Pausing used to replace the
arrow with a text glyph that wiped the drawing out. It now swaps to a
pause mark in the same set.

**Light theme text that was too faint is readable.** The muted colour
failed the contrast bar on the light grey panel; it is darker now.

Settings, the new-session dialog, menus and toasts ease in. The command
palette does not — that one is opened all day from the keyboard.

## 0.47.0 — 2026-08-20 09:07 PDT

**Tabs that do not fit stay reachable.** A strip of a dozen sessions ran off
the right edge, behind a scrollbar that was itself hidden, so a session that
was waiting or finished was simply gone. Names shrink first. What still will
not fit lands in a **N more** control on the strip — always on screen, wearing
the same working / waiting / error ring the tabs themselves wear — and a click
lists every hidden tab, the ones that need you first.

**And the one you are in is obvious.** It was a two-pixel tint on top of a
chip that looked like every other chip. It now sits as the top of the pane —
same fill, a thicker bar in the CLI's colour, the name in weight — and a
session flashing for attention no longer paints itself like the current tab.

**The ring spins only while a session is working.** When it finishes and
needs you, the arc stops and the ring pulses — same motion on the sidebar
as on the tab, which the scrolling list had been clipping. Idle still
draws nothing.

**The stats no longer shove the tabs.** Readings are fixed-width tabular
numbers, so a decimal appearing or swap dropping to zero does not walk the
strip sideways. They live in a status bar at the bottom now, with the
keyboard shortcuts on the right — the top strip is for sessions.

## 0.46.0 — 2026-08-19 16:54 PDT

**Ctrl+V pastes.** It did nothing at all before — xterm treats Ctrl+V as a
control character, which means calling `preventDefault`, which stops the
browser from ever firing a paste event. So the keystroke was neither delivered
to the CLI in a useful form nor pasted. Ctrl+Shift+V worked, and nobody presses
Ctrl+Shift+V in a browser without being told to. The cost is readline's
quoted-insert, which is a fair trade for the commonest action there is.

**Ctrl+C copies when text is selected.** With nothing selected it is still
SIGINT — a CLI you cannot stop is worse than one you cannot copy from — but
having deliberately selected a line, "interrupt" is not what anyone meant. The
toast says what was taken: "Copied 3 lines", so a copy that grabbed the wrong
thing says so rather than being discovered later.

**Shift+Enter is a newline, not a submit.** A terminal sends carriage return
for both, so a CLI cannot tell them apart and every Shift+Enter submitted the
prompt — which is why writing more than one line meant giving up. It now sends
the CSI-u encoding of "Enter with Shift held", which is what the editors people
compare this to send. Verified rather than assumed: typed into a live Claude
Code prompt, `ESC CR` did nothing useful and `CSI 13;2u` moved the cursor to a
second line.

**And the panel's own box grows further** — it stopped at seven lines, so
composing a paragraph meant scrolling inside the box you were writing in. It
now grows to two fifths of the window, which is a different number of lines on
a laptop and on a phone, as it should be.

**A tab says what its marks mean.** A tab can carry a status ring, an attention
glow and an unread dot, and the tooltip said only the working directory — so
the honest reaction to any of them was "what is that". Hovering now spells it
out: *stopped on an error*, *waiting for you*, *new output since you last
looked*.

## 0.45.0 — 2026-08-19 16:27 PDT

**A pane squashed by another client takes its size back.** A tmux window has
exactly one size and every client attached shares it, so a second browser — or
a phone picking the session up — resizes the first one's pane out from under it.
The result is not subtle: tmux fills the columns the window no longer has with
dots, and the terminal reads as broken.

Nothing noticed, because a client only ever spoke up when *its own* terminal
changed size, and being resized by somebody else is precisely the case that
produces no local change. The poll now carries the window's real size, and a
browser that finds it differs from what it is drawing says so — so the dot-fill
clears within one poll instead of persisting until something happens to jog it.
Only the tab in front, and only on a real difference: two browsers both
re-asserting on a timer would fight rather than settle.

**And the tool that found this was causing it.** `visual_check.py` opened
whatever was in the sidebar, which meant a second browser attaching to sessions
someone was working in — doing exactly the thing described above to a live
pane. It makes its own throwaway session now and deletes it afterwards.

## 0.44.0 — 2026-08-19 16:10 PDT

**A session that wants you says what for, in the row.** The ring told you a
session was blocked and you still had to open the tab to find out what on. The
row now shows the last line it actually printed, in place of the working
directory — which is the least urgent thing on screen at the moment something
is asking you a question. The directory is still there on hover.

This **replaces the hover preview**, and 126 lines of machinery go with it. A
popup has to be summoned, positioned and layered above everything else, and
each of those is a way to be wrong: the layering one broke right-click on every
sidebar row for a while. A line that is simply there when it matters has none
of those problems. `GET /api/sessions/<id>/peek` stays for scripts.

The line skips a prompt sitting under it — the bottom of a pane is usually a
cursor waiting, and quoting that back says nothing; the question is the line
above. Frame is dropped the same way it already was: box rules, separators and
bare prompt marks are not something a session said.

## 0.43.0 — 2026-08-19 16:03 PDT

**The tinted greys keep their contrast.** 0.42.0 made a theme able to own the
256-colour greyscale ramp, and did it by walking from the theme's background to
its foreground — which on a monochrome theme is a walk into a saturated colour.
Shades an application meant as *subtle* came out as a wash, and the text on top
of them had to compete with it.

An application reaching for colour 233 has chosen how far from the background it
wants to be. That choice is not ours to move; only the neutrality is. Each step
now keeps exactly the lightness xterm would have given it and takes the theme's
hue at a low saturation, so `#121212` becomes `#0f1510` rather than something
darker and greener. Every contrast relationship survives, which is the
difference between a themed terminal and a coloured one. The suite asserts the
lightness across all 24 steps, that the ramp still climbs, and that the
6×6×6 cube is left exactly as xterm defines it — an application asking for
colour 82 wants *that* green.

How far a theme goes is a number on the theme now, not a constant in the code.

## 0.42.0 — 2026-08-19 15:59 PDT

**Find a directory you have never opened here.** The picker knew everywhere you
had already worked, which is the right first answer and no answer at all for a
project you have not started a session in yet — exactly when you are least
likely to remember where it lives. Start typing a path and the suggestions now
come from the disk, the way a shell completes: a trailing slash lists what is
inside, anything else matches the last segment against its siblings. The
remembered directories stay in the list alongside them.

**Grok's past conversations exist now.** Grok keeps one append-only log per
project rather than a transcript per conversation — the directory name is the
working directory, percent-encoded, and each line carries the session id, the
prompt and when it was sent. CLIque only understood Claude's layout, so every
Grok conversation was invisible and none of them could be resumed. Six were
found on the machine this was written on, going back weeks.

**Grok says when it is waiting for you.** It had no attention patterns at all,
so a Grok session sitting on a confirmation looked exactly like one that had
finished. The patterns are taken from what a real Grok CLI put on screen, not
from documentation.

**A theme can own the 256-colour greyscale ramp.** Themes define the sixteen
ANSI colours; indices 16–255 are the standard xterm cube and nothing here
touched them. Grok paints its background with colour 233, which is neutral
`#121212` on every theme ever written — so on Trinity, a deliberately
monochrome green theme, its pane looked untouched by a theme that had in fact
been applied. Trinity now tints the greyscale ramp between its own background
and foreground, and 233 renders `#051909` instead. Opt-in per theme: an
application choosing colour 82 wants *that* green, but 232–255 means "a shade
near the background", which is a relative intention worth honouring.

**And the terminal stopped repainting every three seconds.** Applying the theme
was unconditional on every poll, and assigning `options.theme` makes xterm
rebuild its colour service and repaint the whole grid — so a panel left open
repainted every terminal it had, twenty times a minute, to arrive at the
colours already on screen. It happens when something actually changes now.

## 0.41.0 — 2026-08-19 15:50 PDT

**The sidebar shows your work again.** Past conversations were listed under
every folder and they had quietly taken the place over: measured on a real
install, **285 of them against 2 running sessions**, with 163 more than a
fortnight old. At that ratio a sidebar is not a view of what is happening, it
is a haystack with your work in it.

They are **off by default** now, and capped at 14 days when switched on, so the
list cannot grow without limit again. Nothing is lost either way — `Ctrl+K`
then `~` searches every transcript however old, and the empty pane still offers
the last few to pick up. Those are places built for looking something up; the
sidebar is for seeing what is running.

**Right-clicking one does something.** They had no menu at all, so a right-click
produced the *browser's* — which does not read as "nothing happened", it reads
as the panel not being in charge of its own sidebar. Resume it, copy its
directory, or turn the whole listing off. Nothing offers to delete one: a past
conversation is a transcript another tool wrote, and deleting someone else's
data is not this program's business.

**Any setting that was not a checkbox was being stored as one.** `update_settings`
ended in a catch-all of `bool(value)`, on the assumption that anything without
an explicit branch was a toggle. That held until a setting was a number, at
which point 14 was silently stored as `True` — and it would have done the same
to the next one. Values are now coerced to the shape of their own default,
which is the schema and was there all along.

## 0.40.0 — 2026-08-19 15:42 PDT

**Right-click works on sidebar rows again.** The preview popup added in 0.34.0
sat at a higher layer than the context menu, and hovering a row for half a
second is exactly what you do on the way to right-clicking it — so the menu
opened *behind* the preview, every time, on any session you were not already
looking at. It failed silently, because the clicks still landed on a menu you
could not see. The preview now sits below the menu and closes when one opens,
and the test suite asserts the order of the whole overlay stack.

**CLIque can make the directory.** Typing a path that does not exist ended in
"there is no directory at that path" and an instruction to go and find a shell —
from a tool whose entire job is running shells. There is a **Create it** button
on that message now. Parents are created; a relative path, an empty one, or
something that exists and is not a directory is refused with the reason. Never
implicit: it happens because the button naming that path was pressed.

## 0.39.0 — 2026-08-19 15:26 PDT

**Move a session to another folder without dragging it.** Filing a session
somewhere else was drag-and-drop and nothing else — which does not exist on a
phone, and is not discoverable on a desktop either, since nothing about a row
says it can be dragged. Right-click a session (long-press on touch) and there
is now **Move to folder…** and **Take out of its folder**.

Worth saying plainly, because the two get conflated: a session's **folder** is
a label and can change whenever you like. Its **working directory** cannot —
the CLI is already running there, and no process can be moved to a different
one. If a session is in the wrong directory, the answer is a new session in the
right one.

## 0.38.0 — 2026-08-19 15:17 PDT

**The overlapping text in menus is fixed, and it was not the renderer.** Two
cursors, a choice repeated on the line below it, fragments of one line sitting
inside another — reliably visible whenever a CLI drew a list to pick from.

The cause was the order of two calls on attach. A session is created far wider
than any browser — 236 columns against a typical 100 — and the window was
resized to the browser's size *after* the terminal was attached. So tmux painted
a complete frame at 236 columns into a terminal that wraps at 100, the CLI then
received the resize and redrew, and the correct frame landed on top of the
wreckage of the first one. The window is sized before anything is painted now,
and the scrollback is captured at the width it will be shown at.

Two suspects were ruled out with measurements before the real one was found: the
Unicode 11 width tables agree with the C library tmux uses on all 26 characters
these CLIs actually draw, wide ones included, and the pane width does not drift
when a second client attaches. Neither was it.

**Renaming a session no longer saves halfway through.** The sidebar is rebuilt
on every poll and an inline rename puts a live text box inside it, so the
rebuild removed the box mid-word — and removing a focused element fires the
event that commits the rename. Typing a name got cut off and saved every three
seconds. The list now holds still while a name is being typed into it.

## 0.37.0 — 2026-08-19 15:11 PDT

**Restarting CLIque no longer kills every session.** This is the important one.
The systemd unit did not set `KillMode`, so it took systemd's default —
`control-group`, which signals *every* process in the unit's cgroup on stop.
The tmux server CLIque starts is a child of the panel, so it lives in that
cgroup: `systemctl restart clique`, an upgrade, or a reboot silently destroyed
the sessions this tool exists to keep alive. Sessions outliving the browser is
the promise; they have to outlive the panel too.

`KillMode=process` is now in the unit, the test suite fails without it, and a
session and its scrollback are verified to survive a restart. **If you installed
the unit before this release, copy `deploy/clique.service` over your
`~/.config/systemd/user/clique.service` and run `systemctl --user daemon-reload`
— editing the repo file alone changes nothing.**

**The working indicator stops spinning forever.** tmux's activity clock counts
a *redraw* as output, so a CLI that animates while it waits ticks it endlessly
and sat permanently on "working". Measured on a real Grok CLI left completely
alone: the clock ticked every two seconds for as long as it was watched, while
the captured pane stayed byte-identical the entire time.

The clock still decides cheaply. Once a pane has claimed to be busy for eight
seconds it has to prove it: the visible screen is captured and compared, and
text that has not changed for four seconds is not work whatever the clock says.
Quiet panes are never captured, so the ordinary case costs nothing, and eight
sessions all stuck in the pathological state cost under two per cent of one
core. The trade, stated plainly: a CLI that is genuinely busy while redrawing a
*byte-identical* screen will read as idle. A spinner is not that, and neither
is streamed output.

A session that has just started also no longer fires a "finished" notification
as it settles.

**Peek shows what was said, not what was drawn.** A modern CLI's pane is mostly
frame — box rules, separators, an input box around nothing — and eight raw
lines of that buried the one line that answered the question. Lines made
entirely of box-drawing glyphs, rules, prompt marks or whitespace are dropped,
which is a property of the text rather than knowledge of any CLI; a line with
one real word in it is always kept. A wider window of scrollback is searched so
the filtering has something to find.

## 0.36.0 — 2026-08-19 14:54 PDT

**Text no longer smears when you have a selection.** Fragments of old lines
were being left behind when a screen redrew underneath a live selection — an
interactive menu being arrowed through was the reliable way to see it. Not a
font problem and not a character-width problem: xterm's default DOM renderer
draws elements per run of text, and those are the stale nodes. The canvas
renderer, now vendored, repaints the whole cell grid each frame, so there is
nothing left over to see.

Canvas rather than WebGL deliberately. WebGL is faster and it wants a GPU
context that a phone, a VM or a remote session can refuse or lose, and this
panel is meant to open anywhere. If the file is missing or a 2d context is
refused, the DOM renderer is still there — imperfect beats absent.

**The directory picker is a picker.** It was a type-ahead list, and browsers
filter those by whatever is already in the field — which is prefilled with the
directory you are in, so it showed exactly one entry and looked like CLIque
knew nothing. There is a real dropdown beside the field now, grouped into
*Running now*, *Recent* and *From history*, showing everything regardless of
what is typed. Type-ahead still works once you start typing somewhere new.

**A path that is not there says so before you press Start.** It used to fall
through to the server's error, which is correct and arrives after you have
named the session, chosen a CLI and committed. It also has an explanation worth
giving: the picker offers directories from past conversations, and a project
that has since moved is still in that history.

## 0.35.0 — 2026-08-19 14:45 PDT

**Stop typing paths from memory.** The working directory field in the new
session dialog now suggests everywhere CLIque already knows you work: what is
running right now, then what you looked at most recently, then the directories
your past conversations came from. It is a native suggestion list, so it is
type-ahead on a desktop and a proper list on a phone keyboard — and typing
somewhere it has never heard of still works, which a dropdown would have taken
away.

The field also stops defaulting to `/root`. That was the machine this was
written on rather than anybody's default; it is now where you were last, or the
home of whoever started the server.

**And it tells you what is already happening there.** Two agents in one
directory is the cheap mistake with the expensive recovery — both editing the
same files, neither aware of the other, and an afternoon spent working out
which change came from where. Before you start, the dialog says so:

> *api rewrite is already running here · 13 files were written in the last 15
> minutes · 2 uncommitted changes on main*

Advisory only. No locks, no forced worktrees, nothing refused — somebody
starting a second session in a busy folder usually means to, they just did not
know. Uncommitted changes are only mentioned alongside something else, because
on their own they are the normal state of every repo anyone works in.

It reads exactly three things: CLIque's own session list, file timestamps, and
git where a repo happens to be — the list the product is allowed to read. A
directory that is not a repo simply reports less. Pulled when you stop typing a
path, never polled, so an idle panel still costs what it always did. Available
to scripts as `GET /api/workspace?cwd=…`.

## 0.34.0 — 2026-08-19 14:40 PDT

**Which one actually needs you — one answer, not twenty indicators.** The
sidebar is honest and it does not scale: at twenty sessions, reading every ring
and deciding is a job, and it is a job you do every few minutes. CLIque now
ranks the same facts and names one. `Ctrl+K` puts it first among the commands
whenever anything is blocked, and it is the top block on the empty pane, which
is the screen you land on after being away.

The order is error, then waiting, then unread — and among equals, whichever has
been like that longest, because the one blocked for eleven minutes is costing
more than the one blocked for ten seconds. A working session never appears; it
does not need you, and listing it would teach you to ignore the list.

It is a sort, not a model. Every fact in it already existed — the attention
tiers, the activity clock, the unread mark — and nothing new is captured,
polled or inferred to produce it.

**Peek at a session without opening it.** Hover a row in the sidebar and the
last few lines appear beside it; on a phone, long-press and choose "Peek at the
last lines". The ring tells you a session is waiting, and this tells you what
it is waiting *for* — which used to mean opening the tab, reading, and going
back to what you were doing.

Nothing is captured until you actually ask. There is no poller behind it, a
sidebar of twenty sessions costs nothing until a pointer settles on a row, and
the answer is cached against the pane's own activity clock so moving back and
forth across a quiet sidebar is one capture rather than one a pass. Available
to scripts as `GET /api/sessions/<id>/peek`.

**Front-end logic is tested now.** `app.js` has no build step and no module
system — the point — and that also meant its decisions had nowhere to be tested
from. `tools/frontend_check.js` reads the file, cuts out a named region, and
runs it against stubs. No jsdom, no bundler, no `package.json`. The ranking
above ships with eight assertions covering what must never appear in it.

## 0.33.0 — 2026-08-19 14:32 PDT

**Move a half-typed thought to the session it belongs in.** You are partway
through an instruction and realise it is the *other* agent that should get it.
That has always cost four steps — select, cut, switch, paste — to move text the
panel was already holding on the server. One step now: the **›** beside the
prompt box, `Ctrl+K` → "Move this draft to another session", or drag that
control onto any row in the sidebar.

- It is a move, not a copy. Two sessions holding the same half-written
  instruction is a way to send it twice.
- Text already waiting in the target is never overwritten — the arriving draft
  is added underneath it, with a blank line between two thoughts.
- The cursor lands at the end of the sentence you were in.

Drafts already survived tab switches, reloads and a closed laptop, because a
draft lives on the session rather than in the box. This only changes which
session that is.

**The command palette can be borrowed as a picker.** "Choose a session" is now
the same list, the same typing and the same arrow keys as jumping to one, with
the actions taken out and a callback put in — rather than a second list widget
to search, style and keep working on a phone. Moving a draft is the first thing
that uses it.

## 0.32.0 — 2026-08-19 14:23 PDT

**One prompt box, not two.** Claude, Codex, Gemini and most of the rest draw
their own input box at the bottom of the pane, and CLIque was putting a second
one directly underneath it. On the new `auto` setting — the default — the panel
does not draw a box for a CLI that already has one. A shell draws no box, and
there the panel's stays, because it is not a duplicate of anything: it is the
only place Run, the repeat counter and a saved draft exist at all.

Which CLIs have their own box is `own_input = true` in `clis.toml`, so it is a
line of config for a CLI we have never heard of rather than a code change.

The mode pill no longer goes with it — and it used to. Anyone who turned the
old setting to "terminal" to stop the double box also lost the control for
Claude's permission mode, with nothing saying why. The pill is what the bar
carries now when the box is gone.

**Real icons.** The panel drew its controls with Unicode characters — `✕`,
`⚙`, `✎`, `▸`, `⇩` — which is a set only by accident: each one comes from
whatever font the operating system picks, so the weights disagreed, the sizes
disagreed, and several were missing outright on a phone. Twelve Lucide icons
are now vendored into a single inline sprite: about 4 KB, no request, no font,
no build step, and `currentColor` so every theme recolours them for free.
`tools/build_icons.py` regenerates it and the test suite fails if the page and
the icon list disagree.

## 0.31.0 — 2026-08-19 14:14 PDT

**Somebody else's outage, said in your panel.** A CLI that has gone quiet and a
provider that is down look identical from the outside, and the difference is
whether you spend twenty minutes debugging your own prompt. CLIque now reads
the public status page of the provider behind each CLI you have **running** and
puts one line under the tabs when it is not good news, with a link to the page.

- Nothing is drawn when everything is fine. "All systems operational" is not
  news, and a bar that is always there is a bar nobody reads on the day it
  finally says something.
- Claude, Codex, Copilot and Cursor ship with a feed. Any other CLI is two
  lines of TOML — `status = { url = "…/api/v2/status.json", page = "…" }` —
  because adding a CLI has never been allowed to need a code change.
- One format, Atlassian Statuspage. A second parser here would be the first
  step towards a directory of per-vendor scrapers.
- Read every five minutes, only for CLIs with a session open, and not at all
  when the panel is idle. No identifier, no session name, no query string. This
  is the only request CLIque makes on its own; `service_status: false` stops
  the thread immediately and the panel never reaches the internet.

**Overlapping characters in a status line are fixed.** xterm.js ships Unicode 6
width tables, in which an emoji-presentation glyph such as `⚠️` is one cell
wide. Fonts draw it as two, so the terminal reserved one column, the glyph
painted two, and the next character landed on top of it — which is why a Claude
Code status line looked like it was printing over itself. The Unicode 11 addon
is vendored and active. Nothing was wrong with the CLI.

**tmux's own status bar no longer shows through.** It was switched off
globally, and that was not enough: this server reads your `~/.tmux.conf` on
purpose so your keybindings work, and a config with a status-line plugin turns
it back on per session. The result was CLIque's plumbing on screen — a viewer
session announcing itself as `sm-view-ba434f` at the bottom of a pane. Now set
on each session it creates, which beats the global value without costing you
anything else you had configured.

**The changelog tab works.** It fetched an absolute path, which escapes the
`<base href>` and resolves against the site root — so it worked on
`127.0.0.1:3200` and 404ed for everyone reaching the panel through
`tailscale serve` at `/clique`, which is the documented way to run it. A test
now fails the build on any API call that escapes the mount point.

**Reconnecting no longer writes into your scrollback.** "— disconnected,
retrying —" was printed into the terminal itself, where it is permanent,
cannot be scrolled past, and is indistinguishable from something your program
printed; a restart during an evening's work left a screen of nothing else with
the real output pushed off the top. Connection state is about the pane, not
from it, so it is an overlay that clears itself. The retry behind it was also
fixed: it backed off not at all and never stopped, so a laptop shut overnight
woke to twelve panes that had each tried twenty thousand times. It now doubles
from one second to thirty, gives up after an hour, and stops immediately when
the session is known to be gone.

**The panel says when the server was upgraded under it.** A panel left open for
days against a server that has been updated is a browser running last week's
scripts, and every symptom of that looks like a bug somewhere else. It says so
once, with a Reload button, and never reloads on its own.

Also fixed, from a review of the front end and the storage layer:

- **A folder colour was written into a `style` attribute unchecked**, and the
  server never validated it — so recolouring a folder could put script on the
  next person's screen. Hex only now, refused at the setter and refused again
  at the point of drawing, because a state file written by hand never passes
  through the setter.
- **`POST` and `PATCH /api/folders` return the folder**, not `{"ok": true}`. A
  caller whose colour was rejected had no way to find out.
- **Two background loops could double up.** Turning the webhook off and on
  again — or the new status feed — cleared the stop signal out from under a
  thread that was still parked on it, leaving two watchers running and every
  notification arriving twice. Each loop now retires when it is no longer the
  current one.
- **Two maps grew forever.** Sessions that had finished, and sessions marked
  for attention, were keyed by id and never removed, in a panel meant to stay
  open for weeks.
- `node --check` now runs on the front end in the test suite. There is no
  build step, which is the point — and it also meant nothing stood between a
  typo and a panel that loads and then does nothing.

## 0.30.0 — 2026-08-19 13:49 PDT

**A security pass, from three independent reviews.** Nothing here was reported
by a user; all of it came from reading the code with fresh eyes. Upgrade if you
expose CLIque beyond your own machine.

- **The `Origin` check could be defeated by anyone with a free tunnel.** It
  fell back to the DNS-rebinding allowlist, which accepts any `ts.net`,
  `trycloudflare.com` or `ngrok` host — correct for the `Host` header, and the
  exact opposite of what is wanted for `Origin`. A page on someone else's ngrok
  subdomain counted as our own page, which is the cross-site WebSocket attack
  the check exists to stop. `Origin` must now match the `Host`, or a name you
  configured.
- **`/api/state` returned `webhook_secret` to any read-only token** — enough to
  forge a signature your receiver trusts. It is write-only now: the page is
  told whether one is set, never what it is. A blank field means "leave it
  alone"; type a single `-` to remove one.
- **`/brand/../app.js` served the application before login.** The public-asset
  test was a `startswith`, and URL parsing does not collapse `..`. It resolves
  the path and checks containment now.
- **The webhook's link-local refusal had two holes**: redirects were followed
  after the check had passed, and `::ffff:169.254.169.254` is not "in" the IPv4
  network so the comparison waved it through. Redirects are refused outright —
  a webhook receiver has no reason to redirect — and mapped addresses are
  unwrapped before testing.
- **`state.json` was world-readable** while the password, signing secret and
  token store were all `0600`. It holds the webhook secret and every unsent
  draft. Now `0600` from creation, and tightened on load for files written by
  earlier versions.
- **The login form read an unbounded body** — the one request an
  unauthenticated caller may send. Capped before the read.
- **Control frames were accepted up to 8 MB.** RFC 6455 allows 125 bytes and no
  fragmentation; a socket with no write permission could send oversized pings
  indefinitely.
- **New sessions defaulted to `/root`**, which was a leftover from the machine
  this was written on rather than a product default. It is now the home of
  whoever started the server.

Also fixed: a write to the PTY was one-shot and silently truncated a large
paste; `tmux.exists` let a timeout escape into a request handler; the token
store had no lock while being written from three threads; and a
`Content-Length` that was not a number returned 500 instead of ignoring it.

Every one of these is now covered by the test suite, which is the only way a
fix stays fixed.

## 0.29.1 — 2026-08-19 13:06 PDT

**You can tell which session you are in from the sidebar again.**

Three things were wrong at once, and the third hid the first two.

The active row's highlight was a background colour and nothing else, which is
too quiet against a dark theme when it is one row in thirty. It now takes the
active CLI's colour down its left edge — the same language the tab bar and the
pane border already speak.

That edge was reading a custom property set on `<main>`, and the sidebar is a
*sibling* of `<main>`, so it never saw it. Both properties are set at the root
now. They are also two properties rather than one, because "which CLI is this"
and "which row am I on" are different questions: turning the CLI tint off
should not take the selection highlight with it.

And the case that prompted this: **a collapsed folder draws no rows at all**,
so switching to a session inside one left the whole sidebar looking like
nothing was selected — because nothing was, there was nothing there to select.
A folder holding the active session now says so in its header, open or shut.
The sidebar also scrolls a selected row into view when it is off-screen,
without animating, because a list someone is reading should not slide under
them.

## 0.29.0 — 2026-08-19 13:04 PDT

**Fixed: pasting text did nothing if the clipboard also held an image.**

Copying from a browser, a spreadsheet, or most document editors puts a
rendered image on the clipboard *alongside* the text. The screenshot handler
claimed the paste whenever an image was present, so those pastes quietly
became a file on disk and the text never arrived — which from where you are
standing is simply "paste is broken". Text now wins whenever both are there. A
real screenshot carries no text, so the feature that was actually wanted loses
nothing.

**Security lint is on permanently.** `ruff` now runs flake8-bandit over the
whole tree as part of the build. Everything it flagged is either fixed or
annotated at the line with the reason it is not a finding — running `tmux` from
`PATH` is the product, and the SHA-1 in the WebSocket handshake is mandated by
RFC 6455.

Two real fixes came out of it:

- **The webhook could be pointed at cloud metadata.** "POST to a URL someone
  typed" is the exact shape that leaks instance credentials on a cloud box.
  Link-local addresses are now refused, checked after resolving the host, and
  re-checked at the moment of sending rather than only when the setting is
  saved. Loopback and private ranges are deliberately still allowed: ntfy on
  `127.0.0.1` or Gotify on the LAN is the normal case, and breaking it to
  protect an admin from their own machine would be the wrong trade.
- **An `assert` was doing real work** in the PTY reader thread. Asserts vanish
  under `python -O`, and what replaced it was an `OSError` raised in a daemon
  thread with nobody to catch it.

**Stopped committing build artefacts.** `pip wheel .` leaves a `build/`
directory in the working tree, and sixty generated files had been swept into
the repo — a stale duplicate of every module sitting beside the real one.

**A clock you can read.** 12-hour or 24-hour, in Settings → Notifications. Not
taken from the locale, because plenty of people read one format at work and the
other at home.

**And a star button** on the About pane. A star is the whole marketing budget —
the lists that catalogue tools like this one sort by exactly that number.

## 0.28.3 — 2026-08-19 12:56 PDT

**The version in the corner tells you when there is something new**, and takes
you to it. A changelog nobody opens is a file, not a feature — so the running
version grows a small dot when it is not the release whose notes were last
read, and clicking goes straight to them. Seeded on first load, so a fresh
install does not arrive already claiming to have news.

**`CPU`, `MEM`, `SWAP`, `DISK`, `LOAD`** — the stat labels were lower case, and
four of the five are acronyms, so it read as wrong before it read as anything.
The values keep their own case: "138.1G free" is a sentence, not a label.

**Fixed: the test suite pointed at a file that no longer exists.**
`tools/smoke.py` named `config/clis.toml` by hand, and when the catalogue moved
into the package in 0.28.0 it kept passing locally against a stale copy left on
disk while CI — which has no stale copy — failed. It now asks the same function
the app asks, so there is one answer and it cannot rot again. That is the
better lesson: a path written down twice is a path that will disagree with
itself.

## 0.28.2 — 2026-08-19 12:45 PDT

**A clock, a tip, and the notification link actually works.**

The empty pane gained the time — in the browser's own zone, or one you name in
Settings → Notifications. `Intl` already carries the whole zone database, so
this needs no data and reaches nothing; a name that is not a real zone is
refused when you save it rather than throwing in the page. And one line of
advice, keyed to the date so it is the same all day: a tip that changes on
every repaint is a slot machine, and nobody finishes reading one.

**The fix that mattered.** Every webhook payload since 0.27.0 has carried a
`?session=<id>` link back to the exact session — and the panel was ignoring the
parameter entirely, so tapping a notification dropped you on whatever tab you
had left open. Which is the one thing a notification exists to save you from.
It now opens and selects that session after the workspace restores, then
strips the parameter so a reload does not keep dragging you back.

## 0.28.1 — 2026-08-19 12:41 PDT

**The empty pane does something now.**

It used to say "No session open", which is the one fact an empty pane already
demonstrates. That space is where you land after finishing something and where
you arrive after signing in, so it is worth more than a label.

It now shows what the box is doing — how many sessions are running, how many
are working, and how many are waiting on you, that last one in the colour that
means it — and then the two shortest routes back in: the sessions you looked at
most recently, and conversations you can resume. One click each.

All of it is state the panel already polls. No new endpoint, nothing to
configure, and nothing fetched from anywhere: an empty pane is not the place to
start having a self-hosted tool phone out for a weather icon.

## 0.28.0 — 2026-08-19 12:37 PDT

**It installs with one command now.** `pip install clique`, and the packaging
finally ships the thing people came for.

The wheel used to carry the server and none of the interface — every static
file, the vendored xterm.js, the icons, and the CLI catalogue were all outside
the package, so an installed copy would have started and served 404s. The
catalogue moved to `clique/config/clis.toml` and the web assets are declared as
package data. Verified the only way that means anything: built the wheel,
installed it into a clean virtualenv, started it, and loaded the page from
site-packages.

The version is read from `clique/__init__.py` now, so there is one number to
bump and the wheel cannot disagree with what the panel reports about itself.

Where the CLI catalogue is read from, in order and with no magic: `--config` if
given, then `$CLIQUE_HOME/clis.toml` if it exists, then the copy in the package
— which in a checkout is the repo's own file, so editing it works exactly as it
always did. `clique config` writes an editable copy into `$CLIQUE_HOME` for an
installed copy, and refuses to do it in a checkout, where it would put a shadow
file in front of the repo's and make the next edit appear to do nothing.

**The collapsed rail keeps your markers.** It drew a plain dot regardless of
what you had chosen, so shrinking the sidebar quietly threw away both the CLI
icons and the status rings — the two marks that make a column of sessions
readable at all. It now makes the same two calls a sidebar row makes, so
whichever of them your settings put in charge is what appears there too.

## 0.27.1 — 2026-08-19 12:29 PDT

**URLs in the pane are clickable.** A click opens a new tab; `Ctrl`/`Cmd` and a
click opens a new window.

Written rather than vendored: xterm already exposes the API this needs, the
whole thing is forty lines, and one more vendored file is one more version to
keep in step with the core for a feature this size.

`http` and `https` only, checked when matching and again when opening. A
terminal prints whatever a program sends it, so what is on screen is not
trustworthy input — `javascript:` and `file:` are the two that would matter,
and neither is matched or opened. Trailing punctuation is trimmed because it
belongs to the sentence, but brackets only when unbalanced, so a Wikipedia URL
ending in `)` still works.

## 0.27.0 — 2026-08-19 12:26 PDT

**It can reach you with the panel shut.**

An attention state you only see while looking at the panel is decoration. One
field in Settings → Notifications takes a URL, and a small JSON body is POSTed
there when a session starts waiting, stops on an error, finishes, or dies.

One URL, not a list of services. ntfy, Gotify, Discord, Mattermost, Home
Assistant and Uptime Kuma's push monitors are all "POST some JSON to this
address", so a single field reaches every one of them with no app, no account,
no SDK and no settings sheet full of logos. Adding the second integration is
the decision that creates a permanent "please add mine" queue, so there is no
first one.

Not Web Push either: VAPID needs signing the standard library cannot do, which
would cost a dependency — and ntfy's own app already delivers real push to a
phone for free.

Each event fires on the edge. A session that has been waiting an hour is not
news every ten seconds, and a notifier that repeats itself is one you mute. An
optional shared secret signs the exact bytes sent. One attempt, five second
timeout, no retry queue — a dropped notification is superseded by the next
change, and durable retries mean a database.

**And a test button**, because every webhook UI needs one for the same reason:
you paste a URL and want to know it works now, rather than the next time
something finishes at three in the morning.

The watcher only exists while a URL is set. With no webhook configured nothing
runs, and an idle CLIque costs exactly what it did before.

## 0.26.0 — 2026-08-19 12:19 PDT

**Waiting on you, and stopped on an error, are now different from idle.**

The question this whole thing exists to answer is *which of these needs me*,
and until now a session waiting for an answer looked exactly like one that had
finished. The activity clock cannot tell them apart; it only knows output
stopped.

Three tiers, each optional, each falling back to the one below:

1. **The activity clock** — shipped, works for any CLI, says only working or
   quiet.
2. **Patterns over the pane** — regexes declared per CLI in `clis.toml`,
   matched against the last forty lines once a session goes quiet. A CLI with
   no patterns skips the tier. Generic defaults ship for Claude Code and are
   meant to be edited: these are the prompts most CLIs show, not a
   transcription of anyone's wording.
3. **The session says so itself** — `POST /api/sessions/<id>/attention` with
   `waiting`, `error` or `clear`. Wire it to a hook your CLI already has and it
   is exact rather than guessed.

Note what is not here: any knowledge of a vendor. Tier 2 reads config you can
edit the day a prompt changes, without waiting for a release. Tier 3 receives
an assertion and believes it. The line is that patterns stay strings in a TOML
file.

A signal goes stale by itself once output arrives after it. A session that
carried on is not waiting, and a mark that sticks when it should not is a mark
you learn to ignore.

**Also: the wrong clock.** `busy` and unread were reading tmux's
`session_activity`, which moves when a *client* does something and stands
still while a detached session produces output for an hour. `window_activity`
is the one that tracks the pane. Both are read now and the later wins — so a
session with no browser attached finally looks busy while it is, and goes
unread when it should.

**And the pressure dots are legible.** CPU, memory, load and disk were on a
smooth green-to-red hue ramp, which looks right in a screenshot and tells you
nothing in use: the middle of that ramp is one indeterminate yellow, and 40%
and 55% are the same colour to anyone not holding a swatch. Four hard bands
now — calm, busy, high, critical — with critical pulsing, because at the point
where something is about to go wrong the dot should find you rather than wait
to be looked at.

## 0.25.0 — 2026-08-19 11:53 PDT

**Which CLI am I in?**

Nine panes of black text look identical, and the moment it matters is the
moment after you switch — a Claude prompt typed into a shell is a mistake you
only notice once it has run.

The top edge of the pane, the top of the active tab, and the prompt box while
you are typing in it all take the active CLI's colour, and switching tabs
repaints them. One custom property set in one place, so turning it off in
Settings → CLI markers is one assignment rather than a hunt through a
stylesheet.

The colours are yours to change now, per CLI, with a reset back to whatever
`clis.toml` ships. A palette that reads well on the built-in dark theme can
vanish on somebody's Solarized, and that was never a reason to make anyone
live with it. They save on the server with everything else, so a colour
chosen at the desk is already there on the phone.

## 0.24.0 — 2026-08-19 11:21 PDT

**The logo stays the logo. The ring around it says how the session is doing.**

Status used to be painted onto the CLI's own mark: Claude's icon rendered in
whatever colour the session happened to be, and a busy one faded in and out.
That spent the one thing on a row you could identify without reading — and it
was reporting whether a browser was attached, which is not what anyone wants to
know.

Four states now, and only three of them draw anything:

- **working** — an arc turning around the icon
- **waiting** — finished and not looked at yet: a steady ring, pulsing
- **idle** — alive and quiet, and it draws *nothing*. Most sessions are fine
  most of the time, and twenty marks all saying "fine" is twenty things to look
  past on the way to the one that is not.
- **stopped** — the mark goes grey and the ring goes away

Only rotation and opacity animate, so the whole thing is composited and costs
nothing; `prefers-reduced-motion` keeps every state distinguishable and stops
the movement. Each ring carries its own label for a screen reader and a
tooltip, because a signal only sighted people receive is half a signal.

"Working" is held for a few seconds after the last output. The server reports
output in the last two seconds and the panel asks every three, so a CLI writing
in bursts used to flicker — and an indicator that blinks twice a second is
worse than none.

## 0.23.0 — 2026-08-19 11:17 PDT

**An agent takes a screenshot; now you can look at it.**

Pasting has worked in one direction for a while: `Ctrl`/`Cmd`+`V` puts an image
in the session's own directory and hands the CLI the path, because a path is
the only thing that can cross between a browser holding bytes and a terminal
that cannot draw a picture. Nothing carried the answer back. An agent that
screenshotted a page had produced something you had to leave the panel to see.

Sessions that made an image grow a count in the tab bar. Clicking it opens the
grid, clicking a thumbnail opens it full size, and **Send path** drops the file
back where you are typing — the same landing paste already uses, so the round
trip is one gesture each way.

Deliberately not a file browser. It lists images that appeared in the working
directory *while the session was running*, one level deep, in directories you
choose (Settings → Images). That rule is what separates what the agent made
from what the project already contained, and it is filesystem state — no vendor
is asked what happened, and no agent has to be told this exists.

Everything the browser sends is re-derived server-side: a path that climbs out
of the working directory is refused after symlink resolution, and the
`Content-Type` comes from the file's magic bytes, never its name.

## 0.22.0 — 2026-08-19 10:55 PDT

**Long press a session on a phone and the menu opens.**

Rename, archive, move and kill were reachable only by right-clicking, which
does not exist on touch. Folders got away with it — the pencil is the same menu
with a way to find it — and tabs had the gear. Sidebar rows had nothing, so
half the app was missing on the device most likely to be checking a session
from the sofa.

- A **half-second press** on any session row opens the same menu right-click
  opens. A short buzz when it lands, because nothing has moved on screen yet
  and a press that has taken is otherwise indistinguishable from one that has
  not.
- **Moving cancels it.** A press that turns into a scroll is a scroll, with
  enough slop that a resting finger does not count as movement — a menu that
  refuses to open is worse than one that opens when you meant to scroll.
- Lifting after the menu opens no longer counts as a tap on the row, which
  would have opened the session behind its own menu.
- Menu rows get a **real tap target** on any coarse-pointer device, rather than
  the padding a mouse cursor is happy with.

Delegated to the sidebar rather than bound per row: the tree is rebuilt on
every poll, and three listeners per session every three seconds is churn for
nothing.

## 0.21.1 — 2026-08-19 10:50 PDT

**`API.md` exists.** The settings sheet had been pointing at a full reference
for several releases and there was no such file — the worst version of a
documented API, because it claims a surface nobody can find.

- Every route, every settings key, every field `PATCH` accepts, what a
  read-only token is refused, and exactly what `/healthz` will and will not
  tell a caller with no credential.
- **`tools/api_drift.py` keeps it honest**, alongside the other suites: add a
  route or a setting without writing it down and it fails. A reference
  maintained by hand holds for about three releases; this one cannot quietly
  fall behind.

Nothing changed in the app. The rule behind it is in `CLAUDE.md` now — every
action in the panel is an HTTP call, because a feature reachable only by
clicking is a feature an agent driving CLIque cannot use.

## 0.21.0 — 2026-08-19 10:43 PDT

**Your tabs are yours, not your browser's.**

Which sessions had a tab, their order, and which groups were collapsed lived in
`localStorage` — the one place a person's choices were still stranded on one
machine. Close the laptop and open the panel on the phone and it was a blank
workspace; clear the browser and twelve panes were a morning to reopen.

- **The workspace is on the server now**: open tabs and their order, the tab
  that was in front, and the collapsed state of Running, Ungrouped and
  Archived. Reload, or sign in somewhere else, and the panes are where you left
  them.
- **Lifted, not reset.** Whatever the browser was holding on the day this
  changed is read once, moved up, and removed locally. Nobody loses the tabs
  they had open.
- Restored on the first poll and never re-applied after, so two panels open at
  once do not drag each other's tabs around mid-read. Last one to touch a tab
  wins the stored copy — the right answer for one person on two devices.

Sidebar width and sidebar shown/hidden stay in the browser, and stay the
counter-example: those are about the screen in front of you, and a phone should
not inherit a 400px sidebar from a desktop.

## 0.20.0 — 2026-08-19 10:30 PDT

**The changelog is in the app now, and it finally says what time it was.**

- **Settings → Changelog** lists every release, newest first, read from the
  same `CHANGELOG.md` this repo ships. One copy, so the notes in the app cannot
  drift from the notes on disk — which is what happens to every second copy of
  a release note by about the third release.
- **Every entry carries a time, in Pacific.** Nineteen releases had shipped on
  a single date, which made the date worthless for telling them apart or
  putting them in order. Backfilled from the commit times, converted out of
  UTC, so the evening ones correctly say the evening they happened rather than
  the next morning in Greenwich.
- The day is a heading and the entry is a time under it, and the release you
  are actually running is marked.

- **`GET /healthz`, no login required.** Uptime Kuma, Gatus, Healthchecks and
  every other self-hosted monitor want one URL that returns 200, and anything
  that makes them carry a credential first is a thing that ends up unmonitored.
  Anonymously it returns `{"ok": true}` and nothing else — no version, no
  session names, no counts. Signed in, or with a token, it adds uptime, how
  many sessions exist and how many are alive, connected terminals, and whether
  tmux is reachable at all.

Fetched the first time the tab is opened rather than at load — release notes
are read twice a year and the panel opens in a quarter of a second. Markdown is
parsed on the server into structure rather than markup, so the browser builds
elements instead of assigning HTML, and nothing out of a file can turn into a
node by accident.

## 0.19.0 — 2026-08-19 10:20 PDT

**What you have not seen, and where you stopped reading.**

- **An unread dot** on any session that produced output while you were
  elsewhere, in the sidebar and on its tab. Flashing says *something happened*
  and is gone a second later; this is still there an hour later, which is the
  point of it. Looking at the pane clears it.
- **A rule across the pane** at the point you left, so coming back to four
  hundred new lines shows where your eye stopped.

Neither stores anything new. The dot compares tmux's own activity clock against
the `last_seen` already written on every tab switch, and the rule is a terminal
decoration rather than text written into the buffer — writing into a pane that
a full-screen CLI is repainting would garble whatever it was drawing.

## 0.18.0 — 2026-08-19 10:19 PDT

**Closing a tab has always kept the session running. Nothing ever said so.**
The ✕ read as destructive, so people killed sessions they only meant to put
down.

- Closing now says what it did — *"Closed the tab — <name> is still running"* —
  and offers **Kill it instead** in the same breath, for the times that was
  what you meant. Killing still asks first.
- **Running is now the sessions with no tab open.** A pane you are looking at
  is already a tab across the top; a second row for it says nothing. What
  needed a home is the session still working with nothing on screen — the one
  you forget you started. It sits at the top of the sidebar.

## 0.17.0 — 2026-08-19 10:05 PDT

**A half-typed prompt survives everything now.** Switch tabs, reload the page,
close the laptop and open it somewhere else — the words are still in the box.

- Drafts are **per session**, so twelve panes can each hold an unsent thought.
- They live **on the server**, not in `localStorage`: an unsent instruction is
  about the work, so it follows you between devices. Sidebar width is the
  counter-example and stays in the browser.
- Written on a debounce rather than per keystroke, and committed immediately
  when you switch tabs or the page is hidden — the laptop-lid case, which is
  exactly when someone expects their words to still be there.
- Sending clears the draft, because it is no longer unsent.

## 0.16.1 — 2026-08-19 09:59 PDT

Marks beside each support option — a coin glyph in each project's colours, and
a cup for Buy Me a Coffee. Drawn inline rather than fetched: no build step, no
CDN, and nothing to 404 on a box with no route to the internet. They are our
own glyphs rather than the projects' official logos, which keeps someone else's
trademarked artwork out of a repo that is about to go public.

## 0.16.0 — 2026-08-19 09:56 PDT

**A tip jar, in About and on the repo page.** CLIque is free and staying that
way; this is for anyone who wants to say thanks.

- Buy Me a Coffee, plus BTC, SHIB and DOGE addresses.
- **Addresses are shown in full and wrap** rather than truncating, with one
  click to copy. A half-shown chain address is worse than none at all, because
  someone will copy what they can see — and a wrong address does not bounce,
  it just loses whatever was sent.
- The same list is in the README, and `.github/FUNDING.yml` puts the coffee
  link behind GitHub's own Sponsor button for when the repo goes public.

## 0.15.1 — 2026-08-19 09:50 PDT

**The About tab in settings was invisible.** Seven tabs did not fit the sheet,
the strip scrolled horizontally, and its scrollbar was hidden by design — so
the last tab was off the edge with nothing on screen to suggest it existed.

The strip wraps now instead of scrolling. Costs a second row on a narrow sheet,
and it is the same fix a phone needs anyway.

## 0.15.0 — 2026-08-19 09:43 PDT

**A session you just started is now the first thing you see.** It was being
filed into a folder automatically by its directory, which meant a new session
could appear anywhere in the tree — or, if a folder rule matched, somewhere you
were not looking at all.

- New sessions land in **Ungrouped** unless you pick a folder when you create
  them. Filing is a decision made afterwards, if at all.
- **Ungrouped now sits above the folders**, not below them.
- Auto-filing by directory stays where it earns its keep — **adoption**, where
  there is nobody to ask.

**The right-click menu names what is actually there to destroy.** A session
whose process ended long ago still offered *Kill*, which asked you to confirm
stopping something that had already stopped, and offered nothing for the thing
you probably wanted: the row gone. Stopped sessions now offer **Delete**, with
wording that says what it does. Running ones are unchanged.

## 0.14.0 — 2026-08-19 09:40 PDT

**Every binding, in one list.** Shortcuts existed only in `title` attributes,
which meant they existed only for whoever already knew they were there.

- `?` in the tab bar, `Ctrl`/`Cmd`+`Shift`+`/`, or **Keyboard shortcuts** in
  the palette.
- Grouped by what you are doing: getting around, inside the palette, reading a
  pane, working in a session.
- Written from one table in the source, so a binding that ships without a line
  here is a visible omission rather than a silent one.
- It says where a key is not ours to promise: the autonomy-mode key is
  whatever that CLI declares in the registry, so it differs between them.

## 0.13.0 — 2026-08-19 09:35 PDT

**The view no longer moves while you are reading it.** On a busy session, a
line arriving mid-sentence dragged the pane to the bottom — and you cannot
read, or copy, what will not hold still. Two of the five feature lists ranked
this first outright.

- **Scroll up and the viewport detaches.** No toggle to find first: the thing
  you already do in order to read is the thing that stops the yanking.
  Returning to the bottom re-attaches.
- A **badge over the pane** says it is paused and how many lines have arrived
  since — a detached pane and a dead one otherwise look identical. Click it to
  catch up.
- A lock button in the tab bar, `Ctrl`/`Cmd`+`Shift`+`L`, and a palette entry,
  for pausing deliberately rather than by scrolling.
- Per session, so pausing one pane to read it leaves the other eleven running
  and following.

**The stream is never paused, only the view.** Output keeps arriving and tmux
keeps the scrollback; the viewport is put back after each write instead.
Freezing the stream would have put the pane out of step with tmux, which holds
the real scrollback and does not care what a browser is looking at.

## 0.12.0 — 2026-08-19 09:23 PDT

**You can paste a screenshot into a session.** A terminal cannot carry an
image, so the only thing that can cross into a pane is text — which meant
showing an agent what you were looking at involved saving the file yourself,
finding its path, and typing it out.

- `Ctrl`/`Cmd`+`V` with an image on the clipboard saves it into the session's
  own working directory, under `.claude-images/`, and puts the path where you
  were already typing. Nothing is sent on your behalf: the CLI does not see it
  until you press enter.
- **Text paste is untouched.** The handler only claims the event when the
  clipboard actually holds an image; everything else goes through to the
  terminal as before.
- It knows nothing about any CLI. Every coding agent can already read a file
  from a path, so this needed no registry entry and no per-CLI branch.
- The path lands in the pane, or in the prompt box if that is where the caret
  was — or if the pane's socket is down, because a path that silently
  disappears is worse than one in the wrong place. A toast says which.

**What it refuses.** The type is established from the file's own leading bytes,
never from a filename or a `Content-Type` the browser supplied — this route
writes into a working directory, so what the bytes are has to be settled
server-side. PNG, JPEG, GIF, WebP and BMP are recognised; anything else is
rejected. Requests are capped at 10 MB, bodies are bounded before they are
read, names are random and never overwrite, and the write is contained to
`<cwd>/.claude-images` after symlink resolution.

## 0.11.0 — 2026-08-19 07:33 PDT

**The autonomy pill was static.** It showed whatever mode a session started in
and never moved again — not when you clicked it, not when you cycled the mode
yourself. Clicking sent the key to the CLI and then forgot to write down what
it had just done.

- Clicking the pill cycles it, and the change is remembered.
- **Cycling by hand in the pane moves it too.** The key the registry declares
  is now watched coming *out* of the keyboard as well as sent *into* the pane,
  so the pill cannot drift out of step with what you did yourself. The key is
  passed straight through — this observes it, it does not intercept it.
- The label is the CLI's own wording from `mode_label`. It was hardcoded to
  Claude Code's "(shift+tab to cycle)", which read as a lie on Gemini.
- The mode persists, so a reload does not reset it and it follows you to
  another device.

**What the pill knows, and does not.** It tracks the mode CLIque last saw set —
by a click, or by the cycle key going through the pane. It does not read the
CLI's real state, because that means parsing someone's terminal output, which
is the line this project does not cross. Those are the only two ways the mode
moves, so in practice they agree.

**How to get it for another CLI:** four lines in `config/clis.toml`, no code —
documented in the README under *Adding a CLI*. Only Claude Code and Gemini
declare modes today. The rest of the registry is a catalogue and none of them
is installed here, so nobody has verified which key cycles what, and a pill
that names the wrong mode is worse than no pill.

## 0.10.1 — 2026-08-19 07:20 PDT

**Fixed: Running and Ungrouped could not be collapsed.** Clicking their headers
did nothing at all. Collapsing was written to flip a flag on a folder record,
and those two are views over the sessions rather than folders — there is no
record to flip, so the click fell through a guard and returned silently.
Archived worked only because it had its own one-off boolean.

All three now share one mechanism, and it lives in the browser rather than on
the server. Same rule as sidebar width: which *folders* you keep shut is about
the work and follows you between devices; which *views* you keep shut is about
this screen and stays here. Archived still starts closed, since being out of
the way is the entire point of it.

## 0.10.0 — 2026-08-19 07:17 PDT

**The stats bar is a gauge now.** Every reading carries a dot that moves along
a green-to-red ramp as the box works, so a glance says how hard it is
breathing without reading five numbers.

- Continuous, not three fixed steps. The ramp bends at green→amber over the
  first 70%, because that is where most of the interesting range lives — a box
  at 40% and a box at 65% should not be the same shade of "fine".
- Deliberately **not** a theme token. Green-to-red is not decoration, it is
  the one colour convention that means the same thing to everyone, and a theme
  recolouring it would be repainting the gauge rather than the panel.
- Load is graded against core count, disk against how full it is, and swap
  starts at amber the moment any is in use — "a little swap" is not a healthy
  reading, it means memory pressure already happened.
- The old amber text is gone. The dot says the same thing more precisely, and
  two marks for one fact is the pattern 0.9.1 removed from sessions.

**Folders are easier to work with.**

- The collapse triangle is half again as wide and sized to be aimed at, rather
  than sized to match the label's type.
- **A pencil appears on hover** to rename, recolour or delete a folder.
  Right-click always did this and nothing ever said so; the pencil is the same
  menu with a way to find it. It holds its space whether or not it is showing,
  so the counts do not jump sideways as the pointer moves down the tree.
- Only real folders get it. Running, Ungrouped and Archived are views over the
  sessions, not things with a name and a colour.

## 0.9.2 — 2026-08-18 21:53 PDT

**Changing the theme now repaints the open terminal immediately.** It was
waiting for the next three-second poll — and with the settings sheet still
covering the pane you were looking at, that reads as "changing the theme did
not change the terminal". Applied on save now: 0.2s instead of up to 3.

This is the second half of the terminal-theming problem. The first half was
0.7.0, where every theme was missing the eight *bright* ANSI colours that CLIs
lean on hardest — so the background changed and the output did not. Both
halves verified in a real browser this time: one pane, no reload, Dracula to
Trinity, every colour following.

## 0.9.1 — 2026-08-18 21:32 PDT

**One mark per session, and it is the CLI's logo.** Showing a status dot *and*
a logo was two marks for one session; the logo now carries the status colour
by default and pulses while the session is working.

- A logo with its own colours cannot be tinted — used as a mask it flattens to
  a solid square — so those wear the status as a **ring** instead. Previously
  they fell back to showing the dot as well, which was the exact thing this
  was meant to stop.
- The plain dot still returns where a session has no marker at all: a CLI set
  to **None**, or markers turned off. Losing status entirely is the worse
  trade, and that has not changed.
- Turn it off in Settings → CLI markers → Status to go back to a dot beside
  the logo.

**The favicon is the logo again.** It had been drawing only the large chevron,
on the grounds that both turn to mud at 16px. True, and beside the point: a
tab icon that does not match the one in the window is worse than a slightly
busy one. Both chevrons, a third heavier, no tile — the tile's rounding is
what was eating the space.

## 0.9.0 — 2026-08-18 21:29 PDT

**Your history is in the sidebar, in its folders.** Past conversations now sit
under the live sessions in each folder — dimmed, because they answer "where did
I leave that" and must never compete with "what is happening now" — with the
project and the age, and a click resumes one. 266 conversations, filed by the
directory they belong to.

- **Repeats are folded.** A scheduled agent writes one transcript per run under
  an identical opening line; thirty-seven rows reading "You are the unattended
  responder…" is thirty-seven rows of nothing. The newest is kept and the rest
  become a ×37 next to it.
- **Folder headers count both**, live sessions and the history behind them, so
  a folder holding two hundred conversations and no running session no longer
  reads as empty.
- Six per folder, then "N more from history". Everything expands at once only
  when you search.
- Titles prefer the CLI's *own* name for a conversation where it wrote one —
  "Analyze Duchamp room rates emails" beats the first eighty characters
  somebody typed. Still no summarising: it reads a field that already exists,
  or the opening line, and stops.
- Turn the whole thing off in Settings → Appearance → Sidebar. `Ctrl`+`K` then
  `~` still searches every one of them.

**Tabs reorder by dragging.** A line marks the edge the tab will land against
rather than reflowing the bar under the pointer, which is harder to aim at.
The order is the browser's, like sidebar width, and rides along with the
open-tab list that already survives a reload.

**Fixed: the app could open to an empty sidebar and fill in three seconds
later.** Python's default listen backlog is five connections, and one page load
is well past that — document, stylesheet, three scripts, brand mark, favicon,
manifest, `/api/state`, `/api/resumable`, and a WebSocket upgrade. The overflow
was dropped. The backlog is 128 now, the opening fetch retries quickly instead
of waiting out the poll, and a panel that really is unreachable says so rather
than showing an empty app, which from the outside is indistinguishable from a
broken one.

## 0.8.0 — 2026-08-18 21:17 PDT

**Every past conversation, findable and resumable.** 266 of them on this box,
discovered in under half a second.

Switching tools should not cost anyone their history. Every CLI worth driving
already writes its transcripts to disk, and the registry already knew the argv
that resumes one — the only missing piece was finding them.

- **Registry-driven, not a Claude branch.** A CLI declares where it keeps its
  transcripts in `config/clis.toml`:

      [cli.claude.history]
      dir     = "~/.claude/projects"
      layout  = "dashed-dir"
      pattern = "*.jsonl"

  Omit the block and the CLI simply has no history, which is the right answer
  for `shell`.
- **Labelled by the first thing you typed**, read from the head of the
  transcript and nothing more. Understanding a conversation would be the
  LLM-summary trap wearing a different hat; the first human turn is free and
  is what you actually remember it by.
- **`~` in the palette** searches them. They stay out of an empty `Ctrl`+`K`
  on purpose — hundreds of old conversations would drown the twenty sessions
  you actually switch between — and join the pool as soon as you type.
- **Resuming is the same code path as starting.** The registry hands back the
  resume argv instead of the launch argv, and the new session lands in the
  folder its directory belongs to. Nothing in CLIque knows what resuming means
  for any particular CLI.
- Discovery is a directory walk plus a bounded read per file, cached, and runs
  when asked rather than on the three-second poll.

**Also:** a new session now files itself into the right folder automatically,
the same way an adopted one does. It only did that for adopted sessions.

## 0.7.0 — 2026-08-18 21:11 PDT

Four things found by actually looking at the running app rather than at the
tests, which all passed throughout.

**Adoption now produces sessions you can use.** It filed five live Claude Code
sessions as plain shells, named after their directories, in no folder.

- The CLI is detected from the pane's **process tree**, not from
  `pane_current_command`. A CLI started from a wrapper or a login shell is a
  child of that shell, so the pane honestly reports `bash` — and `bash` is a
  registered command, so trusting the pane first was wrong every time.
- Names come from the tool being replaced, where it recorded them. A sidebar
  of "mark", "agent-infra", "testcase" is not a migration.
- Sessions land in the folder their directory belongs to.
- **Adopt is now safe and useful to run twice.** It repairs sessions adopted
  before detection improved, instead of skipping anything already known. A
  name you typed yourself is never overwritten — only one CLIque derived.

**The terminal no longer opens as a small pane in a sea of dots.** A tmux
window has exactly one size, shared by every client on it, and `window-size
latest` follows the most recently *active* client — which a browser that has
just resized is not. With another tool still attached to an adopted session,
that left most of the viewport filled with tmux's dot padding. The browser now
sets the window size explicitly, on attach and on every resize. The comment
claiming grouped sessions get independent sizes was wrong and has gone.

**Themes now reach the terminal properly.** Every theme set a background and
the eight base ANSI colours; none of them set the eight *brights*, which CLIs
lean on constantly — so a themed panel wrapped output drawn in xterm's default
palette. All eight themes now carry the full sixteen, and the built-in dark
carries VS Code's Dark+ palette it was always meant to match.

**One theme now covers every surface.** White text on the accent, black modal
scrims and black shadows were hardcoded, so light themes got a black wash and
a pale accent got unreadable text. They are tokens now — and derived from the
theme in code rather than added as three more keys every theme must remember,
so adding a theme is still one block. The sign-in page follows the browser's
light/dark preference: which theme is chosen is not an answer to hand someone
who has not signed in.

**A new theme: Trinity.** Green phosphor on black. Red stays red and yellow
stays yellow — a terminal where an error cannot be told apart from a diff is a
costume, not a theme — and everything not carrying meaning leans green.

**Also:** brand images, the favicon and the web app manifest are served before
sign-in. The login page asked for a password and then tried to draw a logo
that was behind that password.

## 0.6.0 — 2026-08-18 20:48 PDT

**A real identity.** The brand assets until now were CodemanPanel's, borrowed
wholesale — one of them still carried `aria-label="CodemanPanel"`.

The mark is two chevrons: a large one with a smaller one tucked into its
opening. A prompt, and then a second prompt — many CLIs, one place. Drawn from
[the original](docs/brand/original-mark.jpg), traced, and then regularised so
the arms are symmetric and the proportions are exact.

- Violet `#A855F7` to cyan `#22D3EE` on near-black. Developer tooling is
  almost uniformly blue; this reads as itself in a crowded tab strip, and both
  ends stay legible on light and on dark.
- **Everything is generated** by `tools/make_brand.py` from one definition of
  the geometry. The SVGs are written from it and the PNGs are *drawn* from the
  same numbers rather than rasterised from the SVGs — so there is no renderer
  to install and no way for vector and raster to drift apart.
- Sixteen files: tiled logo, bare mark, monochrome mark, lockup, eight raster
  sizes, an Apple touch icon, an Android maskable icon, a multi-size `.ico`,
  and GitHub's 1280×640 social preview.
- The 16px icon is not the 512px one shrunk. It drops the tile and the second
  chevron and thickens the stroke by half, because at that size the full mark
  is mud and a true-weight stroke is a wire outline.

**Installable on a phone.** A web app manifest, `theme-color`, and the iOS
meta tags ship with the icons — so CLIque can be added to a home screen and
opens standalone. The layout itself is not responsive yet; that is the next
piece of the mobile work, and it is on the list.

## 0.5.1 — 2026-08-18 20:35 PDT

**Fixed: signing in landed on a white screen.**

The Content-Security-Policy added in 0.3.0 shipped as a bare
`script-src 'self'`, which blocks *every* inline script. Three of them are
load-bearing here — the one that resolves the mount path on the app page, the
same on the login page, and the redirect on the page you land on after signing
in. All three were silently blocked. The landing page is a blank document whose
only content is that script, so a successful login rendered a white screen.

Nobody hit it for two releases because nobody signed in: the existing cookie
stayed valid across every restart. Renaming the cookie in 0.5.0 forced the
first real login since the policy shipped, and the bug surfaced immediately.

- Inline scripts now carry a **per-response nonce** that the policy names.
  Hardening is unchanged — an injected `<script>` still cannot run, because it
  cannot guess a nonce minted for that one response.
- Hashes were the alternative and would have been worse: editing a comment
  inside one of those scripts would blank the app without a word. That is
  close to how this was found.
- The smoke test now asserts that every inline script on both pages carries a
  nonce the header actually allows. It could not catch this before, because
  curl does not enforce CSP — the suite stayed green while the app served a
  blank page.

## 0.5.0 — 2026-08-18 20:29 PDT — **now CLIque**

*Your private clique of CLIs.*

muxpanel described the mechanism; CLIque describes what it is for. The rename
goes all the way through — package, module, socket, data directory, cookie,
environment variables, systemd unit and the tailnet path — rather than
stopping at the visible strings, because a half-rename is worse than either
name.

**Nothing running was lost, and that was the constraint the migration was
built around.**

- Sessions record the tmux socket they were born on, so the ones started
  before the rename keep running on the old server and keep working. Moving a
  session between tmux servers is not possible; not needing to is the design.
- Any stray pre-rename session that is *not* in the panel's own state can be
  taken over with **Adopt** — the old socket is now scanned as a foreign one,
  which is exactly what it has become.
- The browser's own state — sidebar width, collapsed state, open tabs — is
  carried across once on first load. Reopening to a default sidebar and no
  tabs is the moment a rename feels like a breakage.
- **Served at `/clique` behind the tunnel.** The old `/mux` path still
  resolved, so an existing bookmark would not 404.
- The password moved with everything else, to `$CLIQUE_HOME/password`.

## 0.4.1 — 2026-08-18 20:24 PDT

The palette's "most recently used" order now lives on the server with the rest
of the settings, so it is the same order on the desktop, on another machine,
and on a phone. It was per-browser for one release, which meant a new device
started with the ordering of a stranger.

The rule this follows, and it is worth writing down: **anything about the work
syncs, anything about the screen stays local.** Which session you were last in
is about the work. Sidebar width is about the screen — a 420px sidebar that
suits a desktop is wrong on a laptop and absurd on a phone — so that one stays
in the browser where it belongs.

## 0.4.0 — 2026-08-18 20:22 PDT

**A command palette.** `Ctrl`+`K` opens one box that reaches every action and
every session. It is the feature three independent LLM feature lists ranked
first, and the reason they gave is the right one: numbered tabs work at five
sessions and become friction at thirty, and without a palette every feature
added from here arrives as one more button in the chrome.

- Type nothing and it is a session switcher, **most-recently-used first**, so
  `Ctrl`+`K` `↵` is "back to the one I was just in".
- `>` narrows to commands, `@` to sessions — VS Code's prefixes, because
  muscle memory is the point.
- Fuzzy matching that scores *where* a match lands: the start of a word beats
  the middle of one, and adjacent letters beat scattered ones. Only the title
  is highlighted; marking letters inside a directory path reads as damage.
- Commands cover what was previously button- or right-click-only: new session,
  new folder, adopt, settings, sidebar, history, rename, archive, copy working
  directory, close tab, close every tab, kill — plus every theme, every
  appearance and every snippet, live.
- `Ctrl`+`Shift`+`P` opens it straight into commands.
- Escaping out gives focus back to wherever it came from, including the pane
  you were typing in.

**The pane keeps the keyboard, except for this.** `Ctrl`+`K` is readline's
kill-to-end-of-line, so it is taken from the terminal explicitly rather than
by accident — and it can be handed back in Settings → Appearance → Keyboard,
where `Ctrl`+`Shift`+`P` still opens the palette either way.

Also in this release:

- New panes are built with the active theme already applied. Under a light
  theme, opening a session used to flash a black pane for a moment.
- The context menu's session actions became named functions, so the palette
  runs the same code rather than a second copy of it.

## 0.3.1 — 2026-08-18 20:07 PDT

Real logos, drawn properly. Icons are now classified automatically into two
kinds, because they cannot be drawn the same way:

- A **glyph** — one colour on transparency — stays a CSS mask, so the panel
  supplies the colour and one file serves the tinted, grey and status-coloured
  modes.
- A **badge** — a logo with its own background or several colours — is drawn
  as a real image. Used as a mask it flattened to a solid square, which is
  exactly how Cline and OpenCode were rendering.

Gemini and Cursor gained their true multi-colour marks, and the tintable ones
now carry their real brand colours rather than approximations.

Detection is by inspecting the file, not by a flag in config, so dropping a
new icon into the directory does the right thing without anyone classifying it
correctly.

## 0.3.0 — 2026-08-18 18:52 PDT

Security pass, driven by reading Codeman's hardening documentation and
comparing it line by line. Four real gaps, one of them serious. Full model now
written down in [SECURITY.md](SECURITY.md).

- **Cross-Site WebSocket Hijacking is closed.** A WebSocket handshake is not
  subject to CORS and `SameSite=Lax` does not cover it, so any page you visited
  could have opened a socket to this origin and had the browser attach your
  session cookie — a live root terminal for a hostile page. The upgrade now
  validates `Origin` before completing.
- **DNS rebinding is closed.** A `Host` allowlist runs before auth and before
  any handler. Loopback, IP literals, `.ts.net`, common tunnel providers, plus
  anything in `CLIQUE_ALLOWED_HOSTS`.
- **Login throttling no longer locks out the legitimate user.** Behind a tunnel
  every request arrives from the same loopback address, so a per-IP lockout hit
  the only real user along with the attacker. A correct password now always
  gets through.
- **Content-Security-Policy** added. Everything is served from this origin, so
  a strict policy was free.
- **The password is stored as an scrypt hash.** The server only verifies, so
  keeping the plaintext bought nothing. Set it with
  `python3 -m clique password`. Keep the plaintext in a password manager,
  not next to the hash.
- **API tokens hot-reload.** Minting an agent no longer needs a restart, and
  more importantly, revoking one takes effect immediately — a revocation that
  waits for a restart is not a revocation.

## 0.2.3 — 2026-08-18 18:45 PDT

The CLI icon can now carry the status colour, so a session shows one mark
instead of two: shape says which CLI, colour says how it is doing. Off by
default; the toggle is in Settings → CLI markers. The separate dot returns
automatically wherever there is no icon to carry it.

Fixed while testing it: "someone is watching this" was never true. Each browser
attaches to a grouped viewer session rather than the session itself, so the
session's own client count was always zero and every session sat permanently on
the unwatched colour. Viewer attachment is now folded back into the session it
mirrors, so green and amber mean something again — a regression that had been
invisible while a dot nobody looked at was carrying it.

## 0.2.2 — 2026-08-18 18:42 PDT

Draggable sidebar. Grab the edge, or focus it and use the arrow keys;
double-click resets. The width is remembered per browser and comes back when
the sidebar is collapsed and reopened.

Stored in localStorage rather than server settings, unlike everything else in
the settings sheet: a 420px sidebar that suits a desktop is wrong on a laptop
and absurd on a phone. This is the one preference that is about the screen
rather than about the person.

Real vendor marks for eight more CLIs — OpenCode, Goose, Factory Droid, Cline,
plus Codex, Copilot, Cursor and Qwen from simple-icons. Twelve of sixteen now
have a real icon. Aider publishes a wordmark rather than a mark and three
others have no icon at a stable URL, so those four keep the letter badge,
which is documented as a choice rather than left looking like a gap.

## 0.2.1 — 2026-08-18 18:36 PDT

The settings dialog is now furniture: fixed 560×640, same position, every tab.

It used to size itself to whichever tab was open, so switching resized and
re-centred it — the content jumped under the cursor and the tab strip moved out
from under the pointer that had just clicked it. Now the sheet is a fixed-height
column, the header and tab strip are fixed rows, and only the pane area
scrolls. The scrollbar gutter is reserved whether or not it is showing, so a
short tab and a long one lay out identically instead of shifting by its width.

The per-CLI rows became a grid rather than a flex row. The select was squeezing
the label column, so names wrapped onto two lines and every row was a different
height. Sixteen rows, all 32px, selects aligned to the pixel.

## 0.1.0 — 2026-08-18 17:18 PDT

First working version, replacing Codeman for day-to-day use.

- tmux session engine on its own server, with a CLI type registry so adding a
  coding CLI is a config block rather than a code change.
- Stdlib HTTP server, JSON API and a hand-rolled WebSocket for live terminals.
  A PTY is created only while a browser is watching it.
- Sidebar (folders, drag-drop, rename, search, rail), numbered tabs, xterm.js
  terminal, input bar with mode pill and Run/Shell split, stats readout.
- Password gate, mandatory: this serves a terminal running as root.
- Published on the tailnet at /mux, under systemd.

Fixed before release:

- Each browser now attaches through its own grouped tmux session. Sharing one
  meant tmux sized the pane to the smallest client — which would have resized
  an adopted session under the tool still running it.
- Scrollback is captured history-only; tmux redraws the visible frame on attach
  and sending it too printed the last screenful twice.
- `[hidden]` did nothing against an id selector that set `display`, so the
  new-session modal covered the whole app from page load.
