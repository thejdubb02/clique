# Vendored, on purpose

xterm.js and four addons — fit, unicode11, canvas and webgl — copied here
rather than pulled from a CDN or npm.

`addon-webgl.js` (v0.18.0, MIT) and `addon-canvas.js` (v0.7.0, MIT) both
replace xterm's default DOM renderer, which draws elements per run of text so a
screen redrawing underneath a live selection leaves fragments of the old text
behind — stale nodes, not a font or width problem. Both repaint the whole cell
grid each frame. WebGL does it on the client's GPU (a real win with many live
panes); canvas does it on the CPU. The panel tries WebGL first and falls back
to canvas, then to the DOM renderer, because a phone, a VM or a remote session
can refuse or silently lose a WebGL2 context — so WebGL is used where it works
and never required. (The two addon families version separately: webgl 0.18.0
and canvas 0.7.0 are the siblings that ride xterm core 5.5.0.)

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
