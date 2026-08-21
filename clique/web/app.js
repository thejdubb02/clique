/* CLIque front end.
 *
 * No framework and no build step, matching the backend's argument: this thing
 * runs beside CLI sessions that each want half a gigabyte, so it earns its
 * place by being small.
 *
 * Two ideas carry most of the file:
 *   - A *tab* is a live view. Opening one attaches a WebSocket and a PTY;
 *     closing one tears both down. The tmux session behind it is untouched,
 *     which is why closing a tab is safe and killing is a separate, confirmed
 *     action.
 *   - The *sidebar* is the library. It lists every session whether or not it
 *     has a tab, so a session started last week is somewhere you can find it.
 */

const $ = (sel) => document.querySelector(sel);
const api = async (path, opts = {}) => {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  if (res.status === 401) { location.reload(); throw new Error("unauthorized"); }
  const text = await res.text();
  const body = text ? JSON.parse(text) : {};
  if (!res.ok) throw new Error(body.error || res.statusText);
  return body;
};

let state = { folders: [], sessions: [], clis: [], stats: {}, settings: {} };
let openTabs = [];            // session ids, in tab order
let activeId = null;
const SESSION_DRAG = "text/clique-session";
const FOLDER_DRAG = "text/clique-folder";
let sidebarDragged = false;   // a drag is not a click; the next click is swallowed
function markSidebarDrag() {
  sidebarDragged = true;
  clearTimeout(markSidebarDrag.timer);
  markSidebarDrag.timer = setTimeout(() => { sidebarDragged = false; }, 400);
}
const terms = new Map();      // id -> { term, fit, ws, el, retry }
let repeat = 1;
/* Which of the view-groups are shut.
 *
 * Running, Ungrouped and Archived are views over the sessions, not folders, so
 * there is no folder record to hold the flag. It goes in the server's settings
 * instead — a real folder's collapsed state already syncs, and these being the
 * exception meant the same sidebar looked different on the phone.
 *
 * Archived starts shut, because it is the one group whose whole point is being
 * out of the way. This value stands in until the first poll answers. */
const VIEWS_KEY = "clique.viewsCollapsed";   // read once, to lift the old copy
let viewsCollapsed = new Set(["__archived"]);
/* Sessions that were producing output on the previous poll. A busy -> quiet
 * transition is what "this one finished" means here, which is why it needs a
 * memory of the last poll rather than just the current state. */
const wasBusy = new Map();
const attention = new Set();   // session ids waiting to be looked at

/* Unread: this pane has produced output since you last looked at it.
 *
 * Flashing says *something happened*; this says *what you have not seen*, and
 * it survives you being away for an hour. Both facts already exist — tmux's
 * activity clock and the last_seen we already write on every tab switch — so
 * this stores nothing new. The pane you are looking at is never unread. */
function unread(s) {
  return Boolean(s && s.alive && s.id !== activeId
                 && s.activity > (s.last_seen || 0));
}

/* ------------------------------------------------------------------- helpers */

function session(id) { return state.sessions.find((s) => s.id === id); }

function ago(epoch) {
  if (!epoch) return "";
  const secs = Math.max(0, Math.floor(Date.now() / 1000 - epoch));
  if (secs < 60) return secs + "s";
  if (secs < 3600) return Math.floor(secs / 60) + "m";
  if (secs < 86400) return Math.floor(secs / 3600) + "h";
  return Math.floor(secs / 86400) + "d";
}

/* The CLI marker: icon, colour chip, both, or nothing.
 *
 * Kept separate from the status dot on purpose. The dot says whether a session
 * is alive and watched; the marker says which CLI it is. Folding them together
 * would mean turning icons off also cost you the ability to see what is
 * running, which is not a trade anyone asked for. */
/* What a session is *doing*, which is not the same as whether it is attached.
 *
 * Six states, and only four of them draw anything:
 *
 *   error    — it said so, or its pane matched an error pattern
 *   asking   — work is paused on a question. A permission prompt, a y/n,
 *              a numbered choice. Not working: nothing moves until you answer.
 *   working  — output is arriving. The only state that spins.
 *   unseen   — it finished, and this tab has not been opened since
 *   idle     — alive and quiet and read. Draws nothing, because most sessions
 *              are in this state most of the time and a sidebar of twenty
 *              indicators saying "fine" is a sidebar of noise.
 *   stopped  — the process is gone
 *
 * Asking and unseen must not share a motion. One is blocked on you; the
 * other is a thing that happened while you were elsewhere.
 *
 * `busy` from the server is "output within the last two seconds", polled every
 * three, so a CLI that writes in bursts flickers between busy and not. A
 * spinner that blinks on and off twice a second is worse than no spinner, so
 * the working state is held briefly after the last burst — long enough to
 * bridge the gaps in ordinary output, short enough that a finished session
 * still settles within a poll or two. */
const BUSY_HOLD_MS = 6000;
const busyUntil = new Map();   // id -> when its indicator may stop spinning

function noteBusy(sessions) {
  const now = Date.now();
  for (const s of sessions) {
    if (s.busy) busyUntil.set(s.id, now + BUSY_HOLD_MS);
  }
  // A session that has gone away should not keep a timer alive with it.
  for (const id of [...busyUntil.keys()]) {
    if (!sessions.some((s) => s.id === id)) busyUntil.delete(id);
  }
}

function workState(s) {
  if (!s) return "idle";
  if (!s.alive) return "stopped";
  // What the session said about itself beats anything derived from watching
  // it, and an error beats a question: a CLI that failed and then offered a
  // prompt is reporting the failure, which is the more useful of the two.
  // A question beats working: a blinking permission prompt still ticks the
  // activity clock, and that is work *paused*, not work happening.
  if (s.signal === "error") return "error";
  if (s.signal === "waiting") return "asking";
  if (s.busy || (busyUntil.get(s.id) || 0) > Date.now()) return "working";
  if (attention.has(s.id)) return "unseen";
  // Unread deliberately does *not* appear here. It already has a mark of its
  // own beside the name, and one fact drawn twice is how a row stops being
  // readable at a glance.
  return "idle";
}

/* The colour for a CLI: whatever the person chose, or what clis.toml ships.
 *
 * One function so there is one answer. The shipped palette is a starting
 * point, not a decision — a colour that reads well on the built-in dark theme
 * can vanish on someone's Solarized, and that is not a reason to make them
 * live with it. */
/* Which CLI you are typing into, said in colour.
 *
 * Nine panes of black text look identical, and the moment it matters is the
 * moment after you switch — a Claude prompt typed into a shell is a mistake
 * you only notice once it has run. So the edge of the pane and the top of the
 * active tab take the CLI's colour, and switching repaints them.
 *
 * One custom property, set in one place. Everything that wants to follow the
 * active CLI reads `--cli`, which is why turning the whole thing off is one
 * assignment rather than a hunt through the stylesheet. */
/* Scroll the sidebar to the session you just switched to.
 *
 * Only when it is actually out of view, and never smoothly — the sidebar is a
 * list someone is reading, and animating it under them on every tab change is
 * the kind of motion that reads as the page misbehaving rather than helping.
 * A collapsed folder has no row to reach, and its header is already marked. */
function revealActive() {
  requestAnimationFrame(() => {
    const row = document.querySelector(".session.active");
    if (!row) return;
    const tree = $("#tree");
    const rowBox = row.getBoundingClientRect();
    const treeBox = tree.getBoundingClientRect();
    if (rowBox.top < treeBox.top || rowBox.bottom > treeBox.bottom) {
      row.scrollIntoView({ block: "nearest" });
    }
  });
}

function applyCliTint() {
  const s = session(activeId);
  const off = state.settings.cli_tint === false || !s;
  const colour = s ? cliColor(s.cli, s.color) : "";
  /* Set at the root, not on <main>: the sidebar is a sibling of the pane and
   * would never have seen a property scoped to it — which is how the active
   * row ended up with an invisible edge.
   *
   * Two properties, because they answer different questions. `--cli` is the
   * tint and goes transparent when the tint is switched off. `--active-edge`
   * is "which row am I on", which has to stay visible either way. */
  const root = document.documentElement.style;
  root.setProperty("--cli", off ? "transparent" : colour);
  root.setProperty("--active-edge", off || !colour ? "var(--accent)" : colour);
}

function cliColor(cliId, shipped) {
  return cssColor((state.settings.cli_colors || {})[cliId] || shipped);
}

/* A colour, or nothing.
 *
 * These values end up inside a `style` attribute built as a string, and a
 * string that is not a colour is an opening — `red" onmouseover="…` closes
 * the attribute and puts script in the sidebar. Escaping the quotes would
 * stop that one trick and still leave CSS injection through `url(…)`, so this
 * allows the three shapes that are actually used and refuses everything else.
 *
 * The server validates the same values on the way in. This is the second lock
 * on purpose: a state file written by an older version, or by hand, reaches
 * the browser without ever passing through the setter that checks. */
const CSS_COLOR = /^(?:#[0-9a-fA-F]{3}|#[0-9a-fA-F]{6}|var\(--[a-zA-Z0-9-]+\))$/;

function cssColor(value, fallback = "var(--dim)") {
  const text = String(value == null ? "" : value).trim();
  return CSS_COLOR.test(text) ? text : fallback;
}

function markerFor(item, mode) {
  if (mode === "none") return "";
  const own = cssColor(item.color);
  if (mode === "color") return `<i class="cli-chip" style="background:${own}"></i>`;

  // "icon" is the same shape in neutral grey; "both" tints it the CLI colour.
  const tint = mode === "icon" ? "var(--dim)" : own;
  if (item.icon) {
    const url = `icons/${encodeURIComponent(item.icon)}`;

    // A logo with its own background or several colours cannot be a mask —
    // it flattens to a solid square, which is how Cline and OpenCode were
    // rendering. Draw those as the real image instead. Grey mode desaturates
    // rather than tinting, since there is nothing to tint.
    if (item.icon_full_color) {
      const grey = mode === "icon" ? "filter:grayscale(1);opacity:.75;" : "";
      return `<img class="cli-img" src="${url}" alt="" style="${grey}">`;
    }

    // A single-colour glyph works as a mask: the file supplies the
    // silhouette, the panel supplies the colour. That is what lets one file
    // serve the tinted, grey and status-coloured modes.
    return `<i class="cli-icon" style="-webkit-mask-image:url(${url});` +
           `mask-image:url(${url});background:${tint}"></i>`;
  }
  // No drawing for this CLI: a letter badge, which looks deliberate and means
  // adding a CLI never waits on someone drawing an icon first.
  const letter = escapeHtml((item.label || item.cli || "?").trim()[0] || "?");
  return `<i class="cli-letter" style="color:${tint};border-color:${tint}">${letter}</i>`;
}

function markerMode(cliId) {
  const per = state.settings.marker_by_cli || {};
  return per[cliId] || state.settings.marker_default || "both";
}

function sessionMarker(s, where) {
  const on = where === "tabs" ? state.settings.markers_in_tabs
                              : state.settings.markers_in_sidebar;
  if (on === false) return "";
  const mode = markerMode(s.cli);
  const item = { color: cliColor(s.cli, s.color), icon: s.icon,
                 icon_full_color: s.icon_full_color,
                 label: s.cli_label, cli: s.cli };
  const drawn = markerFor(item, mode);
  if (!drawn) return "";

  /* A logo is a logo. It used to be recoloured to say how the session was
   * doing, which meant Claude's mark was not Claude's colour and the one
   * thing on the row you could identify at a glance stopped being reliable.
   * Shape *and* colour say which CLI it is; the ring around it says how it is
   * doing, and motion in that ring says it is working. Two facts, two places,
   * neither one borrowing the other's channel. */
  if (!statusOnIcon(s)) return drawn;
  const work = workState(s);
  return `<span class="cli-status" data-work="${work}" role="img"` +
         ` aria-label="${WORK_WORDS[work]}" title="${WORK_WORDS[work]}">${drawn}</span>`;
}

/* Whether this session's marker is standing in for the status dot.
 *
 * Only when there is actually a marker to carry it. With the marker turned
 * off — globally or for this CLI — the dot has to come back, because losing
 * status altogether is a worse trade than showing two small marks. */
function statusOnIcon(s) {
  if (!state.settings.status_on_icon) return false;
  // A full-colour logo cannot be *tinted* with the status colour, but it can
  // be ringed with it — see sessionMarker. Either way it carries status, so
  // the separate dot stays away. Showing both was the thing to fix.
  return markerMode(s.cli) !== "none";
}

function statusDot(s, where) {
  const on = where === "tabs" ? state.settings.markers_in_tabs
                              : state.settings.markers_in_sidebar;
  if (on !== false && statusOnIcon(s)) return "";
  // Same five states as the ring, and the same ring: a filled dot cannot
  // spin, so wrapping it is how the sidebar speaks the same language as
  // the tab when the logo is turned off.
  const work = workState(s);
  return `<span class="cli-status" data-work="${work}" role="img"` +
         ` aria-label="${WORK_WORDS[work]}" title="${WORK_WORDS[work]}">` +
         `<i class="dot" data-work="${work}"></i></span>`;
}

/* Said out loud, for a tooltip and for a screen reader. A ring that only means
 * something to people who can see it is half a signal. */
const WORK_WORDS = {
  working: "working",
  asking: "needs an answer",
  unseen: "finished — not opened yet",
  waiting: "needs an answer",
  error: "stopped on an error",
  idle: "idle",
  stopped: "stopped",
};

/* --------------------------------------------------------------------- state */

/* Consecutive failed polls. A blip mid-poll must not blank the UI, but a
 * *first* poll that fails leaves an app with no sidebar and no explanation
 * until the next tick — which reads as broken, because from the outside it is
 * indistinguishable from broken. */
let pollFailures = 0;

/* The workspace — which sessions have a tab, in what order, which one is in
 * front, and which view-groups are shut.
 *
 * It lives in the server's settings with everything else a person chose.
 * Losing it is the expensive kind of loss: twelve panes reopened by hand is a
 * morning, and closing a laptop should not cost that.
 *
 * Restored once, on the first poll, and deliberately not re-applied
 * afterwards. Two panels open at once would otherwise fight, each poll
 * dragging the other's tabs around mid-read. Last one to touch a tab wins the
 * stored copy, which is the right answer for one person on two devices.
 */
let workspaceRestored = false;
let workspaceTimer = null;
let pendingWorkspace = null;

function restoreWorkspace() {
  workspaceRestored = true;
  const saved = state.settings || {};
  let tabs = Array.isArray(saved.open_tabs) ? saved.open_tabs : [];
  let views = Array.isArray(saved.views_collapsed)
    ? saved.views_collapsed : ["__archived"];

  // One-time lift of what the browser was still holding, so nobody loses the
  // tabs they had open on the day this changed. The local copies are removed
  // as they are read: after this the server is the only record.
  let lifted = false;
  for (const [key, take] of [["clique.tabs", (v) => { tabs = v; }],
                             [VIEWS_KEY, (v) => { views = v; }]]) {
    const raw = localStorage.getItem(key);
    if (raw === null) continue;
    localStorage.removeItem(key);
    try {
      const parsed = JSON.parse(raw);
      if (Array.isArray(parsed)) { take(parsed); lifted = true; }
    } catch (err) { /* unreadable: the server's copy is no worse */ }
  }

  viewsCollapsed = new Set(views.filter((id) => typeof id === "string"));
  const live = tabs.filter((id) => typeof id === "string" && session(id));
  pendingWorkspace = { tabs: live, active: saved.active_tab || "", lifted };
  /* The strip can list them before any socket is open. Saving is still
   * deferred: openTabs is now filled so the first paint shows the tabs,
   * but a write here would race the attach of the active one. */
  openTabs = live;
}

function saveWorkspace(now) {
  // Never write before we have read. The first fetch used to fail (home
  // wifi, a slow cookie) and the startup `.then` still ran, saving an
  // empty strip over the tabs you left at work.
  if (!workspaceRestored) return;
  clearTimeout(workspaceTimer);
  const push = () => {
    const body = {
      open_tabs: [...openTabs],
      active_tab: activeId || "",
      views_collapsed: [...viewsCollapsed],
    };
    Object.assign(state.settings, body);
    // Not saveSettings(): that repaints the tree, the tabs and every open
    // terminal, and this fires on every step of a tab drag.
    // keepalive: a closing tab otherwise aborts the fetch, which is how
    // going home found an empty strip even after a save was sent.
    fetch("api/settings", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      keepalive: true,
    }).catch(() => {});
  };
  // Debounced, because a drag is a dozen reorders; committed at once when the
  // page is going away, which is the moment it actually matters.
  if (now) return push();
  workspaceTimer = setTimeout(push, 600);
}

async function refresh() {
  try {
    state = await api("api/state");
    pollFailures = 0;
    $("#offline").hidden = true;
  } catch (err) {
    pollFailures++;
    if (pollFailures >= 4) $("#offline").hidden = false;
    return false;
  }
  // A session killed behind our back keeps its tab until the user closes it,
  // but must not keep a dead socket open.
  openTabs = openTabs.filter((id) => session(id));
  if (!workspaceRestored) restoreWorkspace();   // before the first render
  applySettings();
  noteBusy(state.sessions);   // before anything renders a work state
  noticeFinished(state.sessions.filter((x) => openTabs.includes(x.id)));
  renderTree();
  renderTabs();
  renderStats();
  renderServices();
  renderVersion();
  reclaimSize();
  // First load pulls history in so the sidebar is complete without anyone
  // having to open the palette to trigger it.
  if (!resumable) loadResumable().then(renderTree);
  return true;
}

async function bootWorkspace() {
  /* Wait until the workspace has actually been read, then open it.
   *
   * `refresh().then(...)` used to run even when the first fetch failed
   * and retried in the background. That `.then` saved whatever tabs this
   * window had — none — and that is the empty strip waiting at home. */
  for (let i = 0; i < 10; i++) {
    if (await refresh()) break;
    await new Promise((ok) => setTimeout(ok, Math.min(250 * (i + 1), 2000)));
  }
  if (!workspaceRestored) return;

  const want = pendingWorkspace || { tabs: [], active: "" };
  openTabs = want.tabs.filter((id) => session(id));
  const pick = (want.active && openTabs.includes(want.active))
    ? want.active
    : openTabs[0];
  if (pick) {
    await openSession(pick);
    setTimeout(warmOpenTabs, 500);
  }

  const asked = new URLSearchParams(location.search).get("session");
  if (asked && session(asked)) {
    await openSession(asked);
    selectTab(asked);
  }
  if (asked) {
    const clean = new URL(location.href);
    clean.searchParams.delete("session");
    history.replaceState(null, "", clean);
  }
  saveWorkspace(true);
}

/* Load as a colour, on a continuous ramp rather than three fixed steps.
 *
 * Deliberately *not* a theme token. Green-to-red is not decoration, it is the
 * one colour convention that means the same thing to everyone, and a theme
 * that recoloured it would be repainting the gauge rather than the panel.
 * Saturation and lightness are picked to stay legible on both light and dark.
 *
 * The ramp bends at green -> amber rather than running straight to red,
 * because most of the interesting range is the first 70%: a box at 40% and a
 * box at 65% should not look the same shade of "fine".
 */
/* How hard the box is working, in four steps rather than a gradient.
 *
 * This used to be a smooth hue ramp from green to red, which looked right in
 * a screenshot and was useless in practice: on a continuum, 40% and 55% are
 * the same colour to anyone not holding a swatch, and the whole middle of the
 * range reads as one indeterminate yellow. Four hard bands can be told apart
 * at a glance and across the room, which is the only way a number you are not
 * reading does any work.
 *
 * Critical also moves. At the point where something is about to go wrong, the
 * dot should find you rather than wait to be looked at. */
const PRESSURE = [[90, "critical"], [75, "high"], [50, "busy"], [0, "calm"]];

function pressureLevel(percent) {
  const at = Math.max(0, Math.min(Number(percent) || 0, 100));
  return (PRESSURE.find(([floor]) => at >= floor) || [0, "calm"])[1];
}

/* The version, and whether there is something new behind it.
 *
 * A changelog nobody opens is a file, not a feature. The smallest honest
 * nudge is a mark on the bottom bar when the running release is not the
 * one whose notes were last read. Clicking it goes straight to them.
 *
 * Seeded rather than assumed: the first panel to load stamps whatever is
 * running, so a fresh install does not arrive already claiming to have news.
 * The mark then only ever means "you upgraded since you last looked". */
function baseVersion(text) {
  return String(text || "").split("+")[0];   // drop the +build suffix
}

function changelogHasNews(running, seen) {
  const ver = baseVersion(running);
  seen = String(seen || "");
  return Boolean(ver && seen && ver !== seen);
}

/* How many releases the settings sheet itself holds. The rest lives in the
 * repo file — a panel that starts in a quarter of a second should not try
 * to be the archive. */
const CLOG_SHOW = 5;

/* The version this page's scripts came from.
 *
 * A panel is left open for days and the server underneath it gets upgraded —
 * that is the normal way a self-hosted tool is used, and it means the browser
 * can be running last week's app.js against this week's API. Every symptom of
 * that looks like a bug in something else: a fix that "did not work", a
 * feature that is missing, a route that 404s.
 *
 * Nothing is reloaded automatically. The terminals are safe either way — they
 * live in tmux — but a page that reloads itself under someone's hands is its
 * own kind of rude. Saying so once, with a button, is enough. */
let loadedVersion = null;

function noticeUpgrade() {
  const running = state.version;
  if (!running) return;
  if (loadedVersion === null) { loadedVersion = running; return; }
  if (running === loadedVersion || noticeUpgrade.told) return;
  noticeUpgrade.told = true;
  toast(`CLIque was updated to ${baseVersion(running)} — this page is still ` +
        `running ${baseVersion(loadedVersion)}`, false,
        { label: "Reload", run: () => location.reload() });
}

function renderVersion() {
  const el = $("#version");
  const running = baseVersion(state.version);
  const seen = state.settings.changelog_seen;
  noticeUpgrade();

  if (running && !seen) {
    // First load ever. Stamp it quietly; nothing to announce.
    saveWorkspaceSetting({ changelog_seen: running });
    el.textContent = "v" + state.version;
    return;
  }

  el.textContent = "";
  const label = document.createElement("button");
  label.type = "button";
  label.className = "version-link";
  label.textContent = "v" + state.version;
  label.title = "What changed in this release";
  label.onclick = () => showChangelog(running);
  el.append(label);
  paintWhatsNew();
}

function paintWhatsNew() {
  const chip = $("#whatsNew");
  if (!chip) return;
  const running = baseVersion(state.version);
  const fresh = changelogHasNews(running, state.settings.changelog_seen);
  chip.hidden = !fresh;
}

function showChangelog(running) {
  openSettings();
  const button = document.querySelector('#setTabs button[data-pane="changelog"]');
  if (button) button.click();
  if (running) saveWorkspaceSetting({ changelog_seen: running });
  paintWhatsNew();
}

/* A settings write that must not repaint the world.
 *
 * saveSettings() re-applies everything, which is right for a preference
 * someone just changed and wrong for a bookkeeping value the user never
 * touched — repainting mid-click would fight whatever they are doing. */
function saveWorkspaceSetting(patch) {
  Object.assign(state.settings, patch);
  return api("api/settings", { method: "PATCH", body: JSON.stringify(patch) })
    .catch(() => {});
}

function paintStat(id, percent, value, title) {
  const el = $("#" + id);
  if (!el) return;
  const dot = el.querySelector(".dot");
  const slot = el.querySelector(".v");
  if (dot && !dot.style.background) dot.dataset.level = pressureLevel(percent);
  if (slot) slot.textContent = value;
  if (title) el.title = title;
}

function renderStats() {
  const st = state.stats || {};
  const gb = (mb) => (Math.round((mb || 0) / 1024 * 10) / 10).toFixed(1);

  const cpu = st.cpu ?? 0;
  paintStat("cpu", cpu, Number(cpu).toFixed(1) + "%", `cpu ${cpu}%`);

  const mem = st.mem || {};
  const totalG = Math.round((mem.total_mb || 0) / 1024);
  paintStat("mem", mem.percent,
            gb(mem.used_mb) + "/" + totalG + "G",
            `memory ${mem.percent ?? 0}% used`);

  const load = st.load || {};
  paintStat("load", (load.ratio || 0) * 100,
            Number(load.one ?? 0).toFixed(2),
            `${load.one} / ${load.five} / ${load.fifteen} over ${load.cores} cores`);

  const disk = st.disk || {};
  paintStat("disk", disk.percent,
            Number(disk.free_gb ?? 0).toFixed(1) + "G free",
            `disk ${disk.percent ?? 0}% used`);

  // Any swap in use means memory pressure already happened. The column stays
  // even at zero, so it appearing does not shove the tabs sideways.
  const swap = st.swap || {};
  const swapEl = $("#swap");
  if (swapEl) {
    swapEl.classList.toggle("is-off", !(swap.used_mb > 0));
    paintStat("swap", Math.max(swap.percent || 0, 70),
              gb(swap.used_mb) + "G",
              `swap ${swap.percent ?? 0}% used — memory pressure has already happened`);
  }

  const n = st.clients ?? 0;
  const tabs = openTabs.length;
  let why;
  if (!n && !tabs) {
    why = "No live views. Each open tab becomes one once it is hooked up.";
  } else if (n === tabs) {
    why = `${n} live view${n === 1 ? "" : "s"} — one per open tab in this window.`;
  } else if (n > tabs) {
    why = `${n} live views. This window has ${tabs} tab${tabs === 1 ? "" : "s"}; extras are another window or a phone.`;
  } else {
    why = `${n} live view${n === 1 ? "" : "s"} of ${tabs} tabs — the rest are still hooking up.`;
  }
  paintStat("clients", 0, String(n), why);
}

function sparkline(samples, key, color, height) {
  if (samples.length < 2) return "";
  const width = 320;
  const step = width / (samples.length - 1);
  const points = samples.map((s, i) =>
    `${(i * step).toFixed(1)},${(height - (s[key] / 100) * height).toFixed(1)}`);
  return `<polyline fill="none" stroke="${color}" stroke-width="1.5" ` +
         `points="${points.join(" ")}"/>` +
         `<polygon fill="${color}" opacity="0.13" ` +
         `points="0,${height} ${points.join(" ")} ${width},${height}"/>`;
}

async function showHistory() {
  const box = $("#history");
  if (!box.hidden) { box.hidden = true; return; }
  let data;
  try {
    data = await api("api/stats/history?minutes=60");
  } catch (err) {
    return;
  }
  const h = 54;
  const covered = data.covered_minutes;
  box.innerHTML =
    `<div class="hist-head"><span>Last ${covered || 0} min</span>` +
    `<span class="dim">peak cpu ${data.peak_cpu}% · peak mem ${data.peak_mem}%</span></div>` +
    `<svg viewBox="0 0 320 ${h}" preserveAspectRatio="none" class="spark">` +
    sparkline(data.samples, "cpu", "var(--accent)", h) +
    sparkline(data.samples, "mem", "var(--warn)", h) +
    `</svg>` +
    `<div class="hist-key"><i style="background:var(--accent)"></i>cpu` +
    `<i style="background:var(--warn)"></i>memory</div>` +
    (data.samples.length < 2
      ? `<p class="dim">Collecting — the series starts when the panel starts.</p>` : "");
  box.hidden = false;
}

/* Somebody else's outage, said once.
 *
 * A CLI that has gone quiet and a provider that is down look identical from
 * the outside, and the difference is whether you spend twenty minutes
 * debugging your own prompt. The provider already publishes the answer.
 *
 * Drawn only when there is something to say. "All systems operational" is not
 * news, and a bar that is always on screen is a bar nobody reads on the day it
 * finally says something — which is the same reason an idle session draws no
 * indicator. */
const SERVICE_WORDS = {
  maintenance: "under maintenance",
  minor: "having trouble",
  major: "having problems",
  critical: "down",
};

function renderServices() {
  const host = $("#services");
  const rows = state.services || [];
  host.textContent = "";
  host.hidden = !rows.length;
  if (!rows.length) return;

  for (const row of rows) {
    const cli = (state.clis || []).find((c) => c.id === row.cli);
    const bar = document.createElement("div");
    bar.className = "svc";
    bar.dataset.level = row.indicator;

    const mark = document.createElement("span");
    mark.className = "svc-mark";
    if (cli) {
      mark.innerHTML = markerFor(
        { color: cliColor(cli.id, cli.color), icon: cli.icon,
          icon_full_color: cli.icon_full_color, label: cli.label, cli: cli.id },
        markerMode(cli.id) === "none" ? "color" : markerMode(cli.id));
    }

    // textContent throughout: this is the one place in the panel showing text
    // that came from somebody else's server, and a status page is not a thing
    // to hand markup privileges to.
    const said = document.createElement("span");
    said.className = "svc-said";
    said.textContent = `${row.label} is ${SERVICE_WORDS[row.indicator] || row.indicator}`;

    const detail = document.createElement("span");
    detail.className = "svc-detail";
    detail.textContent = row.description || "";

    bar.append(mark, said, detail);

    if (row.url) {
      const link = document.createElement("button");
      link.type = "button";
      link.className = "svc-link";
      link.textContent = "Status page";
      // The same rule the terminal's links follow: a plain click opens a tab,
      // Ctrl or Cmd opens a window.
      link.onclick = (ev) => openLink(row.url, ev.ctrlKey || ev.metaKey);
      bar.append(link);
    }
    host.append(bar);
  }
}

/* ------------------------------------------------------------------- sidebar */

function treeFingerprint() {
  /* What the sidebar actually shows. Ages bucket through ago(), so a
   * session that is "3m" old does not rebuild the list every poll — only
   * when it ticks over to "4m", or when a ring, name, or folder changes. */
  const query = ($("#q") && $("#q").value.trim().toLowerCase()) || "";
  const s = state.settings || {};
  const sess = (state.sessions || []).map((x) => [
    x.id, x.name, x.folder || "", x.archived ? 1 : 0, x.alive ? 1 : 0,
    workState(x), unread(x) ? 1 : 0, attention.has(x.id) ? 1 : 0,
    x.signal || "", x.saying || "", x.cli || "", x.cwd || "",
    x.branch || "", x.dirty || 0,
    ago(x.created), x.cli_session_id || "",
  ].join("\x1f")).join("\x1e");
  const folders = (state.folders || []).map((f) =>
    [f.id, f.name, f.color, f.collapsed ? 1 : 0].join("\x1f")).join("\x1e");
  const hist = (resumable || []).map((c) => [
    c.cli_session_id || "", c.label, c.cwd, c.folder || "",
    ago(c.updated), c.repeats || 1, c.cli || "",
  ].join("\x1f")).join("\x1e");
  return [
    query, activeId || "",
    [...viewsCollapsed].sort().join(","),
    openTabs.join(","),
    [...historyOpen].sort().join(","),
    s.history_in_sidebar, s.history_days, s.font_panel,
    JSON.stringify(s.markers || {}),
    JSON.stringify(s.cli_colors || {}),
    folders, sess, hist,
  ].join("\x1d");
}

let treeFp = "";
let tabsFp = "";

function renderTree() {
  const tree = $("#tree");

  /* The list holds still while someone is typing a name into it.
   *
   * This is rebuilt from scratch on every poll, three seconds apart, and an
   * inline rename puts a live <input> inside it. Rebuilding removed that input
   * mid-word — and removing a focused element fires `blur`, which is what
   * commits the rename. So typing a name got chopped off and saved at a
   * three-second cadence, which reads as the panel randomly deciding you were
   * finished.
   *
   * Read from the DOM rather than kept as a flag: a flag can be left set by a
   * path that threw, and the failure mode of that is a sidebar frozen forever.
   * If the input is gone, rendering resumes on its own. */
  if (tree.querySelector("input")) return;

  const fp = treeFingerprint();
  if (fp === treeFp && tree.childElementCount) return;
  treeFp = fp;
  const scroll = tree.scrollTop;

  const query = $("#q").value.trim().toLowerCase();
  tree.innerHTML = "";

  const matches = (s) =>
    !query || s.name.toLowerCase().includes(query)
      || s.cwd.toLowerCase().includes(query)
      || (s.branch || "").toLowerCase().includes(query);

  // Running-and-open first: what you are working on should not be somewhere
  // you have to scroll to.
  const groups = [];
  const live = state.sessions.filter((s) => !s.archived);
  const filed = (s) => state.folders.some((f) => f.id === s.folder);
  /* Running is the inbox: alive, not filed, and not already a tab. A
   * session you put in a folder stays there — even with no tab open.
   * Pulling filed ones up here is why a new browser looked like nothing
   * was organised: the folders were saved, the tree just hid them. */
  const running = live.filter((s) => s.alive && !openTabs.includes(s.id) && !filed(s));
  if (running.length) {
    groups.push({ id: "__running", name: "Running", color: "#2d7d46", pinned: true,
                  collapsed: viewsCollapsed.has("__running"), sessions: running });
  }

  /* Ungrouped sits above the folders, not below them. A session you have
   * just started is the one you are looking for, and filing it is a decision
   * you make afterwards — so it has to be somewhere you can see without
   * scrolling past every folder you already have. */
  const unfiled = live.filter((s) => !running.includes(s) && !filed(s));
  if (unfiled.length) {
    groups.push({ id: "__unfiled", name: "Ungrouped", color: "#8b8b8b",
                  collapsed: viewsCollapsed.has("__unfiled"), sessions: unfiled });
  }

  for (const folder of state.folders) {
    groups.push({
      ...folder,
      sessions: live.filter((s) => s.folder === folder.id),
    });
  }

  // Archived last, collapsed unless asked for. Archiving never touched tmux,
  // so everything here is still running and one click from coming back.
  const archived = state.sessions.filter((s) => s.archived);
  if (archived.length) {
    groups.push({ id: "__archived", name: "Archived", color: "#5a5a5a",
                  collapsed: viewsCollapsed.has("__archived"), sessions: archived });
  }

  for (const group of groups) {
    const shown = group.sessions.filter(matches);
    if (query && !shown.length) continue;

    const head = document.createElement("div");
    /* A folder holding the session you are looking at says so, whether it is
     * open or shut. Collapsed groups were the gap: the active row carries the
     * highlight, and a collapsed group draws no rows, so switching to a
     * session inside one left the entire sidebar looking like nothing was
     * selected. The header is the only thing on screen at that point, so it
     * has to be the thing that answers. */
    const holdsActive = group.sessions.some((x) => x.id === activeId);
    head.className = "folder-head" + (holdsActive ? " has-active" : "");
    head.dataset.folder = group.id;
    // Only real folders can be edited. Running, Ungrouped and Archived are
    // views over the sessions, not things with a name and a colour.
    const editable = !group.pinned && group.id.startsWith("f-");
    head.innerHTML =
      `<span class="caret">${icon(group.collapsed ? "chevron-right" : "chevron-down")}</span>` +
      `<i class="dot" style="background:${cssColor(group.color)}"></i>` +
      `<span class="name">${escapeHtml(group.name)}</span>` +
      (editable ? `<button class="folder-edit" title="Rename, recolor or delete">${icon("pencil")}</button>` : "") +
      `<span class="count">${shown.length}` +
      (historyCount(group) ? `<i class="from-history">+${historyCount(group)}</i>` : "") +
      `</span>`;
    head.onclick = () => {
      if (sidebarDragged) { sidebarDragged = false; return; }
      toggleFolder(group);
    };
    if (editable) {
      head.draggable = true;
      head.ondragstart = (ev) => {
        markSidebarDrag();
        ev.dataTransfer.setData(FOLDER_DRAG, group.id);
        ev.dataTransfer.effectAllowed = "move";
        head.classList.add("dragging");
      };
      head.ondragend = () => {
        head.classList.remove("dragging");
        clearTreeDrops();
      };
      head.oncontextmenu = (ev) => folderMenu(ev, group);
      // Right-click still works and always did; nothing announced it. The
      // pencil is the same menu with a way to find it.
      head.querySelector(".folder-edit").onclick = (ev) => {
        ev.stopPropagation();
        folderMenu(ev, group);
      };
    }
    wireDrop(head, group.id);
    tree.appendChild(head);

    if (group.collapsed && !query) continue;
    for (const s of shown) tree.appendChild(sessionRow(s));
    for (const row of historyRows(group, query)) tree.appendChild(row);
  }

  const dots = $("#railDots");
  dots.innerHTML = "";
  /* The rail is the sidebar with the words taken away, not a different thing.
   * It used to draw a plain dot regardless of what you had chosen, so
   * collapsing the sidebar quietly threw away the CLI markers *and* the
   * status rings — the two marks that make a column of sessions readable at
   * all. Same two calls the sidebar row makes, so whichever of them your
   * settings put in charge is the one that appears here too. */
  for (const s of state.sessions.filter((x) => x.alive)) {
    const button = document.createElement("button");
    button.className = "rail-dot";
    button.type = "button";
    button.title = s.name + " — " + WORK_WORDS[workState(s)];
    button.innerHTML = sessionMarker(s, "sidebar") + statusDot(s, "sidebar");
    button.onclick = () => openSession(s.id);
    dots.appendChild(button);
  }
  tree.scrollTop = scroll;
}

/* Recent enough to be worth a row.
 *
 * Without a ceiling this list only grows: a month of work is several hundred
 * conversations, and a conversation from three weeks ago is something you go
 * and search for rather than something you scroll past on the way to what is
 * running. The palette still has all of it. */
function recentEnough(conv) {
  const days = state.settings.history_days;
  if (!days) return true;
  return (Date.now() / 1000 - (conv.updated || 0)) < days * 86400;
}

/* Past conversations belonging to a folder, after folding repeats. Used by the
 * folder header so a folder with no live sessions does not read as empty when
 * it holds two hundred conversations. */
function historyCount(group) {
  if (state.settings.history_in_sidebar === false) return 0;
  if (!resumable || group.id === "__running" || group.id === "__archived") return 0;
  const live = new Set(state.sessions.map((s) => s.cli_session_id).filter(Boolean));
  const keys = new Set();
  for (const c of resumable) {
    if (live.has(c.cli_session_id)) continue;
    if (!recentEnough(c)) continue;
    if ((c.folder || "__unfiled") !== group.id) continue;
    keys.add(`${c.label}\u0000${c.cwd}`);
  }
  return keys.size;
}

/* How many past conversations a folder shows before it asks. Every folder
 * expanded at once is several hundred rows; this is the count where a folder
 * still reads as a folder. */
const HISTORY_SHOWN = 6;
const historyOpen = new Set();

/* Past conversations belonging to this folder, as dimmed rows under the live
 * sessions.
 *
 * The order is deliberate: what is running comes first and always, because
 * that is what the sidebar is for. History sits underneath it, visibly
 * quieter, and is the answer to "where did I leave that" rather than
 * competing with "what is happening now". */
function historyRows(group, query) {
  if (state.settings.history_in_sidebar === false) return [];
  if (!resumable || group.id === "__running" || group.id === "__archived") return [];

  const live = new Set(state.sessions.map((s) => s.cli_session_id).filter(Boolean));
  const matching = resumable.filter((c) => {
    if (live.has(c.cli_session_id)) return false;   // already open as a session
    if (!recentEnough(c)) return false;
    const folder = c.folder || "__unfiled";
    if (folder !== group.id) return false;
    if (!query) return true;
    return (c.label + " " + c.cwd).toLowerCase().includes(query);
  });

  /* Fold repeats together. A scheduled agent produces one transcript per run
   * under an identical opening line, and six rows reading "You are the
   * unattended responder…" is six rows of nothing. Keep the newest, count the
   * rest, and let the count say there is more behind it. */
  const seen = new Map();
  for (const c of matching) {
    const key = `${c.label}\u0000${c.cwd}`;
    const kept = seen.get(key);
    if (!kept) seen.set(key, { ...c, repeats: 1 });
    else {
      kept.repeats++;
      if (c.updated > kept.updated) Object.assign(kept, c, { repeats: kept.repeats });
    }
  }
  const mine = [...seen.values()].sort((a, b) => b.updated - a.updated);
  if (!mine.length) return [];

  const expanded = historyOpen.has(group.id) || Boolean(query);
  const rows = (expanded ? mine : mine.slice(0, HISTORY_SHOWN)).map(historyRow);
  if (!expanded && mine.length > HISTORY_SHOWN) {
    const more = document.createElement("div");
    more.className = "history-more";
    more.textContent = `${mine.length - HISTORY_SHOWN} more from history`;
    more.onclick = () => { historyOpen.add(group.id); renderTree(); };
    rows.push(more);
  } else if (expanded && !query && mine.length > HISTORY_SHOWN) {
    const less = document.createElement("div");
    less.className = "history-more";
    less.textContent = "Show less";
    less.onclick = () => { historyOpen.delete(group.id); renderTree(); };
    rows.push(less);
  }
  return rows;
}

function historyRow(conv) {
  const cli = (state.clis || []).find((c) => c.id === conv.cli);
  const row = document.createElement("div");
  row.className = "session history";
  row.title = `${conv.cwd}\nResume this conversation`;
  const repeats = conv.repeats > 1 ? `<span class="repeats">×${conv.repeats}</span>` : "";
  row.innerHTML =
    `<span class="pal-icon">${cli ? markerFor(
        { color: cliColor(cli.id, cli.color), icon: cli.icon, icon_full_color: cli.icon_full_color,
          label: cli.label, cli: cli.id },
        markerMode(cli.id) === "none" ? "color" : markerMode(cli.id)) : ""}</span>` +
    `<span class="meta"><span class="name">${escapeHtml(conv.label)}${repeats}</span>` +
    `<span class="path">${escapeHtml(conv.project)}</span></span>` +
    `<span class="age">${ago(conv.updated)}</span>`;
  row.onclick = async () => {
    row.classList.add("starting");
    try {
      await resumeConversation(conv);
    } finally {
      row.classList.remove("starting");
    }
  };

  /* These rows had no menu at all, so right-clicking one produced the
   * browser's — which is not "nothing happens", it is the panel visibly not
   * being in charge of its own sidebar. They are not sessions and there is
   * nothing here to kill: a past conversation is a transcript another tool
   * wrote, and deleting somebody else's data is not this program's business.
   * What is on offer is what you can actually do with one. */
  row.oncontextmenu = (ev) => showMenu(ev, [
    ["Resume this conversation", () => resumeConversation(conv)],
    ["Copy its directory", () => copyText(conv.cwd).then(() => toast("Path copied"))],
    ["Hide past conversations", () =>
      saveSettings({ history_in_sidebar: false }).then(renderTree)],
  ]);
  return row;
}

function sessionRow(s) {
  const row = document.createElement("div");
  row.className = "session" + (s.id === activeId ? " active" : "") +
    (s.alive ? "" : " dead") + (s.busy ? " busy" : "") +
    (unread(s) ? " unread" : "") +
    (workState(s) === "asking" ? " asking" : "") +
    (attention.has(s.id) ? " attention" : "");
  row.draggable = true;
  row.dataset.id = s.id;
  /* A session that wants you says what for, in the row.
   *
   * The ring says a session is waiting; on its own that still means opening
   * the tab to find out what it is waiting *for*. The answer is one line, and
   * the row already has a line — the working directory, which is the least
   * urgent thing on screen at the moment something is blocked.
   *
   * This replaced a hover preview. A popup has to be summoned, positioned and
   * layered above everything else, and each of those is a way to be wrong: the
   * layering one broke right-click on every row for a while. A line that is
   * simply there when it matters has none of those problems. */
  const wants = s.signal && s.saying;
  const git = s.branch
    ? escapeHtml(s.branch) + (s.dirty
        ? ` · <span class="git-dirty">${s.dirty} changed</span>` : "")
    : "";
  let pathHtml;
  let pathClass = "path";
  if (!s.alive) {
    pathHtml = escapeHtml("Stopped — click to start again");
  } else if (wants) {
    pathHtml = escapeHtml(s.saying);
    pathClass += " saying";
  } else if (git) {
    pathHtml = git;
  } else {
    pathHtml = escapeHtml(s.cwd);
  }
  row.innerHTML =
    statusDot(s, "sidebar") +
    sessionMarker(s, "sidebar") +
    `<span class="meta"><span class="name">${escapeHtml(s.name)}</span>` +
    `<span class="${pathClass}">${pathHtml}</span>` +
    `</span>` +
    `<span class="age">${ago(s.created)}</span>`;
  // The directory is still one hover away, rather than gone — and so is the
  // branch, when the line is showing a question instead.
  const gitLine = s.branch
    ? (s.dirty ? `${s.branch} · ${s.dirty} changed` : s.branch) : "";
  row.title = [s.cwd, gitLine, wants ? s.saying : ""].filter(Boolean).join("\n");

  /* A draft dragged out of the prompt box and dropped on a session.
   *
   * Deliberately its own MIME type rather than text/plain: the row already
   * accepts a dropped session id as "file this here", and a dropped paragraph
   * of prose landing in that handler would try to move a folder to a session
   * that does not exist. Two meanings, two types, no guessing. */
  row.addEventListener("dragover", (ev) => {
    if (ev.dataTransfer.types.includes(DRAFT_TYPE) && s.id !== draftFor) {
      ev.preventDefault();
      row.classList.add("drop");
      return;
    }
    if (!(ev.dataTransfer.types.includes(SESSION_DRAG)
          || ev.dataTransfer.types.includes("text/plain"))) return;
    ev.preventDefault();
    const box = row.getBoundingClientRect();
    const after = ev.clientY > box.top + box.height / 2;
    row.classList.toggle("drop-before", !after);
    row.classList.toggle("drop-after", after);
  });
  row.addEventListener("dragleave", () =>
    row.classList.remove("drop", "drop-before", "drop-after"));
  row.addEventListener("drop", (ev) => {
    if (ev.dataTransfer.types.includes(DRAFT_TYPE)) {
      ev.preventDefault();
      row.classList.remove("drop");
      moveDraft(s.id);
      return;
    }
    const moved = ev.dataTransfer.getData(SESSION_DRAG);
    if (!moved || moved === s.id) return;
    ev.preventDefault();
    row.classList.remove("drop-before", "drop-after");
    const box = row.getBoundingClientRect();
    const after = ev.clientY > box.top + box.height / 2;
    moveSession(moved, s.id, after);
  });

  row.onclick = () => {
    if (sidebarDragged) { sidebarDragged = false; return; }
    openSession(s.id);
  };
  row.ondblclick = (ev) => { ev.stopPropagation(); renameInline(row, s); };
  row.oncontextmenu = (ev) => sessionMenu(ev, s);
  row.ondragstart = (ev) => {
    markSidebarDrag();
    ev.dataTransfer.setData(SESSION_DRAG, s.id);
    ev.dataTransfer.setData("text/plain", s.id);
    ev.dataTransfer.effectAllowed = "move";
    row.classList.add("dragging");
  };
  row.ondragend = () => {
    row.classList.remove("dragging");
    clearTreeDrops();
  };
  return row;
}

function clearTreeDrops() {
  const tree = $("#tree");
  if (!tree) return;
  for (const el of tree.querySelectorAll(".drop, .drop-before, .drop-after, .dragging")) {
    el.classList.remove("drop", "drop-before", "drop-after", "dragging");
  }
}

function placeInList(ids, moved, target, after) {
  const next = ids.filter((id) => id !== moved);
  const at = next.indexOf(target);
  if (at < 0) next.push(moved);
  else next.splice(after ? at + 1 : at, 0, moved);
  return next;
}

function moveSession(moved, target, after) {
  /* Same splice as the tab strip: remove first, then find the target, or a
   * move downwards lands one slot off and feels like the drop ignored you. */
  const src = session(moved);
  const dst = session(target);
  if (src && dst) src.folder = dst.folder || null;
  const ids = placeInList(state.sessions.map((s) => s.id), moved, target, after);
  const rank = Object.fromEntries(ids.map((id, i) => [id, i]));
  state.sessions.sort((a, b) => (rank[a.id] ?? 99) - (rank[b.id] ?? 99));
  renderTree();
  /* Filing into the target's folder is a different fact from order, and the
   * reorder endpoint does not touch folder. Do both; the list already shows
   * the destination. */
  const file = (src && dst)
    ? api("api/sessions/" + moved, {
        method: "PATCH", body: JSON.stringify({ folder: dst.folder || null }),
      })
    : Promise.resolve();
  file.then(() => api("api/reorder", {
    method: "POST", body: JSON.stringify({ sessions: ids }),
  })).catch(() => refresh());
}

function moveFolder(moved, target, after) {
  const ids = placeInList(
    (state.folders || []).map((f) => f.id), moved, target, after);
  const rank = Object.fromEntries(ids.map((id, i) => [id, i]));
  state.folders.sort((a, b) => (rank[a.id] ?? 99) - (rank[b.id] ?? 99));
  for (const folder of state.folders) folder.order = rank[folder.id] ?? folder.order;
  renderTree();
  api("api/reorder", {
    method: "POST", body: JSON.stringify({ folders: ids }),
  }).catch(() => refresh());
}

function wireDrop(el, folderId) {
  el.ondragover = (ev) => {
    if (ev.dataTransfer.types.includes(FOLDER_DRAG) && folderId.startsWith("f-")) {
      ev.preventDefault();
      const box = el.getBoundingClientRect();
      const after = ev.clientY > box.top + box.height / 2;
      el.classList.toggle("drop-before", !after);
      el.classList.toggle("drop-after", after);
      el.classList.remove("drop");
      return;
    }
    if (ev.dataTransfer.types.includes(SESSION_DRAG)
        || ev.dataTransfer.types.includes("text/plain")) {
      ev.preventDefault();
      el.classList.add("drop");
      el.classList.remove("drop-before", "drop-after");
    }
  };
  el.ondragleave = () =>
    el.classList.remove("drop", "drop-before", "drop-after");
  el.ondrop = async (ev) => {
    el.classList.remove("drop", "drop-before", "drop-after");
    if (ev.dataTransfer.types.includes(FOLDER_DRAG) && folderId.startsWith("f-")) {
      const moved = ev.dataTransfer.getData(FOLDER_DRAG);
      if (!moved || moved === folderId) return;
      ev.preventDefault();
      const box = el.getBoundingClientRect();
      const after = ev.clientY > box.top + box.height / 2;
      moveFolder(moved, folderId, after);
      return;
    }
    const id = ev.dataTransfer.getData(SESSION_DRAG)
            || ev.dataTransfer.getData("text/plain");
    if (!id || id.startsWith("f-")) return;
    ev.preventDefault();
    const folder = folderId.startsWith("__") ? null : folderId;
    await api("api/sessions/" + id, {
      method: "PATCH", body: JSON.stringify({ folder }),
    });
    refresh();
  };
}

function toggleFolder(group) {
  // A view-group has no server record to flip, so it was silently doing
  // nothing — Running and Ungrouped could not be collapsed at all.
  if (!group.id.startsWith("f-")) {
    if (viewsCollapsed.has(group.id)) viewsCollapsed.delete(group.id);
    else viewsCollapsed.add(group.id);
    saveWorkspace();
    return renderTree();
  }
  api("api/folders/" + group.id, {
    method: "PATCH", body: JSON.stringify({ collapsed: !group.collapsed }),
  }).then(refresh);
}

function renameInline(row, s) {
  const holder = row.querySelector(".name");
  const input = document.createElement("input");
  input.value = s.name;
  holder.innerHTML = "";
  holder.appendChild(input);
  input.focus();
  input.select();

  /* Taking the input out of the DOM is what lets the sidebar redraw again —
   * renderTree stands down while one is present. Doing it before the refresh
   * rather than leaving it to the rebuild also means the rebuild cannot be the
   * thing that fires `blur`. */
  const close = () => {
    input.onblur = null;
    input.remove();
  };

  const commit = async () => {
    const name = input.value.trim();
    close();
    if (name && name !== s.name) {
      await api("api/sessions/" + s.id, {
        method: "PATCH", body: JSON.stringify({ name }),
      });
    }
    refresh();
  };
  input.onblur = commit;
  input.onkeydown = (ev) => {
    if (ev.key === "Enter") { ev.preventDefault(); commit(); }
    if (ev.key === "Escape") { close(); refresh(); }
    ev.stopPropagation();
  };
  input.onclick = (ev) => ev.stopPropagation();
}

/* --------------------------------------------------------------- context menu */

/* The event the open menu was positioned from, so a second menu — picking a
 * folder to move into, say — opens in the same place rather than jumping to a
 * corner. A submenu that appears somewhere else reads as a different thing
 * having happened. */
let lastMenuEvent = null;

function showMenu(ev, items) {
  ev.preventDefault();
  lastMenuEvent = ev;
  const menu = $("#menu");
  menu.innerHTML = "";
  for (const [label, fn, danger] of items) {
    const button = document.createElement("button");
    button.textContent = label;
    if (danger) button.className = "danger";
    button.onclick = (click) => {
      // Stop the document-level closer: this click's target is this button,
      // and some items (Change color) rebuild the menu, which would make
      // contains() fail and hide the picker the moment it appeared.
      click.stopPropagation();
      menu.hidden = true;
      fn();
    };
    menu.appendChild(button);
  }
  menu.hidden = false;
  menu.style.setProperty("--menu-origin", "top left");
  menu.style.left = Math.min(ev.clientX, innerWidth - 170) + "px";
  menu.style.top = Math.min(ev.clientY, innerHeight - menu.offsetHeight - 8) + "px";
}

/* The destructive item names what is actually there to destroy. Offering
 * "Kill" on a session whose process ended long ago asks someone to confirm
 * stopping something that already stopped — and hides the thing they probably
 * do want, which is the row gone. */
function sessionMenu(ev, s) {
  const folders = (state.folders || []).filter((f) => f.id.startsWith("f-"));
  showMenu(ev, [
    [s.alive ? "Open" : "Start again", () => openSession(s.id)],
    ["Rename", () => renameSession(s)],
    /* Moving a session between folders was drag-and-drop and nothing else.
     *
     * There is no drag on a phone, which made half the sidebar's organisation
     * unreachable on the device most likely to be checking on a session — the
     * same gap the long-press menu exists to close. It is also not discoverable
     * on a desktop: nothing about a row says it can be dragged. */
    ...(folders.length ? [["Move to folder…", () => moveToFolder(s)]] : []),
    ...(s.folder ? [["Take out of its folder", () => setFolder(s, null)]] : []),
    [s.archived ? "Unarchive" : "Archive", () => setArchived(s, !s.archived)],
    [s.alive ? "Kill session" : "Delete session", () => killSession(s), true],
  ]);
}

/* Which one actually needs you — one answer, not twenty indicators.
 *
 * The sidebar is honest and it does not scale: at twenty sessions, "read every
 * ring and decide" is a job, and it is a job you do every few minutes. This
 * ranks the same facts and names one.
 *
 * A sort, not a model. Everything here already exists — the attention tiers,
 * the activity clock, the unread mark, the age of each — and nothing is
 * captured, polled or inferred to produce it. That is what keeps it honest:
 * it cannot claim to know anything the sidebar does not already show you.
 *
 * The order, worst first:
 *
 *   error    — it stopped badly, and stopped is stopped
 *   waiting  — it asked you something and is doing nothing until you answer
 *   unread   — it produced output you have not seen and then went quiet
 *
 * A working session never appears. It does not need you; that is what working
 * means, and putting it in this list would teach you to ignore the list.
 *
 * Ties break on how long it has been like that, because the one that has been
 * blocked for eleven minutes is costing more than the one blocked for ten
 * seconds. */
const NEXT_RANK = { error: 3, waiting: 2, unread: 1 };

function nextUp() {
  const now = Date.now() / 1000;
  const rows = [];
  for (const s of state.sessions) {
    if (!s.alive || s.archived) continue;
    if (s.id === activeId && document.hasFocus()) continue;   // you are on it
    const work = workState(s);
    let kind = "";
    if (work === "error") kind = "error";
    else if (work === "asking" || work === "waiting") kind = "waiting";
    else if (work !== "working" && unread(s)) kind = "unread";
    if (!kind) continue;
    // Since the pane last said anything, which is when it started waiting.
    const since = Math.max(0, Math.floor(now - (s.activity || now)));
    rows.push({ s, kind, since });
  }
  rows.sort((a, b) => NEXT_RANK[b.kind] - NEXT_RANK[a.kind] || b.since - a.since);
  return rows;
}

const NEXT_WORDS = {
  error: "stopped on an error",
  waiting: "waiting for you",
  unread: "has output you have not seen",
};

/* Said as a sentence, because the point is to be read at a glance rather than
 * decoded. "for 11m" only appears once it has been long enough to matter — on
 * something that went quiet four seconds ago it is noise. */
function nextLine(row) {
  const where = row.s.project || row.s.cwd || "";
  // The activity clock is already the epoch this wants, so `ago` says how long
  // it has been quiet without anything having to convert a duration back.
  const held = row.since >= 60 ? ` for ${ago(row.s.activity)}` : "";
  return `${row.s.name} is ${NEXT_WORDS[row.kind]}${held}` +
         (where ? ` — ${where}` : "");
}

/* Long press, because touch has no right-click.
 *
 * Everything in the session menu — rename, archive, move, kill — was reachable
 * only by right-clicking, which does not exist on a phone. Folders got away
 * with it because the pencil is the same menu with a way to find it, and tabs
 * because of the gear; sidebar rows had nothing, so half the app was missing
 * on the device most likely to be checking on a session from the sofa.
 *
 * Folder heads and history rows now get the same press. Delegated to the
 * tree rather than bound per row: the sidebar is rebuilt when its contents
 * change, and three listeners per session every three seconds is churn for
 * nothing.
 */
const LONG_PRESS_MS = 500;
const LONG_PRESS_SLOP = 10;   // px of drift still counted as holding still

function wireTouchMenus() {
  const tree = $("#tree");
  let timer = null;
  let from = null;
  let fired = false;

  const cancel = () => { clearTimeout(timer); timer = null; from = null; };

  const pressTarget = (node) => {
    const head = node.closest(".folder-head");
    if (head && head.querySelector(".folder-edit")) return head;
    return node.closest(".session");
  };

  tree.addEventListener("touchstart", (ev) => {
    cancel();
    if (ev.touches.length !== 1) return;        // a pinch is not a press
    const row = pressTarget(ev.target);
    if (!row) return;
    const touch = ev.touches[0];
    from = { x: touch.clientX, y: touch.clientY, el: row };
    fired = false;
    timer = setTimeout(() => {
      timer = null;
      const el = from && from.el;
      if (!el) return;
      fired = true;
      // The one moment a buzz is right: nothing has moved on screen yet, and
      // without it a press that has landed feels identical to one that has not.
      if (navigator.vibrate) navigator.vibrate(12);
      el.dispatchEvent(new MouseEvent("contextmenu", {
        bubbles: true, cancelable: true,
        clientX: from.x, clientY: from.y,
      }));
    }, LONG_PRESS_MS);
  }, { passive: true });

  tree.addEventListener("touchmove", (ev) => {
    // A press that turns into a scroll is a scroll. The slop is generous: a
    // finger resting on a list is never perfectly still, and a menu that
    // refuses to open is worse than one that opens when you meant to scroll.
    if (!from || !ev.touches.length) return;
    const touch = ev.touches[0];
    if (Math.abs(touch.clientX - from.x) > LONG_PRESS_SLOP
        || Math.abs(touch.clientY - from.y) > LONG_PRESS_SLOP) cancel();
  }, { passive: true });

  // Not passive: lifting after the menu opened would otherwise become a tap on
  // the row underneath, and the session would open behind its own menu.
  tree.addEventListener("touchend", (ev) => {
    if (fired) { ev.preventDefault(); fired = false; }
    cancel();
  }, { passive: false });

  tree.addEventListener("touchcancel", cancel, { passive: true });
}

const PALETTE = [
  "#c7915b", "#6f42c1", "#2d7d46", "#1f6feb", "#0d7d8f", "#a63d2f",
  "#8b8b8b", "#d96f6f", "#e8a33d", "#3aa3a0", "#7a7fd6", "#ff5fa2",
  "#c4500a", "#8250df", "#1a7f37", "#0550ae", "#bf3989", "#9a6700",
  "#cf222e", "#0969da", "#bc4c00", "#5a32a3", "#087f5b", "#364fc7",
];

function colorPicker(ev, folder) {
  const menu = $("#menu");
  menu.innerHTML = "";
  const grid = document.createElement("div");
  grid.className = "swatches";
  for (const color of PALETTE) {
    const swatch = document.createElement("button");
    swatch.className = "swatch" + (color === folder.color ? " on" : "");
    swatch.style.background = color;
    swatch.title = color;
    swatch.onclick = async () => {
      menu.hidden = true;
      await api("api/folders/" + folder.id, {
        method: "PATCH", body: JSON.stringify({ color }),
      });
      refresh();
    };
    grid.appendChild(swatch);
  }
  menu.appendChild(grid);
  menu.hidden = false;
  menu.style.setProperty("--menu-origin", "top left");
  menu.style.left = Math.min(ev.clientX, innerWidth - menu.offsetWidth - 8) + "px";
  menu.style.top = Math.min(ev.clientY, innerHeight - menu.offsetHeight - 8) + "px";
}

function folderMenu(ev, folder) {
  ev.stopPropagation();
  showMenu(ev, [
    ["Change color", () => colorPicker(ev, folder)],
    ["Rename folder", async () => {
      const name = prompt("Folder name", folder.name);
      if (name) {
        await api("api/folders/" + folder.id, {
          method: "PATCH", body: JSON.stringify({ name }),
        });
        refresh();
      }
    }],
    ["Delete folder", async () => {
      // Sessions survive: the server unfiles them rather than deleting.
      if (!confirm(`Delete folder "${folder.name}"? Its sessions move to Ungrouped.`)) return;
      await api("api/folders/" + folder.id, { method: "DELETE" });
      refresh();
    }, true],
  ]);
}

async function renameSession(s) {
  const row = document.querySelector(`.session[data-id="${s.id}"]`);
  if (row) return renameInline(row, s);
  // Reached from the palette, where the row may be scrolled out of the tree,
  // filtered out by the search box, or inside a collapsed folder.
  const name = prompt("Session name", s.name);
  if (!name || name.trim() === s.name) return;
  await api("api/sessions/" + s.id, {
    method: "PATCH", body: JSON.stringify({ name: name.trim() }),
  });
  refresh();
}

async function setArchived(s, archived) {
  // Deliberately not a confirm: nothing is destroyed, and the session keeps
  // running in tmux either way.
  await api("api/sessions/" + s.id, {
    method: "PATCH", body: JSON.stringify({ archived }),
  });
  if (archived) closeTab(s.id, true);
  refresh();
}

async function newFolder() {
  const name = prompt("Folder name");
  if (!name) return;
  await api("api/folders", { method: "POST", body: JSON.stringify({ name }) });
  refresh();
}

async function adoptSessions() {
  /* Adopt is safe to run more than once, and worth running again: as well as
   * taking over anything new, it repairs sessions adopted before CLIque could
   * detect which CLI they were running. So it is offered even when there is
   * nothing new to take over. */
  const found = await api("api/adoptable");
  const question = found.length
    ? `Adopt ${found.length} session(s) started by another tool?`
    : "Nothing new to adopt. Re-check the ones already adopted for their CLI, name and folder?";
  if (!confirm(question)) return;
  const result = await api("api/sessions/adopt", { method: "POST", body: "{}" });
  await refresh();
  const lines = [];
  if (result.adopted && result.adopted.length) lines.push("Adopted: " + result.adopted.join(", "));
  if (result.updated && result.updated.length) lines.push("Updated: " + result.updated.join(", "));
  alert(lines.join("\n") || "Nothing changed.");
}

async function killSession(s) {
  if (s.alive) {
    // A bland "Stop?" gets a reflexive OK. When the session is mid-task or
    // holds an unsent draft, the confirm should say what is at stake — that is
    // the moment stopping actually costs something. The record and its draft
    // survive a stop either way; what a working session loses is the work in
    // flight, so name that rather than ask a blank question.
    const stakes = [];
    if (workState(s) === "working") stakes.push("still working");
    if ((s.draft || "").trim()) stakes.push("has an unsent draft");
    const question = stakes.length
      ? `"${s.name}" is ${stakes.join(" and ")} — stop it anyway? `
        + `It stays in the folder, draft and all, and you can start it again.`
      : `Stop "${s.name}"? It stays in the folder. You can start it again.`;
    if (!confirm(question)) return;
    closeTab(s.id, true);
    await api("api/sessions/" + s.id + "/kill", { method: "POST", body: "{}" });
    refresh();
    return;
  }
  if (!confirm(`Remove "${s.name}" from the sidebar?`)) return;
  closeTab(s.id, true);
  await api("api/sessions/" + s.id, { method: "DELETE" });
  refresh();
}

/* ---------------------------------------------------------------------- tabs */

function tabsFingerprint() {
  return openTabs.map((id) => {
    const s = session(id);
    if (!s) return id;
    return [
      id, s.name, s.alive ? 1 : 0, workState(s),
      unread(s) ? 1 : 0, attention.has(id) ? 1 : 0,
      s.cwd || "", s.signal || "", s.cli || "",
    ].join("\x1f");
  }).join("\x1e") + "\x1d" + (activeId || "");
}

function renderTabs() {
  const bar = $("#tabs");
  const fp = tabsFingerprint();
  if (fp === tabsFp && bar.childElementCount) {
    applyCliTint();
    packTabs();
    renderInputBar();
    return;
  }
  tabsFp = fp;
  bar.innerHTML = "";
  openTabs.forEach((id, index) => {
    const s = session(id);
    if (!s) return;
    const tab = document.createElement("div");
    tab.dataset.id = id;
    tab.className = "tab" + (id === activeId ? " active" : "") +
      (s.busy ? " busy" : "") + (unread(s) ? " unread" : "") +
      (workState(s) === "asking" ? " asking" : "") +
      (attention.has(id) ? " attention" : "");
    /* A mark nobody can name is a mark nobody trusts.
     *
     * The tab carries up to three of them — a status ring, an attention glow,
     * and the unread dot — and the tooltip said only the working directory, so
     * the honest reaction to any of them is "what is that and should I worry".
     * Saying it in words costs nothing and is the difference between a signal
     * and a decoration. */
    const says = [];
    if (!s.alive) says.push("stopped");
    else if (s.signal === "error") says.push("stopped on an error");
    else if (s.signal === "waiting") says.push("needs an answer");
    else if (workState(s) === "working") says.push("working");
    else if (workState(s) === "unseen") says.push("finished — not opened yet");
    if (unread(s)) says.push("new output since you last looked");
    tab.title = says.length ? `${s.cwd}\n${says.join(" · ")}` : s.cwd;
    tab.innerHTML =
      `<span class="num">${index + 1}</span>` +
      statusDot(s, "tabs") +
      sessionMarker(s, "tabs") +
      `<span class="label">${escapeHtml(s.name)}</span>` +
      `<button class="gear" title="Session settings">${icon("settings")}</button>` +
      `<button class="x" title="Close tab (session keeps running)">${icon("x")}</button>`;
    tab.onclick = () => openSession(id);
    tab.querySelector(".x").onclick = (ev) => { ev.stopPropagation(); closeTab(id); };
    tab.querySelector(".gear").onclick = (ev) => { ev.stopPropagation(); sessionMenu(ev, s); };

    /* Drag to reorder. The order is the browser's, not the server's: which
     * tab sits where is about this screen, the same rule that keeps sidebar
     * width local. It rides along with the open-tab list that already
     * persists, so a reload keeps the arrangement. */
    tab.draggable = true;
    tab.ondragstart = (ev) => {
      ev.dataTransfer.setData("text/clique-tab", id);
      ev.dataTransfer.effectAllowed = "move";
      tab.classList.add("dragging");
    };
    tab.ondragend = () => {
      tab.classList.remove("dragging");
      for (const el of bar.children) el.classList.remove("drop-before", "drop-after");
    };
    tab.ondragover = (ev) => {
      if (!ev.dataTransfer.types.includes("text/clique-tab")) return;
      ev.preventDefault();
      // Which half of the tab the pointer is over decides which side it lands.
      const box = tab.getBoundingClientRect();
      const after = ev.clientX > box.left + box.width / 2;
      tab.classList.toggle("drop-before", !after);
      tab.classList.toggle("drop-after", after);
    };
    tab.ondragleave = () => tab.classList.remove("drop-before", "drop-after");
    tab.ondrop = (ev) => {
      const moved = ev.dataTransfer.getData("text/clique-tab");
      if (!moved || moved === id) return;
      ev.preventDefault();
      const box = tab.getBoundingClientRect();
      const after = ev.clientX > box.left + box.width / 2;
      moveTab(moved, id, after);
    };

    bar.appendChild(tab);
  });
  applyCliTint();
  packTabs();
  renderInputBar();
}

/* Keep every tab on screen, or behind a control that is itself on screen.
 *
 * The standing rule: overflow wraps or is visible, never behind a scrollbar
 * that is itself hidden. A strip of twenty sessions that ran off the right
 * edge — with no way to see that one of them was waiting — is that failure.
 *
 * Names shrink first (flex). What still will not fit is hidden from the
 * right, except the tab you are looking at, which always stays. Those land
 * in #tabOverflow, which wears the same working/waiting/error ring so a
 * session that needs you is not gone, just one click further. */
function packTabs() {
  const bar = $("#tabs");
  const btn = $("#tabOverflow");
  if (!bar || !btn) return;
  const tabs = [...bar.querySelectorAll(".tab")];
  tabs.forEach((tab) => { tab.hidden = false; });
  btn.hidden = true;
  if (!tabs.length || bar.clientWidth < 8) {
    paintOverflowButton([]);
    return;
  }
  if (bar.scrollWidth <= bar.clientWidth + 1) {
    paintOverflowButton([]);
    return;
  }

  btn.hidden = false;
  const activeIdx = tabs.findIndex((tab) => tab.dataset.id === activeId);
  while (bar.scrollWidth > bar.clientWidth + 1) {
    let hide = -1;
    for (let i = tabs.length - 1; i >= 0; i--) {
      if (i === activeIdx || tabs[i].hidden) continue;
      hide = i;
      break;
    }
    if (hide < 0) break;
    tabs[hide].hidden = true;
  }
  paintOverflowButton(tabs.filter((tab) => tab.hidden).map((tab) => tab.dataset.id));
}

function paintOverflowButton(ids) {
  const btn = $("#tabOverflow");
  if (!btn) return;
  const rows = ids.map((id) => session(id)).filter(Boolean);
  btn.hidden = !rows.length;
  if (!rows.length) {
    btn.dataset.work = "idle";
    btn.classList.remove("attention", "unread");
    return;
  }
  const count = rows.length;
  btn.title = count === 1 ? "1 more tab" : `${count} more tabs`;
  btn.setAttribute("aria-label", btn.title);
  const n = btn.querySelector(".n");
  if (n) n.textContent = String(count);

  const states = rows.map((s) => workState(s));
  let work = "idle";
  if (states.includes("error")) work = "error";
  else if (states.includes("asking")) work = "asking";
  else if (states.includes("unseen") || rows.some((s) => attention.has(s.id))) work = "unseen";
  else if (states.includes("working")) work = "working";
  btn.dataset.work = work;
  const ring = btn.querySelector(".cli-status");
  if (ring) ring.setAttribute("data-work", work);
  btn.classList.toggle("attention", rows.some((s) => attention.has(s.id)));
  btn.classList.toggle("unread", rows.some((s) => unread(s)));
}

function overflowRank(s) {
  const work = workState(s);
  if (work === "error") return 0;
  if (work === "asking") return 1;
  if (work === "unseen" || attention.has(s.id)) return 2;
  if (work === "working") return 3;
  if (unread(s)) return 4;
  return 5;
}

function openOverflowMenu(ev) {
  const btn = $("#tabOverflow");
  const menu = $("#menu");
  const hidden = [...$("#tabs").querySelectorAll(".tab")]
    .filter((tab) => tab.hidden)
    .map((tab) => session(tab.dataset.id))
    .filter(Boolean)
    .sort((a, b) => overflowRank(a) - overflowRank(b) ||
                    openTabs.indexOf(a.id) - openTabs.indexOf(b.id));
  menu.innerHTML = "";
  menu.dataset.kind = "tab-overflow";
  if (!hidden.length) {
    menu.hidden = true;
    return;
  }
  for (const s of hidden) {
    const row = document.createElement("button");
    row.type = "button";
    row.className = "tab-more-item" +
      (unread(s) ? " unread" : "") +
      (workState(s) === "asking" ? " asking" : "") +
      (attention.has(s.id) ? " attention" : "");
    const mark = document.createElement("span");
    mark.className = "empty-mark";
    mark.innerHTML = (statusDot(s, "tabs") || "") + sessionMarker(s, "tabs");
    const name = document.createElement("span");
    name.className = "name";
    name.textContent = s.name;
    const why = document.createElement("span");
    why.className = "why";
    const work = workState(s);
    why.textContent = work === "idle" && unread(s)
      ? "new output"
      : (WORK_WORDS[work] || "");
    row.append(mark, name, why);
    row.onclick = () => {
      menu.hidden = true;
      openSession(s.id);
    };
    menu.append(row);
  }
  menu.hidden = false;
  const box = (ev.currentTarget || btn).getBoundingClientRect();
  const width = Math.max(menu.offsetWidth, 220);
  menu.style.minWidth = "220px";
  menu.style.setProperty("--menu-origin", "top right");
  menu.style.left = Math.min(box.left, innerWidth - width - 8) + "px";
  menu.style.top = Math.min(box.bottom + 4, innerHeight - menu.offsetHeight - 8) + "px";
}

/* Put `moved` next to `target`. Removing first and finding the index second
 * matters: computing the destination before the removal is off by one whenever
 * a tab moves rightwards, which is the bug that makes drag-reorder feel like
 * it ignores you every other drag. */
function moveTab(moved, target, after) {
  openTabs = openTabs.filter((id) => id !== moved);
  const at = openTabs.indexOf(target);
  if (at < 0) openTabs.push(moved);
  else openTabs.splice(after ? at + 1 : at, 0, moved);
  saveWorkspace();
  renderTabs();
}

/* Whether the panel should draw a prompt box for the session in front.
 *
 * "auto" asks the CLI, which is the only one that knows: Claude, Codex and
 * the rest draw a box of their own, and ours underneath it is the redundant
 * half of two stacked prompts. A shell draws no box, and there ours is not a
 * duplicate — it is the only place Run, the repeat counter and a saved draft
 * exist at all.
 *
 * With no session open the box stays, because the empty pane is one of the
 * places you start work from. */
function promptWanted() {
  const mode = state.settings.input_mode || "auto";
  if (mode === "panel") return true;
  if (mode === "terminal") return false;
  const s = session(activeId);
  return !(s && s.own_input);
}

function renderInputBar() {
  const s = session(activeId);
  const pill = $("#modePill");
  // The pill exists only for CLIs that declare modes. That falls out of the
  // registry config — there is no per-CLI branch here.
  if (s && s.modes && s.modes.length) {
    pill.hidden = false;
    const now = s.mode || s.modes[0];
    // The label is the CLI's own, from the registry. It was hardcoded to
    // Claude Code's wording, which read as a lie on any other CLI.
    pill.textContent = (s.mode_label || "{mode} mode").replace("{mode}", now);
  } else {
    pill.hidden = true;
  }

  /* The prompt box goes; the pill stays.
   *
   * Hiding the whole bar was the old behaviour and it was wrong: the pill is
   * the control for a CLI's permission mode, so switching off a duplicate
   * text box also took away the way to see and change what Claude was allowed
   * to do. They are hidden separately now, and the bar itself only goes when
   * there is nothing left in it. */
  const wants = promptWanted();
  for (const sel of ["#prompt", ".stepper", ".runsplit"]) {
    const el = $(sel);
    if (el) el.hidden = !wants;
  }
  $("#inputbar").hidden = !wants && pill.hidden;
  $("#inputbar").classList.toggle("pill-only", !wants && !pill.hidden);

  $("#empty").style.display = activeId ? "none" : "grid";
  if (!activeId) renderEmpty();
  renderCopyChip();
}

/* Advance a session to its next mode and remember it.
 *
 * Nothing here knows what a mode means. The registry says which modes exist
 * and in what order; this walks that list and stores where it got to, so the
 * pill says what the CLI is actually on rather than what it started on.
 *
 * `sent` is whether the keystroke has already reached the pane — true when the
 * user pressed it themselves, false when they clicked the pill and we still
 * have to send it. */
function cycleMode(s, sent) {
  if (!s || !s.modes || !s.modes.length) return;
  const at = s.modes.indexOf(s.mode || s.modes[0]);
  const next = s.modes[(at + 1) % s.modes.length];
  if (!sent && s.mode_key) control({ type: "key", key: s.mode_key });
  // Stamped locally first so the pill turns over on the keystroke rather than
  // on the next poll, then persisted so a reload does not forget it.
  s.mode = next;
  renderInputBar();
  api("api/sessions/" + s.id, {
    method: "PATCH", body: JSON.stringify({ mode: next }),
  }).catch(() => {});
}

/* ------------------------------------------------------------ image paste */

/* Sharing a screenshot with an agent.
 *
 * A terminal cannot carry an image — the only thing that can cross into a pane
 * is text. So the bytes go to the server, land in the session's own working
 * directory, and what actually reaches the CLI is a path it can open. Every
 * coding CLI already knows how to read a file, so this needs to know nothing
 * about any of them.
 *
 * Text paste is deliberately untouched: this only steps in when the clipboard
 * actually holds an image, and passes everything else through to xterm.
 */
async function pasteImages(items) {
  const s = session(activeId);
  if (!s) return false;

  const files = [...items]
    .filter((i) => i.kind === "file" && i.type.startsWith("image/"))
    .map((i) => i.getAsFile())
    .filter(Boolean);
  if (!files.length) return false;

  for (const file of files) {
    try {
      const buf = new Uint8Array(await file.arrayBuffer());
      // Chunked so a large screenshot cannot blow the argument limit on
      // String.fromCharCode, which is what a naive spread does at ~100k.
      let binary = "";
      for (let i = 0; i < buf.length; i += 0x8000) {
        binary += String.fromCharCode.apply(null, buf.subarray(i, i + 0x8000));
      }
      const saved = await api(`api/sessions/${s.id}/paste`, {
        method: "POST", body: JSON.stringify({ data: btoa(binary) }),
      });
      const where = deliverPath(saved.path);
      toast(`Saved ${saved.relative} — the path is in ${where}`);
    } catch (err) {
      toast("Could not save that image: " + err.message, true);
    }
  }
  return true;
}

/* The pane with nothing in it.
 *
 * It used to say "No session open", which is the one thing the empty pane
 * already demonstrates. This is the moment someone has just arrived or just
 * finished, so it is worth something: what is happening on the box right now,
 * and the two shortest routes back into work.
 *
 * All of it is state the panel already polls. No new endpoint, no service to
 * reach, nothing to configure — an empty pane is not the place to start
 * asking a self-hosted tool to phone somewhere for a weather icon. */
const EMPTY_SESSIONS = 6;
const EMPTY_RESUMABLE = 5;

/* One line of advice, stable for the day.
 *
 * Keyed to the date rather than picked at random: a tip that changes every
 * time the pane repaints is a slot machine, and nobody finishes reading one.
 * Same tip all day means you either read it or you do not, which is the
 * correct amount of insistence for a thing nobody asked for. */
const TIPS = [
  "Closing a tab does not kill the session — tmux and the CLI carry on without you.",
  "Ctrl/Cmd + K jumps between sessions. Type > for commands, @ for sessions, ~ for past conversations.",
  "Paste a screenshot with Ctrl/Cmd + V — it lands in the session's own folder and the path goes where you were typing.",
  "Scroll up and the view detaches from the stream. The badge says how far behind you are.",
  "Alt + 1 to 9 switches tabs. The pane owns every other key, on purpose.",
  "A ring turning means working. A steady pulse means it is waiting for you.",
  "Adding a CLI is four lines in clis.toml and a reload. No restart, no code.",
  "Drag folders and sessions in the sidebar to rearrange them — the same gesture as the tab strip.",
  "Snippets are for deliberate reuse. Set them up in Settings → Snippets.",
  "Set a webhook in Settings → Notifications and your phone finds out when a session needs you.",
  "Ctrl/Cmd + B collapses the sidebar to a rail, markers and all.",
  "An image an agent writes into the session's directory shows up in the tab bar.",
];

function renderTip() {
  const now = new Date();
  const day = Math.floor((now - new Date(now.getFullYear(), 0, 0)) / 86400000);
  $("#emptyTip").textContent = TIPS[day % TIPS.length];
}

/* The clock, in whichever zone was asked for.
 *
 * Intl does the whole job, so this needs no data and no network — the zone
 * database is already in the browser. An unset zone means the browser's own,
 * and a zone the browser rejects falls back to that rather than throwing and
 * taking the pane down with it. */
function renderClock() {
  const zone = state.settings.clock_zone || undefined;
  const h24 = state.settings.clock_24h !== false;
  // hourCycle rather than hour12 alone: "h23" is what stops 24-hour clocks
  // rendering midnight as 24:00 in some locales.
  const opts = h24
    ? { hour: "2-digit", minute: "2-digit", hourCycle: "h23" }
    : { hour: "numeric", minute: "2-digit", hour12: true };
  const dateOpts = { weekday: "long", day: "numeric", month: "long" };
  let time, date;
  try {
    time = new Intl.DateTimeFormat([], { ...opts, timeZone: zone }).format();
    date = new Intl.DateTimeFormat([], { ...dateOpts, timeZone: zone }).format();
  } catch {
    time = new Intl.DateTimeFormat([], opts).format();
    date = new Intl.DateTimeFormat([], dateOpts).format();
  }
  $("#emptyClock").innerHTML = "";
  const big = document.createElement("div");
  big.className = "clock-time";
  big.textContent = time;
  const small = document.createElement("div");
  small.className = "clock-date";
  small.textContent = date + (state.settings.clock_zone ? " · " + state.settings.clock_zone : "");
  $("#emptyClock").append(big, small);
}

function renderEmpty() {
  if (activeId) return;                 // nothing to draw behind a live pane
  renderClock();
  renderTip();
  const sessions = state.sessions || [];
  const alive = sessions.filter((x) => x.alive);
  const wants = alive.filter((x) => {
    const w = workState(x);
    return w === "asking" || w === "waiting" || w === "error";
  });
  const working = alive.filter((x) => workState(x) === "working");

  const bits = [];
  if (!sessions.length) bits.push("Nothing running yet");
  else bits.push(`${alive.length} running`);
  if (working.length) bits.push(`${working.length} working`);
  if (wants.length) bits.push(`${wants.length} waiting for you`);
  $("#emptyNow").textContent = bits.join(" · ");
  $("#emptyNow").classList.toggle("wants", wants.length > 0);

  /* What needs you, above what you were doing.
   *
   * This is the screen you land on after being away, so it is the one place
   * the ranking is worth spending room on rather than a single line: coming
   * back to three blocked agents and being told about one of them is a worse
   * answer than being told about all three. */
  fillEmptyList($("#emptyNeeds"), nextUp().slice(0, EMPTY_SESSIONS).map((row) => ({
    marker: sessionMarker(row.s, "sidebar") + statusDot(row.s, "sidebar"),
    title: row.s.name || row.s.cli_label || row.s.cli,
    detail: NEXT_WORDS[row.kind] + (row.since >= 60 ? ` · ${ago(row.s.activity)}` : ""),
    dead: false,
    open: () => openSession(row.s.id),
  })));

  // Most recently looked at first, and the ones that are still alive before
  // the ones that are not — "where was I" almost always means a live session.
  const recent = [...sessions]
    .sort((a, b) => (b.alive - a.alive) || ((b.last_seen || 0) - (a.last_seen || 0)))
    .filter((x) => !x.archived)
    .slice(0, EMPTY_SESSIONS);
  fillEmptyList($("#emptyBack"), recent.map((x) => ({
    marker: sessionMarker(x, "sidebar") + statusDot(x, "sidebar"),
    title: x.name || x.cli_label || x.cli,
    detail: [(state.folders || []).find((f) => f.id === x.folder)?.name
             || shortPath(x.cwd, 34),
             x.last_seen ? ago(x.last_seen) : ""].filter(Boolean).join(" · "),
    dead: !x.alive,
    open: () => openSession(x.id),
  })));

  const conversations = (resumable || []).slice(0, EMPTY_RESUMABLE);
  fillEmptyList($("#emptyResume"), conversations.map((c) => ({
    marker: "",
    title: c.label || "(untitled)",
    detail: (c.project || "") + (c.updated ? " · " + ago(c.updated) : ""),
    dead: false,
    open: () => resumeConversation(c),
  })));
}

function fillEmptyList(block, rows) {
  block.hidden = !rows.length;
  const list = block.querySelector(".empty-list");
  list.textContent = "";
  for (const row of rows) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "empty-row" + (row.dead ? " dead" : "");
    const mark = document.createElement("span");
    mark.className = "empty-mark";
    mark.innerHTML = row.marker;
    const title = document.createElement("span");
    title.className = "empty-title";
    title.textContent = row.title;
    const detail = document.createElement("span");
    detail.className = "empty-detail";
    detail.textContent = row.detail;
    button.append(mark, title, detail);
    button.onclick = row.open;
    list.append(button);
  }
}

/* Clickable URLs and paths in the pane.
 *
 * Written here rather than vendoring xterm's web-links addon: the API this
 * needs is already exposed, and one more vendored file is one more version to
 * keep in step with the core for a feature this size.
 *
 * Only http and https for URLs. A terminal prints whatever a program sends
 * it, so the text on screen is not trustworthy input — `javascript:` and
 * `file:` are the two that would matter and neither is matched or opened.
 *
 * A path opens a read-only sheet, not an editor. Ctrl/Cmd+click skips the
 * sheet and drops the path where you are already typing. */
const LINK_RE = /\bhttps?:\/\/[^\s"'`<>]+/g;

/* A URL that wraps is still one URL. xterm only asks about one row at a
 * time, so without this a login link split at column 80 is two dead halves. */
function paneLineWrapped(term, row0) {
  try {
    const line = term.buffer.active.getLine(row0);
    if (line && typeof line.isWrapped === "boolean") return line.isWrapped;
  } catch (err) { /* older buffer */ }
  return false;
}

function paneWrapParts(term, lineNumber) {
  const buf = term.buffer.active;
  let start = lineNumber - 1;
  while (start > 0 && paneLineWrapped(term, start)) start--;
  const parts = [];
  let y = start;
  while (y < buf.length) {
    const line = buf.getLine(y);
    if (!line) break;
    if (y > start && !paneLineWrapped(term, y)) break;
    parts.push({ y: y + 1, text: line.translateToString(true) });
    y++;
  }
  if (!parts.length) {
    const line = buf.getLine(lineNumber - 1);
    parts.push({ y: lineNumber, text: line ? line.translateToString(true) : "" });
  }
  return parts;
}

function paneRowsText(parts) {
  return parts.map((p) => p.text).join("");
}

function paneUrlSegments(parts, start, length) {
  const end = start + length;
  let offset = 0;
  const segs = [];
  for (const part of parts) {
    const a = Math.max(start, offset);
    const b = Math.min(end, offset + part.text.length);
    if (b > a) {
      segs.push({ y: part.y, x0: a - offset + 1, x1: b - offset });
    }
    offset += part.text.length;
  }
  return segs;
}

function urlNeedsLocalCallback(url) {
  try {
    const redir = new URL(url).searchParams.get("redirect_uri") || "";
    return /localhost|127\.0\.0\.1/i.test(redir);
  } catch (err) {
    return /redirect_uri=http%3A%2F%2Flocalhost/i.test(url)
      || /redirect_uri=http:\/\/localhost/i.test(url);
  }
}

function paneHostIsRemote(hostname) {
  const h = String(hostname || (typeof location !== "undefined" && location.hostname) || "")
    .toLowerCase();
  return Boolean(h) && h !== "localhost" && h !== "127.0.0.1" && h !== "[::1]";
}

function tidyCopiedLink(text) {
  if (!text || text.indexOf("\n") < 0) return text;
  const joined = text.replace(/\s+/g, "");
  if (/^https?:\/\//i.test(joined)) return joined;
  return text;
}

/* Paths a CLI prints: absolute, ~/ , ./ ../, or a relative path with a slash
 * and a file extension. Bare words and host/path URLs without a scheme are
 * not matches — those are how you click `example.com/foo` by accident. */
const PATH_RE = /(?:^|[\s"'`=(])((?:~\/|\.{1,2}\/|\/)[^\s"'`<>]+|[A-Za-z0-9._-]+(?:\/[A-Za-z0-9._-]+)+\.[A-Za-z0-9]{1,12})/g;

/* Trailing punctuation almost always belongs to the sentence, not the URL:
 * "see https://example.com/docs." and "(https://example.com)". Brackets are
 * only trimmed when unbalanced, so a Wikipedia URL ending in ")" survives.
 *
 * Checked against, in order: a URL ending a sentence, one wrapped in
 * parentheses, /wiki/Foo_(bar), a query string with &, one followed by a
 * comma, and javascript: and file:, which must not match at all. */
function trimUrl(text) {
  let out = text;
  while (out.length > 1) {
    const last = out[out.length - 1];
    if (".,;:!?'\"".includes(last)) { out = out.slice(0, -1); continue; }
    if (last === ")" && (out.match(/\(/g) || []).length < (out.match(/\)/g) || []).length) {
      out = out.slice(0, -1); continue;
    }
    if (last === "]" && (out.match(/\[/g) || []).length < (out.match(/]/g) || []).length) {
      out = out.slice(0, -1); continue;
    }
    break;
  }
  return out;
}

function trimPath(text) {
  let out = text;
  const linecol = /:\d+(?::\d+)?$/;
  if (linecol.test(out)) out = out.replace(linecol, "");
  return trimUrl(out);
}

function pathRange(line, full, captured) {
  const start = line.indexOf(captured, Math.max(0, full.index));
  const at = start >= 0 ? start : full.index + (full[0].length - captured.length);
  return { start: at + 1, end: at + captured.length };
}

function openLink(url, newWindow) {
  // Re-checked at the point of opening, not only at the point of matching.
  if (!/^https?:\/\//i.test(url)) return;
  // Naming a size is what makes a browser give you a window rather than a
  // tab; without it "_blank" is a tab either way.
  const how = newWindow
    ? "noopener,noreferrer,popup=yes,width=1200,height=860"
    : "noopener,noreferrer";
  window.open(url, "_blank", how);
}

function sendPaneKey(sessionId, key) {
  const entry = terms.get(sessionId || activeId);
  if (!entry || !entry.ws || entry.ws.readyState !== 1) return false;
  entry.ws.send(JSON.stringify({ type: "key", key: key }));
  return true;
}

function handlePaneUrl(url, event, sessionId) {
  /* A CLI on this box that asks the *browser* to come back to localhost is
   * asking the laptop. That callback never arrives. Device-code is the
   * flow these tools already ship for a remote pane — Esc, then 2, is
   * Codex's menu; the same Esc cancels a stuck browser wait for others. */
  if (paneHostIsRemote() && urlNeedsLocalCallback(url)) {
    sendPaneKey(sessionId, "Escape");
    setTimeout(() => sendPaneKey(sessionId, "2"), 500);
    toast("That login cannot come back to this box. A device code should appear — open that short link.");
    return;
  }
  openLink(url, event && (event.ctrlKey || event.metaKey));
}

function wireLinks(term, sessionId) {
  if (!term.registerLinkProvider) return;   // older core: links simply stay text
  term.registerLinkProvider({
    provideLinks(lineNumber, callback) {
      const line = term.buffer.active.getLine(lineNumber - 1);
      if (!line) return callback(undefined);
      const parts = paneWrapParts(term, lineNumber);
      const text = paneRowsText(parts);
      const links = [];
      LINK_RE.lastIndex = 0;
      let match;
      while ((match = LINK_RE.exec(text)) !== null) {
        const url = trimUrl(match[0]);
        if (!url) continue;
        const segs = paneUrlSegments(parts, match.index, url.length);
        for (const seg of segs) {
          if (seg.y !== lineNumber) continue;
          links.push({
            // xterm columns are 1-based and the end is inclusive.
            range: { start: { x: seg.x0, y: seg.y },
                     end: { x: seg.x1, y: seg.y } },
            text: url,
            decorations: { underline: true, pointerCursor: true },
            activate(event, uri) {
              handlePaneUrl(uri, event, sessionId);
            },
          });
        }
      }
      const own = line.translateToString(true);
      PATH_RE.lastIndex = 0;
      while ((match = PATH_RE.exec(own)) !== null) {
        const raw = trimPath(match[1]);
        if (!raw || raw.startsWith("//") || raw.includes("://")) continue;
        const at = pathRange(own, match, raw);
        // A URL's path is not a file path. The character before an http(s)
        // match is `:`; skip anything sitting on that.
        if (at.start > 1 && own[at.start - 2] === ":") continue;
        links.push({
          range: { start: { x: at.start, y: lineNumber },
                   end: { x: at.end, y: lineNumber } },
          text: raw,
          decorations: { underline: true, pointerCursor: true },
          activate(event, path) {
            if (event.ctrlKey || event.metaKey) {
              toast("Path is in " + deliverPath(path));
              return;
            }
            openFileSheet(sessionId, path);
          },
        });
      }
      callback(links.length ? links : undefined);
    },
  });
}

/* A read-only glance at a path the pane printed. Copy it, drop it in the
 * prompt, or look at the text / image. Not an editor — that is a tool the
 * person already has, and opening it is what Send path is for. */
let fileAsked = "";
let fileSession = "";

function filePathNow() {
  return ($("#filePath").textContent || fileAsked || "").trim();
}

async function openFileSheet(sessionId, path) {
  const asked = String(path || "").trim();
  if (!sessionId || !asked) return;
  fileSession = sessionId;
  fileAsked = asked;
  $("#fileTitle").textContent = asked.split("/").pop() || asked;
  $("#filePath").textContent = asked;
  $("#fileText").hidden = true;
  $("#fileText").textContent = "";
  $("#fileImg").hidden = true;
  $("#fileImg").removeAttribute("src");
  $("#fileNote").hidden = false;
  $("#fileNote").textContent = "Looking…";
  $("#file").hidden = false;
  try {
    const info = await api(
      "api/sessions/" + encodeURIComponent(sessionId)
      + "/file?path=" + encodeURIComponent(asked));
    if (fileAsked !== asked || fileSession !== sessionId) return;
    showFile(info);
  } catch (err) {
    if (fileAsked !== asked) return;
    $("#fileNote").hidden = false;
    $("#fileNote").textContent = "Could not read it.";
  }
}

function showFile(info) {
  $("#fileTitle").textContent = info.name || info.asked || "File";
  $("#filePath").textContent = info.path || info.asked || fileAsked;
  const note = $("#fileNote");
  const text = $("#fileText");
  const img = $("#fileImg");
  text.hidden = true;
  text.textContent = "";
  img.hidden = true;
  img.removeAttribute("src");
  note.hidden = true;

  if (info.kind === "text") {
    text.hidden = false;
    text.textContent = info.text || "";
    if (info.truncated) {
      note.hidden = false;
      note.textContent = "First 256 KB shown. Copy the path to open the rest.";
    }
  } else if (info.kind === "image") {
    img.hidden = false;
    img.alt = info.name || "";
    img.src = "api/sessions/" + encodeURIComponent(fileSession)
      + "/file?path=" + encodeURIComponent(info.asked || fileAsked) + "&raw=1";
  } else if (info.kind === "dir") {
    note.hidden = false;
    note.textContent = "That is a directory. Send the path if you want to work from it.";
  } else if (info.kind === "binary") {
    note.hidden = false;
    note.textContent = "Not text — copy or send the path to open it in something that can.";
  } else {
    note.hidden = false;
    note.textContent = "Nothing at that path from this session’s directory.";
  }
}

function closeFileSheet() {
  $("#file").hidden = true;
  $("#fileImg").removeAttribute("src");
  $("#fileText").textContent = "";
  fileAsked = "";
  fileSession = "";
}

/* Artifacts — the other direction of paste.
 *
 * Ctrl+V already gets an image *to* an agent: the browser holds the bytes, the
 * pane holds the CLI, and a path is the only thing that can cross. Nothing
 * carried the answer back. A terminal cannot draw a picture, so a screenshot
 * an agent had just taken was something you left the panel to look at.
 *
 * This asks no vendor what it did and needs no agent told it exists — it is
 * files that appeared in the working directory while the session was running,
 * which is the only kind of state this product reads at all. */
const ART_POLL_MS = 6000;
let artItems = [];
let artOpen = false;

function artSrc(item) {
  return "api/sessions/" + encodeURIComponent(activeId)
    + "/artifact?rel=" + encodeURIComponent(item.rel);
}

async function pollArtifacts() {
  // Only the session in front, and only while someone is looking: this is a
  // directory listing per poll, and the whole point of the product is that it
  // costs nothing to leave running.
  if (!activeId || state.settings.artifacts_show === false) return setArtifacts([]);
  if (document.hidden) return;
  try {
    setArtifacts(await api("api/sessions/" + encodeURIComponent(activeId) + "/artifacts"));
  } catch {
    /* A session can end mid-poll. The next one will agree with reality. */
  }
}

function setArtifacts(items) {
  const changed = items.length !== artItems.length
    || items.some((it, i) => it.rel !== artItems[i].rel || it.mtime !== artItems[i].mtime);
  artItems = items;
  const button = $("#artBtn");
  button.hidden = !items.length;
  button.innerHTML = icon("image") + String(items.length);
  button.title = items.length === 1
    ? "1 image this session made"
    : items.length + " images this session made";
  if (artOpen && changed) renderArtifacts();
}

function renderArtifacts() {
  const grid = $("#artGrid");
  grid.textContent = "";
  if (!artItems.length) {
    const note = document.createElement("p");
    note.className = "dim";
    note.textContent = "Nothing yet. Images written into this session\u2019s "
      + "working directory show up here.";
    grid.append(note);
    return;
  }
  for (const item of artItems) {
    const cell = document.createElement("button");
    cell.type = "button";
    cell.className = "art-cell";
    cell.title = item.rel;
    const img = document.createElement("img");
    img.src = artSrc(item);
    img.alt = item.name;
    img.loading = "lazy";        // thirty thumbnails is thirty requests otherwise
    const cap = document.createElement("span");
    cap.textContent = item.name;
    cell.append(img, cap);
    cell.onclick = () => showArtifact(item);
    grid.append(cell);
  }
}

function showArtifact(item) {
  $("#artGrid").hidden = true;
  $("#artOne").hidden = false;
  $("#artBack").hidden = false;
  $("#artImg").src = artSrc(item);
  $("#artImg").alt = item.name;
  $("#artName").textContent = item.rel;
  $("#artTitle").textContent = item.name;
  // The absolute path, because that is what an agent can open — the same
  // thing paste hands over, arriving the same way.
  $("#artSend").onclick = () => {
    closeArtifacts();
    toast("Path is in " + deliverPath(item.path));
  };
  $("#artCopy").onclick = () => copyText(item.path).then(() => toast("Path copied"));
}

function showArtifactGrid() {
  $("#artGrid").hidden = false;
  $("#artOne").hidden = true;
  $("#artBack").hidden = true;
  // Dropped rather than left behind a `hidden`, so a large image is not held
  // in memory for as long as the tab is open.
  $("#artImg").removeAttribute("src");
  $("#artTitle").textContent = "Images";
}

function openArtifacts() {
  artOpen = true;
  $("#art").hidden = false;
  showArtifactGrid();
  renderArtifacts();
  pollArtifacts();
}

function closeArtifacts() {
  artOpen = false;
  $("#art").hidden = true;
  $("#artImg").removeAttribute("src");
}

/* Put the path where the person is already typing, and say where that was.
 *
 * The pane gets it unless they were typing in the prompt box — but only if the
 * pane's socket is actually up. `control` returning false means the text went
 * nowhere, so the box is the fallback rather than the alternative: a path that
 * silently vanishes is worse than one in the wrong place.
 *
 * Either way it lands without stealing focus or sending anything on their
 * behalf — the CLI does not see it until they press enter. */
function deliverPath(path) {
  const box = $("#prompt");
  if (document.activeElement !== box
      && control({ type: "run", text: path + " ", enter: false })) {
    return "the terminal";
  }
  const head = box.value.slice(0, box.selectionStart);
  const tail = box.value.slice(box.selectionEnd);
  const spacer = head && !head.endsWith(" ") ? " " : "";
  box.value = head + spacer + path + " " + tail;
  const at = (head + spacer + path + " ").length;
  box.setSelectionRange(at, at);
  box.dispatchEvent(new Event("input"));
  return "the prompt box";
}

function toast(text, bad, action) {
  const el = $("#toast");
  el.textContent = "";
  el.append(text);
  /* One action, optional. A message that reports something reversible should
   * carry the reversal, rather than describing where to go and do it. */
  if (action) {
    const button = document.createElement("button");
    button.className = "toast-do";
    button.textContent = action.label;
    button.onclick = () => { el.hidden = true; action.run(); };
    el.append(button);
  }
  el.classList.toggle("bad", Boolean(bad));
  el.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => (el.hidden = true), action ? 7000 : 4000);
}

/* ----------------------------------------------------------------- drafts */

/* A half-typed instruction is work, so it survives a tab switch, a reload and
 * a closed laptop — which means it lives on the server, under the same rule
 * that puts settings there and leaves sidebar width in the browser.
 *
 * Written on a debounce rather than per keystroke: thinking with the box open
 * should not be a request per character. The local session object is updated
 * at the same time, so the next poll does not arrive with a stale draft and
 * overwrite what is being typed. */
let draftTimer = null;
let draftFor = null;          // whose draft is in the box right now

/* The move control appears with the text and goes with it. An always-there
 * button for something you can only do half the time is a button people learn
 * to read past. */
function showDraftMove() {
  const button = $("#draftMove");
  if (button) button.hidden = !$("#prompt").value.trim() || state.sessions.length < 2;
}

/* The box grows with what is in it, up to a share of the window.
 *
 * It stopped at 140px, which is about seven lines — small enough that writing
 * a paragraph meant scrolling inside a box while composing, which is the thing
 * that makes people give up and go elsewhere. A fraction of the viewport
 * rather than a fixed number, because the right ceiling on a laptop and on a
 * phone are not the same number. */
const PROMPT_MAX_SHARE = 0.4;

function growPrompt(box) {
  box.style.height = "auto";
  const ceiling = Math.max(140, Math.round(window.innerHeight * PROMPT_MAX_SHARE));
  box.style.height = Math.min(box.scrollHeight, ceiling) + "px";
}

function loadDraft(id) {
  const box = $("#prompt");
  const s = session(id);
  box.value = (s && s.draft) || "";
  growPrompt(box);
  draftFor = id;
  showDraftMove();
}

function saveDraft(now) {
  const id = draftFor;
  showDraftMove();
  if (!id) return;
  const text = $("#prompt").value;
  const s = session(id);
  if (s && (s.draft || "") === text) return;      // nothing actually moved
  if (s) s.draft = text;
  clearTimeout(draftTimer);
  const send = () => api(`api/sessions/${id}`, {
    method: "PATCH", body: JSON.stringify({ draft: text }),
  }).catch(() => {});
  if (now) send();
  else draftTimer = setTimeout(send, 600);
}

/* Move a half-typed thought to the session it actually belongs in.
 *
 * The moment this exists for: you are typing an instruction, and partway
 * through you realise it is the *other* agent that should get it. Today that
 * costs selecting, cutting, switching, pasting — four steps to move text that
 * the panel is already storing on the server. It is one step now, and the
 * draft lands in the target exactly as you left it.
 *
 * Deliberately a move, not a copy. Two sessions holding the same half-written
 * instruction is a way to send it twice, and the whole point was that it
 * belongs somewhere else.
 *
 * A draft already survives tab switches, reloads and a closed laptop, because
 * it lives on the session rather than in the box. This just changes which
 * session that is. */
//: Its own drag type, so a dropped draft is never mistaken for a dropped
//: session id — the sidebar row accepts both and they mean opposite things.
const DRAFT_TYPE = "text/clique-draft";

async function moveDraft(toId) {
  const text = $("#prompt").value;
  const from = draftFor;
  const target = session(toId);
  if (!text.trim() || !target || toId === from) return;

  // Appended, never overwritten: arriving text must not silently destroy a
  // draft that was already waiting in the target. A blank line between them
  // so two thoughts do not become one sentence.
  const already = (target.draft || "").trim();
  const merged = already ? `${already}\n\n${text.trim()}` : text;

  clearTimeout(draftTimer);          // the debounced save would race this
  target.draft = merged;
  await api(`api/sessions/${toId}`, {
    method: "PATCH", body: JSON.stringify({ draft: merged }),
  });
  if (from) {
    const source = session(from);
    if (source) source.draft = "";
    await api(`api/sessions/${from}`, {
      method: "PATCH", body: JSON.stringify({ draft: "" }),
    }).catch(() => {});
  }
  $("#prompt").value = "";
  await openSession(toId);
  loadDraft(toId);
  $("#prompt").focus();
  // The cursor goes to the end, which is where you were in the sentence.
  $("#prompt").setSelectionRange(merged.length, merged.length);
  toast(`Draft moved to ${target.name}` + (already ? " — added under what was there" : ""));
}

/* ---------------------------------------------------------------- support */

/* An address is money, so it is never truncated and never retyped: shown in
 * full, wrapping, with one click to copy. A chain address typed wrong does not
 * bounce — it just loses whatever was sent. */
/* The marks are drawn here rather than fetched: no build step, no CDN, and
 * nothing to 404 on a box with no way out to the internet. They are our own
 * glyphs in each project's colours, not the projects' official logos — which
 * keeps someone else's trademarked artwork out of a repo that is going
 * public. */
const SUPPORT = [
  { label: "Buy me a coffee", detail: "buymeacoffee.com/jdubb",
    href: "https://buymeacoffee.com/jdubb",
    icon: '<svg viewBox="0 0 32 32" class="give-icon" aria-hidden="true"><circle cx="16" cy="16" r="16" fill="#FFDD00"/><path d="M8.8 11.6h13.1l-1.2 10a3.4 3.4 0 0 1-3.4 3h-4a3.4 3.4 0 0 1-3.4-3z" fill="#0d0d0d"/><path d="M22.2 13.6h1.3a2.9 2.9 0 0 1 0 5.8h-.8" fill="none" stroke="#0d0d0d" stroke-width="1.7" stroke-linecap="round"/><g stroke="#0d0d0d" stroke-width="1.5" stroke-linecap="round"><path d="M13 6.4c-.9 1 -.9 2 0 3"/><path d="M17.4 6.4c-.9 1 -.9 2 0 3"/></g></svg>' },
  { label: "BTC", detail: "Bitcoin network",
    address: "3A3nA8BQFmXdvyUQokHhPd8HAd99wRDYFQ",
    icon: '<svg viewBox="0 0 32 32" class="give-icon" aria-hidden="true"><circle cx="16" cy="16" r="16" fill="#F7931A"/><g fill="#fff"><rect x="12.1" y="5.2" width="1.9" height="4.2" rx=".8"/><rect x="16.1" y="5.2" width="1.9" height="4.2" rx=".8"/><rect x="12.1" y="22.6" width="1.9" height="4.2" rx=".8"/><rect x="16.1" y="22.6" width="1.9" height="4.2" rx=".8"/><text x="15.6" y="23" text-anchor="middle" font-size="16.5" font-weight="700" font-family="ui-sans-serif, Helvetica, Arial, sans-serif">B</text></g></svg>' },
  { label: "SHIB", detail: "Ethereum network",
    address: "0x6b5DEd92946692D50642dC3af169727225E32D3b",
    icon: '<svg viewBox="0 0 32 32" class="give-icon" aria-hidden="true"><circle cx="16" cy="16" r="16" fill="#F00500"/><text x="16" y="22.6" text-anchor="middle" fill="#fff" font-size="15" font-weight="700" font-family="ui-sans-serif, Helvetica, Arial, sans-serif">S</text></svg>' },
  { label: "DOGE", detail: "Dogecoin network",
    address: "DNiJeUJUVaVTDuteLXCtP7JVgvdL2NqoYp",
    icon: '<svg viewBox="0 0 32 32" class="give-icon" aria-hidden="true"><circle cx="16" cy="16" r="16" fill="#C2A633"/><text x="16" y="23" text-anchor="middle" fill="#fff" font-size="17" font-weight="700" font-family="ui-sans-serif, Helvetica, Arial, sans-serif">&#208;</text></svg>' },
];

/* The changelog, out of CHANGELOG.md by way of /api/changelog.
 *
 * Fetched the first time the tab is opened rather than at load: it is the one
 * pane most people look at twice a year, and a panel that starts in a quarter
 * of a second should not spend any of that on release notes.
 *
 * The server sends structure, not markup, so every node here is built rather
 * than assigned as HTML — nothing from a file becomes an element by accident.
 */
let clogLoaded = false;

function dayLabel(iso) {
  const [y, m, d] = iso.split("-").map(Number);
  // Built from parts on purpose: `new Date("2026-08-19")` is parsed as UTC
  // midnight, which prints as the 18th anywhere west of Greenwich.
  return new Date(y, m - 1, d).toLocaleDateString(undefined, {
    weekday: "long", year: "numeric", month: "long", day: "numeric",
  });
}

function inlineSpan(span) {
  const el = document.createElement(
    span.c ? "code" : span.b ? "b" : span.i ? "i" : "span");
  el.textContent = span.t;
  return el;
}

function fillSpans(el, spans) {
  for (const span of spans) el.appendChild(inlineSpan(span));
  return el;
}

function changelogEntry(entry, running) {
  const box = document.createElement("article");
  box.className = "clog-entry";

  const head = document.createElement("div");
  head.className = "clog-head";
  const ver = document.createElement("span");
  ver.className = "clog-ver";
  ver.textContent = entry.version;
  head.appendChild(ver);

  const when = document.createElement("span");
  when.className = "clog-when";
  when.textContent = entry.time ? `${entry.time} ${entry.zone}`.trim() : entry.date;
  head.appendChild(when);

  if (entry.version === running) {
    const badge = document.createElement("span");
    badge.className = "clog-now";
    badge.textContent = "you are running this";
    head.appendChild(badge);
  }
  if (entry.extra.length) {
    head.appendChild(fillSpans(
      Object.assign(document.createElement("span"), { className: "clog-extra" }),
      entry.extra));
  }
  box.appendChild(head);

  let list = null;
  for (const block of entry.blocks) {
    if (block.kind === "li") {
      if (!list) { list = document.createElement("ul"); box.appendChild(list); }
      list.appendChild(fillSpans(document.createElement("li"), block.spans));
    } else {
      list = null;
      box.appendChild(fillSpans(document.createElement("p"), block.spans));
    }
  }
  return box;
}

function renderChangelog(entries) {
  const host = $("#changelog");
  host.textContent = "";
  if (!entries.length) {
    host.textContent = "No CHANGELOG.md on this install.";
    return;
  }
  // The sheet holds the last few. The rest is the file on GitHub — a
  // panel that starts in a quarter of a second is not an archive.
  entries = entries.slice(0, CLOG_SHOW);
  // One heading per day, rather than the same date stamped on ten entries:
  // on a busy day the date is noise and only the time carries information.
  const running = (state.version || "").split("+")[0];
  let day = null;
  for (const entry of entries) {
    if (entry.date !== day) {
      day = entry.date;
      const heading = document.createElement("h3");
      heading.className = "clog-day";
      heading.textContent = dayLabel(day);
      host.appendChild(heading);
    }
    host.appendChild(changelogEntry(entry, running));
  }
}

async function loadChangelog() {
  if (clogLoaded) return;
  const host = $("#changelog");
  host.textContent = "Reading the changelog…";
  try {
    // Relative, like every other call. A leading slash escapes the <base href>
    // and resolves against the site root — so this worked on
    // http://127.0.0.1:3200/ and 404ed for anyone reaching the panel through
    // `tailscale serve` at /clique, which is the documented way to run it.
    // "Could not read the changelog" was the only symptom.
    const entries = await api("api/changelog");
    clogLoaded = true;               // only latch on success, so a failure retries
    renderChangelog(entries);
  } catch {
    host.textContent = "Could not read the changelog.";
  }
}

function renderSupport() {
  const host = $("#support");
  if (!host || host.dataset.built) return;
  host.dataset.built = "1";
  for (const item of SUPPORT) {
    const row = document.createElement("div");
    row.className = "give";
    row.innerHTML =
      `<div class="give-head">${item.icon || ""}` +
      `<b>${escapeHtml(item.label)}</b>` +
      `<span class="note">${escapeHtml(item.detail)}</span></div>`;
    if (item.href) {
      const a = document.createElement("a");
      a.href = item.href;
      a.target = "_blank";
      a.rel = "noopener noreferrer";
      a.textContent = item.href;
      a.className = "give-value";
      row.appendChild(a);
    } else {
      const code = document.createElement("code");
      code.className = "give-value";
      code.textContent = item.address;
      row.appendChild(code);
      const copy = document.createElement("button");
      copy.textContent = "Copy";
      copy.className = "give-copy";
      copy.onclick = () => {
        copyText(item.address);
        toast(`${item.label} address copied`);
      };
      row.appendChild(copy);
    }
    host.appendChild(row);
  }
}

/* -------------------------------------------------------------- shortcuts */

/* Every binding, in one table.
 *
 * A key that only exists in a `title` attribute is a key nobody finds. This is
 * the reference the ? button and Ctrl+Shift+/ open, and it is the only place
 * the list is written down — so adding a binding without adding it here is a
 * visible omission rather than a silent one.
 *
 * Modifier is written Ctrl/Cmd because both work: the app takes whichever the
 * platform sends.
 */
const SHORTCUTS = [
  ["Getting around", [
    ["Ctrl/Cmd + K", "The command palette — every session, most recently used first"],
    ["Ctrl/Cmd + Shift + P", "The palette, opened straight into commands"],
    ["Alt + 1…9", "Jump to a tab by its number"],
    ["Ctrl/Cmd + B", "Show or hide the sidebar"],
    ["Ctrl/Cmd + Shift + /", "This list"],
    ["Ctrl/Cmd + Shift + F", "Full screen — the pane, not the browser"],
    ["F11", "Full screen, if this browser will give it up"],
  ]],
  ["Inside the palette", [
    ["@", "Sessions"],
    ["&gt;", "Commands"],
    ["~", "Past conversations, resumable in one click"],
    ['"', "Past prompts, to reuse one in the box"],
  ]],
  ["Reading a pane", [
    ["Scroll up", "Detaches the view, so arriving output cannot drag it away"],
    ["Ctrl/Cmd + Shift + L", "Scroll lock on or off, without scrolling"],
    ["Click the paused badge", "Catch up and start following again"],
  ]],
  ["Working in a session", [
    ["Click a link", "Opens it in a new tab. Ctrl/Cmd + click opens a new window"],
    ["Click a path", "Looks at the file. Ctrl/Cmd + click drops the path where you are typing"],
    ["Ctrl/Cmd + C", "Copy the selected text. With nothing selected, interrupts as usual"],
    ["Ctrl/Cmd + Shift + C", "Copy the selection, or everything on the screen if nothing is selected"],
    ["Ctrl/Cmd + V", "Paste. An image is saved into the session’s folder and the path is dropped where you are typing"],
    ["Tab", "Expand a snippet — in the pane or the prompt box"],
    ["Shift + Tab", "Cycle the autonomy mode. The key is whatever that CLI declares, so it differs between them"],
    ["Enter", "Send what is in the prompt box"],
    ["Esc", "Close the palette, a menu, or this"],
  ]],
];

function showKeys() {
  const body = $("#keysBody");
  body.innerHTML = SHORTCUTS.map(([group, rows]) =>
    `<h3>${escapeHtml(group)}</h3>` + rows.map(([key, what]) =>
      `<div class="row"><span class="keycell">` +
      key.split(" + ").map((k) => `<kbd>${k}</kbd>`).join(" + ") +
      `</span><span class="what">${what}</span></div>`).join("")).join("");
  $("#keys").hidden = false;
}

/* ------------------------------------------------------------ scroll lock */

/* Detaching the viewport from the stream.
 *
 * On a busy session, output arriving while you read drags the view to the
 * bottom mid-sentence, and you cannot read what you cannot hold still — nor
 * copy it. So the viewport can be detached: the pane keeps receiving, tmux
 * keeps the scrollback, and only the view stops moving.
 *
 * The stream is never paused. Freezing it would put the pane out of step with
 * tmux, which holds the real scrollback and does not care what a browser
 * happens to be looking at.
 *
 * Scrolling up *is* the detach gesture, which is why this needs no
 * explaining: the thing people already do in order to read is the thing that
 * stops the yanking. Returning to the bottom re-attaches.
 */
function following(id) {
  const entry = terms.get(id);
  return !entry || entry.follow !== false;
}

function setFollow(id, on) {
  const entry = terms.get(id);
  if (!entry || (entry.follow !== false) === on) return;
  entry.follow = on;
  const buf = entry.term.buffer.active;
  if (on) {
    entry.behind = 0;
    entry.pinning = true;
    entry.term.scrollToBottom();
    entry.pinning = false;
  } else {
    entry.pinned = buf.viewportY;
    entry.baseline = buf.baseY;
    entry.behind = 0;
  }
  if (id === activeId) renderFollow();
}

function toggleFollow() {
  if (activeId) setFollow(activeId, !following(activeId));
}

/* Everything written while detached lands in the buffer as usual; the only
 * correction is putting the viewport back afterwards. That has to happen in
 * the write callback, because it is the first moment the new lines exist. */
function writeOut(entry, id, data) {
  if (entry.follow !== false) return entry.term.write(data);
  entry.term.write(data, () => {
    const buf = entry.term.buffer.active;
    entry.behind = Math.max(0, buf.baseY - entry.baseline);
    if (buf.viewportY !== entry.pinned) {
      entry.pinning = true;
      entry.term.scrollToLine(entry.pinned);
      entry.pinning = false;
    }
    if (id === activeId) renderFollow();
  });
}

/* A paused pane and a dead one look identical, so the badge has to carry both
 * facts: that the view is detached, and how far behind it has fallen. */
function renderFollow() {
  const entry = terms.get(activeId);
  const paused = Boolean(entry && entry.follow === false);
  const badge = $("#follow");
  badge.hidden = !paused;
  if (paused) {
    const n = entry.behind || 0;
    badge.textContent = n
      ? `Paused — ${n} new line${n === 1 ? "" : "s"} below. Click to catch up`
      : "Paused — click to follow again";
  }
  const lock = $("#lock");
  lock.classList.toggle("on", paused);
  lock.title = paused
    ? "Paused — the view is not following output (Ctrl+Shift+L)"
    : "Following output — scroll up, or press Ctrl+Shift+L, to pause";
}

/* A rule across the pane at the point you left it, so coming back to four
 * hundred new lines tells you where your eye stopped. It is a decoration, not
 * text written into the buffer: writing into a pane a full-screen CLI is
 * repainting would garble whatever it was drawing. */
function markDeparture(id) {
  const entry = terms.get(id);
  if (!entry) return;
  try {
    entry.leftMark = entry.term.registerMarker(0);
  } catch (err) { /* proposed API; the unread dot carries on without it */ }
}

function showDeparture(id) {
  const entry = terms.get(id);
  if (!entry || !entry.leftMark) return;
  try {
    if (entry.sinceLine) entry.sinceLine.dispose();
    entry.sinceLine = entry.term.registerDecoration({
      marker: entry.leftMark, x: 0, width: entry.term.cols,
    });
    if (entry.sinceLine) {
      entry.sinceLine.onRender((el) => el.classList.add("since-line"));
    }
  } catch (err) { /* as above: a missing line is not worth an error */ }
}

function syncPanes() {
  /* Which pane is showing is decided from *current* activeId, never from
   * whoever started attaching. A finishing background attach must not
   * uncover itself if you have already clicked away.
   *
   * Opacity, not visibility. Chrome throws away the canvas of a
   * visibility:hidden terminal, and showing it again used to need a dummy
   * resize that also threw away a live selection. An invisible pane stays
   * painted; switching tabs is just which one is in front. */
  for (const [tid, entry] of terms) {
    const on = tid === activeId;
    entry.el.classList.toggle("is-front", on);
    entry.el.style.pointerEvents = on ? "auto" : "none";
  }
}

function layoutPane(entry) {
  /* Boxed CLIs wrap their own chrome when the grid gets narrower — that is
   * the stacked Gemini prompt. Zooming the picture to fit keeps the grid
   * (and the conversation) and still puts the whole pane on screen.
   * A phone is too small to zoom; that still resizes for real. */
  if (!entry || !entry.term || !entry.fit) return;
  const boxed = sessionOwnsInput(entry.id);
  const cell = paneCellPx(entry.term);
  const scale = paneZoomScale(
    entry.el.clientWidth, entry.el.clientHeight,
    entry.term.cols, entry.term.rows, cell.w, cell.h);
  if (paneShouldZoom(boxed, scale)) {
    applyPaneZoom(entry.term, scale);
    return;
  }
  applyPaneZoom(entry.term, 1);
  try { entry.fit.fit(); } catch (err) { /* not laid out yet */ }
}

function paintPane(entry) {
  if (!entry || !entry.term) return;
  // Fitting would throw away a live selection, which is exactly when
  // someone is about to hit Ctrl+C.
  if (entry.term.hasSelection && entry.term.hasSelection()) {
    try { entry.term.refresh(0, entry.term.rows - 1); } catch (err) { /* frozen */ }
    return;
  }
  layoutPane(entry);
  try { entry.term.refresh(0, entry.term.rows - 1); } catch (err) { /* same */ }
}

function showActivePane() {
  syncPanes();
  const entry = terms.get(activeId);
  if (!entry) { renderCopyChip(); return; }
  paintPane(entry);
  entry.term.focus();
  renderCopyChip();
  requestAnimationFrame(() => {
    if (terms.get(activeId) !== entry) return;
    paintPane(entry);
    if (!document.hidden) reclaimSize(true);
  });
}

function selectTab(id) {
  if (activeId && activeId !== id) markDeparture(activeId);
  if (draftFor && draftFor !== id) saveDraft(true);   // commit before reusing the box
  activeId = id;
  attention.delete(id);   // looking at it is the acknowledgement
  // The last session's images are not this one's. Clear first, ask after, so
  // the count never briefly belongs to the tab you just left.
  setArtifacts([]);
  pollArtifacts();
  applyCliTint();
  revealActive();
  renderFollow();         // the badge belongs to the pane you switched to
  loadDraft(id);
  showDeparture(id);
  markSeen(id);
  showActivePane();
  renderTabs();
  renderTree();
  saveWorkspace();
  landFocus();
}

async function openSession(id) {
  /* Paint the tab you clicked *now*, then hook the socket if it is not
   * already there. Waiting on attach first left the previous pane on
   * screen — a click on a tab that had not warmed yet showed someone
   * else's window until the PTY landed. And if you clicked away while
   * that wait was in flight, the finishing attach stole the view back. */
  const row = session(id);
  if (row && !row.alive) {
    try {
      await api("api/sessions/" + encodeURIComponent(id) + "/start", {
        method: "POST", body: "{}",
      });
      await refresh();
    } catch (err) {
      toast(String(err.message || err), true);
      return;
    }
  }
  if (!openTabs.includes(id)) openTabs.push(id);
  selectTab(id);
  if (!terms.has(id)) {
    await attach(id);
    showActivePane();
  }
  landFocus();
}

/* Warm the other open tabs after the one in front is up.
 *
 * The first click used to wait on a new PTY, a socket, and a tmux viewer —
 * a second of empty pane. The strip already listed them; this just hooks
 * the live view in the background so switching is a show, not a hook-up.
 *
 * False restores click-to-attach (0.48). Hidden tabs connect at the
 * window's current size and stay passive until selected, so they cannot
 * steal the pane you are looking at. */
const WARM_BACKGROUND_TABS = true;
const attaching = new Map();   // id -> in-flight attach
let warmTimer = null;

function warmOpenTabs() {
  if (!WARM_BACKGROUND_TABS) return;
  clearTimeout(warmTimer);
  const next = openTabs.find((id) => {
    const s = session(id);
    return s && s.alive && !terms.has(id) && !attaching.has(id);
  });
  if (!next) return;
  warmTimer = setTimeout(() => {
    attach(next).catch(() => {}).finally(warmOpenTabs);
  }, 350);
}

function closeTab(id, silent) {
  const closed = session(id);
  const entry = terms.get(id);
  if (entry) {
    entry.closing = true;
    if (entry.ws) entry.ws.close();
    entry.term.dispose();
    entry.el.remove();
    terms.delete(id);
  }
  openTabs = openTabs.filter((t) => t !== id);
  if (activeId === id) activeId = openTabs[openTabs.length - 1] || null;
  saveWorkspace();
  if (!silent) {
    /* A tab that was only in the strip — never attached — has to attach
     * when it becomes the one in front. selectTab would paint the chrome
     * and leave the pane empty. */
    if (activeId) {
      if (terms.has(activeId)) selectTab(activeId);
      else openSession(activeId);
    } else {
      renderTabs();
      renderTree();
    }
    /* Closing a tab has always kept the session, and nothing ever said so —
     * which made the ✕ read as destructive and had people killing sessions
     * they only meant to put down. Saying it, and offering the other choice
     * in the same breath, is the whole fix. The kill path keeps its
     * confirmation. */
    if (closed && closed.alive) {
      toast(`Closed the tab — ${closed.name} is still running`, false,
            { label: "Kill it instead", run: () => killSession(closed) });
    }
  }
}

/* ------------------------------------------------------------------ terminal */

/* Reconnection: doubling from a second, capped, and finite.
 *
 * The ceiling matters more than the ladder. A tunnel that is down is down for
 * minutes, and a client asking every second for those minutes is a client
 * that would be rate-limited by anything sitting in front of it. Half an hour
 * of trying is well past the point where a human would have reloaded. */
const RETRY_BASE_MS = 1000;
const RETRY_MAX_MS = 30000;
const RETRY_GIVE_UP = 60;

/* A line of our own, over the pane rather than in it.
 *
 * Created on demand and removed when it has nothing to say, so a pane that
 * has never dropped carries no extra element at all. */
function paneNote(entry, text) {
  if (!text) {
    if (entry.note) { entry.note.remove(); entry.note = null; }
    return;
  }
  if (!entry.note) {
    entry.note = document.createElement("div");
    entry.note.className = "pane-note";
    entry.el.appendChild(entry.note);
  }
  entry.note.textContent = text;   // never innerHTML: this sits over a terminal
}

//: What Shift+Enter sends. The CSI-u encoding of "Enter with Shift held" —
//: `ESC [ 13 ; 2 u` — which is what modern CLIs read as a newline and what the
//: editors people compare this to send. Tested against a live Claude Code
//: prompt rather than taken from a specification.
const NEWLINE_SEQ = "\x1b[13;2u";

/* "3 lines" or "42 characters" — enough that the toast confirms *what* was
 * taken, since a copy that silently took the wrong thing is worse than one
 * that failed. */
function plural(text) {
  const lines = text.split("\n").length;
  if (lines > 1) return `${lines} lines`;
  return `${text.length} character${text.length === 1 ? "" : "s"}`;
}

function wsUrl(id, cols, rows) {
  const url = new URL("ws", document.baseURI);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  url.search = `?id=${encodeURIComponent(id)}&cols=${cols}&rows=${rows}`;
  // A tab that is not in front — or one sitting in a background browser
  // tab — must not claim the shared window's size. Reconnect-while-hidden
  // used to steal it from the window you were actually looking at.
  if (id !== activeId || document.hidden) url.searchParams.set("passive", "1");
  return url.toString();
}

function attachSize(id) {
  /* Match the shared window, not this browser's pane. A hidden terminal
   * that reports 0x0 or the local viewport would resize every warmed
   * session the moment it connected — the two-window fight again. */
  const s = session(id);
  if (s && s.cols && s.rows) return { cols: s.cols, rows: s.rows };
  const front = activeId && terms.get(activeId);
  if (front) return { cols: front.term.cols, rows: front.term.rows };
  return { cols: 120, rows: 32 };
}

async function attach(id) {
  if (terms.has(id)) return;
  if (attaching.has(id)) return attaching.get(id);
  const work = attachNow(id).finally(() => attaching.delete(id));
  attaching.set(id, work);
  return work;
}

async function attachNow(id) {
  const size = attachSize(id);
  const box = $("#terminal");
  // A second attach of the same session (reconnect racing a warmup) used
  // to leave an empty host sitting on top of the real pane.
  for (const old of box.querySelectorAll(`[data-session="${id}"]`)) old.remove();
  const host = document.createElement("div");
  /* Always born hidden. Visibility is decided from live activeId once
   * the terminal is in `terms` — a host that guessed "I am in front"
   * at construction time would flash the wrong pane if you had clicked
   * away, and a host that guessed "I am not" would stay blank if you
   * had clicked *to* it while it was still attaching. */
  host.dataset.session = id;
  host.style.cssText = "position:absolute;inset:0;padding:6px 8px;" +
    "pointer-events:none";
  box.appendChild(host);

  // Built with the theme already on, not with the built-in dark and a repaint
  // on the next poll: under a light theme that was a black pane flashing up
  // for a moment every time a session opened.
  const term = new Terminal({
    cols: size.cols,
    rows: size.rows,
    fontSize: state.settings.font_terminal || 13,
    fontFamily: termFontStack(),
    theme: termTheme(currentTheme()),
    scrollback: 20000,
    cursorBlink: true,
    allowProposedApi: true,
    rightClickSelectsWord: true,
    // Drag-select has to win on a Mac too. xterm's default only honours
    // Option, and only when this is on — Shift does nothing there.
    macOptionClickForcesSelection: true,
  });
  const fit = new FitAddon.FitAddon();
  term.loadAddon(fit);

  /* Character widths from Unicode 11, not Unicode 6.
   *
   * xterm.js ships Unicode 6 tables, where U+26A0 WARNING SIGN is one cell
   * wide. Followed by a variation selector it is drawn as an emoji, which
   * every font makes two cells wide — so the terminal reserves one column,
   * the glyph paints two, and the next character lands on top of it. That is
   * the letters-over-each-other in a Claude Code status line, and it is not
   * the CLI doing anything wrong.
   *
   * Loaded before `open` because the width tables have to be in place before
   * anything is measured, and guarded because the addon is vendored — a
   * missing file should cost the fix, not the terminal. */
  if (window.Unicode11Addon) {
    term.loadAddon(new Unicode11Addon.Unicode11Addon());
    term.unicode.activeVersion = "11";
  }

  term.open(host);

  /* A real renderer, rather than the fallback one.
   *
   * xterm's default is the DOM renderer: one element per run of text, which
   * is why a screen that redraws underneath a live selection — an interactive
   * menu being arrowed through, say — can leave fragments of the old text
   * behind. It is not a font problem and it is not a width problem; it is
   * stale nodes. The canvas renderer repaints the whole cell grid every
   * frame, so there is nothing left over to see.
   *
   * Canvas rather than WebGL, deliberately. WebGL is faster and it needs a GPU
   * context that a phone, a VM or a remote session can refuse or lose, and
   * this panel is meant to be opened from anywhere. Canvas has no such
   * dependency and is still a full repaint.
   *
   * Loaded after `open` because it attaches to the element the terminal has
   * just created, and guarded because it is vendored — if the file is missing
   * or the browser refuses a 2d context, the DOM renderer is still there and
   * a terminal that renders imperfectly beats no terminal at all.
   */
  if (window.CanvasAddon) {
    try {
      term.loadAddon(new CanvasAddon.CanvasAddon());
    } catch (err) {
      /* falls back to the DOM renderer on its own */
    }
  }

  // A hidden tab must not measure the pane and then claim that size. The
  // constructor already matched the shared window, which is what history
  // will arrive at. Fitting waits until syncPanes sees this tab in front.
  wireLinks(term, id);

  /* The pane owns the keyboard — deliberately, and that is why tabs are
   * Alt+1..9 rather than plain digits. The palette is the single exception,
   * so it has to be taken from the pane here as well as bound on the
   * document: without this, Ctrl+K would open the palette *and* have the CLI
   * kill to end of line. Ctrl+Shift+P is safe to take outright, since nothing
   * in a terminal claims it. */
  term.attachCustomKeyEventHandler((ev) => {
    if (ev.type !== "keydown") return true;
    if (ev.key === "F11") {
      ev.preventDefault();
      toggleFullscreen();
      return false;
    }

    /* Shift+Enter is a newline, not a submit.
     *
     * A terminal sends carriage return for both, so a CLI cannot tell them
     * apart and every Shift+Enter submitted the prompt — which is why writing
     * anything of more than one line meant giving up and using the panel's own
     * box. The CSI-u encoding is how a terminal says "Enter, with Shift held",
     * and it is what the editors people compare this to send.
     *
     * Verified rather than assumed: typed into a real Claude Code prompt,
     * `ESC CR` did nothing useful and `CSI 13;2u` put the cursor on a second
     * line. A CLI that does not understand it ignores it, which is the same
     * outcome as today. */
    if (ev.key === "Enter" && ev.shiftKey && !ev.ctrlKey && !ev.altKey) {
      paneSend(entry, NEWLINE_SEQ);
      return false;
    }

    if (!(ev.ctrlKey || ev.metaKey)) return true;
    const key = ev.key.toLowerCase();
    // Handed to the document handler, which does the work — exactly as
    // Ctrl+Shift+P is. Acting here as well would toggle it twice.
    if (ev.shiftKey && (key === "l" || key === "/")) return false;
    if (ev.shiftKey && key === "p") return false;
    if (ev.shiftKey && key === "f") {
      ev.preventDefault();
      toggleFullscreen();
      return false;
    }
    if (!ev.shiftKey && key === "k") return !paletteHotkeyOn();

    /* Ctrl+V is paste. In a browser it could not be anything else.
     *
     * xterm treats it as a control character by default — Ctrl+V is 0x16,
     * readline's quoted-insert — and sending that means calling
     * preventDefault, which stops the browser from ever firing the paste
     * event. So Ctrl+V did nothing at all: it was not passed to the CLI in a
     * useful form and it did not paste either. Ctrl+Shift+V worked, and
     * nobody presses Ctrl+Shift+V in a browser without being told to.
     *
     * Returning false hands the keystroke back to the page, the browser
     * pastes into xterm's own textarea, and xterm sends it on. Quoted-insert
     * is the cost, and it is a fair trade for the commonest action there is.
     */
    if (!ev.shiftKey && key === "v") return false;

    /* Ctrl+C copies when something is selected, and interrupts when nothing
     * is. preventDefault is load-bearing: returning false only stops xterm
     * sending SIGINT, and the browser then "copies" from the hidden
     * textarea — which is empty — and overwrites what we just wrote.
     * Ctrl+Shift+C always copies: the selection, or the visible screen.
     */
    if (key === "c" && (ev.shiftKey || term.hasSelection())) {
      const took = copyPaneSelection() || (ev.shiftKey && copyPaneVisible());
      if (took || ev.shiftKey) {
        ev.preventDefault();
        ev.stopPropagation();
        return false;
      }
    }

    return true;
  });
  term.onSelectionChange(() => {
    if (id === activeId) renderCopyChip();
  });
  wirePaneClipboard(term, host, id);

  const entry = { id, term, fit, el: host, ws: null, closing: false, typed: "",
                  follow: true, behind: 0, pinned: 0, baseline: 0, attempt: 0,
                  note: null, kicking: false, outbox: [] };
  terms.set(id, entry);
  if (!openTabs.includes(id)) {
    entry.closing = true;
    term.dispose();
    host.remove();
    terms.delete(id);
    return;
  }
  // The tab you clicked is already active; uncover this pane now rather
  // than waiting for the socket. A warmup for a background tab stays hidden
  // and must not refit the pane you are looking at.
  if (id === activeId) showActivePane();
  else syncPanes();

  /* Scrolling up detaches the viewport; arriving back at the bottom
   * re-attaches it. Our own corrections set `pinning`, so they are never
   * mistaken for a gesture. */
  term.onScroll(() => {
    if (entry.pinning) return;
    const buf = term.buffer.active;
    if (buf.viewportY >= buf.baseY) { setFollow(id, true); return; }
    if (entry.follow !== false) { setFollow(id, false); return; }
    entry.pinned = buf.viewportY;      // still reading, just moved
  });

  const connect = () => {
    const ws = new WebSocket(wsUrl(id, term.cols, term.rows));
    ws.binaryType = "arraybuffer";
    entry.ws = ws;

    ws.onmessage = (ev) => {
      // Any byte from the server means the connection is good again, so the
      // next drop starts from one second rather than from wherever the last
      // outage climbed to.
      entry.attempt = 0;
      writeOut(entry, id,
        typeof ev.data === "string" ? ev.data : new Uint8Array(ev.data));
    };
    ws.onopen = () => {
      entry.attempt = 0;
      paneNote(entry, "");
      paneFlushOut(entry);
      if (id === activeId) wakePane();
    };
    ws.onclose = () => {
      if (entry.closing) return;
      /* A dropped tailnet connection should heal itself; a killed session
       * should not be retried forever. The comment always said that and the
       * code never did it — a fixed two-second retry with no end.
       *
       * And none of it belongs in the scrollback. Writing "disconnected"
       * into the terminal puts our own chrome into the user's output, where
       * it is permanent, unscrollable-past, and indistinguishable from
       * something their program printed. A restart during an evening's work
       * left a screen that was nothing but our own status lines, with the
       * real output pushed off the top. Connection state is *about* the pane,
       * not *from* it, so it goes on an overlay that clears itself. */
      const known = session(id);
      if (known && !known.alive) return paneNote(entry, "Session ended");
      if (entry.attempt >= RETRY_GIVE_UP) {
        return paneNote(entry, "Not reconnecting — close and reopen the tab");
      }
      const wait = Math.min(RETRY_BASE_MS * 2 ** entry.attempt, RETRY_MAX_MS);
      entry.attempt += 1;
      paneNote(entry, entry.attempt === 1
        ? "Reconnecting…"
        : `Reconnecting… (attempt ${entry.attempt})`);
      entry.retry = setTimeout(() => { if (!entry.closing) connect(); }, wait);
    };
    ws.onerror = () => {};
  };
  connect();

  const send = (text) => paneSend(entry, text);

  term.onData((data) => {
    /* Snippets work in the CLI's own input field too, because CLIque owns
     * the pseudo-terminal — an expansion is simply typed into the pane. We
     * track what has been typed since the last Enter so we know how many
     * characters to erase before sending the replacement. That mirrors the
     * CLI's own line editor rather than reading it, which is why Escape and
     * Enter reset the buffer: those are the points where any editor we might
     * be shadowing has certainly cleared its line. */
    if (data === "\t" && entry.typed) {
      const found = snippetFor(entry.typed);
      if (found) {
        send("\u007f".repeat(found.trigger.length) + expandText(found.text, true));
        entry.typed = "";
        return;   // swallow the Tab; it was the expansion key this time
      }
    }
    /* The user cycling the mode themselves must move the pill too, or it
     * silently drifts and starts describing a mode the CLI is not in. The key
     * is passed straight through — this observes it, it does not intercept
     * it. A CLI whose key we cannot translate reports an empty sequence and
     * simply never matches. */
    const current = session(id);
    if (current && current.mode_seq && data === current.mode_seq) {
      cycleMode(current, true);
    }

    if (data === "\r" || data === "\n" || data === "\u001b") entry.typed = "";
    else if (data === "\u007f") entry.typed = entry.typed.slice(0, -1);
    else if (data >= " " && data.length === 1) entry.typed += data;
    else if (data.length > 1) entry.typed = "";   // paste or escape sequence

    send(data);
  });
  term.onResize(({ cols, rows }) => {
    if (entry.kicking) return;     // paintPane's one-column nudge is not a resize
    if (id !== activeId) return;   // a hidden tab must not resize the window
    if (document.hidden || !document.hasFocus()) return;
    if (!claimable(cols, rows)) return;
    if (entry.ws && entry.ws.readyState === 1) {
      entry.ws.send(JSON.stringify({ type: "resize", cols, rows }));
    }
  });
}

function control(message) {
  const entry = terms.get(activeId);
  if (entry && entry.ws && entry.ws.readyState === 1) {
    entry.ws.send(JSON.stringify(message));
    return true;
  }
  return false;
}

/* ----------------------------------------------------------------- snippets */

function snippets() { return state.settings.snippets || []; }

function expandText(text, forTerminal) {
  const s = session(activeId);
  const filled = text
    .replaceAll("{cwd}", s ? s.cwd : "")
    .replaceAll("{project}", s ? s.project : "")
    .replaceAll("{name}", s ? s.name : "");
  // {cursor} can only mean something where we control the caret. Injecting
  // into a CLI's own input field gives us no such control, so it is dropped
  // there rather than left in the text as a stray token.
  return forTerminal ? filled.replaceAll("{cursor}", "") : filled;
}

function snippetFor(typed) {
  // Longest trigger first, so ";rev" and ";review" can coexist.
  return [...snippets()]
    .sort((a, b) => b.trigger.length - a.trigger.length)
    .find((sn) => typed.endsWith(sn.trigger));
}

function expandInBox(box) {
  const before = box.value.slice(0, box.selectionStart);
  const found = snippetFor(before);
  if (!found) return false;
  const filled = expandText(found.text, false);
  const caret = filled.indexOf("{cursor}");
  const body = filled.replace("{cursor}", "");
  const head = before.slice(0, before.length - found.trigger.length);
  const tail = box.value.slice(box.selectionStart);
  box.value = head + body + tail;
  const at = head.length + (caret >= 0 ? caret : body.length);
  box.setSelectionRange(at, at);
  box.dispatchEvent(new Event("input"));
  return true;
}

/* --------------------------------------------------------------- input bar */

async function run(text) {
  if (!text.trim() || !activeId) return;
  for (let i = 0; i < repeat; i++) {
    if (!control({ type: "run", text, enter: true })) {
      await api(`api/sessions/${activeId}/send`, {
        method: "POST", body: JSON.stringify({ text, enter: true }),
      });
    }
  }
  $("#prompt").value = "";
  saveDraft(true);   // sent, so there is no longer a draft
  setRepeat(1);
}

async function runShell(text) {
  /* "Shell" sends a raw command rather than a prompt. The active pane belongs
   * to a CLI, so the command goes to a shell session for the same directory —
   * reusing one if it exists, creating it if not. Typing `rm -rf` into Claude's
   * prompt box and having it land in a shell would be worse than either. */
  const current = session(activeId);
  if (!current) return;
  let shell = state.sessions.find(
    (s) => s.cli === "shell" && s.cwd === current.cwd && s.alive);
  if (!shell) {
    const created = await api("api/sessions", {
      method: "POST",
      body: JSON.stringify({
        cli: "shell", cwd: current.cwd, folder: current.folder,
        name: "shell: " + (current.cwd.split("/").pop() || current.cwd),
      }),
    });
    await refresh();
    shell = session(created.id);
  }
  await openSession(shell.id);
  await api(`api/sessions/${shell.id}/send`, {
    method: "POST", body: JSON.stringify({ text, enter: true }),
  });
  $("#prompt").value = "";
  saveDraft(true);   // sent, so there is no longer a draft
}

function setRepeat(value) {
  repeat = Math.max(1, Math.min(99, value));
  $("#repeat").textContent = repeat;
}

/* One icon from the sprite in index.html.
 *
 * A string rather than an element, because every caller here is building
 * markup. `name` is never user data — it is a literal at every call site — so
 * there is nothing to escape, and the sprite is the only place the drawing
 * lives. */
function icon(name, cls = "ico") {
  return `<svg class="${cls}" aria-hidden="true"><use href="#i-${name}"/></svg>`;
}

function escapeHtml(text) {
  return String(text).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

/* ------------------------------------------------------------------- modal */

/* A sentence before you start, not a lock afterwards.
 *
 * Two agents in one directory is the cheap mistake with the expensive
 * recovery: both editing the same files, neither aware of the other, and an
 * afternoon working out which change came from where. Nothing here prevents
 * it — no locks, no forced worktrees, no refusing to start. Somebody starting
 * a second session in a busy folder usually means to. They just did not know.
 *
 * On demand, and debounced: this touches the disk and runs git, so it happens
 * when a person has stopped typing a path, not on every keystroke. An idle
 * panel never calls it at all.
 */
let headsTimer = null;

function checkWorkspace() {
  clearTimeout(headsTimer);
  headsTimer = setTimeout(async () => {
    const box = $("#modalHeads");
    const cwd = $("#newForm").cwd.value.trim();
    box.hidden = true;
    if (!cwd) return;
    let look;
    try {
      look = await api("api/workspace?cwd=" + encodeURIComponent(cwd));
    } catch {
      return;                       // never worth interrupting a launch over
    }
    // Still the directory we asked about? The field moves while we are away.
    if ($("#newForm").cwd.value.trim() !== cwd) return;

    /* A path that is not there is worth saying now rather than on submit.
     *
     * It used to fall through to the server's error, which is correct and
     * arrives too late: you have already filled in the name, chosen the CLI
     * and pressed Start. It also happens for a reason the panel can explain —
     * the picker offers directories from past conversations, and a project
     * that has since been moved or deleted is still in that history. */
    box.classList.toggle("missing", !look.exists);
    if (!look.exists) {
      box.textContent = "There is no directory at that path" +
        (knownDirs().some((d) => d.cwd === cwd)
          ? " any more — it is remembered from an earlier session" : "");
      /* And an offer to fix it, rather than a dead end.
       *
       * Sending someone off to find a shell to run `mkdir` is a poor answer
       * from a tool whose entire job is running shells. Never automatic: the
       * directory is created because this button was pressed, naming the path
       * it is about to make. */
      const make = document.createElement("button");
      make.type = "button";
      make.className = "heads-make";
      make.textContent = "Create it";
      make.onclick = async () => {
        make.disabled = true;
        try {
          await api("api/workspace", {
            method: "POST", body: JSON.stringify({ cwd }),
          });
          toast("Created " + cwd);
        } catch (err) {
          box.textContent = String(err.message || err);
          return;
        }
        checkWorkspace();
      };
      box.append(" ", make);
      box.hidden = false;
      return;
    }

    const said = [];
    if (look.sessions.length === 1) {
      said.push(`${look.sessions[0].name} is already running here`);
    } else if (look.sessions.length > 1) {
      said.push(`${look.sessions.length} sessions are already running here`);
    }
    if (look.touched) {
      said.push(`${look.touched} file${look.touched === 1 ? " was" : "s were"}` +
                " written in the last 15 minutes");
    }
    // Uncommitted work is only worth mentioning alongside something else. On
    // its own it is the normal state of every repo anyone works in, and a
    // panel that says so every time is a panel you stop reading.
    if (said.length && look.dirty) {
      said.push(`${look.dirty} uncommitted change${look.dirty === 1 ? "" : "s"}` +
                (look.branch ? ` on ${look.branch}` : ""));
    }
    if (!said.length) return;
    box.textContent = said.join(" · ");
    box.hidden = false;
  }, 350);
}

/* Every directory CLIque already knows you work in.
 *
 * Typing a path from memory is the slowest part of starting a session, and
 * the panel has never needed to be told: it knows where every live session is
 * running, where every past one ran, and — from the conversation history —
 * where you were working before CLIque existed. This is that, ranked, with no
 * new state and nothing asked of the server.
 *
 * A native `<datalist>` rather than a picker. It is type-ahead on a desktop
 * and a proper suggestion list on a phone keyboard, it costs no widget, no
 * library and no build step, and typing somewhere it has never heard of still
 * works — which a dropdown would have taken away.
 *
 * The order is how likely it is to be the answer:
 *   1. running here now
 *   2. sessions you looked at most recently
 *   3. directories from past conversations
 */
const CWD_SUGGESTIONS = 24;

const CWD_GROUPS = { running: "Running now", recent: "Recent", history: "From history" };

function knownDirs() {
  const rank = new Map();          // cwd -> { score, kind }
  const note = (cwd, score, kind) => {
    if (!cwd) return;
    const had = rank.get(cwd);
    if (!had || score > had.score) rank.set(cwd, { score, kind });
  };

  for (const s of state.sessions || []) {
    if (s.archived) continue;
    // Alive outranks everything, then most recently looked at. last_seen is
    // seconds, so it is already the tiebreaker — it just needs to sit below
    // the alive bonus rather than swamping it.
    note(s.cwd, (s.alive ? 4e9 : 0) + (s.last_seen || s.created || 0),
         s.alive ? "running" : "recent");
  }
  for (const c of resumable || []) note(c.cwd, (c.updated || 0) / 2, "history");

  return [...rank.entries()]
    .sort((a, b) => b[1].score - a[1].score)
    .slice(0, CWD_SUGGESTIONS)
    .map(([cwd, meta]) => ({ cwd, kind: meta.kind }));
}

/* Two controls for one field, and they are not the same control twice.
 *
 * The list is a `<select>` because a `<datalist>` alone could not do the job:
 * a browser filters those by whatever is already in the input, and the input
 * is prefilled with the directory you are in — so the suggestion list showed
 * exactly one entry, the one you already had, and looked like the panel knew
 * nothing. A select shows everything regardless of what is typed.
 *
 * The datalist stays anyway, because it earns its place at a different moment:
 * once you start typing a path it is type-ahead over the same list, and it
 * costs nothing when unused.
 */
function fillCwdList() {
  const dirs = knownDirs();

  fillDatalist(dirs.map((d) => d.cwd));

  const pick = $("#cwdPick");
  pick.textContent = "";
  const head = document.createElement("option");
  head.value = "";
  head.textContent = dirs.length ? "Recent…" : "Nowhere yet";
  pick.appendChild(head);

  // Grouped, because "running now" and "somewhere you were in June" are
  // different kinds of answer and a flat list of twenty paths makes you read
  // all of them to find that out.
  for (const [kind, label] of Object.entries(CWD_GROUPS)) {
    const mine = dirs.filter((d) => d.kind === kind);
    if (!mine.length) continue;
    const group = document.createElement("optgroup");
    group.label = label;
    for (const { cwd } of mine) {
      const option = document.createElement("option");
      option.value = cwd;
      // Shortened for the list, full path as the value: a select as wide as
      // the longest path anyone has ever worked in is not a usable control.
      option.textContent = shortPath(cwd);
      option.title = cwd;
      group.appendChild(option);
    }
    pick.appendChild(group);
  }
  pick.disabled = !dirs.length;
}

function fillDatalist(paths) {
  const list = $("#cwdList");
  list.textContent = "";
  for (const cwd of paths) {
    const option = document.createElement("option");
    option.value = cwd;
    list.appendChild(option);
  }
}

/* Completing a path you have never opened here.
 *
 * The dropdown knows everywhere you have been, which is the right first answer
 * and no answer at all for a project you have not started a session in yet —
 * exactly when you are least likely to remember where it lives. So once you
 * start typing a path, the suggestions come from the disk instead, the way a
 * shell completes: a trailing slash lists what is inside, anything else
 * matches the last segment against its siblings.
 *
 * Debounced, and only for something that already looks like a path, so
 * ordinary typing does not become a directory listing per keystroke. */
let browseTimer = null;

function browseFrom(text) {
  clearTimeout(browseTimer);
  if (!text.startsWith("/") && !text.startsWith("~")) {
    return fillDatalist(knownDirs().map((d) => d.cwd));
  }
  browseTimer = setTimeout(async () => {
    let dirs = [];
    try {
      dirs = (await api("api/browse?path=" + encodeURIComponent(text))).dirs || [];
    } catch {
      return;                       // never worth interrupting a launch over
    }
    if ($("#newForm").cwd.value.trim() !== text) return;   // they typed on
    /* The remembered directories stay in the list alongside what is on disk.
     * They are ranked by how likely they are to be the answer, and losing that
     * the moment someone types a slash would be trading a good answer for a
     * complete one. */
    const known = knownDirs().map((d) => d.cwd)
      .filter((cwd) => cwd.toLowerCase().startsWith(text.toLowerCase()));
    fillDatalist([...new Set([...known, ...dirs])]);
  }, 180);
}

function openModal() {
  const form = $("#newForm");
  const cliSelect = form.cli;
  cliSelect.innerHTML = "";
  // Auto-detected: only CLIs whose binary is actually present are offered.
  // Listing the rest as disabled options was just noise once the catalogue
  // grew past a handful.
  const available = state.clis.filter((c) => c.installed);
  for (const cli of available) {
    const option = document.createElement("option");
    option.value = cli.id;
    option.textContent = cli.label;
    cliSelect.appendChild(option);
  }
  if (!available.length) {
    const option = document.createElement("option");
    option.textContent = "No CLIs detected on this box";
    option.disabled = true;
    cliSelect.appendChild(option);
  }
  const folderSelect = form.folder;
  folderSelect.innerHTML = '<option value="">Auto (by directory)</option>';
  for (const folder of state.folders) {
    const option = document.createElement("option");
    option.value = folder.id;
    option.textContent = folder.name;
    folderSelect.appendChild(option);
  }
  const current = session(activeId);
  fillCwdList();
  /* Where you are, then where you were, then the server's own home — never a
   * path baked into the page. "/root" lived here until now, which was the
   * machine this was written on rather than anybody else's default. */
  form.cwd.value = current ? current.cwd
                          : ((knownDirs()[0] || {}).cwd || state.home || "");
  $("#cwdPick").onchange = (ev) => {
    if (!ev.target.value) return;
    form.cwd.value = ev.target.value;
    ev.target.selectedIndex = 0;   // back to "Recent…", so it reads as a picker
    checkWorkspace();
  };
  $("#modalErr").hidden = true;
  form.cwd.oninput = () => { checkWorkspace(); browseFrom(form.cwd.value.trim()); };
  checkWorkspace();
  $("#modal").hidden = false;
  form.name.focus();
}

/* --------------------------------------------------------- applying settings */

function styleSlot(name) {
  let el = document.getElementById("css-" + name);
  if (!el) {
    el = document.createElement("style");
    el.id = "css-" + name;
    document.head.appendChild(el);
  }
  return el;
}

/* The 256-colour palette, for themes that want to own it.
 *
 * A theme defines the sixteen ANSI colours. Indices 16-255 are the standard
 * xterm cube and greyscale ramp, and nothing here touched them — so a CLI that
 * paints its background with, say, colour 233 got neutral #121212 on every
 * theme ever written. Grok does exactly that, which is why its pane looked
 * untouched by a theme that had in fact been applied to it.
 *
 * Overriding the cube would be wrong: an application choosing colour 82 wants
 * that green, not our idea of green. The *greyscale ramp* is different. Apps
 * reach for 232-255 to mean "a shade near the background", which is a relative
 * intention rather than an absolute colour, and honouring it against the
 * theme's own background is closer to what they asked for than neutral grey.
 *
 * Opt-in per theme, because it is only right for a theme that is monochrome by
 * design. Left alone, a theme keeps the standard ramp.
 */
const CUBE = [0, 95, 135, 175, 215, 255];

function hexRgb(hex) {
  const h = hex.replace("#", "");
  const full = h.length === 3 ? [...h].map((c) => c + c).join("") : h;
  return [0, 2, 4].map((i) => parseInt(full.slice(i, i + 2), 16) || 0);
}

function rgbHex([r, g, b]) {
  return "#" + [r, g, b].map((v) =>
    Math.max(0, Math.min(255, Math.round(v))).toString(16).padStart(2, "0")).join("");
}

/* Hue and saturation from a colour; lightness is not taken, because lightness
 * is the thing being preserved. */
function hueSat(hex) {
  const [r, g, b] = hexRgb(hex).map((v) => v / 255);
  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  const light = (max + min) / 2;
  if (max === min) return [0, 0];
  const span = max - min;
  const sat = light > 0.5 ? span / (2 - max - min) : span / (max + min);
  let hue;
  if (max === r) hue = (g - b) / span + (g < b ? 6 : 0);
  else if (max === g) hue = (b - r) / span + 2;
  else hue = (r - g) / span + 4;
  return [hue / 6, sat];
}

function hslHex(h, s, l) {
  if (!s) { const v = Math.round(l * 255); return rgbHex([v, v, v]); }
  const q = l < 0.5 ? l * (1 + s) : l + s - l * s;
  const p = 2 * l - q;
  const channel = (t) => {
    if (t < 0) t += 1;
    if (t > 1) t -= 1;
    if (t < 1 / 6) return p + (q - p) * 6 * t;
    if (t < 1 / 2) return q;
    if (t < 2 / 3) return p + (q - p) * (2 / 3 - t) * 6;
    return p;
  };
  return rgbHex([channel(h + 1 / 3) * 255, channel(h) * 255, channel(h - 1 / 3) * 255]);
}

/* How much of the theme's hue the greys take, when a theme does not say.
 * Deliberately small: this is a tint, and anything stronger stops being one.
 * A theme can set `tint_greys` to a number of its own to go gentler or
 * further, which is a line of config rather than a change here. */
const TINT = 0.22;

function extendedAnsi(theme) {
  const out = [];
  for (let r = 0; r < 6; r++) {
    for (let g = 0; g < 6; g++) {
      for (let b = 0; b < 6; b++) out.push(rgbHex([CUBE[r], CUBE[g], CUBE[b]]));
    }
  }
  /* Lightness is kept, hue is replaced.
   *
   * The first attempt at this walked from the theme's background to its
   * foreground, and on a monochrome theme that is a walk into a saturated
   * colour — so shades an application meant as *subtle* came out as a wash,
   * and the text sitting on them lost its contrast.
   *
   * The greyscale ramp is a set of lightness steps. An application picking one
   * has chosen how far from the background it wants to be, and that choice is
   * the part worth keeping; only the neutrality is ours to change. So each
   * step keeps the lightness the standard ramp gives it and takes the theme's
   * hue at a low saturation. Contrast relationships survive intact, which is
   * what makes it readable rather than merely coloured.
   *
   * The hue comes from the accent — a theme's background is often nearly
   * neutral, and asking a near-grey what colour it is gets you no answer. */
  const [hue, sat] = hueSat(theme.panel.accent || theme.term.foreground || "#808080");
  const strength = typeof theme.tint_greys === "number" ? theme.tint_greys : TINT;
  for (let i = 0; i < 24; i++) {
    const grey = (8 + 10 * i) / 255;      // exactly what xterm would have used
    out.push(hslHex(hue, Math.min(sat, Math.max(0, strength)), grey));
  }
  return out;
}

const _termThemes = new Map();

function termTheme(theme) {
  if (!theme.tint_greys) return theme.term || {};
  let built = _termThemes.get(theme);
  if (!built) {
    built = { ...theme.term, extendedAnsi: extendedAnsi(theme) };
    _termThemes.set(theme, built);
  }
  return built;
}

function currentTheme() {
  const themes = window.CLIQUE_THEMES || {};
  const s = state.settings;
  if (s.theme && themes[s.theme]) return themes[s.theme];
  // No preset chosen: the base appearance picks which built-in to use.
  const wantsLight = s.appearance === "light" ||
    (s.appearance === "system" && matchMedia("(prefers-color-scheme: light)").matches);
  return themes[wantsLight ? "light" : ""] || themes[""];
}

/* Relative luminance, the WCAG definition. Used only to decide whether text
 * sitting on a theme's accent should be white or near-black. */
function luminance(hex) {
  const value = hex.replace("#", "");
  const full = value.length === 3 ? [...value].map((c) => c + c).join("") : value;
  const channels = [0, 2, 4].map((i) => parseInt(full.slice(i, i + 2), 16) / 255)
    .map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
  return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2];
}

/* The tokens a theme should not have to spell out.
 *
 * A theme is meant to be one block in themes.js and nothing else — that rule is
 * what has kept adding one cheap. So the things that follow mechanically from
 * a theme are computed here rather than being three more keys every theme has
 * to remember to set, and get wrong.
 *
 * White text on a pale accent is unreadable, and a black scrim over a light
 * theme looks like a bug rather than a modal. Both were hardcoded until now,
 * which is why the light themes had a black wash over them. */
function derived(theme) {
  const panel = theme.panel || {};
  const light = (theme.base || "dark") === "light";
  const accent = panel.accent || "#0078d4";
  return {
    "on-accent": luminance(accent) > 0.45 ? "#101317" : "#ffffff",
    "scrim": light ? "#2b313bb0" : "#000000aa",
    "shadow": light ? "#2b313b33" : "#00000088",
  };
}

/* Monospace stacks that exist on Windows, Mac and Linux.
 *
 * Each id is a chain, not a single face: the first font that is actually
 * installed wins, and `monospace` is always last so a missing font can
 * never fall through to a proportional one and ruin the grid. Ligatures
 * stay off — `fi` as one glyph steals a column. */
const FONT_FAMILIES = [
  { id: "system",   label: "System",
    stack: 'ui-monospace, "SF Mono", Menlo, Consolas, "Cascadia Mono", "Ubuntu Mono", "DejaVu Sans Mono", monospace' },
  { id: "menlo",    label: "Menlo / SF Mono",
    stack: 'Menlo, "SF Mono", "Cascadia Mono", Consolas, "Ubuntu Mono", "DejaVu Sans Mono", monospace' },
  { id: "consolas", label: "Consolas / Cascadia",
    stack: '"Cascadia Mono", Consolas, "Ubuntu Mono", Menlo, "DejaVu Sans Mono", monospace' },
  { id: "ubuntu",   label: "Ubuntu / DejaVu",
    stack: '"Ubuntu Mono", "DejaVu Sans Mono", "Liberation Mono", Consolas, Menlo, monospace' },
  { id: "courier",  label: "Courier",
    stack: '"Courier New", Courier, monospace' },
];
const FONT_MIN = 9;
const FONT_MAX = 28;

function termFontStack() {
  const id = (state.settings && state.settings.font_family) || "system";
  const row = FONT_FAMILIES.find((item) => item.id === id);
  return (row || FONT_FAMILIES[0]).stack;
}

function paintFontChrome() {
  const size = Number((state.settings || {}).font_terminal) || 13;
  const val = $("#fontSizeVal");
  if (val) val.textContent = String(size);
  const minus = $("#fontMinus");
  const plus = $("#fontPlus");
  if (minus) minus.disabled = size <= FONT_MIN;
  if (plus) plus.disabled = size >= FONT_MAX;
  const slider = $("#setFontTerminal");
  const out = $("#outFontTerminal");
  if (slider && document.activeElement !== slider) {
    slider.value = size;
    if (out) out.textContent = size + "px";
  }
  const pick = $("#setFontFamily");
  if (pick && document.activeElement !== pick) {
    pick.value = (state.settings && state.settings.font_family) || "system";
  }
}

function bumpTermFont(delta) {
  const now = Number(state.settings.font_terminal) || 13;
  const size = Math.min(FONT_MAX, Math.max(FONT_MIN, now + delta));
  if (size === now) return;
  saveSettings({ font_terminal: size });
}

function applySettings() {
  const s = state.settings;
  // Repainted from here as well as from selectTab, so toggling the setting or
  // changing a colour takes effect without switching tabs to see it.
  applyCliTint();
  const theme = currentTheme();
  const root = document.documentElement;

  for (const [name, value] of Object.entries(theme.panel || {})) {
    root.style.setProperty("--" + name, value);
  }
  for (const [name, value] of Object.entries(derived(theme))) {
    root.style.setProperty("--" + name, value);
  }
  // colorScheme is what makes native scrollbars, form controls and the
  // terminal's own selection colour agree with the theme. Without it a light
  // theme still gets dark scrollbars.
  root.style.colorScheme = theme.base || "dark";
  root.style.setProperty("--font-panel", (s.font_panel || 13) + "px");
  paintFontChrome();

  /* Applied when it changes, not on every poll.
   *
   * This runs three seconds apart forever, and assigning `options.theme` makes
   * xterm rebuild its colour service and repaint the whole grid — so a panel
   * left open was repainting every terminal it had, twenty times a minute, to
   * arrive at exactly the colours already on screen. */
  const stamp = (s.theme || "") + "|" + (s.appearance || "") + "|" +
    (s.font_terminal || 13) + "|" + (s.font_family || "system");
  for (const entry of terms.values()) {
    if (entry.painted === stamp) continue;
    entry.painted = stamp;
    entry.term.options.fontSize = s.font_terminal || 13;
    entry.term.options.fontFamily = termFontStack();
    entry.term.options.theme = termTheme(theme);
    try { entry.fit.fit(); } catch (err) { /* not visible yet */ }
  }

  // Order matters and is documented in the settings sheet: both, panel, then
  // terminal. The terminal block is wrapped so "terminal only" means it.
  styleSlot("both").textContent = s.css_both || "";
  styleSlot("panel").textContent = s.css_panel || "";
  styleSlot("term").textContent = s.css_terminal
    ? `#termwrap { ${s.css_terminal} }` : "";

  // The input bar is decided per session, not per settings change — "auto"
  // depends on which CLI is in front. renderInputBar owns it.
  renderInputBar();
}

/* ------------------------------------------------------------ notifications */

function chime() {
  // Synthesised rather than shipped as a file: two notes need no asset, no
  // download, and no decision about what format to vendor.
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const now = ctx.currentTime;
    for (const [i, freq] of [880, 1174.7].entries()) {
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = "sine";
      osc.frequency.value = freq;
      gain.gain.setValueAtTime(0.0001, now + i * 0.12);
      gain.gain.exponentialRampToValueAtTime(0.12, now + i * 0.12 + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.0001, now + i * 0.12 + 0.28);
      osc.connect(gain).connect(ctx.destination);
      osc.start(now + i * 0.12);
      osc.stop(now + i * 0.12 + 0.3);
    }
    setTimeout(() => ctx.close(), 900);
  } catch (err) {
    /* autoplay policy, or no audio device. Never worth an error. */
  }
}

function noticeFinished(sessions) {
  const s = state.settings;
  /* Both of these are keyed by session id and neither had anything that
   * removed a key. A panel left open for a week — which is the way this is
   * meant to be used — creates and deletes sessions all day, and every one of
   * them left an entry behind for as long as the tab stayed open. `busyUntil`
   * was already pruned this way; these two were simply missed. */
  const live = new Set(sessions.map((x) => x.id));
  for (const id of [...wasBusy.keys()]) if (!live.has(id)) wasBusy.delete(id);
  for (const id of [...attention]) if (!live.has(id)) attention.delete(id);

  for (const session of sessions) {
    const before = wasBusy.get(session.id) || false;
    wasBusy.set(session.id, session.busy);
    if (!before || session.busy) continue;

    // Finished. Only worth saying so if he was not already watching it.
    const watching = session.id === activeId && document.hasFocus();
    if (watching) continue;
    // A question is work paused, not work finished. The asking ring
    // already has this; stuffing it into "finished, not opened" would
    // pulse and knock at once for the same fact.
    if (session.signal === "waiting" || session.signal === "error") continue;
    if (s.notify_flash !== false) attention.add(session.id);
    if (s.notify_sound) chime();
  }
}

/* ----------------------------------------------------------------- settings */

async function saveSettings(changes) {
  // Settings live on the server, not in localStorage, so the panel looks the
  // same on his phone as on the desktop.
  state.settings = await api("api/settings", {
    method: "PATCH", body: JSON.stringify(changes),
  });
  // Apply here rather than waiting for the poll. Without this a theme change
  // took up to three seconds to reach the panel and the open terminals, which
  // reads as "changing the theme did not change the terminal" — especially
  // with the settings sheet still covering the pane you were looking at.
  applySettings();
  // A font change resizes the grid inside the pane, not the box around it, so
  // the ResizeObserver that normally pushes a new size to tmux never fires —
  // and the CLI keeps drawing at the old width, leaving dead space. Push it
  // from here. Guarded (only sends on a real change) and user-initiated, so it
  // does not become the timer-based reclaim two windows would fight over.
  reclaimSize();
  renderTree();
  renderTabs();
}

function openSettings() {
  const s = state.settings;
  renderSupport();   // About is one click away, so the list has to be there

  const themeSelect = $("#setTheme");
  themeSelect.innerHTML = "";
  for (const [id, theme] of Object.entries(window.CLIQUE_THEMES || {})) {
    const option = document.createElement("option");
    option.value = id;
    option.textContent = theme.label;
    option.selected = id === (s.theme || "");
    themeSelect.appendChild(option);
  }
  $("#setAppearance").value = s.appearance || "dark";
  $("#setInputMode").value = s.input_mode || "auto";

  const family = $("#setFontFamily");
  if (!family.options.length) {
    for (const item of FONT_FAMILIES) {
      const option = document.createElement("option");
      option.value = item.id;
      option.textContent = item.label;
      family.appendChild(option);
    }
  }
  family.value = s.font_family || "system";

  for (const [slider, out, value] of [
    ["#setFontPanel", "#outFontPanel", s.font_panel || 13],
    ["#setFontTerminal", "#outFontTerminal", s.font_terminal || 13],
  ]) {
    $(slider).value = value;
    $(out).textContent = value + "px";
  }

  $("#setPalette").checked = s.palette_hotkey !== false;
  $("#setHistorySidebar").checked = s.history_in_sidebar !== false;
  $("#setHistoryDays").value = s.history_days || 14;
  $("#outHistoryDays").textContent = s.history_days || 14;
  $("#setStatusOnIcon").checked = !!s.status_on_icon;
  $("#setTabsMarkers").checked = s.markers_in_tabs !== false;
  $("#setSidebar").checked = s.markers_in_sidebar !== false;
  // Not repainted while focused — a poll landing mid-edit would move the
  // cursor in a field someone is pasting a URL into.
  $("#setClock24").value = s.clock_24h === false ? "12" : "24";
  for (const [id, key] of [["#setClockZone", "clock_zone"],
                           ["#setHookUrl", "webhook_url"],
                           ["#setPanelUrl", "panel_url"]]) {
    if (document.activeElement !== $(id)) $(id).value = s[key] || "";
  }
  /* The secret is never sent to the browser — only whether one exists.
   *
   * It used to come down with the rest of the settings, which meant any
   * read-only API token received it and could forge a signature the receiver
   * trusts. Now the field starts empty and says so, and an empty field on
   * blur means "leave it alone" rather than "erase it". Without that last
   * part, opening the settings pane would quietly clear the secret. */
  const secretBox = $("#setHookSecret");
  if (document.activeElement !== secretBox) {
    secretBox.value = "";
    secretBox.placeholder = s.webhook_secret_set ? "set — leave blank to keep" : "";
  }
  $("#setCliTint").checked = s.cli_tint !== false;
  $("#setArtShow").checked = s.artifacts_show !== false;
  // Not repainted while it has focus: this is a textarea someone types a list
  // into, and a poll landing mid-edit would move their cursor.
  if (document.activeElement !== $("#setArtDirs")) {
    $("#setArtDirs").value = (s.artifact_dirs || []).join("\n");
  }
  $("#setFlash").checked = s.notify_flash !== false;
  $("#setServices").checked = s.service_status !== false;
  $("#setSound").checked = !!s.notify_sound;
  $("#setIdle").value = s.notify_idle_seconds || 4;
  $("#outIdle").textContent = s.notify_idle_seconds || 4;

  $("#cssBoth").value = s.css_both || "";
  $("#cssPanel").value = s.css_panel || "";
  $("#cssTerminal").value = s.css_terminal || "";
  $("#aboutVersion").textContent = "version " + (state.version || "");
  /* Prefilled report links.
   *
   * The version and the browser are the two things every report needs and
   * nobody remembers to include, and they are both already here. Nothing is
   * sent from the app itself — the link opens GitHub with the fields filled
   * in, and the person decides what to do with it. A panel that phones home
   * about its own bugs is a panel nobody self-hosts. */
  const issue = "https://github.com/thejdubb02/clique/issues/new";
  $("#linkBug").href = issue + "?template=bug.yml&"
    + new URLSearchParams({ version: state.version || "",
                            browser: navigator.userAgent.slice(0, 200) });
  $("#linkFeature").href = issue + "?template=feature.yml";

  renderCliRows();
  renderSnippetRows();
  $("#settings").hidden = false;
}

function renderCliRows() {
  const rows = $("#cliRows");
  rows.innerHTML = "";
  // Installed first: the ones he actually runs should not sit below a list of
  // ones he does not have.
  const clis = [...state.clis].sort(
    (a, b) => (b.installed - a.installed) || a.label.localeCompare(b.label));

  for (const cli of clis) {
    const mode = markerMode(cli.id);
    const row = document.createElement("div");
    row.className = "cli-row" + (cli.installed ? "" : " absent");
    row.innerHTML =
      `<span class="preview">${markerFor({ ...cli, color: cliColor(cli.id, cli.color) },
                                          mode === "none" ? "both" : mode)}</span>` +
      `<span class="label">${escapeHtml(cli.label)}` +
      (cli.installed ? "" : ' <span class="tag">not installed</span>') + `</span>`;

    const select = document.createElement("select");
    for (const [value, text] of [["both", "Both"], ["icon", "Icon"],
                                 ["color", "Colour"], ["none", "None"]]) {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = text;
      option.selected = value === mode;
      select.appendChild(option);
    }
    select.onchange = async () => {
      await saveSettings({ marker_by_cli: { [cli.id]: select.value } });
      renderCliRows();
    };
    row.appendChild(select);

    /* The colour, editable. A palette that reads well on the built-in dark
     * theme can vanish on someone's Solarized, and that is not a reason to
     * make them live with it. Cleared, it goes back to what clis.toml ships —
     * which is why the reset is beside it rather than hidden in a menu. */
    const swatch = document.createElement("input");
    swatch.type = "color";
    swatch.className = "cli-swatch";
    swatch.value = cliColor(cli.id, cli.color);
    swatch.title = "Colour for " + cli.label;
    // On change, not input: a colour picker fires continuously while dragging,
    // and each one of those would be a write to the server.
    swatch.onchange = async () => {
      await saveSettings({ cli_colors: { [cli.id]: swatch.value } });
      renderCliRows();
    };
    row.appendChild(swatch);

    const reset = document.createElement("button");
    reset.type = "button";
    reset.className = "cli-reset";
    reset.textContent = "↺";
    reset.title = "Back to the shipped colour";
    reset.hidden = !(state.settings.cli_colors || {})[cli.id];
    reset.onclick = async () => {
      await saveSettings({ cli_colors: { [cli.id]: null } });
      renderCliRows();
    };
    row.appendChild(reset);

    rows.appendChild(row);
  }

  const absent = clis.filter((c) => !c.installed).length;
  $("#notInstalledNote").textContent = absent
    ? `${absent} catalogued CLIs are not installed here, so they are not offered `
      + `when starting a session. Install one and it appears by itself.`
    : "";
}

function renderSnippetRows() {
  const rows = $("#snippetRows");
  rows.innerHTML = "";
  const list = snippets();

  if (!list.length) {
    rows.innerHTML = '<p class="note">None yet. The point is the six prompts ' +
                     'you retype every day.</p>';
  }

  list.forEach((snippet, index) => {
    const row = document.createElement("div");
    row.className = "snippet-row";

    const trigger = document.createElement("input");
    trigger.className = "trigger";
    trigger.value = snippet.trigger;
    trigger.placeholder = ";rev";

    const label = document.createElement("input");
    label.className = "snip-label";
    label.value = snippet.label || "";
    label.placeholder = "What it is for";

    const text = document.createElement("textarea");
    text.rows = 2;
    text.value = snippet.text;
    text.placeholder = "The text this expands to";

    const remove = document.createElement("button");
    remove.className = "danger";
    remove.textContent = "Delete";
    remove.onclick = async () => {
      const next = snippets().filter((_, i) => i !== index);
      await saveSettings({ snippets: next });
      renderSnippetRows();
    };

    const commit = async () => {
      const next = snippets().map((existing, i) => i === index
        ? { trigger: trigger.value.trim(), label: label.value.trim(), text: text.value }
        : existing);
      // A row with no trigger or no text is dropped by the server, so an empty
      // one the user abandoned does not persist as a broken snippet.
      await saveSettings({ snippets: next });
    };
    for (const field of [trigger, label, text]) field.onchange = commit;

    row.append(trigger, label, text, remove);
    rows.appendChild(row);
  });
}

/* ------------------------------------------------------------------- wiring */

function wire() {
  $("#q").oninput = renderTree;
  $("#newTab").onclick = openModal;
  $("#settingsBtn").onclick = openSettings;
  $("#whatsNew").onclick = () => showChangelog(baseVersion(state.version));
  $("#settingsDone").onclick = () => ($("#settings").hidden = true);

  // Tabbed panes: siloed so the sheet stays readable as options grow.
  $("#setTabs").onclick = (ev) => {
    const button = ev.target.closest("button[data-pane]");
    if (!button) return;
    for (const other of $("#setTabs").children) other.classList.remove("on");
    button.classList.add("on");
    for (const pane of document.querySelectorAll(".pane")) {
      pane.hidden = pane.dataset.pane !== button.dataset.pane;
    }
    if (button.dataset.pane === "changelog") loadChangelog();
  };

  $("#setTheme").onchange = (ev) => saveSettings({ theme: ev.target.value });
  $("#setFontFamily").onchange = (ev) => saveSettings({ font_family: ev.target.value });
  $("#setAppearance").onchange = (ev) => saveSettings({ appearance: ev.target.value });
  $("#setInputMode").onchange = (ev) => saveSettings({ input_mode: ev.target.value });
  $("#setPalette").onchange = (ev) => saveSettings({ palette_hotkey: ev.target.checked });
  $("#setHistorySidebar").onchange = (ev) => {
    saveSettings({ history_in_sidebar: ev.target.checked }).then(renderTree);
  };
  $("#setHistoryDays").oninput = (ev) => {
    $("#outHistoryDays").textContent = ev.target.value;
  };
  $("#setHistoryDays").onchange = (ev) =>
    saveSettings({ history_days: Number(ev.target.value) }).then(renderTree);
  $("#setStatusOnIcon").onchange = (ev) => saveSettings({ status_on_icon: ev.target.checked });
  $("#setTabsMarkers").onchange = (ev) => saveSettings({ markers_in_tabs: ev.target.checked });
  $("#setSidebar").onchange = (ev) => saveSettings({ markers_in_sidebar: ev.target.checked });
  // On blur, not per keystroke: half a URL is not a setting, and the server
  // would store every prefix on the way to the real one.
  $("#setClock24").onchange = (ev) => {
    saveSettings({ clock_24h: ev.target.value === "24" });
  };
  $("#setClockZone").onblur = (ev) => saveSettings({ clock_zone: ev.target.value });
  // The browser already carries the zone database; offering it is one line and
  // saves anyone guessing whether it is Europe/Kyiv or Europe/Kiev.
  if (typeof Intl.supportedValuesOf === "function") {
    const list = $("#zoneList");
    for (const zone of Intl.supportedValuesOf("timeZone")) {
      const option = document.createElement("option");
      option.value = zone;
      list.append(option);
    }
  }
  $("#setHookUrl").onblur = (ev) => saveSettings({ webhook_url: ev.target.value });
  $("#setHookSecret").onblur = (ev) => {
    const typed = ev.target.value;
    // Blank means "unchanged", so there has to be another way to remove one.
    if (!typed) return;
    saveSettings({ webhook_secret: typed === "-" ? "" : typed });
  };
  $("#setPanelUrl").onblur = (ev) => saveSettings({ panel_url: ev.target.value });
  $("#testHook").onclick = async () => {
    try {
      await api("api/webhook/test", { method: "POST", body: "{}" });
      toast("Sent. If nothing arrives, the URL is the thing to check.");
    } catch (err) {
      toast("Could not send: " + err.message, true);
    }
  };
  $("#setCliTint").onchange = (ev) => saveSettings({ cli_tint: ev.target.checked });
  $("#setArtShow").onchange = (ev) => {
    saveSettings({ artifacts_show: ev.target.checked });
    pollArtifacts();
  };
  // On blur, not on every keystroke: a half-typed directory name is not a
  // setting, and the server would store each prefix on the way there.
  $("#setArtDirs").onblur = (ev) => {
    saveSettings({ artifact_dirs: ev.target.value.split("\n") });
    pollArtifacts();
  };
  $("#setFlash").onchange = (ev) => saveSettings({ notify_flash: ev.target.checked });
  $("#setServices").onchange = (ev) =>
    saveSettings({ service_status: ev.target.checked }).then(renderServices);
  $("#setSound").onchange = (ev) => saveSettings({ notify_sound: ev.target.checked });
  $("#testChime").onclick = chime;

  // Sliders paint live and save on release: saving per pixel would be a
  // request per frame for a value nobody reads until you stop dragging.
  for (const [slider, out, key, suffix] of [
    ["#setFontPanel", "#outFontPanel", "font_panel", "px"],
    ["#setFontTerminal", "#outFontTerminal", "font_terminal", "px"],
    ["#setIdle", "#outIdle", "notify_idle_seconds", ""],
  ]) {
    $(slider).oninput = (ev) => ($(out).textContent = ev.target.value + suffix);
    $(slider).onchange = (ev) => saveSettings({ [key]: Number(ev.target.value) });
  }

  $("#saveCss").onclick = async () => {
    await saveSettings({
      css_both: $("#cssBoth").value,
      css_panel: $("#cssPanel").value,
      css_terminal: $("#cssTerminal").value,
    });
    const note = $("#cssSaved");
    note.textContent = "Applied.";
    setTimeout(() => (note.textContent = ""), 2000);
  };

  $("#addSnippet").onclick = async () => {
    await saveSettings({
      snippets: [...snippets(), { trigger: ";new", label: "", text: "Replace me" }],
    });
    renderSnippetRows();
  };

  $("#fontMinus").onclick = () => bumpTermFont(-1);
  $("#fontPlus").onclick = () => bumpTermFont(1);
  $("#stats").onclick = showHistory;
  $("#cancel").onclick = () => ($("#modal").hidden = true);
  $("#collapse").onclick = () => setSidebar(false);
  $("#expand").onclick = () => setSidebar(true);
  $("#tabOverflow").onclick = (ev) => {
    ev.stopPropagation();
    const menu = $("#menu");
    if (!menu.hidden && menu.dataset.kind === "tab-overflow") {
      menu.hidden = true;
      return;
    }
    openOverflowMenu(ev);
  };

  $("#newFolder").onclick = newFolder;
  $("#adopt").onclick = adoptSessions;

  $("#newForm").onsubmit = async (ev) => {
    ev.preventDefault();
    const form = ev.target;
    try {
      const created = await api("api/sessions", {
        method: "POST",
        body: JSON.stringify({
          name: form.name.value, cli: form.cli.value,
          cwd: form.cwd.value, folder: form.folder.value || null,
        }),
      });
      $("#modal").hidden = true;
      form.reset();
      await refresh();
      openSession(created.id);
    } catch (err) {
      const box = $("#modalErr");
      box.textContent = err.message;
      box.hidden = false;
    }
  };

  $("#draftMove").innerHTML = icon("chevron-right");
  // The button is also the handle: dragging it onto a row is the gesture
  // people try first, and clicking it is the one that works on a phone.
  $("#draftMove").draggable = true;
  $("#draftMove").ondragstart = (ev) => {
    ev.dataTransfer.setData(DRAFT_TYPE, $("#prompt").value);
    ev.dataTransfer.effectAllowed = "move";
  };
  $("#draftMove").onclick = () => {
    if (!$("#prompt").value.trim()) return;
    pickSession("Move this draft to…", moveDraft);
  };
  $("#run").onclick = () => run($("#prompt").value);
  $("#runShell").onclick = () => runShell($("#prompt").value);
  $("#repPlus").onclick = () => setRepeat(repeat + 1);
  $("#repMinus").onclick = () => setRepeat(repeat - 1);

  $("#modePill").onclick = () => cycleMode(session(activeId), false);

  $("#prompt").onkeydown = (ev) => {
    // Escape leaves the box for the pane — scroll it, or type into the CLI's
    // own input. Stopped here so the global Escape does not also fire and start
    // closing menus that were not open.
    if (ev.key === "Escape") { ev.preventDefault(); ev.stopPropagation(); focusTerminal(); return; }
    if (ev.key === "Tab" && expandInBox(ev.target)) { ev.preventDefault(); return; }
    if (ev.key === "Enter" && !ev.shiftKey) { ev.preventDefault(); run(ev.target.value); }
  };
  $("#prompt").oninput = (ev) => {
    growPrompt(ev.target);
    saveDraft();
  };

  /* The laptop-lid case. A debounce that has not fired yet would otherwise be
   * lost exactly when someone most expects the box to still hold their words. */
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") {
      saveDraft(true);
      saveWorkspace(true);
    }
  });
  // Closing the window is not always a visibilitychange. pagehide is.
  addEventListener("pagehide", () => {
    saveDraft(true);
    saveWorkspace(true);
  });

  $("#palQ").oninput = renderPalette;
  $("#palQ").onkeydown = (ev) => {
    // Ctrl+N/P as well as the arrows: the people living in these panes all
    // day already have that in their fingers from readline.
    if (ev.key === "ArrowDown" || (ev.ctrlKey && ev.key === "n")) {
      ev.preventDefault(); movePalette(1);
    } else if (ev.key === "ArrowUp" || (ev.ctrlKey && ev.key === "p")) {
      ev.preventDefault(); movePalette(-1);
    } else if (ev.key === "Enter") {
      ev.preventDefault(); runPaletteItem(palAt);
    } else if (ev.key === "Escape") {
      ev.preventDefault(); closePalette();
    }
    // Whatever the palette does not use, the app must not act on either:
    // Ctrl+B while typing a query should type, not collapse the sidebar.
    ev.stopPropagation();
  };
  $("#palette").onclick = (ev) => {
    if (ev.target === $("#palette")) closePalette();   // the backdrop, not the box
  };

  $("#lock").onclick = toggleFollow;
  $("#keysBtn").onclick = showKeys;
  $("#fullScr").onclick = toggleFullscreen;
  $("#installApp").onclick = installApp;
  document.addEventListener("fullscreenchange", syncFullscreen);
  document.addEventListener("webkitfullscreenchange", syncFullscreen);
  addEventListener("beforeinstallprompt", (ev) => {
    ev.preventDefault();
    installPrompt = ev;
    paintInstall();
  });
  addEventListener("appinstalled", () => {
    installPrompt = null;
    paintInstall();
    toast("Installed — open it from your apps");
  });
  paintInstall();
  $("#keysClose").onclick = () => ($("#keys").hidden = true);
  $("#keys").onclick = (ev) => {
    if (ev.target === $("#keys")) $("#keys").hidden = true;   // the backdrop
  };
  $("#follow").onclick = () => setFollow(activeId, true);
  $("#copySel").onclick = () => copyPaneSelection();
  $("#terminal").addEventListener("contextmenu", (ev) => {
    // A canvas has no native copy. Right-click with a selection copies;
    // without one, leave the event so a browser menu can still appear.
    if (copyPaneSelection()) ev.preventDefault();
  });
  $("#artBtn").onclick = openArtifacts;
  $("#artClose").onclick = closeArtifacts;
  $("#artBack").onclick = showArtifactGrid;
  $("#art").onclick = (ev) => {
    if (ev.target === $("#art")) closeArtifacts();          // the backdrop
  };
  $("#fileClose").onclick = closeFileSheet;
  $("#file").onclick = (ev) => {
    if (ev.target === $("#file")) closeFileSheet();
  };
  $("#fileSend").onclick = () => {
    const path = filePathNow();
    closeFileSheet();
    if (path) toast("Path is in " + deliverPath(path));
  };
  $("#fileCopy").onclick = () => {
    const path = filePathNow();
    if (path) copyText(path).then(() => toast("Path copied"));
  };

  // Capture phase: xterm handles paste on its own textarea, so this has to see
  // the event first. It only claims the event when there is an image in it.
  document.addEventListener("paste", (ev) => {
    const data = ev.clipboardData;
    const items = data && data.items;
    if (!items || !items.length) return;
    const hasImage = [...items].some(
      (i) => i.kind === "file" && i.type.startsWith("image/"));
    if (!hasImage) return;          // plain text: not ours, let it through

    /* Text wins whenever both are on the clipboard.
     *
     * Copying from a browser, a spreadsheet, or most document editors puts a
     * rendered image on the clipboard *alongside* the text. Claiming the
     * event whenever an image is present meant those pastes silently became a
     * screenshot on disk and the text never arrived — which looks exactly
     * like paste being broken, because from where the person is standing it
     * is. A real screenshot carries no text, so this costs that case nothing.
     */
    if ((data.getData("text/plain") || "").trim()) return;

    ev.preventDefault();
    ev.stopPropagation();
    pasteImages(items);
  }, true);

  document.onclick = (ev) => {
    const menu = $("#menu");
    if (!menu.hidden && !menu.contains(ev.target)) menu.hidden = true;
  };
  // Clicks inside the menu must not count as "outside" after a rebuild
  // (the colour picker replaces the buttons that opened it).
  $("#menu").addEventListener("click", (ev) => ev.stopPropagation());

  // Capture: xterm's handler returning false does not preventDefault, so
  // the browser then "copies" from the empty helper textarea and wins the
  // race against our clipboard write. This has to see Ctrl+C first.
  document.addEventListener("keydown", (ev) => {
    const key = ev.key.toLowerCase();
    if (!(ev.ctrlKey || ev.metaKey) || ev.altKey) return;
    if (key !== "c") return;
    if (typingInAField(ev.target)) return;
    if (copyPaneSelection() || (ev.shiftKey && copyPaneVisible())) {
      ev.preventDefault();
      ev.stopPropagation();
    }
  }, true);

  document.onkeydown = (ev) => {
    const key = ev.key.toLowerCase();
    // Ctrl+Shift+P is always live; Ctrl+K can be handed back to the pane in
    // settings, where it means kill-to-end-of-line.
    if ((ev.ctrlKey || ev.metaKey) && ev.shiftKey && key === "p") {
      ev.preventDefault();
      openPalette(">");
      return;
    }
    if ((ev.ctrlKey || ev.metaKey) && !ev.shiftKey && key === "k" && paletteHotkeyOn()) {
      ev.preventDefault();
      openPalette("");
      return;
    }
    if ((ev.ctrlKey || ev.metaKey) && ev.shiftKey && key === "/") {
      ev.preventDefault();
      $("#keys").hidden ? showKeys() : ($("#keys").hidden = true);
      return;
    }
    if ((ev.ctrlKey || ev.metaKey) && ev.shiftKey && key === "l") {
      ev.preventDefault();
      toggleFollow();
      return;
    }
    if ((ev.ctrlKey || ev.metaKey) && ev.shiftKey && key === "f") {
      ev.preventDefault();
      toggleFullscreen();
      return;
    }
    if (ev.key === "F11") {
      ev.preventDefault();
      toggleFullscreen();
      return;
    }
    if ((ev.ctrlKey || ev.metaKey) && ev.key === "b") {
      ev.preventDefault();
      setSidebar($("#sidebar").hidden);
    }
    // Alt+1..9 rather than plain digits: the terminal owns the keyboard.
    if (ev.altKey && ev.key >= "1" && ev.key <= "9") {
      const target = openTabs[Number(ev.key) - 1];
      if (target) { ev.preventDefault(); openSession(target); }
    }
    if (ev.key === "Escape") {
      // Guarded: closing the palette hands focus back, and doing that when it
      // was never open would steal focus from wherever it actually is.
      if (!$("#palette").hidden) closePalette();
      $("#modal").hidden = true;
      $("#menu").hidden = true;
      $("#settings").hidden = true;
      $("#keys").hidden = true;
      closeFileSheet();
      // A full image goes back to the grid first; Escape twice leaves.
      if (!$("#art").hidden && !$("#artOne").hidden) showArtifactGrid();
      else closeArtifacts();
    }
  };

  addEventListener("resize", () => { packTabs(); refitAll(); });
  if (typeof ResizeObserver === "function") {
    const packOnSize = new ResizeObserver(() => packTabs());
    packOnSize.observe($("#tabs"));
    packOnSize.observe($("#tabbar"));
    // The pane's box moved — sidebar drag, font size, window chrome.
    // Measure again and, if this window is the one being looked at, take
    // the shared size back. Not on a timer: a second window that also
    // polled would fight this one.
    const paneBox = new ResizeObserver(() => {
      if (document.hidden) return;
      refitAll();
      reclaimSize(document.hasFocus());
    });
    const termwrap = $("#terminal");
    if (termwrap) paneBox.observe(termwrap);
  }
}

/* --------------------------------------------------------- command palette */

/* One box for every action, and the fastest way between sessions.
 *
 * The reason it exists is not that the buttons are hard to find. It is that
 * numbered tabs work at five sessions and stop working at thirty, and that
 * every feature added from here would otherwise arrive as one more button in
 * the chrome. A palette absorbs them: new commands cost a line each and no
 * pixels at all.
 *
 * Prefixes are borrowed from VS Code, because muscle memory is the point:
 * nothing searches everything, ">" narrows to commands, "@" to sessions.
 */

let palItems = [];
/* Set while the palette is being used to choose a session for something else
 * rather than to go somewhere. Cleared on close either way, so an abandoned
 * pick cannot leave the next Ctrl+K acting on a stale intention. */
let palPick = null;
const PALETTE_HINT = "Jump to a session, or run a command…";
let palAt = 0;
let palReturnTo = null;

/* Past conversations, fetched once and reused. Deliberately not part of the
 * three-second state poll: discovery is a few hundred file reads, and the
 * answer only changes when a conversation ends. */
let resumable = null;
let resumableAt = 0;

async function loadResumable(force) {
  if (!force && resumable && Date.now() - resumableAt < 60000) return resumable;
  try {
    resumable = await api("api/resumable");
    resumableAt = Date.now();
  } catch (err) {
    resumable = resumable || [];
  }
  return resumable;
}

/* Prompts you have already sent, for the palette's prompt search. Fetched the
 * first time it is asked for and cached a minute — the same deal resumable
 * gets, and off the three-second poll for the same reason. */
let promptHistory = null;
let promptHistoryAt = 0;

async function loadPrompts(force) {
  if (!force && promptHistory && Date.now() - promptHistoryAt < 60000) return promptHistory;
  try {
    promptHistory = await api("api/prompts");
    promptHistoryAt = Date.now();
  } catch (err) {
    promptHistory = promptHistory || [];
  }
  return promptHistory;
}

/* Start a session that picks a past conversation back up.
 *
 * The same endpoint that starts a new one — the registry decides what
 * resuming means for each CLI, and this knows nothing about any of them. */
async function resumeConversation(entry) {
  const created = await api("api/sessions", {
    method: "POST",
    body: JSON.stringify({
      name: entry.label.slice(0, 48) || entry.project,
      cli: entry.cli,
      cwd: entry.cwd,
      cli_session_id: entry.cli_session_id,
    }),
  });
  await refresh();
  openSession(created.id);
}

function resumeItems() {
  const clis = new Map((state.clis || []).map((c) => [c.id, c]));
  return (resumable || []).map((entry) => {
    const cli = clis.get(entry.cli);
    return {
      kind: "resume",
      title: entry.label || entry.project,
      detail: entry.project + " · " + ago(entry.updated),
      match: `${entry.label} ${entry.project} ${entry.cwd} ${cli ? cli.label : entry.cli}`,
      icon: cli ? markerFor({ color: cliColor(cli.id, cli.color), icon: cli.icon,
                              icon_full_color: cli.icon_full_color,
                              label: cli.label, cli: cli.id },
                             markerMode(cli.id) === "none" ? "color" : markerMode(cli.id)) : "",
      tag: "resume",
      run: () => resumeConversation(entry),
    };
  });
}

function promptItems() {
  const clis = new Map((state.clis || []).map((c) => [c.id, c]));
  return (promptHistory || []).map((entry) => {
    const cli = clis.get(entry.cli);
    const oneLine = entry.text.replace(/\s+/g, " ").trim();
    return {
      kind: "prompt",
      title: oneLine.length > 100 ? oneLine.slice(0, 100) + "\u2026" : oneLine,
      detail: entry.project + " \u00b7 " + ago(entry.when),
      match: `${entry.text} ${entry.project} ${cli ? cli.label : entry.cli}`,
      icon: cli ? markerFor({ color: cliColor(cli.id, cli.color), icon: cli.icon,
                              icon_full_color: cli.icon_full_color,
                              label: cli.label, cli: cli.id },
                             markerMode(cli.id) === "none" ? "color" : markerMode(cli.id)) : "",
      tag: "prompt",
      run: () => reusePrompt(entry),
    };
  });
}

/* One click on a past prompt drops it back where you would type it, to send or
 * edit — never sent for you. In panel mode that is the box; a terminal-mode CLI
 * owns its own input, so it goes into the pane without a newline for you to
 * read and send there. */
async function reusePrompt(entry) {
  if (promptWanted()) {
    const box = $("#prompt");
    box.value = entry.text;
    growPrompt(box);
    saveDraft(true);
    box.focus();
    try { box.setSelectionRange(box.value.length, box.value.length); } catch (err) { /* ok */ }
  } else if (activeId) {
    try {
      await api(`api/sessions/${activeId}/send`, {
        method: "POST", body: JSON.stringify({ text: entry.text, enter: false }),
      });
    } catch (err) { toast(String(err.message || err), true); return; }
    focusTerminal();
  }
}

/* "The one I was just in" is a fact about the work, not about this screen, so
 * it is kept on the server with the rest of the settings and is the same
 * answer on the desktop and on the phone. The sidebar width is the
 * counter-example — that one is genuinely about the screen and stays local.
 *
 * Stamped locally first so the palette reorders on the click rather than on
 * the next poll, and told to the server in the background: nothing here is
 * worth blocking a tab switch on, or worth an error if the write is lost. */
function markSeen(id) {
  const found = session(id);
  if (!found) return;
  found.last_seen = Date.now() / 1000;
  api(`api/sessions/${id}/seen`, { method: "POST", body: "{}" }).catch(() => {});
}

function paletteHotkeyOn() {
  return state.settings.palette_hotkey !== false;
}

/* Subsequence scoring, close enough to an editor's to feel familiar: every
 * character of the query must appear in order, and *where* it appears decides
 * the score. The start of a word beats the middle of one, and a run of
 * adjacent characters beats the same letters scattered about — which is what
 * makes "sent" find "wsg-sentinel" ahead of "session-tests". */
function fuzzy(query, text) {
  if (!query) return { score: 0, hits: [] };
  const lower = text.toLowerCase();
  const hits = [];
  let score = 0;
  let last = -2;
  let run = 0;
  for (const ch of query) {
    const found = lower.indexOf(ch, last + 1);
    if (found < 0) return null;
    run = found === last + 1 ? run + 1 : 0;
    const boundary = found === 0 || /[\s/\-_.:]/.test(text[found - 1]);
    score += 1 + run * 4 + (boundary ? 6 : 0) + (found === 0 ? 4 : 0);
    hits.push(found);
    last = found;
  }
  // Shorter haystacks win ties, so a query matching a short session name
  // outranks the same letters buried in a long path.
  return { score: score - text.length * 0.05, hits };
}

function highlight(text, hits) {
  if (!hits || !hits.length) return escapeHtml(text);
  const marks = new Set(hits);
  let out = "";
  for (let i = 0; i < text.length; i++) {
    out += marks.has(i) ? `<b>${escapeHtml(text[i])}</b>` : escapeHtml(text[i]);
  }
  return out;
}

/* Paths are shortened from the left, because the tail is the part that
 * identifies a directory and the head is the part every entry shares. */
function shortPath(path, keep = 46) {
  return path.length <= keep ? path : "…" + path.slice(-(keep - 1));
}

function paletteMarker(s) {
  // The palette always draws a marker, even where the sidebar and the tab bar
  // have theirs turned off: a column of bare names is the thing this box
  // exists to stop you scanning.
  const mode = markerMode(s.cli);
  return markerFor(
    { color: cliColor(s.cli, s.color), icon: s.icon, icon_full_color: s.icon_full_color,
      label: s.cli_label, cli: s.cli },
    mode === "none" ? "color" : mode);
}

function sessionTag(s) {
  if (s.archived) return "archived";
  if (!s.alive) return "stopped";
  return openTabs.includes(s.id) ? "open" : "running";
}

function paletteSessions() {
  // Most recently looked at first; never-opened sorts below everything that
  // has been, and archived below that again.
  return [...state.sessions]
    .sort((a, b) => (a.archived ? 1 : 0) - (b.archived ? 1 : 0) ||
                    (b.last_seen || 0) - (a.last_seen || 0))
    .map((s) => ({
      kind: "session",
      id: s.id,
      title: s.name,
      detail: [s.branch, shortPath(s.cwd)].filter(Boolean).join(" · "),
      match: s.name + " " + s.cwd + " " + (s.cli_label || "") + " " + (s.branch || ""),
      icon: paletteMarker(s),
      tag: sessionTag(s),
      run: () => openSession(s.id),
    }));
}

function paletteCommands() {
  const current = session(activeId);
  const items = [];
  const add = (title, detail, run, danger) =>
    items.push({ kind: "command", title, detail, match: title + " " + (detail || ""),
                 icon: "", run, danger });

  /* First among the commands when anything is blocked. "Which one needs me"
   * is the question people open the palette to answer, and making them read
   * past New Session and nine themes to reach the answer defeats it. */
  const blocked = nextUp();
  if (blocked.length) {
    add(blocked.length === 1 ? "Go to the one that needs you"
                             : `Go to what needs you first — ${blocked.length} blocked`,
        nextLine(blocked[0]),
        () => openSession(blocked[0].s.id));
  }

  add("New session", "Start a CLI in a directory", openModal);
  add("New folder", "Group sessions in the sidebar", newFolder);
  add("Adopt sessions", "Take over tmux sessions CLIque did not start", adoptSessions);
  add("Settings", "Themes, markers, snippets, notifications", openSettings);
  add("What's new", "This release, in Settings",
      () => showChangelog(baseVersion(state.version)));
  add("Toggle sidebar", "Ctrl+B", () => setSidebar($("#sidebar").hidden));
  add("Full screen", "The pane, not the browser. Ctrl+Shift+F", toggleFullscreen);
  if (installPrompt) {
    add("Install as an app", "Its own window — no tabs, no URL bar", installApp);
  }
  add("Keyboard shortcuts", "Every binding, in one list", showKeys);
  add("System history", "cpu and memory over the last hour", showHistory);
  add("Resume a past conversation", "Every transcript your CLIs have kept",
      () => openPalette("~"));
  add("Reuse a past prompt", "Search everything you have typed, drop it in the box",
      () => openPalette('"'));

  if (current) {
    add("Rename session", current.name, () => renameSession(current));
    add(current.archived ? "Unarchive session" : "Archive session",
        current.name + " — nothing is killed either way",
        () => setArchived(current, !current.archived));
    add("Copy working directory", current.cwd, () => copyText(current.cwd));
    add("Copy what's on screen", "The visible pane, not the scrollback",
        () => { copyPaneSelection() || copyPaneVisible(); });
    add("Copy the last 50 lines", "The recent output, scrollback and all — no dragging",
        () => copyPaneLast(50));
    add("Focus the terminal", current.name, focusTerminal);
    add(following(current.id) ? "Scroll lock — stop following output"
                              : "Follow output again",
        "Ctrl+Shift+L · scrolling up does it too", toggleFollow);
    add("Close tab", "The session keeps running in tmux", () => closeTab(current.id));
  }
  if (promptWanted()) {
    add("Focus the prompt box", "Type a prompt instead of driving the pane",
        () => $("#prompt").focus());
    if ($("#prompt").value.trim() && state.sessions.length > 1) {
      add("Move this draft to another session",
          "The half-typed thought goes with you, and is added under anything "
          + "already waiting there",
          () => pickSession("Move this draft to…", moveDraft));
    }
  }
  if (openTabs.length > 1) {
    add("Close every tab", `${openTabs.length} open · every session keeps running`,
        closeAllTabs);
  }

  for (const [id, theme] of Object.entries(window.CLIQUE_THEMES || {})) {
    add("Theme: " + theme.label,
        (state.settings.theme || "") === id ? "in use" : theme.base,
        () => saveSettings({ theme: id }));
  }
  for (const mode of ["dark", "light", "system"]) {
    add("Appearance: " + mode, "Used when no preset theme is chosen",
        () => saveSettings({ appearance: mode }));
  }
  for (const sn of snippets()) {
    add("Snippet: " + (sn.label || sn.trigger), sn.trigger + " — insert at the caret",
        () => insertSnippet(sn));
  }

  // Destructive last, and the only entry that gets the danger colour.
  if (current) {
    add(current.alive ? "Kill session" : "Delete session",
        current.alive ? "Stops the CLI. It stays in the folder."
                      : "Nothing is running — removes it from the sidebar",
        () => killSession(current), true);
  }
  return items;
}

function closeAllTabs() {
  for (const id of [...openTabs]) closeTab(id, true);
  activeId = null;
  renderTabs();
  renderTree();
}

function focusTerminal() {
  const entry = terms.get(activeId);
  if (entry) entry.term.focus();
}

/* Smart focus: after a switch, land in the prompt box so a new prompt is one
 * keystroke away, with Escape to hand focus back to the pane (see its keydown).
 *
 * Only when the box is the input surface — a terminal-mode CLI owns its own
 * input, so the pane keeps the focus there. And only on a pointer device: on a
 * phone, focusing a textarea throws the on-screen keyboard up on every switch,
 * so touch keeps whatever focus it had. showActivePane still handles the pane
 * on attach and reconnect; this only overrides it for a switch you asked for. */
function landFocus() {
  if (!activeId || !promptWanted()) return;
  if (!matchMedia("(hover: hover) and (pointer: fine)").matches) return;
  $("#prompt").focus();
}

function insertSnippet(sn) {
  const box = $("#prompt");
  const filled = expandText(sn.text, false);
  const caret = filled.indexOf("{cursor}");
  const body = filled.replace("{cursor}", "");
  const head = box.value.slice(0, box.selectionStart);
  const tail = box.value.slice(box.selectionEnd);
  box.value = head + body + tail;
  const at = head.length + (caret >= 0 ? caret : body.length);
  box.focus();
  box.setSelectionRange(at, at);
  box.dispatchEvent(new Event("input"));
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
  } catch (err) {
    // The clipboard API needs a secure context. Over the tailnet that is
    // https and it is there; on a bare LAN address it simply is not, so fall
    // back rather than fail without saying anything.
    const box = document.createElement("textarea");
    box.value = text;
    box.style.cssText = "position:fixed;top:-1000px;opacity:0";
    document.body.appendChild(box);
    box.select();
    document.execCommand("copy");
    box.remove();
  }
}

function paneSelection() {
  const entry = terms.get(activeId);
  if (!entry || !entry.term) return "";
  try {
    return entry.term.hasSelection() ? (entry.term.getSelection() || "") : "";
  } catch (err) {
    return "";
  }
}

function copyPaneSelection(keepHighlight) {
  const picked = tidyCopiedLink(paneSelection());
  if (!picked || !picked.trim()) return false;
  const entry = terms.get(activeId);
  copyText(picked).then(() => {
    toast("Copied " + plural(picked));
    if (!keepHighlight && entry && entry.term) {
      try { entry.term.clearSelection(); } catch (err) { /* disposed */ }
      renderCopyChip();
    }
  });
  return true;
}

function paneVisibleText() {
  const entry = terms.get(activeId);
  if (!entry || !entry.term) return "";
  const buf = entry.term.buffer.active;
  const lines = [];
  for (let i = 0; i < entry.term.rows; i++) {
    const line = buf.getLine(buf.viewportY + i);
    lines.push(line ? line.translateToString(true) : "");
  }
  return lines.join("\n").replace(/\s+$/, "");
}

/* The last N lines the pane holds, scrollback and all, ending at the cursor —
 * the "without dragging" half of copy. paneVisibleText is only the viewport;
 * this reaches past it to what just happened, wherever the view is parked. No
 * notion of a "reply": CLIque does not read the CLI's output, so a count of
 * lines is the honest unit. Trailing blank rows are dropped. */
function paneLastLines(n) {
  const entry = terms.get(activeId);
  if (!entry || !entry.term) return "";
  const buf = entry.term.buffer.active;
  const end = buf.baseY + buf.cursorY;      // the last row written to
  const lines = [];
  for (let y = end; y >= 0 && lines.length < n; y--) {
    const line = buf.getLine(y);
    lines.unshift(line ? line.translateToString(true) : "");
  }
  return lines.join("\n").replace(/\s+$/, "");
}

function copyPaneVisible() {
  const picked = paneVisibleText();
  if (!picked || !picked.trim()) return false;
  copyText(picked).then(() => toast("Copied the screen"));
  return true;
}

function copyPaneLast(n) {
  const picked = paneLastLines(n);
  if (!picked || !picked.trim()) { toast("Nothing there to copy yet"); return false; }
  copyText(picked).then(() => toast(`Copied the last ${picked.split("\n").length} lines`));
  return true;
}

function paneAtLiveScreen(term) {
  const buf = term && term.buffer && term.buffer.active;
  return !buf || buf.viewportY >= buf.baseY;
}

function paneSgrClick(col, row) {
  return "\x1b[<0;" + col + ";" + row + "M\x1b[<0;" + col + ";" + row + "m";
}

function paneGridCell(term, clientX, clientY) {
  const el = term.element;
  if (!el || !term.cols || !term.rows) return { col: 1, row: 1 };
  const r = el.getBoundingClientRect();
  const col = 1 + Math.max(0, Math.min(term.cols - 1,
    Math.floor((clientX - r.left) / (r.width / term.cols))));
  const row = 1 + Math.max(0, Math.min(term.rows - 1,
    Math.floor((clientY - r.top) / (r.height / term.rows))));
  return { col, row };
}

function sendPaneClick(term, clientX, clientY, sessionId) {
  const entry = terms.get(sessionId || activeId);
  if (!entry || !entry.ws || entry.ws.readyState !== 1) return;
  const at = paneGridCell(term, clientX, clientY);
  entry.ws.send(new TextEncoder().encode(paneSgrClick(at.col, at.row)));
}

function sessionOwnsInput(id) {
  const s = session(id || activeId);
  return Boolean(s && s.own_input);
}

/* Drag-select has to work even when the CLI has turned on mouse tracking.
 * xterm then sends every mousedown to the app and refuses to select unless
 * Shift is held (Option on a Mac, and only if we asked). Nobody holds a
 * modifier to copy a line. A drag is a selection; a click still goes through.
 * A phone is untouched: there is no hover, and the Copy chip is the way. */
const PANE_DRAG_PX = 5;

/* Zoom a boxed CLI instead of shrinking its grid. Below this, the text would
 * be smaller than a readable font, so a phone still resizes for real. */
const PANE_ZOOM_MIN = 0.45;
const PANE_OUTBOX_CAP = 8192;

function paneCellPx(term) {
  try {
    const cell = term._core._renderService.dimensions.css.cell;
    if (cell && cell.width && cell.height) return { w: cell.width, h: cell.height };
  } catch (err) { /* xterm internals moved */ }
  const size = (term && term.options && term.options.fontSize) || 13;
  return { w: size * 0.6, h: size * 1.2 };
}

function paneZoomScale(availW, availH, cols, rows, cellW, cellH) {
  if (availW < 2 || availH < 2 || cols < 1 || rows < 1 || cellW < 1 || cellH < 1) {
    return 1;
  }
  const needW = cols * cellW, needH = rows * cellH;
  if (needW <= availW && needH <= availH) return 1;
  return Math.min(availW / needW, availH / needH);
}

function paneShouldZoom(boxed, scale) {
  return Boolean(boxed) && scale < 1 && scale >= PANE_ZOOM_MIN;
}

function applyPaneZoom(term, scale) {
  const el = term && term.element;
  if (!el) return;
  if (!scale || scale >= 0.995) {
    el.style.transform = "";
    el.style.transformOrigin = "";
    return;
  }
  el.style.transformOrigin = "top left";
  el.style.transform = "scale(" + scale + ")";
}

function paneQueueOut(q, text, cap) {
  const next = (Array.isArray(q) ? q.slice() : []).concat([String(text)]);
  let n = 0;
  for (let i = 0; i < next.length; i++) n += next[i].length;
  const limit = cap || PANE_OUTBOX_CAP;
  while (next.length > 1 && n > limit) n -= next.shift().length;
  return next;
}

function paneSend(entry, text) {
  if (!entry || text == null || text === "") return;
  if (entry.ws && entry.ws.readyState === 1) {
    entry.ws.send(new TextEncoder().encode(text));
    return;
  }
  entry.outbox = paneQueueOut(entry.outbox, text, PANE_OUTBOX_CAP);
}

function paneFlushOut(entry) {
  const q = entry && entry.outbox;
  if (!q || !q.length) return;
  entry.outbox = [];
  if (!entry.ws || entry.ws.readyState !== 1) return;
  for (let i = 0; i < q.length; i++) {
    entry.ws.send(new TextEncoder().encode(q[i]));
  }
}

function paneForceSelectMods(platform) {
  const mac = /Mac|iPhone|iPod|iPad/.test(platform || "");
  return mac ? { altKey: true, shiftKey: false } : { shiftKey: true, altKey: false };
}

function paneDragFarEnough(x0, y0, x1, y1) {
  const dx = x1 - x0, dy = y1 - y0;
  return dx * dx + dy * dy >= PANE_DRAG_PX * PANE_DRAG_PX;
}

function paneShouldStealMouse(ev, mouseEventsOn, finePointer) {
  if (!finePointer || !mouseEventsOn) return false;
  if (!ev || ev.button !== 0) return false;
  if (ev.ctrlKey || ev.metaKey) return false;
  if (ev.shiftKey || ev.altKey) return false;
  if (ev.sourceCapabilities && ev.sourceCapabilities.firesTouchEvents) return false;
  return true;
}

function paneFinePointer() {
  // Headless browsers often report hover:none even when they send real
  // mouse events, so this keys off pointer, not hover.
  try {
    return !window.matchMedia("(pointer: coarse)").matches;
  } catch (err) {
    return true;
  }
}

function paneCellAt(term, clientX, clientY) {
  const el = term.element;
  if (!el || !term.cols || !term.rows) return { x: 0, y: 0 };
  const r = el.getBoundingClientRect();
  const col = Math.max(0, Math.min(term.cols - 1,
    Math.floor((clientX - r.left) / (r.width / term.cols))));
  const row = Math.max(0, Math.min(term.rows - 1,
    Math.floor((clientY - r.top) / (r.height / term.rows))));
  return { x: col, y: row + term.buffer.active.viewportY };
}

function paneSelectRange(term, from, to) {
  const a = from.y * term.cols + from.x;
  const b = to.y * term.cols + to.x;
  const start = a <= b ? from : to;
  const end = a <= b ? to : from;
  const len = Math.max(1, (end.y * term.cols + end.x) - (start.y * term.cols + start.x) + 1);
  try { term.select(start.x, start.y, len); } catch (err) { /* disposed */ }
}

function wirePaneClipboard(term, host, sessionId) {
  let replaying = false;
  const mouseOn = () => Boolean(term.element &&
    term.element.classList.contains("enable-mouse-events"));
  const play = (src, type) => {
    replaying = true;
    (term.element || src.target).dispatchEvent(new MouseEvent(type, {
      bubbles: true, cancelable: true, composed: true, view: window,
      clientX: src.clientX, clientY: src.clientY,
      screenX: src.screenX, screenY: src.screenY,
      button: src.button, buttons: type === "mouseup" ? 0 : 1,
      ctrlKey: src.ctrlKey, metaKey: src.metaKey,
      altKey: false, shiftKey: false, detail: src.detail || 1,
    }));
    replaying = false;
  };

  host.addEventListener("mousedown", (e) => {
    if (replaying) return;
    if (!paneShouldStealMouse(e, mouseOn(), paneFinePointer())) return;
    e.stopImmediatePropagation();
    e.preventDefault();
    const startX = e.clientX, startY = e.clientY;
    let mode = "pending";
    const swallowClick = (ev) => {
      ev.stopImmediatePropagation();
      ev.preventDefault();
      document.removeEventListener("click", swallowClick, true);
    };
    const paint = (x, y) => paneSelectRange(term, paneCellAt(term, startX, startY),
                                            paneCellAt(term, x, y));
    if (e.detail >= 2) {
      const at = paneCellAt(term, e.clientX, e.clientY);
      try { term.selectLines(at.y, at.y); } catch (err) { /* disposed */ }
      document.addEventListener("click", swallowClick, true);
      const done = () => {
        document.removeEventListener("mouseup", done, true);
        requestAnimationFrame(() => copyPaneSelection(true));
      };
      document.addEventListener("mouseup", done, true);
      return;
    }
    const onMove = (ev) => {
      if (mode === "pending") {
        if (!paneDragFarEnough(startX, startY, ev.clientX, ev.clientY)) return;
        mode = "select";
      }
      ev.stopImmediatePropagation();
      ev.preventDefault();
      paint(ev.clientX, ev.clientY);
    };
    const onUp = (ev) => {
      document.removeEventListener("mousemove", onMove, true);
      document.removeEventListener("mouseup", onUp, true);
      if (mode === "pending") {
        document.removeEventListener("click", swallowClick, true);
        play(e, "mousedown");
        play(ev, "mouseup");
        play(ev, "click");
      } else {
        requestAnimationFrame(() => copyPaneSelection(true));
      }
    };
    document.addEventListener("mousemove", onMove, true);
    document.addEventListener("mouseup", onUp, true);
    document.addEventListener("click", swallowClick, true);
  }, true);

  host.addEventListener("mouseup", () => {
    if (mouseOn()) return;
    requestAnimationFrame(() => copyPaneSelection(true));
  });

  host.addEventListener("click", (e) => {
    if (e.button !== 0 || e.detail !== 1) return;
    if (e.shiftKey || e.altKey || e.ctrlKey || e.metaKey) return;
    if (!sessionOwnsInput(sessionId)) return;
    if (term.hasSelection && term.hasSelection()) return;
    if (!paneAtLiveScreen(term)) return;
    if (e.target && e.target.closest && e.target.closest("a, button")) return;
    sendPaneClick(term, e.clientX, e.clientY, sessionId);
  });
}

function renderCopyChip() {
  const chip = $("#copySel");
  if (!chip) return;
  chip.hidden = !paneSelection();
}

function typingInAField(el) {
  if (!el || !el.tagName) return false;
  // xterm's hidden textarea is how the pane takes keys. Ctrl+C there is
  // ours, not a form field's.
  if (el.classList && el.classList.contains("xterm-helper-textarea")) return false;
  const tag = el.tagName;
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return true;
  return Boolean(el.isContentEditable);
}

function openPalette(prefill) {
  // Warm the history in the background. By the time anyone has typed enough
  // to want it, it is there; and if it never arrives, the palette still works.
  loadResumable().then(() => { if (!$("#palette").hidden) renderPalette(); });
  palReturnTo = document.activeElement;
  $("#modal").hidden = true;
  $("#menu").hidden = true;
  $("#palette").hidden = false;
  const input = $("#palQ");
  input.value = prefill || "";
  renderPalette();
  input.focus();
  input.select();
}

function closePalette() {
  $("#palette").hidden = true;
  palPick = null;
  $("#palQ").placeholder = PALETTE_HINT;
  // Give focus back to whatever had it, so opening the palette and changing
  // your mind costs nothing — including the terminal you were typing into.
  if (palReturnTo && document.contains(palReturnTo)) palReturnTo.focus();
  else focusTerminal();
  palReturnTo = null;
}

function renderPalette() {
  const raw = $("#palQ").value;
  // Picking a session is the one case where commands and past conversations
  // are noise: there is exactly one kind of answer that means anything.
  const mode = palPick ? "session"
             : raw[0] === ">" ? "command"
             : raw[0] === "@" ? "session"
             : raw[0] === '"' ? "prompt"
             : raw[0] === "~" ? "resume" : "all";
  // The prefix characters are only stripped when the user actually typed one.
  // In pick mode the list is already narrowed and there is no ">" or "@" in
  // front of what they typed, so slicing would eat their first keystroke.
  const query = (mode === "all" || palPick ? raw : raw.slice(1)).trim().toLowerCase();

  // Prompt history is fetched the first time it is asked for, then the palette
  // re-renders once it lands — the same lazy fetch resumable makes.
  if (mode === "prompt" && promptHistory === null) {
    loadPrompts().then(() => { if (!$("#palette").hidden) renderPalette(); });
  }

  let pool = [];
  if (mode !== "command" && mode !== "prompt") pool = pool.concat(paletteSessions());
  // Nothing is moved to where it already is.
  if (palPick) pool = pool.filter((item) => item.id !== activeId);
  if (mode !== "session" && mode !== "prompt") pool = pool.concat(paletteCommands());
  if (mode === "prompt") pool = pool.concat(promptItems());
  /* Hundreds of past conversations would drown the twenty things you actually
   * switch between, so they join the pool only once you have typed something
   * — or when you have asked for them by name with "~". */
  if (mode === "resume" || (mode === "all" && query)) pool = pool.concat(resumeItems());

  const shown = [];
  for (const item of pool) {
    if (!query) { shown.push({ ...item, hits: [], score: 0 }); continue; }
    const onTitle = fuzzy(query, item.title.toLowerCase());
    const onRest = fuzzy(query, item.match.toLowerCase());
    if (!onTitle && !onRest) continue;
    // A title match outranks one found in a path or a CLI name, and only the
    // title carries the highlight — marking letters inside a directory reads
    // as damage rather than as a match.
    const score = onTitle ? onTitle.score + 12 : onRest.score;
    shown.push({ ...item, score, hits: onTitle ? onTitle.hits : [] });
  }
  if (query) shown.sort((a, b) => b.score - a.score);

  palItems = shown.slice(0, 60);
  // With no query, the first row is the session you are already looking at,
  // so start one below it: Ctrl+K, Enter then means "back to the last one",
  // which is the move this shortcut is really for.
  palAt = (!query && palItems.length > 1 &&
           palItems[0].kind === "session" && palItems[0].id === activeId) ? 1 : 0;
  paintPalette();
}

function paintPalette() {
  const list = $("#palList");
  list.innerHTML = "";
  if (!palItems.length) {
    list.innerHTML = `<div class="pal-empty">Nothing matches.</div>`;
    return;
  }
  palItems.forEach((item, index) => {
    const row = document.createElement("div");
    row.className = "pal-row" + (item.danger ? " danger" : "");
    row.setAttribute("role", "option");
    row.innerHTML =
      `<span class="pal-icon">${item.icon || ""}</span>` +
      `<span class="pal-text">` +
        `<span class="pal-title">${highlight(item.title, item.hits)}</span>` +
        (item.detail ? `<span class="pal-detail">${escapeHtml(item.detail)}</span>` : "") +
      `</span>` +
      (item.tag ? `<span class="pal-tag ${item.tag}">${escapeHtml(item.tag)}</span>` : "");
    row.onmouseenter = () => { palAt = index; paintCursor(); };
    row.onclick = () => runPaletteItem(index);
    list.appendChild(row);
  });
  paintCursor();
}

/* Moving the highlight repaints two class names, not the list. Rebuilding the
 * DOM on every arrow key made a long list visibly stutter. */
function paintCursor() {
  const rows = $("#palList").children;
  for (let i = 0; i < rows.length; i++) {
    rows[i].classList.toggle("on", i === palAt);
    rows[i].setAttribute("aria-selected", i === palAt ? "true" : "false");
  }
  if (rows[palAt]) rows[palAt].scrollIntoView({ block: "nearest" });
}

function movePalette(delta) {
  if (!palItems.length) return;
  palAt = (palAt + delta + palItems.length) % palItems.length;
  paintCursor();
}

function runPaletteItem(index) {
  const item = palItems[index];
  if (!item) return;
  palReturnTo = null;      // the action decides where focus lands, not us
  $("#palette").hidden = true;
  if (palPick) {
    const take = palPick;
    palPick = null;
    return take(item);
  }
  item.run();
}

/* "Now pick a session" — the palette, borrowed.
 *
 * A second list widget for choosing a session would be a second thing to
 * search, sort, style and keep working on a phone. This is the same list, the
 * same typing, the same arrow keys, with the actions taken out and one
 * callback put in.
 *
 * `hint` is shown as the placeholder so it is obvious this is not the usual
 * palette; Escape leaves without choosing, as it does everywhere else. */
function pickSession(hint, onPick) {
  palPick = (item) => onPick(item.id);
  openPalette("");
  $("#palQ").placeholder = hint;
}

/* ------------------------------------------------------------ sidebar width */

const SIDEBAR_DEFAULT = 252;
const SIDEBAR_MIN = 170;      // below this the project path under a name is unreadable
const SIDEBAR_MAX = 560;

/* Width lives in localStorage, not in server settings, unlike everything else
 * in the settings sheet. That is deliberate: a 420px sidebar that suits a
 * desktop is wrong on a laptop and absurd on a phone, and this is the one
 * preference that is genuinely about the screen rather than about him. */
function storedSidebarWidth() {
  const saved = Number(localStorage.getItem("clique.sidebarWidth"));
  if (!saved || Number.isNaN(saved)) return SIDEBAR_DEFAULT;
  return Math.min(Math.max(saved, SIDEBAR_MIN), SIDEBAR_MAX);
}

function setSidebarWidth(px, persist) {
  const width = Math.min(Math.max(Math.round(px), SIDEBAR_MIN), SIDEBAR_MAX);
  document.documentElement.style.setProperty("--sidebar-w", width + "px");
  if (persist) localStorage.setItem("clique.sidebarWidth", String(width));
  return width;
}

function wireResizer() {
  const handle = $("#resizer");
  let frame = 0;

  handle.onpointerdown = (ev) => {
    ev.preventDefault();
    handle.setPointerCapture(ev.pointerId);
    handle.classList.add("dragging");
    document.body.classList.add("resizing");

    const move = (e) => {
      // The sidebar starts at the viewport edge, so its width is simply the
      // pointer's x. No offset bookkeeping, and it stays correct if the drag
      // starts a few pixels off the true edge.
      setSidebarWidth(e.clientX, false);
      // Reflowing a terminal costs real work, so do it once per frame rather
      // than once per pointer event — the two are not the same rate.
      if (!frame) {
        frame = requestAnimationFrame(() => {
          frame = 0;
          const entry = terms.get(activeId);
          if (entry) { try { entry.fit.fit(); } catch (err) { /* hidden */ } }
        });
      }
    };

    const up = (e) => {
      handle.releasePointerCapture(ev.pointerId);
      handle.classList.remove("dragging");
      document.body.classList.remove("resizing");
      handle.onpointermove = null;
      handle.onpointerup = null;
      setSidebarWidth(e.clientX, true);
      packTabs();
      refitAll();
    };

    handle.onpointermove = move;
    handle.onpointerup = up;
  };

  // Double-click the handle to go back to the default, the same way a window
  // manager treats a double-clicked edge.
  handle.ondblclick = () => { setSidebarWidth(SIDEBAR_DEFAULT, true); packTabs(); refitAll(); };

  // Keyboard, because a drag handle that only takes a mouse is not a control.
  handle.onkeydown = (ev) => {
    const step = ev.shiftKey ? 32 : 8;
    const current = parseInt(getComputedStyle(document.documentElement)
      .getPropertyValue("--sidebar-w"), 10) || SIDEBAR_DEFAULT;
    if (ev.key === "ArrowLeft") setSidebarWidth(current - step, true);
    else if (ev.key === "ArrowRight") setSidebarWidth(current + step, true);
    else return;
    ev.preventDefault();
    packTabs();
    refitAll();
  };
}

/* Take the pane's size back when something else has moved it.
 *
 * A tmux window has exactly one size, shared by every client attached to it.
 * So a second browser, or a phone picking the session up, resizes this one's
 * pane out from under it — and the result is not a subtle difference, it is a
 * screen of tmux's dot-fill where the missing columns are.
 *
 * Nothing noticed, because a client only ever spoke up when *its own*
 * terminal changed size. Being resized by someone else is exactly the case
 * that produces no local change and therefore no message. The poll already
 * knows what the window is; this compares it to what we are drawing and says
 * so when they differ.
 *
 * Only the tab in front, and only while this window is the one being looked
 * at. Growing forever left a full-screen CLI's prompt hanging off the bottom;
 * shrinking on a timer let a second window punch dots into this one.
 *
 * `force` is for the moment you come back to the tab. visibilitychange
 * fires before the document is focused (Brave and Chrome), so a hasFocus()
 * gate here used to swallow the reclaim and leave the dots until you
 * clicked the pane. */
function claimable(cols, rows) {
  // A collapsed or hidden tab measures as almost nothing. Sending that
  // as the shared window's size is how coming back left a sea of dots.
  // A real phone still clears this.
  return cols >= 20 && rows >= 8;
}

function reclaimSize(force) {
  if (document.hidden) return;
  if (!force && !document.hasFocus()) return;
  const entry = terms.get(activeId);
  const s = session(activeId);
  if (!entry || !s || !s.alive) return;
  if (!entry.ws || entry.ws.readyState !== 1) return;
  const cols = entry.term.cols;
  const rows = entry.term.rows;
  if (!claimable(cols, rows)) return;
  // Coming back always claims this window's size. The poll's idea of the
  // pane can be a few seconds behind a hidden reconnect, and skipping
  // because the numbers already matched is how a screen of dots lasted
  // until you clicked.
  if (!force && s.cols && s.rows && cols === s.cols && rows === s.rows) return;
  entry.ws.send(JSON.stringify({ type: "resize", cols, rows }));
}

function refitAll() {
  // Only the visible terminal can be measured; the rest refit when selected.
  const entry = terms.get(activeId);
  if (!entry) return;
  if (entry.term.hasSelection && entry.term.hasSelection()) return;
  layoutPane(entry);
}

function wakePane() {
  /* The tab is on screen again. Measure it, paint it, and take the shared
   * window's size back. Layout is often still the background-tab size on
   * the first tick, so the caller retries on a frame and a short timeout. */
  if (document.hidden) return;
  const entry = terms.get(activeId);
  if (entry) paintPane(entry);
  reclaimSize(true);
}

let installPrompt = null;

function isAppWindow() {
  return matchMedia("(display-mode: standalone)").matches
    || matchMedia("(display-mode: window-controls-overlay)").matches
    || matchMedia("(display-mode: fullscreen)").matches
    || Boolean(navigator.standalone);
}

function toggleFullscreen() {
  if (!document.fullscreenEnabled && !document.webkitFullscreenEnabled) {
    toast("This browser will not give up the window");
    return;
  }
  if (document.fullscreenElement || document.webkitFullscreenElement) {
    (document.exitFullscreen || document.webkitExitFullscreen).call(document);
    return;
  }
  const root = document.documentElement;
  const go = root.requestFullscreen || root.webkitRequestFullscreen;
  if (!go) {
    toast("This browser will not give up the window");
    return;
  }
  Promise.resolve(go.call(root)).catch(() => {
    toast("This browser will not give up the window");
  });
}

function syncFullscreen() {
  const on = Boolean(document.fullscreenElement || document.webkitFullscreenElement);
  document.body.classList.toggle("is-fs", on);
  const btn = $("#fullScr");
  if (btn) {
    btn.title = on ? "Leave full screen (Ctrl+Shift+F)" : "Full screen (Ctrl+Shift+F)";
  }
  scheduleWake();
}

function installApp() {
  if (!installPrompt) {
    if (isAppWindow()) toast("Already in its own window");
    else toast("Use the browser’s Install app / Add to Home Screen");
    return;
  }
  const pending = installPrompt;
  installPrompt = null;
  pending.prompt();
  pending.userChoice.then((choice) => {
    paintInstall();
    if (choice && choice.outcome === "accepted") toast("Installed — open it from your apps");
  }).catch(() => { paintInstall(); });
}

function paintInstall() {
  const btn = $("#installApp");
  const hint = $("#installHint");
  const own = isAppWindow();
  if (btn) btn.hidden = own || !installPrompt;
  if (hint) {
    hint.hidden = own;
    if (own) return;
    hint.textContent = installPrompt
      ? "Install as an app for a window of its own — no browser tabs, no URL bar."
      : "Install as an app from the browser menu (Install app, or on a phone Share → Add to Home Screen).";
  }
}

function setSidebar(show) {
  $("#sidebar").hidden = !show;
  $("#resizer").hidden = !show;
  $("#rail").hidden = show;
  localStorage.setItem("clique.sidebar", show ? "1" : "0");
  // Re-apply the stored width on the way back in, so collapsing and expanding
  // returns the sidebar you had rather than the default one.
  if (show) setSidebarWidth(storedSidebarWidth(), false);
  setTimeout(() => { packTabs(); refitAll(); }, 0);
}

/* Carry the browser's own state across the rename, once.
 *
 * Three keys, and losing them is not fatal — but reopening the panel to a
 * default sidebar and no tabs is exactly the moment a rename feels like
 * something broke rather than something was renamed. */
(function migrateLocalKeys() {
  if (localStorage.getItem("clique.migrated")) return;
  for (const key of ["sidebarWidth", "sidebar", "tabs"]) {
    const old = localStorage.getItem("muxpanel." + key);
    if (old !== null && localStorage.getItem("clique." + key) === null) {
      localStorage.setItem("clique." + key, old);
    }
    localStorage.removeItem("muxpanel." + key);
  }
  localStorage.removeItem("muxpanel.mru");   // superseded by the server's last_seen
  localStorage.setItem("clique.migrated", "1");
})();

/* Hover a row or tab, rest a beat, and the native tooltip carries the last
 * few lines the pane said — an answer without opening it. It pulls the peek
 * endpoint lazily: nothing captures a pane until a pointer stops on its row,
 * so a quiet sidebar of twenty still costs nothing, the same bargain the
 * attention ladder makes. No popup on purpose — the earlier one was dropped
 * for being a thing to summon and place, and the browser's own tooltip is
 * neither. Touch has no hover, so the long-press menu stays its way in. */
const PEEK_HOVER_MS = 350;          // a deliberate rest, not a pass-through
const PEEK_TTL_MS = 4000;           // a fetched peek is good for a few seconds
const peekTitles = new Map();       // id -> { at, title }
let peekTimer = null;
let peekRow = null;

async function peekInto(el) {
  const id = el.dataset.id;
  const s = session(id);
  if (!s || !s.alive) return;       // a dead pane has nothing to glance at
  const cached = peekTitles.get(id);
  if (!cached || Date.now() - cached.at >= PEEK_TTL_MS) {
    let data;
    try { data = await api(`api/sessions/${id}/peek?lines=8`); }
    catch { return; }               // a failed glance leaves the plain tooltip
    const lines = (data && data.lines) || [];
    if (!lines.length) return;
    const head = s.cwd || s.name;
    peekTitles.set(id, { at: Date.now(), title: `${head}\n\n${lines.join("\n")}` });
  }
  const entry = peekTitles.get(id);
  if (entry && peekRow === el) el.title = entry.title;   // still resting on it
}

/* Delegated, because both containers are rebuilt every poll: a listener per
 * row would be churn for nothing, the same reason the touch menus are. */
function wirePeekTooltips() {
  for (const sel of ["#tree", "#tabs"]) {
    const root = $(sel);
    if (!root) continue;
    root.addEventListener("mouseover", (ev) => {
      const el = ev.target.closest(".session, .tab");
      if (!el || el === peekRow) return;    // already resting on this one
      peekRow = el;
      clearTimeout(peekTimer);
      peekTimer = setTimeout(() => { if (peekRow === el) peekInto(el); }, PEEK_HOVER_MS);
    });
    root.addEventListener("mouseout", (ev) => {
      const el = ev.target.closest(".session, .tab");
      // Crossing between a row's own children is not leaving the row.
      if (el === peekRow && !el.contains(ev.relatedTarget)) {
        clearTimeout(peekTimer);
        peekRow = null;
      }
    });
  }
}

wire();
wireResizer();
wireTouchMenus();
wirePeekTooltips();
setSidebarWidth(storedSidebarWidth(), false);
setSidebar(localStorage.getItem("clique.sidebar") !== "0");
bootWorkspace();
setInterval(refresh, 3000);
// Slower than the sidebar poll on purpose: this one touches a filesystem, and
// nobody is waiting on a screenshot to the second.
setInterval(pollArtifacts, ART_POLL_MS);
// The clock, only while the pane it lives on is actually showing. A minute is
// the resolution it displays, so a minute is what it costs.
setInterval(() => { if (!activeId && !document.hidden) renderClock(); }, 20000);
// Coming back to the tab should not mean waiting out the interval.
// Focus often lands a tick after visibilitychange; layout a tick after
// that. One reclaim on the event is not enough — the dots in the
// screenshot were exactly "the event fired, hasFocus was still false."
function scheduleWake() {
  // Layout after a long background tab can land after the first paint,
  // and Brave reports hasFocus=false on the visibility event itself.
  // One reclaim is not enough; the dots in the screenshot were exactly
  // "the event fired, the box was still the background-tab size."
  wakePane();
  requestAnimationFrame(wakePane);
  setTimeout(wakePane, 150);
  setTimeout(wakePane, 400);
  setTimeout(wakePane, 1000);
}

addEventListener("visibilitychange", () => {
  if (!document.hidden) {
    pollArtifacts();
    scheduleWake();
  }
});
addEventListener("focus", scheduleWake);
addEventListener("pageshow", scheduleWake);
document.addEventListener("focusin", () => reclaimSize(true));

/* A service worker that fetches and does not cache. Chromium will not
 * offer "Install app" without one, and a cache would serve yesterday's
 * panel on a project that ships ten times a day. */
if (navigator.serviceWorker) {
  navigator.serviceWorker.register("sw.js").catch(() => { /* install stays a browser menu */ });
}
