# Vendored, on purpose

xterm.js and two addons — fit, and unicode11 — copied here rather than
pulled from a CDN or npm.

`addon-unicode11.js` (v0.8.0) is not optional polish. Without it xterm.js
uses Unicode 6 width tables, in which an emoji-presentation glyph such as
`⚠️` is one cell wide; fonts draw it as two, and the next character is
painted over the top of it. Any status line with an emoji in it renders as
overlapping letters.

- No network dependency, so the panel works on a box with no egress and cannot
  be broken by someone else's CDN.
- No build step and no `node_modules`, which is the point of the whole project.
- Pinned by copy: upgrading is a deliberate act with a diff, not a silent
  version bump.

MIT licensed (see `xterm.LICENSE`). Versions are recorded in the commit that
added them.
