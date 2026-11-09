# CLIque over MCP

A read-only MCP server on top of the panel's HTTP API. Any MCP client
(Claude, Codex, a local model) can see sessions without this skill file.
It does not type into a pane. Send, spawn, kill and the other writes are
not tools, and will not be until there is a policy for who may type into
a live session.

## Start

The panel is already running. This process only talks to it:

```bash
python3 -m clique token create my-agent --read-only   # once; the value is shown once
CLIQUE_TOKEN=mxp_... python3 -m clique mcp
```

`CLIQUE_URL` overrides the default `http://127.0.0.1:3200`. If `CLIQUE_TOKEN`
is unset the process exits and names the command above. Point the MCP client
at `python3 -m clique mcp` on stdio. It never prints the token.

## Tools

| tool | calls |
|---|---|
| `list_sessions` | `GET /api/state`, the `sessions` array only |
| `get_session` | the same call, filtered to one id |
| `wait` | `GET /api/sessions/<id>/wait?timeout=` (blocks until idle; default 60s, max 300) |
| `preview_pane` | `GET /api/sessions/<id>/peek?lines=` (default 8, capped at 200) |
| `conversation` | `GET /api/sessions/<id>/transcript`, turns as `{cli, name, turns}` |

A bad token or an unknown session comes back as a tool error, not an empty result.
