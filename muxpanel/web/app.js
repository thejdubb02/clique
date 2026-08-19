/* muxpanel front end.
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
let showArchived = false;
/* Sessions that were producing output on the previous poll. A busy -> quiet
 * transition is what "this one finished" means here, which is why it needs a
 * memory of the last poll rather than just the current state. */
const wasBusy = new Map();
const attention = new Set();   // session ids waiting to be looked at

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
  // When the icon carries status, the CLI's own colour steps aside: shape
  // already says which CLI it is, so colour is free to say how it is doing.
  const item = { color: statusOnIcon(s) ? statusColor(s) : s.color,
                 icon: s.icon, icon_full_color: s.icon_full_color,
                 label: s.cli_label, cli: s.cli };
  return markerFor(item, statusOnIcon(s) && mode === "icon" ? "both" : mode);
}

/* Whether this session's marker is standing in for the status dot.
 *
 * Only when there is actually a marker to carry it. With the marker turned
 * off — globally or for this CLI — the dot has to come back, because losing
 * status altogether is a worse trade than showing two small marks. */
function statusOnIcon(s) {
  if (!state.settings.status_on_icon) return false;
  // A full-colour logo cannot carry a status colour — there is nothing to
  // tint — so those sessions keep the separate dot.
  if (s.icon_full_color && markerMode(s.cli) !== "color") return false;
  return markerMode(s.cli) !== "none";
}

function statusDot(s, where) {
  const on = where === "tabs" ? state.settings.markers_in_tabs
                              : state.settings.markers_in_sidebar;
  if (on !== false && statusOnIcon(s)) return "";
  return `<i class="dot" style="background:${statusColor(s)}"></i>`;
}

function statusColor(s) {
  if (!s.alive) return "var(--dead)";
  return s.attached ? "var(--ok)" : "var(--warn)";
}

/* --------------------------------------------------------------------- state */

async function refresh() {
  try {
    state = await api("api/state");
  } catch (err) {
    return;  // a blip mid-poll should not blank the UI
  }
  // A session killed behind our back keeps its tab until the user closes it,
  // but must not keep a dead socket open.
  openTabs = openTabs.filter((id) => session(id));
  applySettings();
  noticeFinished(state.sessions.filter((x) => openTabs.includes(x.id)));
  renderTree();
  renderTabs();
  renderStats();
  $("#version").textContent = "v" + state.version;
}

function renderStats() {
  const st = state.stats || {};
  const gb = (mb) => Math.round((mb || 0) / 1024 * 10) / 10;

  $("#cpu").textContent = "cpu " + (st.cpu ?? 0) + "%";
  $("#mem").textContent = "mem " + gb(st.mem?.used_mb) + "/" +
                          Math.round((st.mem?.total_mb || 0) / 1024) + "G";

  // Load against core count, because "1.4" means nothing without knowing the
  // box. Over 1.0 per core is a queue, and it turns amber.
  const load = st.load || {};
  const loadEl = $("#load");
  loadEl.textContent = "load " + (load.one ?? 0).toFixed(2);
  loadEl.title = `${load.one} / ${load.five} / ${load.fifteen} over ${load.cores} cores`;
  loadEl.classList.toggle("warn", (load.ratio || 0) > 1);

  // Disk is the quietest way to lose an afternoon here: everything starts
  // failing in ways that never mention disk.
  const disk = st.disk || {};
  const diskEl = $("#disk");
  diskEl.textContent = "disk " + (disk.free_gb ?? 0) + "G free";
  diskEl.classList.toggle("warn", (disk.percent || 0) > 90);

  // Any swap in use means memory pressure already happened, so it only
  // appears when there is something to say.
  const swap = st.swap || {};
  const swapEl = $("#swap");
  swapEl.hidden = !(swap.used_mb > 0);
  swapEl.textContent = "swap " + gb(swap.used_mb) + "G";
  swapEl.classList.toggle("warn", (swap.percent || 0) > 25);

  $("#clients").innerHTML = '<i class="dot"></i>' + (st.clients ?? 0);
}

/* ------------------------------------------------------------ stats history */

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
  const running = live.filter((s) => s.alive && openTabs.includes(s.id));
  if (running.length) groups.push({ id: "__running", name: "Running", color: "#2d7d46", pinned: true, sessions: running });

  for (const folder of state.folders) {
    groups.push({
      ...folder,
      sessions: live.filter((s) => s.folder === folder.id && !running.includes(s)),
    });
  }
  const unfiled = live.filter(
    (s) => !running.includes(s) && !state.folders.some((f) => f.id === s.folder));
  if (unfiled.length) groups.push({ id: "__unfiled", name: "Ungrouped", color: "#8b8b8b", sessions: unfiled });

  // Archived last, collapsed unless asked for. Archiving never touched tmux,
  // so everything here is still running and one click from coming back.
  const archived = state.sessions.filter((s) => s.archived);
  if (archived.length) {
    groups.push({ id: "__archived", name: "Archived", color: "#5a5a5a",
                  collapsed: !showArchived, sessions: archived });
  }

  for (const group of groups) {
    const shown = group.sessions.filter(matches);
    if (query && !shown.length) continue;

    const head = document.createElement("div");
    head.className = "folder-head";
    head.dataset.folder = group.id;
    head.innerHTML =
      `<span class="caret">${group.collapsed ? "▸" : "▾"}</span>` +
      `<i class="dot" style="background:${group.color}"></i>` +
      `<span class="name">${escapeHtml(group.name)}</span>` +
      `<span class="count">${shown.length}</span>`;
    head.onclick = () => toggleFolder(group);
    if (!group.pinned && group.id.startsWith("f-")) {
      head.oncontextmenu = (ev) => folderMenu(ev, group);
    }
    wireDrop(head, group.id);
    tree.appendChild(head);

    if (group.collapsed && !query) continue;
    for (const s of shown) tree.appendChild(sessionRow(s));
  }

  const dots = $("#railDots");
  dots.innerHTML = "";
  for (const s of state.sessions.filter((x) => x.alive)) {
    const dot = document.createElement("i");
    dot.className = "dot";
    dot.style.background = statusColor(s);
    dot.title = s.name;
    dot.onclick = () => openSession(s.id);
    dots.appendChild(dot);
  }
}

function sessionRow(s) {
  const row = document.createElement("div");
  row.className = "session" + (s.id === activeId ? " active" : "") +
    (s.alive ? "" : " dead") + (s.busy ? " busy" : "") +
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
  if (group.id === "__archived") {
    showArchived = !showArchived;
    return renderTree();
  }
  if (!group.id.startsWith("f-")) return;
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

function sessionMenu(ev, s) {
  showMenu(ev, [
    ["Open", () => openSession(s.id)],
    ["Rename", () => renameSession(s)],
    [s.archived ? "Unarchive" : "Archive", () => setArchived(s, !s.archived)],
    ["Kill session", () => killSession(s), true],
  ]);
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
  const found = await api("api/adoptable");
  if (!found.length) return alert("Nothing to adopt — no other sessions found.");
  if (!confirm(`Adopt ${found.length} session(s) started by another tool?`)) return;
  const result = await api("api/sessions/adopt", { method: "POST", body: "{}" });
  await refresh();
  alert("Adopted: " + (result.adopted.join(", ") || "none"));
}

async function killSession(s) {
  if (!confirm(`Kill "${s.name}"? The CLI running in it is stopped for good.`)) return;
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
      (s.busy ? " busy" : "") + (attention.has(id) ? " attention" : "");
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
    bar.appendChild(tab);
  });
  renderInputBar();
}

function renderInputBar() {
  const s = session(activeId);
  const pill = $("#modePill");
  // The pill exists only for CLIs that declare modes. That falls out of the
  // registry config — there is no per-CLI branch here.
  if (s && s.modes && s.modes.length) {
    pill.hidden = false;
    pill.textContent = (s.mode || s.modes[0]) + " mode on (shift+tab to cycle)";
  } else {
    pill.hidden = true;
  }
  $("#empty").style.display = activeId ? "none" : "grid";
}

function selectTab(id) {
  activeId = id;
  attention.delete(id);   // looking at it is the acknowledgement
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
}

async function openSession(id) {
  if (!openTabs.includes(id)) openTabs.push(id);
  if (!terms.has(id)) await attach(id);
  selectTab(id);
}

function closeTab(id, silent) {
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
  if (!silent) { renderTabs(); renderTree(); selectTab(activeId); }
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
    if (ev.shiftKey && key === "p") return false;
    if (!ev.shiftKey && key === "k") return !paletteHotkeyOn();
    return true;
  });

  const entry = { term, fit, el: host, ws: null, closing: false, typed: "" };
  terms.set(id, entry);

  const connect = () => {
    const ws = new WebSocket(wsUrl(id, term.cols, term.rows));
    ws.binaryType = "arraybuffer";
    entry.ws = ws;

    ws.onmessage = (ev) => {
      if (typeof ev.data === "string") term.write(ev.data);
      else term.write(new Uint8Array(ev.data));
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
    /* Snippets work in the CLI's own input field too, because muxpanel owns
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
  const themes = window.MUXPANEL_THEMES || {};
  const s = state.settings;
  if (s.theme && themes[s.theme]) return themes[s.theme];
  // No preset chosen: the base appearance picks which built-in to use.
  const wantsLight = s.appearance === "light" ||
    (s.appearance === "system" && matchMedia("(prefers-color-scheme: light)").matches);
  return themes[wantsLight ? "light" : ""] || themes[""];
}

function applySettings() {
  const s = state.settings;
  const theme = currentTheme();
  const root = document.documentElement;

  for (const [name, value] of Object.entries(theme.panel || {})) {
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
  renderTree();
  renderTabs();
}

function openSettings() {
  const s = state.settings;

  const themeSelect = $("#setTheme");
  themeSelect.innerHTML = "";
  for (const [id, theme] of Object.entries(window.MUXPANEL_THEMES || {})) {
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
  $("#setStatusOnIcon").checked = !!s.status_on_icon;
  $("#setTabsMarkers").checked = s.markers_in_tabs !== false;
  $("#setSidebar").checked = s.markers_in_sidebar !== false;
  $("#setFlash").checked = s.notify_flash !== false;
  $("#setSound").checked = !!s.notify_sound;
  $("#setIdle").value = s.notify_idle_seconds || 4;
  $("#outIdle").textContent = s.notify_idle_seconds || 4;

  $("#cssBoth").value = s.css_both || "";
  $("#cssPanel").value = s.css_panel || "";
  $("#cssTerminal").value = s.css_terminal || "";
  $("#aboutVersion").textContent = "version " + (state.version || "");

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
      `<span class="preview">${markerFor(cli, mode === "none" ? "both" : mode)}</span>` +
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
  };

  $("#setTheme").onchange = (ev) => saveSettings({ theme: ev.target.value });
  $("#setAppearance").onchange = (ev) => saveSettings({ appearance: ev.target.value });
  $("#setInputMode").onchange = (ev) => saveSettings({ input_mode: ev.target.value });
  $("#setPalette").onchange = (ev) => saveSettings({ palette_hotkey: ev.target.checked });
  $("#setStatusOnIcon").onchange = (ev) => saveSettings({ status_on_icon: ev.target.checked });
  $("#setTabsMarkers").onchange = (ev) => saveSettings({ markers_in_tabs: ev.target.checked });
  $("#setSidebar").onchange = (ev) => saveSettings({ markers_in_sidebar: ev.target.checked });
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

  $("#modePill").onclick = () => {
    const s = session(activeId);
    if (s && s.mode_key) control({ type: "key", key: s.mode_key });
  };

  $("#prompt").onkeydown = (ev) => {
    if (ev.key === "Tab" && expandInBox(ev.target)) { ev.preventDefault(); return; }
    if (ev.key === "Enter" && !ev.shiftKey) { ev.preventDefault(); run(ev.target.value); }
  };
  $("#prompt").oninput = (ev) => {
    ev.target.style.height = "auto";
    ev.target.style.height = Math.min(ev.target.scrollHeight, 140) + "px";
  };

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
    { color: s.color, icon: s.icon, icon_full_color: s.icon_full_color,
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
  add("Adopt sessions", "Take over tmux sessions muxpanel did not start", adoptSessions);
  add("Settings", "Themes, markers, snippets, notifications", openSettings);
  add("Toggle sidebar", "Ctrl+B", () => setSidebar($("#sidebar").hidden));
  add("System history", "cpu and memory over the last hour", showHistory);

  if (current) {
    add("Rename session", current.name, () => renameSession(current));
    add(current.archived ? "Unarchive session" : "Archive session",
        current.name + " — nothing is killed either way",
        () => setArchived(current, !current.archived));
    add("Copy working directory", current.cwd, () => copyText(current.cwd));
    add("Focus the terminal", current.name, focusTerminal);
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

  for (const [id, theme] of Object.entries(window.MUXPANEL_THEMES || {})) {
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
  if (current) add("Kill session", "Stops the CLI for good", () => killSession(current), true);
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
  const mode = raw[0] === ">" ? "command" : raw[0] === "@" ? "session" : "all";
  const query = (mode === "all" ? raw : raw.slice(1)).trim().toLowerCase();

  let pool = [];
  if (mode !== "command") pool = pool.concat(paletteSessions());
  if (mode !== "session") pool = pool.concat(paletteCommands());

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
  const saved = Number(localStorage.getItem("muxpanel.sidebarWidth"));
  if (!saved || Number.isNaN(saved)) return SIDEBAR_DEFAULT;
  return Math.min(Math.max(saved, SIDEBAR_MIN), SIDEBAR_MAX);
}

function setSidebarWidth(px, persist) {
  const width = Math.min(Math.max(Math.round(px), SIDEBAR_MIN), SIDEBAR_MAX);
  document.documentElement.style.setProperty("--sidebar-w", width + "px");
  if (persist) localStorage.setItem("muxpanel.sidebarWidth", String(width));
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
  localStorage.setItem("muxpanel.sidebar", show ? "1" : "0");
  // Re-apply the stored width on the way back in, so collapsing and expanding
  // returns the sidebar you had rather than the default one.
  if (show) setSidebarWidth(storedSidebarWidth(), false);
  setTimeout(refitAll, 0);
}

wire();
wireResizer();
setSidebarWidth(storedSidebarWidth(), false);
setSidebar(localStorage.getItem("muxpanel.sidebar") !== "0");
refresh().then(() => {
  // Re-open whatever was open last, so a reload is not a fresh start.
  const saved = JSON.parse(localStorage.getItem("muxpanel.tabs") || "[]");
  for (const id of saved) if (session(id)) openSession(id);
});
setInterval(refresh, 3000);
setInterval(() => localStorage.setItem("muxpanel.tabs", JSON.stringify(openTabs)), 2000);
