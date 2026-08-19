# Changelog

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
