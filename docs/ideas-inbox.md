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
   Tracked in Nextcloud.

Refused for now: a Prometheus `/metrics` endpoint. It is a third format for
the same numbers `/healthz` already returns, and nobody has asked.

---

## Elsewhere

Operational and product work that is tracked but does not belong in the
ranked roadmap lives in the **CLIque** list in Nextcloud Tasks — monitoring,
the vault entry, public-repo assets, the marketing site.
