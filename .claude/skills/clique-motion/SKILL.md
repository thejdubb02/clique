---
name: clique-motion
description: Rules for any animation, transition or hover effect in the CLIque front end (web/). Load before adding or changing motion.
---

# Motion

Wanted, and cheap, or not at all. The whole argument for this tool is that it
is 24 MB and starts instantly; motion that costs a library, a frame budget or a
main-thread stall gives that away for polish.

- **CSS transitions, `@starting-style`, CSS animations, and WAAPI
  (`element.animate()`). Never a motion library.** Those four are in every
  browser already and cost nothing to ship.
- **Animate `transform` and `opacity`.** Anything that moves layout — width,
  height, top, left — repaints the world on a box that is also running a dozen
  PTYs.
- **The frequency gate decides first, not the easing.** Something opened
  hundreds of times a day should not animate at all: the command palette,
  tab switching, the sidebar rail. Modals, drawers, toasts and the settings
  sheet are where motion earns its place.
- **`prefers-reduced-motion` ships with the animation**, not after it — and as
  a gentler variant rather than nothing.
- Hover effects go behind `@media (hover: hover) and (pointer: fine)`; touch
  fires a false hover on tap.

Guidance for the details is vendored in `.claude/skills/` — see the README
there for what was left out and why.
