# The working order

What to build next and in what order. Short-lived: shipped work moves to
the CHANGELOG and drops off this page.

Ranked for Justin's actual use (many agents, two machines) against
[ROADMAP.md](../ROADMAP.md). Not a commitment dump: [ideas-inbox.md](ideas-inbox.md).

---

## Releasing to PyPI

Nothing is blocked and no token is involved. `.github/workflows/publish.yml`
uses Trusted Publishing, and it fires when a GitHub Release is published, not
on a push. So `main` running ahead of PyPI is normal and expected; cutting a
release is what catches it up.

`uvx clique-panel` works. Last cut: `v0.51.9` on 2026-08-28, verified by
installing from PyPI into a clean venv rather than trusting the workflow.

## Now — daily drive

Hours each. These are the ones that pay off every hour in the panel.

1. **Session status line:** elapsed, last activity, process state.

## Then — bigger slices

2. **Phone layout.** Sidebar, tab bar, and an on-screen key row. The largest
    gap between the README and the panel.
3. **Session templates:** CLI + directory + starter prompt + name pattern.
4. **An agent-facing skill.** The API exists; nothing tells an agent so.
5. **Per-session CPU/memory,** to catch one agent starving the box.

## Waiting on a repro

**Land on the last prompt when a session opens.** Was first on this list.
Measured on 2026-08-28 and it did not reproduce: opening a session, reopening
a closed tab, and reloading the page all landed with the last prompt on the
bottom row of the screen, in both session shapes. It needs Justin to say which
CLI it happens with and which way the view is wrong before anything is built
for it. The probe did turn up the entry below.

**A shell session has no scrollback in the browser.** The alternate-screen
switch is stripped only for CLIs with `own_input = true`, so Claude, Grok and
Gemini scroll back properly. A plain shell lets it through, `tmux attach` takes
the alternate screen, and the alternate screen holds no history by design, so
the several hundred lines the server captures on attach are written into the
normal buffer and then hidden behind it. Measured: a shell came back with a
buffer exactly as tall as the window and nothing above it, while the same shell
registered as owning its input came back with 283 lines above. It may be
deliberate, since the alternate screen is also what stops tmux's redraws piling
up as stale copies of the frame. Nothing in the code says either way, which is
the part worth fixing whichever way it goes.

## Out of the tmux question (2026-08-28)

The backend stays; the reasoning is in [ROADMAP.md](../ROADMAP.md). These three
came out of asking, and none of them is a rewrite:

- **`aggressive-resize on`.** Sizes a window from the clients that have it
  currently active, rather than every client in the session. We do not set it.
  Both models raised it unprompted, and since each browser already gets its own
  current window it may let us delete some of the manual focus tracking. Does
  nothing when two browsers are on the same window, so it is a partial fix.
  Twenty minutes to test.
- **Attach background viewers with `-f ignore-size`.** A client with that flag
  does not vote on the window size at all. That is the "a reconnecting tab
  filled the other window with dots" class of bug, fixed in tmux rather than in
  our JavaScript, and it holds even when the focus logic hiccups.
- **Say the shared size out loud.** Something like "120x30, sized by another
  window" in the pane header. Both models made the same point independently:
  most of the confusing reports are people not knowing why their pane looks
  wrong, and a label is an hour.

Also raised, and it lines up with the shell-scrollback finding above: stripping
alternate-screen sequences to fake scrollback is rewriting a byte stream, and
it will break quietly whenever one of those CLIs changes how it renders.
`tmux capture-pane -eJ -S -` gives the whole history with escapes intact
through a separate endpoint, which is more code once and less code every
quarter.

## Hygiene, not a feature

- **`docs/audit-2026-08-19.md`.** Still unread as a pass. Do not take it at
  face value. History cache, unbounded notify threads, CSP `connect-src`.
- **Long-uptime memory.** Read RSS after a quiet stretch, before the next
  restart.

## Not on this list

Error Lens inside the terminal, Todo Tree of agent output, GitLens blame,
Power Mode, minimaps, LLM session summaries, broadcast-to-many-sessions,
a built-in diff editor. Driver, not an IDE.

## Standing

`~/.cache/clique-visual/bin/python tools/visual_check.py` after any `web/`
change, and **open the screenshots**.
