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
let activeOnly = (() => { try { return localStorage.getItem("clique.activeOnly") === "1"; }
                          catch (e) { return false; } })();  // show only running sessions
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

/* Sessions held read-only for review: the prompt, the mobile keys, and live
 * terminal typing are all withheld, so reading a pane — scrolling its output,
 * resting a thumb on the glass — cannot accidentally send anything into it.
 * Browser-local and per-session, remembered across reloads. */
let reviewLocked = new Set((() => {
  try {
    const v = JSON.parse(localStorage.getItem("clique.reviewLocked") || "[]");
    return Array.isArray(v) ? v.filter((x) => typeof x === "string") : [];
  } catch (e) { return []; }
})());
function reviewLockedOf(id) { return !!id && reviewLocked.has(id); }
function persistReviewLocked() {
  try { localStorage.setItem("clique.reviewLocked", JSON.stringify([...reviewLocked])); }
  catch (e) { /* private mode: the lock still holds for this window */ }
}
function toggleReviewLock(id) {
  id = id || activeId;
  if (!id) return;
  if (reviewLocked.has(id)) reviewLocked.delete(id);
  else reviewLocked.add(id);
  persistReviewLocked();
  renderInputBar();
  renderTabs();
  if (id === activeId && !reviewLocked.has(id)) $("#prompt").focus();
}
let _lockHintAt = 0;
function hintReviewLocked() {
  const now = Date.now();
  if (now - _lockHintAt < 3000) return;   // one nudge, not one per keystroke
  _lockHintAt = now;
  toast("Locked for review — unlock to type", false,
        { label: "Unlock", run: () => toggleReviewLock(activeId) });
}

/* A confirm before a command that looks destructive is sent. The patterns are
 * plain, case-insensitive substrings from settings — never a regex to mis-write
 * or a shell to guess at — and this only ever returns which one matched; the
 * decision is the caller's. */
function destructiveHit(text) {
  if (!state.settings || state.settings.confirm_destructive === false) return "";
  const hay = String(text || "").toLowerCase();
  for (const p of (state.settings.destructive_patterns || [])) {
    const needle = String(p || "").toLowerCase().trim();
    if (needle && hay.includes(needle)) return p;
  }
  return "";
}

/* A promise-based confirm sheet: resolves true on the primary button, false on
 * cancel, the X, the backdrop, or Escape — so the safe answer is the easy one.
 * There is deliberately no Enter-to-confirm: this exists to make a reflex pause,
 * and a muscle-memory Enter that confirmed it would defeat the whole point. */
let _confirmResolve = null;
let _confirmKeyHandler = null;
function confirmAction({ title, message, detail = "", okLabel = "Confirm", danger = false }) {
  return new Promise((resolve) => {
    if (_confirmResolve) closeConfirm(false);   // never strand an earlier one
    _confirmResolve = resolve;
    $("#confirmTitle").textContent = title || "Are you sure?";
    $("#confirmMsg").textContent = message || "";
    const det = $("#confirmDetail");
    det.hidden = !detail;
    det.textContent = detail || "";
    const ok = $("#confirmOk");
    ok.textContent = okLabel;
    ok.classList.toggle("danger", !!danger);
    $("#confirmSheet").hidden = false;
    $("#confirmNo").focus();   // cancel is where the keyboard lands
    _confirmKeyHandler = (e) => {
      if (e.key !== "Escape") return;
      e.preventDefault(); e.stopPropagation(); closeConfirm(false);
    };
    document.addEventListener("keydown", _confirmKeyHandler, true);
  });
}
function closeConfirm(result) {
  if ($("#confirmSheet").hidden) return;
  $("#confirmSheet").hidden = true;
  if (_confirmKeyHandler) {
    document.removeEventListener("keydown", _confirmKeyHandler, true);
    _confirmKeyHandler = null;
  }
  const r = _confirmResolve; _confirmResolve = null;
  if (r) r(!!result);
}

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
  paintCliMark(s, colour);
}

/* The active CLI's logo, watermarked into the top-right of the pane.
 *
 * The pane edge already carries the CLI's colour, which answers "which one am
 * I in" for anyone who has learned the colours. This answers it for everyone
 * else, and at a glance across a screen of panes rather than by reading a tab.
 * Opposite corner to the theme character on purpose, so a theme with one and
 * a session with the other do not sit on top of each other.
 *
 * Two shapes of icon, the same split `markerFor` makes for the sidebar. A
 * single-colour glyph becomes a mask tinted with the CLI's own colour, which
 * is what lets one file serve every mode. A logo that carries its own colours
 * cannot be a mask - it flattens to a solid square - so it is drawn as the
 * image it is. A CLI with no icon at all draws nothing here rather than
 * inventing a letter: a watermark is decoration, and a letter blown up to
 * a hundred pixels in the corner reads as a mistake. */
function paintCliMark(active, colour) {
  const el = $("#cliMark");
  if (!el) return;
  const s = active || session(activeId);
  const cli = s && (state.clis || []).find((c) => c.id === s.cli);
  const icon = cli && cli.icon;
  const show = state.settings.cli_watermark !== false && Boolean(icon);
  el.hidden = !show;
  if (!show) return;
  const url = `url("icons/${encodeURIComponent(icon)}")`;
  const full = Boolean(cli.icon_full_color);
  el.classList.toggle("is-image", full);
  if (full) {
    el.style.backgroundImage = url;
    el.style.webkitMaskImage = "";
    el.style.maskImage = "";
    el.style.background = "";
    el.style.backgroundImage = url;
  } else {
    el.style.backgroundImage = "";
    el.style.webkitMaskImage = url;
    el.style.maskImage = url;
    el.style.background = colour || "var(--dim)";
  }
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
    // Mirror this window's own strip locally, keyed by its id, so two open
    // windows keep separate tabs across a reload (the server copy is one shared
    // workspace and would otherwise make them converge).
    try {
      localStorage.setItem("clique.ws." + winId, JSON.stringify(
        { tabs: body.open_tabs, active: body.active_tab, ts: Date.now() }));
    } catch (err) { /* storage full or blocked: the server copy still saves */ }
    // Only the primary window (the earliest-opened, or the only one) writes the
    // SHARED server workspace — the seed a fresh single window restores from — so
    // a clean second window never overwrites it with its own empty strip.
    if (winPeers.size > 1 && windowLabel(winId) !== 1) return;
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

/* --------------------------------------------------- other browser windows */

/* Two windows, one per screen, is a common way to drive this — a build on one
 * monitor, a diff on the other. Same-origin windows talk directly over a
 * BroadcastChannel: no server, nothing over the wire. They find each other and
 * a session can be handed from one to another. Cross-*device* (phone and
 * desktop) is a different problem — separate browsers cannot share a channel —
 * and would need a server relay; this is the same-browser case, which is what
 * "another window" usually means. */

let winId = "";
try { winId = sessionStorage.getItem("clique.win") || ""; } catch (err) { winId = ""; }
if (!winId) {
  winId = (self.crypto && self.crypto.randomUUID)
    ? crypto.randomUUID() : "w" + Math.random().toString(36).slice(2);
  try { sessionStorage.setItem("clique.win", winId); } catch (err) { /* private mode */ }
}
const winJoined = Date.now();
const winPeers = new Map([[winId, { joined: winJoined, seen: winJoined }]]);
let winBus = null;
try { winBus = new BroadcastChannel("clique.windows"); } catch (err) { winBus = null; }

// Every known window oldest-first, so each window computes the SAME numbering
// from the shared join times — the earliest-opened is "1". Ties break on id.
function windowsInOrder() {
  return [...winPeers.entries()]
    .sort((a, b) => a[1].joined - b[1].joined || (a[0] < b[0] ? -1 : 1))
    .map(([id]) => id);
}
function windowLabel(id) { return windowsInOrder().indexOf(id) + 1; }
function otherWindows() { return windowsInOrder().filter((id) => id !== winId); }

function renderWinTag() {
  const tag = $("#winTag");
  if (!tag) return;
  const n = winPeers.size;
  tag.hidden = n < 2;
  if (n >= 2) {
    tag.textContent = "\u29c9 " + windowLabel(winId);
    tag.title = `This is window ${windowLabel(winId)} of ${n}. Open a session's `
      + `menu (its gear, or right-click) to move it to another window.`;
  }
}

let winFlashTimer = null;
function flashWindow() {
  document.body.classList.add("win-flash");
  clearTimeout(winFlashTimer);
  winFlashTimer = setTimeout(() => document.body.classList.remove("win-flash"), 1300);
}

function winSend(msg) {
  try { if (winBus) winBus.postMessage(msg); } catch (err) { /* channel gone */ }
}
function winSee(id, joined) {
  const now = Date.now();
  const cur = winPeers.get(id);
  if (cur) { cur.seen = now; if (joined) cur.joined = joined; }
  else { winPeers.set(id, { joined: joined || now, seen: now }); }
}

function windowMoveItems(s) {
  const others = otherWindows();
  if (!others.length) return [];
  if (others.length === 1) {
    return [["Move to the other window", () => moveSessionToWindow(s.id, others[0])]];
  }
  return others.map((w) => ["Move to window " + windowLabel(w), () => moveSessionToWindow(s.id, w)]);
}

function moveSessionToWindow(id, targetId) {
  const s = session(id);
  if (!s) return;
  winSend({ t: "open", win: targetId, session: id });
  if (openTabs.includes(id)) {
    const wasActive = activeId === id;
    closeTab(id, true);   // silent: a move, not a "still running" close
    if (wasActive && activeId) {
      if (terms.has(activeId)) selectTab(activeId); else openSession(activeId);
    } else { renderTabs(); renderTree(); }
  }
  toast(`Moved ${s.name || "session"} to window ${windowLabel(targetId)}`);
}

// A window's remembered strip is kept under its own id; drop the ones whose
// window has not been seen in days so the keys do not pile up forever.
function pruneWinWorkspaces() {
  try {
    const cutoff = Date.now() - 2 * 864e5;
    for (const key of Object.keys(localStorage)) {
      if (!key.startsWith("clique.ws.")) continue;
      let stale = true;
      try { stale = ((JSON.parse(localStorage.getItem(key)) || {}).ts || 0) < cutoff; }
      catch (err) { stale = true; }
      if (stale) localStorage.removeItem(key);
    }
  } catch (err) { /* storage blocked */ }
}

// Closing a window hands its open tabs to the remaining one, so a window's work
// is collected rather than dropped. Exactly one window adopts — the primary
// (label 1 of those still open) — reading the gone window's own remembered strip
// from localStorage. Scheduled behind a short grace so a *reload* (the same
// window back in a moment) is not mistaken for a close.
const winCollectTimers = new Map();
function scheduleCollect(id) {
  if (winCollectTimers.has(id)) return;
  winCollectTimers.set(id, setTimeout(() => {
    winCollectTimers.delete(id);
    collectFromWindow(id);
  }, 2500));
}
function collectFromWindow(id) {
  if (winPeers.has(id)) return;          // it came back: a reload, not a close
  if (windowLabel(winId) !== 1) return;  // one window adopts, the primary
  let saved = null;
  try { saved = JSON.parse(localStorage.getItem("clique.ws." + id) || "null"); }
  catch (err) { saved = null; }
  try { localStorage.removeItem("clique.ws." + id); } catch (err) { /* fine */ }
  const tabs = (saved && Array.isArray(saved.tabs) ? saved.tabs : [])
    .filter((tid) => !openTabs.includes(tid) && session(tid));
  if (!tabs.length) return;
  for (const tid of tabs) openSession(tid);
  flashWindow();
  toast(`Collected ${tabs.length} tab${tabs.length === 1 ? "" : "s"} from a closed window`);
}

if (winBus) {
  winBus.onmessage = (ev) => {
    const m = ev.data || {};
    if (m.t === "open") {
      if (m.win === winId && m.session && session(m.session)) {
        openSession(m.session);
        flashWindow();
        toast("A session was handed to this window");
      }
      return;
    }
    if (!m.id || m.id === winId) return;
    if (m.t === "join") { winSee(m.id, m.joined); winSend({ t: "ping", id: winId, joined: winJoined }); }
    else if (m.t === "ping") { winSee(m.id, m.joined); }
    else if (m.t === "leave") { winPeers.delete(m.id); scheduleCollect(m.id); }
    renderWinTag();
  };
  winSend({ t: "join", id: winId, joined: winJoined });
  setInterval(() => {
    const now = Date.now();
    for (const [id, p] of [...winPeers]) {
      if (id !== winId && now - p.seen > 12000) { winPeers.delete(id); scheduleCollect(id); }
    }
    winSend({ t: "ping", id: winId, joined: winJoined });
    renderWinTag();
  }, 3000);
  addEventListener("pagehide", () => winSend({ t: "leave", id: winId }));
  pruneWinWorkspaces();
  renderWinTag();
}


/* ------------------------------------------------------------------ the board */

/* Every session as a card, in a column for what it is doing. The columns fill
 * from the same authoritative state the sidebar ring uses, so a card moves the
 * instant a session's state does — while the board is open, the poll re-renders
 * it. A scannable whole-fleet view the one-per-line sidebar is not. */
const BOARD_COLUMNS = [
  { key: "working", label: "Working", states: ["working"] },
  { key: "waiting", label: "Needs you", states: ["asking", "error", "unseen"] },
  { key: "idle", label: "Idle", states: ["idle"] },
  { key: "stopped", label: "Stopped", states: ["stopped"] },
];

function openBoard() {
  $("#board").hidden = false;   // unhide first — renderBoard no-ops while hidden
  renderBoard();
}

function closeBoard() {
  $("#board").hidden = true;
}

function renderBoard() {
  if ($("#board").hidden) return;   // only pay for it while it is on screen
  const host = $("#boardCols");
  host.textContent = "";
  const byState = {};
  for (const s of state.sessions) {
    const w = workState(s);
    (byState[w] || (byState[w] = [])).push(s);
  }
  for (const col of BOARD_COLUMNS) {
    const cards = col.states.flatMap((st) => byState[st] || []);
    const column = document.createElement("div");
    column.className = "board-col";
    const head = document.createElement("div");
    head.className = "board-col-head";
    head.textContent = `${col.label} · ${cards.length}`;
    column.appendChild(head);
    const list = document.createElement("div");
    list.className = "board-col-cards";
    for (const s of cards) list.appendChild(boardCard(s));
    column.appendChild(list);
    host.appendChild(column);
  }
}

function boardCard(s) {
  const w = workState(s);
  const card = document.createElement("button");
  card.className = "board-card work-" + w;
  card.onclick = () => { closeBoard(); openSession(s.id); };
  const top = document.createElement("div");
  top.className = "board-card-top";
  const dot = document.createElement("span");
  dot.className = "dot";
  dot.dataset.work = w;
  const name = document.createElement("span");
  name.className = "board-card-name";
  name.textContent = s.name || s.id;
  top.append(dot, name);
  card.appendChild(top);
  if (s.saying) {
    const sub = document.createElement("div");
    sub.className = "board-card-sub";
    sub.textContent = s.saying;
    card.appendChild(sub);
  }
  return card;
}

/* -------------------------------------------------------------- broadcast */

/* One message to every live session at once — the clearest expression of "one
 * cockpit driving many". Scoped to a folder or the lot; empty text sends a bare
 * Enter to everyone (a "carry on"). The count is shown before you commit, so a
 * broadcast is never a surprise. */
function broadcastChecks() {
  return [...document.querySelectorAll("#broadcastList .bc-item-cb")];
}

function broadcastSelectedIds() {
  return broadcastChecks().filter((cb) => cb.checked).map((cb) => cb.value);
}

// A folder header is on when all its sessions are, indeterminate when only some.
function syncBroadcastGroup(group) {
  const gcb = group.querySelector(".bc-group-cb");
  const items = [...group.querySelectorAll(".bc-item-cb")];
  const on = items.filter((cb) => cb.checked).length;
  gcb.checked = on === items.length && on > 0;
  gcb.indeterminate = on > 0 && on < items.length;
}

function syncBroadcastMaster() {
  const all = broadcastChecks();
  const on = all.filter((cb) => cb.checked).length;
  const master = $("#broadcastAll");
  master.checked = on === all.length && on > 0;
  master.indeterminate = on > 0 && on < all.length;
}

function updateBroadcastCount() {
  const total = broadcastChecks().length;
  const n = broadcastSelectedIds().length;
  $("#broadcastCount").textContent =
    total === 0 ? "no live sessions" : `${n} of ${total} selected`;
  const send = $("#broadcastSend");
  send.disabled = n === 0;
  send.textContent = n > 0 && n === total ? "Send to all" : `Send to ${n}`;
}

// A checklist of the live sessions, grouped by folder. Tick all of them, a whole
// folder, or any handful — the count and the folder headers track the selection.
function renderBroadcastTargets() {
  const host = $("#broadcastList");
  host.textContent = "";
  const live = state.sessions.filter((s) => s.alive);
  const folders = (state.folders || []).filter((f) => f.id.startsWith("f-"));
  const groups = [];
  for (const f of folders) {
    const items = live.filter((s) => s.folder === f.id);
    if (items.length) groups.push({ name: f.name, items });
  }
  const loose = live.filter((s) => !folders.some((f) => f.id === s.folder));
  if (loose.length) groups.push({ name: "Ungrouped", items: loose });

  for (const g of groups) {
    const group = document.createElement("div");
    group.className = "bc-group";
    const head = document.createElement("label");
    head.className = "bc-group-head";
    const gcb = document.createElement("input");
    gcb.type = "checkbox";
    gcb.className = "bc-group-cb";
    gcb.onchange = () => {
      for (const cb of group.querySelectorAll(".bc-item-cb")) cb.checked = gcb.checked;
      syncBroadcastMaster();
      updateBroadcastCount();
    };
    const gname = document.createElement("span");
    gname.textContent = g.name;
    head.append(gcb, gname);
    group.appendChild(head);
    for (const s of g.items) {
      const row = document.createElement("label");
      row.className = "bc-item";
      const cb = document.createElement("input");
      cb.type = "checkbox";
      cb.className = "bc-item-cb";
      cb.value = s.id;
      cb.checked = true;
      cb.onchange = () => {
        syncBroadcastGroup(group);
        syncBroadcastMaster();
        updateBroadcastCount();
      };
      const nm = document.createElement("span");
      nm.className = "bc-item-name";
      nm.textContent = s.name || s.id;
      row.append(cb, nm);
      group.appendChild(row);
    }
    syncBroadcastGroup(group);
    host.appendChild(group);
  }
}

function openBroadcast() {
  renderBroadcastTargets();
  syncBroadcastMaster();
  updateBroadcastCount();
  $("#broadcast").hidden = false;
  $("#broadcastText").focus();
}

function closeBroadcast() {
  $("#broadcast").hidden = true;
}

async function sendBroadcast() {
  const ids = broadcastSelectedIds();
  if (!ids.length) return;
  const text = $("#broadcastText").value;
  // The blast radius makes the guard matter most here — one command into many.
  if (!await okToSend(text, `${ids.length} session${ids.length === 1 ? "" : "s"}`)) return;
  const body = { ids, text, enter: true };
  try {
    const r = await api("api/broadcast", { method: "POST", body: JSON.stringify(body) });
    const n = r && r.count;
    toast(`Sent to ${n} session${n === 1 ? "" : "s"}`);
    $("#broadcastText").value = "";
    closeBroadcast();
    setTimeout(refresh, 400);
  } catch (err) {
    toast("Could not broadcast — " + (err.message || err), true);
  }
}

/* ----------------------------------------------------------------- the inbox */

/* Who needs you, and a way to answer without leaving the phone.
 *
 * "Needs you" is the authoritative signal (a session asking a question or
 * stopped on an error) plus the finished-and-unopened case. workState already
 * folds those into asking / error / unseen, so this is a filter over it, not a
 * second opinion. Most urgent first: a question or an error outranks one that
 * finished quietly, and within a tier the most recently active is on top. */
function needsYou(s) {
  const w = workState(s);
  return w === "asking" || w === "error" || w === "unseen";
}

const INBOX_RANK = { asking: 0, error: 0, unseen: 1 };

function inboxItems() {
  return state.sessions
    .filter(needsYou)
    .sort((a, b) =>
      (INBOX_RANK[workState(a)] - INBOX_RANK[workState(b)])
      || (b.activity || 0) - (a.activity || 0));
}

/* The tab title carries the count, so a backgrounded tab says it at a glance —
 * the "(3)" an unread mail tab uses, which every browser already renders. */
function updateTitle() {
  const n = state.sessions.filter(needsYou).length;
  document.title = n ? `(${n}) CLIque` : "CLIque";
}

function renderInbox() {
  const items = inboxItems();
  const badge = $("#inboxCount");
  badge.textContent = String(items.length);
  badge.hidden = items.length === 0;
  $("#inboxBtn").classList.toggle("lit", items.length > 0);
  // Refresh the open sheet in place — but never while a reply is being typed
  // into it, which re-rendering would wipe.
  const typing = document.activeElement
    && document.activeElement.classList.contains("inbox-reply");
  if (!$("#inbox").hidden && !typing) fillInbox(items);
}

function openInbox() {
  fillInbox(inboxItems());
  $("#inbox").hidden = false;
}

function closeInbox() {
  $("#inbox").hidden = true;
}

function fillInbox(items) {
  const list = $("#inboxList");
  list.textContent = "";
  $("#inboxEmpty").hidden = items.length > 0;
  for (const s of items) list.appendChild(inboxRow(s));
}

function inboxRow(s) {
  const w = workState(s);
  const row = document.createElement("div");
  row.className = "inbox-row work-" + w;

  // Tapping the row jumps to the full session.
  const open = document.createElement("button");
  open.className = "inbox-open";
  open.title = "Open this session";
  open.onclick = () => { closeInbox(); openSession(s.id); };

  const dot = document.createElement("span");
  dot.className = "dot";
  dot.dataset.work = w;
  open.appendChild(dot);

  const meta = document.createElement("span");
  meta.className = "inbox-meta";
  const name = document.createElement("span");
  name.className = "inbox-name";
  name.textContent = s.name || s.id;
  // A permission prompt wants a yes/no; a question or a finished turn wants a
  // reply. The reported note is what tells them apart.
  const wantsPermission = w === "asking" && s.signal_note === "permission";
  const sub = document.createElement("span");
  sub.className = "inbox-sub";
  sub.textContent = (wantsPermission ? "wants your approval" : WORK_WORDS[w])
    + (s.saying ? " · " + s.saying : "");
  meta.append(name, sub);
  open.appendChild(meta);
  row.appendChild(open);

  const bar = document.createElement("div");
  bar.className = "inbox-answer";

  // For a permission prompt, lead with one-tap Approve / Deny (Enter accepts the
  // highlighted default, Escape cancels) — the reply box stays for a worded
  // answer.
  if (wantsPermission) {
    const approve = document.createElement("button");
    approve.className = "inbox-approve";
    approve.textContent = "Approve";
    approve.onclick = () => sendKey(s.id, "Enter", "Approved");
    const deny = document.createElement("button");
    deny.className = "inbox-deny";
    deny.textContent = "Deny";
    deny.onclick = () => sendKey(s.id, "Escape", "Denied");
    bar.append(approve, deny);
  }

  // Answer without opening the pane: type a reply, or send it empty to accept
  // the highlighted default — which is how a Claude Code permission prompt, and
  // most y/n prompts, say yes.
  const input = document.createElement("input");
  input.type = "text";
  input.className = "inbox-reply";
  input.placeholder = "Reply, or send empty to accept…";
  input.autocomplete = "off";
  input.onkeydown = (e) => {
    if (e.key === "Enter") { e.preventDefault(); answerSession(s.id, input); }
  };
  const send = document.createElement("button");
  send.className = "inbox-send hdr-btn";
  send.title = "Send (empty accepts the default)";
  send.innerHTML = '<svg class="ico" aria-hidden="true"><use href="#i-corner-down-left"/></svg>';
  send.onclick = () => answerSession(s.id, input);
  bar.append(input, send);
  row.appendChild(bar);

  return row;
}

async function answerSession(id, input) {
  const text = (input.value || "").trim();
  const body = text ? { text, enter: true } : { key: "Enter" };
  const s = session(id);
  const who = s ? s.name : "session";
  try {
    await api("api/sessions/" + id + "/send", { method: "POST", body: JSON.stringify(body) });
    input.value = "";
    toast(text ? `Replied to "${who}"` : `Sent to "${who}"`);
    setTimeout(refresh, 400);   // let the state settle, then re-rank the inbox
  } catch (err) {
    toast(`Could not reach "${who}" — ${err.message || err}`, true);
  }
}

/* Approve / Deny a permission prompt with one key — Enter accepts the
 * highlighted default, Escape cancels. */
async function sendKey(id, key, label) {
  const s = session(id);
  const who = s ? s.name : "session";
  try {
    await api("api/sessions/" + id + "/send", { method: "POST", body: JSON.stringify({ key }) });
    toast(`${label} → "${who}"`);
    setTimeout(refresh, 400);
  } catch (err) {
    toast(`Could not reach "${who}" — ${err.message || err}`, true);
  }
}

/* When the last poll landed. The pane size label needs it to tell a stale
 * reading from somebody else's decision, and nothing else has the timestamp. */
let lastPollAt = 0;

async function refresh() {
  try {
    state = await api("api/state");
    lastPollAt = Date.now();
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
  renderPlan();
  renderInbox();
  renderBoard();   // no-op unless the board is open
  updateTitle();
  renderServices();
  renderGuard();
  renderVersion();
  renderSidePanel();   // a no-op while the panel is shut
  loadOrphans();
  reclaimSize();
  holdWindow(!document.hidden);
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
  let wantTabs = want.tabs, wantActive = want.active;
  let seenBefore = false;
  try {
    const local = JSON.parse(localStorage.getItem("clique.ws." + winId) || "null");
    if (local && Array.isArray(local.tabs)) {
      // A window we have been in before: restore its own remembered strip.
      wantTabs = local.tabs; wantActive = local.active || ""; seenBefore = true;
    }
  } catch (err) { /* fall back to the server workspace */ }
  if (!seenBefore) {
    // A fresh window. If another is already open, start CLEAN — a second screen
    // is a second desk, not a copy of the first — and let the person move the
    // tabs they want across. Give presence a moment to answer first.
    winSend({ t: "join", id: winId, joined: winJoined });
    await new Promise((ok) => setTimeout(ok, 450));
    if (otherWindows().length > 0) { wantTabs = []; wantActive = ""; }
  }
  openTabs = wantTabs.filter((id) => session(id));
  const pick = (wantActive && openTabs.includes(wantActive))
    ? wantActive
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

function fmtUptime(seconds) {
  const s = Math.max(0, seconds | 0);
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (d) return `${d}d ${h}h`;
  if (h) return `${h}h ${m}m`;
  if (m) return `${m}m`;
  return `${s}s`;
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

/* What is left of the plan, for the CLI in front.
 *
 * Fetched on its own slow timer rather than riding the 3s poll: the windows it
 * reports move over hours, and the panel is a guest on somebody else's API.
 * The server caches too, so a dozen open tabs are still one request.
 *
 * Shown only for a CLI that declares a probe, which today is the ones whose
 * vendor publishes one. Everything else gets no column at all rather than a
 * row of dashes, the same way the status bar treats a missing sensor. */
let planUsage = [];

async function loadUsage() {
  try {
    const payload = await api("api/usage");
    planUsage = payload.usage || [];
  } catch (err) {
    planUsage = [];      // offline, or the setting is off; say nothing
  }
  renderPlan();
}

/* "in 2h" / "in 40m" / "now". A reset an hour out is the number that decides
 * whether to keep going or stop, so it is worth more than the timestamp. */
function untilReset(iso) {
  const at = Date.parse(iso || "");
  if (!at) return "";
  const mins = Math.round((at - Date.now()) / 60000);
  if (mins <= 0) return "now";
  if (mins < 60) return `in ${mins}m`;
  const hours = mins / 60;
  return hours < 24 ? `in ${Math.round(hours)}h` : `in ${Math.round(hours / 24)}d`;
}

/* A meter each, not a line of text.
 *
 * This is the reading that changes what you do next: a week that is 90% gone
 * means stop starting things. Buried in the machine stats as "PLAN 5H 5%" it
 * read as one more number beside disk free, which is not what it is. A short
 * bar is legible at a glance from across the desk, and the exact figure is
 * still there for anyone who wants it. */
function planLevel(percent) {
  return percent >= 90 ? "err" : percent >= 75 ? "wait" : "ok";
}

function renderPlan() {
  const el = $("#plan");
  if (!el) return;
  const s = activeId ? session(activeId) : null;
  const found = s && planUsage.find((u) => u.cli === s.cli);
  const windows = (found && found.windows) || [];
  el.hidden = !windows.length;
  if (!windows.length) { el.replaceChildren(); return; }

  el.replaceChildren();
  for (const w of windows) {
    const pct = Math.round(w.percent);
    const when = untilReset(w.resets_at);
    const meter = mk("span", "plan-w");
    meter.dataset.level = planLevel(w.percent);

    const key = mk("b", "plan-k");
    key.textContent = w.label;
    const track = mk("span", "plan-track");
    const fill = mk("i", "plan-fill");
    // scaleX rather than width: the bar sits in a bottom bar beside a dozen
    // PTYs, and a transform is the one thing that cannot make the browser
    // lay the row out again.
    fill.style.transform = `scaleX(${Math.max(w.percent, 0.5) / 100})`;
    track.appendChild(fill);
    const value = mk("span", "plan-v");
    value.textContent = pct + "%";

    meter.append(key, track, value);
    meter.title = `${w.label}: ${pct}% used`
      + (when ? `, resets ${when}` : "")
      + `\nAsked of ${s.cli_label || s.cli} directly, at most once every few minutes.`;
    el.appendChild(meter);
  }
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

  // Any swap in use means memory pressure already happened. A box using none
  // does not get a column for it — the row closes up rather than holding a
  // blank the width of "SWAP —.–G".
  const swap = st.swap || {};
  const swapEl = $("#swap");
  if (swapEl) {
    swapEl.classList.toggle("is-off", !(swap.used_mb > 0));
    paintStat("swap", Math.max(swap.percent || 0, 70),
              gb(swap.used_mb) + "G",
              `swap ${swap.percent ?? 0}% used — memory pressure has already happened`);
  }

  // Temperature, only when the machine actually has a sensor. Like swap, the
  // column is gone rather than blank when there is nothing honest to say,
  // which on most VMs is always.
  const temp = st.temp || {};
  const tempEl = $("#temp");
  if (tempEl) {
    const c = typeof temp.c === "number" ? temp.c : null;
    tempEl.classList.toggle("is-off", c === null);
    if (c !== null) paintStat("temp", c, Math.round(c) + "°C", `${c}°C, hottest sensor`);
  }

  // How long the box has been up. Always available on Linux, so it always shows.
  const up = (st.uptime && st.uptime.seconds) || 0;
  paintStat("uptime", 0, fmtUptime(up), `up ${fmtUptime(up)} since boot`);

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

/* The resource guard's banner.
 *
 * A heads-up, never a gate. It shows only when the box is genuinely stretched
 * for the number of live sessions the backend saw, carries the one plain
 * sentence it composed, and offers the levers that already exist. Dismissible,
 * and it comes back on its own if the situation gets worse. */
let guardDismissed = "";

function renderGuard() {
  const host = $("#guard");
  if (!host) return;
  const g = (state.stats || {}).guard || {};
  const level = g.level || "ok";
  if (level === "ok" || !g.headline) {
    host.hidden = true;
    host.textContent = "";
    guardDismissed = "";   // a calm box forgets the dismissal
    return;
  }
  // Re-surface after a dismissal only if it got worse or the count changed —
  // the same sentence about the same box stays gone.
  const sig = level + ":" + (g.sessions ?? 0);
  if (guardDismissed === sig) { host.hidden = true; return; }

  host.textContent = "";
  host.dataset.level = level;

  const mark = document.createElement("span");
  mark.className = "guard-mark";
  mark.innerHTML =
    '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" ' +
    'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
    '<path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/>' +
    '<line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>';

  const said = document.createElement("span");
  said.className = "guard-said";
  said.textContent = g.headline;
  if ((g.reasons || []).length) said.title = g.reasons.join(" · ");

  host.append(mark, said);

  // Only offer Reclaim when there is actually something leaked to reclaim —
  // never a button that does nothing. The other lever (auto-reap) needs no
  // button; the sentence already names it.
  if ((orphans || []).length) {
    const act = document.createElement("button");
    act.type = "button";
    act.className = "guard-act";
    act.textContent = "Reclaim";
    act.onclick = reclaimOrphans;
    host.append(act);
  }

  const x = document.createElement("button");
  x.type = "button";
  x.className = "guard-x";
  x.title = "Dismiss";
  x.setAttribute("aria-label", "Dismiss this heads-up");
  x.textContent = "×";
  x.onclick = () => { guardDismissed = sig; host.hidden = true; };
  host.append(x);

  host.hidden = false;
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
    [f.id, f.name, f.color, f.emoji || "", f.collapsed ? 1 : 0].join("\x1f")).join("\x1e");
  const hist = (resumable || []).map((c) => [
    c.cli_session_id || "", c.label, c.cwd, c.folder || "",
    ago(c.updated), c.repeats || 1, c.cli || "",
  ].join("\x1f")).join("\x1e");
  return [
    query, activeOnly ? 1 : 0, activeId || "",
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

function applyActiveOnly() {
  const btn = $("#activeOnly");
  if (!btn) return;
  btn.classList.toggle("on", activeOnly);
  btn.setAttribute("aria-pressed", activeOnly ? "true" : "false");
  btn.title = activeOnly ? "Showing only running sessions \u2014 click to show all"
                         : "Show only running sessions";
}

function toggleActiveOnly() {
  activeOnly = !activeOnly;
  try { localStorage.setItem("clique.activeOnly", activeOnly ? "1" : "0"); } catch (e) {}
  applyActiveOnly();
  renderTree();   // the fingerprint carries activeOnly, so this rebuilds
}

/* The clear × shows only when the search box has something in it. */
function syncQClear() {
  const btn = $("#qClear");
  const q = $("#q");
  if (btn && q) btn.hidden = q.value === "";
}

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

  // A typed search overrides the running-only filter: you search to find a
  // session to open, and the one you want is often a stopped one. With no
  // query, the filter applies as before.
  const matches = (s) =>
    (!activeOnly || s.alive || Boolean(query))
    && (!query || s.name.toLowerCase().includes(query)
      || s.cwd.toLowerCase().includes(query)
      || (s.branch || "").toLowerCase().includes(query));

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
  // With a query up, Ungrouped is drawn even when nothing live is in it: the
  // thing being searched for is often a session that was closed, and its
  // history row has nowhere else to appear.
  if (unfiled.length || query) {
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
    // Pinned sessions float to the top of their group, above recency. A stable
    // sort keeps the order within the pinned, and within the rest.
    shown.sort((a, b) => (b.pinned ? 1 : 0) - (a.pinned ? 1 : 0));
    /* History counts towards whether this group has anything to show. Worked
     * out here rather than after the skip below, because a search whose only
     * match is a session you closed yesterday used to drop the whole group
     * before its history was ever consulted, and the search came up empty. */
    const hist = group.collapsed && !query ? [] : historyRows(group, query);
    if ((query || activeOnly) && !shown.length && !hist.length) continue;

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
      (group.emoji
        ? `<span class="folder-emoji" aria-hidden="true">${escapeHtml(group.emoji)}</span>`
        : `<i class="dot" style="background:${cssColor(group.color)}"></i>`) +
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
    for (const row of hist) tree.appendChild(row);
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

function humanBytes(n) {
  if (!n) return "";
  const mb = n / (1024 * 1024);
  return mb >= 1024 ? (mb / 1024).toFixed(1) + "G" : Math.round(mb) + "M";
}

function sessionRow(s) {
  const row = document.createElement("div");
  row.className = "session" + (s.id === activeId ? " active" : "") +
    (s.alive ? "" : " dead") + (s.busy ? " busy" : "") +
    (unread(s) ? " unread" : "") +
    (workState(s) === "asking" ? " asking" : "") +
    (workState(s) === "error" ? " error" : "") +
    (s.pinned ? " pinned" : "") +
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
    `<span class="meta"><span class="name">${s.pinned ? '<span class="pin" title="Pinned">\u2605</span>' : ''}${escapeHtml(s.name)}</span>` +
    `<span class="${pathClass}">${pathHtml}</span>` +
    `</span>` +
    (s.rss ? `<span class="rss" title="Memory — the CLI and everything it spawned">${humanBytes(s.rss)}</span>` : "") +
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
    ["Open a file", () => openFileSheet(s.id, s.cwd || ".")],
    ...(cliHasTranscript(s.cli) ? [["View conversation", () => openTranscript(s)]] : []),
    ...(cliHasTranscript(s.cli) ? [["Usage", () => openUsage(s)]] : []),
    ...(s.branch || s.dirty ? [["Review changes", () => openDiff(s)]] : []),
    ...(s.branch ? [["Checkpoint — save HEAD + current diff", () => checkpointSession(s)]] : []),
    ["Rename", () => renameSession(s)],
    ["Notes", () => openNote(s)],
    ["Duplicate (same CLI, same directory)", () => duplicateSession(s)],
    ...(otherClis(s).length
      ? [["Open in another CLI…", () => openInOtherCli(s)]] : []),
    /* Moving a session between folders was drag-and-drop and nothing else.
     *
     * There is no drag on a phone, which made half the sidebar's organisation
     * unreachable on the device most likely to be checking on a session — the
     * same gap the long-press menu exists to close. It is also not discoverable
     * on a desktop: nothing about a row says it can be dragged. */
    ...(folders.length ? [["Move to folder…", () => moveToFolder(s)]] : []),
    ...(s.folder ? [["Take out of its folder", () => setFolder(s, null)]] : []),
    ...windowMoveItems(s),
    [s.archived ? "Unarchive" : "Archive", () => setArchived(s, !s.archived)],
    [s.pinned ? "Unpin from top" : "Pin to top", () => setPinned(s, !s.pinned)],
    [reviewLockedOf(s.id) ? "Unlock (read-only)" : "Lock read-only for review",
     () => toggleReviewLock(s.id)],
    ...(s.alive ? [["Interrupt (Ctrl-C)", () => sendKey(s.id, "C-c", "Paused")]] : []),
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

function wireTermTouchMenus() {
  const host = $("#terminal");
  if (!host) return;
  let timer = null;
  let from = null;
  let fired = false;
  const cancel = () => { clearTimeout(timer); timer = null; from = null; };
  host.addEventListener("touchstart", (ev) => {
    cancel();
    if (ev.touches.length !== 1) return;
    const touch = ev.touches[0];
    from = { x: touch.clientX, y: touch.clientY };
    fired = false;
    timer = setTimeout(() => {
      timer = null;
      if (!from) return;
      fired = true;
      if (navigator.vibrate) navigator.vibrate(12);
      host.dispatchEvent(new MouseEvent("contextmenu", {
        bubbles: true, cancelable: true,
        clientX: from.x, clientY: from.y,
      }));
    }, LONG_PRESS_MS);
  }, { passive: true });
  host.addEventListener("touchmove", (ev) => {
    if (!from || !ev.touches.length) return;
    const touch = ev.touches[0];
    if (Math.abs(touch.clientX - from.x) > LONG_PRESS_SLOP
        || Math.abs(touch.clientY - from.y) > LONG_PRESS_SLOP) cancel();
  }, { passive: true });
  host.addEventListener("touchend", (ev) => {
    if (fired) { ev.preventDefault(); fired = false; }
    cancel();
  }, { passive: false });
  host.addEventListener("touchcancel", cancel, { passive: true });
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
    [folder.emoji ? "Change emoji (or clear)" : "Set an emoji", async () => {
      // A plain prompt: paste or type an emoji, or leave it blank to go back to
      // the colour dot. Bounded and escaped on the server and on render.
      const emoji = prompt("Emoji for this folder (blank to clear)", folder.emoji || "");
      if (emoji === null) return;   // cancelled, as distinct from cleared
      await api("api/folders/" + folder.id, {
        method: "PATCH", body: JSON.stringify({ emoji: emoji.trim() }),
      });
      refresh();
    }],
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

/* A second agent on the same work: same directory, folder and CLI, its own
 * fresh process. Everything create_session already accepts, so there is no new
 * endpoint — the source's own fields, handed back in. It shares whatever the
 * source's cwd is (a worktree included); making a *new* worktree is the other
 * gesture, in New Session. The copied name is a starting point — auto-title
 * renames it from the first prompt if it was still a generic one. */
/* Filing a session, and taking it out again.
 *
 * Both of these went with the same 0.44.0 edit, so "Move to folder…" and
 * "Take out of its folder" have each been a ReferenceError since. Restored
 * together because the first calls the second.
 */
async function setFolder(s, folder) {
  await api("api/sessions/" + encodeURIComponent(s.id), {
    method: "PATCH", body: JSON.stringify({ folder }),
  });
  const where = folder
    ? ((state.folders || []).find((f) => f.id === folder) || {}).name || "a folder"
    : "Ungrouped";
  toast(`${s.name} moved to ${where}`);
  refresh();
}

/* Filing a session from the menu.
 *
 * Nothing caught either of these because the call is inside a click handler,
 * where a throw is invisible unless a console happens to be open. smoke.py now
 * reads every arrow-wrapped call in app.js and fails on one that resolves to
 * nothing, which is the cheap version of the test that would have caught it.
 */
function moveToFolder(s) {
  const folders = (state.folders || []).filter((f) => f.id.startsWith("f-"));
  showMenu(lastMenuEvent, folders.map((f) => [
    f.name + (f.id === s.folder ? "  ·  where it is now" : ""),
    () => { if (f.id !== s.folder) setFolder(s, f.id); },
  ]));
}

/* Every other installed CLI, which is also what decides whether the menu
 * offers the item at all. One CLI on the box means there is nothing to open
 * it in, and an item that can only disappoint is worse than no item. */
function otherClis(s) {
  return (state.clis || []).filter((c) => c.installed && c.id !== s.cli);
}

/* The same work, in a different tool.
 *
 * Distinct from Duplicate, which starts a second instance of the same CLI.
 * This starts a different one in the same directory under the same name, so
 * the two sit side by side as tabs reading the same thing, told apart by the
 * CLI marker rather than by a suffix nobody asked for. Handing the same job to
 * Codex that Claude has been chewing on is the point, and it should not mean
 * retyping a path.
 *
 * `mode` is deliberately not carried across. A mode is something a CLI
 * declares in clis.toml, so the source's mode id means nothing to the target
 * and would either be rejected or, worse, silently match something unrelated.
 * The new session takes the target CLI's own default. */
function openInOtherCli(s) {
  const others = otherClis(s);
  if (!others.length) return;
  showMenu(lastMenuEvent, others.map((c) => [c.label || c.id, () => cloneToCli(s, c)]));
}

async function cloneToCli(s, cli) {
  const what = cli.label || cli.id;
  try {
    const created = await api("api/sessions", {
      method: "POST",
      body: JSON.stringify({
        cli: cli.id, cwd: s.cwd, name: s.name,
        folder: s.folder || undefined,
      }),
    });
    await refresh();
    openSession(created.id);
    toast(`“${s.name}” opened in ${what}`);
  } catch (err) {
    toast(`Could not open it in ${what}: ${err.message || err}`, true);
  }
}

async function duplicateSession(s) {
  try {
    const created = await api("api/sessions", {
      method: "POST",
      body: JSON.stringify({
        cli: s.cli, cwd: s.cwd,
        folder: s.folder || undefined,
        name: s.name, mode: s.mode || undefined,
      }),
    });
    await refresh();
    openSession(created.id);
    toast(`Forked “${s.name}”`);
  } catch (err) {
    toast(`Could not duplicate — ${err.message || err}`, true);
  }
}

/* Save the repo's state before you let an agent loose: the current HEAD and
 * the uncommitted diff go to a file under .clique-checkpoints/, so afterwards
 * you can see — or reverse — exactly what it changed. A record, not a lock. */
async function checkpointSession(s) {
  try {
    const r = await api(`api/sessions/${encodeURIComponent(s.id)}/checkpoint`, {
      method: "POST", body: "{}",
    });
    const what = r.shortstat || "no uncommitted changes";
    toast(`Checkpoint saved — ${r.relative} (HEAD ${r.head}, ${what})`);
  } catch (err) {
    toast(`Could not checkpoint — ${err.message || err}`, true);
  }
}

/* Dump the session's whole scrollback to a timestamped .txt under
 * .clique-exports/, so a run can be kept, searched or shared after the fact.
 * tmux holds the history; the server captures it unstyled. */
async function exportScrollback(s) {
  try {
    const r = await api(`api/sessions/${encodeURIComponent(s.id)}/export`, {
      method: "POST", body: "{}",
    });
    toast(`Scrollback saved: ${r.relative} (${r.lines} lines)`);
  } catch (err) {
    toast(`Could not export: ${err.message || err}`, true);
  }
}

/* =========================================================================
 * Side panel — a docked, per-session feature rail.
 *
 * A thin always-on icon rail on the right edge opens a panel to a feature for
 * the session in front: Notes, Git, Session info, Export. Collapsed by default,
 * and while it is shut nothing in here runs — an idle panel costs the browser
 * nothing. Which pane is open and how wide the panel is are this browser's
 * business, the same rule that keeps the sidebar width and the tab order local,
 * so they live in localStorage, not on the server.
 *
 * Cost discipline: the panel re-renders on the 3s poll and on a tab switch, but
 * the Notes pane rebuilds its outline only when the session or pane changes —
 * a poll just refreshes the "due" chips, so typing is never interrupted and the
 * DOM is not thrown away every three seconds. The one heavy step, reflowing the
 * terminal when the panel opens or closes, is debounced.
 * ===================================================================== */

const PANE_MIN = 240, PANE_MAX = 760, PANE_DEFAULT = 320;
const PANE_LABEL = { notes: "Notes", git: "Git", info: "Session", export: "Export" };
const PANE_ICON = { notes: "notebook", git: "git-branch", info: "info", export: "download" };

let panelPane = null;          // open pane id, or null when the panel is shut
let panelWidth = PANE_DEFAULT;
let panelKey = "";             // "pane:sessionId" of what is drawn right now
const notesCache = new Map();  // session id -> items[] (last known, for instant redraw)

const supportsPlaintext = (() => {
  try {
    const d = document.createElement("div");
    d.contentEditable = "plaintext-only";
    return d.contentEditable === "plaintext-only";
  } catch (err) { return false; }
})();

function nowSec() { return Math.floor(Date.now() / 1000); }
function noteId() { return "n" + Math.random().toString(16).slice(2, 12); }

function panelLoad() {
  try {
    const w = parseInt(localStorage.getItem("clique.panel.w"), 10);
    if (w >= PANE_MIN && w <= PANE_MAX) panelWidth = w;
    const p = localStorage.getItem("clique.panel.pane");
    if (p && PANE_LABEL[p]) panelPane = p;
  } catch (err) { /* private mode: the defaults are fine */ }
  document.documentElement.style.setProperty("--panel-w", panelWidth + "px");
}
function panelSave() {
  try {
    localStorage.setItem("clique.panel.w", String(panelWidth));
    if (panelPane) localStorage.setItem("clique.panel.pane", panelPane);
    else localStorage.removeItem("clique.panel.pane");
  } catch (err) { /* nothing to persist to */ }
}

function togglePanel(pane) {
  if (panelPane === pane || (!pane && panelPane)) return closePanel();
  openPanel(pane || panelPane || "notes");
}
function openPanel(pane) {
  if (!PANE_LABEL[pane]) pane = "notes";
  const was = Boolean(panelPane);
  panelPane = pane;
  panelSave();
  renderSidePanel();
  if (!was) refitSoon();   // the pane just took width off the terminal
}
function closePanel() {
  if (!panelPane) return;
  panelPane = null;
  panelSave();
  renderSidePanel();
  refitSoon();
}

/* Run something once, after the browser has finished laying this change out.
 *
 * Two frames, not a timeout. A ResizeObserver delivers its callbacks inside
 * the same rendering update as the frame that dirtied the layout, and *after*
 * that frame's requestAnimationFrame callbacks — so the next frame is the
 * first moment everything that measures the pane has already run. That is a
 * property of the event loop; the 80ms this replaces was a guess, needlessly
 * slow on a fast machine and not reliably long enough on a slow one. Keyed, so
 * a second toggle replaces the first instead of queueing behind it. */
var _layoutJobs = new Map();
function afterLayout(key, fn) {
  cancelAnimationFrame(_layoutJobs.get(key) || 0);
  _layoutJobs.set(key, requestAnimationFrame(() => {
    _layoutJobs.set(key, requestAnimationFrame(() => {
      _layoutJobs.delete(key);
      try { fn(); } catch (err) { /* nothing laid out yet */ }
    }));
  }));
}

/* The pane changed shape. Measure it, tell tmux, and ask for a frame back. */
function settlePane() {
  packTabs();
  refitAll();
  reclaimSize(document.hasFocus());
  repaintPane();
  renderSessionLine();   // the pane's size just changed, and the strip says so
}

/* Ask tmux to repaint this browser's own client now.
 *
 * tmux draws a client when something it tracks changes. A pane handed back the
 * same grid it already had changes nothing tmux can see, so whatever the
 * terminal last drew stays on screen until a keystroke provokes a frame — the
 * "it does not redraw until you type" half of this. One client, the one this
 * socket owns; nobody else's window moves. */
function repaintPane() {
  if (document.hidden) return;
  const entry = terms.get(activeId);
  if (!entry || !entry.ws || entry.ws.readyState !== 1) return;
  try {
    entry.ws.send(JSON.stringify({ type: "refresh" }));
  } catch (err) { /* the socket went while we were measuring */ }
}

/* Reflow tmux once the layout has settled, not on every pixel of a drag. */
function refitSoon() {
  // Opening or closing the panel changes the pane's width, and fitting before
  // layout has caught up computes a boxed CLI's zoom against the old width, so
  // it comes back scaled wrong with dead space or spilling under the panel.
  afterLayout("pane", settlePane);
}

function paintRail() {
  for (const btn of document.querySelectorAll(".railr-btn")) {
    btn.classList.toggle("active", btn.dataset.pane === panelPane);
  }
  const notesBtn = document.querySelector('.railr-btn[data-pane="notes"]');
  if (notesBtn) {
    const items = notesCache.get(activeId);
    notesBtn.dataset.flag = items && notesAnyDue(items) ? "1" : "0";
  }
}

function renderSidePanel() {
  paintRail();
  const wrap = $("#sidepanel");
  const rez = $("#panelResizer");
  if (!wrap) return;
  if (!panelPane) {
    wrap.hidden = true;
    if (rez) rez.hidden = true;
    panelKey = "";
    return;
  }
  wrap.hidden = false;
  if (rez) rez.hidden = false;
  const use = $("#panelIcon");
  if (use) use.setAttribute("href", "#i-" + (PANE_ICON[panelPane] || "notebook"));
  $("#panelTitle").textContent = PANE_LABEL[panelPane] || "";
  const s = activeId ? session(activeId) : null;
  $("#panelFor").textContent = s ? s.name : "no session open";
  const body = $("#panelBody");
  const key = panelPane + ":" + (activeId || "");
  const fresh = key !== panelKey;
  panelKey = key;
  if (panelPane === "notes") return renderNotesPane(body, s, fresh);
  if (panelPane === "git") return renderGitPane(body, s);
  if (panelPane === "info") return renderInfoPane(body, s);
  if (panelPane === "export") return renderExportPane(body, s);
}

/* -------------------------------------------------------- little builders */

function mk(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
}
function paneEmpty(text) { return mk("p", "pane-empty", text); }
function paneP(text) { return mk("p", "pane-hint", text); }
function kvRow(k, v, cls) {
  const row = mk("div", "kv");
  row.append(mk("span", "k", k), mk("span", "v" + (cls ? " " + cls : ""), v));
  return row;
}
function paneButton(label, iconName, fn) {
  const b = document.createElement("button");
  b.type = "button";
  b.className = "pane-btn";
  b.innerHTML = icon(iconName) + `<span>${escapeHtml(label)}</span>`;
  b.onclick = fn;
  return b;
}

/* ----------------------------------------------------------- Notes pane */

let notesItems = [];           // the working outline for the session in front
let notesForId = null;
let notesHideDone = false;
let _notesSaveTimer = null;
let _notesPending = null;      // {id, items} captured when the save was queued
let _notesLoadToken = 0;

/* The menu/palette entry: bring the panel up on Notes for a session, switching
 * to that tab first because the panel always follows the session in front. */
async function openNote(s) {
  if (s && s.id !== activeId) await openSession(s.id);
  openPanel("notes");
}

function renderNotesPane(body, s, fresh) {
  if (!s) {
    notesForId = null;
    body.replaceChildren(paneEmpty("Open a session to take notes for it."));
    return;
  }
  if (!fresh) { refreshNotesDue(); return; }
  buildNotes(body, s, notesCache.get(s.id) || []);
  loadNotes(s.id);
}

async function loadNotes(id) {
  const token = ++_notesLoadToken;
  try {
    const r = await api(`api/sessions/${encodeURIComponent(id)}/notes`);
    if (token !== _notesLoadToken) return;                 // a newer load won
    if (panelPane !== "notes" || activeId !== id) return;  // switched away
    if (_notesPending && _notesPending.id === id) return;    // local edit is newer
    notesCache.set(id, r.items || []);
    buildNotes($("#panelBody"), session(id), r.items || []);
  } catch (err) {
    if (token === _notesLoadToken && panelPane === "notes" && activeId === id) {
      $("#panelBody").replaceChildren(
        paneEmpty("Could not load notes: " + (err.message || err)));
    }
  }
}

function buildNotes(body, s, items) {
  notesItems = items;
  notesForId = s.id;
  body.replaceChildren();
  const tools = mk("div", "pane-tools");
  tools.append(paneButton("Add note", "plus", () => {
    const it = noteNew("");
    notesItems.push(it);
    queueSaveNotes();
    rebuildNotes({ focusId: it.id });
  }));
  const hide = paneButton(notesHideDone ? "Show done" : "Hide done", "filter",
    () => { notesHideDone = !notesHideDone; rebuildNotes(); });
  if (notesHideDone) hide.classList.add("on");
  tools.append(hide);
  body.append(tools);
  const list = mk("div", "notes-list");
  list.id = "notesList";
  body.append(list);
  paintNotesList(list);
  body.append(paneP(
    "Enter starts a new line, Tab indents it, the arrow sends a line to the "
    + "terminal, and the clock sets a reminder."));
}

function rebuildNotes(opts = {}) {
  const list = $("#notesList");
  if (!list) return;
  paintNotesList(list);
  if (opts.focusId) focusNoteText(opts.focusId);
  paintRail();
}

function paintNotesList(list) {
  const kids = [];
  for (const it of notesItems) {
    const node = noteRowEl(it);
    if (node) kids.push(node);
  }
  if (!kids.length) {
    kids.push(paneEmpty("No notes yet. Add one to start a checklist for this session."));
  }
  list.replaceChildren(...kids);
}

function noteNew(text) {
  const t = nowSec();
  return {
    id: noteId(), text: text || "", done: false, collapsed: false,
    created: t, updated: t, remindAt: null, reminded: false, children: [],
  };
}

function noteRowEl(item) {
  if (notesHideDone && item.done) return null;
  const wrap = mk("div", "note-item" + (item.done ? " done" : ""));
  wrap.dataset.id = item.id;
  const row = mk("div", "note-row");

  const hasKids = Boolean(item.children && item.children.length);
  const caret = document.createElement("button");
  caret.type = "button";
  caret.className = "note-caret" + (hasKids ? "" : " leaf");
  caret.innerHTML = icon(item.collapsed ? "chevron-right" : "chevron-down");
  caret.title = item.collapsed ? "Expand" : "Collapse";
  if (hasKids) caret.onclick = () => toggleCollapse(item.id);

  const check = document.createElement("button");
  check.type = "button";
  check.className = "note-check" + (item.done ? " done" : "");
  check.title = item.done ? "Mark not done" : "Mark done";
  check.onclick = () => toggleDone(item.id);

  const text = mk("div", "note-text");
  text.contentEditable = supportsPlaintext ? "plaintext-only" : "true";
  text.spellcheck = false;
  text.dataset.placeholder = "New note";
  text.textContent = item.text || "";
  wireNoteText(text, item);

  const actions = mk("div", "note-actions");
  actions.append(
    noteAct("arrow-right", "Send this line to the terminal", () => sendNoteToTerminal(item)),
    noteAct("clock", "Set a reminder", (ev) => openRemind(ev, item)),
    noteAct("plus", "Add a sub-note", () => addChild(item.id)),
    noteAct("trash", "Delete (its sub-notes move up)", () => removeNote(item.id)),
  );

  row.append(caret, check, text, actions);
  wrap.append(row);

  const meta = noteMeta(item);
  if (meta) wrap.append(meta);

  if (hasKids && !item.collapsed) {
    const kids = mk("div", "note-children");
    for (const c of item.children) {
      const el = noteRowEl(c);
      if (el) kids.append(el);
    }
    wrap.append(kids);
  }
  return wrap;
}

function noteAct(iconName, title, fn) {
  const b = document.createElement("button");
  b.type = "button";
  b.className = "note-act";
  b.title = title;
  b.innerHTML = icon(iconName);
  b.onclick = (ev) => { ev.preventDefault(); ev.stopPropagation(); fn(ev); };
  return b;
}

function noteMeta(item) {
  const meta = mk("div", "note-meta");
  let any = false;
  if (item.remindAt) {
    const chip = mk("span", "note-remind");
    chip.dataset.at = String(item.remindAt);
    chip.dataset.reminded = item.reminded ? "1" : "0";
    if (item.reminded) chip.classList.add("reminded");
    if (!item.done && !item.reminded && item.remindAt <= nowSec()) chip.classList.add("due");
    chip.innerHTML = icon("clock") + `<span>${escapeHtml(fmtWhen(item.remindAt))}</span>`;
    chip.title = "Reminder — click to change";
    chip.onclick = (ev) => openRemind(ev, item);
    meta.append(chip);
    any = true;
  }
  if (item.updated) {
    const t = mk("span", "note-time", "edited " + (ago(item.updated) || "now"));
    if (item.created) t.title = "Created " + fmtWhen(item.created);
    meta.append(t);
    any = true;
  }
  return any ? meta : null;
}

function wireNoteText(elm, item) {
  elm.oninput = () => {
    item.text = elm.textContent;
    item.updated = nowSec();
    queueSaveNotes();
  };
  elm.onkeydown = (ev) => {
    if (ev.key === "Enter" && !ev.shiftKey) {
      ev.preventDefault();
      const id = addSiblingAfter(item.id);
      if (id) rebuildNotes({ focusId: id });
    } else if (ev.key === "Tab") {
      ev.preventDefault();
      const ok = ev.shiftKey ? outdent(item.id) : indent(item.id);
      if (ok) rebuildNotes({ focusId: item.id });
    } else if (ev.key === "Backspace" && elm.textContent === "") {
      ev.preventDefault();
      const prev = prevFocusId(item.id);
      removeNote(item.id);
      if (prev) focusNoteText(prev);
    }
  };
  elm.onblur = () => flushSaveNotes();
}

/* ---- outline operations, all on the in-memory tree, then a debounced save ---- */

function noteFind(id, list = notesItems, parent = null) {
  for (let i = 0; i < list.length; i++) {
    if (list[i].id === id) return { item: list[i], list, index: i, parent };
    const deep = noteFind(id, list[i].children || [], list[i]);
    if (deep) return deep;
  }
  return null;
}
function addSiblingAfter(id) {
  const f = noteFind(id);
  if (!f) return null;
  const it = noteNew("");
  f.list.splice(f.index + 1, 0, it);
  queueSaveNotes();
  return it.id;
}
function addChild(id) {
  const f = noteFind(id);
  if (!f) return;
  const it = noteNew("");
  f.item.children.push(it);
  f.item.collapsed = false;
  queueSaveNotes();
  rebuildNotes({ focusId: it.id });
}
function indent(id) {
  const f = noteFind(id);
  if (!f || f.index === 0) return false;     // nothing to become a child of
  const prev = f.list[f.index - 1];
  f.list.splice(f.index, 1);
  (prev.children = prev.children || []).push(f.item);
  prev.collapsed = false;
  queueSaveNotes();
  return true;
}
function outdent(id) {
  const f = noteFind(id);
  if (!f || !f.parent) return false;          // already at the top level
  const pf = noteFind(f.parent.id);
  f.list.splice(f.index, 1);
  pf.list.splice(pf.index + 1, 0, f.item);
  queueSaveNotes();
  return true;
}
function removeNote(id) {
  const f = noteFind(id);
  if (!f) return;
  // A delete never silently drops a branch: children move up into its place.
  f.list.splice(f.index, 1, ...(f.item.children || []));
  queueSaveNotes();
  rebuildNotes();
}
function toggleDone(id) {
  const f = noteFind(id);
  if (!f) return;
  f.item.done = !f.item.done;
  f.item.updated = nowSec();
  queueSaveNotes();
  rebuildNotes();
}
function toggleCollapse(id) {
  const f = noteFind(id);
  if (!f) return;
  f.item.collapsed = !f.item.collapsed;
  queueSaveNotes();
  rebuildNotes();
}
function setReminder(id, epoch) {
  const f = noteFind(id);
  if (!f) return;
  f.item.remindAt = epoch;
  f.item.reminded = false;         // a new (or changed) time is a fresh reminder
  f.item.updated = nowSec();
  queueSaveNotes();
  rebuildNotes();
}
function prevFocusId(id) {
  const f = noteFind(id);
  if (!f) return null;
  if (f.index > 0) return f.list[f.index - 1].id;
  return f.parent ? f.parent.id : null;
}

function focusNoteText(id) {
  const sel = `.note-item[data-id="${cssEscape(id)}"] > .note-row > .note-text`;
  const el = document.querySelector(sel);
  if (!el) return;
  el.focus();
  const range = document.createRange();
  range.selectNodeContents(el);
  range.collapse(false);
  const s = window.getSelection();
  s.removeAllRanges();
  s.addRange(range);
}
function cssEscape(v) {
  return window.CSS && CSS.escape ? CSS.escape(v) : String(v).replace(/["\\]/g, "\\$&");
}

function refreshNotesDue() {
  if (notesForId !== activeId) return;    // stale; a fresh render will fix it
  const now = nowSec();
  for (const chip of document.querySelectorAll("#panelBody .note-remind")) {
    const at = parseInt(chip.dataset.at, 10) || 0;
    const done = chip.closest(".note-item").classList.contains("done");
    const reminded = chip.dataset.reminded === "1";
    chip.classList.toggle("due", at > 0 && !done && !reminded && at <= now);
  }
  paintRail();
}

function notesAnyDue(items) {
  const now = nowSec();
  const walk = (list) => (list || []).some((it) =>
    (it.remindAt && !it.done && !it.reminded && it.remindAt <= now) || walk(it.children));
  return walk(items);
}

async function sendNoteToTerminal(item) {
  const s = session(activeId);
  if (!s) return;
  const text = (item.text || "").trim();
  if (!text) { toast("That note is empty", true); return; }
  try {
    await api(`api/sessions/${encodeURIComponent(s.id)}/send`, {
      method: "POST", body: JSON.stringify({ text, enter: false }),
    });
    toast(`Sent to ${s.name} — press Enter to run`);
  } catch (err) {
    toast(String(err.message || err), true);
  }
}

function queueSaveNotes() {
  paintRail();
  // Capture the session and the outline as they are *now*, not as they will be
  // when the timer fires. Switching sessions inside the debounce window used to
  // send the new session's outline to the new session's endpoint: the edit was
  // lost, and if that session had never been opened here its outline was the
  // empty one, which the server reads as "delete the file".
  _notesPending = { id: notesForId, items: notesItems };
  clearTimeout(_notesSaveTimer);
  _notesSaveTimer = setTimeout(flushSaveNotes, 600);
}
async function flushSaveNotes() {
  clearTimeout(_notesSaveTimer);
  _notesSaveTimer = null;
  const pending = _notesPending;
  _notesPending = null;
  if (!pending || !pending.id) return;
  const { id, items } = pending;
  notesCache.set(id, items);
  try {
    await api(`api/sessions/${encodeURIComponent(id)}/notes`, {
      method: "POST", body: JSON.stringify({ items }),
    });
  } catch (err) {
    toast("Notes did not save: " + (err.message || err), true);
  }
}

/* ---- the inline reminder time picker ---- */

let _remindItem = null;
function openRemind(ev, item) {
  _remindItem = item;
  const pop = $("#remindPop");
  const input = $("#remindAt");
  input.value = epochToLocalInput(item.remindAt);
  $("#remindClear").hidden = !item.remindAt;
  pop.hidden = false;
  const w = 232, h = pop.offsetHeight || 130;
  const x = ev && ev.clientX ? ev.clientX : innerWidth - w - 48;
  const y = ev && ev.clientY ? ev.clientY : 120;
  pop.style.left = Math.max(8, Math.min(x, innerWidth - w - 8)) + "px";
  pop.style.top = Math.max(8, Math.min(y, innerHeight - h - 8)) + "px";
  input.focus();
}
function closeRemind() { $("#remindPop").hidden = true; _remindItem = null; }

function epochToLocalInput(epoch) {
  if (!epoch) return "";
  const d = new Date(epoch * 1000);
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`
    + `T${p(d.getHours())}:${p(d.getMinutes())}`;
}
function localInputToEpoch(v) {
  if (!v) return null;
  const t = new Date(v).getTime();
  return Number.isFinite(t) ? Math.floor(t / 1000) : null;
}
function fmtWhen(epoch) {
  if (!epoch) return "";
  return new Date(epoch * 1000).toLocaleString([],
    { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

/* ----------------------------------------------------------- Git pane */

function renderGitPane(body, s) {
  if (!s) {
    body.replaceChildren(paneEmpty("Open a session to see its git status."));
    return;
  }
  const kids = [];
  const kv = mk("div", "pane-kv");
  kv.append(kvRow("Branch", s.branch || "not a git branch"));
  kv.append(kvRow("Changes",
    s.dirty ? `${s.dirty} file${s.dirty === 1 ? "" : "s"} changed` : "clean",
    s.dirty ? "warn" : ""));
  kv.append(kvRow("Directory", shortPath(s.cwd || ""), "mono"));
  kids.push(kv);
  const tools = mk("div", "pane-tools");
  if (s.branch || s.dirty) tools.append(paneButton("Review changes", "git-branch", () => openDiff(s)));
  if (s.branch) tools.append(paneButton("Checkpoint now", "arrow-down-to-line", () => checkpointSession(s)));
  kids.push(tools);
  if (s.branch) {
    kids.push(paneP("A checkpoint saves HEAD and the current diff to a file under "
      + ".clique-checkpoints/, so you can see or undo what an agent changed."));
  }
  body.replaceChildren(...kids);
}

/* --------------------------------------------------------- Session info pane */

function renderInfoPane(body, s) {
  if (!s) {
    body.replaceChildren(paneEmpty("Open a session to see its details."));
    return;
  }
  const kv = mk("div", "pane-kv");
  kv.append(kvRow("State", s.alive
    ? (s.command || s.cli_label || s.cli || "running") : "stopped"));
  kv.append(kvRow("Directory", s.cwd || "—", "mono"));
  if (s.project) kv.append(kvRow("Project", s.project));
  if (s.branch) {
    kv.append(kvRow("Branch", s.branch + (s.dirty ? ` · ${s.dirty} changed` : ""),
      s.dirty ? "warn" : ""));
  }
  kv.append(kvRow("CLI", s.cli_label || s.cli || "—"));
  if (s.pid) kv.append(kvRow("PID", String(s.pid), "mono"));
  if (typeof s.rss === "number" && s.rss > 0) kv.append(kvRow("Memory", humanBytes(s.rss)));
  if (s.created) kv.append(kvRow("Up", ago(s.created) || "just now"));
  if (s.activity) kv.append(kvRow("Quiet for", ago(s.activity) || "just now"));
  const tools = mk("div", "pane-tools");
  tools.append(paneButton("Open a file", "chevron-right",
    () => openFileSheet(s.id, s.cwd || ".")));
  body.replaceChildren(kv, tools);
}

/* ----------------------------------------------------------- Export pane */

function renderExportPane(body, s) {
  if (!s) {
    body.replaceChildren(paneEmpty("Open a session to export its scrollback."));
    return;
  }
  const tools = mk("div", "pane-tools");
  tools.append(paneButton("Export scrollback", "download", () => exportScrollback(s)));
  body.replaceChildren(
    paneP("Write this session's whole scrollback to a timestamped text file "
      + "under .clique-exports/ in its directory — a clean log to keep, search "
      + "or share."),
    tools);
}

/* ----------------------------------------------------- panel wiring + resize */

function wireSidePanel() {
  for (const btn of document.querySelectorAll(".railr-btn")) {
    btn.onclick = () => togglePanel(btn.dataset.pane);
  }
  $("#panelClose").onclick = () => closePanel();
  wirePanelResizer();

  $("#remindSet").onclick = () => {
    if (!_remindItem) return;
    const epoch = localInputToEpoch($("#remindAt").value);
    if (!epoch) { toast("Pick a date and time", true); return; }
    setReminder(_remindItem.id, epoch);
    closeRemind();
  };
  $("#remindClear").onclick = () => {
    if (_remindItem) setReminder(_remindItem.id, null);
    closeRemind();
  };
  // Click anywhere off the popover closes it, but not the chip that opened it.
  document.addEventListener("pointerdown", (ev) => {
    const pop = $("#remindPop");
    if (!pop.hidden && !pop.contains(ev.target)
        && !ev.target.closest(".note-remind, .note-act")) {
      closeRemind();
    }
  }, true);
  addEventListener("keydown", (ev) => {
    if (ev.key === "Escape" && !$("#remindPop").hidden) { closeRemind(); return; }
    // Ctrl/Cmd+J toggles the panel, a mirror of Ctrl+B for the sidebar.
    if ((ev.ctrlKey || ev.metaKey) && !ev.shiftKey && !ev.altKey
        && (ev.key === "j" || ev.key === "J")) {
      ev.preventDefault();
      togglePanel(panelPane || "notes");
    }
  });
}

function setPanelWidth(px, persist) {
  const w = Math.min(Math.max(Math.round(px), PANE_MIN), PANE_MAX);
  panelWidth = w;
  document.documentElement.style.setProperty("--panel-w", w + "px");
  if (persist) panelSave();
  return w;
}

function wirePanelResizer() {
  const handle = $("#panelResizer");
  if (!handle) return;
  let frame = 0;
  handle.onpointerdown = (ev) => {
    ev.preventDefault();
    handle.setPointerCapture(ev.pointerId);
    handle.classList.add("dragging");
    document.body.classList.add("resizing");
    // The panel sits between the terminal and the far-right rail, so its width
    // is the distance from the pointer to the rail's inner edge.
    const railW = $("#railR") ? $("#railR").offsetWidth : 38;
    const move = (e) => {
      setPanelWidth((innerWidth - railW) - e.clientX, false);
      if (!frame) {
        frame = requestAnimationFrame(() => {
          frame = 0;
          const entry = terms.get(activeId);
          if (entry) { try { entry.fit.fit(); } catch (err) { /* hidden */ } }
        });
      }
    };
    const up = () => {
      handle.releasePointerCapture(ev.pointerId);
      handle.classList.remove("dragging");
      document.body.classList.remove("resizing");
      handle.onpointermove = null;
      handle.onpointerup = null;
      panelSave();
      packTabs();
      refitAll();
    };
    handle.onpointermove = move;
    handle.onpointerup = up;
  };
  handle.ondblclick = () => { setPanelWidth(PANE_DEFAULT, true); refitAll(); };
  handle.onkeydown = (ev) => {
    const step = ev.shiftKey ? 32 : 8;
    if (ev.key === "ArrowLeft") setPanelWidth(panelWidth + step, true);   // wider
    else if (ev.key === "ArrowRight") setPanelWidth(panelWidth - step, true);
    else return;
    ev.preventDefault();
    refitAll();
  };
}

/* Zen mode: fold away the sidebar, tabs and status bars, leaving the terminal
 * and the prompt. Hiding the chrome resizes the pane, so refit tmux once the
 * layout has settled. The corner button (or the palette) brings it all back. */
function toggleZen(on) {
  const now = document.body.classList.toggle("zen", on);
  // The same settle as the sidebar and the panel: this hides three bars, which
  // is a bigger change to the pane than either, and it used to refit without
  // ever telling tmux the new height.
  afterLayout("pane", settlePane);
  if (now) { const e = terms.get(activeId); if (e && e.term) e.term.focus(); }
  return now;
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

async function setPinned(s, pinned) {
  // Stamped locally first so the row jumps to the top on the click rather than
  // on the next poll, then told to the server like the rest of the settings.
  s.pinned = pinned;
  renderTree();
  await api("api/sessions/" + s.id, {
    method: "PATCH", body: JSON.stringify({ pinned }),
  });
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
    // Close the tab and repaint now — awaiting the kill first left the dead tab
    // and a blank pane on screen until the request came back. `killing`
    // suppresses the "still running" nudge, because it is not.
    closeTab(s.id, false, true);
    try {
      const res = await api("api/sessions/" + s.id + "/kill", { method: "POST", body: "{}" });
      // The server verifies and force-kills; alive:true here means it truly
      // would not die, so say so rather than leave a zombie unmentioned.
      if (res && res.alive) {
        toast(`"${s.name}" would not stop.`, true,
              { label: "Try again", run: () => killSession(session(s.id) || s) });
      } else {
        // Stopped for real. The record and its draft survive, so "undo" is just
        // starting it again — one click, rather than hunting for the greyed row.
        // A resumable CLI comes back where it was; a shell starts fresh in place.
        toast(`Stopped “${s.name}”.`, false,
              { label: "Undo", run: () => openSession(s.id) });
      }
    } catch (err) {
      toast(`Could not stop "${s.name}" — it may still be running.`, true,
            { label: "Retry", run: () => killSession(session(s.id) || s) });
    }
    refresh();
    return;
  }
  if (!confirm(`Remove "${s.name}" from the sidebar?`)) return;
  closeTab(s.id, false, true);
  try {
    await api("api/sessions/" + s.id, { method: "DELETE" });
  } catch (err) {
    toast(`Could not remove "${s.name}" — retry?`, true,
          { label: "Retry", run: () => killSession(session(s.id) || s) });
  }
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
      reviewLockedOf(id) ? 1 : 0,
      s.cwd || "", s.signal || "", s.cli || "",
    ].join("\x1f");
  }).join("\x1e") + "\x1d" + (activeId || "");
}

function renderTabs() {
  // The on-screen key row exists for a phone with a session in front; nothing
  // to send keys to means nothing to show.
  const kr = $("#keyrow"); if (kr) kr.hidden = !activeId;
  const bar = $("#tabs");
  const fp = tabsFingerprint();
  if (fp === tabsFp && bar.childElementCount) {
    applyCliTint();
    packTabs();
    renderInputBar();
    renderSessionLine();   // its facts (branch, activity, uptime) change on the poll, not with the tab set
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
      (workState(s) === "error" ? " error" : "") +
      (reviewLockedOf(id) ? " locked" : "") +
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
  renderSessionLine();
}

/* Say what size the pane is drawn at, but only when that is not the obvious
 * answer.
 *
 * Two things make a pane a size nobody asked for, and both are deliberate.
 * A tmux window has exactly one size shared by every client attached to it,
 * so the browser in front sets it and everyone else draws at that. And a CLI
 * that paints its own prompt box has its columns kept and its picture shrunk,
 * because narrowing the grid is what stacks the box. From the outside both
 * look like a bug: text smaller than you left it, or a band of dead space
 * down one side.
 *
 * A label is the cheapest thing that turns "why does my pane look wrong" into
 * a fact, and it stays quiet when the pane is simply drawn at its own fit —
 * which is almost always. */
function paneSizeNote(id) {
  const entry = terms.get(id);
  const s = session(id);
  if (!entry || !entry.term) return null;
  const cols = entry.term.cols, rows = entry.term.rows;
  if (!claimable(cols, rows)) return null;
  const size = `${cols}x${rows}`;

  /* What tmux is actually painting, straight off the poll. A pane drawing a
   * different grid from the window tmux thinks it has is the case a person
   * cannot fix from where they are standing, so it goes first. Asking the fit
   * addon instead does not work: each browser fits its own box quite happily
   * and has no idea the window underneath belongs to somebody else. */
  /* ...but only from a poll that has actually seen our own last claim. The
   * size arrives on a 3s cycle, so for a moment after this browser asserts a
   * size the poll still holds the previous one, and reading that as somebody
   * else's doing made the label accuse a window that was not there. A fixed
   * delay does not work either: two browsers taking the size off each other
   * both keep claiming, and neither would ever be told. Comparing the two
   * clocks is exact, and it tells whichever browser is *not* winning. */
  const settled = !entry.claimedAt || lastPollAt > entry.claimedAt;
  if (settled && s && s.cols && s.rows && (s.cols !== cols || s.rows !== rows)) {
    return {
      text: `another window set ${s.cols}x${s.rows}`,
      title: `This pane is drawing ${size}, but tmux has the window at ${s.cols}x${s.rows}. `
           + "A tmux window has one size shared by everything attached to it, so "
           + "whichever browser is in front sets it. Click this pane to take it back.",
    };
  }
  const el = entry.term.element;
  if (el && (el.style.transform || "").includes("scale")) {
    return {
      text: `${size} · scaled to fit`,
      title: "This CLI draws its own prompt box, so the columns are kept and the "
           + "picture is shrunk rather than the grid narrowed. Close the side panel "
           + "or widen the window to get the full size back.",
    };
  }
  return null;
}

/* The status line under the tabs: what the session in front is doing, at a
 * glance. Its process, where, which branch, how long it has been up and how
 * long it has been quiet. Every fact already rides on the session in the 3s
 * poll (and renderTabs runs on that poll and on every tab switch), so this
 * needs no clock of its own. Hidden when nothing is open. */
function renderSessionLine() {
  const el = $("#sessionline");
  if (!el) return;
  const s = activeId ? session(activeId) : null;
  if (!s) { el.hidden = true; el.textContent = ""; return; }
  const proc = s.alive ? (s.command || s.cli_label || s.cli || "running") : "stopped";
  const dot =
    !s.alive ? "off"
    : s.state === "error" ? "err"
    : (s.state === "asking" || s.state === "waiting") ? "wait"
    : "ok";
  const parts = [
    `<span class="sl-proc"><i class="sl-dot ${dot}"></i>${escapeHtml(proc)}</span>`,
    `<span class="sl-cwd" title="${escapeHtml(s.cwd || "")}">${escapeHtml(s.project || s.cwd || "")}</span>`,
  ];
  if (s.branch) {
    const dirty = s.dirty ? ` · <span class="git-dirty">${s.dirty} changed</span>` : "";
    parts.push(`<span class="sl-branch">${escapeHtml(s.branch)}${dirty}</span>`);
  }
  const size = paneSizeNote(activeId);
  if (size) {
    parts.push(
      `<span class="sl-size" title="${escapeHtml(size.title)}">${escapeHtml(size.text)}</span>`);
  }
  const tail = [];
  const up = ago(s.created);
  if (up) tail.push(`up ${up}`);
  const seen = ago(s.activity);
  if (seen) tail.push(`quiet ${seen}`);
  if (tail.length) parts.push(`<span class="sl-tail">${escapeHtml(tail.join(" · "))}</span>`);
  el.innerHTML = parts.join("");
  el.hidden = false;
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

  // Read-only review: the prompt goes untypeable, Run/Shell go dead, and the
  // lock lights — so the state, and the way out of it, is one control.
  const locked = reviewLockedOf(activeId);
  $("#inputbar").classList.toggle("locked", locked);
  const box = $("#prompt");
  if (box) {
    box.readOnly = locked;
    box.placeholder = locked ? "Locked for review — unlock to type"
                             : "Type a prompt, or a shell command…";
  }
  for (const sel of ["#run", "#runShell"]) {
    const el = $(sel); if (el) el.disabled = locked;
  }
  const lb = $("#reviewLock");
  if (lb) {
    lb.classList.toggle("on", locked);
    lb.setAttribute("aria-pressed", locked ? "true" : "false");
    lb.title = locked ? "Read-only — click to unlock and type"
                      : "Lock read-only for review";
  }
  const tw = $("#termwrap");
  if (tw) tw.classList.toggle("review-locked", locked);

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

/* The biggest file a drop will send. Mirrors the server's UPLOAD_CAP so an
 * oversize file is refused before it is read and base64'd into memory, not
 * after a 200 MB round trip that was always going to be rejected. */
const DROP_CAP = 10 * 1024 * 1024;

/* Dropping a file onto the window.
 *
 * The drag-and-drop sibling of image paste: a pasted screenshot has no name, a
 * dropped file does — a PDF, a log, a spreadsheet. The bytes go to the open
 * session's working directory under the file's own name, and what comes back is
 * a path the CLI can open, dropped into the prompt exactly like a paste. Every
 * coding CLI already knows how to read a file, so this needs to know nothing
 * about any of them.
 */
async function dropFiles(list) {
  const s = session(activeId);
  if (!s) { toast("Open a session first, then drop the file onto it.", true); return; }
  const files = [...list].filter(Boolean);
  if (!files.length) return;
  for (const file of files) {
    if (file.size > DROP_CAP) {
      toast(`${file.name} is too big to drop in (limit ${Math.round(DROP_CAP / 1048576)} MB).`, true);
      continue;
    }
    try {
      const buf = new Uint8Array(await file.arrayBuffer());
      // Chunked for the same reason paste is: a naive spread into
      // String.fromCharCode blows the argument limit at ~100k bytes.
      let binary = "";
      for (let i = 0; i < buf.length; i += 0x8000) {
        binary += String.fromCharCode.apply(null, buf.subarray(i, i + 0x8000));
      }
      const saved = await api(`api/sessions/${s.id}/upload`, {
        method: "POST",
        body: JSON.stringify({ name: file.name, data: btoa(binary) }),
      });
      const where = deliverPath(saved.path);
      toast(`Saved ${saved.relative} — the path is in ${where}`);
    } catch (err) {
      toast(`Could not save ${file.name}: ${err.message}`, true);
    }
  }
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
  "Drag a file — an image, a PDF, a log — onto the window to hand it to the open session; its path lands in your prompt.",
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

function pathFromText(text) {
  let raw = String(text || "").trim();
  if (!raw || /\s/.test(raw) || raw.length > 1024) return "";
  raw = raw.replace(/^['"`]+|['"`]+$/g, "");
  PATH_RE.lastIndex = 0;
  const match = PATH_RE.exec(/^[~./]/.test(raw) ? raw : (" " + raw));
  if (!match) return "";
  const got = trimPath(match[1]);
  return got === raw ? got : "";
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
  const target = sessionId || activeId;
  if (reviewLockedOf(target)) { hintReviewLocked(); return false; }
  const entry = terms.get(target);
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
let currentFile = null;   // the info the file sheet last rendered, for editing
let fileEditing = false;

function filePathNow() {
  return ($("#filePath").textContent || fileAsked || "").trim();
}

function cliHasTranscript(cliId) {
  const cli = (state.clis || []).find((c) => c.id === cliId);
  return Boolean(cli && cli.transcript);
}

/* Scroll-back for a CLI that keeps none. Claude and Grok draw over the terminal's
 * alternate screen, so their own history is gone the moment it scrolls off — but
 * the transcript on disk has every turn. This reads it into the file sheet: same
 * surface, turns instead of a file. */
/* ----------------------------------------------------------- review changes */

/* See what the agent changed, and say something back about it.
 *
 * The diff is git's own, uncommitted, fetched on demand. The point is the loop:
 * read the change, type a note, and it goes to the agent as its next prompt —
 * the review comment and the follow-up are the same message. No staging, no
 * per-line threading; this is a textarea and the send path that already exists,
 * which is the whole of what a self-hosted panel needs it to be. */
let diffSession = null;

async function openDiff(s) {
  diffSession = s.id;
  $("#diffTitle").textContent = (s.name || "Session") + " — changes";
  $("#diffBody").textContent = "";
  $("#diffFoot").hidden = true;
  $("#diffNote").hidden = false;
  $("#diffNote").textContent = "Reading…";
  $("#diff").hidden = false;
  let data;
  try {
    data = await api("api/sessions/" + encodeURIComponent(s.id) + "/diff");
  } catch (err) {
    if (diffSession === s.id) $("#diffNote").textContent = "Could not read the changes.";
    return;
  }
  if (diffSession !== s.id) return;   // a newer sheet opened while we waited
  renderDiff(data);
}

function renderDiff(data) {
  const body = $("#diffBody");
  const note = $("#diffNote");
  body.textContent = "";
  if (!data.repo) {
    note.hidden = false;
    note.textContent = "This session's folder is not a git repository, so there is nothing to diff.";
    $("#diffFoot").hidden = true;
    return;
  }
  if (data.empty) {
    note.hidden = false;
    note.textContent = "No uncommitted changes — the checkout is clean. You can still send a note below.";
    $("#diffFoot").hidden = false;
    return;
  }
  note.hidden = true;
  body.appendChild(renderUnifiedDiff(data.diff));
  const hidden = (data.untracked_hidden || []).length;
  if (hidden) {
    const more = document.createElement("p");
    more.className = "note";
    more.textContent = `${hidden} more new file${hidden === 1 ? "" : "s"} not shown.`;
    body.appendChild(more);
  }
  $("#diffFoot").hidden = false;
}

function diffLineClass(line) {
  if (line.startsWith("@@")) return "hunk";
  if (line.startsWith("+++") || line.startsWith("---")
      || line.startsWith("index ") || line.startsWith("new file")
      || line.startsWith("deleted file") || line.startsWith("similarity")
      || line.startsWith("rename ") || line.startsWith("old mode")
      || line.startsWith("new mode")) return "meta";
  if (line.startsWith("+")) return "add";
  if (line.startsWith("-")) return "del";
  return "ctx";
}

/* Build the diff as elements, never innerHTML — every line is somebody's code
 * or the agent's output, so it is set as text and cannot become markup. */
function renderUnifiedDiff(text) {
  const frag = document.createDocumentFragment();
  let file = null;
  for (const line of text.split("\n")) {
    if (line.startsWith("diff --git")) {
      file = document.createElement("div");
      file.className = "diff-file";
      const head = document.createElement("div");
      head.className = "diff-file-head";
      const m = line.match(/ b\/(.+)$/);
      head.textContent = m ? m[1] : line.replace(/^diff --git /, "");
      file.appendChild(head);
      frag.appendChild(file);
      continue;
    }
    if (!file) {
      file = document.createElement("div");
      file.className = "diff-file";
      frag.appendChild(file);
    }
    const el = document.createElement("div");
    el.className = "diff-line " + diffLineClass(line);
    el.textContent = line || " ";
    file.appendChild(el);
  }
  return frag;
}

function closeDiff() {
  $("#diff").hidden = true;
  diffSession = null;
}

async function sendDiffComment() {
  const id = diffSession;
  const text = ($("#diffComment").value || "").trim();
  if (!id || !text) return;
  const s = session(id);
  const who = s ? s.name : "session";
  try {
    await api("api/sessions/" + id + "/send",
              { method: "POST", body: JSON.stringify({ text, enter: true }) });
    $("#diffComment").value = "";
    toast(`Sent to "${who}"`);
    closeDiff();
    setTimeout(refresh, 400);
  } catch (err) {
    toast(`Could not reach "${who}" — ${err.message || err}`, true);
  }
}

/* What a session has spent, read from its own transcript on demand. Reuses the
 * file sheet — same "here is something about this session" surface as the
 * conversation, and the numbers line up in its monospace pre. */
async function openUsage(s) {
  fileSession = s.id;
  fileAsked = "\u0000usage:" + s.id;
  $("#fileTitle").textContent = (s.name || "Session") + " — usage";
  $("#filePath").textContent = "token usage";
  resetFileBody();
  $("#fileNote").hidden = false; $("#fileNote").textContent = "Reading…";
  $("#file").hidden = false;
  let data;
  try {
    data = await api("api/sessions/" + encodeURIComponent(s.id) + "/usage");
  } catch (err) {
    if (fileSession === s.id) $("#fileNote").textContent = "Could not read usage.";
    return;
  }
  if (fileSession !== s.id) return;
  showUsage(data);
}

function showUsage(data) {
  const note = $("#fileNote");
  const box = $("#fileText");
  $("#fileTurns").hidden = true;
  $("#fileImg").hidden = true;
  if (!data || !data.has_data) {
    box.hidden = true;
    note.hidden = false;
    note.textContent = "No token usage recorded for this session yet.";
    return;
  }
  const t = data.tokens;
  const n = (x) => (x || 0).toLocaleString();
  note.hidden = true;
  box.hidden = false;
  box.textContent =
    `${n(t.total)} tokens total\n\n`
    + `  input           ${n(t.input)}\n`
    + `  output          ${n(t.output)}\n`
    + `  cache read      ${n(t.cache_read)}\n`
    + `  cache creation  ${n(t.cache_creation)}\n\n`
    + `across ${n(data.messages)} assistant message${data.messages === 1 ? "" : "s"}`;
}

async function openTranscript(s) {
  fileSession = s.id;
  fileAsked = "\u0000transcript:" + s.id;   // no real path collides with this
  $("#fileTitle").textContent = s.name || "Conversation";
  $("#filePath").textContent = "conversation";
  resetFileBody();
  $("#fileNote").hidden = false; $("#fileNote").textContent = "Reading...";
  $("#file").hidden = false;
  let data;
  try {
    data = await api("api/sessions/" + encodeURIComponent(s.id) + "/transcript");
  } catch (err) {
    if (fileSession === s.id) $("#fileNote").textContent = "Could not read the conversation.";
    return;
  }
  if (fileSession !== s.id) return;   // a newer sheet opened while we waited
  showTurns(data);
}

function showTurns(data) {
  const note = $("#fileNote");
  const box = $("#fileTurns");
  const turns = (data && data.turns) || [];
  box.textContent = "";
  $("#fileText").hidden = true;
  $("#fileImg").hidden = true;
  if (!turns.length) {
    box.hidden = true;
    note.hidden = false;
    note.textContent = "No conversation recorded yet for this session.";
    return;
  }
  const cli = (state.clis || []).find((c) => c.id === data.cli);
  const them = (cli && cli.label) || data.cli || "Assistant";
  note.hidden = true;
  for (const t of turns) {
    const turn = document.createElement("div");
    turn.className = "turn " + (t.role === "user" ? "user" : "assistant");
    const who = document.createElement("div");
    who.className = "who";
    who.textContent = t.role === "user" ? "You" : them;
    const body = document.createElement("div");
    body.className = "body";
    body.textContent = t.text || "";
    turn.append(who, body);
    box.append(turn);
  }
  box.hidden = false;
  box.scrollTop = box.scrollHeight;   // land at the newest turn, like the session
}

async function openFileSheet(sessionId, path) {
  const asked = String(path || "").trim();
  if (!sessionId || !asked) return;
  fileSession = sessionId;
  fileAsked = asked;
  $("#fileTitle").textContent = asked.split("/").pop() || asked;
  $("#filePath").textContent = asked;
  resetFileBody();
  $("#fileNote").hidden = false;
  $("#fileNote").textContent = "Looking...";
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

function resetFileBody() {
  $("#fileText").hidden = true;
  $("#fileText").textContent = "";
  $("#fileImg").hidden = true;
  $("#fileImg").removeAttribute("src");
  $("#fileTurns").hidden = true;
  $("#fileTurns").textContent = "";
  const list = $("#fileList");
  if (list) { list.hidden = true; list.textContent = ""; }
  const up = $("#fileUp");
  if (up) up.hidden = true;
}

function fileParentPath(info) {
  const p = String((info && info.path) || "").replace(/\\/g, "/");
  if (!p) return "";
  const parts = p.split("/").filter((bit, i) => bit !== "" || i === 0);
  if (parts.length < 2) return "";
  parts.pop();
  const parent = parts.join("/") || "/";
  return parent === p ? "" : parent;
}

const DIR_LIST_HINT = "200";

function showFileList(entries, truncated) {
  const list = $("#fileList");
  if (!list) return;
  list.textContent = "";
  for (const row of entries || []) {
    const btn = document.createElement("button");
    btn.type = "button";
    const name = document.createElement("span");
    name.className = "name";
    name.textContent = row.name === ".." ? "Parent folder" : row.name;
    const kind = document.createElement("span");
    kind.className = "kind";
    kind.textContent = row.kind === "dir" ? "folder" : "file";
    btn.append(name, kind);
    btn.onclick = () => openFileSheet(fileSession, row.path);
    list.append(btn);
  }
  list.hidden = !list.childElementCount;
  if (truncated) {
    const note = $("#fileNote");
    note.hidden = false;
    note.textContent = "First " + DIR_LIST_HINT + " entries shown.";
  }
}

function showFile(info) {
  $("#fileTitle").textContent = info.name || info.asked || "File";
  $("#filePath").textContent = info.path || info.asked || fileAsked;
  const note = $("#fileNote");
  const text = $("#fileText");
  const img = $("#fileImg");
  resetFileBody();
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
    const entries = info.entries || [];
    if (entries.length) {
      showFileList(entries, info.truncated);
    } else {
      note.hidden = false;
      note.textContent = "That folder is empty.";
    }
  } else if (info.kind === "binary") {
    note.hidden = false;
    note.textContent = "Not text. Copy or send the path to open it in something that can.";
  } else {
    note.hidden = false;
    note.textContent = "Nothing at that path from this session's directory.";
  }

  const parent = (info.kind === "dir") ? "" : fileParentPath(info);
  const up = $("#fileUp");
  if (up) up.hidden = !parent;

  // Editing rides the preview: only an untruncated text file offers Edit (a
  // truncated one would save back a fraction of itself), and never a directory,
  // image, or credential. The server refuses those regardless.
  currentFile = info;
  fileEditing = false;
  $("#fileEdit").hidden = true;
  $("#fileSave").hidden = true;
  $("#fileCancel").hidden = true;
  $("#fileEditBtn").hidden = !fileEditable(info);
  $("#fileSend").hidden = false;
  $("#fileCopy").hidden = false;
}

function fileEditable(info) {
  return !!(info && info.kind === "text" && !info.truncated && info.path);
}

function startFileEdit() {
  if (!fileEditable(currentFile)) return;
  fileEditing = true;
  const ta = $("#fileEdit");
  ta.value = currentFile.text || "";
  ta.hidden = false;
  $("#fileText").hidden = true;
  $("#fileNote").hidden = true;
  if ($("#fileList")) $("#fileList").hidden = true;
  if ($("#fileUp")) $("#fileUp").hidden = true;
  for (const sel of ["#fileEditBtn", "#fileSend", "#fileCopy"]) $(sel).hidden = true;
  $("#fileSave").hidden = false;
  $("#fileCancel").hidden = false;
  ta.focus();
}

function cancelFileEdit() {
  fileEditing = false;
  $("#fileEdit").hidden = true;
  if (currentFile) showFile(currentFile);   // back to the preview we came from
}

async function saveFileEdit() {
  if (!fileEditing || !currentFile) return;
  const text = $("#fileEdit").value;
  const save = $("#fileSave");
  save.disabled = true;
  try {
    await api("api/sessions/" + encodeURIComponent(fileSession) + "/file", {
      method: "POST",
      body: JSON.stringify({ path: currentFile.path || fileAsked, text }),
    });
    // Reflect what is now on disk, then drop back to the preview.
    currentFile = { ...currentFile, text, size: new TextEncoder().encode(text).length };
    fileEditing = false;
    $("#fileEdit").hidden = true;
    showFile(currentFile);
    toast("Saved " + (currentFile.name || "file"));
  } catch (err) {
    toast("Could not save — " + (err.message || err), true);
  } finally {
    save.disabled = false;
  }
}

function closeFileSheet() {
  $("#file").hidden = true;
  resetFileBody();
  $("#fileEdit").hidden = true;
  $("#fileEdit").value = "";
  fileEditing = false;
  currentFile = null;
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

/* A real renderer, and the GPU by default.
 *
 * xterm's default DOM renderer draws one element per run of text, so a screen
 * redrawing under a live selection — an interactive menu being arrowed through
 * — leaves fragments of the old text behind. Stale nodes, not a font or width
 * problem. A full-repaint renderer has nothing left over to show. WebGL does
 * that repaint on the client's GPU (the work is in your browser, never on the
 * server, so a GPU-less server is fine), and it is a real, felt speed-up.
 *
 * The one catch is that WebGL is a per-terminal GPU context and a browser
 * allows only ~16 at once; open more panes than that and the browser drops the
 * oldest pane's context. That is handled, not feared: the losing pane — always
 * a background one — falls back to canvas on the spot (onContextLoss), so
 * nothing you are looking at ever breaks. If losses keep happening we take the
 * hint and offer to turn the GPU off for steadier drawing (see noteWebglLoss).
 *
 * The renderer is kept on the term so closeTab can dispose it before the
 * terminal — a renderer disposed inside term.dispose() throws and used to
 * abort the teardown. */
function attachRenderer(term) {
  if (gpuEnabled() && window.WebglAddon) {
    try {
      const webgl = new WebglAddon.WebglAddon();
      webgl.onContextLoss(() => {
        if (term._cliqueRenderer !== webgl) return;   // already replaced
        try { webgl.dispose(); } catch (e) { /* already gone */ }
        term._cliqueRenderer = null;
        attachCanvas(term);   // a background pane; the visible one keeps its context
        noteWebglLoss();
      });
      term.loadAddon(webgl);
      term._cliqueRenderer = webgl;
      return;
    } catch (err) {
      /* no WebGL2 here — fall through to canvas */
    }
  }
  attachCanvas(term);
}

function attachCanvas(term) {
  if (!window.CanvasAddon) return;   // the DOM renderer stays
  try {
    const canvas = new CanvasAddon.CanvasAddon();
    term.loadAddon(canvas);
    term._cliqueRenderer = canvas;
  } catch (err) {
    /* the DOM renderer stays */
  }
}

/* GPU rendering is per device — a desktop has one, an old phone may not, and
 * the choice must not follow the person across machines the way the server's
 * settings do. So it lives in localStorage, and is on by default. */
function gpuEnabled() {
  try { return localStorage.getItem("clique.gpu") !== "0"; } catch (e) { return true; }
}

function setGpu(on) {
  try { localStorage.setItem("clique.gpu", on ? "1" : "0"); } catch (e) { /* private mode */ }
  // Applied live to every open pane, not just new ones — the toggle is a
  // visible before/after. Dispose whatever each holds, re-attach, repaint.
  for (const entry of terms.values()) {
    if (entry.closing) continue;
    try {
      if (entry.term._cliqueRenderer) {
        try { entry.term._cliqueRenderer.dispose(); } catch (e) { /* gone */ }
        entry.term._cliqueRenderer = null;
      }
      attachRenderer(entry.term);
      entry.term.refresh(0, entry.term.rows - 1);
    } catch (e) { /* one bad pane must not stop the rest */ }
  }
}

/* Detecting that the GPU is not coping, and saying so once.
 *
 * A lost context now and then is normal churn past the ~16-pane cap and is
 * handled silently. But if they keep coming — a weak GPU, a driver that resets,
 * far more panes than the cap — canvas is the steadier choice, so offer it.
 * Once ever (a flag in localStorage): a panel that keeps nagging is one people
 * stop reading, and the toggle in Settings is always there to change back. */
let webglLosses = 0;

function noteWebglLoss() {
  webglLosses += 1;
  if (webglLosses < 3 || !gpuEnabled()) return;
  try { if (localStorage.getItem("clique.gpuAdvised") === "1") return; } catch (e) { /* no store */ }
  try { localStorage.setItem("clique.gpuAdvised", "1"); } catch (e) { /* no store */ }
  toast("GPU rendering keeps dropping on this device — canvas is steadier with "
        + "this many panes. Turn the GPU off?", true,
        { label: "Turn off GPU", run: () => setGpu(false) });
}

/* Server output is coalesced onto one write per animation frame, not one per
 * WebSocket frame. A build that floods — a `yes` loop, a giant diff, a screen
 * cleared and redrawn fast — can deliver hundreds of small frames a second,
 * and writing each on arrival is hundreds of parser+render passes a second:
 * that is the scroll jank you feel with many live panes. Queue the bytes and
 * flush the whole burst in one write next frame — xterm keeps its own order,
 * we just hand it more at once. Everything written while detached lands in the
 * buffer as usual; the viewport correction — once per flush now, not per
 * frame — runs in the write callback, the first moment the new lines exist. */
function writeOut(entry, id, data) {
  (entry.wq || (entry.wq = [])).push(data);
  if (entry.wqRAF || entry.wqTimer) return;   // a flush is already scheduled
  const flush = () => flushWrites(entry, id);
  entry.wqRAF = requestAnimationFrame(flush);
  // Safety net. requestAnimationFrame is starved when the page produces no
  // frames — a backgrounded tab, a headless context — and rAF alone would
  // leave the queue unflushed and the pane looking frozen. Whichever of the
  // two fires first flushes; flushWrites cancels the other.
  entry.wqTimer = setTimeout(flush, 100);
}

function flushWrites(entry, id) {
  if (entry.wqRAF) { cancelAnimationFrame(entry.wqRAF); entry.wqRAF = 0; }
  if (entry.wqTimer) { clearTimeout(entry.wqTimer); entry.wqTimer = 0; }
  if (entry.closing) return;            // the term may already be disposed
  const q = entry.wq;
  if (!q || !q.length) return;
  entry.wq = [];
  const data = q.length === 1 ? q[0] : coalesceChunks(q);
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

/* Terminal output arrives as binary frames (arraybuffer -> Uint8Array); a
 * string only turns up if the server sends a text frame, which it does not.
 * Concatenate a run of byte chunks into one buffer, join a run of strings,
 * and in the mixed case (effectively never) encode the strings so it is still
 * a single write. */
function coalesceChunks(chunks) {
  if (chunks.every((c) => typeof c === "string")) return chunks.join("");
  const enc = new TextEncoder();
  const parts = chunks.map((c) => (typeof c === "string" ? enc.encode(c) : c));
  let total = 0;
  for (const p of parts) total += p.length;
  const out = new Uint8Array(total);
  let off = 0;
  for (const p of parts) { out.set(p, off); off += p.length; }
  return out;
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
  /* Measure from where the terminal actually starts, not from the left of its
   * box: the two differ by the pane's padding, and scaling to the wrong one of
   * them is how the picture ended up a few pixels wider than the room it had. */
  const box = entry.el.getBoundingClientRect();
  const start = entry.term.element ? entry.term.element.getBoundingClientRect().left : box.left;
  const availW = Math.max(0, box.right - start);
  const scale = paneWidthScale(availW, entry.term.cols, cell.w);
  if (paneShouldZoom(boxed, scale)) {
    // Shrinking the picture shrinks every cell with it, so more rows fit in
    // the height than did before, and the pane has to take them. Without
    // this the panel opened, the grid scaled down to clear it, and the
    // bottom fifth of the terminal went dead black. Columns are left exactly
    // where they are: keeping them is the entire reason this zooms.
    const fitRows = Math.floor(entry.el.clientHeight / (cell.h * scale));
    if (fitRows >= 4 && fitRows !== entry.term.rows) {
      // Resize unscaled, so the terminal measures itself in honest pixels,
      // and put the transform back afterwards.
      applyPaneZoom(entry.term, 1);
      entry.relaying = true;
      try {
        entry.term.resize(entry.term.cols, fitRows);
      } catch (err) { /* not laid out yet */ } finally {
        entry.relaying = false;
      }
    }
    applyPaneZoom(entry.term, scale,
                  entry.term.cols * cell.w, entry.term.rows * cell.h);
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
  renderSidePanel();   // re-scope the open pane to the session in front
  saveWorkspace();
  landFocus();
  // On a phone, opening a session slides the drawer shut so the pane is in front.
  if (isMobile() && !$("#sidebar").hidden) setSidebar(false);
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

function closeTab(id, silent, killing) {
  const closed = session(id);
  const entry = terms.get(id);
  if (entry) {
    entry.closing = true;
    try { if (entry.wqRAF) cancelAnimationFrame(entry.wqRAF); } catch (err) { /* nothing queued */ }
    try { if (entry.wqTimer) clearTimeout(entry.wqTimer); } catch (err) { /* nothing queued */ }
    try { if (entry.ws) entry.ws.close(); } catch (err) { /* already closing */ }
    // Dispose the renderer addon while the terminal is still whole; disposing
    // it inside term.dispose() throws and used to abort the whole teardown.
    // Then dispose the term, guarded, so nothing a renderer does can stop the
    // tab from closing or the kill that follows from firing.
    try {
      if (entry.term._cliqueRenderer) {
        entry.term._cliqueRenderer.dispose();
        entry.term._cliqueRenderer = null;
      }
    } catch (err) { /* renderer already gone */ }
    try { entry.term.dispose(); } catch (err) { /* going away regardless */ }
    try { entry.el.remove(); } catch (err) { /* already detached */ }
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
    // Not on a kill: the session is being stopped, not left running, so the
    // "still running" nudge would be a lie and its offer moot.
    if (!killing && closed && closed.alive) {
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
  /* `touch-action: none` is what makes a finger reach the scroll handler
   * below at all. Without it the browser claims a vertical drag for the
   * native scroll of `.xterm-viewport`, whose scroll area is exactly one
   * screen tall because xterm renders the buffer itself, so the gesture is
   * swallowed to move something that cannot move and the pane never gets a
   * touchmove. Measured: one touchstart, zero touchmoves. The cost is
   * pinch-zoom on the pane, which a terminal has its own font control for. */
  host.style.cssText = "position:absolute;inset:0;padding:6px 8px;" +
    "pointer-events:none;touch-action:none";
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
    // A hollow cursor in the pane you are not typing into. With several panes
    // open, two solid blocks both look like the live one.
    cursorInactiveStyle: "outline",
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

  /* The canvas renderer (see attachRenderer). After `open` because it binds to
   * the element the terminal just made. */
  attachRenderer(term);

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
    // Read-only review: swallow the keystroke rather than send it, and say why.
    if (reviewLockedOf(id)) { hintReviewLocked(); return; }
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
  term.onResize(() => {
    if (entry.kicking) return;     // paintPane's one-column nudge is not a resize
    if (entry.relaying) return;    // our own fit; it reports once it settles

    /* The grid changed shape without the box changing, so the ResizeObserver
     * that normally drives a re-layout never fires. It matters because a boxed
     * CLI's zoom is computed from its column count: leave it alone and the
     * scale stays the one that suited the grid we no longer have. That is what
     * a second browser does to this one. It resizes the shared tmux window,
     * this pane's grid follows, the scale does not, and the terminal ends up
     * drawn at half size in a full-width box with xterm's scrollbar floating
     * in the open space where the picture stopped. */
    entry.relaying = true;
    try { layoutPane(entry); } finally { entry.relaying = false; }

    // Report where it settled, not the size that came in: laying out may have
    // fitted again on top of it.
    const cols = entry.term.cols, rows = entry.term.rows;
    if (id !== activeId) return;   // a hidden tab must not resize the window
    if (document.hidden || !document.hasFocus()) return;
    if (!claimable(cols, rows)) return;
    if (entry.ws && entry.ws.readyState === 1) {
      entry.claimedAt = Date.now();
      entry.ws.send(JSON.stringify({ type: "resize", cols, rows, handheld: handheld() }));
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

/* Give an untouched session a name from the first thing you send it, so a tray
 * of "tmp" and "shell" turns into the work you're actually doing. Only ever
 * fills in a name that is still auto-derived — the directory basename, the CLI,
 * or empty — so the moment you name one yourself this leaves it alone; and the
 * rename makes the name no longer auto, so it is one-shot by construction. */
function autoTitleFrom(text) {
  const first = String(text || "").split("\n")[0].replace(/\s+/g, " ").trim();
  return first.split(" ").slice(0, 8).join(" ").slice(0, 48).replace(/[\s.,;:!?]+$/, "");
}
function maybeAutoTitle(sessionId, text) {
  const s = session(sessionId);
  if (!s) return;
  const base = (s.cwd || "").split("/").filter(Boolean).pop() || "";
  const isAuto = !s.name || s.name === base || s.name === s.cli || s.name === s.cli_label;
  if (!isAuto) return;
  const title = autoTitleFrom(text);
  if (title.replace(/\s/g, "").length < 6 || title === s.name) return;  // too thin to name by
  s.name = title;   // locally first, so the sidebar turns over on the keystroke
  renderTree();
  // If the write fails, the next poll simply restores the server's name.
  api("api/sessions/" + sessionId, { method: "PATCH", body: JSON.stringify({ name: title }) })
    .catch(() => {});
}

/* True to go ahead: either nothing matched, or the confirm was accepted. */
async function okToSend(text, whoLabel) {
  const hit = destructiveHit(text);
  if (!hit) return true;
  return confirmAction({
    title: "This looks destructive",
    message: `Matched “${hit}”. Send it to ${whoLabel}?`,
    detail: String(text).trim(),
    okLabel: "Send anyway", danger: true,
  });
}

async function run(text) {
  if (!text.trim() || !activeId) return;
  if (reviewLockedOf(activeId)) { hintReviewLocked(); return; }
  const who = session(activeId);
  if (!await okToSend(text, `“${who ? who.name : "this session"}”`)) return;
  for (let i = 0; i < repeat; i++) {
    if (!control({ type: "run", text, enter: true })) {
      await api(`api/sessions/${activeId}/send`, {
        method: "POST", body: JSON.stringify({ text, enter: true }),
      });
    }
  }
  maybeAutoTitle(activeId, text);
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
  if (reviewLockedOf(activeId)) { hintReviewLocked(); return; }
  if (!await okToSend(text, "a shell for this directory")) return;
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
    $("#wtRow").hidden = true;   // re-shown below only when cwd is a git repo
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

    // A worktree only makes sense in a git repo; look.branch is set for one.
    $("#wtRow").hidden = !look.branch;

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

/* `paths` may be plain strings or {value, label} pairs. A datalist option
 * shows its text content beside the value it would insert, which is how a
 * project can say "wsg-sentinel" while still filling the field with the path
 * nobody remembers. */
function fillDatalist(paths) {
  const list = $("#cwdList");
  list.textContent = "";
  for (const item of paths) {
    const option = document.createElement("option");
    option.value = typeof item === "string" ? item : item.value;
    if (typeof item !== "string" && item.label) option.textContent = item.label;
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
    /* Not a path, so it is a name. The two suggestions the dialog already had
     * both assume you know something: the dropdown knows where you have been,
     * and the completion below needs the first few characters of the path.
     * Neither answers "the one called sentinel", which on a box with forty
     * repos across three parent directories is the actual question. */
    const known = knownDirs()
      .filter((d) => !text || d.cwd.toLowerCase().includes(text.toLowerCase()))
      .map((d) => d.cwd);
    if (text.trim().length < 2) return fillDatalist(known);
    browseTimer = setTimeout(async () => {
      let found = [];
      try {
        found = (await api("api/projects?q=" + encodeURIComponent(text))).projects || [];
      } catch {
        return fillDatalist(known);   // never worth interrupting a launch over
      }
      if ($("#newForm").cwd.value.trim() !== text) return;   // they typed on
      // Somewhere you have already worked stays ahead of a fresh find: it is
      // the better guess, and the search is what covers the case it misses.
      const seen = new Set(known);
      fillDatalist([
        ...known.map((cwd) => ({ value: cwd, label: cwd })),
        ...found.filter((p) => !seen.has(p.path))
          .map((p) => ({ value: p.path, label: p.name + " · " + p.path })),
      ]);
    }, 200);
    return;
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
  $("#wtToggle").checked = false;
  $("#wtBranch").value = "";
  $("#wtBranch").hidden = true;
  $("#wtRow").hidden = true;
  form.cwd.oninput = () => { checkWorkspace(); browseFrom(form.cwd.value.trim()); };
  checkWorkspace();
  showCeilingHint();
  $("#modal").hidden = false;
  form.name.focus();
}

/* The soft ceiling, shown in the New-Session form only when we are near it.
 * Purely informational — Start is never disabled; the guard's whole promise is
 * that it warns and gets out of the way. */
function showCeilingHint() {
  const el = $("#modalCeiling");
  if (!el) return;
  const g = (state.stats || {}).guard || {};
  const ceil = g.ceiling || 0;
  const running = g.sessions ?? (state.sessions || []).filter((s) => s.alive).length;
  if (ceil && (g.level !== "ok" || running >= ceil - 1)) {
    el.textContent =
      `This box comfortably runs ~${ceil} agent${ceil === 1 ? "" : "s"} — ${running} running now.`;
    el.hidden = false;
  } else {
    el.hidden = true;
  }
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

/* Blend two hex colours. Used to derive the quieter terminal tokens rather
 * than making every theme spell them out and get one of them wrong. */
function mix(from, to, amount) {
  const read = (hex) => {
    const value = hex.replace("#", "");
    const full = value.length === 3 ? [...value].map((c) => c + c).join("") : value;
    return [0, 2, 4].map((i) => parseInt(full.slice(i, i + 2), 16));
  };
  const [a, b] = [read(from), read(to)];
  const channel = (i) => Math.round(a[i] + (b[i] - a[i]) * amount);
  return "#" + [0, 1, 2].map((i) => channel(i).toString(16).padStart(2, "0")).join("");
}

/* The terminal tokens a theme should not have to spell out.
 *
 * Same bargain as `derived()` makes for the panel: a theme stays one block in
 * themes.js, and anything that follows mechanically from what it already said
 * is worked out here. Three of them, each fixing something a theme could not
 * have got right by hand:
 *
 * `cursorAccent` is the character *underneath* a block cursor. Left unset it
 * falls back to xterm's own default rather than this theme's background, so
 * the character under the cursor could come out invisible.
 *
 * `selectionForeground` is picked from the luminance of the selection colour,
 * because a theme with a pale selection and one with a dark selection cannot
 * both use the same text colour on top of it, and dragging over a line you
 * then cannot read is not a selection.
 *
 * `selectionInactiveBackground` is the same selection blended halfway back to
 * the background, so the pane you are not looking at holds its selection
 * without competing with the one you are. */
function termTokens(theme) {
  const term = theme.term || {};
  const out = { ...term };
  if (term.background && !out.cursorAccent) out.cursorAccent = term.background;
  if (term.selectionBackground) {
    if (!out.selectionForeground) {
      out.selectionForeground = luminance(term.selectionBackground) > 0.4
        ? "#101010" : "#f5f5f5";
    }
    if (!out.selectionInactiveBackground && term.background) {
      out.selectionInactiveBackground =
        mix(term.selectionBackground, term.background, 0.5);
    }
  }
  return out;
}

function termTheme(theme) {
  let built = _termThemes.get(theme);
  if (!built) {
    built = termTokens(theme);
    if (theme.tint_greys) built.extendedAnsi = extendedAnsi(theme);
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

/* ------------------------------------------------------------------ theme art
 *
 * A theme may carry a figure, watermarked into the bottom-right of the pane.
 * Two ways to say what it is, and a theme picks one:
 *
 *   art: { src: "art/plumber.png" }        a drawing we ship
 *   art: { w, h, pal, rows }               a grid drawn in this file
 *
 * The grid form becomes one SVG data URI, built once per theme and cached; the
 * file form is just a URL. Either way it ends up as a background-image, so a
 * repaint costs the browser nothing and there is no second element in the
 * terminal's way.
 *
 * The two are composited differently and that is not a detail. A grid is drawn
 * to a palette we control, every colour a mid-tone, so it can use the extreme
 * blends below and keep the text perfectly untouched. A supplied drawing has
 * blacks and whites in it, and an extreme blend eats exactly those, so it is
 * laid over at a lower opacity instead. That costs the text a little contrast
 * where the two overlap, which is the honest trade for taking artwork as it
 * comes rather than dictating a palette to whoever drew it.
 *
 * Runs of the same colour on a row collapse into one rect, which takes a
 * fourteen-wide figure from a couple of hundred rects to about forty. That
 * matters only because the whole thing then fits in a URI small enough to sit
 * in a style property without being worth a file.
 *
 * It sits *above* the terminal in the stack and still reads as being behind
 * the text, which is the trick worth understanding: the layer is composited
 * with `lighten` on a dark theme and `darken` on a light one. Both are
 * per-channel extremes, so wherever a glyph is painted the glyph wins the
 * comparison and comes through untouched, and the figure only fills the space
 * between. Text stays exactly as legible as it was with no figure at all.
 * Painting it underneath instead would mean making the terminal's own
 * background transparent, which costs a renderer path we would rather not own.
 */
const _artUrls = new Map();

function themeArt(theme) {
  let built = _artUrls.get(theme);
  if (built) return built;
  const art = theme && theme.art;
  built = { url: "", ratio: 0, blend: "" };
  if (art && art.src) {
    // No ratio: the box is fixed and the drawing is contained inside it,
    // anchored bottom-right, so a tall figure and a wide one both sit in the
    // corner properly without the theme having to measure anything.
    built = { url: `url("${encodeURI(art.src)}")`, ratio: 0, blend: art.blend || "normal" };
  } else if (art && art.rows && art.rows.length) {
    const pal = art.pal || {};
    const w = art.w || art.rows[0].length;
    const h = art.h || art.rows.length;
    const rects = [];
    art.rows.forEach((row, y) => {
      let x = 0;
      while (x < row.length) {
        const ch = row[x];
        let run = 1;
        while (x + run < row.length && row[x + run] === ch) run++;
        if (pal[ch]) {
          rects.push(`<rect x="${x}" y="${y}" width="${run}" height="1" fill="${pal[ch]}"/>`);
        }
        x += run;
      }
    });
    if (rects.length) {
      const svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ' +
        `${w} ${h}" shape-rendering="crispEdges">${rects.join("")}</svg>`;
      built = {
        url: `url("data:image/svg+xml,${encodeURIComponent(svg)}")`,
        ratio: w / h, blend: art.blend || "auto",
      };
    }
  }
  _artUrls.set(theme, built);
  return built;
}

/* Put the current theme's figure in the corner, or take it away.
 *
 * Opacity differs by base on purpose. `lighten` on a dark pane adds light to
 * a nearly black field and is seen readily; `darken` on a white one has more
 * headroom before it starts competing with the text, and the same number
 * looks like a stain rather than a watermark. */
function paintThemeArt(theme) {
  const el = $("#themeArt");
  if (!el) return;
  const light = (theme.base || "dark") === "light";
  const { url, ratio, blend } = state.settings.theme_art === false
    ? { url: "", ratio: 0, blend: "" } : themeArt(theme);
  el.hidden = !url;
  if (!url) return;
  el.style.backgroundImage = url;
  // A grid knows its own shape and gets an aspect ratio. A drawing is
  // contained in the fixed box instead, and must not be smoothed away by the
  // pixelated rendering the grids need.
  el.classList.toggle("is-drawing", !ratio);
  el.style.aspectRatio = ratio ? String(ratio) : "";
  el.style.mixBlendMode = blend === "normal" ? "normal" : light ? "darken" : "lighten";
  el.style.opacity = blend === "normal" ? (light ? "0.10" : "0.12") : light ? "0.10" : "0.13";
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
  // The powerline arrows and icons a lot of agent CLIs draw live in the
  // Private Use Area, which ordinary monospace fonts leave as tofu boxes. A
  // Nerd Font carries them; this picks whichever one you have installed and
  // still falls back to a real monospace so the grid holds if you have none.
  { id: "nerd",     label: "Nerd Font (if installed)",
    stack: '"JetBrainsMono Nerd Font", "FiraCode Nerd Font", "Hack Nerd Font", "MesloLGS NF", "CaskaydiaCove Nerd Font", "Symbols Nerd Font", ui-monospace, Menlo, "DejaVu Sans Mono", monospace' },
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
  // Both ways of choosing a theme have to agree afterwards: picking from the
  // dropdown relabels the rows, and pressing Use in a row moves the dropdown.
  if (!$("#settings").hidden) renderThemeMaker();
  root.style.setProperty("--font-panel", (s.font_panel || 13) + "px");
  paintFontChrome();
  paintThemeArt(theme);

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
  // A lock on a session that no longer exists is stale; a reaped-but-kept
  // session still has a record here, so its lock survives a resume.
  let unlockedAny = false;
  for (const id of [...reviewLocked]) if (!live.has(id)) { reviewLocked.delete(id); unlockedAny = true; }
  if (unlockedAny) persistReviewLocked();

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

function fmtSize(n) {
  if (n < 1024) return n + " B";
  if (n < 1024 * 1024) return (n / 1024).toFixed(n < 10240 ? 1 : 0) + " KB";
  if (n < 1024 * 1024 * 1024) return (n / 1048576).toFixed(1) + " MB";
  return (n / 1073741824).toFixed(2) + " GB";
}

/* The storage readout in Settings → Images. Fetched when the sheet opens and
 * after a purge, not on the poll: it scandirs every session's scratch folders,
 * which is cheap but not free, and nothing about it changes second to second. */
async function refreshStorage() {
  const line = $("#storageUsage");
  if (!line) return;
  try {
    const u = await api("api/storage");
    line.textContent = u.files
      ? `${u.files} shared file${u.files === 1 ? "" : "s"} using ${fmtSize(u.bytes || 0)} on disk.`
      : "No shared files stored right now.";
  } catch {
    line.textContent = "Could not read storage usage.";
  }
}

/* Themes made here, as opposed to the presets that ship in themes.js.
 *
 * They are merged into the same map the presets live in, keyed by id, which is
 * the whole integration: `currentTheme()`, the picker and the palette all read
 * that map and none of them need to know where a theme came from. Kept out of
 * the 3s poll on purpose — a theme is a couple of dozen colours and changes
 * about twice a year, so it is fetched at boot and again when one changes. */
let customThemes = new Set();
let canGenerateThemes = false;

async function loadThemes() {
  let payload;
  try {
    payload = await api("api/themes");
  } catch (err) {
    return;   // the presets still work; a missing list is not worth a toast
  }
  const themes = window.CLIQUE_THEMES || (window.CLIQUE_THEMES = {});
  // Drop the ones we added last time before merging, so a theme deleted on
  // another device disappears here rather than lingering until a reload.
  for (const id of customThemes) delete themes[id];
  customThemes = new Set();
  for (const theme of payload.themes || []) {
    if (!theme || !theme.id) continue;
    themes[theme.id] = theme;
    customThemes.add(theme.id);
  }
  canGenerateThemes = Boolean(payload.can_generate);
  applySettings();               // the theme in use may have just arrived
  if (!$("#settings").hidden) renderThemeMaker();
}

/* The list of themes made here, each with a way to remove it. Only these get
 * a delete: a preset is not ours to take away. */
/* Grouped rather than marked with a glyph.
 *
 * Which themes come with a character is not guessable from the name, and a
 * marker character next to the ones that do would need a legend somewhere to
 * say what it meant. An `optgroup` says it in words, costs nothing, is a
 * native control so it survives a phone and a screen reader, and hides behind
 * no hover or right-click.
 *
 * The order does not move: themes.js already declares the plain presets, then
 * the ones with a figure, and loadThemes merges anything made here onto the
 * end. The groups fall on those boundaries exactly, so muscle memory for where
 * a theme sits in the list is untouched. */
function fillThemeSelect() {
  const select = $("#setTheme");
  if (!select) return;
  const chosen = state.settings.theme || "";
  select.replaceChildren();
  const groups = new Map();
  const groupFor = (label) => {
    let group = groups.get(label);
    if (!group) {
      group = document.createElement("optgroup");
      group.label = label;
      groups.set(label, group);
      select.appendChild(group);
    }
    return group;
  };
  for (const [id, theme] of Object.entries(window.CLIQUE_THEMES || {})) {
    const option = document.createElement("option");
    option.value = id;
    option.textContent = theme.label;
    option.selected = id === chosen;
    // Made here first: one of those could carry art one day, and it would
    // still belong with the rest of yours rather than filed by a feature.
    groupFor(customThemes.has(id) ? "Made here"
             : theme.art ? "With a character" : "Presets").appendChild(option);
  }
  select.value = chosen;
}

function renderThemeMaker() {
  fillThemeSelect();   // picking from a row has to move the picker too
  const note = $("#themeGenNote");
  const gen = $("#themeGen");
  if (!note || !gen) return;
  gen.disabled = !canGenerateThemes;
  note.textContent = canGenerateThemes
    ? "Describe a mood. A model picks the colours that need taste; the rest are worked out and checked for contrast."
    : "Add a model provider under Models first, and point the theme feature at it.";

  const mine = $("#themeMine");
  if (!mine) return;
  mine.replaceChildren();
  const themes = window.CLIQUE_THEMES || {};
  for (const id of customThemes) {
    const theme = themes[id];
    if (!theme) continue;
    const row = mk("div", "theme-row");
    const swatch = mk("span", "theme-swatch");
    swatch.style.background = (theme.panel || {}).bg || "#000";
    swatch.style.borderColor = (theme.panel || {}).accent || "#888";
    const name = mk("span", "theme-name");
    name.textContent = theme.label || id;
    const use = mk("button", "theme-use");
    use.type = "button";
    use.textContent = (state.settings.theme || "") === id ? "in use" : "Use";
    use.disabled = (state.settings.theme || "") === id;
    use.onclick = () => saveSettings({ theme: id }).then(renderThemeMaker);
    const drop = mk("button", "theme-drop");
    drop.type = "button";
    drop.title = "Delete this theme";
    drop.textContent = "\u00d7";
    drop.onclick = async () => {
      try {
        await api(`api/themes/${encodeURIComponent(id)}/delete`, { method: "POST" });
      } catch (err) {
        return toast("Could not delete it: " + (err.message || err), true);
      }
      await refresh();      // the setting may have fallen back to the default
      await loadThemes();
      renderThemeMaker();
    };
    row.append(swatch, name, use, drop);
    mine.appendChild(row);
  }
}

async function generateTheme() {
  const box = $("#themePrompt");
  const gen = $("#themeGen");
  const wanted = (box.value || "").trim();
  if (!wanted) return box.focus();
  gen.disabled = true;
  const was = gen.textContent;
  gen.textContent = "Making it\u2026";
  try {
    const made = await api("api/themes/generate", {
      method: "POST", body: JSON.stringify({ prompt: wanted }),
    });
    box.value = "";
    await loadThemes();
    await saveSettings({ theme: made.id });   // made it, so wear it
    toast(`"${made.label}" is on`);
  } catch (err) {
    toast(err.message || String(err), true);
  } finally {
    gen.textContent = was;
    gen.disabled = false;
    renderThemeMaker();
  }
}

function openSettings() {
  const s = state.settings;
  renderSupport();   // About is one click away, so the list has to be there

  fillThemeSelect();
  renderThemeMaker();
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

  $("#setGpu").checked = gpuEnabled();
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
  $("#setThemeArt").checked = s.theme_art !== false;
  $("#setCliWatermark").checked = s.cli_watermark !== false;
  $("#setArtShow").checked = s.artifacts_show !== false;
  // Not repainted while it has focus: this is a textarea someone types a list
  // into, and a poll landing mid-edit would move their cursor.
  if (document.activeElement !== $("#setProjectRoots")) {
    $("#setProjectRoots").value = (s.project_roots || []).join("\n");
  }
  if (document.activeElement !== $("#setArtDirs")) {
    $("#setArtDirs").value = (s.artifact_dirs || []).join("\n");
  }
  const cleanupDays = Number(s.drop_cleanup_days || 0);
  $("#setDropCleanup").checked = cleanupDays > 0;
  const daysBox = $("#setDropCleanupDays");
  daysBox.disabled = cleanupDays <= 0;
  if (document.activeElement !== daysBox) daysBox.value = cleanupDays > 0 ? cleanupDays : 14;
  refreshStorage();
  $("#setFlash").checked = s.notify_flash !== false;
  $("#setServices").checked = s.service_status !== false;
  $("#setSound").checked = !!s.notify_sound;
  $("#setConfirmDestructive").checked = s.confirm_destructive !== false;
  // Not repainted mid-edit: a poll landing while you type would jump the cursor.
  if (document.activeElement !== $("#setDestructivePatterns")) {
    $("#setDestructivePatterns").value = (s.destructive_patterns || []).join("\n");
  }
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
  $("#q").oninput = () => { syncQClear(); renderTree(); };
  const clearSearch = () => {
    const q = $("#q");
    if (q.value === "") return;
    q.value = "";
    syncQClear();
    renderTree();
    q.focus();
  };
  /* On the press, not the click. A click only fires when the press and the
   * release land on the same element, and on a target this size a pixel of
   * drift put the release on the input instead — which is why it read as
   * "the x works if you hold still and not otherwise". preventDefault keeps
   * the caret in the box instead of moving focus to the button. onclick stays
   * for the keyboard, and clearing an empty box does nothing. */
  $("#qClear").onpointerdown = (ev) => { ev.preventDefault(); clearSearch(); };
  $("#qClear").onclick = clearSearch;
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
    if (button.dataset.pane === "models") loadProviders();
  };

  $("#llmForm").onsubmit = addProvider;

  $("#setTheme").onchange = (ev) => saveSettings({ theme: ev.target.value });
  $("#themeGen").onclick = generateTheme;
  $("#themePrompt").onkeydown = (ev) => {
    if (ev.key === "Enter") { ev.preventDefault(); generateTheme(); }
  };
  $("#setFontFamily").onchange = (ev) => saveSettings({ font_family: ev.target.value });
  $("#setAppearance").onchange = (ev) => saveSettings({ appearance: ev.target.value });
  $("#setInputMode").onchange = (ev) => saveSettings({ input_mode: ev.target.value });
  // Per device, so it never goes through saveSettings (which is server-side).
  $("#setGpu").onchange = (ev) => setGpu(ev.target.checked);
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
  $("#setThemeArt").onchange = (ev) => saveSettings({ theme_art: ev.target.checked });
  $("#setCliWatermark").onchange = (ev) => saveSettings({ cli_watermark: ev.target.checked });

  /* Reloading an installed app.
   *
   * A PWA has no address bar, so there is no reload: if the panel ever gets
   * into a state a fresh load would fix, an installed app has nothing to press
   * and the only way out is to close and reopen it from the home screen. This
   * is that button, and it is shown only where it is the only way, which is why
   * a browser tab does not get one. `standalone` is the installed case
   * everywhere except iOS, which has its own flag and predates the standard.
   *
   * The service worker is asked to update first. It does not cache, so a plain
   * reload already fetches fresh code, but the worker itself is the one thing a
   * reload would otherwise keep, and "reload" meaning "all of it except the
   * part that serves you" is a promise not worth making. Failure is ignored:
   * an update that cannot happen should not stop the reload that can. */
  const installed = matchMedia("(display-mode: standalone)").matches
    || matchMedia("(display-mode: fullscreen)").matches
    || matchMedia("(display-mode: minimal-ui)").matches
    || navigator.standalone === true;
  const reload = $("#reloadBtn");
  if (reload) {
    reload.hidden = !installed;
    reload.onclick = async () => {
      reload.disabled = true;
      try {
        const reg = navigator.serviceWorker
          && await navigator.serviceWorker.getRegistration();
        if (reg) await reg.update();
      } catch (err) {
        /* offline, or no worker. The reload below is the point. */
      }
      location.reload();
    };
  }
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
  // Same reasoning as above. Nothing has to invalidate the two-minute walk
  // cache by hand: the roots are part of its key, so changing them is a miss.
  $("#setProjectRoots").onblur = (ev) => {
    saveSettings({ project_roots: ev.target.value.split("\n") });
  };
  // The toggle carries the days field: unchecked stores 0 (off); checked stores
  // whatever the field says, defaulting to 14 the first time it is switched on.
  $("#setDropCleanup").onchange = (ev) => {
    const box = $("#setDropCleanupDays");
    box.disabled = !ev.target.checked;
    const days = Math.max(1, Math.min(365, Number(box.value) || 14));
    box.value = days;
    saveSettings({ drop_cleanup_days: ev.target.checked ? days : 0 });
  };
  $("#setDropCleanupDays").onchange = (ev) => {
    if (!$("#setDropCleanup").checked) return;   // off: the number is inert
    const days = Math.max(1, Math.min(365, Number(ev.target.value) || 14));
    ev.target.value = days;
    saveSettings({ drop_cleanup_days: days });
  };
  $("#purgeShares").onclick = async () => {
    const ok = await confirmAction({
      title: "Clear shared files?",
      message: "Delete every dropped and pasted file from all sessions’ scratch folders now.",
      detail: "Only .clique-drops and .claude-images are touched — your project files are left alone. This cannot be undone.",
      okLabel: "Clear them",
      danger: true,
    });
    if (!ok) return;
    try {
      const r = await api("api/storage/purge", { method: "POST", body: "{}" });
      toast(r.files
        ? `Cleared ${r.files} file${r.files === 1 ? "" : "s"}, freed ${fmtSize(r.bytes || 0)}.`
        : "Nothing to clear.");
      refreshStorage();
    } catch (err) {
      toast("Could not clear: " + err.message, true);
    }
  };
  $("#setFlash").onchange = (ev) => saveSettings({ notify_flash: ev.target.checked });
  $("#setServices").onchange = (ev) =>
    saveSettings({ service_status: ev.target.checked }).then(renderServices);
  $("#setSound").onchange = (ev) => saveSettings({ notify_sound: ev.target.checked });
  $("#setConfirmDestructive").onchange = (ev) =>
    saveSettings({ confirm_destructive: ev.target.checked });
  // On blur, like the artifact dirs: a half-typed pattern is not a setting.
  $("#setDestructivePatterns").onblur = (ev) =>
    saveSettings({
      destructive_patterns: ev.target.value.split("\n").map((x) => x.trim()).filter(Boolean),
    });
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
  $("#scrim").onclick = () => setSidebar(false);
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

  $("#activeOnly").onclick = toggleActiveOnly;
  // Overflow menu for the occasional header actions, so the top bar stays lean.
  // stopPropagation so the document click-closer does not shut it on this click.
  $("#moreBtn").onclick = (ev) => {
    ev.stopPropagation();
    showMenu(ev, [
      ["New folder", newFolder],
      ["Adopt sessions", adoptSessions],
      ["Broadcast to all", openBroadcast],
      ["Board", openBoard],
    ]);
  };
  $("#inboxBtn").onclick = openInbox;
  $("#inboxClose").onclick = closeInbox;
  $("#inbox").onclick = (ev) => { if (ev.target === $("#inbox")) closeInbox(); };
  $("#boardClose").onclick = closeBoard;
  $("#board").onclick = (ev) => { if (ev.target === $("#board")) closeBoard(); };
  $("#broadcastClose").onclick = closeBroadcast;
  $("#broadcastAll").onchange = () => {
    const on = $("#broadcastAll").checked;
    for (const cb of broadcastChecks()) cb.checked = on;
    for (const g of document.querySelectorAll("#broadcastList .bc-group")) syncBroadcastGroup(g);
    $("#broadcastAll").indeterminate = false;
    updateBroadcastCount();
  };
  $("#broadcastSend").onclick = sendBroadcast;
  $("#broadcast").onclick = (ev) => { if (ev.target === $("#broadcast")) closeBroadcast(); };
  $("#diffClose").onclick = closeDiff;
  $("#diffSend").onclick = sendDiffComment;
  $("#diff").onclick = (ev) => { if (ev.target === $("#diff")) closeDiff(); };
  applyActiveOnly();

  $("#wtToggle").onchange = () => {
    const on = $("#wtToggle").checked;
    $("#wtBranch").hidden = !on;
    if (on) {
      if (!$("#wtBranch").value) {
        const base = ($("#newForm").name.value.trim() || "work")
          .toLowerCase().replace(/[^a-z0-9._-]+/g, "-").replace(/^-+|-+$/g, "");
        $("#wtBranch").value = base || "work";
      }
      $("#wtBranch").focus();
      $("#wtBranch").select();
    }
  };

  $("#newForm").onsubmit = async (ev) => {
    ev.preventDefault();
    const form = ev.target;
    const wtOn = $("#wtToggle").checked && !$("#wtRow").hidden;
    const branch = $("#wtBranch").value.trim();
    if (wtOn && !branch) {
      const box = $("#modalErr");
      box.textContent = "Give the worktree a branch name.";
      box.hidden = false;
      return;
    }
    try {
      const payload = {
        name: form.name.value, cli: form.cli.value,
        cwd: form.cwd.value, folder: form.folder.value || null,
      };
      if (wtOn) { payload.worktree = true; payload.branch = branch; }
      const count = Math.max(1, Math.min(20, parseInt(form.count.value, 10) || 1));
      if (count > 1) {
        // A fleet: one request, N sessions, each with its own worktree when one
        // is on. Open the first; say if any did not start.
        const r = await api("api/sessions/spawn", {
          method: "POST", body: JSON.stringify({ ...payload, count }),
        });
        $("#modal").hidden = true;
        form.reset();
        await refresh();
        if (r.created && r.created[0]) openSession(r.created[0]);
        if (r.errors && r.errors.length) {
          toast(`${r.errors.length} of ${count} didn't start — ${r.errors[0]}`, true);
        }
        return;
      }
      const created = await api("api/sessions", {
        method: "POST", body: JSON.stringify(payload),
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
  $("#reviewLock").onclick = () => toggleReviewLock(activeId);
  $("#confirmOk").onclick = () => closeConfirm(true);
  $("#confirmNo").onclick = () => closeConfirm(false);
  $("#confirmCancel").onclick = () => closeConfirm(false);
  $("#confirmSheet").onclick = (ev) => { if (ev.target === $("#confirmSheet")) closeConfirm(false); };
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
  $("#openSel").onclick = () => {
    const path = pathFromText(paneSelection());
    if (path && activeId) openFileSheet(activeId, path);
  };
  $("#fileUp").onclick = () => {
    const parent = currentFile && fileParentPath(currentFile);
    if (parent && fileSession) openFileSheet(fileSession, parent);
  };
  $("#terminal").addEventListener("contextmenu", (ev) => {
    // A canvas has no native copy. Right-click with a selection copies;
    // without one, leave the event so a browser menu can still appear.
    // A path under the pointer, or a selected path, is offered as Open:
    // click already does that on desktop, but a phone has no hover to
    // discover the link and no right-click either.
    const entry = terms.get(activeId);
    const under = entry ? panePathAt(entry.term, ev.clientX, ev.clientY) : "";
    const picked = pathFromText(paneSelection());
    const path = under || picked;
    if (path && activeId) {
      ev.preventDefault();
      const name = path.split("/").filter(Boolean).pop() || path;
      showMenu(ev, [
        ["Open " + name, () => openFileSheet(activeId, path)],
        ["Copy path", () => copyText(path).then(() => toast("Path copied"))],
        ["Send path", () => toast("Path is in " + deliverPath(path))],
      ]);
      return;
    }
    if (copyPaneSelection()) ev.preventDefault();
    else if (matchMedia("(pointer: coarse)").matches && activeId) {
      const s = session(activeId);
      if (!s) return;
      ev.preventDefault();
      showMenu(ev, [
        ["Open a file in this folder", () => openFileSheet(s.id, s.cwd || ".")],
      ]);
    }
  });
  $("#artBtn").onclick = openArtifacts;
  $("#artClose").onclick = closeArtifacts;
  $("#artBack").onclick = showArtifactGrid;
  $("#art").onclick = (ev) => {
    if (ev.target === $("#art")) closeArtifacts();          // the backdrop
  };
  $("#zenExit").onclick = () => toggleZen(false);
  wireSidePanel();
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
  $("#fileEditBtn").onclick = startFileEdit;
  $("#fileSave").onclick = saveFileEdit;
  $("#fileCancel").onclick = cancelFileEdit;
  // Ctrl/Cmd+S saves from inside the editor; Escape backs out to the preview.
  $("#fileEdit").onkeydown = (ev) => {
    if ((ev.ctrlKey || ev.metaKey) && ev.key === "s") { ev.preventDefault(); saveFileEdit(); }
    else if (ev.key === "Escape") { ev.preventDefault(); ev.stopPropagation(); cancelFileEdit(); }
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

  /* Dropping a file onto the window.
   *
   * Only an OS file drag is ours: a drag carrying "Files" in its types. The
   * sidebar and the tab strip drag their own items to reorder them and those
   * never carry Files, so the guard below leaves that gesture completely alone.
   * A depth counter rides dragenter/dragleave because those fire on every child
   * crossed, not once for the window; the veil's pointer-events:none keeps the
   * cursor from ever "leaving" onto the veil, so the count stays honest. */
  const veil = $("#dropveil");
  let dragDepth = 0;
  const isFileDrag = (ev) => {
    const t = ev.dataTransfer && ev.dataTransfer.types;
    return Boolean(t) && [...t].includes("Files");
  };
  const showVeil = () => {
    if (!veil) return;
    const none = !activeId;
    veil.classList.toggle("nosess", none);
    veil.querySelector(".dropveil-msg").textContent =
      none ? "Open a session first" : "Drop to add to this session";
    veil.querySelector(".dropveil-sub").textContent = none
      ? "A dropped file needs a session to land in."
      : "Lands in the session folder; the path goes to your prompt.";
    veil.hidden = false;
  };
  const hideVeil = () => { dragDepth = 0; if (veil) veil.hidden = true; };
  document.addEventListener("dragenter", (ev) => {
    if (!isFileDrag(ev)) return;
    ev.preventDefault();
    dragDepth++;
    showVeil();
  });
  document.addEventListener("dragover", (ev) => {
    if (!isFileDrag(ev)) return;
    ev.preventDefault();               // required, or the browser blocks the drop
    if (ev.dataTransfer) ev.dataTransfer.dropEffect = activeId ? "copy" : "none";
  });
  document.addEventListener("dragleave", (ev) => {
    if (!isFileDrag(ev)) return;
    dragDepth = Math.max(0, dragDepth - 1);
    if (dragDepth === 0 && veil) veil.hidden = true;
  });
  document.addEventListener("drop", (ev) => {
    if (!isFileDrag(ev)) return;
    ev.preventDefault();
    hideVeil();
    const dropped = ev.dataTransfer && ev.dataTransfer.files;
    if (dropped && dropped.length) dropFiles(dropped);
  }, true);
  // A drag that ends off-window (Escape, or a drop somewhere else) leaves the
  // veil up otherwise, because no drop or final dragleave ever reaches us.
  window.addEventListener("dragend", hideVeil);

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

let orphans = null;
let orphansAt = 0;

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

/* ---------------------------------------------------------- model providers */

/* Bring-your-own-key model providers, shown in the Models settings tab. Off the
 * poll entirely (keys never ride the three-second /api/state), fetched when the
 * tab opens. The key itself never comes back — the panel reports only whether
 * one is set, so there is nothing here to leak into the DOM. */
let llmProviders = null;

async function loadProviders() {
  let data;
  try {
    data = await api("api/llm/providers");
  } catch (err) {
    llmProviders = [];
    renderProviders();
    return;
  }
  llmProviders = data.providers || [];
  const warn = $("#llmCryptoWarn");
  const form = $("#llmForm");
  const cryptoOff = data.encryption === false;
  if (warn) {
    warn.textContent = cryptoOff
      ? "Key encryption isn't available on this box yet. Install it with "
        + "pip install 'clique-panel[llm]' and restart, then you can add a key."
      : "";
    warn.hidden = !cryptoOff;
  }
  if (form) {
    // Without encryption we refuse to take a key at all, rather than store it
    // in the clear — so the field and the button go dead until it's installed.
    form.key.disabled = cryptoOff;
    form.querySelector('button[type="submit"]').disabled = cryptoOff;
  }
  renderProviders();
}

function providerKindLabel(kind) {
  return kind === "anthropic" ? "Anthropic" : "OpenAI-compatible";
}

function renderProviders() {
  const host = $("#llmProviders");
  if (!host) return;
  host.textContent = "";
  const list = llmProviders || [];
  if (!list.length) {
    const empty = document.createElement("p");
    empty.className = "note dim";
    empty.textContent = "No providers yet — add one below.";
    host.append(empty);
    return;
  }
  for (const prov of list) {
    const row = document.createElement("div");
    row.className = "llm-row";
    row.dataset.id = prov.id;

    const main = document.createElement("div");
    main.className = "llm-main";
    const name = document.createElement("b");
    name.textContent = prov.label || prov.model;
    const meta = document.createElement("span");
    meta.className = "dim";
    meta.textContent = `${providerKindLabel(prov.kind)} · ${prov.model}`;
    main.append(name, meta);

    const status = document.createElement("span");
    status.className = "llm-status";

    const test = document.createElement("button");
    test.type = "button";
    test.className = "llm-btn";
    test.textContent = "Test";
    test.onclick = () => testProvider(prov.id, status);

    const del = document.createElement("button");
    del.type = "button";
    del.className = "llm-btn danger";
    del.textContent = "Delete";
    del.onclick = () => deleteProvider(prov.id, prov.label || prov.model);

    row.append(main, status, test, del);
    host.append(row);
  }
}

async function testProvider(id, statusEl) {
  if (statusEl) { statusEl.textContent = "testing…"; statusEl.dataset.state = ""; statusEl.title = ""; }
  let result;
  try {
    result = await api(`api/llm/providers/${id}/test`, { method: "POST", body: "{}" });
  } catch (err) {
    if (statusEl) { statusEl.textContent = "✗ test failed"; statusEl.dataset.state = "bad"; }
    return;
  }
  if (!statusEl) return;
  if (result.ok) {
    statusEl.textContent = "✓ reachable";
    statusEl.dataset.state = "ok";
    statusEl.title = result.sample ? `replied: ${result.sample}` : "";
  } else {
    statusEl.textContent = "✗ failed";
    statusEl.dataset.state = "bad";
    statusEl.title = result.error || "";
  }
}

async function deleteProvider(id, label) {
  if (!confirm(`Remove "${label}" and its stored key?`)) return;
  try {
    await api(`api/llm/providers/${id}/delete`, { method: "POST", body: "{}" });
  } catch (err) {
    toast("Could not remove that provider.", true);
    return;
  }
  toast("Provider removed.");
  await loadProviders();
}

async function addProvider(ev) {
  ev.preventDefault();
  const form = ev.target;
  const errBox = $("#llmFormErr");
  errBox.hidden = true;
  const body = {
    label: form.label.value.trim(),
    kind: form.kind.value,
    base_url: form.base_url.value.trim(),
    model: form.model.value.trim(),
    key: form.key.value,
  };
  let created;
  try {
    created = await api("api/llm/providers", { method: "POST", body: JSON.stringify(body) });
  } catch (err) {
    errBox.textContent = (err && err.message) || "Could not add the provider.";
    errBox.hidden = false;
    return;
  }
  form.reset();
  await loadProviders();
  // Answer "does my key work?" right away by testing what we just added.
  const status = $("#llmProviders")
    .querySelector(`.llm-row[data-id="${created.id}"] .llm-status`);
  if (status) testProvider(created.id, status);
}

/* Leaked sessions: tmux still running with no record behind it, so nothing in
 * the list can see or stop it. Fetched off the three-second poll and cached a
 * minute, like resumable — this is a rare exception surface, not a live gauge. */
async function loadOrphans(force) {
  if (!force && orphans && Date.now() - orphansAt < 60000) return orphans;
  try {
    orphans = await api("api/orphans");
    orphansAt = Date.now();
  } catch (err) {
    orphans = orphans || [];
  }
  renderOrphans();
  return orphans;
}

function orphanMB(list) {
  return Math.round(list.reduce((n, o) => n + (o.rss || 0), 0) / 1e6);
}

function renderOrphans() {
  const el = $("#orphans");
  if (!el) return;
  const list = orphans || [];
  if (!list.length) { el.hidden = true; el.textContent = ""; return; }
  el.textContent = "";
  el.insertAdjacentHTML("afterbegin",
    '<svg class="wi" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
    'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
    '<path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/>' +
    '<line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>');
  const label = document.createElement("span");
  const n = document.createElement("b");
  n.textContent = list.length;
  label.append(n, ` leaked session${list.length > 1 ? "s" : ""} \u00b7 ${orphanMB(list)} MB`);
  label.title = "tmux sessions still running with no record behind them \u2014 "
    + "nothing in the panel points to them, so they are safe to reclaim";
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "reclaim";
  btn.textContent = "Reclaim";
  btn.onclick = reclaimOrphans;
  el.append(label, btn);
  el.hidden = false;
}

async function reclaimOrphans() {
  const list = orphans || [];
  if (!list.length) return;
  if (!confirm(`Stop ${list.length} leaked session(s) and reclaim ~${orphanMB(list)} MB? `
    + `They have no record in the panel and nothing points to them.`)) return;
  let result;
  try { result = await api("api/orphans/reap", { method: "POST", body: "{}" }); }
  catch (err) { toast("Could not reclaim leaked sessions.", true); return; }
  const killed = (result.killed || []).length;
  toast(`Reclaimed ${killed} leaked session${killed === 1 ? "" : "s"}.`);
  await loadOrphans(true);
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
  add(panelPane ? "Close the side panel" : "Open the side panel",
      "Notes, git, session info and export. Ctrl+J", () => togglePanel(panelPane || "notes"));
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
    add("Open a file", "Look at a path in this session's folder",
        () => openFileSheet(current.id, current.cwd || "."));
    add("Copy what's on screen", "The visible pane, not the scrollback",
        () => { copyPaneSelection() || copyPaneVisible(); });
    add("Copy the last 50 lines", "The recent output, scrollback and all — no dragging",
        () => copyPaneLast(50));
    add("Export scrollback to a file", "The whole history to a timestamped .txt in the session's folder",
        () => exportScrollback(current));
    if (current.alive) {
      add("Interrupt (Ctrl-C)", "Send Ctrl-C to pause what the session is doing",
          () => sendKey(current.id, "C-c", "Paused"));
    }
    add("Notes for this session", "A nested checklist in the side panel — to-dos, context, reminders",
        () => openNote(current));
    add("Git and checkpoints", "Branch, what's changed, and a checkpoint, in the side panel",
        () => openPanel("git"));
    add("Session info", "Directory, CLI, memory and uptime, in the side panel",
        () => openPanel("info"));
    add("Focus the terminal", current.name, focusTerminal);
    add(document.body.classList.contains("zen") ? "Exit zen mode" : "Zen mode",
        "Hide everything but the terminal and the prompt", () => toggleZen());
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
  /* Always offered here, even where the status bar hides the button. The
   * button is for the installed app, which has no address bar; the palette is
   * for anyone who would rather type it, and for a keyboard that cannot reach
   * the browser's own reload because the pane has the keys. */
  add("Reload the panel", "Sessions keep running; tabs and layout come back",
      () => { const b = $("#reloadBtn"); if (b) b.onclick(); else location.reload(); });

  for (const [id, theme] of Object.entries(window.CLIQUE_THEMES || {})) {
    const what = theme.art ? theme.base + " · has a character" : theme.base;
    add("Theme: " + theme.label,
        (state.settings.theme || "") === id ? "in use" : what,
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

function panePathAt(term, clientX, clientY) {
  if (!term) return "";
  const cell = paneCellAt(term, clientX, clientY);
  const line = term.buffer.active.getLine(cell.y);
  if (!line) return "";
  const own = line.translateToString(true);
  PATH_RE.lastIndex = 0;
  let match;
  while ((match = PATH_RE.exec(own)) !== null) {
    const raw = trimPath(match[1]);
    if (!raw || raw.startsWith("//") || raw.includes("://")) continue;
    const at = pathRange(own, match, raw);
    if (cell.x + 1 >= at.start && cell.x + 1 <= at.end) return raw;
  }
  return "";
}

function sendPaneClick(term, clientX, clientY, sessionId) {
  const entry = terms.get(sessionId || activeId);
  if (!entry || !entry.ws || entry.ws.readyState !== 1) return;
  const at = paneGridCell(term, clientX, clientY);
  entry.ws.send(new TextEncoder().encode(paneSgrClick(at.col, at.row)));
}

function paneSgrWheel(col, row, up) {
  // SGR mouse wheel: button 64 is up, 65 is down. Press only — a wheel tick has
  // no release, unlike a click.
  return "\x1b[<" + (up ? 64 : 65) + ";" + col + ";" + row + "M";
}

function sendPaneWheel(term, clientX, clientY, up, ticks, sessionId) {
  const entry = terms.get(sessionId || activeId);
  if (!entry || !entry.ws || entry.ws.readyState !== 1) return;
  const at = paneGridCell(term, clientX, clientY);
  const seq = new TextEncoder().encode(paneSgrWheel(at.col, at.row, up));
  for (let i = 0; i < Math.max(1, ticks); i++) entry.ws.send(seq);
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

/* How far a boxed CLI's picture has to shrink to clear the pane's width.
 *
 * Width alone, deliberately. The zoom exists to protect the *columns*:
 * narrowing the grid is what stacks a boxed CLI's prompt, and rows are free to
 * follow the box like any other terminal's. Letting the height vote used to
 * trap the pane — taking the rows the zoom had freed made the grid taller than
 * its box, which read as a fresh reason to keep zooming, so closing the panel
 * never gave the full size back. */
function paneWidthScale(availW, cols, cellW) {
  if (availW < 2 || cols < 1 || cellW < 1) return 1;
  const need = cols * cellW;
  return need <= availW ? 1 : availW / need;
}

function paneShouldZoom(boxed, scale) {
  return Boolean(boxed) && scale < 1 && scale >= PANE_ZOOM_MIN;
}

/* Scale the picture, and tell the terminal how wide it really is.
 *
 * The width matters as much as the transform. xterm sizes its viewport (and
 * therefore its scrollbar) from its own root element, while the screen it
 * draws follows the column count — so a pane that deliberately keeps a wide
 * grid inside a narrower box leaves the two disagreeing, and the scrollbar
 * ends up stranded partway across with terminal text on both sides of it.
 * Giving the root the grid's true width makes them agree, and the transform
 * then shrinks both together. */
function applyPaneZoom(term, scale, naturalW, naturalH) {
  const el = term && term.element;
  if (!el) return;
  if (!scale || scale >= 0.995) {
    el.style.transform = "";
    el.style.transformOrigin = "";
    el.style.width = "";
    el.style.height = "";
    return;
  }
  if (naturalW) el.style.width = Math.ceil(naturalW) + "px";
  if (naturalH) el.style.height = Math.ceil(naturalH) + "px";
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

  /* The wheel cuts two ways, and which way is decided by the screen the pane
   * is on — which the server tells us as `alt`.
   *
   * On the NORMAL screen a CLI in mouse mode is handed every wheel tick by
   * xterm, so scrolling up never reaches the pane's own 20k lines of
   * scrollback — the thing you wanted to re-read is simply gone off the top.
   * Caught in the capture phase, before xterm can forward it: the wheel scrolls
   * that buffer and the CLI never sees it.
   *
   * On the ALTERNATE screen a full-screen app — Claude, Grok, an editor — owns
   * the view and keeps no scrollback here; tmux redraws it in place, so that
   * buffer never fills and scrolling it does nothing. The scroll is the app's.
   * So we forward the wheel to it as SGR mouse-wheel events; it moves its own
   * view and the redraw streams back. That is what makes those CLIs, which
   * never scrolled here before, finally scroll. */
  host.addEventListener("wheel", (e) => {
    if (!sessionOwnsInput(sessionId)) return;
    const ticks = Math.max(1, Math.round(Math.abs(e.deltaY) / 24));
    const s = session(sessionId);
    if (s && s.alt) {
      sendPaneWheel(term, e.clientX, e.clientY, e.deltaY < 0, ticks, sessionId);
    } else {
      term.scrollLines(Math.sign(e.deltaY) * ticks);
    }
    e.preventDefault();
    e.stopPropagation();
  }, { capture: true, passive: false });

  /* The same scroll, with a finger, because a phone has no wheel.
   *
   * This was simply missing. Scrolling the pane has always been our own wheel
   * handler rather than the browser's, since xterm needs telling which of the
   * two buffers a scroll belongs to, and nothing was listening for touch at
   * all. So on a phone the pane did not scroll, and it looked like a broken
   * gesture rather than an absent one.
   *
   * The branch is the wheel's, unchanged: a full-screen app owns its own view
   * and is sent wheel events to move it, anything else scrolls the pane's own
   * scrollback.
   *
   * Pixels are carried between moves rather than rounded away. A slow drag
   * moves a few pixels per event, every one of which truncates to zero lines,
   * and the pane would not move at all until you flicked. Keeping the
   * remainder is the difference between a gesture that tracks your thumb and
   * one that only responds to violence.
   *
   * A short movement is left alone so a tap still reaches the CLI and a
   * long-press can still start a selection. */
  const TOUCH_SLOP = 8;
  let touchY = 0;
  let touchX = 0;
  let carried = 0;
  let dragging = false;
  let tracking = false;

  host.addEventListener("touchstart", (e) => {
    tracking = e.touches.length === 1;
    dragging = false;
    carried = 0;
    if (!tracking) return;
    touchY = e.touches[0].clientY;
    touchX = e.touches[0].clientX;
  }, { capture: true, passive: true });

  host.addEventListener("touchmove", (e) => {
    if (!tracking || e.touches.length !== 1) return;
    /* Deliberately not gated on `sessionOwnsInput`, which the wheel handler
     * above does gate on. The wheel can afford it: a CLI that does not own its
     * input is left to the browser's native scrolling. A finger has no such
     * fallback, because `touch-action: none` above is exactly what took the
     * gesture away from the browser, so gating here means a shell session
     * simply does not scroll. Which is how this was written the first time. */
    const y = e.touches[0].clientY;
    const moved = touchY - y;
    if (!dragging) {
      if (Math.abs(moved) < TOUCH_SLOP) return;   // still might be a tap
      dragging = true;
      touchY = y;
      return;
    }
    const cell = paneCellPx(term);
    const height = (cell && cell.h) || 17;
    carried += touchY - y;
    touchY = y;
    const lines = Math.trunc(carried / height);
    if (lines) {
      carried -= lines * height;
      const s = session(sessionId);
      if (s && s.alt) {
        sendPaneWheel(term, touchX, y, lines < 0, Math.abs(lines), sessionId);
      } else {
        term.scrollLines(lines);
      }
    }
    // Held even on a move that did not add up to a line yet: releasing it
    // would let the page rubber-band underneath a half-finished drag.
    e.preventDefault();
    e.stopPropagation();
  }, { capture: true, passive: false });

  host.addEventListener("touchend", () => { tracking = false; dragging = false; },
                        { capture: true, passive: true });
  host.addEventListener("touchcancel", () => { tracking = false; dragging = false; },
                        { capture: true, passive: true });
}

function renderCopyChip() {
  const wrap = $("#selChips");
  const open = $("#openSel");
  const picked = paneSelection();
  const has = Boolean(picked && picked.trim());
  if (wrap) wrap.hidden = !has;
  if (open) open.hidden = !pathFromText(picked);
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
/* Who gets to set the shared tmux window when more than one panel is open.
 *
 * `document.hasFocus()` was doing this job and cannot: it is per browser
 * window, and a desktop panel on one machine and a phone in your hand both
 * report true at the same time, because both are true. So on every poll each
 * one saw the window at the other's size, decided it had been resized out from
 * under it, and claimed it back. Two clients, three seconds apart, forever: the
 * CLI reflows to 162 columns, then to 42, then back, and the phone is unusable
 * for as long as a desktop panel is open somewhere.
 *
 * The rule now is Justin's, and it is a product decision rather than a
 * heuristic: **a handheld wins.** A phone is only ever picked up to do
 * something that could not wait, so when one is awake and in front of you it
 * should own the window and the desktop should get out of the way.
 *
 * A phone in a pocket does not count, and does not need to be special-cased:
 * a backgrounded tab or a dark screen is `document.hidden`, which is already
 * the first thing this checks.
 *
 * A desktop still claims on any real action — selecting a tab, coming back to
 * the window, clicking into the pane — because that is the moment you are
 * using it, and it still claims on the poll while it is being used, so a lone
 * desktop panel recovers from tmux's dot-fill exactly as it did. What it no
 * longer does is assert its size purely for being open. */
let lastTouch = 0;
const TOUCH_ACTIVE_MS = 45000;

for (const kind of ["keydown", "pointerdown", "touchstart"]) {
  addEventListener(kind, () => { lastTouch = Date.now(); }, { capture: true, passive: true });
}

/* Touch as the primary input, which is the honest test for "this is a phone
 * or a tablet". Not the width: a narrow desktop window is still a desktop, and
 * a tablet in landscape is still the thing that should win. */
function handheld() {
  return matchMedia("(pointer: coarse)").matches;
}

function recentlyUsed() {
  return Date.now() - lastTouch < TOUCH_ACTIVE_MS;
}

function claimable(cols, rows) {
  // A collapsed or hidden tab measures as almost nothing. Sending that
  // as the shared window's size is how coming back left a sea of dots.
  // A real phone still clears this.
  return cols >= 20 && rows >= 8;
}

/* A phone tells the server it is still here, and tells it when it is not.
 *
 * The server decides who owns the shared tmux window and a handheld wins, but
 * a phone that already owns it has nothing left to resize, so without this the
 * claim would lapse under an idle phone and the desktop would take the window
 * back mid-read. And without the release, putting the phone down would lock
 * the desktop out until the backstop timer expired.
 *
 * Only from a phone, only while it is awake and looking at a session. A
 * desktop never sends either: it is not claiming anything. */
function holdWindow(alive) {
  if (!handheld()) return;
  const entry = terms.get(activeId);
  if (!entry || !entry.ws || entry.ws.readyState !== 1) return;
  entry.ws.send(JSON.stringify({ type: alive ? "hold" : "release" }));
}

document.addEventListener("visibilitychange", () => holdWindow(!document.hidden));
// A phone locked or swapped away often gets pagehide rather than a clean
// visibilitychange, and letting go is the half that must not be missed.
addEventListener("pagehide", () => holdWindow(false));

function reclaimSize(force) {
  if (document.hidden) return;
  if (!force && !document.hasFocus()) return;
  /* The poll-driven reclaim is the one that fights, so it is the one that
   * needs a reason. A handheld always has one. A desktop needs to have been
   * touched recently; being merely open is not a claim on somebody else's
   * screen. `force` is always a real action and always wins. */
  if (!force && !handheld() && !recentlyUsed()) return;
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
  entry.claimedAt = Date.now();
  entry.ws.send(JSON.stringify({ type: "resize", cols, rows, handheld: handheld() }));
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

function isMobile() { return matchMedia("(max-width: 640px)").matches; }

function setSidebar(show) {
  $("#sidebar").hidden = !show;
  $("#resizer").hidden = !show;
  $("#rail").hidden = show;
  // On a phone the sidebar is an overlay drawer, so a shown one gets a scrim
  // behind it — to dim the pane and to catch a tap that means "close".
  $("#scrim").hidden = !(show && isMobile());
  localStorage.setItem("clique.sidebar", show ? "1" : "0");
  // Re-apply the stored width on the way back in, so collapsing and expanding
  // returns the sidebar you had rather than the default one.
  if (show) setSidebarWidth(storedSidebarWidth(), false);
  // Toggling the sidebar changes the pane's width, so refit and push the new
  // size to tmux — after the layout has actually settled, not at 0ms. Fitting
  // too early computes a boxed CLI's zoom against the old width, and the pane
  // comes back scaled wrong with a stray scrollbar and dead space beside it.
  afterLayout("pane", settlePane);
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
/* A phone keyboard cannot send Esc, Tab, Ctrl+C or the arrows a TUI needs, so a
 * row of them sits above the input on a narrow screen and taps them straight
 * into the pane. Delegated: the row is static, the target is whatever is in
 * front. */
function wireKeyRow() {
  const row = $("#keyrow");
  if (!row) return;
  row.addEventListener("click", (ev) => {
    const btn = ev.target.closest("[data-key]");
    if (!btn || !activeId) return;
    sendPaneKey(activeId, btn.dataset.key);
    focusTerminal();
  });
}

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
      // Crossing between a row's own children is not leaving the row. Guard on
      // peekRow first: with nothing being peeked, a mouseout over empty space
      // makes el null, and `null === peekRow(null)` would then call .contains
      // on null.
      if (peekRow && el === peekRow && !el.contains(ev.relatedTarget)) {
        clearTimeout(peekTimer);
        peekRow = null;
      }
    });
  }
}

wire();
wireResizer();
wireTouchMenus();
wireTermTouchMenus();
wirePeekTooltips();
wireKeyRow();
panelLoad();   // restore panel width + which pane, before the first render
setSidebarWidth(storedSidebarWidth(), false);
setSidebar(localStorage.getItem("clique.sidebar") !== "0");
// A phone starts with the drawer closed and the pane in front, whatever a
// desktop session in the same browser last left the sidebar at.
if (isMobile()) setSidebar(false);
bootWorkspace();
// Themes made here, once. They are wanted on any device the moment it loads,
// and they change about twice a year, so this is a boot fetch rather than
// weight on every poll.
loadThemes();
loadUsage();
// Its own cadence: plan windows move over hours and this is somebody else's
// API. The server caches on top of this, so extra tabs cost nothing.
setInterval(loadUsage, 5 * 60 * 1000);
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
