/* Sidebar plan usage and machine stats.
 *
 * Loaded before app.js as a classic script. These functions run only after
 * app.js has initialised the shared panel state and helpers they use.
 */
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

// The sidebar usage panel's one meter row: bar, percent, and — the reset
// time is a visible span rather than only a tooltip, because a phone has no
// hover to reveal it from.
function planMeter(w, askedOf) {
  const pct = Math.round(w.percent);
  const when = untilReset(w.resets_at);
  const meter = mk("span", "plan-w");
  meter.dataset.level = planLevel(w.percent);

  const key = mk("b", "plan-k");
  key.textContent = w.label;
  const track = mk("span", "plan-track");
  const fill = mk("i", "plan-fill");
  // scaleX rather than width: the bar sits beside a dozen PTYs, and a
  // transform is the one thing that cannot make the browser lay out again.
  fill.style.transform = `scaleX(${Math.max(w.percent, 0.5) / 100})`;
  track.appendChild(fill);
  const value = mk("span", "plan-v");
  value.textContent = pct + "%";

  meter.append(key, track, value);
  if (when) {
    const reset = mk("span", "plan-r");
    reset.textContent = when === "now" ? "resets now" : `resets ${when}`;
    meter.append(reset);
  }
  meter.title = `${w.label}: ${pct}% used`
    + (when ? `, resets ${when}` : "")
    + `\nAsked of ${askedOf} directly, at most once every few minutes.`;
  return meter;
}

// Every installed CLI with a probe, not just a running one — fetched only
// while the panel is open (?all=1 is the explicit ask that widens usage_now()
// past sessions actually running; a panel nobody opens makes no outbound call).
let allUsage = null;   // null = not fetched yet
let usageError = "";   // the panel itself could not be reached
let usageTimer = null;
const USAGE_STALE_S = 300;  // usage.TTL on the server

function usageAge(u) {
  return u.checked ? Date.now() / 1000 - u.checked : 0;
}

async function loadAllUsage(recheck = true) {
  try {
    const payload = await api("api/usage?all=1");
    allUsage = payload.usage || [];
    usageError = "";
  } catch (err) {
    // Keep the last bars on a blip; say why only when there is nothing else.
    usageError = err.message || "network error";
    if (allUsage === null) allUsage = [];
  }
  renderUsagePanel();
  // A stale reading is answered at once and refreshed behind it, so one
  // quiet look a few seconds later picks up the fresh numbers.
  if (recheck && !usageError && allUsage.some((u) => usageAge(u) >= USAGE_STALE_S)) {
    setTimeout(() => loadAllUsage(false), 4000);
  }
}

function renderUsagePanel() {
  const body = $("#usageBody");
  const toggle = $("#usageToggle");
  if (!body || !toggle) return;
  const open = toggle.getAttribute("aria-expanded") === "true";
  body.hidden = !open;
  if (!open || allUsage === null) return;

  if (!allUsage.length) {
    const empty = mk("div", "usage-empty");
    empty.textContent = usageError
      ? `Couldn't reach the panel: ${usageError}`
      : "No CLI reports plan usage yet.";
    body.replaceChildren(empty);
    return;
  }

  const running = new Set(state.sessions.filter((s) => s.cli).map((s) => s.cli));
  body.replaceChildren(...allUsage.map((u) => {
    const cli = (state.clis || []).find((c) => c.id === u.cli);
    const isRunning = running.has(u.cli);
    const wrap = mk("div", "usage-cli" + (isRunning ? "" : " is-idle"));
    const head = mk("div", "usage-cli-head");
    head.innerHTML = markerFor(
      { color: cliColor(u.cli, cli?.color), icon: cli?.icon,
        icon_full_color: cli?.icon_full_color, label: cli?.label, cli: u.cli },
      "both"
    );
    const name = mk("span", "usage-cli-name");
    name.textContent = cli?.label || u.cli;
    head.append(name);
    if (!isRunning) {
      const tag = mk("span", "usage-cli-idle-tag");
      tag.textContent = "idle";
      head.append(tag);
    }
    // Only an old reading says how old: a fresh one needs no caption.
    const age = usageAge(u);
    if (age >= USAGE_STALE_S) {
      const checked = mk("span", "usage-cli-idle-tag");
      const mins = Math.round(age / 60);
      checked.textContent = mins < 60 ? `checked ${mins}m ago` : `checked ${Math.round(mins / 60)}h ago`;
      head.append(checked);
    }
    wrap.append(head, ...(u.windows || []).map((w) => planMeter(w, cli?.label || u.cli)));
    if (u.error) {
      const why = mk("div", "usage-empty");
      why.textContent = `Couldn't read usage: ${u.error}`;
      wrap.append(why);
    }
    return wrap;
  }));
}

function wireUsagePanel() {
  const toggle = $("#usageToggle");
  if (!toggle) return;
  const startPolling = () => {
    if (!usageTimer) usageTimer = setInterval(() => loadAllUsage(), 5 * 60 * 1000);
    loadAllUsage();
  };
  const startOpen = localStorage.getItem("clique.usagePanel") === "1";
  toggle.setAttribute("aria-expanded", String(startOpen));
  if (startOpen) startPolling();
  toggle.addEventListener("click", () => {
    const open = toggle.getAttribute("aria-expanded") !== "true";
    toggle.setAttribute("aria-expanded", String(open));
    localStorage.setItem("clique.usagePanel", open ? "1" : "0");
    if (open) {
      startPolling();
    } else if (usageTimer) {
      clearInterval(usageTimer);
      usageTimer = null;
    }
    renderUsagePanel();
  });
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
