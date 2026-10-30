"""LLM provider routes for the HTTP request handler."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler
from typing import Any, Callable


class ProviderRoutes(BaseHTTPRequestHandler):
    """LLM provider methods mixed into :class:`clique.app.Handler`."""

    # Provided by clique.app.Handler.
    panel: Any
    _json: Callable[..., Any]

    def _provider_GET(self, path: str) -> bool:
        if path == "/api/llm/providers":
            self._json(self.panel.llm_list())
            return True
        return False

    def _provider_POST(self, path: str, body: dict) -> bool:
        if path == "/api/llm/providers":
            self._json(self.panel.llm_create(body), 201)
            return True
        if path.startswith("/api/llm/providers/"):
            parts = path.split("/")
            provider_id = parts[4]
            action = parts[5] if len(parts) > 5 else ""
            if action == "test":
                result = self.panel.llm_test(provider_id)
            elif action == "delete":
                result = self.panel.llm_delete(provider_id)
            elif not action:
                result = self.panel.llm_update(provider_id, body)
            else:
                self._json({"error": "not found"}, 404)
                return True
            if result is None:
                self._json({"error": "no such provider"}, 404)
                return True
            self._json(result)
            return True
        return False
