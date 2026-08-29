# Ideas inbox

Raw, unranked, and **not** a commitment. Things worth a conversation before
they earn a place in [ROADMAP.md](../ROADMAP.md).

The roadmap is ranked by convergence across five independent feature lists and
is the plan. This file is the inbox in front of it, so an idea mentioned once
in passing is not lost and does not quietly become a priority either.

---

## Usage stats, and an opt-in anonymous share

Raised 2026-08-21. Two products, not one: **your own nerd dashboard** (always
local), and **optional anonymous rollups** to us so the product is not steered
by one box.

The rule is the same as the rest of CLIque: **counts and clocks, never the
words.** No prompt text, no pane contents, no paths, no names. The site
currently says "no telemetry" — that claim has to become "nothing leaves
unless you turn it on", with a preview of the last packet.

**Interesting and honest** (tmux + the panel, no vendor API):

- Sessions started / still alive / killed; tmux lifetime vs time anyone was
  looking
- Peak concurrent agents; hour-of-day and day-of-week
- Mix of CLIs actually used, not just installed
- Prompts sent (count and length buckets, not the text)
- Time working / waiting-on-you / idle — we already classify this
- Times it asked, times it errored, how long until you answered
- Image pastes, artifacts made, paths glanced
- Phone vs desktop, version, OS — so we know what to keep supporting

**Share:** off by default. Checkbox with the exact JSON shown first. Random
install id created on opt-in, rotatable, deleted when they turn it off.
Daily rollup POST over HTTPS, not a stream of events. Self-hosters and paid
users use the same packet; our ingest is a boring authenticated endpoint with
its own key, budget, and a monitor.

Refused: prompt/pane text, raw cwd, keystroke logs, anything that needs to
know which AI vendor is talking.

---

## Borrowed from the VS Code / terminal ecosystem

Raised 2026-08-19. The observation behind it is sound: the extensions people
keep installed for years cluster into four buckets — **AI help, git,
lint/format, and see-it-without-hunting** — and CLIque is well placed for the
fourth and badly placed for the others.

**Fits the product** — these are "see it without hunting", which is what a
driver is for:

- *Error Lens* → surface a CLI's errors where you are looking, not in a panel
  you have to open. Feeds directly into the planned **error attention state**.
- *Todo Tree* → collect TODO/FIXME across a session's working directory.
- *GitLens*-ish, in the smallest possible dose → branch and dirty state in the
  sidebar (already on the roadmap). Blame and history are not ours.
- *Better Comments*, *Indent Rainbow*, *Color Highlight* → the terminal is the
  CLI's canvas, but the **prompt box** is ours and could highlight structure.
- Icon packs → the per-CLI marker system already does this job.

**Fits as themes, cheaply** — one block in `themes.js` each:
One Dark Pro, Night Owl, Catppuccin, Everforest, Rosé Pine.
(Dracula, Nord, Gruvbox, Tokyo Night, Solarized and Trinity already ship.)

**Belongs in the session, not in CLIque** — the terminal stack people dress up
is `starship`, `zsh-autosuggestions`, `bat`, `eza`, `fzf`, `zoxide`, `delta`,
`lazygit`, Nerd Fonts. All of it is per-user shell configuration and works in
a CLIque pane already. The one thing CLIque owes it: **ship a Nerd Font**, or
document how to point the terminal at one, so prompt glyphs are not boxes.
Worth doing, small, and currently the only real gap.

**Deliberately not ours** — Prettier, ESLint, Live Server, REST clients, path
IntelliSense, Copilot-style inline completion. These are editor features. The
agent in the pane already does them, and reimplementing them is the
"driver, not an IDE" line in the roadmap.

**Security note worth keeping:** the VS Code marketplace has shipped malicious
"AI assistant" extensions that exfiltrate workspace files. If CLIque ever
grows plugins, that is the failure mode to design against from the first
commit — not after.

---

## Talking to the rest of a self-hosted box

Raised 2026-08-19: make CLIque easy to wire into Uptime Kuma "and others".

**The shape matters more than the list.** A self-hoster runs some unguessable
combination of Uptime Kuma, Gatus, ntfy, Gotify, Home Assistant, Discord and
Mattermost, and per-service integrations means a settings sheet full of logos
and a permanent queue of "please add mine". Two generic things cover all of it:

1. **`/healthz`** — shipped in 0.20.0. Any monitor that can watch a URL can
   watch CLIque, with no credential to configure.
2. **One outbound webhook** on session events — died, finished, wants input.
   Uptime Kuma's push monitors, ntfy, Gotify, Discord and Mattermost are all
   "POST some JSON to this URL", so one field serves every one of them.

Refused for now: a Prometheus `/metrics` endpoint. It is a third format for
the same numbers `/healthz` already returns, and nobody has asked.

---

## Computer use, and the half of it that is ours

Raised 2026-08-19, from a thread where someone moving between CLIs said the
one thing they still miss is **computer use**.

**Not ours, and never will be.** Driving a screen needs a vision model, a
click channel, and knowledge of whose tool schema is on the other end — all
three repo rules at once, and the 24 MB with them. The CLIs already have it:
Claude Code ships a browser extension, anything with MCP can load Playwright.
CLIque is not going to re-run that race and would lose it.

**Half of it is ours, though, and it is small.** What a terminal cannot do is
show a picture. Paste already works in one direction — `Ctrl`/`Cmd`+`V` puts
your screenshot in the session's directory and hands the agent the path. The
reverse does not exist: when an agent writes a PNG — a browser screenshot, a
rendered chart, a diff image — you have to leave the panel to look at it.

So: **an artifact strip.** Images that appeared in a session's working
directory, newest first, click to enlarge. Filesystem state only, so rule 1
stays clean and no vendor has to be understood. It is the cheapest honest
answer to "I want to see what it did".

Open question before building — what counts as an artifact:

- Watching the whole working directory is noisy in any repo full of assets.
- A `.clique/` drop directory is precise, but something has to tell the agent
  it exists.
- Modified-since-session-start across a couple of conventional directories
  needs nobody told anything, and will occasionally show you a favicon.

---

## Usage and cost

Raised 2026-08-19: a panel showing what the connected CLIs are costing —
subscription state, when a quota resets, and for anyone on OpenRouter or their
own API keys, real spend.

This is three different features wearing one name, and they are not equally
possible. Splitting them is the whole design.

### 1. Tokens and cost from the transcripts — buildable, and rule-clean

The CLIs already write it down. Every assistant turn in a Claude Code
transcript carries `usage` (input, output, cache-creation, cache-read,
thinking) and the `model` that produced it, and those files are the same ones
the History feature already reads. Cost is arithmetic over a price table, not
a question anyone has to be asked.

No vendor API, no key, no account, nothing to log into. It is filesystem
state, which is the only kind this product reads. Per session, per folder, per
day, per model — all of it falls out of files that are on the disk already.

The price table is the honest weak point: prices change, and a stale one
quietly reports the wrong number. It ships as data, dated, with the date shown
next to the total.

### 2. Subscription state and reset times — refuse until it is real

There is no supported way to ask "how much of my plan is left". A CLI that
shows you knows because it is the vendor's own client talking to its own
service. Getting at it from outside means an undocumented endpoint or scraping
a UI, which is precisely the vendor coupling the product exists to avoid — and
it would break on a release nobody warned us about, in a panel whose whole
promise is that it works with a CLI that ships next week.

If a vendor publishes a real endpoint for it, revisit. Until then, saying "we
cannot see this" is better than a number that goes wrong silently.

### 3. OpenRouter and your own API keys — opt-in, and it changes the threat model

Different category, and worth being precise about why it is allowed. This is
not reverse-engineering a protocol to drive an agent; it is a dashboard
reading an account the person deliberately connected. OpenRouter publishes
credit and generation endpoints, and a key the user pastes in is a key the
user chose to paste in.

The cost is not technical. **Today, a compromised CLIque gets an attacker a
shell as the user who started it** — which is stated plainly in the README and
is bad enough. Storing API keys means it also holds credentials to paid
accounts, and "someone got a shell" becomes "someone got a shell and your
billing". That is a real step up in what a break-in is worth, and it has to be
a deliberate, off-by-default, clearly-worded choice — not a settings row that
looks like every other settings row.

If it ships: keys never leave the server, never appear in `/api/state`, are
never rendered back into the page, and are stored where the password hash and
session secret already live.

### The order

1 first, because it is most of the value and costs nothing but arithmetic.
3 only if people ask, and only with the warning written before the feature.
2 not at all, until a vendor makes it possible honestly.

---

## Making the terminal feel fast

Raised 2026-08-19, from comparing against Ghostty and WezTerm — not
competitors, but the same "does this feel fast" complaint applies.

**Verified in the repo first, because none of this should be assumed:**
xterm.js is vendored as a single 478 K built file with the fit addon (1.5 K)
beside it. The renderer is xterm's **default DOM renderer** — no WebGL addon
is loaded. There is **no scrollback search**. `Ctrl`/`Cmd`+`K`, `Ctrl`/`Cmd`+`B`
and `Escape` are taken; `Ctrl`/`Cmd`+`F` is not.

**The constraint that governs all three.** Anything added here is vendored the
way xterm.js already is — the built browser file, copied in, version recorded
in the commit that adds it. Never npm, never a bundler. If licensing makes
that awkward, a clean-room reimplementation is the fallback; a build step is
not. The zero-dependency claim is worth more than any one of these.

### 1. WebGL renderer — cheapest, do first

xterm's WebGL addon is a near drop-in swap and noticeably faster on heavy
scrollback and fast output, which is exactly what an agent dumping a build log
produces.

The part that needs care is not turning it on, it is turning it off again:
a WebGL context can fail to initialise and can be *lost* later on low-power
devices and mobile Safari. It needs a real fallback to the canvas/DOM renderer
on both `contextloss` and a failed init — not an exception in the console and
a blank pane. Needs checking on an actual phone, which is also the moment to
confirm the terminal itself still renders there even though the surrounding
layout is admittedly unfinished.

### 2. Scrollback search

xterm has a search addon; same vendoring. A small bar in the existing visual
language — nine presets, light/dark/system and three CSS slots all have to
keep working, so it cannot be a one-off styled element. Forward and backward
through matches, a case toggle, `Escape` to close, and `Ctrl`/`Cmd`+`F` is
free.

### 3. Local echo — the one that actually fixes "laggy"

The real fix, because the product's whole case is reaching a self-hosted box
from somewhere else over a real network. Typed characters currently wait for a
full round trip.

**Codeman published exactly this as `xterm-zerolag-input`** — MIT, ~6 KB
gzipped, no dependencies, built so other tools could adopt it rather than
re-solve it. Their notes say two earlier attempts failed the same way: by
writing the local characters straight into the terminal, where the terminal's
own repaints stomp on them. The approach that works renders them as a **DOM
overlay above the terminal**, which is then silently replaced when the real
echo arrives.

⚠️ **Clean room.** The repo's third rule is that Codeman's code is never read
or used. A package it published for others to adopt is a genuinely different
thing from its application source — but the rule exists so the question never
has to be argued, and the safe reading is the strict one. Take the *finding*
(overlay, not direct writes; two prior attempts failed the other way), which is
public knowledge now it has been written down, and implement from the xterm.js
API. Do not read their source.

### Not to chase

The rest of Ghostty's surface — GPU shaders, the Kitty graphics protocol,
ligatures. None of it came from a complaint anyone actually had here.

---

## Steering many agents, not making agents smarter

Raised 2026-08-19. Seven proposals aimed at owning the gap between "I have one
CLI open" and "I am running twelve". Assessed against the two rules, and
against what already exists.

### Worth building

**Fleet health and soft budgets.** A live strip showing the aggregate CPU and
memory of *CLIque's own sessions* against the box's headroom, and an optional
warning before starting the one that tips it over. The README already says the
real ceiling is about a dozen on a 16 GB box — that is tribal knowledge sitting
in a paragraph, and it belongs in the interface. Pure process state; the stats
plumbing and the hour of history are already here, so this is aggregation and a
threshold, not new machinery. **The strongest of the seven.**

**The attention queue.** Three tiers of detection and a webhook shipped in
0.26/0.27, and the deep link from a notification landed in 0.28.2. What is
missing is the list: one *needs you* view, ordered, reachable from the palette,
with a snooze that clears the badge without killing anything. The parts are all
built; this is the surface that makes them a control panel rather than a set of
marks.

**Handoff packs.** One action that writes a dated folder containing the
scrollback, the artifacts from the session's directory, and a git status
snapshot. Filesystem in, filesystem out. Nobody in the category treats this as
a product feature, and both audiences want it for opposite reasons — moving
work between machines, and remembering what happened yesterday.

**The first sixty seconds.** A skippable first run that creates one folder,
launches two different CLIs side by side, and demonstrates the thing everyone
gets wrong: closing a tab does not kill the agent. Every competitor assumes
tmux and worktrees are already understood. Cheap, and it is the moment adoption
is won or lost.

### Already on the roadmap, sharpened

**"Explore this idea with three agents"** is the session-templates item wearing
better clothes — and the clothes are the point. A template that spins N
sessions across *different* CLIs on one question, in a dated folder, is a much
better pitch than "saved session configuration". Fold the framing in; do not
add a second feature.

### Where I would not go

**Read-only shared links and presence.** This is the one to refuse. The stated
security model is that anyone who reaches the panel has a shell as the user who
started it, and that honesty is load-bearing. A read-only link does not narrow
that as much as it sounds: a pane shows whatever the agent printed, which
routinely includes file contents, environment values and the occasional
credential echoed by a build. A time-limited URL is still a URL that can be
forwarded. Adding it implies a boundary the product does not have, and the
first person to learn that will learn it the expensive way. If real multi-human
work is ever wanted it is a deliberate project with its own threat model, not a
token flag.

**A folder-level context file that gets injected into first prompts.** Writing
a `NOTES.md` is something anyone can already do, and an agent can already read
it. The part that gives me pause is CLIque *injecting* it — that is the panel
having an opinion about how you talk to your agent, which is one step from the
prompt-engineering business. The useful 80% is a starter prompt on a template,
which the item above already covers.

### Restated refusals, for the record

Kanban and task claiming (occupied territory, and durable job state means a
database). Self-healing and replay loops (needs more agent knowledge than the
rules allow). Diff review and editor surfaces (driver, not IDE). Automatic
worktree *merging* — create and forget, never merge.

---

## Elsewhere

Operational and product work that is tracked but does not belong in the
ranked roadmap: monitoring, public-repo assets, the marketing site.

## Open: how far can a theme skin a CLI, and should it?

Raised 2026-08-19. **Shelved deliberately — foundation first.** Written down
because it is a design decision with evidence attached, not a bug.

Trinity is not "a green theme". It is meant to look like the Matrix, and that
intent should survive whichever CLI is running in the pane. Today it does not,
and the reason is measurable rather than mysterious.

### The ladder that decides it

How much say a theme gets is decided by the application, by how precisely it
named the colour — least specific first:

| What the CLI emits | What it means | Who owns it |
|---|---|---|
| nothing | no opinion | the theme, entirely |
| the 16 ANSI colours | "the terminal's idea of red" | the theme |
| greyscale, 232–255 | "a shade near the background" | **relative** — theme may re-tint, keeping each step's lightness (shipped in 0.43.0) |
| the cube, 16–231 | an exact colour from a fixed grid | absolute — untouched today |
| truecolor, `38;2;r;g;b` | an exact RGB | absolute, and **unreachable by anything** |

Only the last row is a hard limit. The cube is a *policy*: overriding it would
be vandalism on a theme like Nord, whose whole job is a specific palette, and
is arguably the entire point for a theme like Trinity.

### What is actually known

`tools/palette_probe.py` measures this by starting a CLI and counting escape
sequences. It can only measure CLIs installed on the machine it runs on, so
**four of the sixteen in the catalogue are measured and twelve are not**:

| CLI | 16 ANSI | greyscale | cube | truecolor | theme reaches |
|---|---|---|---|---|---|
| shell | 0 | 0 | 0 | 0 | everything (paints nothing) |
| grok | 0 | 99 | 10 | 0 | 91% |
| claude | 0 | 10 | 9 | 0 | 53% |
| gemini | 0 | 3 | 67 | 0 | **4%** |
| aider · amazonq · cline · codex · copilot · crush · cursor · droid · goose · opencode · plandex · qwen | — | — | — | — | **unmeasured** |

**None of the four measured emits truecolor**, which is the finding that
matters: every colour those four paint is reachable if a theme claims the cube.
Whether that holds for the other twelve is exactly what is not known, and it is
the thing that decides whether "a monochrome theme owns the whole palette" is a
complete answer or a partial one. A single CLI that paints in truecolor puts a
ceiling on this that no amount of palette work removes.

### The shape of the answer, when it is built

A theme may declare itself monochrome, claiming all 240 indices and mapping
each through its own hue while keeping that index's lightness and relative
saturation. Nothing per-CLI and nothing per-vendor — the transformation already
written for the greyscale ramp, applied wider, and only for a theme that asked.

The cost, which is why it is opt-in: hue distinctions disappear. Red and blue at
the same lightness become the same green, so errors stop being red. Contrast and
emphasis survive; *which colour* does not. For the Matrix that is the point. For
every other theme it is damage.

### What would move this forward

1. Run `python3 tools/palette_probe.py` on a box with more of the sixteen
   installed. Twelve unknowns is too many to design against.
2. Build the monochrome mode behind a theme flag and **look at it** — whether
   keeping lightness alone reads as the Matrix, or whether contrast needs
   pushing too, is not a question anyone can answer from a table.



---

# Codeman's feature list, read against ours (2026-08-29)

Source: the project README and its public marketing pages. **No source file was
opened, and none ever will be** (CLAUDE.md rule 3). Everything below is a
description of behaviour, which is the part that is fair to borrow.

The headline finding is not a gap list. It is that Codeman's feature set and
ours diverge on purpose, and roughly half of what it advertises is work CLIque
has already decided not to do.

## Already ours, at parity or better

Parallel tmux sessions, xterm.js with full ANSI and TUI support, several CLIs
per session declared in config, tab colour by state, notifications on approval
and idle, image paste and drag-and-drop, a file browser and viewer, a REST API
with a WebSocket terminal channel, cross-device tab order, session pinning,
loopback by default with a password, a host allowlist and CSRF guards, and a
phone drawer with an on-screen key row. Ours is 24 MB against a Node runtime.

## Genuinely wanted, and none of it breaks a rule

Ranked by what it would change on a normal day.

1. **Local echo.** Every keystroke over Tailscale costs a round trip, and their
   claim of 200 to 300ms matches what this box feels like. Draw the character
   immediately, send it in the background, and drop the overlay when the real
   echo arrives. Theirs is a positioned element inside the terminal rather than
   writing into the buffer, which is a sound instinct: a CLI that repaints its
   own screen will scribble over anything injected into the grid. We would
   write our own, in plain JS with no library, and the hard part is not the
   drawing, it is knowing when to stop drawing.
2. **QR code login.** Typing a long password on a phone is the single worst
   thing about using this on a phone. A short-lived single-use token in a QR
   code on the desktop screen removes it entirely. Small, and the auth system
   already has tokens.
3. **Live plan usage in the status bar.** Being built now. Notably this is
   *filesystem* state, not vendor knowledge, as long as the panel reads a
   declared file rather than knowing who Anthropic is.
4. **Swipe between sessions on a phone.** Tabs are unusable with a thumb.
5. **One-tap slash commands.** We have a key row; what is missing is letting a
   CLI declare its own quick commands in `clis.toml`, which is config rather
   than code and therefore the right shape.
6. **Voice dictation.** The browser's own speech API is free and needs no
   dependency. Worth a try on the phone before deciding.
7. **Auto-resume when a limit resets.** Falls out of the usage work almost for
   free, and is the one piece of autonomy that needs no model knowledge: a
   timestamp passed, so send a key.

## Wanted, but a real slice

**SSH sessions against a remote host** fits our thesis exactly, since tmux is
already the persistence layer and it would work the same way on the far end.
**A container per session** is defensible as isolation rather than as an IDE
feature, but it is a large surface and every bug in it is a security bug.

## Not for us, and the reasons already exist

Their respawn controller, orchestrator loop, phased plans with verification
gates, per-agent token counts, floating windows per subagent, auto-compact at a
token threshold, and reading a vendor's transcript files all require the panel
to know which vendor is running and to interpret what a model is doing. That is
rule 1, and [ROADMAP.md](../ROADMAP.md) already names agent orchestration a
trap for a second reason: it demos well and produces divergent states nobody
can reconcile.

Multi-user workspaces and an admin audit log are a different product. A
Cloudflare tunnel is a worse Tailscale for this estate.

## The uncomfortable one

Their autonomy features are the reason someone would pick Codeman, and we are
refusing them on principle rather than on capability. That refusal is worth
restating out loud every time this comparison is made, because "we could not"
and "we chose not to" are different sentences and only one of them is true.

## Open: two of the theme names are live trademarks

Raised 2026-08-29 when the character themes shipped, and deliberately not acted
on, because it is Justin's call rather than a technical one.

The artwork is settled. Every figure is an original design and the prompts
carry an explicit instruction keeping them that way, because an image model
asked for the archetype returns the actual character. That line is drawn and
documented above the character block in `themes.js`.

The **names** are the part still exposed. `Tetris` and `Pacman` are actively
enforced marks; `Triforce` is Nintendo's. In practice a terminal theme named
after something is common and low risk, and plenty of published palettes do it.
But this package is on PyPI under a real name, and the cost of being wrong is
asymmetric: renaming later breaks a setting people have already chosen, because
`theme` is stored as the id string.

So the cheap moment to decide is before many people have picked one. Two
honest options:

- **Leave them.** The likelihood of anyone caring about a colour scheme is
  genuinely low, and the names are what make the themes findable.
- **Rename the two or three exposed ones** and keep the ids as aliases, so a
  stored setting still resolves. Roughly an hour: a label change, an alias map
  in `currentTheme()`, and a line in `API.md`.

Not urgent, and not worth raising a third time unprompted. Worth doing before
the themes get written about anywhere.
