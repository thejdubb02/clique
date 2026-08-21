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

1. **Searchable prompt history.** Every prompt sent, per session and globally,
   fuzzy, one click to reuse. Snippets stay for the ones you meant to keep.
2. **Land on the last prompt when a session opens.** You currently land
   wherever the stream left you.

## Next — awareness

3. **Copy last output / last N lines.** Selection and the visible screen
   already copy (0.50.17–0.50.21). This is the leftover: last reply, last N
   lines, without dragging.
4. **Pin / favourite** sessions, above recency.
5. **Session status line:** elapsed, last activity, process state.

## Then — bigger slices

6. **Phone layout.** Sidebar, tab bar, and an on-screen key row. The largest
    gap between the README and the panel.
7. **Session templates:** CLI + directory + starter prompt + name pattern.
8. **An agent-facing skill.** The API exists; nothing tells an agent so.
9. **Per-session CPU/memory,** to catch one agent starving the box.

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
