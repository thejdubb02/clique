"""Entry point: ``python3 -m clique --host ... --port ...``."""

from __future__ import annotations

import argparse
import getpass
import os
import sys
import time
from pathlib import Path

from . import tmux, version_string
from .app import Panel, serve
from .auth import Auth, AuthDisabled, hash_password
from .registry import Registry, RegistryError
from .store import Store
from .tokens import TokenStore

ROOT = Path(__file__).resolve().parents[1]
HOME = Path(os.environ.get("CLIQUE_HOME", str(Path.home() / ".clique")))


def read_password(explicit: str | None) -> str:
    """Password from the flag, the environment, or a 0600 file — never the repo.

    The file is the one that matters in practice: systemd units end up in git
    and environment variables show up in `ps`, so the secret lives on disk with
    permissions and is read at startup.
    """
    if explicit:
        return explicit
    if os.environ.get("CLIQUE_PASSWORD"):
        return os.environ["CLIQUE_PASSWORD"]
    try:
        return (HOME / "password").read_text().strip()
    except OSError:
        return ""


def set_password(args) -> int:
    """Write a hashed password. The plaintext never touches disk."""
    plain = args.value or getpass.getpass("New password: ")
    if len(plain) < 8:
        print("too short — this gates a root terminal", file=sys.stderr)
        return 1
    target = HOME / "password"
    target.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(hash_password(plain) + "\n")
    print(f"password updated in {target} (scrypt hash, not the password)")
    print("restart to apply:  systemctl --user restart clique")
    return 0


def manage_tokens(args) -> int:
    store = TokenStore(HOME / "tokens.json")

    if args.action == "list":
        rows = store.listing()
        if not rows:
            print("no API tokens. Create one: python3 -m clique token create <name>")
        for row in rows:
            used = time.strftime("%Y-%m-%d", time.localtime(row["last_used"])) \
                if row["last_used"] else "never"
            print(f"{row['id']}  {row['name']:<24} {','.join(row['scopes']):<11} last used {used}")
        return 0

    if args.action == "revoke":
        if not args.target:
            print("which token? see: python3 -m clique token list", file=sys.stderr)
            return 1
        ok = store.revoke(args.target)
        print("revoked" if ok else f"no token with id {args.target}")
        return 0 if ok else 1

    if not args.target:
        print("name it, so `token list` is readable later", file=sys.stderr)
        return 1
    token, raw = store.create(args.target, ["read"] if args.read_only else None)
    print(f"created {token.id} ({','.join(token.scopes)})")
    print()
    print(f"  {raw}")
    print()
    print("Shown once — only its hash is stored. Put it in the agent's config now.")
    print("Use it as:  Authorization: Bearer <token>")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="clique", description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=3200)
    parser.add_argument("--password", default=None,
                        help="overrides CLIQUE_PASSWORD and ~/.clique/password")
    parser.add_argument("--config", default=str(ROOT / "config" / "clis.toml"))
    parser.add_argument("--state", default=str(ROOT / "data" / "state.json"))
    parser.add_argument("--version", action="version", version=version_string())

    # Token management is a CLI job, not an API one: an endpoint that mints
    # credentials is an endpoint that escalates any other hole into permanent
    # access. Creating one requires shell on the box.
    sub = parser.add_subparsers(dest="command")
    make = sub.add_parser("token", help="manage API tokens for agents")
    make.add_argument("action", choices=["create", "list", "revoke"])
    make.add_argument("target", nargs="?", help="name to create, or id to revoke")
    make.add_argument("--read-only", action="store_true")

    pw = sub.add_parser("password", help="set the login password (stored hashed)")
    pw.add_argument("value", nargs="?", help="omit to be prompted")

    args = parser.parse_args(argv)

    if args.command == "token":
        return manage_tokens(args)
    if args.command == "password":
        return set_password(args)

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

    panel = Panel(Store(Path(args.state)), registry, auth, TokenStore(HOME / "tokens.json"))
    try:
        serve(args.host, args.port, panel)
    except KeyboardInterrupt:
        print("\nstopped", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
