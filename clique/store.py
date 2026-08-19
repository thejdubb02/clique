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
import os
import re
import threading
import time
import uuid
import zoneinfo
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import artifacts

#: A colour is three or six hex digits and nothing else. This value is written
#: into a style attribute, so anything looser than a full match is an opening.
_HEX = re.compile(r"#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})")

#: Seeded on first run. Empty match lists — fill these with your own trees
#: in the panel. A match is a directory prefix that auto-files new sessions.
DEFAULT_FOLDERS = [
    {"id": "f-work", "name": "Work", "color": "#c7915b", "match": []},
    {"id": "f-personal", "name": "Personal", "color": "#2d7d46", "match": []},
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
    #: One mark per session, not two. The CLI's logo carries the status colour
    #: — shape says which CLI it is, colour says how it is doing — and the
    #: separate dot is dropped. A logo with its own colours cannot be tinted,
    #: so those get a status ring around them instead; either way exactly one
    #: mark is drawn. The dot only comes back where a session has no marker at
    #: all, because losing status entirely is the worse trade.
    "status_on_icon": True,
    #: Preset palette id from web/themes.js. "" is the built-in dark.
    "theme": "",
    #: "dark" | "light" | "system". Themes carry their own base, so this only
    #: decides which built-in is used when no preset is chosen.
    "appearance": "dark",
    #: Independent, because the two are read at different distances: the
    #: sidebar is scanned, the terminal is read.
    "font_panel": 13,
    "font_terminal": 13,
    #: Ctrl+K opens the command palette, which means the pane never sees that
    #: key — and Ctrl+K is readline's kill-to-end-of-line. Anyone who uses it
    #: there can hand it back; the palette stays reachable on Ctrl+Shift+P,
    #: which no terminal claims.
    "palette_hotkey": True,
    #: Past conversations listed under the live sessions in each folder. On,
    #: because a tool you have just moved to should show you your work rather
    #: than an empty tree — but a few hundred rows is not everyone's sidebar.
    "history_in_sidebar": True,
    #: "panel" keeps clique's prompt box. "terminal" hides it and lets the
    #: CLI's own input field be the only one — two stacked prompts is
    #: redundant chrome. Snippets work in both: CLIque owns the PTY, so an
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
    #: The workspace: which sessions have a tab, in what order, and which one
    #: you were looking at. On the server with everything else a person chose,
    #: because losing it is the expensive kind of loss — twelve panes reopened
    #: by hand is a morning, and a laptop closing should not cost that. It is
    #: restored when a panel loads and not re-applied on the poll, so a second
    #: device does not yank the tabs out from under the first.
    "open_tabs": [],
    "active_tab": "",
    #: Running / Ungrouped / Archived are views over the sessions rather than
    #: folders, so there is no folder record to hold their collapsed state.
    #: A real folder's flag already syncs; these were the odd ones out.
    "views_collapsed": ["__archived"],
    #: Which CLI you are typing into, said in colour. Switching tabs is the
    #: moment it matters: nine panes of black text look identical, and sending
    #: a Claude prompt to a shell is a mistake that costs a paragraph of
    #: apology. An edge on the pane and the active tab, in the CLI's colour.
    "cli_tint": True,
    #: Per-CLI overrides of the colour shipped in clis.toml, because one
    #: person's palette is another person's invisible-on-their-theme.
    #: {"claude": "#d97757"}. An empty entry means "use the shipped one".
    "cli_colors": {},
    #: 24 or 12. Not derived from the locale: plenty of people read one format
    #: at work and the other at home, and the machine's guess is wrong for
    #: exactly the people who care enough to notice.
    "clock_24h": True,
    #: The newest release whose notes have actually been looked at. Seeded to
    #: whatever is running the first time a panel loads, so a fresh install
    #: does not open wearing a badge about its own arrival.
    "changelog_seen": "",
    #: An IANA zone for the clock on the empty pane — "Europe/Lisbon". Empty
    #: means the browser's own, which is right for most people and wrong for
    #: anyone whose box lives in a different country from they do.
    "clock_zone": "",
    #: One URL, POSTed a small JSON body when a session starts waiting, errors,
    #: finishes or dies. One field rather than a list of services, because
    #: ntfy, Gotify, Discord, Mattermost, Home Assistant and Uptime Kuma push
    #: are all "POST some JSON here" — and adding the second integration is the
    #: decision that creates a permanent "please add mine" queue.
    "webhook_url": "",
    #: Optional. Signs the exact bytes sent as X-CLIque-Signature, so a
    #: receiver on the open internet can tell your panel from anyone else.
    "webhook_secret": "",
    #: Where this panel answers, so a notification can link back to it. Only
    #: the person running it knows this — behind a tunnel the server has no
    #: reliable idea of its own public address.
    "panel_url": "",
    #: An agent that takes a screenshot has made something a terminal cannot
    #: show you. Off is a real preference — some working directories are full
    #: of images nobody wants a strip of — so it is a switch, not a rule.
    "artifacts_show": True,
    #: Where to look for them, relative to the session's working directory.
    #: Configurable because there is no universal answer: one person's agent
    #: writes to screenshots/, another's to whatever the MCP server chose.
    "artifact_dirs": list(artifacts.DEFAULT_DIRS),
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
    #: When this session was last looked at, in any browser. Lives here rather
    #: than in localStorage because "the one I was just in" is a fact about the
    #: work, not about the screen — it should be the same answer on the desktop
    #: and on the phone. Sidebar width is the counter-example and stays local.
    last_seen: float = 0.0
    adopted: bool = False
    order: int = 0
    #: Out of the way, not gone. Archiving never touches the tmux session, so
    #: an archived session is still running and can be un-archived at any time.
    archived: bool = False
    #: What is half-typed in the prompt box and not sent yet. On the server for
    #: the same reason `last_seen` is: an unsent instruction is about the work,
    #: not about the screen, so it survives a reload and follows you to another
    #: device. Sidebar width is the counter-example and stays local.
    draft: str = ""
    #: What the session said about itself, if anything: "waiting" or "error".
    #: Tier 3 of the attention ladder — set by a POST the user wires to
    #: whatever hook their own CLI already has, and believed rather than
    #: guessed at. Stored because a signal that vanished on restart would be
    #: worse than no signal: you would learn not to trust it.
    signal: str = ""
    #: The tmux activity clock when the signal arrived. Output after this
    #: point means the session moved on, and the signal is stale — which is
    #: what stops "waiting" sticking to a session that carried on by itself.
    signal_at: float = 0.0


def new_id() -> str:
    return str(uuid.uuid4())


def auto_folder(cwd: str, folders: list[Folder]) -> str | None:
    """File a new session by where it runs.

    Longest match wins, so ``/home/you/work/app`` beats ``/home/you/work`` if
    both are configured. A session the user has dragged somewhere keeps that
    folder — this only ever decides the *initial* home.
    """
    best: tuple[int, str] | None = None
    for folder in folders:
        for prefix in folder.match:
            if cwd.startswith(prefix) and (best is None or len(prefix) > best[0]):
                best = (len(prefix), folder.id)
    return best[1] if best else None


def _clean_ids(value, limit: int = 200) -> list[str]:
    """A list of ids from the browser, deduplicated and bounded.

    Order is meaningful — this is what holds tab order — so it is preserved
    rather than sorted, and the cap is here because these end up in every
    `/api/state` response.
    """
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for raw in value[:limit]:
        text = str(raw or "")[:64]
        if text and text not in out:
            out.append(text)
    return out


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
        # Flushed and fsynced before the rename, not just written.
        #
        # `write_text` followed by `replace` looks atomic and is not: the
        # rename is ordered, the *data* is not, so a machine that loses power
        # between them comes back to a state file of the right name and zero
        # length. Every session, folder and setting, gone — and the panel
        # would come up empty rather than obviously broken, which is worse.
        # The directory is synced too, or the rename itself can be lost.
        with open(tmp, "w") as handle:
            handle.write(json.dumps(payload, indent=2))
            handle.flush()
            os.fsync(handle.fileno())
        if self.path.exists():
            self.path.replace(self.path.with_suffix(".json.bak"))
        tmp.replace(self.path)
        with contextlib.suppress(OSError):
            directory = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)

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

    def touch_session(self, session_id: str) -> Session | None:
        """Record that this session was just looked at."""
        with self._lock:
            found = self.session(session_id)
            if not found:
                return None
            found.last_seen = time.time()
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
                if key == "cli_colors" and isinstance(value, dict):
                    # Merged one level deep for the same reason marker_by_cli
                    # is: the UI sends only the CLI that changed.
                    merged = dict(self.settings.get("cli_colors") or {})
                    for cli_id, colour in value.items():
                        if colour is None or not str(colour).strip():
                            merged.pop(cli_id, None)      # back to the shipped one
                        elif _HEX.fullmatch(str(colour).strip()):
                            merged[str(cli_id)[:64]] = str(colour).strip().lower()
                    self.settings["cli_colors"] = merged
                elif key == "marker_by_cli" and isinstance(value, dict):
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
                elif key in ("open_tabs", "views_collapsed"):
                    self.settings[key] = _clean_ids(value)
                elif key == "artifact_dirs":
                    self.settings[key] = artifacts.clean_dirs(value)
                elif key == "active_tab":
                    self.settings[key] = str(value or "")[:64]
                elif key == "snippets":
                    self.settings[key] = _clean_snippets(value)
                elif key.startswith("css_"):
                    self.settings[key] = str(value or "")[:MAX_CSS_CHARS]
                elif key == "webhook_url":
                    value = str(value or "").strip()[:2048]
                    # http(s) only. A file: or a gopher: here would be someone
                    # using the notifier to make the server fetch something.
                    self.settings[key] = value if value.startswith(("http://", "https://")) else ""
                elif key == "clock_24h":
                    self.settings[key] = bool(value)
                elif key == "changelog_seen":
                    self.settings[key] = str(value or "")[:32]
                elif key == "clock_zone":
                    # Validated against the system's own zone database rather
                    # than a length check: a name that is not a real zone would
                    # throw in the browser and take the whole pane with it.
                    name = str(value or "").strip()[:64]
                    if not name:
                        self.settings[key] = ""
                    else:
                        try:
                            zoneinfo.ZoneInfo(name)
                            self.settings[key] = name
                        except (zoneinfo.ZoneInfoNotFoundError, ValueError, OSError):
                            pass
                elif key in ("webhook_secret", "panel_url"):
                    self.settings[key] = str(value or "").strip()[:2048]
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
