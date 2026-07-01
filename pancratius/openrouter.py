"""A small typed OpenRouter chat client over the standard library.

Shared infrastructure for every corpus tool that reaches OpenRouter (book
translation, video description drafting, …). Rather than add an HTTP dependency
to the deliberately light package core, this wraps ``urllib`` with the few
things those pipelines actually need: bearer auth from the environment, prompt
caching markers (so a stable read-only prefix is billed once at the cache-read
rate for the rest of a run), retry/backoff on rate limits, structured output via
``response_format``, and a typed ``Usage`` so cost accounting reads real
per-call token counts back from the API.

Pricing is fetched live from the public ``/models`` endpoint — never hardcoded —
because OpenRouter prices drift. This module owns the transport and the value
types; callers own their prompts, schemas, and stage models.
"""

from __future__ import annotations

import http.client
import json
import logging
import os
import time
import urllib.error
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol

logger = logging.getLogger(__name__)

_DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"

# An OpenRouter model slug (`google/gemini-2.5-flash-lite`, …). A distinct domain
# name so a model id never silently swaps with an arbitrary string in a signature.
type ModelId = str

type Role = Literal["system", "user", "assistant"]
# An untyped JSON-API payload (request body, response envelope, schema blob);
# narrowed by `.get` at each use. The OpenRouter response schema is open enough
# that pinning a TypedDict would lie about fields the provider may add or omit.
type JsonObject = dict[str, Any]


class OpenRouterError(RuntimeError):
    """An OpenRouter call failed (auth, transport, or a malformed reply)."""


@dataclass(frozen=True, slots=True)
class ModelPricing:
    """Per-million-token USD prices for one model (cache-read may be absent)."""

    input_per_mtok: float
    output_per_mtok: float
    cached_input_per_mtok: float | None

    def cost(self, prompt_tokens: int, completion_tokens: int, cached_tokens: int) -> float:
        cache_rate = self.cached_input_per_mtok
        if cache_rate is None:
            cached_tokens = 0
            cache_rate = 0.0
        fresh = max(prompt_tokens - cached_tokens, 0)
        return (
            fresh * self.input_per_mtok
            + cached_tokens * cache_rate
            + completion_tokens * self.output_per_mtok
        ) / 1_000_000


@dataclass(frozen=True, slots=True)
class Usage:
    prompt_tokens: int
    completion_tokens: int
    cached_tokens: int
    cost_usd: float | None

    @staticmethod
    def empty() -> Usage:
        return Usage(0, 0, 0, 0.0)

    def __add__(self, other: Usage) -> Usage:
        left, right = self.cost_usd, other.cost_usd
        cost = None if left is None or right is None else left + right
        return Usage(
            self.prompt_tokens + other.prompt_tokens,
            self.completion_tokens + other.completion_tokens,
            self.cached_tokens + other.cached_tokens,
            cost,
        )


@dataclass(frozen=True, slots=True)
class ChatMessage:
    """One message. ``cache`` marks its content as a prompt-cache breakpoint so a
    stable prefix (a style guide, a long read-only reference) is reused across a
    run's calls."""

    role: Role
    content: str
    cache: bool = False


@dataclass(frozen=True, slots=True)
class Completion:
    text: str
    usage: Usage
    model: ModelId
    # True when the provider stopped on the token cap (finish_reason "length") —
    # the reply is very likely cut off mid-content.
    truncated: bool = False


class LLMClient(Protocol):
    """The slice of the client a pipeline depends on. Typing against this (not
    the concrete class) keeps a pipeline testable with a stub and inverts the
    network dependency."""

    def complete(
        self,
        *,
        model: ModelId,
        messages: Sequence[ChatMessage],
        temperature: float,
        max_tokens: int,
        response_format: JsonObject | None = None,
        reasoning_max_tokens: int | None = None,
    ) -> Completion: ...

    def fetch_pricing(self, model: ModelId) -> ModelPricing: ...


def _message_payload(message: ChatMessage) -> JsonObject:
    # DeepSeek caches automatically on a stable prefix, so what actually secures
    # the hits is message ORDER: constant prefix first, varying instruction last.
    # The explicit breakpoint below is for providers that need a manual marker
    # (e.g. Anthropic, Gemini) and is a harmless no-op for auto-caching ones.
    if not message.cache:
        return {"role": message.role, "content": message.content}
    return {
        "role": message.role,
        "content": [
            {"type": "text", "text": message.content, "cache_control": {"type": "ephemeral"}}
        ],
    }


def _usage_from(raw: JsonObject) -> Usage:
    details = raw.get("prompt_tokens_details") or {}
    cached = int(details.get("cached_tokens") or 0)
    cost = raw.get("cost")
    return Usage(
        prompt_tokens=int(raw.get("prompt_tokens") or 0),
        completion_tokens=int(raw.get("completion_tokens") or 0),
        cached_tokens=cached,
        cost_usd=float(cost) if cost is not None else None,
    )


class OpenRouterClient:
    """Synchronous OpenRouter client. One instance per run; thread-safe for the
    stateless ``complete`` calls a pipeline makes."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = _DEFAULT_BASE_URL,
        timeout: float = 180.0,
        max_retries: int = 5,
        backoff_base: float = 2.0,
        rate_limit_retries: int = 8,
        rate_limit_backoff: float = 20.0,
        referer: str = "https://pancratius.local",
        title: str = "Pancratius",
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        # A 429 is the per-minute RPM window, not flakiness: wait long enough to
        # clear it (~20s) over more attempts, and don't let it spend a normal retry.
        self._rate_limit_retries = rate_limit_retries
        self._rate_limit_backoff = rate_limit_backoff
        self._referer = referer
        self._title = title

    @classmethod
    def from_env(cls, *, title: str = "Pancratius") -> OpenRouterClient:
        key = os.environ.get("OPENROUTER_API_KEY")
        if not key:
            raise OpenRouterError(
                "OPENROUTER_API_KEY is not set; export it (e.g. from .env) first."
            )
        return cls(api_key=key, title=title)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": self._referer,
            "X-Title": self._title,
        }

    def _post(self, path: str, payload: JsonObject) -> JsonObject:
        data = json.dumps(payload).encode("utf-8")
        url = f"{self._base_url}{path}"
        attempt = rate_limit_hits = 0
        last = "?"
        while True:
            request = urllib.request.Request(url, data=data, headers=self._headers(), method="POST")
            try:
                with urllib.request.urlopen(request, timeout=self._timeout) as response:
                    return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                if exc.code == 429:  # rate-limit window: long wait, own budget, no normal retry spent
                    rate_limit_hits += 1
                    if rate_limit_hits >= self._rate_limit_retries:
                        raise OpenRouterError(
                            f"429 rate limit not clearing after {rate_limit_hits} waits: {url}"
                        ) from exc
                    wait = _retry_after(exc) or self._rate_limit_backoff
                    logger.warning("openrouter 429; waiting %.0fs (%d)", wait, rate_limit_hits)
                    time.sleep(wait)
                    continue
                if not 500 <= exc.code < 600:  # 4xx (auth/bad request): not retryable
                    body = exc.read().decode("utf-8", "replace")[:500]
                    raise OpenRouterError(f"HTTP {exc.code} from {url}: {body}") from exc
                last = f"HTTP {exc.code}"
            except (urllib.error.URLError, http.client.HTTPException, ConnectionError, TimeoutError) as exc:
                # Transport-level transients incl. IncompleteRead (a response body cut
                # off mid-stream): retryable.
                last = f"transport error: {type(exc).__name__}: {exc}"
            except json.JSONDecodeError:
                # A 200 with a non-JSON body (gateway hiccup / overload): retry it.
                last = "malformed JSON response body"
            attempt += 1
            if attempt >= self._max_retries:
                raise OpenRouterError(f"exhausted {self._max_retries} retries to {url}: {last}")
            # Linear backoff (base·attempt: 2s, 4s, 6s, …) — transient 5xx/transport
            # errors clear fast; the per-minute 429 window has its own longer wait.
            time.sleep(self._backoff_base * attempt)

    def complete(
        self,
        *,
        model: ModelId,
        messages: Sequence[ChatMessage],
        temperature: float,
        max_tokens: int,
        response_format: JsonObject | None = None,
        reasoning_max_tokens: int | None = None,
    ) -> Completion:
        payload: JsonObject = {
            "model": model,
            "messages": [_message_payload(m) for m in messages],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "usage": {"include": True},
        }
        if response_format is not None:
            payload["response_format"] = response_format
        if reasoning_max_tokens is not None:
            # Cap the hidden chain so a reasoning model can't spend the whole budget
            # thinking and return empty content.
            payload["reasoning"] = {"max_tokens": reasoning_max_tokens}
        body = self._post("/chat/completions", payload)
        choices = body.get("choices") or []
        if not choices:
            raise OpenRouterError(f"no choices in response: {json.dumps(body)[:400]}")
        content = choices[0].get("message", {}).get("content")
        if content is None:
            # A reasoning model can exhaust max_tokens before emitting content
            # (finish_reason "length"). Return empty text and let the caller decide.
            content = ""
        if not isinstance(content, str):
            raise OpenRouterError("response message had non-string content")
        return Completion(
            text=content,
            usage=_usage_from(body.get("usage") or {}),
            model=model,
            truncated=choices[0].get("finish_reason") == "length",
        )

    def fetch_pricing(self, model: ModelId) -> ModelPricing:
        request = urllib.request.Request(
            f"{self._base_url}/models", headers={"User-Agent": "pancratius"}, method="GET"
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                catalog = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, json.JSONDecodeError) as exc:
            raise OpenRouterError(f"could not fetch model pricing: {exc}") from exc
        for entry in catalog.get("data", []):
            if entry.get("id") == model:
                pricing = entry.get("pricing") or {}
                cache_read = pricing.get("input_cache_read")
                return ModelPricing(
                    input_per_mtok=float(pricing.get("prompt") or 0.0) * 1_000_000,
                    output_per_mtok=float(pricing.get("completion") or 0.0) * 1_000_000,
                    cached_input_per_mtok=(
                        float(cache_read) * 1_000_000 if cache_read not in (None, "", "0") else None
                    ),
                )
        raise OpenRouterError(f"model not found in OpenRouter catalog: {model}")


def _retry_after(exc: urllib.error.HTTPError) -> float | None:
    raw = exc.headers.get("Retry-After") if exc.headers else None
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None
