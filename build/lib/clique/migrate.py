"""One-time imports from the tool CLIque replaces.

Kept in its own module, and deliberately not wired into anything but `adopt`,
because none of it is part of the product. It exists so that switching tools
does not cost anyone a day of set-up, and it should be deleted once it has done
that job.

Everything here fails quiet. A missing file, a changed schema or a corrupt
record means "no extra information available", never a failed adoption — the
session is still adopted, just with a name derived from its directory.
"""

from __future__ import annotations

import contextlib
import json
from pathlib import Path

#: Codeman's tmux index. Each record carries `muxName` outright, so no name has
#: to be reconstructed from an id. Preferred source.
CODEMAN_MUX = Path("/root/.codeman/mux-sessions.json")

#: Codeman's main state file. Its session keys are full uuids and its tmux
#: sessions are named `codeman-` plus the first eight characters of one, so
#: names here have to be reconstructed. Fallback, and the only source for
#: anything the mux index has lost track of.
CODEMAN_STATE = Path("/root/.codeman/state.json")

MUX_PREFIX = "codeman-"


def _mux_index(path: Path = CODEMAN_MUX) -> list[dict]:
    with contextlib.suppress(OSError, ValueError):
        raw = json.loads(path.read_text())
        if isinstance(raw, list):
            return [r for r in raw if isinstance(r, dict)]
    return []


def codeman_names(path: Path = CODEMAN_STATE) -> dict[str, str]:
    """Map tmux session name -> the name its owner actually gave it.

    Adopting without this produces a sidebar of directory basenames: "mark",
    "agent-infra", "testcase". The names people chose — "Duchamp Dashboard
    Dev", "StriderSentinel agent Dev" — are the entire reason the sidebar is
    worth reading, and they are sitting in a JSON file right there.
    """
    names: dict[str, str] = {}
    # Reconstructed ids first, then the direct index on top — so where both
    # know a session, the one that did not have to guess wins.
    try:
        raw = json.loads(path.read_text())
    except (OSError, ValueError):
        raw = {}

    for session_id, record in (raw.get("sessions") or {}).items():
        if not isinstance(record, dict) or not isinstance(session_id, str):
            continue
        name = (record.get("name") or "").strip()
        if name:
            names[f"{MUX_PREFIX}{session_id[:8]}"] = name

    for record in _mux_index():
        mux = str(record.get("muxName") or "").strip()
        name = str(record.get("name") or "").strip()
        if mux and name:
            names[mux] = name
    return names


def codeman_cwds(path: Path = CODEMAN_STATE) -> dict[str, str]:
    """Map tmux session name -> the working directory Codeman recorded.

    A fallback only. tmux's own `pane_current_path` is the truth, because it
    follows the pane if someone has `cd`-ed since; this covers the case where
    tmux reports nothing useful.
    """
    cwds: dict[str, str] = {}
    with contextlib.suppress(OSError, ValueError):
        raw = json.loads(path.read_text())
        for session_id, record in (raw.get("sessions") or {}).items():
            if isinstance(record, dict) and record.get("workingDir"):
                cwds[f"{MUX_PREFIX}{session_id[:8]}"] = str(record["workingDir"])
    return cwds
