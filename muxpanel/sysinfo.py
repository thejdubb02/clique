"""CPU and memory for the stats readout, straight from /proc.

No psutil: one more dependency to carry for two numbers we can read ourselves,
on a tool whose whole argument is that it adds nothing to the box.
"""

from __future__ import annotations

import threading

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
