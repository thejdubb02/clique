"""CPU and memory for the stats readout, straight from /proc.

No psutil: one more dependency to carry for two numbers we can read ourselves,
on a tool whose whole argument is that it adds nothing to the box.
"""

from __future__ import annotations

import os
import threading
import time
from collections import deque

_lock = threading.Lock()
_previous: tuple[int, int] | None = None  # (busy, total) jiffies


def cpu_percent() -> float:
    """Busy CPU since the last call.

    Delta-based, so the first call reports 0.0 rather than a meaningless
    average since boot. The UI polls every few seconds, which is exactly the
    window this measures.
    """
    global _previous
    try:
        with open("/proc/stat") as fh:
            fields = [int(v) for v in fh.readline().split()[1:]]
    except OSError:
        return 0.0

    idle = fields[3] + (fields[4] if len(fields) > 4 else 0)
    total = sum(fields)
    busy = total - idle

    with _lock:
        last = _previous
        _previous = (busy, total)

    if not last:
        return 0.0
    busy_delta, total_delta = busy - last[0], total - last[1]
    return round(100.0 * busy_delta / total_delta, 1) if total_delta > 0 else 0.0


def memory() -> dict:
    """Used/total in MB, using MemAvailable — the only figure that reflects
    what a new process can actually get."""
    values: dict[str, int] = {}
    try:
        with open("/proc/meminfo") as fh:
            for line in fh:
                key, _, rest = line.partition(":")
                if key in ("MemTotal", "MemAvailable"):
                    values[key] = int(rest.split()[0])
                if len(values) == 2:
                    break
    except OSError:
        return {"used_mb": 0, "total_mb": 0, "percent": 0.0}

    total = values.get("MemTotal", 0)
    available = values.get("MemAvailable", 0)
    used = max(total - available, 0)
    return {
        "used_mb": used // 1024,
        "total_mb": total // 1024,
        "percent": round(100.0 * used / total, 1) if total else 0.0,
    }


def swap() -> dict:
    """Swap in use.

    Worth its own number rather than folding into memory: any swap in use on an
    interactive box means memory pressure has *already* happened, and an agent
    that starts swapping feels broken long before it fails. A box can look fine
    on used-memory and be crawling.
    """
    values: dict[str, int] = {}
    try:
        with open("/proc/meminfo") as fh:
            for line in fh:
                key, _, rest = line.partition(":")
                if key in ("SwapTotal", "SwapFree"):
                    values[key] = int(rest.split()[0])
    except OSError:
        return {"used_mb": 0, "total_mb": 0, "percent": 0.0}
    total = values.get("SwapTotal", 0)
    used = max(total - values.get("SwapFree", 0), 0)
    return {
        "used_mb": used // 1024,
        "total_mb": total // 1024,
        "percent": round(100.0 * used / total, 1) if total else 0.0,
    }


def disk(path: str = "/") -> dict:
    """Free space on the filesystem the work lives on.

    The quietest way to lose an afternoon on a VPS: node_modules, model caches,
    transcripts and docker layers fill the disk, and every tool starts failing
    in a way that never mentions disk.
    """
    try:
        st = os.statvfs(path)
    except OSError:
        return {"free_gb": 0.0, "total_gb": 0.0, "percent": 0.0}
    total = st.f_blocks * st.f_frsize
    free = st.f_bavail * st.f_frsize
    used = total - free
    return {
        "free_gb": round(free / 1024**3, 1),
        "total_gb": round(total / 1024**3, 1),
        "percent": round(100.0 * used / total, 1) if total else 0.0,
    }


def load() -> dict:
    """Run-queue length, and how it compares to the number of cores.

    A better "is this box struggling" signal than instantaneous CPU, which
    swings wildly between samples. Normalised against core count so the number
    means the same thing on any machine.
    """
    try:
        one, five, fifteen = os.getloadavg()
    except OSError:
        return {"one": 0.0, "five": 0.0, "fifteen": 0.0, "cores": 1, "ratio": 0.0}
    cores = os.cpu_count() or 1
    return {
        "one": round(one, 2),
        "five": round(five, 2),
        "fifteen": round(fifteen, 2),
        "cores": cores,
        "ratio": round(one / cores, 2),
    }


_RSS_TTL = 8.0
_proc_cache: dict = {"at": 0.0, "rss": {}, "kids": {}}
_PAGE_KB = os.sysconf("SC_PAGE_SIZE") // 1024


def _walk_proc() -> tuple[dict, dict]:
    """(rss_kb_by_pid, children_by_ppid) from one pass over /proc."""
    rss: dict = {}
    kids: dict = {}
    for entry in os.scandir("/proc"):
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        try:
            with open(f"/proc/{pid}/stat", "rb") as fh:
                data = fh.read()
        except OSError:
            continue
        close = data.rfind(b")")  # comm can hold spaces/parens; split after it
        if close < 0:
            continue
        fields = data[close + 2 :].split()
        try:
            ppid = int(fields[1])  # stat field 4, minus the two before comm
            rss[pid] = int(fields[21]) * _PAGE_KB  # stat field 24 (rss, in pages)
        except (IndexError, ValueError):
            continue
        kids.setdefault(ppid, []).append(pid)
    return rss, kids


def rss_by_root(roots) -> dict:
    """Resident memory (KiB) of each root pid's whole tree — the CLI plus
    everything it spawned. Cached briefly: RSS does not move fast enough to
    reread on every three-second poll, and one /proc walk covers every tab."""
    now = time.time()
    if now - _proc_cache["at"] >= _RSS_TTL:
        rss, kids = _walk_proc()
        _proc_cache.update(at=now, rss=rss, kids=kids)
    rss, kids = _proc_cache["rss"], _proc_cache["kids"]
    out: dict = {}
    for root in set(roots):
        total, stack, seen = 0, [root], set()
        while stack:
            pid = stack.pop()
            if pid in seen:
                continue
            seen.add(pid)
            total += rss.get(pid, 0)
            stack.extend(kids.get(pid, []))
        out[root] = total
    return out


def snapshot(clients: int = 0) -> dict:
    return {
        "cpu": cpu_percent(),
        "mem": memory(),
        "swap": swap(),
        "disk": disk(),
        "load": load(),
        "clients": clients,
    }


# --- Resource guard --------------------------------------------------------
#
# A soft read on whether the box is stretched for the number of live agent
# sessions. Never a blocker — it hands the UI a level, one plain sentence, and
# a soft session ceiling for the New-Session form. This is the same read-only
# /proc sampling the status bar already does; the added cost is a comparison.

#: Left free for the OS, the panel itself, and a little burst headroom.
_RESERVE_MB = 1024
#: Below this much available RAM the box is tight whatever the session count.
_RAM_FLOOR_MB = 512
#: A coding agent's rough resident cost, used only until real sessions are
#: measured. Deliberately mid-range — agents vary a lot by CLI and repo.
_DEFAULT_SESSION_MB = 650

#: Swap climbing by more than this between baselines counts as "growing".
_SWAP_STEP_MB = 64
#: Don't re-baseline swap faster than this, so the verdict does not depend on
#: how often the guard is polled — every client's poll calls it.
_SWAP_REBASE_S = 20.0

_swap_lock = threading.Lock()
_swap_seen: dict = {"at": 0.0, "mb": None, "growing": False}


def _swap_growing(used_mb: int) -> bool:
    """Is swap actively climbing? Re-baselined at most every few seconds and
    holding its last verdict in between, so a burst of polls cannot reset it."""
    now = time.time()
    with _swap_lock:
        seen = _swap_seen
        if seen["mb"] is None:
            seen.update(at=now, mb=used_mb, growing=False)
            return False
        if now - seen["at"] < _SWAP_REBASE_S:
            return seen["growing"]
        growing = used_mb > 0 and (used_mb - seen["mb"]) >= _SWAP_STEP_MB
        seen.update(at=now, mb=used_mb, growing=growing)
        return growing


def _avg_session_mb(session_rss_kb) -> float:
    """Mean resident cost of a live session in MB — measured, not guessed.

    Falls back to a default until at least one session is running, so a fresh
    box still gets a sensible ceiling instead of one built on no data.
    """
    vals = [kb for kb in session_rss_kb if kb and kb > 0]
    if not vals:
        return float(_DEFAULT_SESSION_MB)
    return max(1.0, (sum(vals) / len(vals)) / 1024)


def _reap_phrase(hours: float) -> str:
    if not hours or hours <= 0:
        return ""
    h = int(hours) if float(hours).is_integer() else round(hours, 1)
    return f"{h}h"


def guard(
    sessions: int,
    session_rss_kb=(),
    *,
    mem: dict | None = None,
    swap_info: dict | None = None,
    load_info: dict | None = None,
    reap_hours: float = 6.0,
) -> dict:
    """Soft verdict on whether the box is stretched for its live sessions.

    Returns a level (``ok`` / ``watch`` / ``high``), a one-line headline, the
    reasons behind it, and a soft session ceiling for the New-Session form.
    Reads mem/swap/load itself unless handed the values a snapshot already
    gathered (which is also how the checks feed it synthetic numbers).
    """
    mem = memory() if mem is None else mem
    swap_info = swap() if swap_info is None else swap_info
    load_info = load() if load_info is None else load_info

    total_mb = int(mem.get("total_mb", 0))
    used_mb = int(mem.get("used_mb", 0))
    free_mb = max(total_mb - used_mb, 0)
    free_gb = round(free_mb / 1024, 1)

    avg_mb = _avg_session_mb(session_rss_kb)
    usable = max(total_mb - _RESERVE_MB, 0)
    ceiling = max(1, int(usable // avg_mb)) if avg_mb else 1

    cores = int(load_info.get("cores", 1) or 1)
    one = load_info.get("one", 0.0)
    ratio = load_info.get("ratio", 0.0)
    swap_growing = _swap_growing(int(swap_info.get("used_mb", 0)))

    rank = {"ok": 0, "watch": 1, "high": 2}
    level = "ok"
    reasons: list[str] = []

    def flag(new: str, reason: str) -> None:
        nonlocal level
        if rank[new] > rank[level]:
            level = new
        reasons.append(reason)

    cores_word = "core" if cores == 1 else "cores"
    if sessions > ceiling:
        flag("high", f"{sessions} sessions, past a comfortable ~{ceiling} for this box")
    elif sessions >= ceiling and sessions >= 2:
        flag("watch", f"{sessions} sessions, about the most this box runs well (~{ceiling})")

    if free_mb < _RAM_FLOOR_MB:
        flag("high", f"only ~{free_gb} GB RAM free")
    elif free_mb < _RAM_FLOOR_MB * 3:
        flag("watch", f"~{free_gb} GB RAM free")

    if swap_growing:
        flag("high", "swap is climbing — memory pressure has already hit")

    if ratio >= 2.0:
        flag("high", f"load {one} over {cores} {cores_word}")
    elif ratio >= 1.0:
        flag("watch", f"load {one} over {cores} {cores_word}")

    headline = ""
    if level != "ok":
        tail = "heavy for this box" if level == "high" else "getting full for this box"
        plural = "" if sessions == 1 else "s"
        headline = f"{sessions} session{plural}, ~{free_gb} GB free — {tail}."
        reap = _reap_phrase(reap_hours)
        headline += (
            f" Idle ones auto-reap in {reap}." if reap else " You can reclaim idle ones now."
        )

    return {
        "level": level,
        "sessions": sessions,
        "ceiling": ceiling,
        "avg_session_mb": round(avg_mb),
        "free_mb": free_mb,
        "reasons": reasons,
        "headline": headline,
    }


class History:
    """A rolling window of CPU and memory samples.

    Exists to answer "was there a spike while I was away", which a live number
    cannot. Kept in memory on purpose: it is diagnostic, not a record, and
    writing a sample to disk every few seconds to answer a question asked twice
    a week is the wrong trade on a box that is short of I/O.

    Sampling happens on its own thread rather than on request, so the series
    has an even spacing whether or not anyone is looking at it — a graph built
    from poll-driven samples lies about quiet periods.
    """

    def __init__(self, interval: int = 5, window_minutes: int = 180) -> None:
        self.interval = interval
        self.capacity = max(1, (window_minutes * 60) // interval)
        self._samples: deque[tuple[int, float, float]] = deque(maxlen=self.capacity)
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def start(self) -> None:
        if self._thread:
            return
        # Prime the CPU delta so the first real sample is not a meaningless 0.
        cpu_percent()
        self._thread = threading.Thread(target=self._run, name="sysinfo", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            mem = memory()
            with self._lock:
                self._samples.append((int(time.time()), cpu_percent(), mem["percent"]))

    def series(self, minutes: int = 60) -> dict:
        cutoff = time.time() - minutes * 60
        with self._lock:
            rows = [s for s in self._samples if s[0] >= cutoff]
        return {
            "interval": self.interval,
            "window_minutes": minutes,
            "samples": [{"t": t, "cpu": c, "mem": m} for t, c, m in rows],
            "peak_cpu": max((c for _, c, _ in rows), default=0.0),
            "peak_mem": max((m for _, _, m in rows), default=0.0),
            # How far back the buffer actually reaches, so the UI can say
            # "last 12 minutes" after a restart instead of implying an hour.
            "covered_minutes": round((time.time() - rows[0][0]) / 60) if rows else 0,
        }
