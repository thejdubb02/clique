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

PALETTE = ["#c7915b", "#6f42c1", "#2d7d46", "#1f6feb", "#0d7d8f", "#a63d2f", "#8b8b8b"]


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
        if not raw:
            self._write()

    def _write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "folders": [asdict(f) for f in self.folders],
            "sessions": [asdict(s) for s in self.sessions],
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
        with self._lock:
            found = self.session(session_id)
            if not found:
                return None
            for key, value in fields.items():
                if hasattr(found, key) and value is not None:
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
                if hasattr(found, key) and value is not None:
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
