"""Past conversations a CLI can be resumed into.

Switching tools should not cost anyone their history. Every CLI worth driving
already keeps its own transcripts on disk, and the registry already knows the
argv that resumes one — so the only missing piece was finding them.

Nothing here understands a conversation. It reads the *first* user turn for a
label and stops; the transcript itself is the CLI's business, and parsing it
would be the "LLM-generated session summaries" trap wearing a different hat.

Kept deliberately cheap. Discovery is a directory walk plus a bounded read of
the head of each file, cached against the directory's mtime, and it runs when
someone asks for it rather than on the three-second poll.
"""

from __future__ import annotations

import json
import re
import threading
import time
import urllib.parse
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

#: How much of a transcript to read. A transcript can be thirty megabytes, and
#: everything worth having is in the first fraction of a percent — but "near
#: the top" measured in bytes is not the same as measured in lines: an
#: `ai-title` record has been seen as far in as 86 KB, past a wall of tool
#: output. This window covers every one on this box with room to spare.
HEAD_BYTES = 160_000

#: Longest label kept. Past this a prompt is a document, and the sidebar has a
#: fixed width regardless.
MAX_LABEL = 72

#: Discovery is a few hundred small reads. Fast, but not free, and the answer
#: only changes when someone finishes a conversation.
CACHE_SECONDS = 30


@dataclass(frozen=True)
class Conversation:
    """One resumable conversation belonging to one CLI."""

    cli: str
    cli_session_id: str
    cwd: str
    label: str
    updated: float
    size: int = 0
    branch: str = ""

    @property
    def project(self) -> str:
        return Path(self.cwd).name or self.cwd

    def as_dict(self) -> dict:
        return {
            "cli": self.cli,
            "cli_session_id": self.cli_session_id,
            "cwd": self.cwd,
            "project": self.project,
            "label": self.label,
            "updated": self.updated,
            "size": self.size,
            "branch": self.branch,
        }


def _epoch(stamp: str) -> float:
    """An ISO-8601 timestamp as seconds, or 0 if it is not one."""
    if not stamp:
        return 0.0
    text = stamp.replace("Z", "+00:00")
    # Sub-second precision beyond microseconds is valid ISO and not something
    # fromisoformat accepts before 3.11 — trimmed rather than lost.
    text = re.sub(r"\.(\d{6})\d+", r".\1", text)
    try:
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        return 0.0


def _decode_dashed(
    name: str, roots: tuple[str, ...] = ("/root", "/home", "/tmp", "/opt", "/srv")  # noqa: S108 — a list of path prefixes to decode against, not a temp file
) -> str:
    """Turn `-home-you-projects-app` back into `/home/you/projects/app`.

    The encoding is lossy — a directory with a literal "-" in its name encodes
    the same as a "/" — so this is a best guess, and it is checked against the
    filesystem before being trusted. Where the guess does not exist we fall
    back to the transcript's own `cwd` field, which is exact.
    """
    if not name.startswith("-"):
        return name
    guess = "/" + name[1:].replace("-", "/")
    if Path(guess).is_dir():
        return guess
    # Walk the segments and re-join with "-" wherever the "/" split does not
    # exist on disk. Recovers names like "agent-infra" that contain a dash.
    parts = name[1:].split("-")
    path = ""
    for part in parts:
        step = f"{path}/{part}"
        if Path(step).exists() or not path:
            path = step
            continue
        merged = f"{path}-{part}"
        path = merged if Path(merged).exists() else step
    return path or guess


def _first_prompt(path: Path) -> tuple[str, str, str]:
    """(label, cwd, branch) read from the head of a transcript.

    The label prefers the CLI's *own* title for the conversation where it
    wrote one — "Analyze Duchamp room rates emails" beats the first eighty
    characters someone typed, every time — and falls back to the first human
    turn where it did not.

    Note what this is not: it does not read the conversation. It reads a field
    the CLI already computed, or the opening line, and stops. Summarising a
    transcript ourselves is the trap the roadmap names.

    Returns empty strings rather than raising: a transcript mid-write,
    half-flushed, or in a shape we have not seen is a conversation labelled by
    its directory, never an error that costs the whole listing.
    """
    label = title = cwd = branch = ""
    try:
        with path.open("rb") as fh:
            head = fh.read(HEAD_BYTES)
    except OSError:
        return "", "", ""

    for line in head.split(b"\n"):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue  # a truncated final line in the window we read
        if not isinstance(record, dict):
            continue
        cwd = cwd or str(record.get("cwd") or "")
        branch = branch or str(record.get("gitBranch") or "")
        if not title and record.get("aiTitle"):
            title = str(record["aiTitle"])[:MAX_LABEL].strip()
            if cwd:
                break   # everything worth having; the rest is transcript
        if label or record.get("type") != "user" or record.get("isSidechain"):
            continue
        text = _text_of(record.get("message"))
        if text:
            label = text
    return title or label, cwd, branch


def _text_of(message) -> str:
    """The human-readable part of a turn, whatever shape it arrived in."""
    if isinstance(message, str):
        raw = message
    elif isinstance(message, dict):
        content = message.get("content")
        if isinstance(content, str):
            raw = content
        elif isinstance(content, list):
            raw = " ".join(
                part.get("text", "")
                for part in content
                if isinstance(part, dict) and part.get("type") == "text"
            )
        else:
            return ""
    else:
        return ""

    # Command envelopes and pasted-file markers are not what anyone typed.
    raw = re.sub(r"<[^>]{1,40}>", " ", raw)
    raw = " ".join(raw.split())
    # A conversation opened with a bare slash command tells you nothing. Let
    # it fall through to the project name rather than filling the sidebar with
    # rows that all read "/clear".
    if raw.startswith(("Caveat:", "[Request interrupted")) or re.fullmatch(r"/\w+( \w+)?", raw):
        return ""
    return raw[:MAX_LABEL].strip()


class History:
    """Discovery across every CLI whose registry block declares a history."""

    def __init__(self, registry) -> None:
        self.registry = registry
        self._lock = threading.Lock()
        self._cache: list[Conversation] = []
        self._at = 0.0

    def conversations(self, force: bool = False) -> list[Conversation]:
        with self._lock:
            if not force and self._cache and time.time() - self._at < CACHE_SECONDS:
                return self._cache
            found: list[Conversation] = []
            for cli_id, cli in self.registry.types().items():
                if cli.history:
                    found.extend(self._for_cli(cli_id, cli.history))
            found.sort(key=lambda c: c.updated, reverse=True)
            self._cache, self._at = found, time.time()
            return found

    def _for_cli(self, cli_id: str, spec: dict) -> list[Conversation]:
        root = Path(str(spec.get("dir", ""))).expanduser()
        if not root.is_dir():
            return []
        layout = spec.get("layout")
        if layout == "prompt-log":
            return self._from_prompt_log(cli_id, spec, root)
        if layout != "dashed-dir":
            return []
        pattern = str(spec.get("pattern") or "*.jsonl")

        out: list[Conversation] = []
        for project_dir in root.iterdir():
            if not project_dir.is_dir():
                continue
            decoded = _decode_dashed(project_dir.name)
            for transcript in project_dir.glob(pattern):
                try:
                    stat = transcript.stat()
                except OSError:
                    continue
                # An empty or barely-started transcript is not work anyone
                # wants offered back to them.
                if stat.st_size < 512:
                    continue
                label, cwd, branch = _first_prompt(transcript)
                out.append(
                    Conversation(
                        cli=cli_id,
                        cli_session_id=transcript.stem,
                        cwd=cwd or decoded,
                        label=label or Path(cwd or decoded).name or transcript.stem[:8],
                        updated=stat.st_mtime,
                        size=stat.st_size,
                        branch=branch,
                    )
                )
        return out

    def _from_prompt_log(self, cli_id: str, spec: dict, root: Path) -> list[Conversation]:
        """One append-only log per project, a line per prompt. Grok's shape.

        `~/.grok/sessions/%2Froot%2Fventures/prompt_history.jsonl` — the
        directory name is the working directory, percent-encoded, and each line
        carries the session id, the prompt and when it was sent. That is
        everything a resumable conversation needs in one file, which makes this
        cheaper to read than a directory of transcripts.

        The *first* prompt of each session is kept as its label, matching the
        other layout: what you asked for at the start is what the conversation
        is about, and the last thing you said is usually "yes" or "carry on".
        """
        name = str(spec.get("file") or "prompt_history.jsonl")
        out: list[Conversation] = []
        for project_dir in root.iterdir():
            if not project_dir.is_dir():
                continue
            log = project_dir / name
            if not log.is_file():
                continue
            cwd = urllib.parse.unquote(project_dir.name)
            first: dict[str, tuple[str, float]] = {}
            try:
                with log.open(encoding="utf-8", errors="replace") as handle:
                    for line in handle:
                        try:
                            row = json.loads(line)
                        except ValueError:
                            continue
                        sid = str(row.get("session_id") or "")
                        prompt = str(row.get("prompt") or "").strip()
                        if not sid or not prompt or row.get("is_bash"):
                            continue
                        when = _epoch(str(row.get("timestamp") or ""))
                        held = first.get(sid)
                        if held is None:
                            first[sid] = (prompt, when)
                        elif when > held[1]:
                            # Keep the opening prompt, but track the latest
                            # moment: that is when the conversation was last
                            # touched, which is what the sidebar orders by.
                            first[sid] = (held[0], when)
            except OSError:
                continue
            for sid, (prompt, when) in first.items():
                out.append(Conversation(
                    cli=cli_id,
                    cli_session_id=sid,
                    cwd=cwd,
                    label=" ".join(prompt.split())[:120],
                    updated=when,
                    size=0,
                    branch="",
                ))
        return out
