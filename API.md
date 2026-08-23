# CLIque API

Everything the panel does, it does through this API — there is no action in the
UI that is not an HTTP call you can make yourself. That is deliberate: an agent
driving CLIque should never be reduced to pretending to be a browser.

This file is checked against the code by `tools/api_drift.py`, which runs as
part of the test suite. A route or a setting that is not documented here fails
the build, so this cannot quietly fall behind the app.

## Authenticating

Two ways in, and they are not equivalent.

**A session cookie** — what the browser uses. Writes additionally require a
same-origin `Origin`/`Referer`, so a cookie alone is not enough for a hostile
page to act on your behalf.

**A bearer token** — what an agent or a script should use. Tokens are minted on
the box, never over the network: an endpoint that creates credentials turns any
other hole into permanent access.

```bash
python3 -m clique token create my-agent
python3 -m clique token create watcher --read-only
python3 -m clique token list
python3 -m clique token revoke tk_xxxxxxxx
```

```bash
curl -H "Authorization: Bearer $CLIQUE_TOKEN" http://127.0.0.1:3200/api/state
```

Driving CLIque from an agent — start sessions, send prompts, wait for one to
finish — is written up in `skills/drive-clique/SKILL.md`.

A read-only token is refused on every write with `403 this API token is
read-only`. Bearer tokens skip the same-origin check — they are not a browser
credential and nothing can attach them to a cross-site request by accident.

Requests to an unrecognised `Host` are refused with `403` before authentication
runs at all.

## Reading

### `GET /api/state`

The whole panel in one object, and what the browser polls every three seconds.

| Key | |
|---|---|
| `version` | e.g. `0.21.0+8f32d69` |
| `home` | the home directory of whoever started the server — where a new session starts when nothing better is known |
| `home` | the home directory of whoever started the server — where a new session starts when nothing better is known |
| `folders` | `id`, `name`, `color`, `collapsed`, `order` |
| `sessions` | see below |
| `clis` | every CLI the registry knows: `id`, `label`, `command`, `installed`, `modes`, `color`, `icon` |
| `settings` | the full settings object — see **Settings** |
| `stats` | the same snapshot as `/api/stats` |

Each session carries `own_input` — whether this CLI draws its own input box,
which is what `input_mode: "auto"` reads — plus `id`, `name`, `cli`, `cli_label`, `cwd`, `project`,
`folder`, `mode` (and `modes`, `mode_label`), `adopted`, `archived`, `pinned`, `draft`, `state`,
`saying` — the last line a session actually printed, and only for one that is
waiting or has errored. It is what the sidebar shows in place of the working
directory when something is asking for you: the ring says a session is blocked,
this says what on. Empty for every other session, and captured only for the
handful that are not, cached against the pane's own activity clock.

`cols` and `rows` are the **shared tmux window's** size, which is not
necessarily what any one client is drawing at — every client attached to a
session shares one size, so a second browser resizes the first one's pane. A
client that finds these differ from its own terminal should say so; that is how
a pane recovers from being resized by somebody else instead of sitting in
tmux's dot-fill until something happens to jog it.

Each session also carries `created`, `last_seen`, `order`, `rss` (process-tree resident bytes), plus the live facts: `alive`, `attached`,
`command`, `activity` (tmux's own clock) and `busy`.

`branch` and `dirty` come from git in that session's working directory —
the branch name, and how many paths `status --porcelain` reports. Cached
per repo for a few seconds so the poll does not pay for git every time.
`""` and `0` where there is no repo, no git, or git has not answered yet.
This is not a git UI: it is the sentence the folder tree was missing.

`busy` starts from the activity clock — output within the last two seconds —
but does not end there. A redraw counts as output, so a CLI that animates while
it waits ticks the clock forever and would sit permanently on "working". Once a
pane has claimed to be busy for eight seconds it has to prove it: the visible
screen is captured and compared, and text that has not changed for four seconds
is not work whatever the clock says. Quiet panes are never captured, so this
costs nothing in the ordinary case. There is still no vendor API behind any of
it, which is why it works for any CLI.

### `GET /api/stats` · `GET /api/stats/history?minutes=60`

CPU, memory, swap, disk, load and connected terminals; the history form returns
a series, clamped to 180 minutes.

### `GET /api/resumable`

Every past conversation CLIque can find on disk, with the folder it belongs to
already worked out. Feed a row's `cli_session_id` to `POST /api/sessions` to
resume it.

### `GET /api/prompts?limit=400`

Individual prompts you have sent, newest first and deduplicated by text, for the
command palette's prompt search. Read from each CLI's own history — a prompt-log
CLI whole, a transcript CLI from a bounded tail — never logged a second time by
CLIque. Each row carries `cli`, `cwd`, `project`, `text` (the full prompt, to
reuse), `when`, and `cli_session_id`. `limit` is capped at 400.

### `GET /api/adoptable`

tmux sessions started by another tool that CLIque could take over, with the CLI
guessed from the process tree. Already-known sessions are filtered out.

### `GET /api/sessions/<id>/transcript`

The session's conversation as turns — `{cli, name, turns}` where each turn is
`{role, text}`, oldest first, consecutive same-role turns merged. For a CLI that
draws over the alternate screen (Claude, Grok) and so keeps no scroll-back of
its own. Read from the CLI's own transcript, bounded to a tail (never the whole
file), and only the typed turns: user prompts and the assistant's prose, not its
thinking or tool calls. Empty for a CLI whose history is prompts-only.

### `GET /api/sessions/<id>/diff`

What the session's agent has changed but not committed, for review —
`{repo, name, diff, empty, truncated, untracked_hidden}`. `diff` is git's own
unified diff: tracked changes (against HEAD, or the empty tree in a repo with no
commit yet — staged and unstaged together) plus new files git is not yet
tracking, shown as all-additions and capped, with any beyond the cap named in
`untracked_hidden`. The whole diff is capped by size too; `truncated` is true
when it was cut. `empty` is true for a clean checkout; `repo` is false when the
working directory is not a git repository (then there is no `diff`) — and, being
git's answer, also false if git times out. Computed on demand, not on the poll.

### `GET /api/sessions/<id>/wait`

Blocks until the session reaches one of the `for` states — a comma list of
`working`, `waiting`, `error`, `idle`, `stopped` — or `timeout` seconds pass
(default 60, capped at 300). Returns `{id, state, matched, waited}`; `matched`
is false on a timeout. The primitive for driving CLIque from a script or an
agent: start work, then wait for `idle` (done) or `waiting` (needs a person)
instead of polling the whole panel. See `skills/drive-clique/SKILL.md`.

### `GET /api/orphans`

Leaked sessions: live tmux on our own socket, past a short grace window, that no
record points to — the process keeps running and holds its memory, but nothing
in the panel can see or stop it. `mux`, `command`, `pid`, `idle` seconds and
`rss` bytes, heaviest first. A record removed without killing its tmux is how
they arise. Reclaim them with the route below.

### `GET /api/changelog`

Release notes parsed out of `CHANGELOG.md`: `version`, `date`, `time`, `zone`,
and `blocks` of spans. Structure rather than markup, so nothing has to render
someone else's HTML. The settings sheet shows the newest five and links to
the file on GitHub for the rest; this endpoint still returns the lot.

### `GET /healthz`

**No authentication.** For Uptime Kuma, Gatus, Healthchecks or anything else
that watches a URL — making a monitor carry a credential is how things end up
unmonitored.

Anonymously it returns `{"ok": true}` and nothing else: no version, no session
names, no counts. With a cookie or a token it adds `version`, `uptime`, `tmux`
(reachable at all), `sessions`, `alive` and `attached`.

## Sessions

### `POST /api/sessions` → `201`

```json
{"cli": "claude", "cwd": "/home/you/project", "name": "project",
 "folder": "f-abc123", "mode": "default", "cli_session_id": null}
```

Only `cli` and `cwd` matter; the name defaults to the directory. `mode` is one
the CLI declares. `cli_session_id` resumes a past conversation — the same code
path as starting a new one, differing only in the argv the registry returns.

Pass `worktree: true` with a `branch` to run the session in a fresh git worktree
of the repo at `cwd` — an isolated checkout, so several agents can work the same
repo at once without touching each other's files. The response then carries the
`worktree` path. Deleting such a session removes the worktree, but only when it
has no uncommitted changes; a dirty one is left alone so nothing is lost.

A missing directory, an unknown CLI, or a CLI whose command is not installed is
a `400` with the reason in `error`.

### `PATCH /api/sessions/<id>`

Fields: `name`, `folder` (`null` means Ungrouped), `mode`, `archived`, `pinned`, `draft`.
Only the fields you send are touched — absent and `null` are different, so a
rename cannot silently unfile a session.

### `POST /api/sessions/<id>/kill`

Stops the process. The record stays in its folder, stopped. Start it again
with `/start`. Closing a tab does not come here.

### `POST /api/sessions/<id>/start`

Starts a stopped session again — same id, name, folder and directory. When
the CLI was first launched with our session id, that is also the resume key
(Claude's `--session-id`). A shell has nothing to resume; it starts again
in the same place. Already running is a `400`.

### `DELETE /api/sessions/<id>`

Forgets the record, and stops the process if it is still running. This is
the destructive one. The UI only offers it on a session that is already
stopped.

### `POST /api/sessions/<id>/send`

```json
{"text": "run the tests", "enter": true}
{"key": "C-c"}
```

Text is typed into the pane; `enter` decides whether it is submitted. `key`
sends a single key by tmux name instead.

### `POST /api/sessions/<id>/paste`

```json
{"data": "<base64>", "name": "shot.png"}
```

Writes an image into `<cwd>/.claude-images` and returns the `path` it saved.
The type is sniffed from the bytes rather than trusted from the name, the cap
is 10 MB, and containment is checked after symlink resolution — the write can
only ever land inside that directory.

### `POST /api/webhook/test`

Fires one `test` event at the configured webhook immediately. `400` if no URL
is set. There to answer "did I paste that right" without waiting for something
to finish at three in the morning.

**The webhook body**, for all events:

```json
{"event": "waiting", "at": 1787167125,
 "session": {"id": "...", "name": "api rewrite", "cli": "claude",
             "cli_label": "Claude Code", "folder": "...", "cwd": "/srv/app"},
 "text": "api rewrite is waiting for you",
 "url": "https://box.ts.net/clique/?session=..."}
```

`event` is `waiting`, `error`, `finished`, `died` or `test`. Each fires on the
edge — a session waiting for an hour is not news every ten seconds. With
`webhook_secret` set, `X-CLIque-Signature: sha256=<hmac>` covers the exact
bytes sent. One attempt, five second timeout, no retry: a dropped notification
is superseded by the next change, and a retry queue means durable state.

The watcher only runs while `webhook_url` is set, so a panel without one still
costs nothing when idle.

### `POST /api/sessions/<id>/attention`

```json
{"state": "waiting"}
```

`waiting`, `error`, or `clear`. Lets a session say for itself that it is stuck,
which is the only tier of the attention ladder that is not a guess — wire it to
a hook your CLI already has:

```bash
curl -XPOST -H "Authorization: Bearer $CLIQUE_TOKEN" \
     -H "Content-Type: application/json" -d '{"state":"waiting"}' \
     "$CLIQUE_URL/api/sessions/$ID/attention"
```

The signal is stamped with the pane's activity clock and goes stale by itself
the moment output arrives after it — a session that carried on is no longer
waiting, and a stuck "waiting" would teach you to ignore the mark. Returns the
resulting `signal`.

Sessions in `/api/state` carry `signal`: `"waiting"`, `"error"` or `""`, from
whichever tier could answer — this endpoint first, then the per-CLI patterns in
`clis.toml` matched against a pane that has gone quiet, then nothing.

### `GET /api/workspace?cwd=/srv/app`

What is already going on in a directory, asked before starting something in it.

```json
{"cwd": "/srv/app", "exists": true, "branch": "main", "dirty": 2,
 "touched": 13, "sessions": [{"id": "...", "name": "api rewrite", "cli": "claude"}]}
```

`sessions` are live CLIque sessions whose working directory resolves to the
same path; `touched` counts files written in the last 15 minutes; `dirty` and
`branch` come from git and are `0`/`""` where there is no repo, no git, or a
repo too large to answer within three seconds.

Advisory only — nothing is locked, refused or enforced. Pulled, never polled:
this touches the disk, and it runs when someone has stopped typing a path.

### `GET /api/browse?path=/root/pers`

Directories that could complete a partial path, the way a shell completes one:
a trailing slash lists what is inside, anything else matches the last segment
against its siblings. Directories only; hidden ones appear only once the
segment being typed starts with a dot. Capped at 60.

```json
{"dirs": ["/root/personal/whatbox-media-stack"]}
```

This is what the new-session dialog uses once you start typing a path. The
dropdown beside it answers a different question — everywhere you have already
worked — and neither is a substitute for the other.

### `POST /api/workspace` → `201` with the same shape

```json
{"cwd": "/srv/new-project"}
```

Creates the directory, parents included, and returns what `GET` would now say
about it. A path that already exists as a directory is a success; a relative
path, an empty one, or something that exists and is not a directory is a `400`
with the reason in `error`.

Never implicit — nothing calls this except a person pressing the button that
names the path. There is no sandbox on where, deliberately: anyone who can
reach the panel already has a shell as this user, so a restriction here would
protect nobody while breaking the ordinary case of working outside `$HOME`.

### `GET /api/sessions/<id>/peek?lines=8`

The last few lines of a pane, so "is that one waiting on me" can be answered
without opening the tab and changing what you are looking at.

```json
{"lines": ["Ran 1 shell command", "Flummoxing… (4m 41s · thinking)"],
 "alive": true, "activity": 1787200000}
```

The lines that **said** something, not the last N raw rows. A modern CLI's pane
is mostly frame — box rules, separators, an input box drawn around nothing —
and showing that verbatim buries the one line that answers the question. A line
is dropped when every character in it is a box-drawing glyph, a rule, a prompt
mark or whitespace; that is a property of the text, not knowledge of any CLI,
and a line with one real word in it is always kept.

Colour is stripped. `lines` is how many to return, clamped to 2–40, default 6;
a wider window of scrollback is searched to find them. Nothing captures a pane
until this is called — there is no poller behind it — and the answer is cached
against the pane's own activity clock, so repeated calls while nothing is
printed cost one capture.

### `GET /api/sessions/<id>/file?path=<path>`

Read-only glance at a path the pane printed. Relative paths are against the
session's working directory; absolute paths and `~/` are allowed because
anyone who can reach the panel already has a shell as this user.

```json
{"asked": "docs/foo.md", "path": "/srv/app/docs/foo.md", "name": "foo.md",
 "kind": "text", "size": 1204, "text": "# Foo\n", "truncated": false}
```

`kind` is `text`, `image`, `binary`, `dir` or `missing`. Text is capped;
`truncated` is true when there is more. `?raw=1` on an image returns the
bytes, typed from magic, same as an artifact.

A compiler-style suffix (`foo.py:12` or `foo.py:12:4`) is stripped. This is
not an editor.

### `GET /api/sessions/<id>/artifacts`

Images that appeared in the session's working directory **after the session
started**, newest first, capped at 30:

```json
[{"name": "shot.png", "rel": "screenshots/shot.png",
  "path": "/srv/app/screenshots/shot.png", "size": 40122, "mtime": 1787200000.0}]
```

`rel` is what `GET .../artifact` takes; `path` is what an agent can open. Which
directories are searched is the `artifact_dirs` setting, nothing is searched
recursively, and symlinks are skipped. Returns `[]` when `artifacts_show` is
off.

The `since the session started` filter is what makes this the agent's output
rather than the project's artwork — a repository cloned *during* the session is
the case it cannot tell apart.

### `GET /api/sessions/<id>/artifact?rel=<path>`

The image itself. `rel` must be relative, must not climb with `..`, and is
re-resolved against the working directory with containment checked after
symlink resolution — a path that leaves it is `404`, never served. The
`Content-Type` comes from the file's magic bytes, not its extension; anything
that is not an image CLIque recognises is `415`, and over 10 MB is `413`.

### `POST /api/sessions/<id>/seen`

Marks it looked-at. Returns the new `last_seen`, which is what the unread dot
compares tmux's activity clock against.

### `POST /api/sessions/adopt`

Takes over every adoptable tmux session found. Safe to run twice — it repairs
earlier runs rather than duplicating them.

### `POST /api/orphans/reap`

Kills leaked sessions (see `GET /api/orphans`) and reclaims their memory. Body
`{"muxes": [...]}` limits it to those names; an empty or absent list reaps all
of them. A mux that belongs to a real record is never touched. Returns
`{"killed": [...]}`.

### `POST /api/reorder`

The sidebar's drag-and-drop. Either list, or both. Unlisted items keep their
place at the tail. Order is what `/api/state` returns.

```json
{"sessions": ["id1", "id2", "id3"], "folders": ["f-aaaa", "f-bbbb"]}
```

## Folders

- `POST /api/folders` → `201` with the whole folder — body `name`, `color`
- `PATCH /api/folders/<id>` → the folder — `name`, `color`, `collapsed`
- `DELETE /api/folders/<id>` — sessions inside become Ungrouped

`color` is three or six hex digits with a leading `#`, and nothing else — it is
written into a `style` attribute in the sidebar. Anything else is ignored and
the folder keeps the colour it had, so read the response back rather than
assuming the value you sent is the value that stuck.

## Service status

`GET /api/state` carries `services`: the providers behind your **running** CLIs
that are reporting a problem, worst first. Almost always empty — it holds the
exceptions, not a commentary on four status pages being fine.

```json
[{"cli": "claude", "label": "Claude Code", "indicator": "major",
  "description": "Elevated error rates", "url": "https://status.claude.com",
  "checked": 1787200000}]
```

`indicator` is Statuspage's own vocabulary: `maintenance`, `minor`, `major` or
`critical`. `none` never appears — an operational service is not news. A
reading older than an hour is dropped rather than shown, so a box that has lost
DNS says nothing instead of leaving yesterday's outage on the screen.

The feed is a `status` block in `clis.toml` next to the launch command:

```toml
[cli.claude]
status = { url = "https://status.claude.com/api/v2/status.json",
           page = "https://status.claude.com" }
```

A second optional key sits beside it: `own_input = true` marks a CLI that
draws its own input box at the bottom of the pane, so the panel does not stack
a second one underneath. Purely about what is on screen — a shell prints `>`
and is not doubled by anything, and there the panel's box is the only place
Run, the repeat counter and a saved draft live.

`url` must be an Atlassian Statuspage v2 endpoint — that one format covers
Anthropic, OpenAI, GitHub and Cursor, and a second parser here would be the
first step towards a directory of per-vendor scrapers. `page` is what the panel
links to. Adding a feed for a CLI we have never heard of is those two lines and
a reload; a CLI with no block is never asked about.

Read every five minutes, and only for CLIs with a session open right now. An
idle panel makes no requests. It sends no identifier, no session name and no
query string. `service_status: false` stops the thread immediately.

## Settings

### `PATCH /api/settings`

Send only the keys you are changing; the merged object comes back. Unknown keys
are ignored rather than stored. Values are clamped, not rejected, where a bad
one could otherwise make the UI unusable and unfixable.

**Everything a person chose lives here, on the server, so it survives a reload
and follows them to another device.** If you add a preference to CLIque, it
goes in this object — `localStorage` is only for what is about the screen in
front of you (sidebar width, sidebar shown or hidden).

| Key | Type | |
|---|---|---|
| `marker_default` | `"both"` \| `"icon"` \| `"dot"` \| `"none"` | Which mark a session gets by default |
| `marker_by_cli` | object | Per-CLI override; merges one level deep, so sending one CLI does not reset the rest. `null` clears one |
| `markers_in_tabs` | bool | Marks on tabs |
| `markers_in_sidebar` | bool | Marks in the sidebar |
| `status_on_icon` | bool | The CLI logo carries the status colour, instead of a second dot |
| `theme` | string | Preset id from `web/themes.js`; `""` is the built-in |
| `appearance` | `"dark"` \| `"light"` \| `"system"` | Base used when no preset is chosen |
| `font_panel` | 9–28 | Sidebar and chrome |
| `font_terminal` | 9–28 | The pane, read at a different distance. Also the `+`/`−` stepper in the bottom-right |
| `font_family` | `"system"` \| `"menlo"` \| `"consolas"` \| `"ubuntu"` \| `"courier"` | Monospace stack for the pane. Each id is a fallback chain that exists on Windows, Mac and Linux, so a missing font still lines up. Unknown ids are dropped |
| `palette_hotkey` | bool | Whether `Ctrl`+`K` opens the palette or is handed to the pane |
| `history_in_sidebar` | bool | Past conversations listed under live sessions. **Off by default** — a month of work is several hundred of them, and at that ratio the sidebar stops showing what is running. The palette still searches all of it |
| `history_days` | int | How far back the sidebar goes when the above is on. Default 14. Does not limit the palette |
| `reap_idle_hours` | int | Stop an idle session's process after this many hours to free its memory, greying its tab; clicking it resumes exactly where it was. Only a resumable session no browser is attached to and that is not busy is reaped. Default 6; `0` turns it off; clamped to 720 |
| `input_mode` | `"auto"` \| `"panel"` \| `"terminal"` | Whether the panel draws a prompt box. `auto` (default) asks the CLI — one that draws its own box gets no second one under it. The mode pill is never hidden by this |
| `css_both`, `css_panel`, `css_terminal` | string | Custom CSS, applied in that order |
| `snippets` | list | `{"trigger", "label", "text"}`; malformed entries are dropped here rather than becoming a render error later |
| `notify_flash` | bool | Flash a tab whose session finished |
| `notify_sound` | bool | Off by default: a room with twenty agents would be unbearable |
| `notify_idle_seconds` | 2–120 | Quiet before a session counts as finished |
| `open_tabs` | list of session ids | The workspace: which sessions have a tab, in order. Deduplicated, order preserved |
| `active_tab` | session id | Which one was in front |
| `views_collapsed` | list | Shut view-groups: `__running`, `__unfiled`, `__archived` |
| `cli_tint` | bool | Colour the pane edge, active tab and prompt box with the active CLI's colour |
| `cli_colors` | map | Per-CLI colour overrides, `{"claude": "#d97757"}`. Merged one level deep like `marker_by_cli`; a `null` value restores the shipped colour. Must be a 3- or 6-digit hex, anything else is dropped |
| `changelog_seen` | version | Newest release whose notes have been read. Seeded on first load so a fresh install does not badge itself |
| `service_status` | bool | Ask the provider behind a running CLI whether it is having a bad day. The only outbound requests CLIque makes without being told to — see **Service status** below. On by default |
| `clock_24h` | bool | 24-hour clock. Not derived from the locale — people read one format at work and another at home |
| `clock_zone` | IANA zone | Clock on the empty pane, e.g. `Europe/Lisbon`. Validated against the system zone database; a name that is not real is dropped rather than stored, because `Intl` throws on one. Blank means the browser's own |
| `webhook_url` | url | Where to POST session events. `http`/`https` only; anything else is stored as `""` |
| `webhook_secret` | string | Signs each request as `X-CLIque-Signature`. **Write-only** — `/api/state` returns it as `""` plus a `webhook_secret_set` boolean, so a read-only token cannot lift it and forge a signature. Send `""` to remove one |
| `panel_url` | url | This panel's public address, included so a notification can link back |
| `artifacts_show` | bool | List the images a session makes |
| `artifact_dirs` | list | Where to look, relative to each session's cwd; `.` is the cwd itself. Absolute entries and `..` are dropped, max 12 |

## Terminals

`GET /ws?id=<id>&cols=<n>&rows=<n>` upgrades to a WebSocket carrying the
pane. Text frames are keystrokes; JSON control frames handle `resize` and
running a command. The handshake enforces `Origin`, because a WebSocket is not
subject to CORS and `SameSite=Lax` does not cover it.

`passive=1` attaches a viewer without resizing the shared tmux window, and
sizes its PTY to the window that is already there. Each window is locked
to `manual` size — attaching a client cannot move it, only an explicit
`resize` from a focused pane. Used when a tab is warming in the
background, or reconnecting while hidden. A `resize` below 20x8 is
ignored; that is a collapsed tab measuring itself, not a real window.

The PTY is created on connect and destroyed on disconnect — no viewer, no
process. **Closing the socket does not stop the session**; that is the whole
point of tmux underneath. Use `DELETE` to actually end one.

## Errors

| | |
|---|---|
| `400` | Bad input — `error` says what |
| `401` | No credential, or one that is not valid |
| `403` | Read-only token, cross-origin write, or an unrecognised `Host` |
| `404` | No such route, session or folder |
| `500` | A bug; the traceback is in the journal |
