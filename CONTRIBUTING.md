# Contributing

CLIque is a **driver, not an IDE**. That is the filter for every patch.

## Before you write code

1. **Adding a CLI is config, never code.** A block in [`config/clis.toml`](config/clis.toml) and a reload. If your CLI needs a code change to appear, open an issue rather than a per-CLI branch — that is a design failure we want to see.
2. **Filesystem, tmux, and process state only.** If a feature needs to know which vendor is talking, understand its protocol, or interpret what a model thinks, it does not belong in the core. See [ROADMAP.md](ROADMAP.md).
3. **Clean room on Codeman (and everyone else).** Features are fair inspiration. **Source code is not.** Do not read another tool's source to implement something here.

## The API is the whole surface

Every action in the panel is an HTTP call. A feature reachable only by clicking is a feature an agent driving CLIque cannot use.

A new route, settings key, or PATCH-able field means a line in [`API.md`](API.md) **in the same commit.** `python3 tools/api_drift.py` fails otherwise.

## Tests

Not mocked. They talk to a real tmux server and real HTTP.

```bash
python3 tools/smoke.py
python3 tools/smoke_http.py
python3 tools/api_drift.py
ruff check .
```

## Releasing (maintainers)

Bump `__version__` in `clique/__init__.py`, write the `CHANGELOG.md` entry, then run `python3 tools/stamp_changelog.py`. It stamps the wall-clock Pacific time on any heading that lacks one.

## Security

A hole that reaches a terminal is a [private advisory](https://github.com/thejdubb02/clique/security/advisories/new), not a public issue. See [SECURITY.md](SECURITY.md).

## Where requests go, and how they get seen

Issues and discussions are both on. The templates ask for the two things a
report is useless without — what you were trying to do, and which version —
and Settings → About links straight to them with the version and browser
already filled in. Nothing is sent from the panel itself: a tool that phones
home about its own bugs is a tool nobody self-hosts.

On the maintaining side, `tools/feedback.py` pulls open issues, open
discussions and outside mentions into `docs/feedback-inbox.md` and marks what
is new since it last ran. That file is generated and overwritten — decisions
belong in [docs/ideas-inbox.md](docs/ideas-inbox.md) and
[ROADMAP.md](ROADMAP.md), which is the whole point of keeping them apart.
