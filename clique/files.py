"""Read a path a pane pointed at, so a click is not a dead end.

The terminal already turns http(s) into a click. A path like ``docs/foo.md``
was still inert, which is the thing you actually want to copy, drop in a
prompt, or glance at. This is that glance: read-only, capped, no editor.

Relative paths are against the session's working directory. Absolute paths
and ``~/`` are allowed because anyone who can reach the panel already has a
shell as this user — a sandbox here would protect nobody and hide the
ordinary case of an agent printing an absolute path.
"""

from __future__ import annotations

import contextlib
import os
import re
from pathlib import Path

#: How much text the sheet will hold. Bigger than that is still copyable;
#: we just will not dump it into the browser.
TEXT_CAP = 256 * 1024

#: File-preview reads are fenced by default: a read may not escape the session's
#: working directory. A trusted-local deployment can opt out with
#: CLIQUE_FENCE_READS=0, which makes any absolute path an agent prints clickable
#: again — anyone past the auth gate there already has a shell as this user. On
#: by default because the project ships public for strangers to self-host behind
#: a tunnel, where that assumption does not hold.
_FENCE = os.environ.get("CLIQUE_FENCE_READS", "1").strip().lower() not in (
    "0",
    "false",
    "no",
    "off",
)

#: Credential and key material never makes sense to preview through a click, and
#: serving one is the same mistake whether or not the directory fence is on — so
#: this block is enforced *unconditionally*, independent of CLIQUE_FENCE_READS.
#: A blocked read simply looks "missing", the same as any path we will not open.
_BLOCKED_NAMES = frozenset(
    (
        "id_rsa",
        "id_ed25519",
        "id_ecdsa",
        "id_dsa",
        ".netrc",
        ".pgpass",
        ".htpasswd",
        "credentials",
        ".git-credentials",
        ".npmrc",
        ".pypirc",
        ".dockercfg",
        ".terraformrc",
        ".bw-session",
    )
)
#: Private-key and keystore material, matched by extension.
_BLOCKED_EXTS = frozenset((".pem", ".key", ".p12", ".pfx", ".jks", ".keystore"))
#: Whole directories whose every file is a credential.
_BLOCKED_DIRS = frozenset(
    (
        ".ssh",
        ".aws",
        ".gnupg",
        ".gcloud",
        ".kube",
        ".docker",
        ".azure",
        ".terraform",
        ".vault",
    )
)


def _is_credential(target: Path) -> bool:
    """A path that holds secrets and must never be served through a preview.

    Names and key extensions are matched on the *resolved* path, so a symlink
    with an innocent name that points at ``~/.ssh/id_rsa`` is caught both by its
    real name and by the ``.ssh`` in its resolved parents. ``.env`` matches its
    whole family (``.env.local``, ``.env.production``); a template like
    ``.env.example`` is swept up too, which costs only a preview you can still
    open in the shell.
    """
    name = target.name.lower()
    if name in _BLOCKED_NAMES or name.startswith(".env"):
        return True
    if target.suffix.lower() in _BLOCKED_EXTS:
        return True
    return bool({p.lower() for p in target.parts} & _BLOCKED_DIRS)


def _fence(cwd: str, target: Path) -> None:
    """Directory containment: a read may not resolve outside the session's cwd.

    Checked *after* resolution so a symlink cannot smuggle a path out — both
    sides are real paths here. This is the part CLIQUE_FENCE_READS toggles; the
    credential block above is not.
    """
    base = Path(cwd).resolve()
    if target != base and base not in target.parents:
        raise ValueError("outside the session directory")


#: A printed path with a compiler-style suffix: ``src/app.js:42`` or
#: ``src/app.js:42:7``. The file is the part before that.
_LINECOL = re.compile(r":\d+(?::\d+)?$")


def _looks_image(head: bytes) -> bool:
    """Same families the artifact route serves, decided from bytes not names."""
    if head.startswith(b"\x89PNG") or head.startswith(b"\xff\xd8\xff"):
        return True
    if head.startswith(b"GIF87a") or head.startswith(b"GIF89a") or head.startswith(b"BM"):
        return True
    return head[:4] == b"RIFF" and head[8:12] == b"WEBP"


def clean(raw: str) -> str:
    """The path a click meant, minus the line number a CLI stuck on the end."""
    text = str(raw or "").strip().replace("\x00", "")[:1024]
    text = _LINECOL.sub("", text)
    return text.rstrip(".,;:!?)\"'")


def resolve(cwd: str, raw: str) -> Path:
    """Turn what was printed into an absolute path.

    ``~/`` follows the user that started the panel, not a username. Relative
    is against ``cwd``. ``..`` is resolved, not refused — the shell would
    too.
    """
    text = clean(raw)
    if not text:
        raise ValueError("empty path")
    if text.startswith("~/"):
        target = (Path.home() / text[2:]).resolve()
    else:
        path = Path(text)
        target = path.resolve() if path.is_absolute() else (Path(cwd) / path).resolve()
    if _is_credential(target):
        raise ValueError("blocked credential file")
    if _FENCE:
        _fence(cwd, target)
    return target


#: The most an in-browser edit may write back. Generous next to the 256 KB read
#: cap — the editor only ever opens a file that read whole, never a truncated one
#: — but bounded, because this is what turns a text box into a write to disk.
EDIT_CAP = 2 * 1024 * 1024


def write(cwd: str, raw: str, text: str) -> int:
    """Save edited text back to a path the pane pointed at.

    The gate is a read's gate, reused whole: ``resolve`` refuses a credential
    file and — with the fence on — anything that resolves outside the session's
    directory, symlinks followed. On top of that a save only ever *overwrites an
    existing regular file*: it will not create one, and it will not touch a
    directory or a device. Written atomically (temp then rename) so a failure
    mid-write cannot leave a half-file, with the file's own mode preserved.
    """
    target = resolve(cwd, raw)  # raises ValueError on a credential / an escape
    data = text.encode("utf-8")
    if len(data) > EDIT_CAP:
        raise ValueError("too large to save from here")
    if not target.is_file():
        raise ValueError("not an editable file")
    mode = target.stat().st_mode & 0o777
    tmp = target.with_name(target.name + ".clique-save")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, target)
    except OSError:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise
    return len(data)


def inspect(cwd: str, raw: str) -> dict:
    """What the sheet needs: kind, size, and text when it is safe to show."""
    asked = clean(raw)
    out = {
        "asked": asked,
        "path": "",
        "name": Path(asked).name if asked else "",
        "kind": "missing",
        "size": 0,
        "text": "",
        "truncated": False,
    }
    if not asked:
        return out
    try:
        target = resolve(cwd, asked)
    except (OSError, RuntimeError, ValueError):
        return out
    out["path"] = str(target)
    out["name"] = target.name
    try:
        if target.is_dir():
            out["kind"] = "dir"
            out["size"] = 0
            return out
        if not target.is_file():
            return out
        size = target.stat().st_size
        out["size"] = size
        # Read only what is shown, not the whole file: read_bytes() on a
        # 30 GB build artifact would load it all into RAM before slicing.
        with target.open("rb") as fh:
            head = fh.read(TEXT_CAP + 1)
    except OSError:
        return out

    if _looks_image(head):
        out["kind"] = "image"
        return out
    if b"\x00" in head[:1024]:
        out["kind"] = "binary"
        return out
    out["kind"] = "text"
    sample = head[:TEXT_CAP]
    out["truncated"] = size > TEXT_CAP
    out["text"] = sample.decode("utf-8", errors="replace")
    return out
