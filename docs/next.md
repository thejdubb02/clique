# The working order

What to build next and in what order. Short-lived by design: when something
here ships it moves to the CHANGELOG and drops off this page.

- **Why** a thing is ranked where it is: [ROADMAP.md](../ROADMAP.md), which is
  ordered by where five independent feature lists agreed without seeing each
  other's work.
- **Not committed to anything**: [ideas-inbox.md](ideas-inbox.md).

---

## First thing next session

**Read `docs/audit-2026-08-19.md`.** A 383-line code review Grok produced
against this codebase, and the only thing asked for today that was not
delivered. Several items were visible over Justin's shoulder and look real:

- history discovery re-reads ~160 KB per transcript on a 30-second all-or-
  nothing cache — a per-file cache on (path, mtime, size) instead
- `notify.post` starts a thread per event, unbounded
- opening the workspace attaches every tab at once, rather than the active one
- the bearer/cookie check re-verifies several times per request
- CSP `connect-src` allows any `ws:`/`wss:` where `'self'` would do

Work through it, agree or disagree with each item on the evidence, and say
which. Do not take it at face value — two of today's three "reviews" contained
confident claims that measurement disproved.

## Then, in order

1. **`uvx clique` / PyPI.** Still the top of the roadmap and still blocked on
   Justin's token. `clique-panel` is unclaimed; the import package is still
   top-level `clique`, which collides with an unrelated library on PyPI. Nobody
   can install this yet, and everything else only pays off for someone who can.
2. **Clickable file paths in the pane.** URLs are already clickable; a path
   like `docs/audit-2026-08-19.md` is not, and it is exactly the thing you want
   to act on. Asked for directly on 2026-08-19. Copy it, drop it in the prompt,
   or view it — a read-only viewer for text is the same category as the image
   viewer that already exists, so it does not cross into being an editor.
   Codeman's `attach <path>` is the push half of the same idea; see the private
   notes.
3. **An agent-facing skill.** The whole API exists and nothing tells an agent
   so. Small, entirely ours to write, probably the best value-per-hour left.
4. **Mobile layout.** Still the largest gap between what the README promises
   and what the panel does.
5. **A long-uptime memory number.** Never measured, because the service was
   restarted dozens of times today. The panel has been left alone since
   0.46.0 — read it before restarting anything.

## Shelved, with the reasoning written down

- **Theming across all sixteen CLIs** — [ideas-inbox.md](ideas-inbox.md). Four
  measured, twelve unknown, and one CLI painting in truecolor would cap the
  whole idea. Needs `tools/palette_probe.py` run on a box with more installed.
- **Trinity as the Matrix** — the same entry. Wants a monochrome theme mode
  that claims the whole 256-colour palette, and eyes on the result.

## A standing note

Run `~/.cache/clique-visual/bin/python tools/visual_check.py` after any change
under `web/`, and **open the screenshots**. Four of the bugs found on
2026-08-19 were visual, three were self-inflicted, and the whole suite stayed
green through every one of them.
