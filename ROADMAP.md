# muxpanel — what this should become

Written 2026-08-19, after v0.1.0 shipped. This is the feature list for a tool
of this kind, not a promise about order. Anything marked **done** is in the
build today.

The organising principle: muxpanel is where Justin *lives* during a working
day. So the bar for a feature is "does this remove friction from the thing he
does forty times a day", not "is this impressive".

---

## 1. Session management

| | State |
|---|---|
| tmux persistence, survives browser/laptop/network | **done** |
| Folder tree, drag-drop, rename, search | **done** |
| Archive without killing | **done** |
| Folder colours | **done** |
| Kill with confirmation | **done** |
| Adopt sessions started by another tool | **done** |
| **Duplicate a session** — same CLI, same directory, new instance | todo |
| **Restart in place** — same tab, same folder, fresh CLI process | todo |
| **Sort within a folder** (manual order, last-used, name) | todo |
| **Bulk actions** — archive/kill several at once | todo |
| **Session notes** — a line of "what is this for" under the name | todo |
| **Auto-file rules** — directory prefix → folder, inherited from CodemanPanel's `match` | partial (data model exists, no editor) |
| **Star / pin** a handful above everything | todo |
| **Idle vs working indicator** — is the CLI waiting on *me* or thinking | todo, high value |

That last one is the single most useful thing not built. Codeman had it and it
is what makes twenty tabs manageable: you look at the sidebar and see which
three want an answer.

## 2. Appearance

| | State |
|---|---|
| Per-CLI icons and colours, four modes | **done** |
| Markers independently toggleable in tabs and sidebar | **done** |
| **Preset themes** for panel and terminal | **in this build** |
| **Custom CSS — panel, terminal, and both**, independently | **in this build** |
| **Font size / family** for the terminal | todo |
| **Density** — compact vs comfortable sidebar rows | todo |
| **Light mode** that is actually good, not an inverted dark theme | todo |
| **Per-folder accent** applied to its sessions' tabs | todo |

Custom CSS is carried over from CodemanPanel deliberately. It is the escape
hatch that means a preference he cares about never has to become a feature
request — he just writes three lines.

## 3. Input and prompting

| | State |
|---|---|
| Prompt box, Run / Shell split | **done** |
| Mode pill driven by the CLI registry | **done** |
| Repeat stepper | **done** |
| **Snippets / text expanders** — his own short codes | **in this build** |
| **Prompt history** — up-arrow through what he has sent | todo |
| **Send to several sessions at once** — same prompt, many agents | todo |
| **Draft per session** — switching tabs keeps what you were typing | todo |
| **Voice input** | todo (was always phase 2) |
| **File picker / paste an image** into a prompt | todo |

Snippets are the highest-frequency win here. The same six prompts get retyped
every day.

## 4. Awareness

| | State |
|---|---|
| CPU, memory, connected clients | **done** |
| **Per-session resource use** — which agent is eating the box | todo |
| **Desktop / push notification when a session wants input** | todo |
| **Token or cost readout** per session, where the CLI exposes it | todo |
| **Activity sparkline** — has this session done anything in an hour | todo |

## 5. Reliability

| | State |
|---|---|
| Auto-reconnect on network drop | **done** |
| Viewer sessions reaped, self-healing after a restart | **done** |
| Scrollback restored on reattach | **done** |
| **Scrollback search** inside a session | todo |
| **Export a session's transcript** to a file | todo |
| **State file backup / restore** beyond the single `.bak` | todo |

## 6. Deliberately not doing

Kept from the original brief, and still right:

- Subagent visualisation, respawn controllers, autonomous overnight running
- Multi-host awareness
- A mobile-specific UI (responsive is enough; a phone is for checking, not driving)
- Anything that needs a database

---

## The next three, in order

1. **Idle vs working indicator.** Turns the sidebar from a list into a dashboard.
2. **Prompt history and per-session drafts.** Small, constant, daily friction.
3. **Notification when a session wants input.** Makes it safe to look away.
