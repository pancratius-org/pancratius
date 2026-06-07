# research-pure: the OpenRouter ChatCompleter — the ONE network adapter, on the official SDK.
"""The real `panel.ChatCompleter`: a chat/completions call through the official OpenRouter Python
SDK (`openrouter.OpenRouter().chat.send`). This is the ONLY module that does network I/O — kept off
the panel core so `panel` stays unit-testable with a fake completer.

The SDK is imported LAZILY (inside `__init__`/`complete`), never at module import, so this module
(and the whole package's import-time `get_type_hints` sweep + test suite) loads WITHOUT the SDK
installed; only a live run needs it (`uv run --extra live`).

The SDK already retries 5xx with backoff internally; this adapter adds retry/backoff for the
transient cases it does NOT (HTTP 429 rate-limit, and connection-level failures with no response),
and on a non-retryable error surfaces the HTTP/SDK error BODY rather than swallowing it."""
from __future__ import annotations

import os
import time

from ..identity import ModelId
from .panel import ChatReply, Message

_REFERER = "https://github.com/litdocs/pancratius"   # OpenRouter app-attribution headers (rankings)
_TITLE = "pancratius-lineation"


class OpenRouterCompleter:
    """A `panel.ChatCompleter` backed by the official OpenRouter SDK. Reads `OPENROUTER_API_KEY`
    once at construction and FAILS LOUD if it is missing — a live panel run needs it. The SDK
    client is built lazily on first use so importing this module never touches the SDK."""

    def __init__(self, *, timeout: float = 180.0, max_retries: int = 5,
                 backoff_base: float = 2.0) -> None:
        key = os.environ.get("OPENROUTER_API_KEY")
        if not key:
            raise RuntimeError("OPENROUTER_API_KEY is not set — required for a live panel run")
        self._key = key
        self._timeout_ms = int(timeout * 1000)
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self._client = None    # built on first complete() — keeps the SDK import lazy

    def _sdk(self):
        """Lazily import the SDK and build the client. Importing here (not at module top) keeps the
        module importable without the SDK; only a live call pays for it."""
        if self._client is None:
            from openrouter import OpenRouter   # lazy: a live run only
            self._client = OpenRouter(
                api_key=self._key, http_referer=_REFERER, x_open_router_title=_TITLE,
                timeout_ms=self._timeout_ms)
        return self._client

    def complete(self, *, model: ModelId, messages: list[Message], temperature: float,
                 max_tokens: int) -> ChatReply:
        """One chat completion through the SDK. Retries the transient cases the SDK does not (429
        and no-response network errors) with linear backoff; any other error (4xx, exhausted
        retries) is raised with its captured body."""
        from openrouter import errors      # lazy: the typed SDK errors, a live run only

        last = ""
        for attempt in range(self._max_retries):
            try:
                res = self._sdk().chat.send(
                    model=model, messages=messages, temperature=temperature,
                    max_completion_tokens=max_tokens)
                return _reply(res)
            except errors.TooManyRequestsResponseError as e:    # 429 — retryable rate limit
                last = _err(e)
            except errors.OpenRouterError as e:                 # any other HTTP error: capture body
                if 500 <= e.status_code < 600:                  # SDK already retried 5xx; it's spent
                    last = _err(e)
                else:                                           # 4xx (auth/bad-request) — not retryable
                    raise RuntimeError(f"{model}: {_err(e)}") from e
            except errors.NoResponseError as e:                 # connection-level failure — retryable
                last = f"NoResponseError: {e}"
            if attempt < self._max_retries - 1:                 # no pointless sleep before the raise
                time.sleep(self._backoff_base * (attempt + 1))  # linear backoff between retries
        raise RuntimeError(f"{model}: exhausted {self._max_retries} retries — {last}")


def _err(e) -> str:
    """A compact, body-bearing message for an SDK HTTP error — the response body is the evidence
    (rate-limit window, provider message, validation detail), so it is never swallowed."""
    return f"{type(e).__name__} {e.status_code}: {(e.body or '')[:300]}"


def _reply(res) -> ChatReply:
    """Map an SDK `ChatResult` to the panel's `ChatReply`. `content` may be `None` or a content-part
    list on some models; coerce to the plain text the parser expects, never fabricating text."""
    choice = res.choices[0]
    content = choice.message.content
    if isinstance(content, list):                  # content parts (dicts or SDK models) → text
        content = "".join(_part_text(p) for p in content)
    return ChatReply(content=content if isinstance(content, str) else "",
                     finish_reason=choice.finish_reason)


def _part_text(part) -> str:
    """Text of one content part — a dict (`{"text": ...}`) or an SDK pydantic model (`.text`)."""
    return part.get("text", "") if isinstance(part, dict) else (getattr(part, "text", "") or "")
