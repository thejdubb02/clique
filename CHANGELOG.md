# Changelog

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
