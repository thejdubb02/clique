# The working order

What to build next and in what order. Short-lived: shipped work moves to
the CHANGELOG and drops off this page.

Ranked for Justin's actual use (many agents, two machines) against
[ROADMAP.md](../ROADMAP.md). Not a commitment dump: [ideas-inbox.md](ideas-inbox.md).

---

## Blocked on Justin

**PyPI / `uvx clique-panel`.** The package is built. Needs his publish token.
Until that lands, nobody else can install this.

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
