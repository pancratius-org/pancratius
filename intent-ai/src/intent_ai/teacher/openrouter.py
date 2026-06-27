# research-pure: the OpenRouter ChatCompleter — the ONE network adapter, on the official SDK.
"""The real `panel.ChatCompleter`: a chat/completions call through the official OpenRouter Python
SDK (`openrouter.OpenRouter().chat.send`). This is the ONLY module that does network I/O — kept off
the panel core so `panel` stays unit-testable with a fake completer.

The SDK is imported LAZILY (inside `__init__`/`complete`), never at module import, so this module
(and the whole package's import-time `get_type_hints` sweep + test suite) loads WITHOUT the SDK
installed; only a live run needs it (`uv run --extra live`).

The SDK already retries 5xx with backoff internally; this adapter adds retry/backoff for the
transient cases it does NOT (HTTP 429 rate-limit, connection-level failures with no response, and raw
httpx transport drops mid-stream — e.g. an incomplete chunked read on a long completion, which the SDK
does not type), and on a non-retryable error surfaces the HTTP/SDK error BODY rather than swallowing it."""
from __future__ import annotations

import os
import threading
import time

from ..identity import ModelId
from .panel import ChatReply, Message

_REFERER = "https://github.com/pankratyus"           # OpenRouter app-attribution headers (rankings)
_TITLE = "pancratius-lineation"


class OpenRouterCompleter:
    """A `panel.ChatCompleter` backed by the official OpenRouter SDK. Reads `OPENROUTER_API_KEY`
    once at construction and FAILS LOUD if it is missing — a live panel run needs it. The SDK
    client is built lazily on first use so importing this module never touches the SDK."""

    def __init__(self, *, timeout: float = 180.0, max_retries: int = 5,
                 backoff_base: float = 2.0, rate_limit_retries: int = 8,
                 rate_limit_backoff: float = 20.0) -> None:
        key = os.environ.get("OPENROUTER_API_KEY")
        if not key:
            raise RuntimeError("OPENROUTER_API_KEY is not set — required for a live panel run")
        self._key = key
        self._timeout_ms = int(timeout * 1000)
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        # A 429 is the per-minute RPM window, not flakiness: it needs a wait long enough to clear
        # the window (~60s), and more attempts, since a busy shared model stays limited for minutes.
        self._rate_limit_retries = rate_limit_retries
        self._rate_limit_backoff = rate_limit_backoff
        self._client = None    # built on first complete() — keeps the SDK import lazy
        self._client_lock = threading.Lock()   # one client build even under a worker pool

    def _sdk(self):
        """Lazily import the SDK and build the client. Importing here (not at module top) keeps the
        module importable without the SDK; only a live call pays for it. The lock makes the one-time
        build safe when `run_panel` fans calls across threads (the SDK's HTTP client itself is shared
        concurrently after that — it is built for it)."""
        if self._client is None:
            with self._client_lock:
                if self._client is None:                    # double-checked: build exactly once
                    from openrouter import OpenRouter       # lazy: a live run only
                    self._client = OpenRouter(
                        api_key=self._key, http_referer=_REFERER, x_open_router_title=_TITLE,
                        timeout_ms=self._timeout_ms)
        return self._client

    def complete(self, *, model: ModelId, messages: list[Message], temperature: float,
                 max_tokens: int, response_format: dict[str, object] | None = None,
                 reasoning_max_tokens: int | None = None) -> ChatReply:
        """One chat completion through the SDK. When `response_format` is given (a structured-output
        JSON schema) it is sent as-is. `reasoning_max_tokens` caps a reasoning model's hidden chain
        (sent as the SDK `reasoning` param) so it cannot spend the whole budget thinking and return
        empty — the ds-flash runaway. We do NOT set `provider.require_parameters` — these models are
        not advertised for require_parameters ROUTING, so that flag 404s ("no endpoints handle the
        requested parameters"). Schema support is therefore BEST-EFFORT (the providers honor it on the
        default route to varying degrees, NOT constrained decoding) — so out-of-set/extra keys are
        still expected and the resolver is the real guard: an invalid key surfaces as a fault rather
        than silently corrupting truth. Retries the transient cases the SDK does not (429 and
        no-response network errors) with linear backoff; any other error (4xx, exhausted retries) is
        raised with its captured body."""
        import httpx                        # lazy: the SDK's transport; its transient errors aren't SDK-typed
        from openrouter import errors      # lazy: the typed SDK errors, a live run only

        extra: dict[str, object] = {}
        if response_format is not None:
            extra["response_format"] = _sdk_response_format(response_format)
        if reasoning_max_tokens is not None:
            extra["reasoning"] = {"max_tokens": reasoning_max_tokens}
        last = ""
        attempt = rate_limit_hits = 0
        while True:
            try:
                res = self._sdk().chat.send(
                    model=model, messages=messages, temperature=temperature,
                    max_completion_tokens=max_tokens, **extra)
                return _reply(res)
            except errors.TooManyRequestsResponseError as e:    # 429 — the RPM window, not flakiness
                last = _err(e)
                rate_limit_hits += 1
                if rate_limit_hits >= self._rate_limit_retries:
                    raise RuntimeError(
                        f"{model}: exhausted {self._rate_limit_retries} rate-limit waits — "
                        f"{last}") from e
                time.sleep(self._rate_limit_backoff)            # wait out the per-minute window
                continue                                        # a 429 does not spend a normal retry
            except errors.OpenRouterError as e:                 # any other HTTP error: capture body
                if 500 <= e.status_code < 600:                  # SDK already retried 5xx; it's spent
                    last = _err(e)
                else:                                           # 4xx (auth/bad-request) — not retryable
                    raise RuntimeError(f"{model}: {_err(e)}") from e
            except errors.NoResponseError as e:                 # connection-level failure — retryable
                last = f"NoResponseError: {e}"
            except httpx.TransportError as e:                   # mid-stream drop / read-timeout / connect — retryable
                last = f"{type(e).__name__}: {e}"
            attempt += 1
            if attempt >= self._max_retries:
                raise RuntimeError(f"{model}: exhausted {self._max_retries} retries — {last}")
            time.sleep(self._backoff_base * attempt)            # linear backoff between retries


def _sdk_response_format(rf: dict[str, object]) -> dict[str, object]:
    """The core emits standard JSON-Schema spelling (`"schema"`); the SDK's pydantic model names that
    field `schema_`. Rename at THIS one adapter boundary so the SDK quirk never leaks into the pure
    contract schemas."""
    js = rf.get("json_schema")
    if not isinstance(js, dict) or "schema" not in js:
        return rf
    return {**rf, "json_schema": {("schema_" if k == "schema" else k): v for k, v in js.items()}}


def _err(e) -> str:
    """A compact, body-bearing message for an SDK HTTP error — the response body is the evidence
    (rate-limit window, provider message, validation detail), so it is never swallowed."""
    return f"{type(e).__name__} {e.status_code}: {(e.body or '')[:300]}"


def _reply(res) -> ChatReply:
    """Map an SDK `ChatResult` to the panel's `ChatReply`. `content` may be `None` or a content-part
    list on some models; coerce to the plain text the parser expects, never fabricating text. A
    structured-output SAFETY REFUSAL arrives as `content=None` + a `refusal` string — surface that as
    the content so it survives as evidence (it parses to zero rows, which the panel refuses on). The
    provider `usage` (token counts + OpenRouter `cost`) is carried verbatim for spend accounting."""
    choice = res.choices[0]
    content = choice.message.content
    if isinstance(content, list):                  # content parts (dicts or SDK models) → text
        content = "".join(_part_text(p) for p in content)
    if not isinstance(content, str) or not content:
        content = getattr(choice.message, "refusal", None) or ""
    return ChatReply(content=content, finish_reason=choice.finish_reason, usage=_usage(res))


def _usage(res) -> dict[str, object] | None:
    """The provider usage record as a plain dict — token counts and (OpenRouter) the call `cost` in
    USD. Pulled defensively (an SDK model or dict, or absent) so spend logging never crashes a run."""
    u = getattr(res, "usage", None)
    if u is None:
        return None
    if isinstance(u, dict):
        return dict(u)
    return {k: getattr(u, k) for k in ("prompt_tokens", "completion_tokens", "total_tokens", "cost")
            if getattr(u, k, None) is not None}


def _part_text(part) -> str:
    """Text of one content part — a dict (`{"text": ...}`) or an SDK pydantic model (`.text`)."""
    return part.get("text", "") if isinstance(part, dict) else (getattr(part, "text", "") or "")
