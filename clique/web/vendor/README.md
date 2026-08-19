# Vendored, on purpose

xterm.js and its fit addon, copied here rather than pulled from a CDN or npm.

- No network dependency, so the panel works on a box with no egress and cannot
  be broken by someone else's CDN.
- No build step and no `node_modules`, which is the point of the whole project.
- Pinned by copy: upgrading is a deliberate act with a diff, not a silent
  version bump.

MIT licensed (see `xterm.LICENSE`). Versions are recorded in the commit that
added them.
