# The working order

What to build next and in what order. Short-lived by design: when something
here ships it moves to the CHANGELOG and drops off this page.

- **Why** a thing is ranked where it is: [ROADMAP.md](../ROADMAP.md), which is
  ordered by where five independent feature lists agreed without seeing each
  other's work.
- **The full backlog**, including operational work that is not a feature:
  the **CLIque** list in Nextcloud Tasks.
- **Not committed to anything**: [ideas-inbox.md](ideas-inbox.md).

---

## Today — the three you will feel

These are all hours, not days, and all of them fire many times an hour once
there are a dozen sessions open.

### 1. Per-session prompt drafts
A half-typed instruction survives a tab switch, a reload and a closed laptop.
Pure loss-prevention, and the cheapest high-frequency win on the list.

*On the server, not in localStorage. The rule set in 0.4.1: a draft is about
the work and follows you between devices; only things about the screen stay
local. Debounced, so it is not a write per keystroke.*

### 2. Close-and-keep vs close-and-kill, and a home for what is running
Closing a tab already keeps the session alive, and the sidebar already lists
everything — but neither says so. Make the choice explicit on close, and give
sessions that are running with no tab their own visible area.

*Mostly presentation over state that already exists. The kill path must keep
its confirmation.*

### 3. Unread marker and a since-last-viewed separator
A line in the scrollback where you stopped reading, and a mark on sessions
that produced output while you were elsewhere. Flashing says *something
happened*; this says *what you have not seen*.

*The activity clock that drives the busy pulse already has what this needs.*

---

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
| **Vault entry** | Still named *muxpanel (devbox)*. Ten seconds in the Vaultwarden UI; the bash guard correctly stops an agent doing it. |
| **No monitor at all** | CLIque binds loopback only, so it needs a **push** monitor on vps1's Kuma plus a heartbeat thread. The push token is a secret and goes in Vaultwarden. |
| **Failure, rescue, orphans** | Name the ways a session can fail and show which one it is in; restart a CLI in place; reconcile records against tmux both ways so nothing is left running that nobody can see. |

---

## Bigger, and not today

Mobile layout and the PWA · file handling (drop to upload, paste images,
download, light browser) · public-repo assets and a LICENSE · the marketing
site · the side-panel AI helper · emoji folder markers · searchable prompt
history · individually revocable sessions.

`File routes need realpath containment` is blocked until there are file
routes, and should be done *with* them rather than after.
