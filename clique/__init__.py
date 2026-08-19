"""CLIque — Your private clique of CLIs.

Folder-organised, CLI-agnostic terminal sessions in a browser, kept alive in tmux.
"""

from __future__ import annotations

import subprocess
from functools import lru_cache
from pathlib import Path

#: Semver, bumped with every release-worthy change and shown in the sidebar.
#: Minor for features, patch for fixes. The version alone is not enough to
#: identify a build during active development, which is what build_id is for.
__version__ = "0.41.0"


@lru_cache(maxsize=1)
def build_id() -> str:
    """Short commit of the working tree, or "" outside a checkout.

    A bug report that says "0.2.0" during a week of daily commits names a
    range, not a build. This makes it name a commit.
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(Path(__file__).resolve().parents[1]),
             "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return ""


def version_string() -> str:
    build = build_id()
    return f"{__version__}+{build}" if build else __version__
