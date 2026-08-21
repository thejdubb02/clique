"""CLI type registry: ``config/clis.toml`` in, launch argv out.

The point of this module is that adding a coding CLI is a config block. Nothing
here may know that Claude Code or Grok exist — if a CLI name appears in this
file, the design has failed.

Config errors are raised at *load* time with the offending key named, not
discovered later as a session that starts and immediately dies.
"""

from __future__ import annotations

import os
import re
import shutil
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

#: Where CLIs get installed when they are not on a service's PATH.
#:
#: This list exists because grok was installed, working, and reported as
#: missing: systemd hands a user unit a minimal PATH, and every one of these
#: directories is somewhere a package manager routinely puts a binary.
#: Widening the service PATH fixes the common case; this catches the rest.
EXTRA_BIN_DIRS = [
    Path.home() / ".local/bin",
    Path.home() / ".npm-global/bin",
    Path.home() / ".bun/bin",
    Path.home() / ".cargo/bin",
    Path.home() / ".deno/bin",
    Path("/usr/local/bin"),
    Path("/opt/homebrew/bin"),
]

#: Substitutions available in ``args``/``resume``.
PLACEHOLDERS = {"id", "uuid", "name", "cwd", "mode", "cli_session_id"}

#: Only meaningful when reattaching, so it is an error anywhere else.
RESUME_ONLY = {"cli_session_id"}

_TOKEN = re.compile(r"\{([a-z_]+)\}")

#: Where icon files live, relative to this package.
ICON_DIR = Path(__file__).parent / "web" / "icons"

_icon_kind_cache: dict[str, tuple[float, bool]] = {}


def icon_is_full_colour(filename: str) -> bool:
    """Whether an icon must be drawn as an image rather than tinted as a mask.

    Two kinds of icon exist in the wild and they cannot be drawn the same way.

    A *glyph* — one colour on transparency — works as a CSS mask: the file
    supplies the silhouette and the panel supplies the colour, which is what
    lets one file serve the tinted, grey and status-coloured modes.

    A *badge* — a logo with its own background or several colours — does not.
    Used as a mask it flattens to a solid square, which is exactly how Cline
    and OpenCode were rendering. Those have to be drawn at full colour as an
    ordinary image, losing tintability but gaining the real logo.

    Detected rather than declared, so dropping a new icon in the directory
    does the right thing without anyone having to classify it correctly.
    """
    path = ICON_DIR / filename
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return False
    cached = _icon_kind_cache.get(filename)
    if cached and cached[0] == mtime:
        return cached[1]

    if path.suffix.lower() != ".svg":
        full = True                        # a raster logo is never a silhouette
    else:
        try:
            body = path.read_text(errors="replace")
        except OSError:
            return False
        colours = {c.lower() for c in re.findall(r"#[0-9a-fA-F]{3,6}", body)}
        box = re.search(r'viewBox="0 0 ([\d.]+) ([\d.]+)"', body)
        background = False
        if box:
            w, h = box.group(1), box.group(2)
            background = bool(
                re.search(rf'<rect[^>]*width="{w}"[^>]*height="{h}"', body)
                or re.search(rf'<rect[^>]*height="{h}"[^>]*width="{w}"', body)
            )
        full = background or len(colours) >= 2 or "Gradient" in body

    _icon_kind_cache[filename] = (mtime, full)
    return full


class RegistryError(Exception):
    """Raised for a malformed clis.toml, always naming the CLI and the key."""


@dataclass(frozen=True)
class CliType:
    id: str
    label: str
    command: str
    args: list[str] = field(default_factory=list)
    resume: list[str] = field(default_factory=list)
    color: str = "#8b8b8b"
    modes: list[str] = field(default_factory=list)
    mode_key: str = "S-Tab"
    mode_label: str = "{mode} mode"
    #: Filename in web/icons/. Drawn as a mask and tinted, so only the
    #: silhouette matters — a flat single-colour shape, not artwork.
    #: Whether this CLI draws its own input box at the bottom of the pane.
    #:
    #: Purely about what is on screen. A CLI with a boxed prompt sitting
    #: directly above CLIque's own prompt box is two input boxes stacked, and
    #: the panel's one is the redundant half. A CLI that just prints `>` and
    #: waits — a shell, most readline tools — is not doubled by anything, and
    #: there the panel's box is the only place Run, the repeat counter and a
    #: saved draft live.
    #:
    #: Read only when `input_mode` is "auto", which is the default. The two
    #: explicit settings still win.
    own_input: bool = False

    #: Where to ask whether the service behind this CLI is having a bad day.
    #:
    #: `url` is a Statuspage v2 endpoint — `.../api/v2/status.json` — and
    #: `page` is the human page to link to. One shape, not one adapter per
    #: vendor: Anthropic, OpenAI, GitHub and Cursor all run Statuspage, and a
    #: second format here would be the first step towards a per-vendor
    #: directory of scrapers.
    #:
    #: A CLI with no block is simply never asked. Nothing is invented for it,
    #: and a CLI that runs against your own model has no service to be down.
    status: dict = field(default_factory=dict)

    icon: str = ""
    #: Where this CLI keeps its own conversation history, so past work can be
    #: found and resumed. A table, not code:
    #:
    #:   [cli.claude.history]
    #:   dir     = "~/.claude/projects"
    #:   layout  = "dashed-dir"   # <dir>/<cwd with / and . as ->/<id>.jsonl
    #:   pattern = "*.jsonl"
    #:
    #: Omit it and the CLI simply has no resumable history, which is the right
    #: answer for `shell` and for anything whose transcripts we cannot find.
    history: dict = field(default_factory=dict)
    #: How this CLI says it is stuck. A table of regexes matched against the
    #: last lines of the pane once it goes quiet:
    #:
    #:   [cli.claude.attention]
    #:   waiting = ["Do you want to proceed\\?"]
    #:   error   = ["^Error:"]
    #:
    #: Config, not code — which is the whole point. CLIque never learns what a
    #: vendor's prompt looks like; someone writes it down here, and can fix it
    #: the day the vendor changes it without waiting for a release. Omit the
    #: table and the session still gets the generic prompts (y/n, Do you want,
    #: a numbered choice) that every CLI tends to draw.
    attention: dict = field(default_factory=dict)

    @property
    def waiting_patterns(self) -> list[str]:
        return [str(x) for x in (self.attention.get("waiting") or [])]

    @property
    def error_patterns(self) -> list[str]:
        return [str(x) for x in (self.attention.get("error") or [])]

    @property
    def mode_seq(self) -> str:
        """What a terminal actually sends when `mode_key` is pressed.

        `mode_key` is written in tmux's notation because that is what sends it
        *into* a pane. Recognising the same key coming *out* of the keyboard
        needs the escape sequence instead — otherwise CLIque only knows about
        mode changes it made itself, and the pill drifts the moment anyone
        cycles the mode by hand.

        Only the shapes that appear in the registry are translated. An
        unrecognised key gives "", which means "cannot detect this one" and is
        handled as such rather than guessed at.
        """
        key = self.mode_key or ""
        if key in ("S-Tab", "BTab"):
            return "\x1b[Z"
        if key == "Tab":
            return "\t"
        if len(key) == 3 and key.startswith("C-") and key[2].isalpha():
            return chr(ord(key[2].lower()) - 96)
        return ""

    @property
    def has_modes(self) -> bool:
        """Whether the UI should show a mode pill for this CLI."""
        return bool(self.modes)

    @property
    def default_mode(self) -> str | None:
        return self.modes[0] if self.modes else None

    def resolve(self) -> str | None:
        """Absolute path to the binary, or None if it is genuinely not here.

        PATH first, then the usual install directories. Returning the resolved
        path rather than a bool matters: it is what gets executed, so a CLI
        found outside PATH still launches instead of merely being listed.
        """
        if "/" in self.command:
            return self.command if os.access(self.command, os.X_OK) else None

        found = shutil.which(self.command)
        if found:
            return found

        for directory in EXTRA_BIN_DIRS:
            candidate = directory / self.command
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
            # Some CLIs install into a directory named after themselves.
            nested = directory.parent / f".{self.command}" / "bin" / self.command
            if nested.is_file() and os.access(nested, os.X_OK):
                return str(nested)
        return None

    @property
    def installed(self) -> bool:
        """False is informational, not fatal — a CLI may be configured early."""
        return self.resolve() is not None

    def as_dict(self) -> dict:
        """The shape the frontend consumes. No Python types leak."""
        return {
            "id": self.id,
            "label": self.label,
            "color": self.color,
            "modes": list(self.modes),
            "mode_key": self.mode_key,
            "installed": self.installed,
            # Empty means "no drawing for this one" — the UI falls back to a
            # letter badge, so a newly added CLI looks deliberate without
            # anyone having to draw an icon first.
            "icon": self.icon,
            "icon_full_color": bool(self.icon) and icon_is_full_colour(self.icon),
            "own_input": self.own_input,
        }


def _show(names) -> str:
    """Render placeholders back in the form the config file writes them."""
    return ", ".join("{" + n + "}" for n in sorted(names))


def _tokens(values: list[str]) -> set[str]:
    return {m for v in values for m in _TOKEN.findall(v)}


def _validate(cli: CliType) -> None:
    where = f"cli.{cli.id}"

    if not cli.command:
        raise RegistryError(f"{where}: `command` is required")

    for key, values in (("args", cli.args), ("resume", cli.resume)):
        unknown = _tokens(values) - PLACEHOLDERS
        if unknown:
            raise RegistryError(
                f"{where}.{key}: unknown placeholder(s) "
                f"{_show(unknown)}; known: {_show(PLACEHOLDERS)}"
            )

    misplaced = _tokens(cli.args) & RESUME_ONLY
    if misplaced:
        raise RegistryError(
            f"{where}.args: {_show(misplaced)} is only available in `resume` "
            f"— a new session has no prior id"
        )

    # A status block that is wrong should fail at load, next to the line that
    # is wrong, rather than as a feed that quietly never reports anything.
    if cli.status:
        unknown = set(cli.status) - {"url", "page"}
        if unknown:
            raise RegistryError(
                f"{where}.status: unknown key(s) {_show(unknown)}; known: url, page"
            )
        for key in ("url", "page"):
            value = str(cli.status.get(key) or "")
            if not value:
                raise RegistryError(f"{where}.status: `{key}` is required")
            if not value.startswith("https://"):
                raise RegistryError(
                    f"{where}.status.{key}: must be an https:// URL, not {value!r}"
                )

    if cli.icon and ("/" in cli.icon or ".." in cli.icon):
        raise RegistryError(
            f"{where}.icon: must be a bare filename in web/icons/, not a path"
        )

    # A {mode} with no `modes` list would render empty and hand the CLI a bare
    # flag with no value, which fails in a way that looks like a CLI bug.
    if "mode" in _tokens(cli.args + cli.resume) and not cli.modes:
        raise RegistryError(
            f"{where}: uses {{mode}} but declares no `modes` list"
        )


def parse(data: dict) -> dict[str, CliType]:
    """Turn parsed TOML into validated CliTypes. Split out so tests skip disk."""
    block = data.get("cli")
    if not isinstance(block, dict) or not block:
        raise RegistryError("no [cli.*] sections found")

    types: dict[str, CliType] = {}
    for cli_id, raw in block.items():
        if not isinstance(raw, dict):
            raise RegistryError(f"cli.{cli_id}: expected a table")
        unknown_keys = set(raw) - {
            "label", "command", "args", "resume", "color",
            "modes", "mode_key", "mode_label", "icon", "history", "attention",
            "status", "own_input",
        }
        if unknown_keys:
            raise RegistryError(
                f"cli.{cli_id}: unknown key(s) {', '.join(sorted(unknown_keys))}"
            )
        cli = CliType(
            id=cli_id,
            label=raw.get("label", cli_id),
            command=raw.get("command", ""),
            args=list(raw.get("args", [])),
            resume=list(raw.get("resume", [])),
            color=raw.get("color", "#8b8b8b"),
            modes=list(raw.get("modes", [])),
            mode_key=raw.get("mode_key", "S-Tab"),
            mode_label=raw.get("mode_label", "{mode} mode"),
            icon=raw.get("icon", ""),
            own_input=bool(raw.get("own_input", False)),
            status=dict(raw.get("status") or {}),
            history=dict(raw.get("history") or {}),
            attention=dict(raw.get("attention") or {}),
        )
        _validate(cli)
        types[cli_id] = cli
    return types


class Registry:
    """Reads clis.toml, and re-reads it when it changes on disk.

    Hot reload is deliberate: adding a CLI should not cost a restart, because a
    restart drops every attached browser.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._types: dict[str, CliType] = {}
        self._mtime: float = -1.0

    def types(self) -> dict[str, CliType]:
        try:
            mtime = self.path.stat().st_mtime
        except OSError as exc:
            raise RegistryError(f"cannot read {self.path}: {exc}") from exc

        if mtime != self._mtime:
            with self.path.open("rb") as fh:
                data = tomllib.load(fh)
            # Only adopt the new set once it has fully validated, so a typo
            # leaves the last good registry serving instead of emptying it.
            self._types = parse(data)
            self._mtime = mtime
        return self._types

    def get(self, cli_id: str) -> CliType:
        types = self.types()
        if cli_id not in types:
            raise RegistryError(
                f"unknown CLI type {cli_id!r}; configured: {', '.join(sorted(types))}"
            )
        return types[cli_id]

    def launch_argv(
        self,
        cli_id: str,
        *,
        session_id: str,
        name: str,
        cwd: str,
        mode: str | None = None,
        cli_session_id: str | None = None,
    ) -> list[str]:
        """Full argv to exec for a session, resume form when we know a prior id."""
        cli = self.get(cli_id)

        if mode is None:
            mode = cli.default_mode
        elif not cli.has_modes:
            raise RegistryError(f"cli.{cli_id}: does not support modes")
        elif mode not in cli.modes:
            raise RegistryError(
                f"cli.{cli_id}: unknown mode {mode!r}; "
                f"configured: {', '.join(cli.modes)}"
            )

        template = cli.resume if (cli_session_id and cli.resume) else cli.args
        ctx = {
            "id": session_id,
            "uuid": session_id,
            "name": name,
            "cwd": cwd,
            "mode": mode or "",
            "cli_session_id": cli_session_id or "",
        }
        rendered = [_TOKEN.sub(lambda m: ctx[m.group(1)], arg) for arg in template]
        # The resolved path, not the bare name: the CLI may live somewhere the
        # service's PATH does not reach.
        return [cli.resolve() or cli.command, *rendered]
