"""End-to-end check of the session engine against a real tmux server.

Runs on a throwaway socket so it can never see, touch or kill a live session.
Deliberately not mocked: the failure modes worth catching here (quoting, pane
history limits, send-keys interpreting a prompt as key names) only exist when
tmux is actually running.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from clique import app as app_mod
from clique import (
    attention,
    files,
    gitinfo,
    notify,
    services,
    sysinfo,
    termstrip,
    tmux,
    usage,
    working,
)
from clique.__main__ import config_path
from clique.registry import Registry, RegistryError

SOCKET = "clique-smoke"
ROOT = Path(__file__).resolve().parents[1]

passed = failed = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global passed, failed
    if cond:
        passed += 1
        print(f"  ok   {label}")
    else:
        failed += 1
        print(f"  FAIL {label} {detail}")


def check_mcp() -> None:
    """The read-only MCP server, against a throwaway panel.

    Same shape as ``tools/smoke_http.py``: own port, own home, own tmux
    socket, so this cannot see or kill a live session. Speaks JSON-RPC on
    the server's stdin, which is what an MCP client actually does.
    """
    import contextlib
    import select
    import socket
    import urllib.error
    import urllib.request

    print("mcp")
    home = Path("/tmp/clique-mcp-smoke-home")
    socket_name = "clique-mcp-smoke"
    # Throwaway panel on loopback. Gone before this function returns.
    password = "mcp-smoke-check"  # noqa: S105
    env = dict(os.environ, CLIQUE_HOME=str(home), CLIQUE_TMUX_SOCKET=socket_name)
    env.pop("CLIQUE_TOKEN", None)
    env.pop("CLIQUE_URL", None)

    try:
        missing = subprocess.run(
            [sys.executable, "-m", "clique", "mcp"],
            cwd=str(ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except subprocess.TimeoutExpired:
        missing = None
        check("mcp refuses to start without CLIQUE_TOKEN", False, "hung on stdin")
    if missing is not None:
        check(
            "mcp refuses to start without CLIQUE_TOKEN",
            missing.returncode == 1,
            missing.returncode,
        )
        check(
            "the error names how to create a token",
            "python3 -m clique token create" in missing.stderr,
            missing.stderr.strip()[:200],
        )
        check(
            "and that error does not contain a token",
            "mxp_" not in missing.stderr and "mxp_" not in missing.stdout,
        )

    # The panel clamps peek to 40, so a client asking for a megabyte would
    # still look fine on the wire. The cap has to be checked here, or a
    # regression in the wrapper cannot fail this test.
    from clique.mcp_server import ToolError, _lines, _timeout

    check("peek lines default to 8", _lines({}) == 8)
    check("peek lines cap at 200", _lines({"lines": 10000}) == 200)
    try:
        _lines({"lines": 0})
        rejected = False
    except ToolError:
        rejected = True
    check("peek lines reject 0", rejected)
    check("omitted timeout is left to the route", _timeout({}) is None)

    # Read-only is true today by inspection, not by anything that would fail
    # if it stopped being true. This is that check: one Client.get(), method
    # always GET, no write verb anywhere in the file.
    src = (ROOT / "clique" / "mcp_server.py").read_text()
    check("exactly one call site makes an HTTP request", src.count("urllib.request.Request(") == 1)
    check("that call is hardcoded GET", 'method="GET"' in src)
    check(
        "no write HTTP verb appears anywhere in the file",
        not any(verb in src for verb in ('"POST"', '"PUT"', '"DELETE"', '"PATCH"')),
    )
    check("timeout caps at 300", _timeout({"timeout": 9000}) == 300)

    shutil.rmtree(home, ignore_errors=True)
    home.mkdir(parents=True)
    tmux._run(["kill-server"], socket_name, check=False)
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    base = f"http://127.0.0.1:{port}"

    panel = None
    mcp = None
    token = ""
    err_fh = (home / "panel.err").open("w")
    try:
        panel = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "clique",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--password",
                password,
                "--state",
                str(home / "state.json"),
            ],
            cwd=str(ROOT),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=err_fh,
        )
        up = False
        for _ in range(80):
            if panel.poll() is not None:
                break
            try:
                urllib.request.urlopen(base + "/healthz", timeout=2).read()
                up = True
                break
            except (urllib.error.URLError, OSError):
                time.sleep(0.25)
        if not up:
            err_fh.flush()
            tail = (home / "panel.err").read_text(encoding="utf-8", errors="replace")[-200:]
            check("throwaway panel is up", False, tail.strip())
            return
        check("throwaway panel is up", True)

        minted = subprocess.run(
            [sys.executable, "-m", "clique", "token", "create", "mcp-smoke", "--read-only"],
            cwd=str(ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
        )
        for line in minted.stdout.splitlines():
            if line.strip().startswith("mxp_"):
                token = line.strip()
        check(
            "mcp smoke token minted",
            token.startswith("mxp_"),
            minted.stderr.strip()[:200],
        )
        if not token:
            return

        mcp = subprocess.Popen(
            [sys.executable, "-m", "clique", "mcp"],
            cwd=str(ROOT),
            env={**env, "CLIQUE_URL": base, "CLIQUE_TOKEN": token, "PYTHONUNBUFFERED": "1"},
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        pending = bytearray()

        def rpc(msg: dict, timeout: float = 10) -> dict:
            assert mcp is not None and mcp.stdin is not None and mcp.stdout is not None
            mcp.stdin.write((json.dumps(msg) + "\n").encode())
            mcp.stdin.flush()
            fd = mcp.stdout.fileno()
            os.set_blocking(fd, False)
            deadline = time.time() + timeout
            while b"\n" not in pending:
                if time.time() > deadline:
                    raise TimeoutError("mcp server did not answer")
                ready, _, _ = select.select([fd], [], [], 0.2)
                if not ready:
                    if mcp.poll() is not None:
                        raise RuntimeError(f"mcp exited {mcp.returncode}")
                    continue
                try:
                    chunk = os.read(fd, 65536)
                except BlockingIOError:
                    continue
                if not chunk:
                    raise RuntimeError("mcp closed stdout")
                pending.extend(chunk)
            line, _, rest = bytes(pending).partition(b"\n")
            pending.clear()
            pending.extend(rest)
            return json.loads(line)

        init = rpc(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "smoke", "version": "0"},
                },
            }
        )
        agreed = (init.get("result") or {}).get("protocolVersion")
        check(
            "initialize answers", init.get("id") == 1 and agreed == "2025-11-25", init.get("error")
        )

        listed = rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        tools = (listed.get("result") or {}).get("tools") or []
        names = [tool.get("name") for tool in tools]
        check(
            "tools/list has the five read-only tools",
            names
            == [
                "list_sessions",
                "get_session",
                "wait",
                "preview_pane",
                "conversation",
            ],
            names,
        )
        check(
            "each tool declares an object input schema",
            all(
                isinstance(tool.get("inputSchema"), dict)
                and tool["inputSchema"].get("type") == "object"
                for tool in tools
            ),
            names,
        )

        called = rpc(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "list_sessions", "arguments": {}},
            }
        )
        result = called.get("result") or {}
        content = result.get("content") or []
        text = content[0].get("text") if content and isinstance(content[0], dict) else ""
        try:
            parsed = json.loads(text) if isinstance(text, str) else None
        except json.JSONDecodeError:
            parsed = None
        check(
            "list_sessions returns the session list",
            result.get("isError") is False and isinstance(parsed, list),
            (text or "")[:200],
        )

        missing_pane = rpc(
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {
                    "name": "preview_pane",
                    "arguments": {"id": "no-such-session", "lines": 8},
                },
            }
        )
        bad = missing_pane.get("result") or {}
        bad_content = bad.get("content") or []
        bad_text = bad_content[0].get("text") if bad_content else ""
        check(
            "a missing session is a tool error, not an empty pane",
            bad.get("isError") is True and "404" in (bad_text or ""),
            (bad_text or "")[:200],
        )
    except Exception as exc:  # noqa: BLE001 — a hung server must fail the check, not the file
        check("mcp dialogue", False, f"{type(exc).__name__}: {exc}")
    finally:
        err_text = ""
        if mcp is not None:
            if mcp.stdin is not None:
                with contextlib.suppress(OSError):
                    mcp.stdin.close()
            try:
                mcp.wait(timeout=5)
            except subprocess.TimeoutExpired:
                mcp.terminate()
                try:
                    mcp.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    mcp.kill()
                    mcp.wait(timeout=5)
            if mcp.stderr is not None:
                err_text = mcp.stderr.read().decode("utf-8", "replace")
        if mcp is not None and token:
            check("mcp stderr never echoes the token", token not in err_text)
        if panel is not None and panel.poll() is None:
            panel.terminate()
            try:
                panel.wait(timeout=10)
            except subprocess.TimeoutExpired:
                panel.kill()
                panel.wait(timeout=5)
        err_fh.close()
        tmux._run(["kill-server"], socket_name, check=False)
        shutil.rmtree(home, ignore_errors=True)


def main() -> int:
    if not tmux.available():
        print("tmux not installed — cannot run engine smoke test")
        return 1

    tmux._run(["kill-server"], SOCKET, check=False)

    print("isolation")
    # A test that shares the live socket or the live state file is how a
    # /tmp shell lands on the tab someone is looking at. These two are the
    # whole wall; if either fails, the rest of the suite is not safe to run.
    stripped = {k: v for k, v in os.environ.items() if k != "CLIQUE_TMUX_SOCKET"}
    default_socket = subprocess.run(
        [sys.executable, "-c", "from clique import tmux; print(tmux.SOCKET)"],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        env=stripped,
    )
    check(
        "the engine defaults to its own socket",
        default_socket.stdout.strip() == "clique",
        default_socket.stdout,
    )
    named = subprocess.run(
        [sys.executable, "-c", "from clique import tmux; print(tmux.SOCKET)"],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        env={**os.environ, "CLIQUE_TMUX_SOCKET": "clique-env-check"},
    )
    check(
        "CLIQUE_TMUX_SOCKET is how a test gets a different one",
        named.stdout.strip() == "clique-env-check",
        named.stdout,
    )
    sandbox = "/tmp/clique-state-home-test"
    state_out = subprocess.run(
        [
            sys.executable,
            "-c",
            "from clique.__main__ import default_state_path; print(default_state_path())",
        ],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        env={**os.environ, "CLIQUE_HOME": sandbox},
    )
    check(
        "a test home does not inherit the live state file",
        state_out.stdout.strip() == sandbox + "/state.json",
        state_out.stdout.strip() or state_out.stderr[-200:],
    )

    print("boxed CLIs do not steal the mouse from the browser")
    filt = termstrip.boxed_stream()
    check("plain text is untouched", filt.feed(b"hello") == b"hello")
    check("mouse tracking on is hidden", filt.feed(b"\x1b[?1000h\x1b[?1006hhi") == b"hi")
    check("mouse tracking off is hidden too", filt.feed(b"\x1b[?1000lbye") == b"bye")
    check(
        "bracketed paste stays when mixed with mouse",
        filt.feed(b"\x1b[?1000;2004h") == b"\x1b[?2004h",
    )
    check("colour is not a mouse code", filt.feed(b"\x1b[31mred") == b"\x1b[31mred")
    split = termstrip.boxed_stream()
    check("a split sequence is held", split.feed(b"\x1b[?100") == b"")
    check("and dropped once it completes", split.feed(b"0hOK") == b"OK")
    check("the alt screen switch is hidden", filt.feed(b"\x1b[?1049hview") == b"view")
    check("wiping scrollback is hidden", filt.feed(b"\x1b[3Jkeep") == b"keep")
    check("a visible clear still happens", filt.feed(b"\x1b[2J") == b"\x1b[2J")
    passthrough = termstrip.StreamFilter()
    check("a shell keeps mouse tracking", passthrough.feed(b"\x1b[?1000h") == b"\x1b[?1000h")

    print("registry")
    # The same file the app resolves to, asked for the same way. Naming the
    # path here by hand is what let it rot when the catalogue moved into the
    # package: the suite went on passing against a stale copy left on disk
    # while CI, which has no stale copy, failed.
    reg = Registry(config_path(None))
    types = reg.types()
    check("loads clis.toml", set(types) >= {"claude", "grok", "shell"}, sorted(types))
    check("mode pill on for claude", types["claude"].has_modes)
    check("mode pill off for grok", not types["grok"].has_modes)
    argv = reg.launch_argv("shell", session_id="a" * 32, name="smoke", cwd="/tmp")
    # argv[0] is the *resolved* path, not the bare name: a CLI installed
    # outside the service's PATH still has to launch. This is what grok needed.
    check(
        "renders argv with a resolved binary",
        argv[0].endswith("/bash") and Path(argv[0]).is_file(),
        argv,
    )
    check("detects an installed CLI", types["shell"].installed)
    check(
        "reports a missing CLI as absent",
        not any(c.installed for c in types.values() if c.command == "definitely-not-here"),
        "",
    )

    from clique.registry import parse as parse_registry

    absent = parse_registry({"cli": {"nope": {"command": "definitely-not-here-9x"}}})["nope"]
    check(
        "resolve() returns None for a binary that is not here",
        absent.resolve() is None and not absent.installed,
    )

    print("files")
    # A click on a printed path is a filesystem read, so these are real files
    # in a throwaway directory, not mocks.
    import tempfile

    png = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00"
        b"\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc``"
        b"\x00\x00\x00\x04\x00\x01\xf6\x178U\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    tmp = Path(tempfile.mkdtemp(prefix="clique-files-"))
    (tmp / "note.md").write_text("hello\n", encoding="utf-8")
    (tmp / "shot.png").write_bytes(png)
    (tmp / "bin.dat").write_bytes(b"\x00\x01\x02")
    (tmp / "sub").mkdir()
    (tmp / "sub" / "child.md").write_text("nested\n", encoding="utf-8")
    (tmp / "big.txt").write_bytes(b"x" * (files.TEXT_CAP + 8))
    check("strips a compiler suffix", files.clean("src/app.js:42:7") == "src/app.js")
    check("strips trailing punctuation", files.clean("docs/foo.md.") == "docs/foo.md")
    check("a lone dot is this folder", files.clean(".") == ".")
    check("and so is parent", files.clean("..") == "..")
    text = files.inspect(str(tmp), "note.md")
    check("reads a relative text file", text["kind"] == "text" and text["text"] == "hello\n", text)
    check(
        "and a :line suffix still finds it", files.inspect(str(tmp), "note.md:12")["kind"] == "text"
    )
    check(
        "an image is an image from its bytes",
        files.inspect(str(tmp), "shot.png")["kind"] == "image",
    )
    check(
        "a nul in the first block is binary", files.inspect(str(tmp), "bin.dat")["kind"] == "binary"
    )
    listing = files.inspect(str(tmp), "sub")
    check("a directory is a directory", listing["kind"] == "dir")
    names = [row["name"] for row in listing.get("entries") or []]
    check(
        "and it lists what is inside",
        "child.md" in names and ".." in names,
        names,
    )
    check(
        "listed paths stay inside the folder",
        all(
            row["path"].startswith(str(tmp / "sub")) or row["name"] == ".."
            for row in listing.get("entries") or []
        ),
        listing.get("entries"),
    )
    top = files.inspect(str(tmp), ".")
    top_names = [row["name"] for row in top.get("entries") or []]
    check("the session folder listing has no parent climb", ".." not in top_names, top_names)
    check("and it still names a child", "note.md" in top_names, top_names)
    check(
        "missing stays missing, not an error",
        files.inspect(str(tmp), "nope.md")["kind"] == "missing",
    )
    # Reads are fenced to the session directory by default now, so climbing out
    # with `..` is refused. Opting out (CLIQUE_FENCE_READS=0) restores the old
    # trusted-local behaviour where the path simply resolves.
    climbed = files.inspect(str(tmp / "sub"), "../note.md")
    check(
        ".. outside the session dir is refused by the default fence",
        climbed["kind"] == "missing",
        climbed,
    )
    outside_abs = files.inspect(str(tmp), "/etc/hostname")
    check(
        "an absolute path outside the session dir is refused",
        outside_abs["kind"] == "missing" and not outside_abs.get("entries"),
        outside_abs,
    )
    # Credential and key material is refused even inside the session dir, and by
    # its whole family / key extensions — .env.local and a .pem, not just .env.
    for secret in (".env", ".env.local", "server.pem", ".bw-session", ".npmrc"):
        (tmp / secret).write_text("SECRET=1\n", encoding="utf-8")
        check(
            f"a credential file is refused: {secret}",
            files.inspect(str(tmp), secret)["kind"] == "missing",
            files.inspect(str(tmp), secret),
        )
    # Realpath containment: a symlink inside the dir that resolves outside it is
    # refused, because resolution happens before the containment check.
    outside = Path(tempfile.mkdtemp(prefix="clique-outside-"))
    (outside / "secret.txt").write_text("out\n", encoding="utf-8")
    try:
        (tmp / "escape").symlink_to(outside / "secret.txt")
        check(
            "a symlink resolving outside the session dir is refused",
            files.inspect(str(tmp), "escape")["kind"] == "missing",
            files.inspect(str(tmp), "escape"),
        )
    except OSError:
        pass  # a filesystem without symlinks — skip rather than fail
    finally:
        shutil.rmtree(outside, ignore_errors=True)
    _saved_fence = files._FENCE
    try:
        files._FENCE = False
        opened = files.inspect(str(tmp / "sub"), "../note.md")
        check(
            ".. resolves again when the fence is off",
            opened["kind"] == "text" and opened["text"] == "hello\n",
            opened,
        )
        # ...but the credential block is not the fence: it holds regardless.
        check(
            "a credential is still refused with the fence off",
            files.inspect(str(tmp), ".env")["kind"] == "missing",
        )
    finally:
        files._FENCE = _saved_fence
    big = files.inspect(str(tmp), "big.txt")
    check(
        "caps the text it will dump in a browser",
        big["truncated"] and len(big["text"]) == files.TEXT_CAP,
        big["size"],
    )

    # files.write: the same gate as a read, plus overwrite-existing-only.
    (tmp / "note.md").chmod(0o640)
    n = files.write(str(tmp), "note.md", "edited\n")
    check(
        "saves edited text back to an existing file",
        n == 7 and (tmp / "note.md").read_text() == "edited\n",
    )
    check("preserves the file's mode on save", ((tmp / "note.md").stat().st_mode & 0o777) == 0o640)

    def _refused(reason, *args):
        try:
            files.write(*args)
        except (ValueError, OSError):
            return True
        return False

    check("save refuses a credential file", _refused("cred", str(tmp), ".env", "x"))
    check(
        "save refuses outside the session dir",
        _refused("escape", str(tmp / "sub"), "../note.md", "x"),
    )
    check("save will not create a new file", _refused("new", str(tmp), "brand-new.md", "x"))
    check("save refuses a directory", _refused("dir", str(tmp), "sub", "x"))
    outside2 = Path(tempfile.mkdtemp(prefix="clique-outside2-"))
    (outside2 / "keep.txt").write_text("out\n", encoding="utf-8")
    try:
        (tmp / "esc").symlink_to(outside2 / "keep.txt")
        check(
            "save refuses a symlink that resolves out of the dir",
            _refused("symesc", str(tmp), "esc", "hacked"),
        )
        check("and the symlink target is untouched", (outside2 / "keep.txt").read_text() == "out\n")
    except OSError:
        pass  # no symlinks on this fs — skip
    finally:
        shutil.rmtree(outside2, ignore_errors=True)
    shutil.rmtree(tmp, ignore_errors=True)

    print("tokens")
    from clique.tokens import TokenStore

    tok_dir = Path(tempfile.mkdtemp(prefix="clique-tokens-"))
    store = TokenStore(tok_dir / "tokens.json")
    op, _ = store.create("an-operator-token", ["read", "write"])
    bound, _ = store.create("hook:abc", ["attention"], session="sess-1")
    check("a bound token records its session", bound.session == "sess-1")
    check("an operator token has no session", op.session == "")
    check(
        "per-session tokens are hidden from the operator listing",
        [t["id"] for t in store.listing()] == [op.id],
    )
    check(
        "revoke_session drops the bound token and reports it",
        store.revoke_session("sess-1") == 1 and all(t.session != "sess-1" for t in store.tokens),
    )
    check(
        "revoke_session on an unknown session drops nothing",
        store.revoke_session("nope") == 0 and len(store.tokens) == 1,
    )
    shutil.rmtree(tok_dir, ignore_errors=True)

    print("pairing")
    from clique.auth import LOGIN_PAGE, login_page
    from clique.pairing import Desk, grouped

    blank = login_page()
    check(
        "the password page is byte-for-byte what it was",
        blank == LOGIN_PAGE.replace("__ERROR__", "").replace("__NONCE__", "").encode(),
    )
    errored = login_page("Wrong password.", "nonce-1")
    check(
        "an error still only fills the existing slots",
        errored
        == LOGIN_PAGE.replace("__ERROR__", '<p class="err">Wrong password.</p>')
        .replace("__NONCE__", "nonce-1")
        .encode(),
    )
    paired = login_page(pair="K7PM-3XQF", nonce="abc").decode()
    check(
        "a pair page posts the code, not a password",
        'name="pair"' in paired
        and 'value="K7PM-3XQF"' in paired
        and 'name="password"' not in paired,
    )
    check(
        "the button stays a real visible control",
        "Sign in on this device" in paired and "display:none" not in paired,
        paired[paired.find("<button") : paired.find("</button>") + 9]
        if "<button" in paired
        else "",
    )
    check(
        "and a password fallback is offered",
        'href="./"' in paired and "Sign in with a password" in paired,
    )
    check(
        "the auto-submit script carries the page nonce",
        'nonce="abc"' in paired and "document.forms[0].submit()" in paired,
    )
    injected = login_page(pair='"><script>alert(1)</script>', nonce="n").decode()
    check(
        "a pair from the query is escaped into the HTML",
        "<script>alert(1)</script>" not in injected and "&quot;" in injected,
    )

    desk = Desk()
    code, ttl = desk.start()
    check("a minted code lasts two minutes", ttl == 120 and len(code) == 8, (ttl, code))
    check("the login path redeems a grouped lowercase code", desk.redeem(grouped(code).lower()))
    check("and the same code will not redeem twice", not desk.redeem(code))

    desk.start()
    check("a wrong code does not redeem", not desk.redeem("ZZZZZZZZ"))

    expired, _ = desk.start()
    desk._open.expires = time.time() - 1
    check("an expired code does not redeem", not desk.redeem(expired))

    # The Android claim path is Desk.redeem then tokens.create. Hitting the
    # real method, not a copy of it, is what keeps a refactor from changing
    # the contract this feature is required not to touch.
    from clique.app import Panel

    claim_dir = Path(tempfile.mkdtemp(prefix="clique-pair-claim-"))
    claim_tokens = TokenStore(claim_dir / "tokens.json")

    class _ClaimPanel:
        def __init__(self) -> None:
            self.pairing = Desk()
            self.tokens = claim_tokens

        pair_claim = Panel.pair_claim

    phone = _ClaimPanel()
    live, _ = phone.pairing.start()
    claimed = phone.pair_claim({"code": grouped(live).lower(), "name": "Justin's Pixel"})
    check(
        "Android claim still returns a named token",
        bool(claimed)
        and str(claimed.get("token") or "").startswith("mxp_")
        and claimed.get("name") == "Justin's Pixel",
        claimed,
    )
    check("and a second claim is refused", phone.pair_claim({"code": live}) is None)
    shutil.rmtree(claim_dir, ignore_errors=True)

    print("gitinfo")
    import tempfile

    gitinfo.clear()
    plain = Path(tempfile.mkdtemp(prefix="clique-git-plain-"))
    check(
        "a directory that is not a repo says nothing",
        gitinfo.probe(str(plain))["branch"] == "" and gitinfo.probe(str(plain))["dirty"] == 0,
    )
    repo = Path(tempfile.mkdtemp(prefix="clique-git-repo-"))
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "symbolic-ref", "HEAD", "refs/heads/visual"],
        check=True,
        capture_output=True,
    )
    info = gitinfo.probe(str(repo))
    check("an empty repo still has a branch", info["branch"] == "visual", info)
    check("and is clean", info["dirty"] == 0, info)
    (repo / "a.txt").write_text("x\n", encoding="utf-8")
    info = gitinfo.probe(str(repo))
    check("an untracked file is dirty", info["dirty"] == 1, info)
    gitinfo.clear()
    started = time.time()
    gitinfo.of(str(repo))
    check("the sidebar read returns without waiting on git", time.time() - started < 0.25)
    got = {"branch": "", "dirty": 0}
    deadline = time.time() + 3
    while time.time() < deadline:
        got = gitinfo.of(str(repo))
        if got["branch"] == "visual":
            break
        time.sleep(0.05)
    check("and the next read has the branch", got["branch"] == "visual" and got["dirty"] == 1, got)
    shutil.rmtree(plain, ignore_errors=True)
    shutil.rmtree(repo, ignore_errors=True)

    print("worktree setup hook")
    primary = Path(tempfile.mkdtemp(prefix="clique-wt-primary-"))
    wt = Path(tempfile.mkdtemp(prefix="clique-wt-worktree-"))
    check(
        "no .clique-setup or .clique-copy means nothing to run",
        gitinfo.worktree_setup(str(primary), str(wt)) is None,
    )

    (wt / ".clique-setup").write_text("#!/bin/sh\necho hi\n", encoding="utf-8")
    check(
        "a .clique-setup that is not executable is not run",
        gitinfo.worktree_setup(str(primary), str(wt)) is None,
    )
    (wt / ".clique-setup").chmod(0o755)
    sh = gitinfo.worktree_setup(str(primary), str(wt))
    check(
        "an executable .clique-setup is called and its failure does not stop the CLI",
        sh is not None and "./.clique-setup" in sh and "continuing anyway" in sh,
        sh,
    )

    (primary / ".env").write_text("SECRET=1\n", encoding="utf-8")
    (wt / ".clique-copy").write_text(
        "# comment\n.env\nmissing.txt\n../../../etc/passwd\n/etc/passwd\n\n", encoding="utf-8"
    )
    sh = gitinfo.worktree_setup(str(primary), str(wt))
    check(
        "a real name in .clique-copy is carried over",
        sh is not None and "cp -p" in sh and ".env" in sh,
        sh,
    )
    check(
        "a path leaving the checkout is reported, not copied",
        sh is not None and "skipped ../../../etc/passwd" in sh and "skipped /etc/passwd" in sh,
        sh,
    )
    passwd_clauses = [c for c in (sh or "").split("; ") if "passwd" in c]
    check(
        "and neither traversal line reaches a cp",
        len(passwd_clauses) == 2 and all("cp" not in c for c in passwd_clauses),
        passwd_clauses,
    )
    shutil.rmtree(primary, ignore_errors=True)
    shutil.rmtree(wt, ignore_errors=True)

    print("working groups")
    from clique.store import Group, _clean_members

    # A member is a snapshot, not just an id. That is what lets a group whose
    # session was deleted offer it back instead of quietly being one short.
    kept = _clean_members(
        [
            {"session": "a", "cli": "claude", "cwd": "/srv/x", "name": "Dash"},
            {"session": "a", "cli": "grok", "cwd": "/srv/y", "name": "dupe"},
            {"session": "", "cli": "grok"},
            {"session": "b", "extra": "dropped", "cli": "grok", "cwd": "/srv/z", "name": "B"},
            "not a dict",
        ]
    )
    check(
        "a member keeps what it takes to rebuild it",
        kept[0] == {"session": "a", "cli": "claude", "cwd": "/srv/x", "name": "Dash"},
        kept,
    )
    check("the same session cannot be added twice", len(kept) == 2, kept)
    check("a member with no session is dropped", all(m["session"] for m in kept), kept)
    check(
        "and nothing else a caller sent is stored",
        all(set(m) == {"session", "cli", "cwd", "name"} for m in kept),
        kept,
    )
    check("a member that is not even a dict is ignored", len(kept) == 2, kept)
    wide = _clean_members([{"session": f"s{i}"} for i in range(40)])
    check("a group you could not see at a glance is capped", len(wide) == 24, len(wide))

    group = Group(id="g-1", name="Morning")
    check("a group starts empty and coloured", group.members == [] and group.color)

    print("who owns the shared tmux window")
    # A tmux window has one size and every attached client sees it, so two
    # panels of different shapes cannot both be right. This used to be settled
    # by document.hasFocus(), which is per browser window: a desktop on one
    # machine and a phone in your hand both report true, so both claimed it
    # every poll and the CLI reflowed between 162 and 42 columns forever.
    from clique.app import Handler, _desktop_size, _handheld, _may_size_window

    _handheld.clear()
    _desktop_size.clear()
    check(
        "with no phone about, a desktop sizes the window",
        _may_size_window("sm-test", False) is True,
    )
    check("a phone always may", _may_size_window("sm-test", True) is True)
    check("and once it has, the desktop may not", _may_size_window("sm-test", False) is False)
    check(
        "the phone still may, repeatedly",
        _may_size_window("sm-test", True) and _may_size_window("sm-test", True),
    )
    check("another session is unaffected", _may_size_window("sm-other", False) is True)

    # Releasing is the ordinary way out: a phone going into a pocket should
    # not lock a desktop out until a timer expires.
    _handheld.pop("sm-test", None)
    check(
        "after the phone lets go, the desktop may again", _may_size_window("sm-test", False) is True
    )

    # The backstop, for a phone that vanishes without saying so.
    _handheld.clear()
    _may_size_window("sm-test", True)
    _handheld["sm-test"] = time.time() - (app_mod.HANDHELD_HOLD + 1)
    check("a claim older than the hold has expired", _may_size_window("sm-test", False) is True)
    check("and the stale entry is pruned", "sm-test" not in _handheld, dict(_handheld))
    _handheld.clear()

    # When the phone lets go, the server puts the window back. A desktop
    # panel that is merely open will not reclaim (recentlyUsed is 45s), so
    # without this it sits in tmux's dot-fill at the phone's size.
    resizes: list[tuple] = []
    real_resize = app_mod.tmux.resize_window

    def capture_resize(mux, cols, rows, socket=None):
        resizes.append((mux, int(cols), int(rows)))

    app_mod.tmux.resize_window = capture_resize
    try:
        session = SimpleNamespace(mux="sm-restore", socket=SOCKET)
        bridge = SimpleNamespace(resize=lambda *a, **k: None)
        handler = object.__new__(Handler)

        def control(payload: bytes) -> None:
            handler._control(session, bridge, payload, True)

        control(b'{"type":"resize","cols":53,"rows":20,"handheld":true}')
        check("a phone resize is applied", resizes[-1] == ("sm-restore", 53, 20), resizes)

        before = list(resizes)
        control(b'{"type":"resize","cols":235,"rows":60,"handheld":false}')
        check("a desktop resize while held is not applied", resizes == before, resizes)
        remembered = _desktop_size.get("sm-restore")
        check(
            "but that size is remembered",
            remembered is not None and remembered[:2] == (235, 60),
            dict(_desktop_size),
        )

        resizes.clear()
        control(b'{"type":"release"}')
        check(
            "release restores the remembered desktop size",
            resizes == [("sm-restore", 235, 60)],
            resizes,
        )
        check("and the phone's hold is gone", "sm-restore" not in _handheld, dict(_handheld))

        _handheld.clear()
        _desktop_size.clear()
        resizes.clear()
        crashed = False
        try:
            control(b'{"type":"release"}')
        except Exception as exc:  # noqa: BLE001 — the check is that nothing raises
            crashed = True
            detail = repr(exc)
        else:
            detail = ""
        check("release with no remembered size does not crash", not crashed, detail)
        check("and does not resize", resizes == [], resizes)

        # Same prune as the hold: a stale remembered size goes, unless a
        # phone still holds that mux (that size is what release restores).
        _handheld.clear()
        _desktop_size.clear()
        _desktop_size["sm-restore"] = (235, 60, time.time() - (app_mod.HANDHELD_HOLD + 1))
        _may_size_window("sm-restore", False)
        check(
            "a stale remembered size is pruned",
            "sm-restore" not in _desktop_size,
            dict(_desktop_size),
        )

        _may_size_window("sm-restore", True)
        _desktop_size["sm-restore"] = (235, 60, time.time() - (app_mod.HANDHELD_HOLD + 1))
        _may_size_window("sm-restore", False)
        check(
            "a remembered size is kept while the phone still holds",
            "sm-restore" in _desktop_size,
            dict(_desktop_size),
        )
    finally:
        app_mod.tmux.resize_window = real_resize
        _handheld.clear()
        _desktop_size.clear()

    print("finding a project by name")
    import tempfile

    from clique import projects

    sand = Path(tempfile.mkdtemp(prefix="clique-projects-"))
    (sand / "work" / "wsg-sentinel").mkdir(parents=True)
    (sand / "work" / "wsg-sentinel" / ".git").mkdir()
    (sand / "work" / "notes").mkdir()
    # A repo inside a repo, which is the shape that broke the first version of
    # the walk: treating a project root as a leaf made every client directory
    # inside a client repo invisible.
    (sand / "clients" / ".git").mkdir(parents=True)
    (sand / "clients" / "acme-carwash").mkdir()
    (sand / "clients" / "acme-carwash" / "package.json").write_text("{}", encoding="utf-8")
    # The things a walk must not wander into. `.cache` is the real one: on the
    # box this was written for it is 11GB.
    (sand / ".cache" / "junk" / "pyproject.toml").parent.mkdir(parents=True)
    (sand / ".cache" / "junk" / "pyproject.toml").write_text("", encoding="utf-8")
    (sand / "work" / "node_modules" / "left-pad").mkdir(parents=True)
    (sand / "work" / "node_modules" / "left-pad" / "package.json").write_text(
        "{}", encoding="utf-8"
    )

    projects.forget()
    found, partial = projects.index(home=sand)
    names = sorted(p.name for p in found)
    check("it finds the repos", "wsg-sentinel" in names and "clients" in names, names)
    check(
        "including a project inside a project",
        "acme-carwash" in names,
        names,
    )
    check("a directory with no marker is not a project", "notes" not in names, names)
    check("it does not walk into a hidden directory", "junk" not in names, names)
    check("or into node_modules", "left-pad" not in names, names)
    check("and it finished", partial is False)

    hit = projects.search("sentinel", home=sand)
    check(
        "searching by name finds the path",
        [x["path"] for x in hit["projects"]] == [str(sand / "work" / "wsg-sentinel")],
        hit,
    )
    check("and says what kind it is", hit["projects"][0]["kind"] == "git", hit)
    kinds = {x["name"]: x["kind"] for x in projects.search("", home=sand)["projects"]}
    check("a manifest with no repo still counts", kinds.get("acme-carwash") == "node", kinds)
    # The ranking is the part somebody notices: the directory *called* the
    # thing has to beat the one that merely contains it in its path.
    (sand / "work" / "sentinel-old").mkdir()
    (sand / "work" / "sentinel-old" / ".git").mkdir()
    projects.forget()
    order = [x["name"] for x in projects.search("sentinel-old", home=sand)["projects"]]
    check("an exact name outranks a path match", order[:1] == ["sentinel-old"], order)
    check("nothing matches nonsense", projects.search("zzzz", home=sand)["projects"] == [])

    projects.forget()
    narrow = projects.search("", [str(sand / "clients")], home=sand)
    check(
        "naming a root narrows the walk to it",
        all(x["path"].startswith(str(sand / "clients")) for x in narrow["projects"]),
        narrow,
    )
    projects.forget()
    check(
        "a root inside another root is not walked twice",
        len(projects._roots([str(sand), str(sand / "work")], sand)) == 1,
    )
    shutil.rmtree(sand, ignore_errors=True)
    projects.forget()

    print("engine")
    tmux.bootstrap(SOCKET, history_limit=9000)
    check("server bootstraps", tmux.list_sessions(SOCKET) == [])
    tmux._run(["set-option", "-g", "history-limit", "2000"], SOCKET)
    tmux.bootstrap(SOCKET, history_limit=9000)
    again = tmux._run(["show-options", "-g", "history-limit"], SOCKET)
    check("a second bootstrap updates global options", "9000" in again, again.strip())

    sid = "1234abcd-0000-0000-0000-000000000000"
    mux = tmux.mux_name(sid)
    check("name is short and ours", mux == "sm-1234abcd", mux)

    tmux.create(
        mux,
        "/tmp",
        ["bash", "--norc", "-i"],
        socket=SOCKET,
        env={"CLIQUE": "1", "CLIQUE_SESSION": sid},
    )
    check("session exists", tmux.exists(mux, SOCKET))
    size_opt = tmux._run(["show-window-options", "-t", mux, "window-size"], SOCKET)
    check("the window does not autoscale from attach", "manual" in size_opt, size_opt.strip())
    tmux._run(["set-window-option", "-t", mux, "window-size", "latest"], SOCKET)
    tmux.bootstrap(SOCKET, history_limit=9000)
    relocked = tmux._run(["show-window-options", "-t", mux, "window-size"], SOCKET)
    check(
        "a restart relocks windows born before the option", "manual" in relocked, relocked.strip()
    )

    panes = tmux.list_sessions(SOCKET)
    check("lists one session", len(panes) == 1, panes)
    check("reports cwd", panes and panes[0].cwd == "/tmp", panes[0].cwd if panes else "")
    check("marked as ours", panes and panes[0].ours)

    hist = tmux._run(["display-message", "-p", "-t", mux, "#{history_limit}"], SOCKET).strip()
    check("history-limit applied to pane", hist == "9000", hist)

    env = tmux._run(["show-environment", "-t", mux, "CLIQUE_SESSION"], SOCKET).strip()
    check("session id in pane env", env.endswith(sid), env)

    # The literal-send path is the one that breaks first: a prompt full of
    # punctuation must land as characters, not as tmux key names.
    nasty = "echo 'quote\" ; semi $VAR {brace} Enter C-c'"
    tmux.send_text(mux, nasty, SOCKET)
    time.sleep(1.2)
    out = tmux.capture(mux, SOCKET, styled=False)
    check(
        "literal text survives send-keys", 'quote" ; semi $VAR {brace} Enter C-c' in out, out[-120:]
    )

    for i in range(40):
        tmux.send_text(mux, f"echo line{i}", SOCKET)
    time.sleep(1.5)
    scroll = tmux.capture(mux, SOCKET, lines=5000, styled=False)
    check("scrollback survives past the visible frame", "line0" in scroll and "line39" in scroll)

    check(
        "attach argv targets our socket",
        tmux.attach_argv(mux, SOCKET)[:4] == ["tmux", "-L", SOCKET, "attach-session"],
    )

    print("guards")
    try:
        tmux.kill("codeman-2be07f26", SOCKET)
        check("refuses to kill a foreign session", False, "no error raised")
    except tmux.TmuxError as exc:
        check("refuses to kill a foreign session", "refusing" in str(exc))

    try:
        tmux.create(mux, "/tmp", ["bash"], socket=SOCKET)
        check("refuses duplicate session", False, "no error raised")
    except tmux.TmuxError:
        check("refuses duplicate session", True)

    try:
        reg.get("nope")
        check("rejects unknown CLI type", False)
    except RegistryError:
        check("rejects unknown CLI type", True)

    print("notification edges")
    # Pure function, so every case is worth asserting: what fires and what
    # stays quiet is the difference between a notifier you keep and one you
    # mute after a day.
    edges = notify.Watcher._events
    quiet = ("", False, True)
    busy = ("", True, True)
    waiting = ("waiting", False, True)
    failed_ = ("error", False, True)
    gone = ("", False, False)

    check("working then quiet is 'finished'", edges(busy, quiet) == ["finished"])
    check("quiet staying quiet says nothing", edges(quiet, quiet) == [])
    check("becoming waiting says so", edges(quiet, waiting) == ["waiting"])
    check("staying waiting says it once", edges(waiting, waiting) == [])
    check("waiting outranks 'finished'", edges(busy, waiting) == ["waiting"])
    check("an error is its own event", edges(busy, failed_) == ["error"])
    check("waiting then error is news again", edges(waiting, failed_) == ["error"])
    check("dying says only that", edges(busy, gone) == ["died"])
    check("a dead session stays quiet", edges(gone, gone) == [])
    check("going back to work says nothing", edges(waiting, busy) == [])

    print("webhook targets")
    # The feature is "POST to a URL someone typed", which is the exact shape
    # that turns into a credential leak on a cloud box. These assertions are
    # the boundary: what a self-hoster legitimately points this at, and what
    # has no honest use at all.
    allow = notify.allowed
    check("a public endpoint is fine", allow("https://ntfy.sh/my-topic"))
    check("so is ntfy on this very box", allow("http://127.0.0.1:8080/hook"))
    check("and something on the LAN", allow("http://192.168.1.10:2586/message"))
    check("cloud metadata is refused", not allow("http://169.254.169.254/latest/meta-data/"))
    check("link-local v6 too", not allow("http://[fe80::1]/x"))
    check("file: is not a webhook", not allow("file:///etc/passwd"))
    check("nor is gopher:", not allow("gopher://example.com/1"))
    check(
        "a name that does not resolve is refused", not allow("http://clique-no-such-host.invalid/x")
    )
    check("and so is nonsense", not allow("not a url at all"))

    print("the prompt box decides itself")
    # The distinction is about what is on screen, not about what reads input.
    # A shell reads input and is not doubled by anything; the panel's box is
    # the only place Run, the repeat counter and a draft live there.
    check("a boxed CLI says so", types["claude"].own_input)
    check("a shell does not", not types["shell"].own_input)
    check("and it reaches the browser", reg.get("claude").as_dict()["own_input"] is True)

    print("working, or only redrawing")
    # The blind spot in tmux's activity clock, and the whole reason
    # clique/working.py exists: a redraw counts as output, so a CLI that
    # animates while it waits ticks the clock forever. Two panes that tick it
    # identically, one of which is doing nothing.
    tmux.create(
        "sm-still",
        "/tmp",
        ["bash", "-c", "while true; do printf '\\033[H\\033[2Jwaiting > '; sleep 0.4; done"],
        socket=SOCKET,
    )
    tmux.create(
        "sm-moving",
        "/tmp",
        [
            "bash",
            "-c",
            "i=0; while true; do i=$((i+1)); printf '\\033[H\\033[2Jline %s\\n' $i; sleep 0.4; done",
        ],
        socket=SOCKET,
    )
    time.sleep(1.0)
    panes = {p.mux: p for p in tmux.list_sessions(SOCKET)}
    check(
        "both panes tick the activity clock",
        all(time.time() - panes[n].activity < 2 for n in ("sm-still", "sm-moving")),
    )
    check(
        "and both are called working at first",
        working.busy(panes["sm-still"], SOCKET) and working.busy(panes["sm-moving"], SOCKET),
    )

    # Polled rather than sampled once, because "unchanged" is not a property
    # of one observation — the first capture after SETTLE has nothing to
    # compare against and correctly says nothing. The panel polls every three
    # seconds; this does the same for long enough to decide.
    deadline = time.time() + working.SETTLE + working.STILL + 12
    verdicts = {}
    while time.time() < deadline:
        panes = {p.mux: p for p in tmux.list_sessions(SOCKET)}
        for name in ("sm-still", "sm-moving"):
            verdicts[name] = working.busy(panes[name], SOCKET)
        if not verdicts["sm-still"]:
            break
        time.sleep(2)
    check("a pane redrawing the same screen settles to not working", not verdicts["sm-still"])
    check("a pane whose output changes stays working", verdicts["sm-moving"])

    working.forget(set())
    check(
        "and everything about a session that is gone is dropped",
        not working._since and not working._seen,
    )
    for name in ("sm-still", "sm-moving"):
        tmux.kill(name, SOCKET)

    print("compacting is caught before the waiting/error settle delay")
    # A real compaction is often faster than SETTLE (8s): this pane keeps
    # redrawing a changing "Compacting..." line the whole time, the same
    # shape a real CLI's spinner draws, so busy() never settles during it.
    # Filled with real lines first so the pane is genuinely full, the same
    # as an ordinary session's scrollback — a fresh, mostly-blank pane would
    # (correctly) trip the padding check below instead of this one.
    tmux.create(
        "sm-compact",
        "/tmp",
        [
            "bash",
            "-c",
            "yes filler | head -100; "
            "i=0; while [ $i -lt 40 ]; do i=$((i+1)); "
            "printf 'Compacting conversation... %s\\n' $i; sleep 0.3; done",
        ],
        socket=SOCKET,
    )
    time.sleep(1.5)
    compact_pane = next(p for p in tmux.list_sessions(SOCKET) if p.mux == "sm-compact")
    check(
        "the pane is busy, well short of SETTLE",
        working.busy(compact_pane, SOCKET) and not working.settled(compact_pane),
    )
    check(
        "compacting is detected anyway — settled() would still refuse it",
        attention.detect_compacting("sm-compact", compact_pane.activity, [], SOCKET),
    )
    tmux.kill("sm-compact", SOCKET)

    print("generic question prompts")
    # These have to fire for a CLI with no [attention] table — that is how
    # Codex, Cursor, Gemini and the rest surface a permission prompt without
    # CLIque knowing anything about those vendors.
    check(
        "(y/n) is a question",
        attention.verdict_text("Allow this command (y/n)", [], []) == "waiting",
    )
    check(
        "a capitalised default (Y/n) is too",
        attention.verdict_text("Apply this change? (Y/n)", [], []) == "waiting",
    )
    check(
        "a [y/N] bracket prompt is",
        attention.verdict_text("Overwrite the file [y/N]", [], []) == "waiting",
    )
    check(
        "a short question-mark line is",
        attention.verdict_text("Do you want to continue?", [], []) == "waiting",
    )
    check(
        "a Codex-style permission question is",
        attention.verdict_text("Allow Codex to run `npm test`?", [], []) == "waiting",
    )
    check(
        "a numbered choice is",
        attention.verdict_text("  ❯ 1. Allow\n    2. Deny", [], []) == "waiting",  # noqa: RUF001
    )
    check(
        "a menu drawn with another pointer glyph is",
        attention.verdict_text("  › 2. No, keep it", [], []) == "waiting",  # noqa: RUF001
    )
    check(
        "an arrow-key menu hint is",
        attention.verdict_text("Choose one: (Use arrow keys)", [], []) == "waiting",
    )
    # What a peek and the row's "saying" line both rest on: a pane is mostly
    # frame, and the frame has to be droppable without knowing any CLI.
    check(
        "a rule of box drawing is frame",
        attention.is_rule("\u2500" * 40),
    )
    check(
        "a bare prompt mark is frame",
        attention.is_rule("\u276f"),
    )
    check(
        "and still is when the CLI pads it with a non-breaking space",
        attention.is_rule("\u276f\xa0"),
    )
    check(
        "a rule padded the same way is still frame",
        attention.is_rule("\u2500" * 40 + "\xa0"),
    )
    check(
        "a line with one real word in it is content",
        not attention.is_rule("\u2500" * 40 + " Ran 1 shell command"),
    )
    check(
        "content_lines keeps what was said and drops the frame around it",
        attention.content_lines("\u2500" * 20 + "\n\u276f\xa0\nRan 1 shell command\n   \n")
        == ["Ran 1 shell command"],
    )

    check(
        "a traceback is an error, not a question",
        attention.verdict_text("Traceback (most recent call last):\n  File", [], []) == "error",
    )
    check(
        "a compacting status line is compacting, not a question",
        attention.verdict_text("✻ Compacting conversation… (esc to interrupt)", [], [])
        == "compacting",
    )
    check(
        "a per-CLI compacting word from clis.toml is honoured",
        attention.verdict_text("Shrinking context history", [], [], ["(?i)shrinking context"])
        == "compacting",
    )
    check(
        "an error during compaction still wins",
        attention.verdict_text("Compacting…\nError: ran out of memory", [], []) == "error",
    )
    # A short-lived TUI in a tall pane leaves genuinely blank rows below it —
    # capture-pane returns the pane's full row count, not the height of what
    # is drawn. Slicing the raw last LINES would eat that padding and could
    # push the status line out of the window it is supposed to be found in.
    check(
        "trailing blank pane rows do not push the status line out of the tail",
        attention.verdict_text("Compacting conversation…\n" + "\n" * 60, [], []) == "compacting",
    )
    # False positives are the failure mode that erodes trust in the inbox, so
    # the finished-turn shapes that actually caused one must stay silent.
    check(
        "ordinary output is neither",
        attention.verdict_text("wrote 12 files\nrunning tests", [], []) == "",
    )
    check(
        "a finished-turn summary is not a question",
        attention.verdict_text("● Done — 109.3 GB back, verified on disk", [], []) == "",
    )
    check(
        "a spinner status line is not a question",
        attention.verdict_text("✻ Sautéed for 9m 17s · 1 shell still running", [], []) == "",
    )
    check("a bare prompt glyph is not a question", attention.verdict_text("❯", [], []) == "")  # noqa: RUF001
    check(
        "a plain numbered list is not a menu",
        attention.verdict_text("1. First step\n2. Second step", [], []) == "",
    )
    check(
        "a ternary in code is not a question",
        attention.verdict_text("  const x = ok ? 1 : 2;", [], []) == "",
    )
    check(
        "a long prose line ending in ? is not a prompt",
        attention.verdict_text(
            "this is a long explanatory sentence that trails off into a "
            "rhetorical question aimed straight at the reader?",
            [],
            [],
        )
        == "",
    )

    print("service status")

    # The two things that make this feature honest rather than a widget: it
    # only ever asks about a CLI you actually have running, and it says
    # nothing at all when everyone is up.
    class _Store:
        settings: ClassVar[dict] = {"service_status": True, "open_tabs": ["s1"]}
        sessions: ClassVar[list] = []

    class _Panel:
        store = _Store()
        registry = reg

    svc = services.Services(_Panel())
    _Store.sessions = [
        SimpleNamespace(cli="claude", id="s1"),
        SimpleNamespace(cli="shell", id="s2"),
    ]
    asked = sorted(svc.wanted())
    check("asks only about CLIs with a tab open", asked == ["claude"], asked)
    check("and never about one with no feed", "shell" not in asked, asked)

    _Store.settings = {"service_status": True, "open_tabs": []}
    check("a session with no tab is not asked about", svc.wanted() == {}, svc.wanted())

    _Store.settings = {"service_status": True, "open_tabs": ["s1"]}
    _Store.sessions = []
    check("an idle panel asks nothing at all", svc.wanted() == {}, svc.wanted())

    # A reading is only shown while it is a problem and while it is fresh.
    now = int(time.time())
    svc._seen = {
        "claude": {
            "cli": "claude",
            "label": "Claude Code",
            "indicator": "none",
            "description": "All Systems Operational",
            "url": "",
            "checked": now,
        },
    }
    check("an operational service is not news", svc.snapshot() == [], svc.snapshot())
    svc._seen["claude"]["indicator"] = "major"
    check("a real outage is", len(svc.snapshot()) == 1, svc.snapshot())
    svc._seen["claude"]["checked"] = now - services.STALE - 1
    check(
        "and a reading nobody could refresh goes quiet rather than stale",
        svc.snapshot() == [],
        svc.snapshot(),
    )

    # Worst first, so the bar leads with the thing that matters.
    svc._seen = {
        "a": {
            "cli": "a",
            "label": "A",
            "indicator": "minor",
            "description": "",
            "url": "",
            "checked": now,
        },
        "b": {
            "cli": "b",
            "label": "B",
            "indicator": "critical",
            "description": "",
            "url": "",
            "checked": now,
        },
    }
    check(
        "worst first",
        [r["cli"] for r in svc.snapshot()] == ["b", "a"],
        [r["cli"] for r in svc.snapshot()],
    )

    # The fetcher refuses the same addresses the webhook refuses.
    check(
        "a status feed cannot be pointed at cloud metadata",
        services.read("http://169.254.169.254/api/v2/status.json") is None,
    )
    check(
        "nor at a host that does not resolve",
        services.read("https://clique-no-such-host.invalid/api/v2/status.json") is None,
    )

    print("front end")
    # There is no build step, which is the point — and it also means nothing
    # between a typo and the browser. A syntax error in app.js does not fail a
    # Python test suite; it fails silently, in front of the user, as a panel
    # that loads and then does nothing. `node --check` parses without running.
    node = shutil.which("node") or shutil.which("nodejs")
    if not node:
        print("  · no node; skipping the parse check")
    else:
        for name in ("app.js", "themes.js"):
            script = ROOT / "clique" / "web" / name
            done = subprocess.run([node, "--check", str(script)], capture_output=True, text=True)
            check(
                f"{name} parses",
                done.returncode == 0,
                done.stderr.strip().splitlines()[-1] if done.stderr.strip() else "",
            )

        # Every handler a menu item points at has to exist.
        #
        # `node --check` parses app.js and is perfectly happy with a call to a
        # function nobody wrote: the reference is only resolved when somebody
        # clicks. That is how "Move to folder…" spent twelve releases throwing
        # a ReferenceError into a console nobody had open, and it is a whole
        # class of bug that costs one regex to close. Arrow-wrapped calls are
        # the menu idiom throughout, so that is what this reads.
        source = (ROOT / "clique" / "web" / "app.js").read_text(encoding="utf-8")
        declared = set(re.findall(r"(?:function|const|let|var)\s+([A-Za-z_$][\w$]*)", source))
        called = set(re.findall(r"\(\)\s*=>\s*([A-Za-z_$][\w$]*)\s*\(", source))
        # Things that are legitimately not ours: globals, and methods reached
        # through an object rather than by bare name.
        ambient = {
            "alert",
            "confirm",
            "fetch",
            "close",
            "open",
            "print",
            "reload",
            "Boolean",
            "Number",
            "String",  # builtins used as callbacks
        }
        missing = sorted(called - declared - ambient)
        check("every menu handler app.js calls is defined in it", not missing, missing)

        # The decisions inside app.js, tested without a browser. See
        # tools/frontend_check.js for why that is possible without a build.
        done = subprocess.run(
            [node, str(ROOT / "tools" / "frontend_check.js")], capture_output=True, text=True
        )
        for line in done.stdout.splitlines():
            if line.strip().startswith(("ok", "FAIL")):
                print("  " + line.strip())
        tally = done.stdout.strip().splitlines()[-1] if done.stdout.strip() else "no output"
        check(f"front-end logic: {tally}", done.returncode == 0, done.stderr.strip()[:200])

    print("the unit that keeps sessions alive")
    # One line in a file nobody reads, and the entire promise of the product
    # rests on it. systemd's default KillMode signals every process in the
    # unit's cgroup, and the tmux server is a child of the panel — so with the
    # default, upgrading CLIque kills every session it exists to protect.
    unit = (ROOT / "deploy" / "clique.service").read_text()
    check(
        "the service unit does not take tmux down with it",
        "KillMode=process" in unit,
        "KillMode is missing — a restart will kill every session",
    )

    print("icons")
    # The sprite is generated, and a hand-edit or a half-finished rename would
    # otherwise show up as an invisible button rather than a failure.
    done = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "build_icons.py"), "--check"],
        capture_output=True,
        text=True,
    )
    check(
        "the sprite matches the icon list",
        done.returncode == 0,
        (done.stderr or done.stdout).strip()[:120],
    )

    page = (ROOT / "clique" / "web" / "index.html").read_text()
    script = (ROOT / "clique" / "web" / "app.js").read_text()
    used = set(re.findall(r'href="#i-([a-z0-9-]+)"', page + script))
    # Names inside an icon() call, including the ternary in the folder caret —
    # pull the whole argument list, then every quoted string out of it.
    # Only the first argument — the icon name. The second is a CSS class, and
    # the function's own definition has no quoted first argument at all, so it
    # contributes nothing.
    for call in re.findall(r"icon\(([^)]*)\)", script):
        used |= set(re.findall(r"""['"]([a-z-]+)['"]""", call.split(",")[0]))
    have = set(re.findall(r'<symbol id="i-([a-z0-9-]+)"', page))
    check("every icon drawn has a symbol behind it", used <= have, sorted(used - have))

    # The whole reason these are inline SVG rather than an image or a font: a
    # theme changes `color` and the icons follow. One `fill="#333"` from a
    # future regenerate would silently opt that icon out, and it would only
    # show up as an invisible control on somebody's light theme.
    block = page.split("<!-- icons:start -->", 1)[1].split("<!-- icons:end -->", 1)[0]
    painted = set(re.findall(r'(?:fill|stroke)="([^"]+)"', block))
    check(
        "no icon carries a colour of its own", painted <= {"none", "currentColor"}, sorted(painted)
    )

    print("snippets on the bar")
    # Real Store, not a fake — the thing worth catching here is the old
    # custom_quick_commands shape surviving the merge into snippets.
    from clique import store as store_mod

    qc_dir = Path(tempfile.mkdtemp(prefix="clique-quickcmd-"))
    qc_store = store_mod.Store(qc_dir / "state.json")
    qc_store.update_settings(
        {"snippets": [{"trigger": "", "label": "cost", "text": "/cost", "bar": True}]}
    )
    check(
        "a bar snippet needs no trigger",
        qc_store.settings["snippets"][0]["bar"]
        and qc_store.settings["snippets"][0]["text"] == "/cost",
        qc_store.settings["snippets"],
    )
    qc_store.update_settings(
        {
            "snippets": [
                {"trigger": "", "text": "/cost", "bar": True, "color": "#4A9EFF"},
                {"trigger": "", "text": "/x", "bar": True, "color": "javascript:alert(1)"},
            ]
        }
    )
    colors = [s["color"] for s in qc_store.settings["snippets"]]
    check(
        "a snippet's pill colour is kept only if it is a real hex",
        colors == ["#4A9EFF", ""],
        colors,
    )
    qc_store.update_settings({"snippets": []})
    check(
        "clearing snippets clears the bar with it",
        qc_store.settings["snippets"] == [],
        qc_store.settings["snippets"],
    )

    # A pre-0.72.0 state.json with the old per-CLI shape still on disk should
    # come back up as bar-shown snippets, not vanish.
    (qc_dir / "state.json").write_text(
        json.dumps(
            {
                "settings": {
                    "custom_quick_commands": {
                        "claude": ["/cost", "Grok CLI can we use grok-4.7-build-fast"]
                    },
                    "snippets": [{"trigger": ";rev", "label": "", "text": "review this"}],
                }
            }
        ),
        encoding="utf-8",
    )
    migrated_store = store_mod.Store(qc_dir / "state.json")
    check(
        "custom_quick_commands is gone after load",
        "custom_quick_commands" not in migrated_store.settings,
        list(migrated_store.settings),
    )
    bar_texts = {s["text"] for s in migrated_store.settings["snippets"] if s["bar"]}
    check(
        "both legacy commands became bar snippets",
        bar_texts == {"/cost", "Grok CLI can we use grok-4.7-build-fast"},
        migrated_store.settings["snippets"],
    )
    check(
        "the pre-existing typed snippet survives the migration untouched",
        any(s["trigger"] == ";rev" and not s["bar"] for s in migrated_store.settings["snippets"]),
        migrated_store.settings["snippets"],
    )

    print("session templates")
    tmpl_dir = Path(tempfile.mkdtemp(prefix="clique-tmpl-"))
    tmpl_store = store_mod.Store(tmpl_dir / "state.json")
    tmpl_store.update_settings(
        {
            "session_templates": [
                {
                    "name": "bugfix",
                    "cli": "grok",
                    "cwd": "/src",
                    "prompt": "look",
                    "folder": "f",
                    "worktree": 1,
                },
                {"name": "no cli", "cli": "", "cwd": "/src"},
                {"name": "no cwd", "cli": "grok", "cwd": "  "},
            ]
        }
    )
    kept = tmpl_store.settings["session_templates"]
    check(
        "a template with a cli and a directory is kept",
        len(kept) == 1
        and kept[0]["cli"] == "grok"
        and kept[0]["cwd"] == "/src"
        and kept[0]["prompt"] == "look"
        and kept[0]["worktree"] is True,
        kept,
    )
    check(
        "a template missing cli or cwd is dropped",
        all(t["name"] != "no cli" and t["name"] != "no cwd" for t in kept),
        kept,
    )
    tmpl_store.update_settings(
        {"session_templates": [{"cli": "grok", "cwd": f"/p/{i}"} for i in range(205)]}
    )
    check(
        "session templates cap at 200",
        len(tmpl_store.settings["session_templates"]) == 200,
        len(tmpl_store.settings["session_templates"]),
    )

    print("usage: a reset time in either shape a vendor sends it")
    # Anthropic answers with ISO already; OpenAI answers with Unix seconds.
    # _windows() is what has to paper over that, once, rather than every CLI's
    # TOML block needing to say which kind it is.
    epoch_windows = usage._windows(
        {"window": [{"label": "PLAN", "percent": "p", "resets": "r"}]},
        {"p": 12, "r": 1792625277},
    )
    check(
        "a Unix-seconds reset becomes ISO",
        epoch_windows and epoch_windows[0]["resets_at"] == "2026-10-21T23:27:57+00:00",
        epoch_windows,
    )
    iso_windows = usage._windows(
        {"window": [{"label": "PLAN", "percent": "p", "resets": "r"}]},
        {"p": 12, "r": "2026-09-21T12:00:00Z"},
    )
    check(
        "an ISO reset passes through unchanged",
        iso_windows and iso_windows[0]["resets_at"] == "2026-09-21T12:00:00Z",
        iso_windows,
    )
    missing_windows = usage._windows(
        {"window": [{"label": "X", "percent": "p", "resets": "r"}]}, {"p": 12}
    )
    check(
        "no reset at all is None, not a crash",
        missing_windows and missing_windows[0]["resets_at"] is None,
        missing_windows,
    )

    print("usage: a `*` path step takes a dict's one value, key unknown")
    # Grok's auth.json is keyed by "<issuer>::<client-id>", not a fixed field.
    grok_auth = {"https://auth.x.ai::b1a0": {"key": "tok_abc", "email": "j@x.com"}}
    check(
        "the wildcard reaches the token regardless of the outer key",
        usage._dig(grok_auth, "*.key") == "tok_abc",
        usage._dig(grok_auth, "*.key"),
    )
    check(
        "a wildcard against an empty dict is None, not a crash",
        usage._dig({}, "*.key") is None,
    )

    print("usage: a `key=value` path step finds a list item by a stable id")
    # Antigravity's usage reply nests buckets inside named groups rather than
    # a fixed field per number: groups: [{name, buckets: [{id, ...}]}].
    quota = {
        "groups": [
            {
                "name": "Gemini Models",
                "buckets": [{"id": "gemini-weekly", "remaining_fraction": 0.99}],
            },
            {
                "name": "Claude and GPT models",
                "buckets": [{"id": "3p-weekly", "remaining_fraction": 1.0}],
            },
        ]
    }
    check(
        "a two-level id search reaches the right bucket",
        usage._dig(
            quota, "groups.name=Claude and GPT models.buckets.id=3p-weekly.remaining_fraction"
        )
        == 1.0,
        usage._dig(
            quota, "groups.name=Claude and GPT models.buckets.id=3p-weekly.remaining_fraction"
        ),
    )
    check(
        "an id that is not in the list is None, not a crash",
        usage._dig(quota, "groups.name=Nope.buckets.id=x.remaining_fraction") is None,
    )
    check(
        "a `key=value` step against a dict (not a list) is None, not a crash",
        usage._dig({"groups": {}}, "groups.name=x.y") is None,
    )

    print(
        "usage: `remaining` is the inverse of `percent`, for a vendor that answers with what is left"
    )
    remaining_windows = usage._windows(
        {"window": [{"label": "GEM", "remaining": "left"}]}, {"left": 0.75}
    )
    check(
        "a 0.75 remaining fraction becomes 25% used",
        remaining_windows and abs(remaining_windows[0]["percent"] - 25.0) < 1e-9,
        remaining_windows,
    )
    check(
        "percent wins over remaining when a probe somehow declares both",
        usage._windows(
            {"window": [{"label": "X", "percent": "p", "remaining": "r"}]}, {"p": 40, "r": 0.1}
        )[0]["percent"]
        == 40.0,
    )

    print("usage: a `cmd` probe runs argv and reads its stdout as JSON")
    # No live agy dependency here — a stand-in argv the real python3 can run,
    # same shape as [cli.antigravity.usage].cmd in clis.toml.
    cmd_payload = usage._fetch_cmd(
        {"cmd": [sys.executable, "-c", 'print(\'{"ok": true, "n": 7}\')']}
    )
    check(
        "its stdout parses as the payload",
        cmd_payload == {"ok": True, "n": 7},
        cmd_payload,
    )
    check(
        "a nonzero exit is no usage, not a crash",
        usage._fetch_cmd({"cmd": [sys.executable, "-c", "import sys; sys.exit(1)"]}) is None,
    )
    check(
        "a spec with neither cmd nor url is None",
        usage._fetch_cmd({}) is None,
    )

    print("usage: running-only by default, every installed CLI on an explicit ask")
    # Panel.usage_now is a plain method on self.store/self.registry, so a
    # duck-typed fake stands in rather than wiring up a real Panel (auth,
    # tokens, tmux) for one filtering rule.
    seen_cli_ids: list[str] = []

    def fake_usage_read(cli_id, spec, guard, *, force=False):
        seen_cli_ids.append(cli_id)
        return {"cli": cli_id, "windows": [], "checked": 0}

    usage._orig_read, usage.read = usage.read, fake_usage_read
    try:
        fake_store = SimpleNamespace(
            settings={"usage_bar": True},
            sessions=[SimpleNamespace(cli="claude")],
        )
        fake_registry = SimpleNamespace(
            types=lambda: {
                "claude": SimpleNamespace(usage={"url": "x"}, installed=True),
                "codex": SimpleNamespace(usage={"url": "x"}, installed=True),
                "gemini": SimpleNamespace(usage=None, installed=True),
                "grok": SimpleNamespace(usage={"url": "x"}, installed=False),
            }
        )
        fake_panel = SimpleNamespace(store=fake_store, registry=fake_registry)

        seen_cli_ids.clear()
        app_mod.Panel.usage_now(fake_panel)
        check(
            "by default, only a running CLI with a probe is asked",
            seen_cli_ids == ["claude"],
            seen_cli_ids,
        )

        seen_cli_ids.clear()
        app_mod.Panel.usage_now(fake_panel, all_installed=True)
        check(
            "all_installed asks every installed CLI with a probe, running or not",
            sorted(seen_cli_ids) == ["claude", "codex"],
            seen_cli_ids,
        )

        seen_cli_ids.clear()
        fake_store.settings["usage_bar"] = False
        app_mod.Panel.usage_now(fake_panel, all_installed=True)
        check("usage_bar off refuses even the explicit ask", seen_cli_ids == [], seen_cli_ids)
    finally:
        usage.read = usage._orig_read

    print("mounted under a path prefix")
    # CLIque is documented as running behind `tailscale serve` at /clique,
    # which strips the prefix before the server sees it — so only the browser
    # knows where the app is mounted, and every request has to be resolved
    # against <base href>. One absolute path is enough to break a feature for
    # everyone who followed the README, and to work perfectly on localhost.
    # That is exactly how the changelog tab shipped broken.
    script = (ROOT / "clique" / "web" / "app.js").read_text()
    absolute = re.findall(r"""(?:api|fetch)\(\s*['"`]/[^'"`]*""", script)
    check("no API call escapes the mount point", not absolute, absolute[:3])

    print("per-session cpu")
    # Same shape as the memory read: one number per root, over the whole
    # tree, from the shared /proc walk. A rate needs two samples, so the
    # first look is zero and a pid that dies between them must not raise.
    sysinfo._cpu_previous.clear()
    sysinfo._proc_cache["at"] = 0.0
    walks = {"n": 0}
    real_walk = sysinfo._walk_proc

    def _counting_walk():
        walks["n"] += 1
        return real_walk()

    sysinfo._walk_proc = _counting_walk
    try:
        own = os.getpid()
        rss_own = sysinfo.rss_by_root([own])
        cpu_own = sysinfo.cpu_percent_by_root([own])
        check("rss still counts this process", rss_own.get(own, 0) > 0, rss_own)
        check("the first cpu sample is zero", cpu_own == {own: 0.0}, cpu_own)
        check("memory and cpu share one /proc walk", walks["n"] == 1, walks["n"])
    finally:
        sysinfo._walk_proc = real_walk

    missing = [1 << 22, (1 << 22) + 1]
    cold = sysinfo.cpu_percent_by_root(missing)
    check(
        "every asked-for pid is a key",
        set(cold) == set(missing) and all(v == 0.0 for v in cold.values()),
        cold,
    )

    ghost = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        sysinfo._cpu_previous.pop(ghost.pid, None)
        sysinfo._proc_cache["at"] = 0.0
        before = sysinfo.cpu_percent_by_root([ghost.pid])
        check("a new pid starts at zero", before.get(ghost.pid) == 0.0, before)
        ghost.kill()
        ghost.wait(timeout=5)
        sysinfo._proc_cache["at"] = 0.0
        try:
            after = sysinfo.cpu_percent_by_root([ghost.pid])
            raised = False
        except Exception as exc:  # noqa: BLE001 — the check is that nothing escapes
            after = exc
            raised = True
        check("a pid that exits between samples does not raise", not raised, after)
        check(
            "a gone pid comes back as a float",
            isinstance(after, dict) and isinstance(after.get(ghost.pid), float),
            after,
        )
    finally:
        if ghost.poll() is None:
            ghost.kill()
            ghost.wait(timeout=5)

    burn = subprocess.Popen([sys.executable, "-c", "while True:\n    pass"])
    try:
        sysinfo._cpu_previous.pop(burn.pid, None)
        sysinfo._proc_cache["at"] = 0.0
        idle = sysinfo.cpu_percent_by_root([burn.pid])
        check("a busy child starts at zero", idle.get(burn.pid) == 0.0, idle)
        time.sleep(0.5)
        sysinfo._proc_cache["at"] = 0.0
        hot = sysinfo.cpu_percent_by_root([burn.pid])
        check("a busy child shows cpu on the next sample", hot.get(burn.pid, 0) > 0, hot)
        held = sysinfo.cpu_percent_by_root([burn.pid])
        check(
            "a second read inside the cache window keeps that rate",
            held.get(burn.pid) == hot.get(burn.pid),
            held,
        )
    finally:
        if burn.poll() is None:
            burn.kill()
        burn.wait(timeout=5)
    sysinfo._proc_cache["at"] = 0.0

    check_mcp()

    print("teardown")
    tmux.kill(mux, SOCKET)
    check("kills our own session", not tmux.exists(mux, SOCKET))
    tmux._run(["kill-server"], SOCKET, check=False)
    check("empty socket lists nothing", tmux.list_sessions(SOCKET) == [])

    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
