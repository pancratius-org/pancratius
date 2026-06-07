# research-pure: the OpenRouter ChatCompleter — the ONE network adapter (stdlib urllib, no deps).
"""The real `panel.ChatCompleter`: an OpenAI-compatible POST to OpenRouter's chat/completions, with
the API key from `OPENROUTER_API_KEY`. This is the ONLY module that does network I/O — kept off the
panel core so `panel` stays unit-testable with a fake completer. Stdlib `urllib` only (no new dep)."""
from __future__ import annotations

import json
import os
import urllib.request

from ..identity import ModelId
from .panel import ChatReply, Message

_ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"


class OpenRouterCompleter:
    """A `panel.ChatCompleter` backed by OpenRouter. Reads `OPENROUTER_API_KEY` once at construction
    and FAILS LOUD if it is missing — a live panel run needs it."""

    def __init__(self, *, endpoint: str = _ENDPOINT, timeout: float = 120.0) -> None:
        key = os.environ.get("OPENROUTER_API_KEY")
        if not key:
            raise RuntimeError("OPENROUTER_API_KEY is not set — required for a live panel run")
        self._key = key
        self._endpoint = endpoint
        self._timeout = timeout

    def complete(self, *, model: ModelId, messages: list[Message], temperature: float,
                 max_tokens: int) -> ChatReply:
        body = json.dumps({"model": model, "messages": messages,
                           "temperature": temperature, "max_tokens": max_tokens}).encode("utf-8")
        req = urllib.request.Request(
            self._endpoint, data=body, method="POST",
            headers={"Authorization": f"Bearer {self._key}", "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self._timeout) as resp:   # the one network call
            data = json.loads(resp.read())
        choice = data["choices"][0]
        return ChatReply(content=choice["message"]["content"] or "",
                         finish_reason=choice.get("finish_reason"))
