/* Notes, Git, Session info and Export panes.
 *
 * Loaded before app.js as a classic script. These functions run only after
 * app.js has initialised the shared panel state and helpers they use.
 */
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
  if (typeof s.cpu === "number" && s.cpu > 0) kv.append(kvRow("CPU", `${s.cpu.toFixed(0)}%`));
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

