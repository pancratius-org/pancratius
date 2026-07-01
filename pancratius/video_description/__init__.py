"""Split a raw video description into the site's lede and reading body.

A YouTube description is discovery copy — the real message wrapped in an SEO
opener, hashtags, and a promo footer (book ads, a Telegram channel, a donation
block). This context turns one into the two fields the video page renders: a
`hook` (the lede/SEO copy) and a `body` (a clean Markdown reading), faithfully
and without inventing anything. When the source carries no message, the body is
empty; it is never a raw dump.

The model does the split; :mod:`~pancratius.video_description.qa` gates it and a
deterministic fallback covers a model or API failure, so an unattended sync
always produces a clean, valid draft. This is a *draft* of the author's own
words — the same footing as an AI translation draft, not an editorial judgement.
"""

from __future__ import annotations

from pancratius.openrouter import LLMClient, OpenRouterClient, OpenRouterError, Usage
from pancratius.video_description.config import DEFAULT_MODEL, DescriptionConfig
from pancratius.video_description.engine import draft_description
from pancratius.video_description.models import (
    BodyMarkdown,
    DescriptionDraft,
    Hook,
    RawDescription,
    SplitMethod,
    VideoContext,
)

_CLIENT_TITLE = "Pancratius video sync"


def client_from_env() -> LLMClient | None:
    """The OpenRouter client for enrichment, or None when ``OPENROUTER_API_KEY``
    is unset — in which case the pipeline uses its deterministic fallback."""
    try:
        return OpenRouterClient.from_env(title=_CLIENT_TITLE)
    except OpenRouterError:
        return None


__all__ = [
    "DEFAULT_MODEL",
    "BodyMarkdown",
    "DescriptionConfig",
    "DescriptionDraft",
    "Hook",
    "LLMClient",
    "RawDescription",
    "SplitMethod",
    "Usage",
    "VideoContext",
    "client_from_env",
    "draft_description",
]
