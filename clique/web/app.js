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
 * Five states, and only four of them draw anything:
 *
 *   error    — it said so, or its pane matched an error pattern
 *   working  — output is arriving
 *   waiting  — it stopped and wants you: either it said so, or it went quiet
 *              with something unread
 *   idle     — alive and quiet and read. Draws nothing, because most sessions
 *              are in this state most of the time and a sidebar of twenty
 *              indicators saying "fine" is a sidebar of noise.
 *   stopped  — the process is gone
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
  if (s.signal === "error") return "error";
  if (s.signal === "waiting") return "waiting";
  if (s.busy || (busyUntil.get(s.id) || 0) > Date.now()) return "working";
  if (attention.has(s.id)) return "waiting";
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
function applyCliTint() {
  const s = session(activeId);
  const off = state.settings.cli_tint === false || !s;
  document.querySelector("main").style.setProperty(
    "--cli", off ? "transparent" : cliColor(s.cli, s.color));
}

function cliColor(cliId, shipped) {
  return (state.settings.cli_colors || {})[cliId] || shipped || "var(--dim)";
}

function markerFor(item, mode) {
  if (mode === "none") return "";
  if (mode === "color") return `<i class="cli-chip" style="background:${item.color}"></i>`;

  // "icon" is the same shape in neutral grey; "both" tints it the CLI colour.
  const tint = mode === "icon" ? "var(--dim)" : item.color;
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
  // Same five states as the ring: with no marker to carry them, the dot is
  // what is left, and it should not be saying something different.
  const work = workState(s);
  return `<i class="dot" data-work="${work}" role="img"` +
         ` aria-label="${WORK_WORDS[work]}" title="${WORK_WORDS[work]}"></i>`;
}

/* Said out loud, for a tooltip and for a screen reader. A ring that only means
 * something to people who can see it is half a signal. */
const WORK_WORDS = {
  working: "working",
  waiting: "waiting for you",
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
  pendingWorkspace = { tabs, active: saved.active_tab || "", lifted };
  // Nothing is written back here: `openTabs` is still empty at this point, and
  // saving it now would erase the very list that was just lifted. The write
  // happens once the tabs are actually open.
}

function saveWorkspace(now) {
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
    api("api/settings", { method: "PATCH", body: JSON.stringify(body) })
      .catch(() => {});   // a lost workspace write is not worth interrupting for
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
    // Retry the opening fetch quickly instead of waiting out the poll: the
    // usual cause is a connection dropped while the page was still loading
    // the rest of itself.
    if (pollFailures < 4) {
      setTimeout(refresh, 250 * pollFailures);
      return;
    }
    $("#offline").hidden = false;
    return;
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
  $("#version").textContent = "v" + state.version;
  // First load pulls history in so the sidebar is complete without anyone
  // having to open the palette to trigger it.
  if (!resumable) loadResumable().then(renderTree);
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

function statDot(percent) {
  const level = pressureLevel(percent);
  return `<i class="dot stat" data-level="${level}" aria-hidden="true"></i>`;
}

function renderStats() {
  const st = state.stats || {};
  const gb = (mb) => Math.round((mb || 0) / 1024 * 10) / 10;

  const cpu = st.cpu ?? 0;
  const cpuEl = $("#cpu");
  cpuEl.innerHTML = statDot(cpu) + "cpu " + cpu + "%";
  cpuEl.title = `cpu ${cpu}%`;

  const mem = st.mem || {};
  const memEl = $("#mem");
  memEl.innerHTML = statDot(mem.percent) + "mem " + gb(mem.used_mb) + "/" +
                    Math.round((mem.total_mb || 0) / 1024) + "G";
  memEl.title = `memory ${mem.percent ?? 0}% used`;

  // Load against core count, because "1.4" means nothing without knowing the
  // box. One per core is a full queue, so that is where the dot reaches red.
  const load = st.load || {};
  const loadEl = $("#load");
  loadEl.innerHTML = statDot((load.ratio || 0) * 100) + "load " + (load.one ?? 0).toFixed(2);
  loadEl.title = `${load.one} / ${load.five} / ${load.fifteen} over ${load.cores} cores`;

  // Disk is the quietest way to lose an afternoon here: everything starts
  // failing in ways that never mention disk.
  const disk = st.disk || {};
  const diskEl = $("#disk");
  diskEl.innerHTML = statDot(disk.percent) + "disk " + (disk.free_gb ?? 0) + "G free";
  diskEl.title = `disk ${disk.percent ?? 0}% used`;

  // Any swap in use means memory pressure already happened, so it only
  // appears when there is something to say — and it starts amber rather than
  // green, because "a little swap" is not a healthy reading.
  const swap = st.swap || {};
  const swapEl = $("#swap");
  swapEl.hidden = !(swap.used_mb > 0);
  swapEl.innerHTML = statDot(Math.max(swap.percent || 0, 70)) + "swap " + gb(swap.used_mb) + "G";
  swapEl.title = `swap ${swap.percent ?? 0}% used — memory pressure has already happened`;

  $("#clients").innerHTML = '<i class="dot" style="background:var(--ok)"></i>' + (st.clients ?? 0);
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

/* ------------------------------------------------------------------- sidebar */

function renderTree() {
  const query = $("#q").value.trim().toLowerCase();
  const tree = $("#tree");
  tree.innerHTML = "";

  const matches = (s) =>
    !query || s.name.toLowerCase().includes(query) || s.cwd.toLowerCase().includes(query);

  // Running-and-open first: what you are working on should not be somewhere
  // you have to scroll to.
  const groups = [];
  const live = state.sessions.filter((s) => !s.archived);
  /* Running is for sessions with no tab open. A pane you are looking at is
   * already a tab across the top, and a second row for it says nothing; what
   * needs a home is the session still working with nothing on screen, which
   * is exactly the one you forget you started. */
  const running = live.filter((s) => s.alive && !openTabs.includes(s.id));
  if (running.length) {
    groups.push({ id: "__running", name: "Running", color: "#2d7d46", pinned: true,
                  collapsed: viewsCollapsed.has("__running"), sessions: running });
  }

  /* Ungrouped sits above the folders, not below them. A session you have
   * just started is the one you are looking for, and filing it is a decision
   * you make afterwards — so it has to be somewhere you can see without
   * scrolling past every folder you already have. */
  const unfiled = live.filter(
    (s) => !running.includes(s) && !state.folders.some((f) => f.id === s.folder));
  if (unfiled.length) {
    groups.push({ id: "__unfiled", name: "Ungrouped", color: "#8b8b8b",
                  collapsed: viewsCollapsed.has("__unfiled"), sessions: unfiled });
  }

  for (const folder of state.folders) {
    groups.push({
      ...folder,
      sessions: live.filter((s) => s.folder === folder.id && !running.includes(s)),
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
    head.className = "folder-head";
    head.dataset.folder = group.id;
    // Only real folders can be edited. Running, Ungrouped and Archived are
    // views over the sessions, not things with a name and a colour.
    const editable = !group.pinned && group.id.startsWith("f-");
    head.innerHTML =
      `<span class="caret">${group.collapsed ? "▸" : "▾"}</span>` +
      `<i class="dot" style="background:${group.color}"></i>` +
      `<span class="name">${escapeHtml(group.name)}</span>` +
      (editable ? `<button class="folder-edit" title="Rename, recolour or delete">✎</button>` : "") +
      `<span class="count">${shown.length}` +
      (historyCount(group) ? `<i class="from-history">+${historyCount(group)}</i>` : "") +
      `</span>`;
    head.onclick = () => toggleFolder(group);
    if (editable) {
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
  for (const s of state.sessions.filter((x) => x.alive)) {
    const dot = document.createElement("i");
    dot.className = "dot";
    dot.dataset.work = workState(s);
    dot.title = s.name + " — " + WORK_WORDS[dot.dataset.work];
    dot.onclick = () => openSession(s.id);
    dots.appendChild(dot);
  }
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
  return row;
}

function sessionRow(s) {
  const row = document.createElement("div");
  row.className = "session" + (s.id === activeId ? " active" : "") +
    (s.alive ? "" : " dead") + (s.busy ? " busy" : "") +
    (unread(s) ? " unread" : "") +
    (attention.has(s.id) ? " attention" : "");
  row.draggable = true;
  row.dataset.id = s.id;
  row.innerHTML =
    statusDot(s, "sidebar") +
    sessionMarker(s, "sidebar") +
    `<span class="meta"><span class="name">${escapeHtml(s.name)}</span>` +
    `<span class="path">${escapeHtml(s.cwd)}</span></span>` +
    `<span class="age">${ago(s.created)}</span>`;

  row.onclick = () => openSession(s.id);
  row.ondblclick = (ev) => { ev.stopPropagation(); renameInline(row, s); };
  row.oncontextmenu = (ev) => sessionMenu(ev, s);
  row.ondragstart = (ev) => {
    ev.dataTransfer.setData("text/plain", s.id);
    row.classList.add("dragging");
  };
  row.ondragend = () => row.classList.remove("dragging");
  return row;
}

function wireDrop(el, folderId) {
  el.ondragover = (ev) => { ev.preventDefault(); el.classList.add("drop"); };
  el.ondragleave = () => el.classList.remove("drop");
  el.ondrop = async (ev) => {
    ev.preventDefault();
    el.classList.remove("drop");
    const id = ev.dataTransfer.getData("text/plain");
    if (!id) return;
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

  const commit = async () => {
    const name = input.value.trim();
    if (name && name !== s.name) {
      await api("api/sessions/" + s.id, {
        method: "PATCH", body: JSON.stringify({ name }),
      });
    }
    refresh();
  };
  input.onblur = commit;
  input.onkeydown = (ev) => {
    if (ev.key === "Enter") { ev.preventDefault(); input.blur(); }
    if (ev.key === "Escape") { input.onblur = null; refresh(); }
    ev.stopPropagation();
  };
  input.onclick = (ev) => ev.stopPropagation();
}

/* --------------------------------------------------------------- context menu */

function showMenu(ev, items) {
  ev.preventDefault();
  const menu = $("#menu");
  menu.innerHTML = "";
  for (const [label, fn, danger] of items) {
    const button = document.createElement("button");
    button.textContent = label;
    if (danger) button.className = "danger";
    button.onclick = () => { menu.hidden = true; fn(); };
    menu.appendChild(button);
  }
  menu.hidden = false;
  menu.style.left = Math.min(ev.clientX, innerWidth - 170) + "px";
  menu.style.top = Math.min(ev.clientY, innerHeight - menu.offsetHeight - 8) + "px";
}

/* The destructive item names what is actually there to destroy. Offering
 * "Kill" on a session whose process ended long ago asks someone to confirm
 * stopping something that already stopped — and hides the thing they probably
 * do want, which is the row gone. */
function sessionMenu(ev, s) {
  showMenu(ev, [
    ["Open", () => openSession(s.id)],
    ["Rename", () => renameSession(s)],
    [s.archived ? "Unarchive" : "Archive", () => setArchived(s, !s.archived)],
    [s.alive ? "Kill session" : "Delete session", () => killSession(s), true],
  ]);
}

/* Long press, because touch has no right-click.
 *
 * Everything in the session menu — rename, archive, move, kill — was reachable
 * only by right-clicking, which does not exist on a phone. Folders got away
 * with it because the pencil is the same menu with a way to find it, and tabs
 * because of the gear; sidebar rows had nothing, so half the app was missing
 * on the device most likely to be checking on a session from the sofa.
 *
 * Delegated to the tree rather than bound per row: the sidebar is rebuilt on
 * every poll, and three listeners per session every three seconds is churn for
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

  tree.addEventListener("touchstart", (ev) => {
    cancel();
    if (ev.touches.length !== 1) return;        // a pinch is not a press
    const row = ev.target.closest(".session");
    if (!row) return;
    const touch = ev.touches[0];
    from = { x: touch.clientX, y: touch.clientY, id: row.dataset.id };
    fired = false;
    timer = setTimeout(() => {
      timer = null;
      const s = session(from && from.id);
      if (!s) return;
      fired = true;
      // The one moment a buzz is right: nothing has moved on screen yet, and
      // without it a press that has landed feels identical to one that has not.
      if (navigator.vibrate) navigator.vibrate(12);
      sessionMenu({ clientX: from.x, clientY: from.y, preventDefault() {} }, s);
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

const PALETTE = ["#c7915b", "#6f42c1", "#2d7d46", "#1f6feb", "#0d7d8f", "#a63d2f",
                 "#8b8b8b", "#d96f6f", "#e8a33d", "#3aa3a0", "#7a7fd6", "#ff5fa2"];

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
  menu.style.left = Math.min(ev.clientX, innerWidth - 190) + "px";
  menu.style.top = Math.min(ev.clientY, innerHeight - 110) + "px";
}

function folderMenu(ev, folder) {
  ev.stopPropagation();
  showMenu(ev, [
    ["Change colour", () => colorPicker(ev, folder)],
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
  const question = s.alive
    ? `Kill "${s.name}"? The CLI running in it is stopped for good.`
    : `Delete "${s.name}"? Nothing is running in it — this removes the session from the sidebar.`;
  if (!confirm(question)) return;
  closeTab(s.id, true);
  await api("api/sessions/" + s.id, { method: "DELETE" });
  refresh();
}

/* ---------------------------------------------------------------------- tabs */

function renderTabs() {
  const bar = $("#tabs");
  bar.innerHTML = "";
  openTabs.forEach((id, index) => {
    const s = session(id);
    if (!s) return;
    const tab = document.createElement("div");
    tab.className = "tab" + (id === activeId ? " active" : "") +
      (s.busy ? " busy" : "") + (unread(s) ? " unread" : "") +
      (attention.has(id) ? " attention" : "");
    tab.title = s.cwd;
    tab.innerHTML =
      `<span class="num">${index + 1}</span>` +
      statusDot(s, "tabs") +
      sessionMarker(s, "tabs") +
      `<span class="label">${escapeHtml(s.name)}</span>` +
      `<button class="gear" title="Session settings">⚙</button>` +
      `<button class="x" title="Close tab (session keeps running)">✕</button>`;
    tab.onclick = () => selectTab(id);
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
  renderInputBar();
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
  $("#empty").style.display = activeId ? "none" : "grid";
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
  button.textContent = "\u25A3 " + items.length;
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

function loadDraft(id) {
  const box = $("#prompt");
  const s = session(id);
  box.value = (s && s.draft) || "";
  box.style.height = "auto";
  box.style.height = Math.min(box.scrollHeight, 140) + "px";
  draftFor = id;
}

function saveDraft(now) {
  const id = draftFor;
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
    const entries = await api("/api/changelog");
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
  ]],
  ["Inside the palette", [
    ["@", "Sessions"],
    ["&gt;", "Commands"],
    ["~", "Past conversations, resumable in one click"],
  ]],
  ["Reading a pane", [
    ["Scroll up", "Detaches the view, so arriving output cannot drag it away"],
    ["Ctrl/Cmd + Shift + L", "Scroll lock on or off, without scrolling"],
    ["Click the paused badge", "Catch up and start following again"],
  ]],
  ["Working in a session", [
    ["Ctrl/Cmd + V", "With an image on the clipboard: saves it into the session’s folder and drops the path where you are typing"],
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
  lock.textContent = paused ? "\u23f8" : "\u21e3";
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
  renderFollow();         // the badge belongs to the pane you switched to
  loadDraft(id);
  showDeparture(id);
  markSeen(id);
  for (const [tid, entry] of terms) {
    entry.el.style.display = tid === id ? "block" : "none";
  }
  const entry = terms.get(id);
  if (entry) {
    entry.fit.fit();
    entry.term.focus();
  }
  renderTabs();
  renderTree();
  saveWorkspace();
}

async function openSession(id) {
  if (!openTabs.includes(id)) openTabs.push(id);
  if (!terms.has(id)) await attach(id);
  selectTab(id);
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
    renderTabs(); renderTree(); selectTab(activeId);
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

function wsUrl(id, cols, rows) {
  const url = new URL("ws", document.baseURI);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  url.search = `?id=${encodeURIComponent(id)}&cols=${cols}&rows=${rows}`;
  return url.toString();
}

async function attach(id) {
  const host = document.createElement("div");
  host.style.cssText = "position:absolute;inset:0;padding:6px 8px;";
  $("#terminal").appendChild(host);

  // Built with the theme already on, not with the built-in dark and a repaint
  // on the next poll: under a light theme that was a black pane flashing up
  // for a moment every time a session opened.
  const term = new Terminal({
    fontSize: state.settings.font_terminal || 13,
    fontFamily: 'ui-monospace, "SF Mono", Menlo, Consolas, monospace',
    theme: currentTheme().term || {},
    scrollback: 20000,
    cursorBlink: true,
    allowProposedApi: true,
  });
  const fit = new FitAddon.FitAddon();
  term.loadAddon(fit);
  term.open(host);
  fit.fit();

  /* The pane owns the keyboard — deliberately, and that is why tabs are
   * Alt+1..9 rather than plain digits. The palette is the single exception,
   * so it has to be taken from the pane here as well as bound on the
   * document: without this, Ctrl+K would open the palette *and* have the CLI
   * kill to end of line. Ctrl+Shift+P is safe to take outright, since nothing
   * in a terminal claims it. */
  term.attachCustomKeyEventHandler((ev) => {
    if (ev.type !== "keydown" || !(ev.ctrlKey || ev.metaKey)) return true;
    const key = ev.key.toLowerCase();
    // Handed to the document handler, which does the work — exactly as
    // Ctrl+Shift+P is. Acting here as well would toggle it twice.
    if (ev.shiftKey && (key === "l" || key === "/")) return false;
    if (ev.shiftKey && key === "p") return false;
    if (!ev.shiftKey && key === "k") return !paletteHotkeyOn();
    return true;
  });

  const entry = { term, fit, el: host, ws: null, closing: false, typed: "",
                  follow: true, behind: 0, pinned: 0, baseline: 0 };
  terms.set(id, entry);

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
      writeOut(entry, id,
        typeof ev.data === "string" ? ev.data : new Uint8Array(ev.data));
    };
    ws.onclose = () => {
      if (entry.closing) return;
      // A dropped tailnet connection should heal itself; a killed session
      // should not be retried forever.
      term.write("\r\n\x1b[90m— disconnected, retrying —\x1b[0m\r\n");
      entry.retry = setTimeout(() => { if (!entry.closing) connect(); }, 2000);
    };
    ws.onerror = () => {};
  };
  connect();

  const send = (text) => {
    if (entry.ws && entry.ws.readyState === 1) {
      entry.ws.send(new TextEncoder().encode(text));
    }
  };

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

function escapeHtml(text) {
  return String(text).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

/* ------------------------------------------------------------------- modal */

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
  form.cwd.value = current ? current.cwd : "/root";
  $("#modalErr").hidden = true;
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

  for (const entry of terms.values()) {
    entry.term.options.fontSize = s.font_terminal || 13;
    entry.term.options.theme = theme.term || {};
    try { entry.fit.fit(); } catch (err) { /* not visible yet */ }
  }

  // Order matters and is documented in the settings sheet: both, panel, then
  // terminal. The terminal block is wrapped so "terminal only" means it.
  styleSlot("both").textContent = s.css_both || "";
  styleSlot("panel").textContent = s.css_panel || "";
  styleSlot("term").textContent = s.css_terminal
    ? `#termwrap { ${s.css_terminal} }` : "";

  // Most CLIs draw their own prompt; two stacked boxes is redundant chrome.
  $("#inputbar").hidden = s.input_mode === "terminal";
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
  for (const session of sessions) {
    const before = wasBusy.get(session.id) || false;
    wasBusy.set(session.id, session.busy);
    if (!before || session.busy) continue;

    // Finished. Only worth saying so if he was not already watching it.
    const watching = session.id === activeId && document.hasFocus();
    if (watching) continue;
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
  $("#setInputMode").value = s.input_mode || "panel";

  for (const [slider, out, value] of [
    ["#setFontPanel", "#outFontPanel", s.font_panel || 13],
    ["#setFontTerminal", "#outFontTerminal", s.font_terminal || 13],
  ]) {
    $(slider).value = value;
    $(out).textContent = value + "px";
  }

  $("#setPalette").checked = s.palette_hotkey !== false;
  $("#setHistorySidebar").checked = s.history_in_sidebar !== false;
  $("#setStatusOnIcon").checked = !!s.status_on_icon;
  $("#setTabsMarkers").checked = s.markers_in_tabs !== false;
  $("#setSidebar").checked = s.markers_in_sidebar !== false;
  $("#setCliTint").checked = s.cli_tint !== false;
  $("#setArtShow").checked = s.artifacts_show !== false;
  // Not repainted while it has focus: this is a textarea someone types a list
  // into, and a poll landing mid-edit would move their cursor.
  if (document.activeElement !== $("#setArtDirs")) {
    $("#setArtDirs").value = (s.artifact_dirs || []).join("\n");
  }
  $("#setFlash").checked = s.notify_flash !== false;
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
  $("#setAppearance").onchange = (ev) => saveSettings({ appearance: ev.target.value });
  $("#setInputMode").onchange = (ev) => saveSettings({ input_mode: ev.target.value });
  $("#setPalette").onchange = (ev) => saveSettings({ palette_hotkey: ev.target.checked });
  $("#setHistorySidebar").onchange = (ev) => {
    saveSettings({ history_in_sidebar: ev.target.checked }).then(renderTree);
  };
  $("#setStatusOnIcon").onchange = (ev) => saveSettings({ status_on_icon: ev.target.checked });
  $("#setTabsMarkers").onchange = (ev) => saveSettings({ markers_in_tabs: ev.target.checked });
  $("#setSidebar").onchange = (ev) => saveSettings({ markers_in_sidebar: ev.target.checked });
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

  $("#stats").onclick = showHistory;
  $("#cancel").onclick = () => ($("#modal").hidden = true);
  $("#collapse").onclick = () => setSidebar(false);
  $("#expand").onclick = () => setSidebar(true);

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

  $("#run").onclick = () => run($("#prompt").value);
  $("#runShell").onclick = () => runShell($("#prompt").value);
  $("#repPlus").onclick = () => setRepeat(repeat + 1);
  $("#repMinus").onclick = () => setRepeat(repeat - 1);

  $("#modePill").onclick = () => cycleMode(session(activeId), false);

  $("#prompt").onkeydown = (ev) => {
    if (ev.key === "Tab" && expandInBox(ev.target)) { ev.preventDefault(); return; }
    if (ev.key === "Enter" && !ev.shiftKey) { ev.preventDefault(); run(ev.target.value); }
  };
  $("#prompt").oninput = (ev) => {
    ev.target.style.height = "auto";
    ev.target.style.height = Math.min(ev.target.scrollHeight, 140) + "px";
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
  $("#keysClose").onclick = () => ($("#keys").hidden = true);
  $("#keys").onclick = (ev) => {
    if (ev.target === $("#keys")) $("#keys").hidden = true;   // the backdrop
  };
  $("#follow").onclick = () => setFollow(activeId, true);
  $("#artBtn").onclick = openArtifacts;
  $("#artClose").onclick = closeArtifacts;
  $("#artBack").onclick = showArtifactGrid;
  $("#art").onclick = (ev) => {
    if (ev.target === $("#art")) closeArtifacts();          // the backdrop
  };

  // Capture phase: xterm handles paste on its own textarea, so this has to see
  // the event first. It only claims the event when there is an image in it.
  document.addEventListener("paste", (ev) => {
    const items = ev.clipboardData && ev.clipboardData.items;
    if (!items || !items.length) return;
    const hasImage = [...items].some(
      (i) => i.kind === "file" && i.type.startsWith("image/"));
    if (!hasImage) return;          // plain text: not ours, let it through
    ev.preventDefault();
    ev.stopPropagation();
    pasteImages(items);
  }, true);

  document.onclick = (ev) => {
    if (!$("#menu").contains(ev.target)) $("#menu").hidden = true;
  };

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
    if ((ev.ctrlKey || ev.metaKey) && ev.key === "b") {
      ev.preventDefault();
      setSidebar($("#sidebar").hidden);
    }
    // Alt+1..9 rather than plain digits: the terminal owns the keyboard.
    if (ev.altKey && ev.key >= "1" && ev.key <= "9") {
      const target = openTabs[Number(ev.key) - 1];
      if (target) { ev.preventDefault(); selectTab(target); }
    }
    if (ev.key === "Escape") {
      // Guarded: closing the palette hands focus back, and doing that when it
      // was never open would steal focus from wherever it actually is.
      if (!$("#palette").hidden) closePalette();
      $("#modal").hidden = true;
      $("#menu").hidden = true;
      $("#settings").hidden = true;
      $("#keys").hidden = true;
      // A full image goes back to the grid first; Escape twice leaves.
      if (!$("#art").hidden && !$("#artOne").hidden) showArtifactGrid();
      else closeArtifacts();
    }
  };

  addEventListener("resize", refitAll);
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
      detail: shortPath(s.cwd),
      match: s.name + " " + s.cwd + " " + (s.cli_label || ""),
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

  add("New session", "Start a CLI in a directory", openModal);
  add("New folder", "Group sessions in the sidebar", newFolder);
  add("Adopt sessions", "Take over tmux sessions CLIque did not start", adoptSessions);
  add("Settings", "Themes, markers, snippets, notifications", openSettings);
  add("Toggle sidebar", "Ctrl+B", () => setSidebar($("#sidebar").hidden));
  add("Keyboard shortcuts", "Every binding, in one list", showKeys);
  add("System history", "cpu and memory over the last hour", showHistory);
  add("Resume a past conversation", "Every transcript your CLIs have kept",
      () => openPalette("~"));

  if (current) {
    add("Rename session", current.name, () => renameSession(current));
    add(current.archived ? "Unarchive session" : "Archive session",
        current.name + " — nothing is killed either way",
        () => setArchived(current, !current.archived));
    add("Copy working directory", current.cwd, () => copyText(current.cwd));
    add("Focus the terminal", current.name, focusTerminal);
    add(following(current.id) ? "Scroll lock — stop following output"
                              : "Follow output again",
        "Ctrl+Shift+L · scrolling up does it too", toggleFollow);
    add("Close tab", "The session keeps running in tmux", () => closeTab(current.id));
  }
  if (state.settings.input_mode !== "terminal") {
    add("Focus the prompt box", "Type a prompt instead of driving the pane",
        () => $("#prompt").focus());
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
        current.alive ? "Stops the CLI for good" : "Nothing is running — removes it from the sidebar",
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
  // Give focus back to whatever had it, so opening the palette and changing
  // your mind costs nothing — including the terminal you were typing into.
  if (palReturnTo && document.contains(palReturnTo)) palReturnTo.focus();
  else focusTerminal();
  palReturnTo = null;
}

function renderPalette() {
  const raw = $("#palQ").value;
  const mode = raw[0] === ">" ? "command"
             : raw[0] === "@" ? "session"
             : raw[0] === "~" ? "resume" : "all";
  const query = (mode === "all" ? raw : raw.slice(1)).trim().toLowerCase();

  let pool = [];
  if (mode !== "command") pool = pool.concat(paletteSessions());
  if (mode !== "session") pool = pool.concat(paletteCommands());
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
  item.run();
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
      refitAll();
    };

    handle.onpointermove = move;
    handle.onpointerup = up;
  };

  // Double-click the handle to go back to the default, the same way a window
  // manager treats a double-clicked edge.
  handle.ondblclick = () => { setSidebarWidth(SIDEBAR_DEFAULT, true); refitAll(); };

  // Keyboard, because a drag handle that only takes a mouse is not a control.
  handle.onkeydown = (ev) => {
    const step = ev.shiftKey ? 32 : 8;
    const current = parseInt(getComputedStyle(document.documentElement)
      .getPropertyValue("--sidebar-w"), 10) || SIDEBAR_DEFAULT;
    if (ev.key === "ArrowLeft") setSidebarWidth(current - step, true);
    else if (ev.key === "ArrowRight") setSidebarWidth(current + step, true);
    else return;
    ev.preventDefault();
    refitAll();
  };
}

function refitAll() {
  // Only the visible terminal can be measured; the rest refit when selected.
  const entry = terms.get(activeId);
  if (entry) { try { entry.fit.fit(); } catch (err) { /* not visible */ } }
}

function setSidebar(show) {
  $("#sidebar").hidden = !show;
  $("#resizer").hidden = !show;
  $("#rail").hidden = show;
  localStorage.setItem("clique.sidebar", show ? "1" : "0");
  // Re-apply the stored width on the way back in, so collapsing and expanding
  // returns the sidebar you had rather than the default one.
  if (show) setSidebarWidth(storedSidebarWidth(), false);
  setTimeout(refitAll, 0);
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

wire();
wireResizer();
wireTouchMenus();
setSidebarWidth(storedSidebarWidth(), false);
setSidebar(localStorage.getItem("clique.sidebar") !== "0");
refresh().then(async () => {
  // Re-open whatever was open last, so a reload is not a fresh start — and on
  // a second device, so signing in there is not a fresh start either.
  const want = pendingWorkspace || { tabs: [], active: "" };
  await Promise.all(want.tabs.filter((id) => session(id)).map(openSession));
  // Chosen last: every openSession above selects its own tab as it finishes,
  // in whatever order the sockets came up.
  if (want.active && openTabs.includes(want.active)) selectTab(want.active);
  // Now that the tabs exist, commit: this is what persists a lifted copy, and
  // what prunes tabs whose sessions are gone.
  saveWorkspace(true);
});
setInterval(refresh, 3000);
// Slower than the sidebar poll on purpose: this one touches a filesystem, and
// nobody is waiting on a screenshot to the second.
setInterval(pollArtifacts, ART_POLL_MS);
// Coming back to the tab should not mean waiting out the interval.
addEventListener("visibilitychange", () => { if (!document.hidden) pollArtifacts(); });
