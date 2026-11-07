/* File, transcript, and usage preview sheet.
 *
 * Loaded before app.js as a classic script. These functions run only after
 * app.js has initialised the shared panel state and helpers they use.
 */
/* A read-only glance at a path the pane printed. Copy it, drop it in the
 * prompt, or look at the text / image. Not an editor — that is a tool the
 * person already has, and opening it is what Send path is for. */
let fileAsked = "";
let fileSession = "";
let currentFile = null;   // the info the file sheet last rendered, for editing
let fileEditing = false;
let fileShowSource = false;   // Markdown sheet: true while the raw <pre> is up

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
  $("#fileNote").hidden = false;
  $("#fileNote").innerHTML = loaderCells() + " Reading…";
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
  $("#fileNote").innerHTML = loaderCells() + " Looking…";
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
  fileShowSource = false;
  const doc = $("#fileDoc");
  if (doc) { doc.hidden = true; doc.textContent = ""; }
  const srcBtn = $("#fileSource");
  if (srcBtn) { srcBtn.hidden = true; srcBtn.textContent = "Source"; }
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

function isMarkdownPath(p) {
  return /\.(md|markdown|mdown|mkd)$/i.test(String(p || "").split(/[?#]/)[0]);
}

/* parseMarkdown: blocks from a string, no DOM. */
function mdHrefKind(href) {
  const u = String(href || "").trim();
  if (!u) return "empty";
  if (/^(https?:|mailto:)/i.test(u)) return "abs";
  if (/^[a-z][a-z0-9+.-]*:/i.test(u) || u.startsWith("//")) return "unsafe";
  return "rel";
}

function splitMdCells(line) {
  let s = String(line || "").trim();
  if (s.startsWith("|")) s = s.slice(1);
  if (s.endsWith("|")) s = s.slice(0, -1);
  const cells = [];
  let cur = "";
  for (let i = 0; i < s.length; i++) {
    if (s[i] === "\\" && i + 1 < s.length) { cur += s[i + 1]; i++; continue; }
    if (s[i] === "|") { cells.push(cur.trim()); cur = ""; continue; }
    cur += s[i];
  }
  cells.push(cur.trim());
  return cells;
}

function mdAlign(cell) {
  const t = String(cell || "").replace(/\s/g, "");
  if (!/^:?-+:?$/.test(t)) return null;
  const left = t.startsWith(":");
  const right = t.endsWith(":");
  if (left && right) return "center";
  if (right) return "right";
  if (left) return "left";
  return "";
}

function parseMarkdown(text) {
  const lines = String(text || "").replace(/\r\n?/g, "\n").split("\n");
  const fenceOpen = (line) => {
    const m = String(line).match(/^ {0,3}(`{3,}|~{3,})(.*)$/);
    if (!m) return null;
    return { ch: m[1][0], n: m[1].length, info: m[2].trim().split(/\s+/).filter(Boolean)[0] || "" };
  };
  const fenceClose = (line, open) => {
    const m = String(line).match(/^ {0,3}(`{3,}|~{3,})\s*$/);
    return !!(m && m[1][0] === open.ch && m[1].length >= open.n);
  };
  const heading = (line) => String(line).match(/^ {0,3}(#{1,6})(?: +(.*?))?\s*$/);
  const isHr = (line) =>
    /^ {0,3}(?:(?:-[\t ]*){3,}|(?:\*[\t ]*){3,}|(?:_[\t ]*){3,})\s*$/.test(line);
  const quoteStrip = (line) => {
    const m = String(line).match(/^ {0,3}> ?(.*)$/);
    return m ? m[1] : null;
  };
  const listMark = (line) => {
    const m = String(line || "").match(/^( *)([-*+]|\d+\.) (.*)$/);
    if (!m) return null;
    return { indent: m[1].length, ordered: /\d/.test(m[2]), start: parseInt(m[2], 10) || 1, rest: m[3] };
  };
  const looksTable = (i) => {
    if (i + 1 >= lines.length) return false;
    if (lines[i].indexOf("|") < 0) return false;
    const header = splitMdCells(lines[i]);
    const sep = splitMdCells(lines[i + 1]);
    return header.length > 0 && sep.length > 0 && sep.every((c) => mdAlign(c) !== null);
  };
  const isInterrupt = (i) => {
    const line = lines[i];
    if (/^\s*$/.test(line)) return true;
    if (fenceOpen(line) || heading(line) || isHr(line) || listMark(line)) return true;
    if (quoteStrip(line) !== null) return true;
    if (looksTable(i)) return true;
    return false;
  };
  const pad = (cells, n) => {
    const out = cells.slice(0, n);
    while (out.length < n) out.push("");
    return out;
  };
  const parseList = (startI, base, depth) => {
    const first = listMark(lines[startI]);
    const ordered = first.ordered;
    const items = [];
    let i = startI;
    while (i < lines.length) {
      const mark = listMark(lines[i]);
      if (!mark || mark.indent !== base || mark.ordered !== ordered) break;
      let itemText = mark.rest;
      let nested = null;
      i++;
      while (i < lines.length) {
        const line = lines[i];
        if (/^\s*$/.test(line)) break;
        const sub = listMark(line);
        if (sub && sub.indent === base) break;
        if (sub && sub.indent < base) break;
        if (sub && sub.indent > base) {
          if (depth < 1) {
            const got = parseList(i, sub.indent, depth + 1);
            nested = got.block;
            i = got.i;
            continue;
          }
          itemText += "\n" + sub.rest;
          i++;
          continue;
        }
        const lead = (line.match(/^ */) || [""])[0].length;
        if (lead > base) {
          itemText += "\n" + line.trim();
          i++;
          continue;
        }
        break;
      }
      const item = { inlines: parseInlines(itemText) };
      if (nested) item.children = nested;
      items.push(item);
    }
    return { block: { type: "list", ordered, start: first.start, items }, i };
  };

  const blocks = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (/^\s*$/.test(line)) { i++; continue; }
    const open = fenceOpen(line);
    if (open) {
      i++;
      const body = [];
      while (i < lines.length && !fenceClose(lines[i], open)) body.push(lines[i++]);
      if (i < lines.length) i++;
      blocks.push({ type: "code", lang: open.info, text: body.join("\n") });
      continue;
    }
    const h = heading(line);
    if (h) {
      const title = (h[2] || "").replace(/\s+#+\s*$/, "").trim();
      blocks.push({ type: "heading", level: h[1].length, inlines: parseInlines(title) });
      i++;
      continue;
    }
    if (isHr(line)) { blocks.push({ type: "hr" }); i++; continue; }
    if (looksTable(i)) {
      const headerCells = splitMdCells(lines[i]);
      const sep = splitMdCells(lines[i + 1]);
      const align = headerCells.map((_, k) => mdAlign(sep[k] || "") || "");
      const header = headerCells.map(parseInlines);
      i += 2;
      const rows = [];
      while (i < lines.length && lines[i].indexOf("|") >= 0 && !/^\s*$/.test(lines[i])
          && !fenceOpen(lines[i]) && !isHr(lines[i]) && !heading(lines[i])) {
        rows.push(pad(splitMdCells(lines[i]), header.length).map(parseInlines));
        i++;
      }
      blocks.push({ type: "table", header, align, rows });
      continue;
    }
    if (listMark(line)) {
      const got = parseList(i, listMark(line).indent, 0);
      blocks.push(got.block);
      i = got.i;
      continue;
    }
    const quoted = quoteStrip(line);
    if (quoted !== null) {
      const inner = [];
      while (i < lines.length) {
        const q = quoteStrip(lines[i]);
        if (q === null) break;
        inner.push(q);
        i++;
      }
      blocks.push({ type: "blockquote", children: parseMarkdown(inner.join("\n")) });
      continue;
    }
    const buf = [line];
    i++;
    while (i < lines.length && !isInterrupt(i)) buf.push(lines[i++]);
    blocks.push({ type: "paragraph", inlines: parseInlines(buf.join("\n")) });
  }
  return blocks;
}

function takeMdLink(text, i) {
  if (text[i] !== "[") return null;
  let j = i + 1;
  while (j < text.length) {
    if (text[j] === "\\") { j += 2; continue; }
    if (text[j] === "]" && text[j + 1] === "(") {
      let k = j + 2;
      let depth = 1;
      while (k < text.length) {
        if (text[k] === "\\") { k += 2; continue; }
        if (text[k] === "(") depth++;
        else if (text[k] === ")") {
          depth--;
          if (depth === 0) {
            let href = text.slice(j + 2, k).trim();
            if (href.startsWith("<") && href.endsWith(">")) href = href.slice(1, -1).trim();
            return { alt: text.slice(i + 1, j), href, end: k + 1 };
          }
        }
        k++;
      }
      return null;
    }
    if (text[j] === "]") return null;
    j++;
  }
  return null;
}

function findMdDelim(text, from, delim) {
  let j = from;
  while (j < text.length) {
    if (text[j] === "\\") { j += 2; continue; }
    if (text[j] === "`") {
      const c = text.indexOf("`", j + 1);
      if (c < 0) return -1;
      j = c + 1;
      continue;
    }
    if (text.startsWith(delim, j)) return j;
    j++;
  }
  return -1;
}

function parseInlines(text) {
  text = String(text || "");
  const out = [];
  let i = 0;
  let buf = "";
  const pushText = (s) => {
    if (!s) return;
    const last = out[out.length - 1];
    if (last && last.type === "text") last.text += s;
    else out.push({ type: "text", text: s });
  };
  const flush = () => { pushText(buf); buf = ""; };
  while (i < text.length) {
    const ch = text[i];
    if (ch === "\\" && i + 1 < text.length) { buf += text[i + 1]; i += 2; continue; }
    if (ch === "`") {
      const close = text.indexOf("`", i + 1);
      if (close >= 0) {
        flush();
        out.push({ type: "code", text: text.slice(i + 1, close) });
        i = close + 1;
        continue;
      }
    }
    if (ch === "!" && text[i + 1] === "[") {
      const got = takeMdLink(text, i + 1);
      if (got) {
        flush();
        const kind = mdHrefKind(got.href);
        const alt = got.alt.replace(/\\(.)/g, "$1");
        if (kind === "unsafe" || kind === "empty") pushText(alt);
        else out.push({ type: "image", src: got.href, alt });
        i = got.end;
        continue;
      }
    }
    if (ch === "[") {
      const got = takeMdLink(text, i);
      if (got) {
        flush();
        const kind = mdHrefKind(got.href);
        const kids = parseInlines(got.alt);
        if (kind === "unsafe" || kind === "empty") {
          for (const k of kids) {
            if (k.type === "text") pushText(k.text);
            else out.push(k);
          }
        } else out.push({ type: "link", href: got.href, children: kids });
        i = got.end;
        continue;
      }
    }
    if (ch === "_" && /[A-Za-z0-9]/.test(text[i - 1] || "")) { buf += ch; i++; continue; }
    if (ch === "*" || ch === "_") {
      const dbl = text.startsWith(ch + ch, i);
      const delim = dbl ? ch + ch : ch;
      const close = findMdDelim(text, i + delim.length, delim);
      if (close >= 0 && close > i + delim.length) {
        flush();
        out.push({
          type: dbl ? "strong" : "em",
          children: parseInlines(text.slice(i + delim.length, close)),
        });
        i = close + delim.length;
        continue;
      }
    }
    buf += ch;
    i++;
  }
  flush();
  return out;
}

function renderMarkdown(blocks, opts) {
  opts = opts || {};
  const frag = document.createDocumentFragment();
  for (const b of blocks || []) {
    const el = mdBlock(b, opts);
    if (el) frag.append(el);
  }
  return frag;
}

function mdBlock(b, opts) {
  if (!b) return null;
  if (b.type === "heading") {
    const level = Math.min(6, Math.max(1, b.level || 1));
    const el = document.createElement("h" + level);
    mdInlines(el, b.inlines, opts);
    return el;
  }
  if (b.type === "paragraph") {
    const el = document.createElement("p");
    mdInlines(el, b.inlines, opts);
    return el;
  }
  if (b.type === "code") {
    const pre = document.createElement("pre");
    const code = document.createElement("code");
    if (b.lang) code.className = "language-" + b.lang;
    code.textContent = b.text || "";
    pre.append(code);
    return pre;
  }
  if (b.type === "blockquote") {
    const el = document.createElement("blockquote");
    for (const c of b.children || []) {
      const kid = mdBlock(c, opts);
      if (kid) el.append(kid);
    }
    return el;
  }
  if (b.type === "hr") return document.createElement("hr");
  if (b.type === "list") {
    const el = document.createElement(b.ordered ? "ol" : "ul");
    if (b.ordered && b.start && b.start !== 1) el.start = b.start;
    for (const item of b.items || []) {
      const li = document.createElement("li");
      mdInlines(li, item.inlines, opts);
      if (item.children) {
        const nested = mdBlock(item.children, opts);
        if (nested) li.append(nested);
      }
      el.append(li);
    }
    return el;
  }
  if (b.type === "table") {
    const table = document.createElement("table");
    const thead = document.createElement("thead");
    const headRow = document.createElement("tr");
    (b.header || []).forEach((cell, i) => {
      const th = document.createElement("th");
      const al = (b.align || [])[i];
      if (al) th.style.textAlign = al;
      mdInlines(th, cell, opts);
      headRow.append(th);
    });
    thead.append(headRow);
    table.append(thead);
    const tbody = document.createElement("tbody");
    for (const row of b.rows || []) {
      const tr = document.createElement("tr");
      (row || []).forEach((cell, i) => {
        const td = document.createElement("td");
        const al = (b.align || [])[i];
        if (al) td.style.textAlign = al;
        mdInlines(td, cell, opts);
        tr.append(td);
      });
      tbody.append(tr);
    }
    table.append(tbody);
    return table;
  }
  return null;
}

function mdInlines(parent, inlines, opts) {
  for (const n of inlines || []) {
    if (n.type === "text") parent.append(document.createTextNode(n.text || ""));
    else if (n.type === "code") {
      const el = document.createElement("code");
      el.textContent = n.text || "";
      parent.append(el);
    } else if (n.type === "strong" || n.type === "em") {
      const el = document.createElement(n.type === "strong" ? "strong" : "em");
      mdInlines(el, n.children, opts);
      parent.append(el);
    } else if (n.type === "link") parent.append(mdLink(n, opts));
    else if (n.type === "image") parent.append(mdImage(n, opts));
    else if (n.children) mdInlines(parent, n.children, opts);
  }
}

function mdLink(n, opts) {
  const href = String(n.href || "").trim();
  const kind = mdHrefKind(href);
  if (kind === "abs") {
    const a = document.createElement("a");
    a.href = href;
    a.rel = "noopener noreferrer";
    mdInlines(a, n.children, opts);
    if (/^https?:/i.test(href)) {
      a.onclick = (ev) => {
        ev.preventDefault();
        openLink(href, ev.ctrlKey || ev.metaKey);
      };
    }
    return a;
  }
  if (kind === "rel") {
    const a = document.createElement("a");
    a.href = "#";
    a.className = "md-path";
    mdInlines(a, n.children, opts);
    const path = resolveMdPath(opts.filePath, href);
    a.onclick = (ev) => {
      ev.preventDefault();
      if (opts.sessionId && path) openFileSheet(opts.sessionId, path);
    };
    return a;
  }
  const span = document.createElement("span");
  mdInlines(span, n.children, opts);
  return span;
}

function mdImage(n, opts) {
  const src = String(n.src || "").trim();
  const alt = n.alt || "";
  const kind = mdHrefKind(src);
  if (kind === "abs" && /^https?:/i.test(src)) {
    const a = document.createElement("a");
    a.href = src;
    a.rel = "noopener noreferrer";
    a.textContent = alt || src;
    a.onclick = (ev) => {
      ev.preventDefault();
      openLink(src, ev.ctrlKey || ev.metaKey);
    };
    return a;
  }
  if (kind === "rel" && opts.sessionId) {
    const img = document.createElement("img");
    img.alt = alt;
    const path = resolveMdPath(opts.filePath, src);
    img.src = "api/sessions/" + encodeURIComponent(opts.sessionId)
      + "/file?path=" + encodeURIComponent(path) + "&raw=1";
    return img;
  }
  return document.createTextNode(alt || src);
}

function resolveMdPath(fromFile, rel) {
  const raw = String(rel || "").trim().split(/[?#]/)[0];
  if (!raw) return "";
  const from = String(fromFile || "").replace(/\\/g, "/");
  let joined;
  if (raw.startsWith("/")) joined = raw;
  else {
    const slash = from.lastIndexOf("/");
    const dir = slash < 0 ? "." : (slash === 0 ? "/" : from.slice(0, slash));
    joined = dir === "." ? raw : dir === "/" ? "/" + raw.replace(/^\//, "") : dir + "/" + raw;
  }
  const parts = [];
  const abs = joined.startsWith("/");
  for (const p of joined.split("/")) {
    if (p === "" || p === ".") continue;
    if (p === "..") { if (parts.length) parts.pop(); continue; }
    parts.push(p);
  }
  return (abs ? "/" : "") + parts.join("/");
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
    text.textContent = info.text || "";
    const md = isMarkdownPath(info.path || info.asked || fileAsked);
    const doc = $("#fileDoc");
    const srcBtn = $("#fileSource");
    if (md && doc) {
      fileShowSource = false;
      const frag = renderMarkdown(parseMarkdown(info.text || ""), {
        sessionId: fileSession,
        filePath: info.path || info.asked || fileAsked,
      });
      doc.textContent = "";
      doc.append(frag);
      doc.hidden = false;
      text.hidden = true;
      if (srcBtn) { srcBtn.hidden = false; srcBtn.textContent = "Source"; }
    } else {
      text.hidden = false;
      if (doc) { doc.hidden = true; doc.textContent = ""; }
      if (srcBtn) srcBtn.hidden = true;
    }
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
    note.textContent = info.error
      ? "Can't open that here: " + info.error + "."
      : "Nothing at that path from this session's directory.";
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
  if ($("#fileDoc")) $("#fileDoc").hidden = true;
  $("#fileNote").hidden = true;
  if ($("#fileList")) $("#fileList").hidden = true;
  if ($("#fileUp")) $("#fileUp").hidden = true;
  for (const sel of ["#fileEditBtn", "#fileSend", "#fileCopy", "#fileSource"]) $(sel).hidden = true;
  $("#fileSave").hidden = false;
  $("#fileCancel").hidden = false;
  ta.focus();
}

function cancelFileEdit() {
  fileEditing = false;
  $("#fileEdit").hidden = true;
  if (currentFile) showFile(currentFile);   // back to the preview we came from
}

let fileSaveSeq = 0;
async function saveFileEdit() {
  if (!fileEditing || !currentFile) return;
  const text = $("#fileEdit").value;
  const save = $("#fileSave");
  const file = currentFile;
  const mine = ++fileSaveSeq;
  save.disabled = true;
  try {
    await api("api/sessions/" + encodeURIComponent(fileSession) + "/file", {
      method: "POST",
      body: JSON.stringify({ path: currentFile.path || fileAsked, text }),
    });
    // The sheet was closed or moved to another file meanwhile: the write
    // landed, but there is nothing here to repaint.
    if (currentFile !== file) { toast("Saved " + (file.name || "file")); return; }
    // Reflect what is now on disk, then drop back to the preview.
    currentFile = { ...currentFile, text, size: new TextEncoder().encode(text).length };
    fileEditing = false;
    $("#fileEdit").hidden = true;
    showFile(currentFile);
    toast("Saved " + (currentFile.name || "file"));
  } catch (err) {
    toast("Could not save — " + (err.message || err), true);
  } finally {
    if (mine === fileSaveSeq) save.disabled = false;   // a newer save owns the button
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
