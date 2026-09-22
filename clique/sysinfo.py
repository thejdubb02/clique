"""CPU and memory for the stats readout, straight from /proc.

No psutil: one more dependency to carry for two numbers we can read ourselves,
on a tool whose whole argument is that it adds nothing to the box.
"""

from __future__ import annotations

import glob
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


def uptime() -> dict:
    """Seconds since the box booted.

    A quiet sanity read: a host that reboots under you takes every session with
    it, so "up 12 days" versus "up 3 minutes" is worth a glance. Always available
    on Linux, so it always shows.
    """
    try:
        with open("/proc/uptime") as fh:
            seconds = float(fh.read().split()[0])
    except (OSError, ValueError, IndexError):
        return {"seconds": 0}
    return {"seconds": int(seconds)}


def temperature() -> dict:
    """The hottest CPU or board sensor in Celsius, when the machine exposes one.

    Best-effort and often absent: a VM or a container usually has no sensor, so
    an empty dict is the honest answer and the status bar hides the column just
    as it does for swap. Reads the kernel's thermal zones first, then hwmon as a
    fallback, and keeps only physically plausible readings so a bogus zone does
    not report 0 or 8000 degrees.
    """
    plausible: list[float] = []
    for pattern in (
        "/sys/class/thermal/thermal_zone*/temp",
        "/sys/class/hwmon/hwmon*/temp*_input",
    ):
        for path in glob.glob(pattern):
            try:
                with open(path) as fh:
                    celsius = int(fh.read().strip()) / 1000.0
            except (OSError, ValueError):
                continue
            if 0.0 < celsius < 150.0:
                plausible.append(celsius)
        # Only a usable reading ends the search. A box whose thermal zones all
        # report 0 still deserves the hwmon fallback it advertises.
        if plausible:
            break  # thermal zones are enough; do not double-count with hwmon
    if not plausible:
        return {}
    return {"c": round(max(plausible), 1)}


_RSS_TTL = 8.0
_proc_cache: dict = {"at": 0.0, "rss": {}, "kids": {}, "ticks": {}, "comm": {}}
_PAGE_KB = os.sysconf("SC_PAGE_SIZE") // 1024
# Same clock /proc uses for utime/stime. cpu_percent() never needs this: its
# busy and total both come from /proc/stat, so the tick rate cancels out.
_CLK_TCK = os.sysconf("SC_CLK_TCK")
# root pid -> (tree utime+stime, sample time, last percent). Same idea as
# cpu_percent()'s _previous: a rate needs the sample before this one.
_cpu_previous: dict[int, tuple[int, float, float]] = {}


def _walk_proc() -> tuple[dict, dict, dict, dict]:
    """(rss_kb_by_pid, children_by_ppid, cpu_ticks_by_pid, comm_by_pid) from one /proc pass."""
    rss: dict = {}
    kids: dict = {}
    ticks: dict = {}
    comm: dict[int, str] = {}
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
            # utime + stime (stat fields 14 and 15), in clock ticks. Child
            # time (cutime/cstime) stays out: the tree walk adds each live
            # child itself, and adding both would count those twice.
            ticks[pid] = int(fields[11]) + int(fields[12])
            # Between the first "(" and that closing ")". A naive split
            # breaks when the name itself holds spaces or parens.
            comm[pid] = data[data.index(b"(") + 1:close].decode("utf-8", "replace")
        except (IndexError, ValueError):
            continue
        kids.setdefault(ppid, []).append(pid)
    return rss, kids, ticks, comm


def _proc_snapshot() -> dict:
    """One /proc walk, shared by RSS and CPU, refreshed on the RSS TTL.

    CPU is a rate and RSS is a level, but they read the same line of the
    same files. A second walk per poll would pay that cost twice for a
    number that does not move faster than memory does."""
    now = time.time()
    if now - _proc_cache["at"] >= _RSS_TTL:
        rss, kids, ticks, comm = _walk_proc()
        _proc_cache.update(at=now, rss=rss, kids=kids, ticks=ticks, comm=comm)
    return _proc_cache


def _sum_tree(root, values: dict, kids: dict) -> int:
    """Sum `values` over root and every descendant. A cycle is skipped."""
    total, stack, seen = 0, [root], set()
    while stack:
        pid = stack.pop()
        if pid in seen:
            continue
        seen.add(pid)
        total += values.get(pid, 0)
        stack.extend(kids.get(pid, ()))
    return total


def rss_by_root(roots) -> dict:
    """Resident memory (KiB) of each root pid's whole tree — the CLI plus
    everything it spawned. Cached briefly: RSS does not move fast enough to
    reread on every three-second poll, and one /proc walk covers every tab."""
    cache = _proc_snapshot()
    rss, kids = cache["rss"], cache["kids"]
    return {root: _sum_tree(root, rss, kids) for root in set(roots)}


def sub_clis_by_root(roots, commands: set[str]) -> dict[int, list[str]]:
    """The row already names the session's own CLI. This is another one it
    shelled out to — only a descendant counts, or every session would flag
    itself."""
    cache = _proc_snapshot()
    kids, comm = cache["kids"], cache["comm"]
    out: dict[int, list[str]] = {}
    for root in set(roots):
        found: set[str] = set()
        # Start below the root, and remember the root so a cycle cannot
        # walk back up and count the session as its own sub-CLI.
        stack, seen = list(kids.get(root, ())), {root}
        while stack:
            pid = stack.pop()
            if pid in seen:
                continue
            seen.add(pid)
            name = comm.get(pid, "")
            if name in commands:
                found.add(name)
            stack.extend(kids.get(pid, ()))
        if found:
            out[root] = sorted(found)
    return out


def cpu_percent_by_root(roots) -> dict:
    """CPU percent (0-100+, can exceed 100 with multiple threads/cores) of
    each root pid's whole tree, since the last call. First call for a root
    reports 0.0 — there is no prior sample to diff against."""
    cache = _proc_snapshot()
    ticks_by_pid, kids, sampled_at = cache["ticks"], cache["kids"], cache["at"]
    out: dict = {}
    # Same lock cpu_percent() uses for its previous sample. The /proc walk
    # stays outside it: that is the slow part, and RSS already shares it.
    with _lock:
        for root in set(roots):
            ticks = _sum_tree(root, ticks_by_pid, kids)
            prev = _cpu_previous.get(root)
            if prev is None:
                # No earlier sample. 0.0, not a percent-since-boot.
                pct = 0.0
            elif prev[1] == sampled_at:
                # Still the walk we already turned into a percent. Diffing
                # it again would be a zero over a few milliseconds and the
                # badge would flicker to idle between real samples.
                pct = prev[2]
            else:
                dt = sampled_at - prev[1]
                dticks = ticks - prev[0]
                # A backwards counter means the pid was reused, or it exited
                # and this root is gone. Treat that as a fresh baseline.
                pct = (
                    round(100.0 * dticks / (dt * _CLK_TCK), 1)
                    if dt > 0 and dticks > 0 and _CLK_TCK
                    else 0.0
                )
            _cpu_previous[root] = (ticks, sampled_at, pct)
            out[root] = pct
        gone = [pid for pid in _cpu_previous if pid not in ticks_by_pid and pid not in out]
        for pid in gone:
            _cpu_previous.pop(pid, None)
    return out


def snapshot(clients: int = 0) -> dict:
    return {
        "cpu": cpu_percent(),
        "mem": memory(),
        "swap": swap(),
        "disk": disk(),
        "load": load(),
        "uptime": uptime(),
        "temp": temperature(),
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
