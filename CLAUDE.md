# CLIque — standing rules

Folder-organised, CLI-agnostic coding sessions in a browser, persisted in tmux.
Replaces Codeman + CodemanPanel. Python **stdlib only** — no framework, no
`node_modules`, no build step. 24 MB resident against Codeman's 253 MB, and
that gap is the product.

Docs, each with one job — read the one that matches the question:

| Question | File |
|---|---|
| What is being built next, in order | `docs/next.md` (short-lived; shipped work drops off it) |
| Why it is ranked that way, what is refused | `ROADMAP.md` |
| Raised but not committed to | `docs/ideas-inbox.md` |
| The full backlog, ops work included | the **CLIque** list in Nextcloud Tasks |
| What shipped | `CHANGELOG.md` |

## Releasing

Bump `__version__`, write the `CHANGELOG.md` entry, then run
`python3 tools/stamp_changelog.py` — it puts the wall-clock Pacific time on any
heading that lacks one, taking it from the release commit where there is one.
Skipping it leaves an entry the app renders without a time, and on a day with
ten releases the date alone distinguishes nothing. It is idempotent; run it
whenever.

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

- **Adding a CLI is config, never code.** A block in `config/clis.toml` and a
  reload. If it ever needs a code change, the design has failed.
- **tmux underneath does not mean a terminal on top.** A clean, advanced UI is
  wanted — with a switch to turn it off for anyone who wants the bare terminal
  feel.

## Where things live

`clique/app.py` routes and HTTP · `tmux.py` the session engine (socket
`clique`, sessions prefixed `sm-`) · `store.py` state · `registry.py` the CLI
registry · `web/` the front end, hand-written JS with no build.

Runs under systemd as a user unit: `systemctl --user restart clique`, port
3200, published on the tailnet. Password in Vaultwarden as *CLIque (devbox)*.
