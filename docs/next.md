# The working order

What to build next and in what order. Short-lived by design: when something
here ships it moves to the CHANGELOG and drops off this page.

- **Why** a thing is ranked where it is: [ROADMAP.md](../ROADMAP.md), which is
  ordered by where five independent feature lists agreed without seeing each
  other's work.
- **Not committed to anything**: [ideas-inbox.md](ideas-inbox.md).

---

## Today — all four shipped

Scroll lock (0.13.0), prompt drafts (0.17.0), close-and-keep with a home for
running sessions (0.18.0), unread marker and the since-last-viewed rule
(0.19.0). Screenshot paste (0.12.0), the shortcut reference (0.14.0) and the
support section (0.16.0) were not on this list and shipped anyway.

## Next, if the day holds

### 5. Attention states: waiting and error
Beyond busy/quiet — **working / waiting / needs attention / error / quiet**.
"Which of my eighteen agents needs me?" is the question this product exists to
answer. The busy pulse and finished-flash landed in 0.2.0; what is missing
needs output-pattern matching, and it has to degrade cleanly when a CLI is
silent or non-standard. *A day.*

### 6. Hover preview of the last few lines
On sidebar rows and on tabs. Glance without switching. Named the highest-value
awareness win after the badge, and it costs hours.

---

## Operational — small, and must not slip

| | |
|---|---|
| **Failure, rescue, orphans** | Name the ways a session can fail and show which one it is in; restart a CLI in place; reconcile records against tmux both ways so nothing is left running that nobody can see. |

---

## Bigger, and not today

Mobile layout and the PWA · file handling (drop to upload, paste images,
download, light browser) · public-repo assets and a LICENSE · the marketing
site · the side-panel AI helper · emoji folder markers · searchable prompt
history · individually revocable sessions.

`File routes need realpath containment` is blocked until there are file
routes, and should be done *with* them rather than after.
