"""OpenRouter access for the translation pipeline.

The transport, cost accounting, and value types live in the shared
:mod:`pancratius.openrouter` infrastructure. This module re-exports the slice the
translation pipeline uses and keeps the historical ``TranslatorClient`` name as
an alias of the generic :class:`~pancratius.openrouter.LLMClient` protocol.
"""

from __future__ import annotations

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

__all__ = [
    "ChatMessage",
    "Completion",
    "JsonObject",
    "ModelId",
    "ModelPricing",
    "OpenRouterClient",
    "OpenRouterError",
    "Role",
    "TranslatorClient",
    "Usage",
]
