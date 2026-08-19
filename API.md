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
| `folders` | `id`, `name`, `color`, `collapsed`, `order` |
| `sessions` | see below |
| `clis` | every CLI the registry knows: `id`, `label`, `command`, `installed`, `modes`, `color`, `icon` |
| `settings` | the full settings object — see **Settings** |
| `stats` | the same snapshot as `/api/stats` |

Each session carries `own_input` — whether this CLI draws its own input box,
which is what `input_mode: "auto"` reads — plus `id`, `name`, `cli`, `cli_label`, `cwd`, `project`,
`folder`, `mode` (and `modes`, `mode_label`), `adopted`, `archived`, `draft`,
`created`, `last_seen`, `order`, plus the live facts: `alive`, `attached`,
`command`, `activity` (tmux's own clock) and `busy` — output within the last
two seconds. `busy` going false is what "this one finished" means here; there
is no vendor API behind it, which is why it works for any CLI.

### `GET /api/stats` · `GET /api/stats/history?minutes=60`

CPU, memory, swap, disk, load and connected terminals; the history form returns
a series, clamped to 180 minutes.

### `GET /api/resumable`

Every past conversation CLIque can find on disk, with the folder it belongs to
already worked out. Feed a row's `cli_session_id` to `POST /api/sessions` to
resume it.

### `GET /api/adoptable`

tmux sessions started by another tool that CLIque could take over, with the CLI
guessed from the process tree. Already-known sessions are filtered out.

### `GET /api/changelog`

Release notes parsed out of `CHANGELOG.md`: `version`, `date`, `time`, `zone`,
and `blocks` of spans. Structure rather than markup, so nothing has to render
someone else's HTML.

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

A missing directory, an unknown CLI, or a CLI whose command is not installed is
a `400` with the reason in `error`.

### `PATCH /api/sessions/<id>`

Fields: `name`, `folder` (`null` means Ungrouped), `mode`, `archived`, `draft`.
Only the fields you send are touched — absent and `null` are different, so a
rename cannot silently unfile a session.

### `DELETE /api/sessions/<id>`

Kills the tmux session and forgets it. This is the destructive one; closing a
tab in the UI does not come here.

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

### `POST /api/reorder`

```json
{"sessions": ["id1", "id2", "id3"]}
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
| `font_terminal` | 9–28 | The pane, read at a different distance |
| `palette_hotkey` | bool | Whether `Ctrl`+`K` opens the palette or is handed to the pane |
| `history_in_sidebar` | bool | Past conversations listed under live sessions |
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

`GET /ws?session=<id>&cols=<n>&rows=<n>` upgrades to a WebSocket carrying the
pane. Text frames are keystrokes; JSON control frames handle `resize` and
running a command. The handshake enforces `Origin`, because a WebSocket is not
subject to CORS and `SameSite=Lax` does not cover it.

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
