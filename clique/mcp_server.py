"""Read-only MCP server over CLIque's HTTP API.

Stdio JSON-RPC, stdlib only. Every tool is a GET against a panel that is
already running (``CLIQUE_URL``, default ``http://127.0.0.1:3200``), with
``Authorization: Bearer`` from ``CLIQUE_TOKEN``. Nothing here types into a
pane. Send, spawn, kill and the other writes are a different card: this
file has no POST.

Speaks the initialize handshake (protocol versions through 2025-11-25) and
``server/discover`` for 2026-07-28 clients. ``tools/list`` / ``tools/call``
answer either way.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

from . import version_string

#: Newest first. A client picks one; initialize echoes a requested version
#: we know, otherwise the newest handshake version (2025-11-25).
SUPPORTED = (
    "2026-07-28",
    "2025-11-25",
    "2025-06-18",
    "2025-03-26",
    "2024-11-05",
)
_HANDSHAKE = "2025-11-25"
_INSTRUCTIONS = (
    "Read-only CLIque panel. Tools: list_sessions, get_session, wait, "
    "preview_pane, conversation. They never type into a session."
)

#: Peek asks for a tail, not a transcript. The panel itself clamps to 40;
#: this cap is so a buggy client cannot ask for a megabyte in one call.
_PEEK_DEFAULT = 8
_PEEK_MAX = 200
#: The wait route caps at 300 and defaults to 60. Stay inside that.
_WAIT_MAX = 300

_READ_ONLY = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}
_ID = {"type": "string", "description": "Session id"}


class ProtocolError(Exception):
    """JSON-RPC error. The model is unlikely to fix this by retrying."""

    def __init__(self, code: int, message: str, data: dict | None = None) -> None:
        self.code = code
        self.message = message
        self.data = data


class ToolError(Exception):
    """A tool failure the caller can read. ``isError`` on the result, not a crash."""


class ApiError(Exception):
    def __init__(self, status: int, detail: str) -> None:
        self.status = status
        self.detail = detail


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect would forward the bearer token. Refuse it."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, msg, headers, fp)


def _schema(properties: dict, required: list[str] | None = None) -> dict:
    schema: dict = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        schema["required"] = required
    return schema


def _tools() -> list[dict]:
    return [
        {
            "name": "list_sessions",
            "description": "Every session on the panel, without the rest of /api/state.",
            "inputSchema": _schema({}),
            "annotations": dict(_READ_ONLY),
        },
        {
            "name": "get_session",
            "description": "One session by id, from /api/state. Errors if it is not there.",
            "inputSchema": _schema({"id": _ID}, ["id"]),
            "annotations": dict(_READ_ONLY),
        },
        {
            "name": "wait",
            "description": (
                "Block until the session is idle, or timeout seconds pass "
                "(default 60, max 300). Returns id, state, matched, waited."
            ),
            "inputSchema": _schema(
                {
                    "id": _ID,
                    "timeout": {
                        "type": "number",
                        "minimum": 0,
                        "maximum": _WAIT_MAX,
                        "description": "Seconds to wait (default 60, max 300)",
                    },
                },
                ["id"],
            ),
            "annotations": dict(_READ_ONLY),
        },
        {
            "name": "preview_pane",
            "description": (
                "Last lines of a session pane (default 8, capped at 200). Does not open the tab."
            ),
            "inputSchema": _schema(
                {
                    "id": _ID,
                    "lines": {
                        "type": "number",
                        "minimum": 1,
                        "maximum": _PEEK_MAX,
                        "description": "How many lines to return (default 8, cap 200)",
                    },
                },
                ["id"],
            ),
            "annotations": dict(_READ_ONLY),
        },
        {
            "name": "conversation",
            "description": "The session's conversation as turns: {cli, name, turns}.",
            "inputSchema": _schema({"id": _ID}, ["id"]),
            "annotations": dict(_READ_ONLY),
        },
    ]


_BY_NAME = {tool["name"]: tool for tool in _tools()}


def _server_info() -> dict:
    return {"name": "clique", "version": version_string() or "0"}


def _number(value: object, name: str) -> float:
    # bool is an int subclass. JSON true is not a timeout.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ToolError(f"{name} must be a number")
    return float(value)


def _need_id(args: dict) -> str:
    sid = args.get("id")
    if not isinstance(sid, str) or not sid.strip():
        raise ToolError("id is required")
    return sid


def _lines(args: dict) -> int:
    raw = args.get("lines", _PEEK_DEFAULT)
    if raw is None:
        raw = _PEEK_DEFAULT
    n = int(_number(raw, "lines"))
    if n < 1:
        raise ToolError("lines must be at least 1")
    return min(n, _PEEK_MAX)


def _timeout(args: dict) -> float | None:
    if "timeout" not in args or args["timeout"] is None:
        return None
    n = _number(args["timeout"], "timeout")
    if n < 0:
        raise ToolError("timeout must be at least 0")
    return min(n, float(_WAIT_MAX))


class Client:
    """GET against the panel. There is no method that writes."""

    def __init__(self, base: str, token: str) -> None:
        self.base = base.rstrip("/")
        self._token = token
        # No env proxy: an HTTP_PROXY would otherwise see the bearer token.
        self._opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            _NoRedirect(),
        )

    def scrub(self, text: str) -> str:
        if self._token and self._token in text:
            return text.replace(self._token, "[redacted]")
        return text

    def get(self, path: str, timeout: float) -> object:
        req = urllib.request.Request(  # noqa: S310 — http(s) only, redirects refused
            self.base + path,
            method="GET",
            headers={
                "Authorization": "Bearer " + self._token,
                "Accept": "application/json",
            },
        )
        try:
            with self._opener.open(req, timeout=timeout) as res:
                raw = res.read()
        except urllib.error.HTTPError as exc:
            detail = self._detail(exc.read())
            raise ApiError(exc.code, detail) from None
        except urllib.error.URLError as exc:
            message = f"could not reach CLIque at {self.base}: {exc.reason}"
            raise ApiError(0, self.scrub(message)) from None
        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            raise ApiError(200, "CLIque returned non-JSON") from None

    def _detail(self, raw: bytes) -> str:
        text = raw.decode("utf-8", "replace").strip()
        try:
            body = json.loads(text) if text else None
        except json.JSONDecodeError:
            body = None
        if isinstance(body, dict) and "error" in body:
            err = body["error"]
            text = err if isinstance(err, str) else json.dumps(err, ensure_ascii=False)
        return self.scrub(text)[:300] or "empty response"

    def sessions(self) -> list:
        state = self.get("/api/state", 30)
        if not isinstance(state, dict) or not isinstance(state.get("sessions"), list):
            raise ToolError("CLIque state has no sessions array")
        return state["sessions"]

    def session(self, sid: str) -> dict:
        for row in self.sessions():
            if isinstance(row, dict) and row.get("id") == sid:
                return row
        raise ToolError(self.scrub(f"no session with id {sid}"))

    def wait(self, sid: str, timeout: float | None) -> object:
        # Default `for` is the route's own (idle). Timeout is the only knob
        # this tool takes; the socket has to outlive the panel's cap.
        query = ""
        http_timeout = 75.0
        if timeout is not None:
            query = "?" + urllib.parse.urlencode({"timeout": str(timeout)})
            http_timeout = min(timeout, float(_WAIT_MAX)) + 15
        path = f"/api/sessions/{urllib.parse.quote(sid, safe='')}/wait{query}"
        return self.get(path, http_timeout)

    def peek(self, sid: str, lines: int) -> object:
        query = urllib.parse.urlencode({"lines": str(lines)})
        return self.get(
            f"/api/sessions/{urllib.parse.quote(sid, safe='')}/peek?{query}",
            30,
        )

    def transcript(self, sid: str) -> object:
        return self.get(f"/api/sessions/{urllib.parse.quote(sid, safe='')}/transcript", 30)


def _tool_result(payload: object, *, error: bool) -> dict:
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    result: dict = {
        "resultType": "complete",
        "content": [{"type": "text", "text": text}],
        "isError": error,
    }
    if not error:
        result["structuredContent"] = payload
    return result


def _call_tool(client: Client, name: str, args: dict) -> dict:
    try:
        if name == "list_sessions":
            payload: object = client.sessions()
        elif name == "get_session":
            payload = client.session(_need_id(args))
        elif name == "wait":
            payload = client.wait(_need_id(args), _timeout(args))
        elif name == "preview_pane":
            payload = client.peek(_need_id(args), _lines(args))
        else:
            payload = client.transcript(_need_id(args))
    except ToolError as exc:
        return _tool_result(client.scrub(str(exc)), error=True)
    except ApiError as exc:
        if exc.status:
            return _tool_result(f"CLIque API returned {exc.status}: {exc.detail}", error=True)
        return _tool_result(exc.detail, error=True)
    return _tool_result(payload, error=False)


def _initialize(params: dict) -> dict:
    requested = params.get("protocolVersion")
    version = requested if isinstance(requested, str) and requested in SUPPORTED else _HANDSHAKE
    return {
        "protocolVersion": version,
        "capabilities": {"tools": {"listChanged": False}},
        "serverInfo": _server_info(),
        "instructions": _INSTRUCTIONS,
    }


def _discover() -> dict:
    return {
        "resultType": "complete",
        "supportedVersions": list(SUPPORTED),
        "capabilities": {"tools": {"listChanged": False}},
        "_meta": {"io.modelcontextprotocol/serverInfo": _server_info()},
        "instructions": _INSTRUCTIONS,
        "ttlMs": 3_600_000,
        "cacheScope": "public",
    }


def _tools_list() -> dict:
    return {
        "resultType": "complete",
        "tools": _tools(),
        "ttlMs": 300_000,
        "cacheScope": "public",
    }


def _on_call(client: Client, params: dict) -> dict:
    name = params.get("name")
    if not isinstance(name, str) or name not in _BY_NAME:
        shown = name if isinstance(name, str) and name else "missing"
        raise ProtocolError(-32602, f"Unknown tool: {shown}")
    args = params.get("arguments", {})
    if args is None:
        args = {}
    if not isinstance(args, dict):
        raise ProtocolError(-32602, "arguments must be an object")
    return _call_tool(client, name, args)


def _send(msg: dict) -> None:
    # One JSON object per line. stdout is the protocol; nothing else may write it.
    line = json.dumps(msg, ensure_ascii=False, separators=(",", ":"))
    sys.stdout.buffer.write(line.encode("utf-8") + b"\n")
    sys.stdout.buffer.flush()


def _ok(mid: object, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def _fail(mid: object, code: int, message: str, data: dict | None = None) -> dict:
    err: dict = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": mid, "error": err}


def _version_error(params: dict) -> dict | None:
    meta = params.get("_meta")
    if not isinstance(meta, dict):
        return None
    version = meta.get("io.modelcontextprotocol/protocolVersion")
    if not isinstance(version, str) or version in SUPPORTED:
        return None
    return {
        "supported": list(SUPPORTED),
        "requested": version,
    }


def _handle(msg: dict, client: Client) -> None:
    method = msg.get("method")
    has_id = "id" in msg and msg["id"] is not None
    mid = msg.get("id")
    if not isinstance(method, str):
        if has_id:
            _send(_fail(mid, -32600, "invalid request"))
        return
    # A notification (no id, or notifications/*) gets no reply.
    if method.startswith("notifications/") or not has_id:
        return
    params = msg.get("params")
    if params is None:
        params = {}
    if not isinstance(params, dict):
        _send(_fail(mid, -32602, "invalid params"))
        return
    bad = _version_error(params)
    if bad is not None:
        _send(_fail(mid, -32022, "Unsupported protocol version", bad))
        return
    try:
        if method == "initialize":
            result = _initialize(params)
        elif method == "ping":
            result = {}
        elif method == "server/discover":
            result = _discover()
        elif method == "tools/list":
            result = _tools_list()
        elif method == "tools/call":
            result = _on_call(client, params)
        else:
            _send(_fail(mid, -32601, f"method not found: {method}"))
            return
    except ProtocolError as exc:
        _send(_fail(mid, exc.code, exc.message, exc.data))
        return
    except Exception:  # noqa: BLE001 — one bad call must not kill the stdio session
        # Type only. The message can carry a URL, and must never carry the token.
        print("mcp: internal error", file=sys.stderr)
        _send(_fail(mid, -32603, "internal error"))
        return
    _send(_ok(mid, result))


def serve(client: Client) -> int:
    for raw in sys.stdin.buffer:
        line = raw.strip()
        if not line:
            continue
        try:
            msg = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            _send(_fail(None, -32700, "parse error"))
            continue
        if isinstance(msg, dict):
            _handle(msg, client)
        else:
            # 2025-06-18 dropped JSON-RPC batching. One object per line.
            _send(_fail(None, -32600, "invalid request"))
    return 0


def main() -> int:
    token = os.environ.get("CLIQUE_TOKEN", "").strip()
    if not token:
        print(
            "CLIQUE_TOKEN is not set. Create one with: python3 -m clique token create <name>",
            file=sys.stderr,
        )
        return 1
    base = os.environ.get("CLIQUE_URL", "").strip() or "http://127.0.0.1:3200"
    if not base.startswith(("http://", "https://")):
        print("CLIQUE_URL must be an http or https URL", file=sys.stderr)
        return 1
    return serve(Client(base, token))
