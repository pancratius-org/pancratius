"""OpenRouter access for the translation pipeline.

The transport, cost accounting, and value types live in the shared
:mod:`pancratius.openrouter` infrastructure. This module re-exports the slice the
translation pipeline uses and keeps the historical ``TranslatorClient`` name as
an alias of the generic :class:`~pancratius.openrouter.LLMClient` protocol.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from threading import Lock

from pancratius.openrouter import (
    ChatMessage,
    Completion,
    JsonObject,
    LLMClient,
    ModelId,
    ModelPricing,
    OpenRouterClient,
    OpenRouterError,
    Role,
    Usage,
)

# The pipeline and profile stages type against this protocol so they stay
# testable with a stub; it is the generic LLM client under the translation name.
TranslatorClient = LLMClient


class CostCapExceeded(RuntimeError):
    """The run reached its operator-supplied API spending cap."""


class CostCappedClient:
    """Stop before the next API call once completed calls reach a USD cap."""

    def __init__(self, client: TranslatorClient, max_cost_usd: float) -> None:
        if max_cost_usd <= 0:
            raise ValueError("max_cost_usd must be positive")
        self._client = client
        self._max_cost_usd = max_cost_usd
        self._spent_usd = 0.0
        self._lock = Lock()
        self._pricing: dict[ModelId, ModelPricing] = {}

    @property
    def spent_usd(self) -> float:
        with self._lock:
            return self._spent_usd

    def complete(
        self,
        *,
        model: ModelId,
        messages: Sequence[ChatMessage],
        temperature: float,
        max_tokens: int,
        response_format: JsonObject | None = None,
    ) -> Completion:
        # Holding the lock across the call keeps a shared cap exact between calls
        # when the CLI translates several books concurrently.
        with self._lock:
            if self._spent_usd >= self._max_cost_usd:
                raise CostCapExceeded(
                    f"cost cap reached: billed ${self._spent_usd:.4f} of "
                    f"${self._max_cost_usd:.4f}"
                )
            completion = self._client.complete(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                response_format=response_format,
            )
            cost = completion.usage.cost_usd
            if cost is None:
                pricing = self._pricing.get(model)
                if pricing is None:
                    pricing = self._client.fetch_pricing(model)
                    self._pricing[model] = pricing
                cost = pricing.cost(
                    completion.usage.prompt_tokens,
                    completion.usage.completion_tokens,
                    completion.usage.cached_tokens,
                )
                usage = replace(completion.usage, cost_usd=cost)
                completion = replace(completion, usage=usage)
            self._spent_usd += cost
            return completion

    def fetch_pricing(self, model: ModelId) -> ModelPricing:
        return self._client.fetch_pricing(model)

__all__ = [
    "ChatMessage",
    "Completion",
    "CostCapExceeded",
    "CostCappedClient",
    "JsonObject",
    "ModelId",
    "ModelPricing",
    "OpenRouterClient",
    "OpenRouterError",
    "Role",
    "TranslatorClient",
    "Usage",
]
