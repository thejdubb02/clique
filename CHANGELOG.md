# Changelog

## 0.9.1 — 2026-08-19

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

## 0.9.0 — 2026-08-19

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

## 0.8.0 — 2026-08-19

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

## 0.7.0 — 2026-08-19

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

## 0.6.0 — 2026-08-19

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

## 0.5.1 — 2026-08-19

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

## 0.5.0 — 2026-08-19 — **now CLIque**

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
- **Live at https://example.invalid/clique.** The old `/mux` path
  still resolves, so an existing bookmark will not 404.
- The password moved with everything else, to `/root/.clique/password`.
  Vaultwarden holds the only other copy.

## 0.4.1 — 2026-08-19

The palette's "most recently used" order now lives on the server with the rest
of the settings, so it is the same order on the desktop, on another machine,
and on a phone. It was per-browser for one release, which meant a new device
started with the ordering of a stranger.

The rule this follows, and it is worth writing down: **anything about the work
syncs, anything about the screen stays local.** Which session you were last in
is about the work. Sidebar width is about the screen — a 420px sidebar that
suits a desktop is wrong on a laptop and absurd on a phone — so that one stays
in the browser where it belongs.

## 0.4.0 — 2026-08-19

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

## 0.3.1 — 2026-08-19

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

## 0.3.0 — 2026-08-19

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
  `python3 -m clique password`. Vaultwarden now holds the only copy.
- **API tokens hot-reload.** Minting an agent no longer needs a restart, and
  more importantly, revoking one takes effect immediately — a revocation that
  waits for a restart is not a revocation.

## 0.2.3 — 2026-08-19

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

## 0.2.2 — 2026-08-19

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

## 0.2.1 — 2026-08-19

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

## 0.1.0 — 2026-08-19

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
