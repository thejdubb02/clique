# CLIque — standing rules

Folder-organised, CLI-agnostic coding sessions in a browser, persisted in tmux.
Replaces Codeman + CodemanPanel. Python **stdlib only** — no framework, no
`node_modules`, no build step. 24 MB resident against Codeman's 253 MB, and
that gap is the product.

Docs, each with one job — read the one that matches the question:

| Question | File |
|---|---|
| What is being built next, in order | the **CLIque** board in Kaneo, one card per job |
| Why it is ranked that way, what was measured | `docs/next.md` |
| The argument, and what is refused | `ROADMAP.md` |
| Raised but not committed to | `docs/ideas-inbox.md` |
| What shipped | `CHANGELOG.md` |

## On-demand guidance (skills in `.claude/skills/`)

- **Motion** (`clique-motion`): CSS/WAAPI only, never a library; animate transform and opacity; reduced-motion ships with it.
- **After any `web/` change, and before calling a session done** (`clique-release-check`): `tools/visual_check.py`, `tools/shipped_check.py`, release steps.
- **End of any panel feature** (`clique-three-surfaces`): does Android need this, and if not, why not? Answer out loud.

## The API is the whole surface

Every action in the panel is an HTTP call — there is nothing the UI can do that
a script cannot. Keep it that way: a feature reachable only by clicking is a
feature an agent driving CLIque cannot use.

**A new route, settings key or PATCH-able field means a line in `API.md` in the
same commit.** `tools/api_drift.py` fails otherwise, so this is not a thing to
remember — but write the description properly, because the check only proves
the name is present, not that the prose is any good.

## It has to work on a stranger's machine

Every feature is designed for someone who installs the package, opens the
panel, and is off. Not for this box. If a feature only works because of
something in `/root/platform`, a cron job we run, a file another tool of ours
writes, or a key in our vault, it is not finished, and it is a trap: it will
look complete here and be dead on arrival everywhere else.

Where a feature genuinely needs a fact from somewhere else, ship the thing that
fetches it and declare the vendor-shaped parts in `clis.toml`, which is where
they already belong.

## The three rules

1. **Filesystem, tmux, and process state only** — optional git detection. If a
   feature needs to know which AI vendor is running, understand its protocol,
   or interpret what a model "thinks", it does not belong in the core. This is
   why the working indicator is built from tmux's activity clock.
2. **A driver, not an IDE.** Make it trivial to jump to the tool that already
   does the job well. Do not reimplement that tool.
3. **Clean room on Codeman.** Its *features* are fair inspiration and the
   backlog is full of them. **Its code is not — none of it, ever.** Do not
   read Codeman's source to implement something here; work from observed
   behaviour and build it our own way. CLIque wins by being smaller, faster,
   lighter and more powerful, and a lifted implementation forfeits all four.
   The same applies to any other tool we borrow ideas from, including the
   VS Code Claude Code extension.

## It has to work on a phone

Every feature, menu and settings pane is built for a phone browser and the
installed PWA from the start, not retrofitted. The overall layout is not
responsive yet — that is a known, tracked job — but nothing new should add to
that debt.

- **Overflow wraps or is visible.** Never hidden behind a scrollbar that is
  itself hidden: that is how the About tab disappeared.
- **Nothing lives only behind right-click or hover.** There is no hover on
  touch, and no right-click either — anything reachable that way needs a
  long-press or a visible control too.
- **Sheets and popovers size to the viewport** (`min(Xpx, 9Xvw)`), and touch
  targets are big enough to hit with a thumb.

## Where state lives

**Anything a person chose, chose once, or half-typed goes on the server.**
Settings, snippets, themes, folders, names, modes, drafts, and the workspace
itself — which tabs are open, in what order, which one is in front, which
view-groups are shut — they sync, they survive a reload and a closed laptop,
and they follow him to another device. Losing one is a bug, and `localStorage`
is not storage for them.

Restore the workspace on the *first* poll only, never on later ones: two panels
open at once would otherwise drag each other's tabs around mid-read.

The only things that stay in the browser are the ones that are genuinely about
*this screen in front of me*: sidebar width, sidebar shown or hidden. A phone
should not inherit a 400px sidebar from a desktop.

When in doubt it goes on the server. The test is not "is it small" — it is
"would he be annoyed to set this twice".

## Two consequences worth stating

- **Adding a CLI is config, never code.** A block in `clique/config/clis.toml`
  and a reload. If it ever needs a code change, the design has failed.
- **tmux underneath does not mean a terminal on top.** A clean, advanced UI is
  wanted — with a switch to turn it off for anyone who wants the bare terminal
  feel.

## Where things live

`clique/app.py` routes and HTTP · `tmux.py` the session engine (socket
`clique`, sessions prefixed `sm-`) · `store.py` state · `registry.py` the CLI
registry · `web/` the front end, hand-written JS with no build.

Runs under systemd as a user unit (`deploy/clique.service`):
`systemctl --user restart clique`, port 3200, loopback only. Password is an
scrypt hash in `$CLIQUE_HOME/password`.
