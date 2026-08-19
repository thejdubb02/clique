"""On-disk state: which sessions exist, and which folder each one sits in.

A JSON file, not a database. The authoritative record of *what is running* is
tmux itself — this file only holds what tmux cannot: a human name, a folder, a
CLI type. Anything here that contradicts tmux loses, which is why a session
whose tmux server has gone shows as dead rather than resurrecting on restart.

Writes are atomic (temp file + rename) with one backup kept, because the
alternative is a half-written state file after a power cut taking the whole
sidebar with it.
"""

from __future__ import annotations

import contextlib
import json
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

#: Seeded on first run. Matches the folders already in use in CodemanPanel, so
#: switching tools does not mean re-filing everything by hand.
DEFAULT_FOLDERS = [
    {"id": "f-platform", "name": "WSG Platform", "color": "#c7915b", "match": ["/root/platform/"]},
    {"id": "f-ventures", "name": "Ventures", "color": "#6f42c1", "match": ["/root/ventures/"]},
    {"id": "f-personal", "name": "Personal", "color": "#2d7d46", "match": ["/root/personal/"]},
    {"id": "f-clients", "name": "Clients", "color": "#1f6feb", "match": ["/root/clients/"]},
    {"id": "f-skyhawk", "name": "Skyhawk", "color": "#0d7d8f", "match": ["/root/skyhawk"]},
    {"id": "f-mark", "name": "Mark", "color": "#a63d2f", "match": ["/root/mark"]},
]

PALETTE = ["#c7915b", "#6f42c1", "#2d7d46", "#1f6feb", "#0d7d8f", "#a63d2f",
           "#8b8b8b", "#d96f6f", "#e8a33d", "#3aa3a0", "#7a7fd6", "#ff5fa2"]

#: How a CLI is marked in the tab bar and sidebar.
#:
#: "both" is an icon tinted in the CLI's colour; "icon" is the same shape in
#: neutral grey; "color" is a plain colour chip; "none" is nothing at all. The
#: live/attached status dot is separate and always shown — that is status, not
#: branding, and hiding it would cost information rather than decoration.
MARKER_MODES = ("both", "icon", "color", "none")

DEFAULT_SETTINGS = {
    "marker_default": "both",
    "marker_by_cli": {},
    "markers_in_tabs": True,
    "markers_in_sidebar": True,
    #: Let the CLI icon carry the status colour and drop the separate dot, so
    #: shape says which CLI and colour says how it is doing. Falls back to the
    #: dot wherever there is no icon to carry it — losing status entirely
    #: would be a worse trade than showing two marks.
    "status_on_icon": False,
    #: Preset palette id from web/themes.js. "" is the built-in dark.
    "theme": "",
    #: "dark" | "light" | "system". Themes carry their own base, so this only
    #: decides which built-in is used when no preset is chosen.
    "appearance": "dark",
    #: Independent, because the two are read at different distances: the
    #: sidebar is scanned, the terminal is read.
    "font_panel": 13,
    "font_terminal": 13,
    #: "panel" keeps muxpanel's prompt box. "terminal" hides it and lets the
    #: CLI's own input field be the only one — two stacked prompts is
    #: redundant chrome. Snippets work in both: muxpanel owns the PTY, so an
    #: expansion is injected into the pane either way.
    "input_mode": "panel",
    #: Three independent slots, applied in this order: both, then panel, then
    #: terminal. Carried over from CodemanPanel, where custom CSS is the escape
    #: hatch that stops every small preference becoming a feature request.
    "css_both": "",
    "css_panel": "",
    "css_terminal": "",
    #: Text expanders: [{"trigger": ";rev", "label": "...", "text": "..."}]
    "snippets": [],
    #: A tab that finished while you were looking elsewhere should say so.
    #: Flash is silent and always safe; sound is opt-in because a room with
    #: twenty agents in it would otherwise be unbearable.
    "notify_flash": True,
    "notify_sound": False,
    #: Seconds of no pane output before a session counts as finished. Short
    #: enough to feel immediate, long enough that a CLI pausing to think does
    #: not read as done.
    "notify_idle_seconds": 4,
}

#: A snippet body over this is a document, not an expander, and storing one
#: would bloat every /api/state response the sidebar polls.
MAX_SNIPPET_CHARS = 8000

#: Same reasoning: custom CSS is a stylesheet, not a payload.
MAX_CSS_CHARS = 40000


@dataclass
class Folder:
    id: str
    name: str
    color: str = "#8b8b8b"
    match: list[str] = field(default_factory=list)
    collapsed: bool = False
    order: int = 0


@dataclass
class Session:
    id: str
    name: str
    cli: str
    cwd: str
    mux: str
    socket: str | None
    folder: str | None = None
    mode: str | None = None
    cli_session_id: str | None = None
    created: float = 0.0
    adopted: bool = False
    order: int = 0
    #: Out of the way, not gone. Archiving never touches the tmux session, so
    #: an archived session is still running and can be un-archived at any time.
    archived: bool = False


def new_id() -> str:
    return str(uuid.uuid4())


def auto_folder(cwd: str, folders: list[Folder]) -> str | None:
    """File a new session by where it runs.

    Longest match wins, so ``/root/mark/duchamp`` beats ``/root/`` if both are
    configured. A session the user has dragged somewhere keeps that folder —
    this only ever decides the *initial* home.
    """
    best: tuple[int, str] | None = None
    for folder in folders:
        for prefix in folder.match:
            if cwd.startswith(prefix) and (best is None or len(prefix) > best[0]):
                best = (len(prefix), folder.id)
    return best[1] if best else None


def _clean_snippets(value) -> list[dict]:
    """Normalise a snippet list from the browser.

    Kept strict because these are stored, replayed into a terminal, and shown
    in a menu: a malformed one should be dropped here rather than becoming a
    render error later.
    """
    if not isinstance(value, list):
        return []
    out = []
    for raw in value[:200]:
        if not isinstance(raw, dict):
            continue
        trigger = str(raw.get("trigger") or "").strip()
        text = str(raw.get("text") or "")[:MAX_SNIPPET_CHARS]
        if not trigger or not text:
            continue
        out.append({
            "trigger": trigger[:40],
            "label": str(raw.get("label") or "").strip()[:80],
            "text": text,
        })
    return out


class Store:
    """Thread-safe reader/writer for state.json.

    Every mutation takes the lock and writes immediately. The file is small and
    writes are rare (a session created, a rename, a drag), so buffering would
    only add a window in which a crash loses the user's folders.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()
        self.folders: list[Folder] = []
        self.sessions: list[Session] = []
        self.settings: dict = dict(DEFAULT_SETTINGS)
        self._load()

    # ------------------------------------------------------------ persistence

    def _load(self) -> None:
        raw: dict = {}
        for candidate in (self.path, self.path.with_suffix(".json.bak")):
            try:
                raw = json.loads(candidate.read_text())
                break
            except FileNotFoundError:
                continue
            except (ValueError, OSError):
                continue  # try the backup rather than starting empty

        folders = raw.get("folders") or DEFAULT_FOLDERS
        self.folders = [Folder(**{k: v for k, v in f.items() if k in Folder.__annotations__})
                        for f in folders]
        self.sessions = [Session(**{k: v for k, v in s.items() if k in Session.__annotations__})
                         for s in raw.get("sessions", [])]
        # Merge rather than replace, so a setting added in a later version
        # appears with its default instead of being missing.
        self.settings = {**DEFAULT_SETTINGS, **(raw.get("settings") or {})}
        if not raw:
            self._write()

    def _write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "folders": [asdict(f) for f in self.folders],
            "sessions": [asdict(s) for s in self.sessions],
            "settings": self.settings,
        }
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2))
        if self.path.exists():
            self.path.replace(self.path.with_suffix(".json.bak"))
        tmp.replace(self.path)

    def save(self) -> None:
        with self._lock:
            self._write()

    # --------------------------------------------------------------- sessions

    def session(self, session_id: str) -> Session | None:
        with self._lock:
            return next((s for s in self.sessions if s.id == session_id), None)

    def add_session(self, session: Session) -> Session:
        with self._lock:
            if session.folder is None:
                session.folder = auto_folder(session.cwd, self.folders)
            if not session.created:
                session.created = time.time()
            session.order = len(self.sessions)
            self.sessions.append(session)
            self._write()
            return session

    def remove_session(self, session_id: str) -> Session | None:
        with self._lock:
            found = self.session(session_id)
            if found:
                self.sessions.remove(found)
                self._write()
            return found

    def update_session(self, session_id: str, **fields) -> Session | None:
        """Apply supplied fields. See the None handling below — it is load-bearing."""
        with self._lock:
            found = self.session(session_id)
            if not found:
                return None
            for key, value in fields.items():
                if not hasattr(found, key):
                    continue
                # None means "not supplied" for every field except `folder`,
                # where it is the actual value: dragging a session out of every
                # folder has to be able to clear it.
                if value is None and key != "folder":
                    continue
                setattr(found, key, value)
            self._write()
            return found

    def reorder_sessions(self, ordered_ids: list[str]) -> None:
        """Apply a drag-and-drop ordering. Unlisted sessions keep their tail."""
        with self._lock:
            rank = {sid: i for i, sid in enumerate(ordered_ids)}
            for session in self.sessions:
                session.order = rank.get(session.id, len(rank) + session.order)
            self.sessions.sort(key=lambda s: s.order)
            self._write()

    # --------------------------------------------------------------- settings

    def update_settings(self, changes: dict) -> dict:
        """Merge a partial settings update.

        Per-CLI marker choices merge one level deeper: the UI sends only the
        CLI that changed, and replacing the whole map would silently reset
        every other CLI to the default.
        """
        with self._lock:
            for key, value in changes.items():
                if key not in DEFAULT_SETTINGS:
                    continue  # ignore unknown keys rather than storing junk
                if key == "marker_by_cli" and isinstance(value, dict):
                    merged = dict(self.settings.get("marker_by_cli") or {})
                    for cli_id, mode in value.items():
                        if mode in MARKER_MODES:
                            merged[cli_id] = mode
                        elif mode is None:
                            merged.pop(cli_id, None)
                    self.settings["marker_by_cli"] = merged
                elif key == "marker_default":
                    if value in MARKER_MODES:
                        self.settings[key] = value
                elif key == "snippets":
                    self.settings[key] = _clean_snippets(value)
                elif key.startswith("css_"):
                    self.settings[key] = str(value or "")[:MAX_CSS_CHARS]
                elif key in ("theme", "appearance", "input_mode"):
                    self.settings[key] = str(value or "")[:64]
                elif key == "notify_idle_seconds":
                    with contextlib.suppress(TypeError, ValueError):
                        self.settings[key] = max(2, min(int(value), 120))
                elif key.startswith("font_"):
                    # Clamped rather than rejected: a bad number here should
                    # not be able to make the UI unreadable and unfixable.
                    with contextlib.suppress(TypeError, ValueError):
                        self.settings[key] = max(9, min(int(value), 28))
                else:
                    self.settings[key] = bool(value)
            self._write()
            return self.settings

    # ---------------------------------------------------------------- folders

    def folder(self, folder_id: str) -> Folder | None:
        with self._lock:
            return next((f for f in self.folders if f.id == folder_id), None)

    def add_folder(self, name: str, color: str | None = None) -> Folder:
        with self._lock:
            folder = Folder(
                id=f"f-{uuid.uuid4().hex[:8]}",
                name=name,
                color=color or PALETTE[len(self.folders) % len(PALETTE)],
                order=len(self.folders),
            )
            self.folders.append(folder)
            self._write()
            return folder

    def update_folder(self, folder_id: str, **fields) -> Folder | None:
        with self._lock:
            found = self.folder(folder_id)
            if not found:
                return None
            for key, value in fields.items():
                if not hasattr(found, key):
                    continue
                # None means "not supplied" for every field except `folder`,
                # where it is the actual value: dragging a session out of every
                # folder has to be able to clear it.
                if value is None and key != "folder":
                    continue
                setattr(found, key, value)
            self._write()
            return found

    def remove_folder(self, folder_id: str) -> bool:
        """Delete a folder. Its sessions become unfiled — never deleted.

        Losing a folder must not lose work; that asymmetry is deliberate.
        """
        with self._lock:
            found = self.folder(folder_id)
            if not found:
                return False
            self.folders.remove(found)
            for session in self.sessions:
                if session.folder == folder_id:
                    session.folder = None
            self._write()
            return True
