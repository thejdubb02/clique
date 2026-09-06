#!/usr/bin/env python3
"""Are the panel, the repo, the site and PyPI all saying the same thing?

Four surfaces drift apart quietly and each one embarrasses differently: the
site tells a stranger to `pip install` something two months old, the README
badge claims a version nobody can get, a release is tagged but never published,
or work sits committed on this box and nowhere else.

None of that is visible from inside the panel, and all of it is checkable in
about ten seconds, so it is a command rather than a thing to remember at the
end of a session.

    python3 tools/shipped_check.py            # everything, including the network
    python3 tools/shipped_check.py --local    # skip the network, for CI or a plane

Exit 0 when every surface agrees. Exit 1 with the list when they do not, and a
warning is not a failure: being ahead of PyPI is normal right up until you have
told somebody to install it.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SITE = "https://useclique.dev"
PYPI = "https://pypi.org/pypi/clique-panel/json"
TIMEOUT = 20

problems: list[str] = []
notes: list[str] = []


def say(label: str, value: str, ok: bool | None = True) -> None:
    mark = "ok  " if ok else ("warn" if ok is None else "FAIL")
    print(f"  {mark} {label:<26} {value}")


def panel_version() -> str:
    text = (ROOT / "clique" / "__init__.py").read_text(encoding="utf-8")
    found = re.search(r'^__version__ = "([^"]+)"', text, re.M)
    return found.group(1) if found else ""


def readme_badge() -> str:
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    found = re.search(r"badge/version-([0-9][^-]*)-", text)
    return found.group(1) if found else ""


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, check=False
    ).stdout.strip()


def fetch(url: str) -> str | None:
    """Named, because the default one is refused.

    GitHub Pages answers `Python-urllib` with a 403, so the first version of
    this reported the site as unreachable while curl fetched it fine. A check
    that cries wolf gets ignored, which is worse than not having it.
    """
    request = urllib.request.Request(url, headers={"User-Agent": "clique-shipped-check"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return response.read(400_000).decode("utf-8", "replace")
    except (urllib.error.URLError, OSError, ValueError):
        return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--local", action="store_true", help="skip the site and PyPI")
    args = ap.parse_args(argv)

    version = panel_version()
    print("the panel")
    say("version", version, bool(version))
    if not version:
        problems.append("could not read __version__")

    print("\nthe repo")
    badge = readme_badge()
    say("README badge", badge or "(none)", badge == version)
    if badge != version:
        problems.append(f"README badge says {badge or 'nothing'}, the panel says {version}")

    dirty = git("status", "--porcelain")
    say(
        "working tree",
        "clean" if not dirty else f"{len(dirty.splitlines())} file(s) uncommitted",
        not dirty,
    )
    if dirty:
        problems.append("uncommitted changes")

    git("fetch", "--quiet", "origin")
    ahead = git("rev-list", "--count", "origin/main..HEAD")
    say("unpushed commits", ahead or "0", ahead in ("", "0"))
    if ahead not in ("", "0"):
        problems.append(f"{ahead} commit(s) not pushed")

    tag = f"v{version}"
    tagged = git("tag", "--list", tag)
    say("tagged", tag if tagged else f"{tag} does not exist", bool(tagged) or None)
    if not tagged:
        notes.append(f"no {tag} tag, so nothing has been released at this version")

    if args.local:
        print("\nskipping the site and PyPI (--local)")
    else:
        print("\nthe site")
        page = fetch(SITE)
        say("useclique.dev", "reachable" if page else "unreachable", bool(page))
        if page is None:
            problems.append("the site did not answer")
        else:
            pip = "pip install clique-panel" in page
            say("install shown", "pip install" if pip else "not the pip line", pip)
            if not pip:
                problems.append("the site is not telling people to pip install")

        print("\nPyPI")
        raw = fetch(PYPI)
        published = ""
        if raw:
            try:
                published = json.loads(raw)["info"]["version"]
            except (ValueError, KeyError):
                published = ""
        say("serving", published or "unknown", bool(published))
        if not published:
            problems.append("could not read what PyPI is serving")
        elif published != version:
            # Being ahead is normal while work is in flight. It stops being
            # normal the moment the site tells a stranger to install it, which
            # it does, so this is loud rather than silent.
            say("matches the panel", f"{published} published, {version} here", None)
            notes.append(
                f"PyPI is on {published} and this is {version}: anyone following the "
                "site's install line gets the older one"
            )
        else:
            say("matches the panel", "yes", True)

    print()
    for note in notes:
        print(f"  note: {note}")
    for problem in problems:
        print(f"  problem: {problem}")
    if not problems and not notes:
        print("  every surface agrees")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
