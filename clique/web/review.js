/* Review changes sheet.
 *
 * Loaded before app.js as a classic script. These functions run only after
 * app.js has initialised the shared panel state and helpers they use.
 */
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
