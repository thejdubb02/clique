# Security model

CLIque serves a **terminal running as root** over a browser. That is not a
side effect; it is the product. So the security model is not about hardening a
web app — it is about controlling exactly who reaches that terminal, and
assuming that anyone who does has the box.

This document exists because the honest version of that sentence should be
written down rather than discovered.

## The perimeter

| Layer | What it does |
|---|---|
| **Bind address** | `127.0.0.1:3200`. Nothing reaches the process except through the tunnel. |
| **Tunnel** | Tailscale Serve, Caddy, or nginx terminates TLS. CLIque itself never binds the public internet. |
| **Host allowlist** | Rejects any `Host` we do not answer to, **before auth or any handler**. |
| **Password** | Mandatory. Stored as an scrypt hash; the process cannot recover the plaintext. |
| **Session cookie** | HMAC-signed, `HttpOnly`, `SameSite=Lax`, `Secure` whenever the request arrived over HTTPS (incl. behind a TLS tunnel), 30-day TTL. |
| **API tokens** | Separate credential for agents, scoped read, read/write, or `attention`-only, revocable individually. |
| **Origin checks** | On every state-changing request *and* on the WebSocket upgrade. |

## The four attacks this is actually built against

**1. DNS rebinding.** An attacker points their domain at `127.0.0.1`, gets you
to open their page, and the browser then treats requests to their domain as
same-origin with *their* script — bypassing every same-origin protection at
once. The only fix is to refuse on the `Host` header before a handler runs,
which is what `host_allowed()` does. Allowed: loopback, IP literals, `.ts.net`,
the usual tunnel providers, and anything named in `CLIQUE_ALLOWED_HOSTS`.

**2. Cross-Site WebSocket Hijacking.** A WebSocket handshake is not subject to
CORS, and `SameSite=Lax` does not cover it — so a hostile page can open a
socket to this origin and the browser will attach the session cookie. Without
an `Origin` check on the upgrade, that page has a live root terminal. This is
the single most dangerous hole a browser terminal can have, and it is checked
before the handshake completes.

**3. CSRF.** A cookie is attached by the browser to any request to this origin,
including one a hostile page triggered, so a cookie alone does not prove
intent. State-changing requests need a matching `Origin`. A *missing* Origin is
allowed, so `curl` and agents keep working; an opaque `null` origin is
rejected, because that is a sandboxed frame rather than our page. Token
requests skip the check entirely — browsers never attach an `Authorization`
header on their own, which is what makes a token proof of authorship.

**4. Password guessing.** Failures are counted per address with a decay window.
Crucially, **a correct password always gets through, even while throttled**.
Behind a tunnel every request arrives from the same loopback address, so a
per-IP lockout would lock out the only legitimate user along with the attacker
— a denial of service dressed as a protection.

## Credentials

All three live under `$CLIQUE_HOME` (set this to `~/.clique` unless you have a
reason not to). Mode 0600.

- **Login password** — `password`, an scrypt hash. The server only ever
  verifies, so keeping the plaintext in this file buys nothing. Set it with
  `python3 -m clique password`. Keep the plaintext in a password manager, not
  next to the hash.
- **Cookie signing secret** — `secret`, 32 random bytes, persisted so a restart
  does not log everyone out. Deleting it invalidates every session, which is
  the "log out everywhere" lever.
- **API tokens** — `tokens.json`, SHA-256 hashes only. A leaked file is a list
  of names and dates, not working keys. Minted with
  `python3 -m clique token create`, never through the API: an endpoint that
  mints credentials turns any other hole into permanent access.
- **State-hook token** — a per-session, `attention`-scoped token handed to each
  launched session in its own environment (`$CLIQUE_TOKEN`) so a state hook can
  report to `/api/sessions/<id>/attention`. The scopes are enforced on *both*
  sides: every `GET /api/*` and the `/ws` attach require a `read` scope, every
  write requires `write`. So this token reaches only that one status nudge — an
  agent that reads its own `$CLIQUE_TOKEN`, or a prompt-injected one, cannot
  spawn a shell, drive a session, or read a terminal, transcript, or host file
  with it. And because each session's token is **bound to its session id**, a
  token exfiltrated from one pane can nudge only *its own* status — not another
  session's. It is minted fresh when the pane is created (re-minted on resume)
  and revoked when the session is deleted, so it is individually revocable and
  dies with the session that held it. Tokens on panes launched before this model
  existed are unbound and still accepted for any session, until those sessions
  are recreated.
- **BYOK model keys** — optional, and off unless you add one. A provider's key
  is encrypted at rest with AES-256-GCM before it is written; the data key is a
  separate `0600` `secret.key` under `$CLIQUE_HOME`, so lifting `state.json`
  (from a backup or a sync) yields ciphertext, not a key. The crypto comes from
  the opt-in extra (`pip install 'clique-panel[llm]'`); with it absent, a key is
  refused rather than stored in the clear. The plaintext key is never returned
  by any endpoint (only whether one is set), never logged, and never rides
  `/api/state`. Outbound calls to a provider refuse a `base_url` that resolves
  to a cloud-metadata / link-local address or, by default, an internal-network
  host — so a mistyped or hostile URL cannot turn the panel into an SSRF proxy;
  loopback stays open for local models. What we do **not** yet do: pin the
  connection to the resolved IP (DNS-rebinding on an operator-set URL is the
  residual), and a model's own output is treated as data — the copilot's
  "run this" is always a draft an operator approves, never auto-run.

## Where we are stronger than the tool we replaced

- **No dependency surface.** Zero third-party packages against ~790 MB of
  `node_modules`. Most real-world compromise of self-hosted tools arrives
  through a transitive dependency, and we do not have any.
- **No build step**, so nothing is generated between what is reviewed and what
  runs.
- **Least privilege by construction.** `kill()` refuses any tmux session that
  is not ours unless explicitly forced. Sessions live on our own tmux socket,
  so a bug here cannot reach another tool's work.
- **Nothing goes through a shell.** Every tmux call is an argv list, so a
  session name or path containing a quote is data, not a command.
- **Read-only API tokens**, so an agent that only needs to look cannot act.

## Where we are still behind, honestly

- **Sessions are not individually revocable.** The cookie is stateless HMAC, so
  the only revocation is rotating the secret, which logs out every device.
  Codeman keeps server-side opaque tokens and can drop one. *Worth adopting.*
- **No schema validation library.** Input is clamped and filtered by hand.
  It is careful, but Zod-style declarative validation is harder to get wrong.
- **File previews are fenced, and never serve a credential.** `GET …/file`
  previews a path an agent printed. Directory containment is `realpath` *after*
  resolution — a symlink cannot smuggle a path out of the session's working
  directory — and can be opted out on a trusted-local box with
  `CLIQUE_FENCE_READS=0`. The credential blocklist is *not* part of that
  opt-out: `.env` and its whole family (`.env.local`, …), private-key material
  (`.pem`/`.key`/`.p12`), token dotfiles (`.npmrc`, `.pypirc`, `.bw-session`),
  and whole credential directories (`.ssh`, `.aws`, `.gnupg`, …) are refused
  whether or not the fence is on. Paste and artifact routes contain to the
  working tree unconditionally and serve with `nosniff`. Arbitrary
  upload/download is still not offered.
- **No audit log.** Who logged in, from where, and when is not recorded.
- **The review diff shows untracked files.** `GET …/diff` needs only read
  scope and renders untracked, non-gitignored files in full — so an `.env` the
  agent left sitting in the working tree is visible to a read-only viewer.
  Gitignored files are never shown. Reasonable for a single-user tool; worth
  knowing before sharing a read-only link.
- **No multi-user model**, deliberately — this is one person's tool.

## Reporting

Open a [GitHub security advisory](https://github.com/thejdubb02/clique/security/advisories/new)
if it is exploitable. Anything else can be an issue. Do not file a public issue
for a hole that reaches a terminal.
