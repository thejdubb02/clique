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

Each session carries `id`, `name`, `cli`, `cli_label`, `cwd`, `project`,
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

- `POST /api/folders` → `201 {"id": "..."}` — body `name`, `color`
- `PATCH /api/folders/<id>` — `name`, `color`, `collapsed`
- `DELETE /api/folders/<id>` — sessions inside become Ungrouped

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
| `input_mode` | `"panel"` \| `"terminal"` | Keep CLIque's prompt box, or let the CLI's own be the only one |
| `css_both`, `css_panel`, `css_terminal` | string | Custom CSS, applied in that order |
| `snippets` | list | `{"trigger", "label", "text"}`; malformed entries are dropped here rather than becoming a render error later |
| `notify_flash` | bool | Flash a tab whose session finished |
| `notify_sound` | bool | Off by default: a room with twenty agents would be unbearable |
| `notify_idle_seconds` | 2–120 | Quiet before a session counts as finished |
| `open_tabs` | list of session ids | The workspace: which sessions have a tab, in order. Deduplicated, order preserved |
| `active_tab` | session id | Which one was in front |
| `views_collapsed` | list | Shut view-groups: `__running`, `__unfiled`, `__archived` |

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
