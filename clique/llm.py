"""One provider-agnostic way to ask a model a question — stdlib urllib only.

No per-provider SDK. Two request shapes cover the field: the OpenAI Chat
Completions API (which OpenRouter, Groq, Together, local Ollama and most others
copied) and the Anthropic Messages API. A provider profile is just
``{kind, base_url, model}`` plus a key that arrives already decrypted from
[secretbox] at call time — this module never stores it, never logs it, and puts
it only in the one header the request needs.

Before anything is sent, the destination is checked: a prompt and a key must
not be POSTed to a cloud metadata endpoint or (by default) an internal-network
host, whatever a profile's ``base_url`` was set to. See ``_guard_url``.
"""

from __future__ import annotations

import ipaddress
import json
import os
import socket
import urllib.error
import urllib.request
from urllib.parse import urlsplit

#: Internal-network hosts are refused by default so a mistyped or hostile
#: base_url cannot turn the panel into a proxy into the operator's LAN. Local
#: models on loopback are always allowed (that is the point of local models);
#: link-local — which is where cloud metadata lives — is always refused.
_ALLOW_PRIVATE = os.environ.get("CLIQUE_LLM_ALLOW_PRIVATE", "").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)

#: Keep a test/probe cheap and bounded — this is a reachability check, not a
#: place to spend tokens.
_PROBE_TOKENS = 16


class LLMError(RuntimeError):
    """A completion could not be produced. The message is safe to show a user
    and never contains the key or the request headers."""


def _blocked(ip: ipaddress._BaseAddress) -> bool:
    """Is this a resolved address we refuse to talk to?

    Order matters: link-local (169.254/16, which carries the cloud metadata
    service) and the other always-dangerous ranges are refused first; loopback
    is then explicitly allowed for local models; anything else private is
    refused unless the operator opted in.
    """
    if ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
        return True
    if ip.is_loopback:
        return False
    if ip.is_private:
        return not _ALLOW_PRIVATE
    return False


def _guard_url(url: str) -> None:
    """Refuse a URL that is not http(s) or that resolves to a blocked address.

    Resolves every address the host maps to and refuses if any is blocked, so a
    host with one public and one internal record cannot smuggle the request in.
    (Pin-to-resolved-IP against DNS rebinding is a noted follow-up; base_url is
    operator-set, so the exposure here is small.)
    """
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise LLMError("provider URL must be http or https")
    host = parts.hostname
    if not host:
        raise LLMError("provider URL has no host")
    port = parts.port or (443 if parts.scheme == "https" else 80)
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise LLMError(f"cannot resolve provider host: {host}") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if _blocked(ip):
            raise LLMError(f"provider host resolves to a blocked address ({ip})")


def _request_for(profile: dict, messages: list, max_tokens: int) -> tuple[str, dict, dict]:
    """Build (url, headers, body) for a profile — the pure shaping step, so the
    two API dialects are checkable without touching the network."""
    kind = profile.get("kind")
    base = (profile.get("base_url") or "").rstrip("/")
    model = profile.get("model")
    key = profile.get("key") or ""
    if not base:
        raise LLMError("provider has no base URL")
    if not model:
        raise LLMError("provider has no model")

    if kind == "openai":
        url = f"{base}/chat/completions"
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        body = {"model": model, "messages": messages, "max_tokens": max_tokens}
    elif kind == "anthropic":
        url = f"{base}/v1/messages"
        headers = {
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        # Anthropic carries the system prompt beside the turns, not as a role.
        system = " ".join(m.get("content", "") for m in messages if m.get("role") == "system")
        body = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": [m for m in messages if m.get("role") != "system"],
        }
        if system:
            body["system"] = system
    else:
        raise LLMError(f"unknown provider kind: {kind!r}")
    return url, headers, body


def _text_of(kind: str, payload: dict) -> str:
    """Pull the assistant's text out of either dialect's response."""
    try:
        if kind == "openai":
            return payload["choices"][0]["message"]["content"]
        if kind == "anthropic":
            for block in payload.get("content", []):
                if block.get("type", "text") == "text":
                    return block["text"]
            return ""
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMError("provider response was not in the expected shape") from exc
    raise LLMError(f"unknown provider kind: {kind!r}")


def complete(profile: dict, messages: list, *, max_tokens: int = 1024, timeout: float = 30) -> str:
    """Ask ``profile``'s model to continue ``messages`` and return the text.

    ``messages`` is the OpenAI role/content shape; it is translated for
    Anthropic. Raises ``LLMError`` — never leaking the key — on a bad URL, a
    blocked destination, a transport failure, or a provider error.
    """
    url, headers, body = _request_for(profile, messages, max_tokens)
    _guard_url(url)
    req = urllib.request.Request(  # noqa: S310 — scheme + destination vetted by _guard_url
        url, data=json.dumps(body).encode("utf-8"), headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — see _guard_url
            payload = json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        raise LLMError(f"provider returned {exc.code}: {_safe_detail(exc)}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        raise LLMError(f"could not reach provider: {reason}") from None
    except json.JSONDecodeError as exc:
        raise LLMError("provider sent a response that was not JSON") from exc
    return _text_of(profile.get("kind"), payload)


def _safe_detail(exc: urllib.error.HTTPError) -> str:
    """A short, bounded slice of a provider's error body — enough to see "bad
    key" or "no such model" without dumping a page into the UI."""
    try:
        raw = exc.read().decode("utf-8", "replace")
    except OSError:
        return exc.reason or "error"
    raw = raw.strip().replace("\n", " ")
    return raw[:200] if raw else (exc.reason or "error")


def test(profile: dict, *, timeout: float = 20) -> dict:
    """A cheap reachability + auth probe for the New-Provider "Test" button.

    Returns ``{"ok": True, "model": ...}`` or ``{"ok": False, "error": ...}`` —
    the error is the same user-safe message ``complete`` would raise.
    """
    try:
        text = complete(
            profile,
            [{"role": "user", "content": "Reply with the single word: ok"}],
            max_tokens=_PROBE_TOKENS,
            timeout=timeout,
        )
    except LLMError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "model": profile.get("model", ""), "sample": (text or "").strip()[:80]}
