"""Entry point: ``python3 -m muxpanel --host ... --port ...``."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from . import __version__, tmux
from .app import Panel, serve
from .auth import Auth, AuthDisabled
from .registry import Registry, RegistryError
from .store import Store

ROOT = Path(__file__).resolve().parents[1]
HOME = Path(os.environ.get("MUXPANEL_HOME", "/root/.muxpanel"))


def read_password(explicit: str | None) -> str:
    """Password from the flag, the environment, or a 0600 file — never the repo.

    The file is the one that matters in practice: systemd units end up in git
    and environment variables show up in `ps`, so the secret lives on disk with
    permissions and is read at startup.
    """
    if explicit:
        return explicit
    if os.environ.get("MUXPANEL_PASSWORD"):
        return os.environ["MUXPANEL_PASSWORD"]
    try:
        return (HOME / "password").read_text().strip()
    except OSError:
        return ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="muxpanel", description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=3200)
    parser.add_argument("--password", default=None,
                        help="overrides MUXPANEL_PASSWORD and ~/.muxpanel/password")
    parser.add_argument("--config", default=str(ROOT / "config" / "clis.toml"))
    parser.add_argument("--state", default=str(ROOT / "data" / "state.json"))
    parser.add_argument("--version", action="version", version=__version__)
    args = parser.parse_args(argv)

    if not tmux.available():
        print("tmux not found. Install: sudo apt install tmux", file=sys.stderr)
        return 1

    try:
        registry = Registry(Path(args.config))
        registry.types()  # fail loudly at startup, not on the first click
    except RegistryError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 1

    try:
        auth = Auth(read_password(args.password), HOME / "secret")
    except AuthDisabled as exc:
        print(str(exc), file=sys.stderr)
        return 1

    panel = Panel(Store(Path(args.state)), registry, auth)
    try:
        serve(args.host, args.port, panel)
    except KeyboardInterrupt:
        print("\nstopped", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
