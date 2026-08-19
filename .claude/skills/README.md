# Vendored skills

Animation and design-engineering guidance from
[emilkowalski/skills](https://github.com/emilkowalski/skills), MIT licensed —
the licence is kept beside them as `LICENSE-emilkowalski`.

These are **instructions, not code**. Nothing here is imported, bundled or
served; they load into a session working on CLIque and shape the CSS that gets
written. They add zero bytes to the app, which is the only reason they are
here at all.

Left behind on purpose:

- `animate-expo` — React Native.
- `ask-sonner`, `pick-ui-library` — about choosing and using libraries. CLIque
  does not install any, so the advice has nothing to attach to.
- `prototype` — worth revisiting when the advanced-UI layer starts.

The one rule that matters when following them: **take the taste, refuse the
dependency.** The `animate` skill's own tool table walks from CSS transitions
up to a motion library; for CLIque the walk stops before the last row. CSS and
the Web Animations API are in every browser already and cost nothing.
