/* Model providers, palette, and pane interaction helpers.
 *
 * Loaded before app.js as a classic script. These functions run only after
 * app.js has initialised the shared panel state and helpers they use.
 */
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
  for (const g of groups()) {
    const live = (g.members || []).filter((m) => (session(m.session) || {}).alive).length;
    add("Open group: " + g.name,
        `${(g.members || []).length} sessions · ${live} already running`,
        () => openGroup(g));
  }
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

/* Whether the pane should be handed the keyboard when nobody asked it to be.
 *
 * On a desktop, yes: the pane is where you type unless you deliberately went
 * to the prompt box. On a phone showing the panel's box it is the opposite,
 * and this is not a preference. Focusing xterm's hidden textarea is what puts
 * the phone keyboard's input method back on the terminal, which is the exact
 * path 0.66.0 moved typing off because Android duplicates the line down it.
 * Showing the box and then focusing the pane anyway left the fix half done.
 *
 * Focus nothing instead and let the first tap decide. `focusTerminal` itself
 * is untouched: Escape out of the prompt box is somebody asking. */
function paneWantsFocus() {
  return !(handheld() && promptWanted());
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
    if (BARE_HOST.test(raw)) continue;
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

/* How close to the pane's top or bottom edge a drag has to get before it
 * starts pulling more history into view, and how often it pulls a line while
 * it stays there. Selecting something longer than the window has to mean
 * dragging past the edge, the way it does in any text editor. */
const PANE_EDGE_SCROLL_PX = 24;
const PANE_EDGE_SCROLL_MS = 50;

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

/* Local echo. A keystroke goes to tmux and comes back as output before it is
 * drawn, so on a slow link every letter waits a round trip and the pane feels
 * like a web page. Draw the letter now, in a layer above the terminal, and
 * take it away as the real echo lands. Nothing is written into xterm's buffer:
 * a CLI that repaints its line would fight a guess put there.
 *
 * It only guesses where a guess is safe: plain ASCII typed on the main screen
 * with a visible cursor, on a line that is not asking for a password. Anything
 * else (Enter, arrows, a paste, a cursor that went somewhere unexpected) drops
 * the guess. A guess that nothing confirms within ECHO_WAIT_MS means the CLI is
 * not echoing there, so it stops guessing until the next Enter. */
const ECHO_WAIT_MS = 1000;
const ECHO_SECRET = /pass(word|phrase|code)|\bPIN\b/i;

function echoClear(entry) {
  clearTimeout(entry.echoTimer);
  entry.echo = null;
  if (entry.echoEl) entry.echoEl.textContent = "";
}

function echoGuessable(term) {
  const buf = term.buffer.active;
  if (buf.type !== "normal") return false;
  try { if (term._core.coreService.isCursorHidden) return false; } catch (err) { /* xterm internals moved */ }
  const line = buf.getLine(buf.baseY + buf.cursorY);
  return !(line && ECHO_SECRET.test(line.translateToString(true)));
}

function echoKey(entry, data) {
  if (state.settings.local_echo === false || !entry.term) return;
  if (data === "\r" || data === "\n") { entry.echoOff = false; return echoClear(entry); }
  const e = entry.echo;
  if (data === "\u007f" && e && e.text) { e.text = e.text.slice(0, -1); return echoDraw(entry); }
  if (!/^[ -~]$/.test(data) || entry.echoOff) return echoClear(entry);
  const term = entry.term, buf = term.buffer.active;
  if (!e) {
    if (!echoGuessable(term)) return;
    entry.echo = { text: "", x: buf.cursorX, y: buf.baseY + buf.cursorY };
  }
  if (entry.echo.x + entry.echo.text.length + 1 >= term.cols) return echoClear(entry);
  entry.echo.text += data;
  echoDraw(entry);
}

/* After each write: whatever the cursor advanced past has really been echoed,
 * so that much of the guess goes. A cursor on another row, or behind where
 * the guess started, means the CLI did something else; the guess is dropped. */
function echoSettle(entry) {
  const e = entry.echo;
  if (!e || !entry.term) return;
  const buf = entry.term.buffer.active;
  const moved = buf.cursorX - e.x;
  if (buf.type !== "normal" || buf.baseY + buf.cursorY !== e.y || moved < 0) return echoClear(entry);
  e.text = e.text.slice(moved);
  e.x = buf.cursorX;
  if (!e.text) return echoClear(entry);
  echoDraw(entry);
}

function echoDraw(entry) {
  const e = entry.echo, term = entry.term;
  if (!e || !e.text) return echoClear(entry);
  let el = entry.echoEl;
  const screen = term.element && term.element.querySelector(".xterm-screen");
  if (!screen) return;
  if (!el || el.parentNode !== screen) {
    el = entry.echoEl = document.createElement("div");
    el.className = "local-echo";
    screen.appendChild(el);
  }
  const cell = paneCellPx(term), theme = term.options.theme || {};
  el.style.left = e.x * cell.w + "px";
  el.style.top = (e.y - term.buffer.active.viewportY) * cell.h + "px";
  el.style.font = `${term.options.fontSize}px/${cell.h}px ${term.options.fontFamily}`;
  el.style.color = theme.foreground || "";
  el.style.background = theme.background || "";
  el.replaceChildren(...[...e.text].map((ch) => {
    const c = document.createElement("span");
    c.style.width = cell.w + "px";
    c.textContent = ch;
    return c;
  }));
  clearTimeout(entry.echoTimer);
  entry.echoTimer = setTimeout(() => {
    if (entry.echo && entry.echo.text) { entry.echoOff = true; echoClear(entry); }
  }, ECHO_WAIT_MS);
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

// -1 past the top edge, 1 past the bottom, 0 still inside. Plain numbers
// rather than a rect object, so this is the same call with a real
// getBoundingClientRect() or a made-up one in a test.
function paneEdgeScrollDir(y, top, bottom, edge) {
  if (y < top + edge) return -1;
  if (y > bottom - edge) return 1;
  return 0;
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
    // The click that follows a mouseup arrives in the same task, so a timer
    // runs after it. Without this, a release with no click after it (off the
    // window, say) left the catcher armed to eat the next click anywhere.
    const dropSwallow = () => {
      setTimeout(() => document.removeEventListener("click", swallowClick, true));
    };
    const paint = (x, y) => paneSelectRange(term, paneCellAt(term, startX, startY),
                                            paneCellAt(term, x, y));
    // A drag that reaches past the top or bottom edge keeps scrolling that
    // direction — using the last known pointer position, so each tick both
    // reveals another line and stretches the selection to include it, the
    // same as it would if the pointer had actually moved that far.
    let lastX = startX, lastY = startY;
    let edgeTimer = null;
    const stopEdgeScroll = () => {
      if (edgeTimer) { clearInterval(edgeTimer); edgeTimer = null; }
    };
    const edgeDir = (y) => {
      const r = host.getBoundingClientRect();
      return paneEdgeScrollDir(y, r.top, r.bottom, PANE_EDGE_SCROLL_PX);
    };
    // Nothing to reveal past an edge that is already the top or bottom of
    // what the pane holds — a boxed CLI with no scrollback yet is stuck at
    // both, and scrolling it anyway blanked the one line it had.
    const canScroll = (dir) => {
      const buf = term.buffer.active;
      return dir < 0 ? buf.viewportY > 0 : buf.viewportY < buf.baseY;
    };
    const startEdgeScroll = (dir) => {
      if (edgeTimer) return;
      edgeTimer = setInterval(() => {
        if (!canScroll(dir)) { stopEdgeScroll(); return; }
        term.scrollLines(dir);
        paint(lastX, lastY);
      }, PANE_EDGE_SCROLL_MS);
    };
    if (e.detail >= 2) {
      const at = paneCellAt(term, e.clientX, e.clientY);
      try { term.selectLines(at.y, at.y); } catch (err) { /* disposed */ }
      document.addEventListener("click", swallowClick, true);
      const done = () => {
        document.removeEventListener("mouseup", done, true);
        dropSwallow();
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
      lastX = ev.clientX;
      lastY = ev.clientY;
      paint(lastX, lastY);
      const dir = edgeDir(lastY);
      if (dir && canScroll(dir)) startEdgeScroll(dir); else stopEdgeScroll();
    };
    const onUp = (ev) => {
      document.removeEventListener("mousemove", onMove, true);
      document.removeEventListener("mouseup", onUp, true);
      stopEdgeScroll();
      if (mode === "pending") {
        document.removeEventListener("click", swallowClick, true);
        play(e, "mousedown");
        play(ev, "mouseup");
        play(ev, "click");
      } else {
        dropSwallow();
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
   * never scrolled here before, finally scroll.
   *
   * Carried, not floored. A physical notch used to arrive as one `wheel`
   * event with deltaY around 100; a trackpad or a precision mouse instead
   * fires dozens of tiny ones (deltaY 1-10) for the same gesture. Flooring
   * each event to at least one tick — the old `Math.max(1, ...)` — sent a
   * tick for every one of those tiny events, so a trackpad scroll landed
   * several times more lines than the same physical motion on a notched
   * wheel: scrolling that felt like it had a mind of its own. Pixels now
   * accumulate across events, same as the touch handler below, and a tick
   * only fires once the total crosses the threshold.
   *
   * Normalized to pixels first. Chrome and Safari already report deltaY in
   * pixels (deltaMode 0), but Firefox's default mouse wheel reports it in
   * lines (deltaMode 1, deltaY around 3 per notch) — accumulating that
   * against a 24px threshold would need eight notches before the first
   * tick, eight times less sensitive than before for exactly the browser
   * that made the old per-event floor look correct by accident. */
  const WHEEL_PX_PER_TICK = 24;
  let wheelCarried = 0;
  host.addEventListener("wheel", (e) => {
    if (!sessionOwnsInput(sessionId)) return;
    let dy = e.deltaY;
    if (e.deltaMode === 1) dy *= (paneCellPx(term).h || 17);       // lines
    else if (e.deltaMode === 2) dy *= (host.clientHeight || 400);  // pages
    wheelCarried += dy;
    const ticks = Math.trunc(Math.abs(wheelCarried) / WHEEL_PX_PER_TICK);
    if (ticks) {
      const dir = wheelCarried < 0 ? -1 : 1;
      wheelCarried -= dir * ticks * WHEEL_PX_PER_TICK;
      const s = session(sessionId);
      if (s && s.alt) {
        sendPaneWheel(term, e.clientX, e.clientY, dir < 0, ticks, sessionId);
      } else {
        term.scrollLines(dir * ticks);
      }
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
