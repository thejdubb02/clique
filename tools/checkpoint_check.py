#!/usr/bin/env python3
"""Verify the checkpoint button, end to end, in a real (headless) browser.

Feature: before you let an agent loose, "Checkpoint" on a session saves the
repo's current HEAD and its uncommitted diff to a file under
`.clique-checkpoints/`, so afterwards you can see — or `git apply -R` — exactly
what changed. This drives the real endpoint through the page (same-origin fetch,
cookie and all) and confirms:

- a git session writes a checkpoint file that names HEAD and carries the diff;
- the recorded diff includes both a tracked edit and an untracked file;
- a non-git directory is refused with 400, and no file is written.

    ~/.cache/clique-visual/bin/python tools/checkpoint_check.py

Its own home, port, tmux socket and work directories. 0 on pass, 1 on fail.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from clique.auth import COOKIE_NAME, Auth

PASSWORD = "checkpoint-check"  # noqa: S105 — a throwaway panel on loopback
PORT = 3294
BASE = f"http://127.0.0.1:{PORT}"
HOME = Path("/tmp/clique-checkpoint-check-home")
SOCKET = "clique-checkpoint-check"
GITWORK = Path("/tmp/clique-checkpoint-check-git")
PLAINWORK = Path("/tmp/clique-checkpoint-check-plain")

# POST the checkpoint endpoint the way the page does — same-origin, cookie sent
# automatically — and hand back the status and parsed body.
_POST_JS = """
async (id) => {
  const r = await fetch(`/api/sessions/${id}/checkpoint`, {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}',
  });
  let body = null;
  try { body = await r.json(); } catch (e) { body = null; }
  return {status: r.status, body};
}
"""


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True)


def _make_repo() -> None:
    shutil.rmtree(GITWORK, ignore_errors=True)
    GITWORK.mkdir(parents=True)
    _git(GITWORK, "init", "-q")
    _git(GITWORK, "config", "user.email", "t@example.com")
    _git(GITWORK, "config", "user.name", "Test")
    (GITWORK / "tracked.txt").write_text("original line\n")
    _git(GITWORK, "add", "-A")
    _git(GITWORK, "commit", "-q", "-m", "first")
    # A tracked edit and a brand-new untracked file — both must show up.
    (GITWORK / "tracked.txt").write_text("original line\nan agent-ish edit\n")
    (GITWORK / "new_file.txt").write_text("freshly created\n")


def _new_session(page, cwd: Path) -> str:
    return page.evaluate(
        """async (cwd) => {
      const r = await fetch('/api/sessions', {method:'POST',
        headers:{'Content-Type':'application/json'},
        body: JSON.stringify({cli:'shell', cwd, name:'w'})});
      const id = (await r.json()).id; await refresh(); return id;
    }""",
        str(cwd),
    )


def _panel() -> subprocess.Popen:
    shutil.rmtree(HOME, ignore_errors=True)
    HOME.mkdir(parents=True)
    env = dict(os.environ, CLIQUE_HOME=str(HOME), CLIQUE_TMUX_SOCKET=SOCKET)
    subprocess.run(
        [sys.executable, "-m", "clique", "password"],
        input=f"{PASSWORD}\n{PASSWORD}\n",
        text=True,
        env=env,
        cwd=str(ROOT),
        capture_output=True,
    )
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "clique",
            "--host",
            "127.0.0.1",
            "--port",
            str(PORT),
            "--state",
            str(HOME / "state.json"),
        ],
        env=env,
        cwd=str(ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for _ in range(80):
        try:
            urllib.request.urlopen(BASE + "/healthz", timeout=2).read()
            return proc
        except (urllib.error.URLError, OSError):
            time.sleep(0.25)
    proc.kill()
    raise SystemExit(f"the check's own panel never came up on {PORT}")


def main() -> int:
    from playwright.sync_api import sync_playwright

    _make_repo()
    shutil.rmtree(PLAINWORK, ignore_errors=True)
    PLAINWORK.mkdir(parents=True)

    proc = _panel()
    res: dict[str, object] = {}
    try:
        auth = Auth(PASSWORD, HOME / "secret")
        with sync_playwright() as play:
            browser = play.chromium.launch()
            ctx = browser.new_context(viewport={"width": 1200, "height": 850})
            ctx.add_cookies(
                [{"name": COOKIE_NAME, "value": auth.issue(), "domain": "127.0.0.1", "path": "/"}]
            )
            page = ctx.new_page()
            errs: list[str] = []
            page.on("pageerror", lambda e: errs.append(str(e)))
            page.on(
                "console",
                lambda m: (
                    errs.append(m.text)
                    if m.type == "error" and "Failed to load resource" not in m.text
                    else None
                ),
            )
            page.goto(BASE, wait_until="networkidle")
            page.wait_for_timeout(600)

            # A git session: checkpoint lands, names HEAD, carries both changes.
            gid = _new_session(page, GITWORK)
            page.wait_for_timeout(400)
            out = page.evaluate(_POST_JS, gid)
            ok = out.get("status") == 201 and isinstance(out.get("body"), dict)
            res["git_status_201"] = ok
            saved = (out.get("body") or {}) if ok else {}
            res["head_reported"] = bool(saved.get("head")) and saved.get("head") != "unborn"
            rel = saved.get("relative") or ""
            res["under_checkpoints"] = rel.startswith(".clique-checkpoints/")
            written = GITWORK / rel if rel else GITWORK / "nope"
            text = written.read_text() if written.is_file() else ""
            res["file_written"] = bool(text)
            res["names_head"] = bool(saved.get("head")) and saved.get("head") in text
            res["has_tracked_edit"] = "an agent-ish edit" in text
            res["has_untracked_file"] = "freshly created" in text and "new_file.txt" in text
            res["shortstat_reported"] = bool(saved.get("shortstat"))

            # A non-git directory: refused, nothing written.
            pid = _new_session(page, PLAINWORK)
            page.wait_for_timeout(300)
            out2 = page.evaluate(_POST_JS, pid)
            res["plain_refused_400"] = out2.get("status") == 400
            res["plain_no_dir"] = not (PLAINWORK / ".clique-checkpoints").exists()

            res["errors"] = errs[:6]
            browser.close()
    finally:
        proc.terminate()
        subprocess.run(
            ["tmux", "-L", SOCKET, "kill-server"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        shutil.rmtree(HOME, ignore_errors=True)
        shutil.rmtree(GITWORK, ignore_errors=True)
        shutil.rmtree(PLAINWORK, ignore_errors=True)

    ok = all(v for k, v in res.items() if k != "errors") and not res.get("errors")
    for key, value in res.items():
        good = (not value) if key == "errors" else bool(value)
        print(f"  {'ok  ' if good else 'FAIL'} {key}: {value}")
    print("checkpoint_check:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
