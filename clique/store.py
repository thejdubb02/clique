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


def colour(value, fallback: str) -> str:
    """A colour we are willing to put in a stylesheet, or the fallback.

    Folder colours reach the browser inside a `style` attribute. Anything that
    is not a plain hex triple has no business there: a value like
    `red" onmouseover="...` closes the attribute and adds script to the
    sidebar, which turns "recolour a folder" into stored XSS for the next
    person to open the panel. Settings colours were checked this way already;
    folders were the path that never was.
    """
    text = str(value or "").strip()
    return text if _HEX.fullmatch(text) else fallback


#: Seeded on first run. Empty match lists — fill these with your own trees
#: in the panel. A match is a directory prefix that auto-files new sessions.
DEFAULT_FOLDERS = [
    {"id": "f-work", "name": "Work", "color": "#c7915b", "match": []},
    {"id": "f-personal", "name": "Personal", "color": "#2d7d46", "match": []},
]

PALETTE = [
    "#c7915b",
    "#6f42c1",
    "#2d7d46",
    "#1f6feb",
    "#0d7d8f",
    "#a63d2f",
    "#8b8b8b",
    "#d96f6f",
    "#e8a33d",
    "#3aa3a0",
    "#7a7fd6",
    "#ff5fa2",
    "#c4500a",
    "#8250df",
    "#1a7f37",
    "#0550ae",
    "#bf3989",
    "#9a6700",
    "#cf222e",
    "#0969da",
    "#bc4c00",
    "#5a32a3",
    "#087f5b",
    "#364fc7",
]

#: How a CLI is marked in the tab bar and sidebar.
#:
#: "both" is an icon tinted in the CLI's colour; "icon" is the same shape in
#: neutral grey; "color" is a plain colour chip; "none" is nothing at all. The
#: live/attached status dot is separate and always shown — that is status, not
#: branding, and hiding it would cost information rather than decoration.
MARKER_MODES = ("both", "icon", "color", "none")

#: Monospace stacks the pane is allowed to ask for. Each id maps to a CSS
#: fallback chain in the browser, so a font missing on this OS still lines
#: up instead of going proportional. Unknown ids are dropped, not stored.
FONT_FAMILIES = ("system", "menlo", "consolas", "ubuntu", "courier", "nerd")

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
    #: Which monospace stack the pane uses. Ids, not CSS: the browser maps
    #: them to a fallback chain that exists on Windows, Mac and Linux, so a
    #: font that is not installed still lines up instead of going proportional.
    "font_family": "system",
    #: Ctrl+K opens the command palette, which means the pane never sees that
    #: key — and Ctrl+K is readline's kill-to-end-of-line. Anyone who uses it
    #: there can hand it back; the palette stays reachable on Ctrl+Shift+P,
    #: which no terminal claims.
    "palette_hotkey": True,
    #: Past conversations listed under the live sessions in each folder.
    #:
    #: Off, and it used to be on. The reasoning for on was that a tool you have
    #: just moved to should show you your work rather than an empty tree, and
    #: that is right for the first ten minutes and wrong for every day after:
    #: measured on a real install, 285 past conversations against 2 running
    #: sessions. At that ratio the sidebar stops being a view of what is
    #: happening and becomes a haystack with your work in it.
    #:
    #: Nothing is lost by it. History is searchable in the command palette —
    #: everything, not a recent slice — and the empty pane offers the last few
    #: to pick up. Those are places designed for looking something up; the
    #: sidebar is for seeing what is running.
    "history_in_sidebar": False,
    #: Days of history the sidebar will show when it is switched on. Without a
    #: ceiling the list only ever grows, and a conversation from three weeks
    #: ago is something you search for rather than something you scroll past.
    "history_days": 14,
    #: Kill an idle session's process after this many hours and grey its tab,
    #: freeing its memory — a claude tab is ~700 MB of nothing while idle. Its
    #: state is on disk, so clicking the tab resumes it exactly where it was.
    #: Only a session that can be resumed, that no browser is looking at, and
    #: that is not busy is ever reaped. 0 turns it off.
    "reap_idle_hours": 6,
    #: Auto-delete dropped/pasted files older than this many days from each
    #: session's scratch folders (.clique-drops, .claude-images) — nothing else
    #: on disk is ever touched. Off by default (0): a share is your file, and it
    #: is not this panel's place to delete your files behind your back until you
    #: ask it to. Turn it on in Settings and pick the age. The manual purge and
    #: the storage readout work whether this is on or off.
    "drop_cleanup_days": 0,
    #: "auto" lets each CLI decide: a CLI that draws its own input box gets no
    #: second box under it, and one that does not — a shell, a readline tool —
    #: keeps the panel's, which is also the only place Run, the repeat counter
    #: and a saved draft live. "panel" always shows it, "terminal" never does.
    #:
    #: The mode pill is not part of this. It is the control for a CLI's
    #: permission mode, and hiding it along with the prompt box was the bug in
    #: the two-value version of this setting.
    #:
    #: Snippets work whatever this says: CLIque owns the PTY, so an expansion
    #: is injected into the pane either way.
    "input_mode": "auto",
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
    #: A one-click confirm before a command that matches one of the patterns
    #: below is sent from the prompt box or a broadcast. Not a block, and not a
    #: guarantee — the pane still has a shell — but a guard against the
    #: fat-fingered catastrophe. Patterns are plain, case-insensitive substrings
    #: (a string in a list, the same shape as every other pattern here — never a
    #: regex to mis-write), editable in Settings. The defaults are the
    #: catastrophic-and-rarely-meant ones; everyday `rm -rf ./build` is
    #: deliberately not among them, because a guard that cries wolf is turned off.
    "confirm_destructive": True,
    "destructive_patterns": [
        "rm -rf /",
        "rm -rf ~",
        "rm -rf $HOME",
        "rm -fr /",
        "mkfs",
        "of=/dev/",
        "> /dev/sd",
        "> /dev/nvme",
        ":(){",
        "git push --force",
        "git push -f",
        "git reset --hard",
        "drop database",
        "drop table",
    ],
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
    #: Whether to ask the provider behind a running CLI if it is having a bad
    #: day. Statuspage feeds named in clis.toml, read every five minutes, and
    #: only for CLIs that have a session open right now — an idle panel makes
    #: no requests at all. On, because an outage banner nobody found in a
    #: settings sheet is an outage banner nobody gets; off is one switch, for
    #: anyone who wants a panel that never reaches the internet.
    "service_status": True,
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

#: A draft is text you are about to send and a name is a label — neither is a
#: file, and /api/state replays every one on every three-second poll.
MAX_DRAFT_CHARS = 100_000
MAX_NAME_CHARS = 200

#: Same reasoning: custom CSS is a stylesheet, not a payload.
MAX_CSS_CHARS = 40000


@dataclass
class Folder:
    id: str
    name: str
    color: str = "#8b8b8b"
    #: An optional emoji shown in place of the colour dot. Empty = the dot.
    emoji: str = ""
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
    #: A git worktree CLIque made for this session, so several agents can run
    #: on one repo without clobbering each other. Empty for an ordinary
    #: session. Deleting the session removes it — but only when it is clean.
    worktree: str = ""
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
    #: Floated to the top of its group in the sidebar, above recency — a
    #: favourite. The few sessions you keep coming back to should not sink as
    #: newer ones arrive.
    pinned: bool = False
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
    #: Why it is waiting, when the source knows: "permission" (wants an approval
    #: — the inbox offers Approve/Deny), "idle" (a question or a finished turn —
    #: a reply), or "" (unspecified). Set alongside `signal`, cleared with it.
    signal_note: str = ""


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


def _clean_patterns(value) -> list[str]:
    """Match strings for the destructive-command confirm: trimmed, deduped,
    bounded. Plain substrings, so nothing here is compiled or executed — a
    hostile settings write is at worst a list of harmless strings."""
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for raw in value:
        text = str(raw or "").strip()[:120]
        if text and text not in out:
            out.append(text)
        if len(out) >= 100:
            break
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
        out.append(
            {
                "trigger": trigger[:40],
                "label": str(raw.get("label") or "").strip()[:80],
                "text": text,
            }
        )
    return out


class Store:
    """Thread-safe reader/writer for state.json.

    Every mutation takes the lock and writes immediately. The file is small and
    writes are rare (a session created, a rename, a drag), so buffering would
    only add a window in which a crash loses the user's folders.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        # The state file, never the directory holding it. A directory here is
        # a caller mistake ($CLIQUE_HOME instead of $CLIQUE_HOME/state.json),
        # and _write would rename it out from under itself: `path.replace(
        # path.with_suffix(".json.bak"))` on a directory moves the whole home.
        # Fail loudly instead of clobbering a workspace.
        if self.path.is_dir():
            raise ValueError(
                f"state path is a directory, not a file: {self.path} "
                "-- pass $CLIQUE_HOME/state.json, not $CLIQUE_HOME"
            )
        self._lock = threading.RLock()
        self.folders: list[Folder] = []
        self.sessions: list[Session] = []
        self.settings: dict = dict(DEFAULT_SETTINGS)
        self.themes: list[dict] = []
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
        self.folders = [
            Folder(**{k: v for k, v in f.items() if k in Folder.__annotations__}) for f in folders
        ]
        self.sessions = [
            Session(**{k: v for k, v in s.items() if k in Session.__annotations__})
            for s in raw.get("sessions", [])
        ]
        # Merge rather than replace, so a setting added in a later version
        # appears with its default instead of being missing.
        self.settings = {**DEFAULT_SETTINGS, **(raw.get("settings") or {})}
        # Bring-your-own-key LLM providers. Kept out of `settings` on purpose:
        # each carries an encrypted key, and settings is echoed to every read
        # client — providers are fetched only on demand, keys never in the poll.
        # Themes somebody made here. Top level rather than inside `settings`
        # because each is a couple of dozen colours and `settings` rides every
        # poll — these are fetched once and again when one changes.
        themes = raw.get("themes")
        self.themes = (
            [t for t in themes if isinstance(t, dict) and t.get("id")]
            if isinstance(themes, list)
            else []
        )

        llm = raw.get("llm") if isinstance(raw.get("llm"), dict) else {}
        providers = llm.get("providers")
        routes = llm.get("routes")
        self.llm = {
            "providers": providers if isinstance(providers, list) else [],
            # feature name -> provider id, so different features can run on
            # different models (the inbox on something cheap-fast, a digest on
            # something stronger). A feature with no route falls back per-caller.
            "routes": routes if isinstance(routes, dict) else {},
        }
        if not raw:
            self._write()
        else:
            # Tighten a file written by a version that created it 0644. Doing
            # it here rather than only on the next write means an install that
            # is merely running, and not changing anything, still gets fixed.
            with contextlib.suppress(OSError):
                if self.path.exists() and (self.path.stat().st_mode & 0o077):
                    os.chmod(self.path, 0o600)
                backup = self.path.with_suffix(".json.bak")
                if backup.exists() and (backup.stat().st_mode & 0o077):
                    os.chmod(backup, 0o600)

    def _write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "folders": [asdict(f) for f in self.folders],
            "sessions": [asdict(s) for s in self.sessions],
            "settings": self.settings,
            "themes": self.themes,
            "llm": self.llm,
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
        # 0600 from creation, not whatever the umask happens to be. This file
        # holds the webhook secret and every unsent draft; the password hash,
        # the signing secret and the token store are all 0600 and this one was
        # 0644 purely because `open(..., "w")` does not take a mode.
        with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as handle:
            handle.write(json.dumps(payload, indent=2))
            handle.flush()
            os.fsync(handle.fileno())
        if self.path.exists():
            self.path.replace(self.path.with_suffix(".json.bak"))
        tmp.replace(self.path)
        # An existing file keeps its old mode through a rename, so tighten any
        # state.json written by a version that did not know better.
        with contextlib.suppress(OSError):
            os.chmod(self.path, 0o600)
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
                if key == "draft" and isinstance(value, str):
                    value = value[:MAX_DRAFT_CHARS]
                elif key == "name" and isinstance(value, str):
                    value = value[:MAX_NAME_CHARS]
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

    def reorder_folders(self, ordered_ids: list[str]) -> None:
        """Apply a drag-and-drop ordering. Unlisted folders keep their tail."""
        with self._lock:
            rank = {fid: i for i, fid in enumerate(ordered_ids)}
            for folder in self.folders:
                folder.order = rank.get(folder.id, len(rank) + folder.order)
            self.folders.sort(key=lambda f: f.order)
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
                            merged.pop(cli_id, None)  # back to the shipped one
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
                elif key == "destructive_patterns":
                    self.settings[key] = _clean_patterns(value)
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
                elif key == "font_family":
                    name = str(value or "").strip().lower()
                    if name in FONT_FAMILIES:
                        self.settings[key] = name
                elif key.startswith("font_"):
                    # Clamped rather than rejected: a bad number here should
                    # not be able to make the UI unreadable and unfixable.
                    with contextlib.suppress(TypeError, ValueError):
                        self.settings[key] = max(9, min(int(value), 28))
                elif key == "history_days":
                    with contextlib.suppress(TypeError, ValueError):
                        self.settings[key] = max(1, min(int(value), 90))
                elif key == "reap_idle_hours":
                    with contextlib.suppress(TypeError, ValueError):
                        self.settings[key] = max(0, min(int(value), 720))
                elif key == "drop_cleanup_days":
                    with contextlib.suppress(TypeError, ValueError):
                        self.settings[key] = max(0, min(int(value), 365))
                else:
                    # Coerced to the shape of its own default.
                    #
                    # This used to be `bool(value)` outright, on the assumption
                    # that anything not handled above was a checkbox. It held
                    # right up until a setting was a number, at which point 14
                    # was silently stored as True — and it would have done the
                    # same to the next one. The default is the schema; there is
                    # no reason to guess.
                    #
                    # bool before int deliberately: in Python a bool *is* an
                    # int, so testing int first would catch every checkbox.
                    default = DEFAULT_SETTINGS[key]
                    if isinstance(default, bool):
                        self.settings[key] = bool(value)
                    elif isinstance(default, int):
                        with contextlib.suppress(TypeError, ValueError):
                            self.settings[key] = int(value)
                    elif isinstance(default, str):
                        self.settings[key] = str(value or "")[:1024]
                    else:
                        # Lists and dicts all have explicit branches above; if
                        # one ever does not, storing it unchanged is closer to
                        # right than turning it into True.
                        self.settings[key] = value
            self._write()
            return self.settings

    # ------------------------------------------------------------ llm providers
    #
    # The store is deliberately dumb here: it persists whatever record it is
    # handed and never sees a plaintext key. Encryption (secretbox), redaction
    # and validation all live one layer up, in the panel — this just keeps the
    # list and writes it to the 0600 state file.

    def llm_providers(self) -> list[dict]:
        with self._lock:
            return [dict(p) for p in self.llm.get("providers", [])]

    def provider(self, provider_id: str) -> dict | None:
        with self._lock:
            found = next(
                (p for p in self.llm.get("providers", []) if p.get("id") == provider_id), None
            )
            return dict(found) if found else None

    def save_provider(self, record: dict) -> dict:
        """Insert or replace a provider by id, and persist."""
        with self._lock:
            providers = self.llm.setdefault("providers", [])
            for i, existing in enumerate(providers):
                if existing.get("id") == record.get("id"):
                    providers[i] = record
                    break
            else:
                providers.append(record)
            self._write()
            return dict(record)

    def remove_provider(self, provider_id: str) -> bool:
        with self._lock:
            providers = self.llm.get("providers", [])
            kept = [p for p in providers if p.get("id") != provider_id]
            if len(kept) == len(providers):
                return False
            self.llm["providers"] = kept
            # A route pointing at the deleted provider would dangle; drop it, so
            # a feature is never wired to a provider that no longer exists.
            routes = self.llm.get("routes", {})
            self.llm["routes"] = {f: p for f, p in routes.items() if p != provider_id}
            self._write()
            return True

    def llm_routes(self) -> dict:
        with self._lock:
            return dict(self.llm.get("routes", {}))

    def set_route(self, feature: str, provider_id: str | None) -> dict:
        """Point a feature at a provider, or clear it with a falsy provider_id."""
        with self._lock:
            routes = self.llm.setdefault("routes", {})
            if provider_id:
                routes[feature] = provider_id
            else:
                routes.pop(feature, None)
            self._write()
            return dict(routes)

    # ---------------------------------------------------------------- folders

    def folder(self, folder_id: str) -> Folder | None:
        with self._lock:
            return next((f for f in self.folders if f.id == folder_id), None)

    # ----------------------------------------------------------------- themes

    #: Enough to keep every theme anyone liked, few enough that a runaway
    #: script cannot turn the state file into a colour database.
    THEME_LIMIT = 40

    def add_theme(self, theme: dict) -> dict:
        """Store a finished theme and hand back the stored copy, id and all.

        The caller has already validated and derived it — this only owns the
        id, the ordering and the ceiling. Oldest goes when the ceiling is hit,
        because a theme somebody is still using is one they will have selected,
        and the selected one is never the oldest for long.
        """
        with self._lock:
            stored = {**theme, "id": f"t-{uuid.uuid4().hex[:8]}", "created": int(time.time())}
            self.themes.append(stored)
            del self.themes[: max(0, len(self.themes) - self.THEME_LIMIT)]
            self._write()
            return stored

    def delete_theme(self, theme_id: str) -> bool:
        """Forget a theme, and stop wearing it if it was the one on.

        Falling back to the default matters: the settings sheet that would let
        you pick another is drawn in the theme you just deleted, so leaving the
        setting pointing at nothing is how the panel comes back unpainted.
        """
        with self._lock:
            before = len(self.themes)
            self.themes = [t for t in self.themes if t.get("id") != theme_id]
            if len(self.themes) == before:
                return False
            if self.settings.get("theme") == theme_id:
                self.settings["theme"] = ""
            self._write()
            return True

    def add_folder(self, name: str, color: str | None = None) -> Folder:
        with self._lock:
            folder = Folder(
                id=f"f-{uuid.uuid4().hex[:8]}",
                name=name,
                color=colour(color, PALETTE[len(self.folders) % len(PALETTE)]),
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
                if key == "color":
                    value = colour(value, found.color)
                elif key == "emoji":
                    # Display text, escaped on render — bounded, not parsed. A
                    # couple of code points plus their variation selectors / ZWJ.
                    value = str(value or "").strip()[:16]
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
