#!/usr/bin/env python3
"""Verify per-session token usage — the sum History.session_usage reads from a
session's transcript.

A CLI logs a ``usage`` block on each assistant message; this adds them across
the file. The check writes a small fake Claude-style transcript, points a fake
registry's history dir at it, and confirms the totals, the message count, and
the empty case. No panel or browser needed — the summing is the whole feature.

    python3 tools/usage_check.py

Exit status is 0 on pass, 1 on fail.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from clique.history import History
from clique.registry import CliType


class _Reg:
    def __init__(self, root: str) -> None:
        self._root = root

    def types(self) -> dict:
        return {
            "claude": CliType(
                id="claude",
                label="Claude",
                command="claude",
                history={"dir": self._root, "layout": "dashed-dir", "pattern": "*.jsonl"},
            ),
            "shell": CliType(id="shell", label="Shell", command="bash"),
        }


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="clique-usage-"))
    try:
        (root / "proj").mkdir()
        sid = "sess-1234"
        recs = [
            {"type": "user", "message": {"content": "hi"}},
            {
                "type": "assistant",
                "message": {
                    "usage": {
                        "input_tokens": 100,
                        "output_tokens": 50,
                        "cache_read_input_tokens": 10,
                        "cache_creation_input_tokens": 5,
                    }
                },
            },
            {"type": "assistant", "message": {"usage": {"input_tokens": 200, "output_tokens": 80}}},
            {"type": "assistant", "message": {}},  # no usage block — skipped
        ]
        (root / "proj" / f"{sid}.jsonl").write_text("\n".join(json.dumps(r) for r in recs))

        hist = History(_Reg(str(root)))
        u = hist.session_usage("claude", sid)
        empty = hist.session_usage("claude", "no-such-session")
        shell = hist.session_usage("shell", sid)

        checks = {
            "input summed": u["tokens"]["input"] == 300,
            "output summed": u["tokens"]["output"] == 130,
            "cache_read summed": u["tokens"]["cache_read"] == 10,
            "cache_creation summed": u["tokens"]["cache_creation"] == 5,
            "total summed": u["tokens"]["total"] == 445,
            "messages counted (usage only)": u["messages"] == 2,
            "has_data true": u["has_data"] is True,
            "missing session has no data": empty["has_data"] is False,
            "non-transcript cli has no data": shell["has_data"] is False,
        }
    finally:
        shutil.rmtree(root, ignore_errors=True)

    for label, good in checks.items():
        print(f"  {'ok  ' if good else 'FAIL'} {label}")
    ok = all(checks.values())
    print("usage_check:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
