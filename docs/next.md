# The working order

Why the next work is ranked the way it is. The list itself is on the **CLIque**
board in Kaneo, one card per job across the panel, the desktop and the phone;
this page holds the reasoning, the measurements and the things that were tried
and did not work.

Ranked for Justin's actual use (many agents, two machines) against
[ROADMAP.md](../ROADMAP.md). Not a commitment dump: [ideas-inbox.md](ideas-inbox.md).

---

## Releasing to PyPI

Nothing is blocked and no token is involved. `.github/workflows/publish.yml`
uses Trusted Publishing, and it fires when a GitHub Release is published, not
on a push. So `main` running ahead of PyPI is normal and expected; cutting a
release is what catches it up.

`uvx clique-panel` works. Last cut: `v0.69.0` on 2026-09-19, carrying 0.68.2
through 0.69.0 with it. That gap is the lesson: 0.67.1 through 0.67.4 sat
untagged for nine days while the site told strangers to `pip install`, and
nothing noticed until `shipped_check.py` was run on purpose.

`python3 tools/shipped_check.py` is the thing that actually catches drift here:
panel version, README badge, working tree, unpushed commits, the tag, what
useclique.dev tells a stranger to install, and what PyPI serves. Ten seconds,
and it is in `CLAUDE.md` as the last thing before calling a session done.

Reordered 2026-08-29 after reading Codeman's feature list against ours
([ideas-inbox.md](ideas-inbox.md)). The phone moved up because it is the one
part of this that has never actually been used on a phone.

## The order lives on the board

Ranked work is tracked on the **CLIque** board in Kaneo, one card each, so the
panel, the desktop and the phone sit in one list instead of three. This file
keeps what does not fit on a card: why something is ranked where it is, what
was measured, and what was tried and did not work.

Next, in order, as of 2026-09-21 (evening):

| | Card |
|---|---|
| 13px of phantom horizontal scroll on a phone | CLQ-62 |
| Local echo, so the pane stops feeling like a web page | CLQ-64 |
| The key row keys are too narrow, and that is a decision — CSS already has `overflow-x: auto`; the card's own measured widths may be stale, needs a real phone check before it needs code | CLQ-63 |
| Auto-resume when a usage limit resets — the probe now exists for Claude, Codex and Grok (see "Grok usage probe" below); Gemini/Antigravity are checked and blocked, not just unresearched. The resume trigger itself is not built | CLQ-68 |
| The rest of the phone pass | CLQ-65 |
| MCP server, phase 2: write verbs, blocked on CLQ-52's policy model | CLQ-76 |
| Operator: BYOK that narrates the fleet, never a fourth agent | CLQ-52 |
| Typography | CLQ-67 |

Four shipped 2026-09-21, see **Shipped off this list** below: per-session CPU
(CLQ-56, 0.73.0), session templates (CLQ-54, 0.74.0), an MCP server, read-only
phase (CLQ-53, 0.75.0), and the sidebar usage panel (CLQ-75, 0.77.0), built
directly rather than with Grok, since the design decisions (placement, the
idle/running distinction, reusing the status bar's own meter markup) were the
bulk of the work.

The phone and desktop cards live on the same board: CLQ-10 to CLQ-17 and
CLQ-59, CLQ-60 for Android, CLQ-57 and CLQ-58 for the desktop.

**CLQ-61 is the refuse list** and is marked done on purpose, because the
decision is the deliverable. Read it before building anything agentic.

## Why the mobile work is still at the top

`agent-infra/tools/mobile_view.py` renders iOS in WebKit and Android in
Chromium at real device sizes and pixel ratios, so these were measured rather
than suspected. Most of what it found is fixed:

- ~~34 inputs under 16px~~ done in 0.64.0. None left on any device.
- ~~29 tap targets under 44px~~ done in 0.64.0: 34 down to 9, and the nine are
  the key row, which is CLQ-63 rather than a defect.
- ~~The status bar hanging off the screen~~ done in 0.64.0. 460px of controls
  in a 393px phone was what the clipped icons actually were.
- ~~The login page~~ done in 0.60.0, along with installing as an app.

Grok CLI read the whole mobile surface on 2026-09-02
([mobile-review-grok.md](mobile-review-grok.md)). All five of its findings are
fixed, in 0.66.1 and 0.67.0, each checked against the code before it was
believed. Worth repeating as a method: a second model reading for one question
found more in an hour than the tool-driven passes had in a week, and the two it
found first were both things the suite was green through.

What is left is CLQ-62 and CLQ-63. The resize fight that made the panel
unusable on a phone is fixed in 0.58.0 and scrolling in 0.61.0. Still worth
half an hour with a real handset afterwards, because the tool cannot see the
notch and does not run in standalone mode.

## Shipped off this list

- **Grok usage probe**, 0.80.0. 0.78.0 called Grok's stored credential
  unusable for a balance check; it turns out `~/.grok/auth.json` holds an
  OAuth token, just keyed by the issuer and client id rather than a fixed
  field, and that token reads the same weekly-credit endpoint
  (`cli-chat-proxy.grok.com`) the CLI's own usage screen calls. `usage.py`
  gained a `*` path step, a dict's one value regardless of its key, so the
  probe can still be one declarative block instead of a Grok-shaped code
  path. Verified against a live account. Part of CLQ-68; Gemini and
  Antigravity remain blocked as they were.
- **A "compacting" ring state, plus a spinning favicon**, 0.79.0. Detected the
  same way waiting/error already are: a regex against the pane's own text,
  declared per-CLI in `clis.toml`, generic default (`"(?i)compacting"`) so
  most CLIs need no config. Not "needs you", so it stays out of the inbox and
  the tab-title count — but it spins the browser tab's favicon, the one
  signal a browser renders outside the page itself, for catching it from a
  backgrounded tab. CLQ-77, asked in chat rather than off this list.
- **Codex usage probe**, 0.78.0. `codex login`'s ChatGPT OAuth token reads
  plan usage straight from `chatgpt.com/backend-api/wham/usage`, verified
  against a real install. Its reset time is Unix seconds rather than
  Claude's ISO string, so `usage.py` gained `_resets_at`, which accepts
  either and always hands the browser ISO. Checked Grok, Gemini and
  Antigravity too: Grok's stored credential is not a usable balance-check
  key (the only real signal is an actual CLI invocation, which would spend
  real usage to check), Gemini's credential file is encrypted at rest, and
  Antigravity's token has no known usage endpoint. Part of CLQ-68.
- **The sidebar usage panel**, 0.77.0. Collapsed "Plan usage" row above the
  version footer, opens into a bar per installed CLI with a probe, dimmed and
  tagged idle when it is not the active session's CLI. Reuses the status
  bar's own meter markup (`planMeter`, extracted out of `renderPlan`).
  Backend: `Panel.usage_now(all_installed=True)` and `GET
  /api/usage?all=1`, gated behind opening the panel rather than the routine
  poll. CLQ-75.
- **Per-session CPU**, 0.73.0. Same process-tree walk that already gave `rss`
  now also reports CPU percent, cached on the same 8-second cycle. The reap
  advisor half of the card (idle time plus these two numbers deciding what to
  clean up) was not built — a natural next step, not this pass. CLQ-56.
- **Session templates**, 0.74.0. A saved CLI + directory + folder + name +
  starter prompt. Settings > Templates mirrors Settings > Snippets exactly.
  No folder field in the Settings row yet, so a template made through the UI
  always lands Ungrouped. CLQ-54.
- **An MCP server, read-only phase**, 0.75.0. `clique/mcp_server.py`, stdlib
  only, hand-rolled JSON-RPC — list_sessions, get_session, wait,
  preview_pane, conversation. Deliberately stops there: anything that can
  send keys into a pane needs the policy model CLQ-52 owns, split off as
  CLQ-76 rather than built ahead of that policy. CLQ-53.
- **The worktree setup hook**, 0.69.0. `.clique-setup` and `.clique-copy`,
  both ordinary tracked files in the repo rather than CLIque settings, so a
  worktree made on any machine gets them for free — no more failing on the
  first `npm i` because a fresh worktree has no `.env`. CLQ-55.
- **`GET /api/briefing`**, 0.68.4. Every session actually waiting on or
  erroring for a person, in one call, reshaped from what `sessions_view()`
  already computes. No UI reads it yet — the right rail and the Android
  notification body are still ahead of it. CLQ-51.
- **CLI-declared quick commands**, 0.68.3. A `quick_commands` list in
  `clis.toml`, next to `modes`; one row of one-tap buttons, no per-CLI code.
  Claude Code got `/clear` and `/compact`. CLQ-66.
- **QR login**, 0.68.0. The pairing code was already here; what was missing was
  the browser, because a scanned code had to become a session cookie rather
  than an API token. CLQ-49.
- **An agent-facing skill.** `skills/drive-clique` exists and covers state,
  wait, peek, transcript, send, worktrees. Its read-only surface now has an
  MCP version too, so any client can drive those reads without loading a
  skill first — see CLQ-53 above.
- **Use it on a phone, then fix what is wrong.** Done, and everything above
  came out of it rather than out of a guess.
- **Peek under a session row on the phone**, and **prompt history on the
  phone**, both in Android 0.3.0. CLQ-5 and CLQ-9.

## Checked off this list, 2026-08-29

Both had drifted: the work shipped and the entry stayed. Verified against the
CHANGELOG rather than by reading the code, and worth doing again before
trusting the order above.

- **Session status line.** Shipped 0.50.93. The strip under the tabs carries
  the process, the directory, the branch and its changed-file count, uptime and
  quiet time, which is every fact the entry asked for, plus the pane size added
  in 0.52.0.
- **Land on the last prompt.** Measured on 2026-08-28 and it does not
  reproduce; see the entry below.
- **Plan usage in the status bar.** Shipped 0.54.0 and reshaped in 0.54.1. The
  shape that made it work is worth copying: the panel runs a probe a CLI
  declares in `clis.toml` and never learns whose API it is, so a second vendor
  is a block of TOML. Claude is the only one with a probe today because it is
  the only one of the four installed here whose vendor publishes an endpoint.

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

All three are done, in 0.52.0, but not the way they were proposed. Tested
rather than taken on trust, and the results are worth keeping:

- **`aggressive-resize` and `-f ignore-size` are both no-ops here.** Each only
  applies to a window whose `window-size` is `smallest` or `latest`, and ours
  are locked to `manual`, where nothing but `resize-window` moves the window at
  all. Neither model knew we already lock. Setting them would have looked like
  a fix and changed nothing.
- **What the testing did find** is that only the *current* window was being
  locked. A window created afterwards inherited the loose global rule and
  collapsed to the size of the next client to attach: measured 200x50 going to
  80x23 the moment a browser arrived. Every window is locked now.
- **Global `window-size manual` is still impossible.** It kills the tmux 3.4
  server the next time a detached session is created, and `default-size` does
  not rescue it, so per-window locking is not a workaround to tidy away later.
- **The size label shipped** and is the part that will actually save time. It
  says "another window set 120x30" or "scaled to fit", with real numbers, only
  when the pane is not at its own size.

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

## The Android client

Native, and its own repo: [clique-android](https://github.com/thejdubb02/clique-android).
Its roadmap is `docs/port-plan.md` there, not here, and the work is tracked on
the **CLIque** board in Kaneo.

Two things about it that are the panel's business rather than the app's:

- **A phone claims the shared tmux window, and now gives it back.** Fixed in
  0.67.8. The client side of that rule is untouched on purpose: `recentlyUsed()`
  is also what stops two desktop panels resizing each other every three seconds.
- **Answering a signalling session must not attach to it.** Opening a session
  repaints the pane, the panel reads output-after-a-signal as the session having
  carried on, and the signal is gone in under three seconds. Measured, not
  guessed. It is why the app's Approve and Deny live on the notification. The
  same trap applies to anything else that acts on a waiting session.

Distribution is our own F-Droid repository at `fdroid.useclique.dev`, so a
release reaches a phone as an update notification. The official F-Droid
catalogue is a separate, slower thing and is not done.

## Not on this list

Error Lens inside the terminal, Todo Tree of agent output, GitLens blame,
Power Mode, minimaps, LLM session summaries, broadcast-to-many-sessions,
a built-in diff editor. Driver, not an IDE.

## Standing

`~/.cache/clique-visual/bin/python tools/visual_check.py` after any `web/`
change, and **open the screenshots**.
