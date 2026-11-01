# pyright: reportAttributeAccessIssue=false, reportIncompatibleMethodOverride=false, reportArgumentType=false
#!/usr/bin/env python3
"""Four backend hardening fixes, each with a check that fails without it.

- llm.complete does not follow a redirect (SSRF guard only vets the first URL);
- an oversized request body gets 413 and closes the connection (no desync);
- two concurrent files.write calls never share a temp file;
- a failed create_session removes its worktree and kills its tmux session.

No network beyond loopback, no tmux, no browser.

    python3 tools/hardening_check.py

Exit status is 0 on pass, 1 on fail.
"""

from __future__ import annotations

import http.server
import io
import sys
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from clique import app, attention, files, gitinfo, llm, store, tmux, usage


def check_llm_redirect() -> bool:
    hits: list[str] = []
    raised = False

    class H(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            hits.append(self.path)
            if self.path == "/start":
                self.send_response(302)
                self.send_header("Location", "/internal")
                self.end_headers()
            else:
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"{}")

        do_GET = do_POST

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    guard, llm._guard_url = llm._guard_url, lambda url: None  # loopback is the test fixture
    profile = {"kind": "openai", "base_url": f"http://127.0.0.1:{srv.server_port}/start", "model": "m", "key": "k"}
    orig = llm._request_for
    llm._request_for = lambda p, m, t: (profile["base_url"], {}, {})
    try:
        llm.complete(profile, [{"role": "user", "content": "x"}])
    except llm.LLMError:
        raised = True
    finally:
        llm._guard_url, llm._request_for = guard, orig
        srv.shutdown()
    return raised and hits == ["/start"]


def check_body_413() -> bool:
    """Oversize POST and PATCH against a real Handler over a socket: one 413,
    then the connection is closed, and the handler never carries on."""
    import socket

    errors: list[object] = []

    class S(app.Server):
        def handle_error(self, request, client_address):
            errors.append(client_address)

    saved = (app.Handler.panel if hasattr(app.Handler, "panel") else None, app.Handler._host_ok, app.Handler._may_write)
    app.Handler.panel = SimpleNamespace()
    app.Handler._host_ok = lambda self: True
    app.Handler._may_write = lambda self: (True, "")
    srv = S(("127.0.0.1", 0), app.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    good = True
    try:
        for method, path in (("POST", "/api/sessions"), ("PATCH", "/api/settings")):
            c = socket.create_connection(("127.0.0.1", srv.server_address[1]), timeout=5)
            c.sendall(f"{method} {path} HTTP/1.1\r\nHost: 127.0.0.1\r\n"
                      f"Content-Length: {app.MAX_BODY_BYTES + 1}\r\n\r\n".encode())
            data = b""
            while True:  # EOF means the server closed; a timeout means it did not
                chunk = c.recv(65536)
                if not chunk:
                    break
                data += chunk
            c.close()
            good &= b" 413 " in data.split(b"\r\n")[0]
            good &= data.count(b"HTTP/1.") == 1
    finally:
        srv.shutdown()
        app.Handler.panel, app.Handler._host_ok, app.Handler._may_write = saved
    return good and not errors


def check_write_temp_unique() -> bool:
    d = Path(tempfile.mkdtemp())
    (d / "f.txt").write_text("a")
    seen: list[str] = []
    real = files.os.replace
    files.os.replace = lambda src, dst: (seen.append(str(src)), real(src, dst))[1]
    try:
        files.write(str(d), "f.txt", "one")
        # a stale temp from a "concurrent" writer must not be reused
        files.write(str(d), "f.txt", "two")
    finally:
        files.os.replace = real
    return len(set(seen)) == 2 and (d / "f.txt").read_text() == "two" and not list(d.glob("*.clique-save"))


def _rollback_run(fail: str) -> list[str]:
    log: list[str] = []
    saved = (gitinfo.repo_root, gitinfo.add_worktree, gitinfo.worktree_setup, gitinfo.remove_worktree,
             tmux.bootstrap, tmux.create, tmux.kill)
    wt = tempfile.mkdtemp()

    def boom(*a, **k):
        raise OSError(fail)

    def make_tmux(*a, **k):
        log.append("tmux")
        if fail == "tmux":  # the session exists, then create fails
            boom()

    gitinfo.repo_root = lambda c: c
    gitinfo.add_worktree = lambda r, b: (wt, "")
    gitinfo.worktree_setup = boom if fail == "setup" else (lambda r, m: None)
    gitinfo.remove_worktree = lambda p: log.append("rmworktree")
    tmux.bootstrap = lambda: None
    tmux.create = make_tmux
    tmux.kill = lambda m, *a, **k: log.append("kill")
    p = object.__new__(app.Panel)
    cli = SimpleNamespace(installed=True, default_mode=None, hooks=False, label="x", command="x")
    p.registry = SimpleNamespace(launch_argv=lambda *a, **k: ["true"], get=lambda c: cli)
    p._pane_env = lambda s: {}
    p._hooks_argv = lambda c: []
    p.store = SimpleNamespace(add_session=boom if fail == "store" else None)
    try:
        p.create_session({"cwd": wt, "worktree": True, "branch": "b"})
    except OSError:
        pass
    finally:
        (gitinfo.repo_root, gitinfo.add_worktree, gitinfo.worktree_setup, gitinfo.remove_worktree,
         tmux.bootstrap, tmux.create, tmux.kill) = saved
    return log


def check_create_rollback() -> bool:
    setup = _rollback_run("setup")
    tm = _rollback_run("tmux")
    store = _rollback_run("store")
    return ("rmworktree" in setup and "tmux" not in setup
            and "kill" in tm and "rmworktree" in tm
            and "kill" in store and "rmworktree" in store)


def check_write_chmod_failure_cleans_up() -> bool:
    d = Path(tempfile.mkdtemp())
    (d / "f.txt").write_text("a")
    real = files.os.fchmod

    def boom(fd, mode):
        raise OSError("no chmod")

    files.os.fchmod = boom
    try:
        files.write(str(d), "f.txt", "x")
    except OSError:
        pass
    finally:
        files.os.fchmod = real
    return sorted(p.name for p in d.iterdir()) == ["f.txt"]


def check_upload_symlink_swap() -> bool:
    """A drop dir swapped for a symlink between the fence check and the create
    must not let the file land outside cwd."""
    cwd, outside = Path(tempfile.mkdtemp()), Path(tempfile.mkdtemp())
    real_mkdir, fence = Path.mkdir, files._FENCE

    def swap(self, *a, **k):
        if self.name == files.DROP_DIR:
            real_mkdir(self, *a, **k)
            self.rmdir()
            self.symlink_to(outside)
            return
        return real_mkdir(self, *a, **k)

    Path.mkdir, files._FENCE = swap, True
    msg = ""
    try:
        files.store_upload(str(cwd), "a.txt", b"x")
    except ValueError as exc:
        msg = str(exc)
    finally:
        Path.mkdir, files._FENCE = real_mkdir, fence
    return msg == "outside the session directory" and not list(outside.iterdir())


def check_login_throttle() -> bool:
    """N threads attempt at once (each sleeps in the check, as a real one does
    between check and failure): at most MAX_FAILURES may pass, and a success
    clearing the dict while others iterate must not raise."""
    h = SimpleNamespace(panel=SimpleNamespace(failures={}, failures_lock=threading.Lock()),
                        FAILURE_WINDOW=300, MAX_FAILURES=8)
    h.panel.failures["old"] = [1.0]
    passed: list[bool] = []
    barrier = threading.Barrier(40)

    def attempt():
        barrier.wait()
        passed.append(not app.Handler._reserve_attempt(h, "x"))

    threads = [threading.Thread(target=attempt) for _ in range(40)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    with h.panel.failures_lock:
        h.panel.failures.pop("x", None)
    return sum(passed) <= h.MAX_FAILURES and "old" not in h.panel.failures


def check_llm_response_cap() -> bool:
    class H(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"choices":[{"message":{"content":"' + b"a" * (llm.MAX_RESPONSE_BYTES + 10) + b'"}}]}')

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    guard, orig = llm._guard_url, llm._request_for
    llm._guard_url = lambda url: None
    llm._request_for = lambda p, m, t: (f"http://127.0.0.1:{srv.server_port}/", {}, {})
    try:
        llm.complete({"kind": "openai"}, [{"role": "user", "content": "x"}])
        return False
    except llm.LLMError as exc:
        return "too large" in str(exc)
    finally:
        llm._guard_url, llm._request_for = guard, orig
        srv.shutdown()


def check_body_must_be_object() -> bool:
    import socket

    class S(app.Server):
        pass

    saved = (getattr(app.Handler, "panel", None), app.Handler._host_ok, app.Handler._may_write)
    app.Handler.panel = SimpleNamespace()
    app.Handler._host_ok = lambda self: True
    app.Handler._may_write = lambda self: (True, "")
    srv = S(("127.0.0.1", 0), app.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    good = True
    try:
        for payload in (b"[1]", b"not json", b'"s"'):
            c = socket.create_connection(("127.0.0.1", srv.server_address[1]), timeout=5)
            c.sendall(b"PATCH /api/settings HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n"
                      b"Content-Length: %d\r\n\r\n" % len(payload) + payload)
            data = b""
            while chunk := c.recv(65536):
                data += chunk
            c.close()
            good &= b" 400 " in data.split(b"\r\n")[0]
    finally:
        srv.shutdown()
        app.Handler.panel, app.Handler._host_ok, app.Handler._may_write = saved
    return good


def check_store_bad_shape_uses_backup() -> bool:
    import json

    d = Path(tempfile.mkdtemp())
    (d / "state.json").write_text(json.dumps({"sessions": [1]}))
    (d / "state.json.bak").write_text(json.dumps({"folders": [{"id": "f", "name": "Bak", "color": "#000", "match": []}]}))
    try:
        st = store.Store(d / "state.json")
    except Exception:  # noqa: BLE001
        return False
    return [f.name for f in st.folders] == ["Bak"] and st.sessions == []


def check_store_bad_settings_uses_backup() -> bool:
    import json

    d = Path(tempfile.mkdtemp())
    (d / "state.json").write_text(json.dumps({"settings": []}))
    (d / "state.json.bak").write_text(json.dumps({"folders": [{"id": "f", "name": "Bak", "color": "#000", "match": []}]}))
    try:
        st = store.Store(d / "state.json")
    except Exception:  # noqa: BLE001
        return False
    return [f.name for f in st.folders] == ["Bak"]


def check_auto_resume_bad_timestamp() -> bool:
    mk = lambda i: SimpleNamespace(id=i, folder="f", cli="c", mux=i, socket=None)  # noqa: E731
    cli = SimpleNamespace(usage={"x": 1}, limited_patterns=[], after_limit="go")
    sent: list[str] = []
    panel = SimpleNamespace(
        store=SimpleNamespace(folders=[SimpleNamespace(id="f", auto_resume=True)], sessions=[mk("a"), mk("b")]),
        registry=SimpleNamespace(types=lambda: {"c": cli}),
        session_state=lambda s: "idle", _limit_at={})
    reads = iter([{"windows": [{"percent": 100, "resets_at": "garbage"}]},
                  {"windows": [{"percent": 100, "resets_at": "2999-01-01T00:00:00+00:00"}]}])
    saved = (tmux.capture, attention.limited_text, usage.read, tmux.send_text)
    tmux.capture, attention.limited_text = lambda *a: "", lambda *a: True
    usage.read, tmux.send_text = lambda *a: next(reads), lambda *a: sent.append(a[0])
    try:
        app.Panel.auto_resume_tick(panel, now=1.0)
    finally:
        tmux.capture, attention.limited_text, usage.read, tmux.send_text = saved
    return "b" in panel._limit_at


def main() -> int:
    ok = True
    for fn in (check_llm_redirect, check_body_413, check_write_temp_unique, check_write_chmod_failure_cleans_up, check_create_rollback,
               check_upload_symlink_swap, check_login_throttle, check_llm_response_cap,
               check_body_must_be_object, check_store_bad_shape_uses_backup, check_store_bad_settings_uses_backup, check_auto_resume_bad_timestamp):
        try:
            good = fn()
        except Exception as exc:  # noqa: BLE001
            print(f"FAIL {fn.__name__}: {exc!r}")
            ok = False
            continue
        print(("ok   " if good else "FAIL ") + fn.__name__)
        ok &= good
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
