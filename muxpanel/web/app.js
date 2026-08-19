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

let state = { folders: [], sessions: [], clis: [], stats: {} };
let openTabs = [];            // session ids, in tab order
let activeId = null;
const terms = new Map();      // id -> { term, fit, ws, el, retry }
let repeat = 1;

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
  renderTree();
  renderTabs();
  renderStats();
  $("#version").textContent = "v" + state.version;
}

function renderStats() {
  const st = state.stats || {};
  $("#cpu").textContent = "cpu " + (st.cpu ?? 0) + "%";
  $("#mem").textContent = "mem " + Math.round((st.mem?.used_mb || 0) / 1024 * 10) / 10 +
                          "/" + Math.round((st.mem?.total_mb || 0) / 1024) + "G";
  $("#clients").innerHTML = '<i class="dot"></i>' + (st.clients ?? 0);
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
  const running = state.sessions.filter((s) => s.alive && openTabs.includes(s.id));
  if (running.length) groups.push({ id: "__running", name: "Running", color: "#2d7d46", pinned: true, sessions: running });

  for (const folder of state.folders) {
    groups.push({
      ...folder,
      sessions: state.sessions.filter((s) => s.folder === folder.id && !running.includes(s)),
    });
  }
  const unfiled = state.sessions.filter(
    (s) => !running.includes(s) && !state.folders.some((f) => f.id === s.folder));
  if (unfiled.length) groups.push({ id: "__unfiled", name: "Ungrouped", color: "#8b8b8b", sessions: unfiled });

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
  row.className = "session" + (s.id === activeId ? " active" : "") + (s.alive ? "" : " dead");
  row.draggable = true;
  row.dataset.id = s.id;
  row.innerHTML =
    `<i class="dot" style="background:${statusColor(s)}"></i>` +
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
    ["Rename", () => {
      const row = document.querySelector(`.session[data-id="${s.id}"]`);
      if (row) renameInline(row, s);
    }],
    ["Kill session", () => killSession(s), true],
  ]);
}

function folderMenu(ev, folder) {
  ev.stopPropagation();
  showMenu(ev, [
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
    tab.className = "tab" + (id === activeId ? " active" : "");
    tab.title = s.cwd;
    tab.innerHTML =
      `<span class="num">${index + 1}</span>` +
      `<i class="dot" style="background:${statusColor(s)}"></i>` +
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

  const term = new Terminal({
    fontSize: 13,
    fontFamily: 'ui-monospace, "SF Mono", Menlo, Consolas, monospace',
    theme: { background: "#1e1e1e", foreground: "#cccccc" },
    scrollback: 20000,
    cursorBlink: true,
    allowProposedApi: true,
  });
  const fit = new FitAddon.FitAddon();
  term.loadAddon(fit);
  term.open(host);
  fit.fit();

  const entry = { term, fit, el: host, ws: null, closing: false };
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

  term.onData((data) => {
    if (entry.ws && entry.ws.readyState === 1) {
      entry.ws.send(new TextEncoder().encode(data));
    }
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
  for (const cli of state.clis) {
    const option = document.createElement("option");
    option.value = cli.id;
    option.textContent = cli.label + (cli.installed ? "" : " (not installed)");
    option.disabled = !cli.installed;
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

/* ------------------------------------------------------------------- wiring */

function wire() {
  $("#q").oninput = renderTree;
  $("#newTab").onclick = openModal;
  $("#cancel").onclick = () => ($("#modal").hidden = true);
  $("#collapse").onclick = () => setSidebar(false);
  $("#expand").onclick = () => setSidebar(true);

  $("#newFolder").onclick = async () => {
    const name = prompt("Folder name");
    if (name) { await api("api/folders", { method: "POST", body: JSON.stringify({ name }) }); refresh(); }
  };

  $("#adopt").onclick = async () => {
    const found = await api("api/adoptable");
    if (!found.length) return alert("Nothing to adopt — no other sessions found.");
    if (!confirm(`Adopt ${found.length} session(s) started by another tool?`)) return;
    const result = await api("api/sessions/adopt", { method: "POST", body: "{}" });
    await refresh();
    alert("Adopted: " + (result.adopted.join(", ") || "none"));
  };

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
    if (ev.key === "Enter" && !ev.shiftKey) { ev.preventDefault(); run(ev.target.value); }
  };
  $("#prompt").oninput = (ev) => {
    ev.target.style.height = "auto";
    ev.target.style.height = Math.min(ev.target.scrollHeight, 140) + "px";
  };

  document.onclick = (ev) => {
    if (!$("#menu").contains(ev.target)) $("#menu").hidden = true;
  };

  document.onkeydown = (ev) => {
    if ((ev.ctrlKey || ev.metaKey) && ev.key === "b") {
      ev.preventDefault();
      setSidebar($("#sidebar").hidden);
    }
    // Alt+1..9 rather than plain digits: the terminal owns the keyboard.
    if (ev.altKey && ev.key >= "1" && ev.key <= "9") {
      const target = openTabs[Number(ev.key) - 1];
      if (target) { ev.preventDefault(); selectTab(target); }
    }
    if (ev.key === "Escape") { $("#modal").hidden = true; $("#menu").hidden = true; }
  };

  addEventListener("resize", () => {
    const entry = terms.get(activeId);
    if (entry) entry.fit.fit();
  });
}

function setSidebar(show) {
  $("#sidebar").hidden = !show;
  $("#rail").hidden = show;
  localStorage.setItem("muxpanel.sidebar", show ? "1" : "0");
  setTimeout(() => { const e = terms.get(activeId); if (e) e.fit.fit(); }, 0);
}

wire();
setSidebar(localStorage.getItem("muxpanel.sidebar") !== "0");
refresh().then(() => {
  // Re-open whatever was open last, so a reload is not a fresh start.
  const saved = JSON.parse(localStorage.getItem("muxpanel.tabs") || "[]");
  for (const id of saved) if (session(id)) openSession(id);
});
setInterval(refresh, 3000);
setInterval(() => localStorage.setItem("muxpanel.tabs", JSON.stringify(openTabs)), 2000);
