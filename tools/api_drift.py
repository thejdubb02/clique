"""Fail when API.md has fallen behind the code.

An API reference is a promise, and a hand-maintained one is a promise that
holds for about three releases. Anyone driving CLIque from a script or an agent
is reading this file, so a route that exists and is undocumented is worse than
no reference at all — it teaches people the surface is smaller than it is, and
a setting they cannot find is a setting they will store somewhere worse.

So the check is mechanical: every route in `app.py`, every settings key the
store accepts, and every session field `PATCH` will take has to appear in
API.md. Adding one and forgetting to write it down breaks the build, which is
the only kind of documentation discipline that survives a busy day.

It checks *presence*, not prose. Nothing can confirm the description is any
good — but nothing silently disappears either.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from clique.store import DEFAULT_SETTINGS

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "clique" / "app.py").read_text(encoding="utf-8")
REFERENCE = (ROOT / "API.md").read_text(encoding="utf-8")


def routes() -> set[str]:
    """Every path the server answers on, however it is matched.

    Three spellings, because the router uses three: whole-path equality, a
    `/api/sessions/<id>/verb` suffix, and `parts[1] == "..."` for the ones
    with an id in the middle.
    """
    found = set(re.findall(r'path == "(/[^"]*)"', SOURCE))
    found |= {f"/api/sessions/<id>/{verb}"
              for verb in re.findall(r'path\.endswith\("/([^"]+)"\)', SOURCE)}
    found |= {f"/api/{group}/<id>"
              for group in re.findall(r'parts\[1\] == "([^"]+)"', SOURCE)}
    # Not part of the contract: "/" is the app itself, and the login form is a
    # browser flow rather than something an agent calls.
    return {r for r in found if r not in {"/", "/login"}}


def session_fields() -> set[str]:
    match = re.search(r'allowed = \{([^}]*)\}', SOURCE)
    return set(re.findall(r'"([^"]+)"', match.group(1))) if match else set()


def documented(needle: str) -> bool:
    """Present in the reference, in prose or in a table cell.

    Routes with an `<id>` in them are matched on their stem: the file writes
    `/api/sessions/<id>/paste` under its own heading but refers to folders as
    `/api/folders/<id>` in a list, and both should count.
    """
    if needle in REFERENCE:
        return True
    stem = needle.replace("<id>", "").rstrip("/")
    return bool(stem) and stem in REFERENCE


def main() -> int:
    missing: list[tuple[str, str]] = []
    for route in sorted(routes()):
        if not documented(route):
            missing.append(("route", route))
    for key in DEFAULT_SETTINGS:
        if f"`{key}`" not in REFERENCE:
            missing.append(("setting", key))
    for field in sorted(session_fields()):
        if f"`{field}`" not in REFERENCE:
            missing.append(("session field", field))

    checked = len(routes()) + len(DEFAULT_SETTINGS) + len(session_fields())
    if missing:
        print(f"API.md is behind the code — {len(missing)} of {checked} undocumented:\n")
        for kind, name in missing:
            print(f"  {kind:<14} {name}")
        print("\nDocument them in API.md, or the next person driving CLIque"
              "\nfrom a script will not know they exist.")
        return 1
    print(f"API.md covers all {checked} routes, settings and session fields")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
