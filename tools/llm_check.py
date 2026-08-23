#!/usr/bin/env python3
"""The provider-agnostic LLM client: right request shape, and a real SSRF gate.

No network and no keys needed — the request shaping and response parsing are
pure, and the destination guard is exercised with IP literals so it resolves
offline and deterministically. What matters here is that a prompt and a key are
refused before they can be POSTed to a cloud metadata endpoint or an internal
host, and that no error message ever carries the key.

    python3 tools/llm_check.py

Exit status is 0 on pass, 1 on fail.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from clique import llm


def _raises(fn) -> bool:
    try:
        fn()
    except llm.LLMError:
        return True
    return False


def main() -> int:
    res: dict[str, object] = {}

    # OpenAI dialect: key rides in the Bearer header, path and body as expected.
    oai = {
        "kind": "openai",
        "base_url": "https://api.example.com/v1/",
        "model": "gpt-x",
        "key": "sk-K",
    }
    url, headers, body = llm._request_for(oai, [{"role": "user", "content": "hi"}], 128)
    res["openai_path"] = url == "https://api.example.com/v1/chat/completions"
    res["openai_auth"] = headers.get("Authorization") == "Bearer sk-K"
    res["openai_body"] = (
        body["model"] == "gpt-x" and body["max_tokens"] == 128 and bool(body["messages"])
    )

    # Anthropic dialect: x-api-key + version, and the system turn is lifted out.
    ant = {
        "kind": "anthropic",
        "base_url": "https://api.anthropic.com",
        "model": "claude-x",
        "key": "sk-A",
    }
    msgs = [{"role": "system", "content": "be brief"}, {"role": "user", "content": "hi"}]
    url, headers, body = llm._request_for(ant, msgs, 64)
    res["anthropic_path"] = url == "https://api.anthropic.com/v1/messages"
    res["anthropic_auth"] = headers.get("x-api-key") == "sk-A" and "anthropic-version" in headers
    res["anthropic_system_split"] = body.get("system") == "be brief" and all(
        m["role"] != "system" for m in body["messages"]
    )

    # Misconfiguration is refused, not guessed at.
    res["unknown_kind_raises"] = _raises(lambda: llm._request_for({"kind": "nope"}, [], 8))
    res["no_base_raises"] = _raises(
        lambda: llm._request_for({"kind": "openai", "model": "m"}, [], 8)
    )
    res["no_model_raises"] = _raises(
        lambda: llm._request_for({"kind": "openai", "base_url": "https://x"}, [], 8)
    )

    # Response parsing pulls the text out of either shape.
    res["parse_openai"] = (
        llm._text_of("openai", {"choices": [{"message": {"content": "hello"}}]}) == "hello"
    )
    res["parse_anthropic"] = (
        llm._text_of("anthropic", {"content": [{"type": "text", "text": "hello"}]}) == "hello"
    )
    res["parse_bad_shape_raises"] = _raises(lambda: llm._text_of("openai", {"nope": 1}))

    # The SSRF gate: the whole point of the module's safety.
    res["blocks_metadata"] = _raises(lambda: llm._guard_url("http://169.254.169.254/latest/meta"))
    res["blocks_private"] = _raises(lambda: llm._guard_url("http://10.0.0.5/v1"))
    res["blocks_non_http"] = _raises(lambda: llm._guard_url("ftp://example.com/x"))
    # Loopback (local models like Ollama) and public hosts are allowed through.
    try:
        llm._guard_url("http://127.0.0.1:11434/v1/chat/completions")
        llm._guard_url("https://1.1.1.1/v1/chat/completions")
        res["allows_loopback_and_public"] = True
    except llm.LLMError:
        res["allows_loopback_and_public"] = False

    # complete() must run the gate BEFORE any socket work, and the resulting
    # error must not leak the key that sat in the headers.
    danger = {
        "kind": "openai",
        "base_url": "http://169.254.169.254",
        "model": "m",
        "key": "sk-LEAK",
    }
    try:
        llm.complete(danger, [{"role": "user", "content": "hi"}], timeout=1)
        res["complete_guards_first"] = False
        res["error_hides_key"] = False
    except llm.LLMError as exc:
        res["complete_guards_first"] = "blocked address" in str(exc)
        res["error_hides_key"] = "sk-LEAK" not in str(exc)

    ok = all(res.values())
    for key, value in res.items():
        print(f"  {'ok  ' if value else 'FAIL'} {key}: {value}")
    print("llm_check:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
