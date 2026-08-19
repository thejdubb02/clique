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
    icon: str = ""

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
            "modes", "mode_key", "mode_label", "icon",
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
