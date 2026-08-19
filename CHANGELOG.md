# Changelog

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
