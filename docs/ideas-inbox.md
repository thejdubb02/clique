# Ideas inbox

Raw, unranked, and **not** a commitment. Things worth a conversation before
they earn a place in [ROADMAP.md](../ROADMAP.md).

The roadmap is ranked by convergence across five independent feature lists and
is the plan. This file is the inbox in front of it, so an idea mentioned once
in passing is not lost and does not quietly become a priority either.

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

## Elsewhere

Operational and product work that is tracked but does not belong in the
ranked roadmap: monitoring, public-repo assets, the marketing site.
