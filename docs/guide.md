# CLIque user guide

CLIque is a driver, not an IDE. It keeps a private tmux server, puts every CLI
session in a folder you can find, and gives you a browser to jump between them,
from your desk or your phone. This guide is a tour of what it can do. For the
HTTP API see [API.md](../API.md); for the security model see
[SECURITY.md](../SECURITY.md).

- [The layout](#the-layout)
- [Sessions and tabs](#sessions-and-tabs)
- [The sidebar and folders](#the-sidebar-and-folders)
- [The terminal pane](#the-terminal-pane)
- [The prompt box](#the-prompt-box)
- [The side panel](#the-side-panel)
- [Staying on top of many sessions](#staying-on-top-of-many-sessions)
- [Adopting and broadcasting](#adopting-and-broadcasting)
- [Appearance and settings](#appearance-and-settings)
- [Git worktrees](#git-worktrees)
- [The command palette](#the-command-palette)
- [Keyboard shortcuts](#keyboard-shortcuts)
- [On your phone](#on-your-phone)
- [Security in one minute](#security-in-one-minute)
- [Driving it from scripts](#driving-it-from-scripts)

---

## The layout

Five areas, left to right and top to bottom:

- **Sidebar** (left): your folders and sessions, a search box, and the settings
  and inbox controls in its header. Hide it with `Ctrl+B`.
- **Tab bar** (top): one tab per open session, with a status ring on each. A new
  session button, a follow/pause control, and an overflow button when the tabs
  do not all fit.
- **Status line** (under the tabs): the session in front at a glance, its
  process, directory, branch, how long it has been up and how long it has been
  quiet.
- **Terminal pane** (center): the live session. Below it, the prompt box.
- **Side panel** (right): a docked panel for per-session features, opened from
  the icon rail on the far right. Toggle it with `Ctrl+J`.
- **Status bar** (bottom): CPU, memory, disk, load, and how many live views are
  attached.

---

## Sessions and tabs

A session is one CLI running in tmux. It keeps running whether or not a tab is
open on it, and whether or not your browser is even awake.

- **Start one:** the `+` in the tab bar, or open the command palette (`Ctrl+K`)
  and pick "New session". Choose a directory and a CLI. You can start several at
  once with the "How many" field.
- **Name it:** a session you leave unnamed renames itself from your first real
  prompt, so you are not left with a strip of "tmp".
- **Switch:** click a tab, or press `Alt+1` through `Alt+9` for the first nine.
- **Reorder:** drag tabs left and right. The order is this browser's, and it
  survives a reload.
- **Overflow:** when the tabs do not all fit, the extra ones move behind a "more"
  button that still carries a ring if one of them needs you.
- **Close a tab:** the tab's x. This detaches the view only; the session keeps
  running in tmux, and a note says so with a one click "kill it instead".
- **Duplicate:** the session menu has "Duplicate", a fresh CLI in the same
  directory.
- **Interrupt:** "Interrupt (Ctrl-C)" on the session menu or in the palette
  sends Ctrl-C, the gentle way to stop what an agent is doing without killing the
  session.
- **Stop and restart:** stopping a session frees its memory; it can be started
  again later, resuming the conversation where it left off.

Right-click a session row, or long-press it on a phone, for the full menu:
rename, review changes, checkpoint, move to a folder, archive, pin, lock for
review, interrupt, and kill.

---

## The sidebar and folders

Folders are how you keep more than a handful of sessions findable.

- **New folder:** the palette, or the `...` menu in the sidebar header.
- **Rename, recolour, set an emoji, or delete:** the pencil on a folder, or its
  right-click menu. An emoji or a colour makes a folder findable at a glance;
  leave the emoji blank to go back to a plain dot.
- **Move a session in:** drag its row onto a folder, or use "Move to folder" on
  the session menu (which works on a phone, where there is no drag).
- **Reorder:** drag folders and sessions. The arrangement is remembered.
- **Search:** the box at the top filters sessions as you type; matches on name,
  directory and branch.
- **Only what is running:** the filter icon in the header hides stopped sessions.
- **Past conversations:** optionally, each folder can list conversations your
  CLIs have kept, so you can resume one without hunting. Turn it on in
  Settings, Appearance.
- **Orphans:** if a tmux session leaks (started outside CLIque, or left behind),
  it shows at the foot of the sidebar with a way to reap it.

---

## The terminal pane

The center pane is the live session, drawn by a real terminal, so a full-screen
CLI looks exactly as it does in your own terminal.

- **Follow and pause:** output follows the live tail by default. Scroll up and it
  pauses so you can read, with a badge showing how far behind you are; the lock
  control at the bottom, or `Ctrl+Shift+L`, catches you back up.
- **Copy:** drag to select and it copies without interrupting the CLI. With
  nothing selected, `Ctrl+Shift+C` copies the whole visible screen.
- **Links:** click any http or https link to open it. A link that wrapped across
  lines is still one link.
- **File paths:** click a file path in the output to open a read-only preview.
  From the preview you can edit and save a text file (it only ever overwrites a
  file that already exists in the session's directory), and credential files are
  refused.
- **Images a session made:** when a session writes images, a button on its tab
  shows the count; open it for a gallery. You choose which folders to watch in
  Settings, Images.
- **Fonts:** set the terminal typeface and size in Settings, Appearance, or nudge
  the size with the plus and minus in the corner. The typeface list includes a
  Nerd Font option if you have one installed.
- **GPU:** terminals are drawn on the GPU by default for speed, and fall back on
  their own if a device struggles. You can turn it off in Settings, Appearance.

---

## The prompt box

Under the pane is a prompt box. Whether it shows is up to the CLI: a tool that
draws its own input box does not need ours underneath it, a shell does. You can
force it on or off in Settings, Appearance, "Prompt box".

- **Run:** the Run button, or Enter. The repeat counter next to it runs the same
  thing several times.
- **Shell:** the Shell button runs what you typed in a shell session for that
  directory, without leaving the one you are on.
- **Drafts:** a half-typed prompt is saved per session, on the server, so it
  survives a tab switch, a reload, and a closed laptop. You can even move a draft
  to another session from the palette.
- **Paste a screenshot:** paste an image and it is saved into the session's image
  folder, with its path dropped into the prompt for you to send.
- **Snippets:** define short triggers in Settings, Snippets that expand on Tab,
  with placeholders like the current directory, project and session name. They
  work in our prompt box and in a CLI's own input.

---

## The side panel

The far right edge has an always-on icon rail. Click an icon and a panel slides
open to that feature for the session you are on. Click it again, or press
`Ctrl+J`, to close it. It is collapsed by default and does no work while it is
shut, and it remembers its width and which pane you had open. Drag its left edge
to resize it.

Four panes, each scoped to the tab in front:

### Notes

A nested checklist for the session, saved on the server so it survives reloads
and restarts and never lands as an untracked file in your project.

- **Add and edit:** "Add note", then type. Press Enter for the next line.
- **Nest:** press Tab to make a line a sub-note of the one above, Shift+Tab to
  pull it back out. Collapse a topic with the caret to hide its sub-notes.
- **Check off:** the checkbox marks a line done; "Hide done" tucks the finished
  ones away.
- **Send to the terminal:** the arrow on a line drops that text into the session,
  ready for you to press Enter. Turn a note into an action without retyping it.
- **Reminders:** the clock on a line sets a date and time. When it comes due the
  reminder is delivered through your webhook (the same one that pings you when a
  session is waiting), so it reaches you even with the panel closed, and the line
  is flagged in the panel with a dot on the rail.
- **Timestamps:** each line shows when it was last edited, and its creation time
  on hover.

An older plain-text note from before this panel existed is carried over into the
outline the first time you open it.

### Git

Branch, how many files have changed, and the working directory, with two
actions: "Review changes" opens the diff, and "Checkpoint now" saves the current
HEAD and uncommitted diff to a file under `.clique-checkpoints/`, so you can see,
or undo, exactly what an agent changed after you turned it loose.

### Session info

The directory, CLI, memory in use, and how long the session has been up and
quiet. A quick read on what a session actually is without opening a menu.

### Export

Writes the session's whole scrollback to a timestamped text file under
`.clique-exports/` in its directory, a clean log with no colour codes, to keep,
search or share after the fact.

---

## Staying on top of many sessions

The point of CLIque is knowing which session needs you without reading twenty of
them.

- **The ring on each tab and row:** a filling arc means working, a pulse means
  waiting on you, nothing means idle, and grey means stopped.
- **The inbox:** the bell in the sidebar header lists everything that is waiting
  or stopped on an error, with a count. It answers "which one needs me".
- **Unread marks:** a dot appears on a session that produced output while you
  were looking at another, and a rule marks where you stopped reading.
- **The board:** a column view of every session by what it is doing (working,
  waiting, error, idle, stopped), from the palette or the sidebar menu.
- **How "waiting" is decided:** a session can report it directly (a hook), or
  CLIque can match the CLI's own wording, or it stays quiet. You control the
  patterns and the quiet-for delay in Settings, Notifications.
- **A real notification:** set a webhook URL in Settings, Notifications and
  CLIque taps you on the shoulder when a session starts waiting, finishes, errors
  or dies, and when a note reminder comes due. One URL covers ntfy, Gotify,
  Discord, Mattermost, Home Assistant and Uptime Kuma push, so a phone push is
  free. An optional secret signs the request.
- **On-screen extras:** a flash and a chime when a session finishes are both
  optional, in Settings, Notifications.
- **Service outages:** if the provider behind a running CLI is having a bad day,
  a small banner says so. Nothing is drawn when all is well.

---

## Adopting and broadcasting

- **Adopt:** "Adopt sessions" in the sidebar menu takes over tmux sessions that
  another tool started, guessing the CLI from the process tree. The sockets it
  scans are configurable.
- **Broadcast:** "Broadcast" sends one message to every live session at once, or
  to a folder, or to a chosen few. Send it empty and it just presses Enter
  everywhere, a "carry on" to all.

---

## Appearance and settings

Open Settings from the gear in the sidebar header, or the palette. It saves on
the server, so your look follows you between devices; only device-specific things
like the sidebar width stay local.

- **Appearance:** a theme, a dark, light or follow-system base, terminal and
  sidebar font sizes, the prompt-box mode, and the GPU toggle.
- **CLI markers:** how a session's CLI is shown, as a ring or a dot, in the tabs
  and the sidebar, with a per-CLI colour, and an option to tint the pane edge
  with the active CLI's colour.
- **Snippets:** your Tab-expanded triggers.
- **Notifications:** the webhook and its secret, the flash and chime, the
  finished delay, the clock format and time zone, and your command-safety
  patterns.
- **Images:** which folders to watch for the artifact gallery.
- **Custom CSS:** three blocks, applied to everything, the panel only, or the
  terminal only, for when a theme is not quite it.
- **Models:** OpenAI-compatible or Anthropic providers for the optional model
  features. Keys are encrypted at rest.
- **API:** manage the tokens that drive CLIque over HTTP.

---

## Git worktrees

When you start a session in a git repo, you can tick "Run in a new git worktree".
Each session then works in its own branch and its own checkout, so several agents
can work the same repo without stepping on each other. Deleting a session removes
its worktree only if it is clean; a worktree with uncommitted changes is left
alone so nothing is lost.

---

## The command palette

`Ctrl+K` opens the palette, the fastest way to anything. Start typing:

- a **session name** to jump to it (most recently used first),
- `>` for **commands** (new session, settings, board, broadcast, and the rest),
- `~` for **past conversations** to resume,
- `"` to **reuse a prompt** you have typed before.

`Ctrl+Shift+P` opens it straight into commands.

---

## Keyboard shortcuts

| Shortcut | Does |
|---|---|
| `Ctrl+K` | Command palette |
| `Ctrl+Shift+P` | Palette, into commands |
| `Ctrl+B` | Toggle the sidebar |
| `Ctrl+J` | Toggle the side panel |
| `Ctrl+Shift+F` | Full screen (the pane, not the browser) |
| `Ctrl+Shift+L` | Pause or catch up the output |
| `Alt+1` to `Alt+9` | Jump to a tab |
| `Ctrl+Shift+C` | Copy the whole screen |
| `Ctrl+V` | Paste a screenshot into the prompt |
| `Ctrl+Shift+/` | The full shortcut list |

In the Notes pane: Enter for a new line, Tab and Shift+Tab to nest and un-nest,
Backspace on an empty line to remove it. On a Mac, Cmd stands in for Ctrl.

---

## On your phone

CLIque is built to check on from the couch.

- The sidebar becomes a drawer, and the pane takes the full width.
- A row of terminal keys (Esc, Tab, Ctrl-C, arrows) appears for a session in
  front.
- Long-press stands in for right-click, so every menu is reachable.
- "Install as an app", in Settings, About, adds it to your home screen with no
  browser chrome.
- Your open tabs, their order and the active one are saved on the server, so the
  panel comes back the way you left it.

---

## Security in one minute

- CLIque binds to loopback only. To reach it from elsewhere, put it behind
  Tailscale, nginx or Caddy; do not expose it directly.
- Set a password with `clique password`. It is hashed, not stored.
- For scripts, mint a token with `clique token create <name>` on the box itself,
  never over the network. Read-only tokens exist and are refused on any write.
- Writes from a browser are checked for same-origin, and requests to an
  unrecognised host are refused before authentication.
- Keys for the optional model features are brought in through Settings and
  encrypted at rest; nothing is hardcoded.

The full model is in [SECURITY.md](../SECURITY.md).

---

## Driving it from scripts

Every action in the UI is an HTTP call, so a fleet of agents can be run and
watched from a script. The pieces that matter most:

- `GET /api/state` is the whole panel in one snapshot.
- `POST /api/sessions/spawn` starts up to twenty at once, each in its own
  worktree branch if you ask.
- `GET /api/sessions/<id>/wait` blocks until a session changes state, so a script
  can wait for an agent to finish or ask a question rather than polling.
- `POST /api/broadcast` types one thing into all of them.

The full reference is [API.md](../API.md), and there is a skill for agents at
`skills/drive-clique/`.
