# Changelog

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
  anything in `MUXPANEL_ALLOWED_HOSTS`.
- **Login throttling no longer locks out the legitimate user.** Behind a tunnel
  every request arrives from the same loopback address, so a per-IP lockout hit
  the only real user along with the attacker. A correct password now always
  gets through.
- **Content-Security-Policy** added. Everything is served from this origin, so
  a strict policy was free.
- **The password is stored as an scrypt hash.** The server only verifies, so
  keeping the plaintext bought nothing. Set it with
  `python3 -m muxpanel password`. Vaultwarden now holds the only copy.
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
