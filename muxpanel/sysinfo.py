"""CPU and memory for the stats readout, straight from /proc.

No psutil: one more dependency to carry for two numbers we can read ourselves,
on a tool whose whole argument is that it adds nothing to the box.
"""

from __future__ import annotations

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


def snapshot(clients: int = 0) -> dict:
    mem = memory()
    return {"cpu": cpu_percent(), "mem": mem, "clients": clients}


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
