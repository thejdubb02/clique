"""File, artifact, paste, and upload routes for the HTTP request handler."""

from __future__ import annotations

import base64
import binascii
import secrets
import time
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any, Callable

from . import artifacts, files

#: Where a pasted image lands: inside the session's own working directory, so
#: the CLI can read it with a relative path and it travels with the project.
#: The name matches what the tool CLIque replaces used, so existing habits and
#: any .gitignore already covering it keep working.
PASTE_DIR = ".claude-images"

#: Biggest image accepted. A screenshot is well under a megabyte; ten is
#: generous and stops a paste being a memory attack.
MAX_PASTE_BYTES = 10 * 1024 * 1024

#: Leading bytes -> extension. The type comes from the *content*, never from a
#: filename or a Content-Type the browser supplied: this route writes a file
#: into a working directory, so what it is has to be established here.
IMAGE_MAGIC = (
    (b"\x89PNG\r\n\x1a\n", ".png"),
    (b"\xff\xd8\xff", ".jpg"),
    (b"GIF87a", ".gif"),
    (b"GIF89a", ".gif"),
    (b"BM", ".bmp"),
)


#: Containment, defined once. The paste route and the artifact routes are the
#: two places a working directory is touched, and they must agree about what
#: "inside it" means.
_inside = artifacts.inside


#: Extension -> the type it is served as. Beside IMAGE_MAGIC so the two cannot
#: drift: nothing is ever served as a type the magic did not establish.
IMAGE_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
    ".webp": "image/webp",
}


def image_extension(data: bytes) -> str:
    """The extension for these bytes, or "" if they are not an image we know."""
    for magic, ext in IMAGE_MAGIC:
        if data.startswith(magic):
            return ext
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return ""


class FileRoutes(BaseHTTPRequestHandler):
    """File methods mixed into :class:`clique.app.Handler`."""

    # Provided by clique.app.Handler.
    panel: Any
    _json: Callable[..., Any]
    _send: Callable[..., Any]

    def _file(self, session_id: str, asked: str) -> None:
        """Read-only glance at a path the pane printed.

        Relative to the session's working directory, or absolute / ``~/``.
        Text is capped; images are described here and served by `_file_raw`.
        """
        session = self.panel.store.session(session_id)
        if not session:
            return self._json({"error": "no such session"}, 404)
        return self._json(files.inspect(session.cwd, asked))

    def _file_write(self, session_id: str, body: dict) -> None:
        """Save edited text back to a file, behind the write gate.

        The path is resolved and fenced exactly as a read is (`files.write` reuses
        `files.resolve`), so a save cannot escape the session directory or touch a
        credential file, and only ever overwrites an existing regular file. The
        write scope is already enforced by the gate above — an `attention`-only
        hook token, which every pane carries, cannot reach here."""
        session = self.panel.store.session(session_id)
        if not session:
            return self._json({"error": "no such session"}, 404)
        text = body.get("text")
        asked = str(body.get("path") or "")
        if not isinstance(text, str):
            return self._json({"error": "text is required"}, 400)
        try:
            written = files.write(session.cwd, asked, text)
        except ValueError as exc:
            return self._json({"error": str(exc)}, 400)
        except OSError:
            return self._json({"error": "could not save the file"}, 500)
        return self._json({"ok": True, "bytes": written})

    def _file_raw(self, session_id: str, asked: str) -> None:
        """The bytes of an image the pane pointed at."""
        session = self.panel.store.session(session_id)
        if not session:
            return self._json({"error": "no such session"}, 404)
        info = files.inspect(session.cwd, asked)
        if info["kind"] != "image" or not info["path"]:
            return self._json({"error": "not an image"}, 415)
        target = Path(info["path"])
        try:
            if target.stat().st_size > MAX_PASTE_BYTES:
                return self._json({"error": "image too large"}, 413)
            raw = target.read_bytes()
        except OSError:
            return self._json({"error": "could not read it"}, 404)
        kind = IMAGE_TYPES.get(image_extension(raw))
        if not kind:
            return self._json({"error": "not an image this understands"}, 415)
        return self._send(200, raw, kind, {"Cache-Control": "no-store"})

    def _artifacts(self, session_id: str) -> None:
        """What this session's working directory has to show.

        The other half of paste. A terminal cannot draw a picture, so an agent
        that takes a screenshot has, until it is listed here, produced
        something you have to leave the panel to look at.
        """
        session = self.panel.store.session(session_id)
        if not session:
            return self._json({"error": "no such session"}, 404)
        settings = self.panel.store.settings
        if not settings.get("artifacts_show", True):
            return self._json([])
        return self._json(
            artifacts.scan(
                session.cwd,
                settings.get("artifact_dirs") or [],
                # Only what appeared while this session has been running. A
                # directory full of committed images is the project, not the work.
                since=session.created,
            )
        )

    def _artifact(self, session_id: str, relative: str) -> None:
        """Serve one image out of a session's working directory.

        The type comes from the bytes, never from the extension: this reads a
        file a browser asked for by name, and what a name claims about its
        contents is not evidence.
        """
        session = self.panel.store.session(session_id)
        if not session:
            return self._json({"error": "no such session"}, 404)
        target = artifacts.locate(session.cwd, relative)
        if target is None:
            return self._json({"error": "not an artifact of this session"}, 404)
        try:
            if target.stat().st_size > MAX_PASTE_BYTES:
                return self._json({"error": "image too large"}, 413)
            raw = target.read_bytes()
        except OSError:
            return self._json({"error": "could not read it"}, 404)
        kind = IMAGE_TYPES.get(image_extension(raw))
        if not kind:
            return self._json({"error": "not an image this understands"}, 415)
        # No-store, because the file behind a name can be overwritten by the
        # next screenshot and a cached one would be a lie.
        return self._send(200, raw, kind, {"Cache-Control": "no-store"})

    def _paste_image(self, session_id: str, body: dict) -> None:
        """Write a pasted image into the session's directory and say where.

        Sharing a screenshot with an agent is otherwise impossible: a terminal
        has no way to carry an image, so the only thing that can cross is a
        path. The browser has the bytes, the pane has the CLI, and this is the
        bridge — it saves the file and hands back a path the CLI can open.

        Containment is checked *after* resolution, not before, because that is
        the only order that survives a symlinked working directory. The write
        can only ever land inside `<cwd>/.claude-images`.
        """
        session = self.panel.store.session(session_id)
        if not session:
            return self._json({"error": "no such session"}, 404)

        try:
            raw = base64.b64decode(body.get("data") or "", validate=True)
        except (ValueError, binascii.Error):
            return self._json({"error": "not valid base64"}, 400)
        if not raw:
            return self._json({"error": "empty paste"}, 400)
        if len(raw) > MAX_PASTE_BYTES:
            return self._json({"error": "image too large"}, 413)

        ext = image_extension(raw)
        if not ext:
            return self._json({"error": "not an image this understands"}, 415)

        try:
            root = Path(session.cwd).resolve(strict=True)
        except OSError:
            return self._json({"error": "working directory is gone"}, 409)
        if not root.is_dir():
            return self._json({"error": "working directory is gone"}, 409)

        target_dir = (root / PASTE_DIR).resolve()
        if target_dir != root / PASTE_DIR and not _inside(target_dir, root):
            return self._json({"error": "refused: escapes the working directory"}, 403)
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
            target_dir = target_dir.resolve(strict=True)
            if not _inside(target_dir, root):
                return self._json({"error": "refused: escapes the working directory"}, 403)
            name = f"paste-{int(time.time() * 1000)}-{secrets.token_hex(4)}{ext}"
            target = target_dir / name
            # Exclusive create: never overwrite something already there, even
            # if a name somehow collides.
            with open(target, "xb") as fh:
                fh.write(raw)
        except OSError as exc:
            return self._json({"error": f"could not save: {exc}"}, 500)

        return self._json(
            {
                "path": str(target),
                "relative": f"{PASTE_DIR}/{name}",
                "bytes": len(raw),
            },
            201,
        )

    def _upload_file(self, session_id: str, body: dict) -> None:
        """Write a dropped file into the session's directory and say where.

        The drag-and-drop sibling of `_paste_image`: a paste is a nameless image,
        a drop is a named file of any kind — a PDF, a log, a spreadsheet — that
        the agent then opens by the path this hands back. The name is the user's,
        so it is reduced to a safe basename and put through the same credential
        and directory gate a read is (`files.store_upload` reuses the read gate):
        a drop cannot escape the session directory, overwrite an existing file,
        or land a credential name."""
        session = self.panel.store.session(session_id)
        if not session:
            return self._json({"error": "no such session"}, 404)
        try:
            raw = base64.b64decode(body.get("data") or "", validate=True)
        except (ValueError, binascii.Error):
            return self._json({"error": "not valid base64"}, 400)
        if not raw:
            return self._json({"error": "empty file"}, 400)
        if len(raw) > files.UPLOAD_CAP:
            return self._json({"error": "file too large"}, 413)
        try:
            target = files.store_upload(session.cwd, str(body.get("name") or ""), raw)
        except ValueError as exc:
            return self._json({"error": str(exc)}, 400)
        except OSError as exc:
            return self._json({"error": f"could not save: {exc}"}, 500)
        try:
            rel = str(target.relative_to(Path(session.cwd).resolve()))
        except ValueError:
            rel = target.name
        return self._json({"path": str(target), "relative": rel, "bytes": len(raw)}, 201)
