"""Orchestration: model → QA → retry → deterministic fallback.

`draft_description` is the one entry point. Given a raw description and its context
it returns a clean :class:`DescriptionDraft` and the token :class:`Usage` spent. It
asks the model for a structured split, checks it with :mod:`qa`, and on a blocking
violation re-prompts the model with the exact complaint. If the model never
clears QA (or there is no client, or the API fails), it returns the deterministic
fallback — the sync always gets a clean, valid draft.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import replace
from difflib import SequenceMatcher

from pancratius.openrouter import LLMClient, OpenRouterError, Usage
from pancratius.video_description.config import DescriptionConfig
from pancratius.video_description.fallback import deterministic_split
from pancratius.video_description.models import (
    DescriptionDraft,
    RawDescription,
    SplitMethod,
    VideoContext,
)
from pancratius.video_description.prompts import RESPONSE_FORMAT, build_messages
from pancratius.video_description.qa import verify

logger = logging.getLogger(__name__)

_JSON_OBJECT = re.compile(r"\{.*\}", re.DOTALL)
_RETRY_JSON = "Return ONLY the JSON object with keys hook, body_markdown, dropped — no prose, no code fences, and do not truncate it."


def draft_description(
    raw: RawDescription,
    context: VideoContext,
    *,
    client: LLMClient | None,
    config: DescriptionConfig | None = None,
) -> tuple[DescriptionDraft, Usage]:
    config = config or DescriptionConfig()
    if client is None:
        return deterministic_split(raw, context, config), Usage.empty()

    usage = Usage.empty()
    feedback: str | None = None
    for attempt in range(1, config.attempts + 1):
        try:
            completion = client.complete(
                model=config.model,
                messages=build_messages(context, raw, feedback=feedback),
                temperature=config.temperature,
                max_tokens=config.max_tokens,
                response_format=RESPONSE_FORMAT,
            )
        except OpenRouterError as exc:
            logger.warning("editorial: API error on attempt %d, using fallback: %s", attempt, exc)
            break
        usage += completion.usage

        draft = _parse(completion.text)
        if draft is None or completion.truncated:
            logger.info("editorial: unparseable/truncated reply on attempt %d, retrying", attempt)
            feedback = _RETRY_JSON
            continue

        verdict = verify(draft, raw, context, config)
        if verdict.ok:
            return _tidy(draft, context), usage
        logger.info(
            "editorial: QA rejected attempt %d (%s)",
            attempt,
            ", ".join(v.code.value for v in verdict.violations if v.blocking),
        )
        feedback = verdict.feedback()

    logger.warning("editorial: model did not clear QA in %d attempts, using fallback", config.attempts)
    return deterministic_split(raw, context, config), usage


def _tidy(draft: DescriptionDraft, context: VideoContext) -> DescriptionDraft:
    """Drop a sub-minute short's body when it merely restates the hook — the lede
    already carries the single thought. A genuinely fuller short body is kept."""
    if context.is_short and draft.body and _restates(draft.body, draft.hook):
        return replace(draft, body="")
    return draft


def _restates(body: str, hook: str) -> bool:
    a = " ".join(body.lower().split())
    b = " ".join(hook.lower().split())
    return SequenceMatcher(None, a, b).ratio() > 0.6


def _parse(text: str) -> DescriptionDraft | None:
    """Parse the model's JSON reply into an LLM-method draft, or None if it is not
    a well-formed object with the expected fields."""
    match = _JSON_OBJECT.search(text)
    if match is None:
        return None
    try:
        obj = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    hook, body = obj.get("hook"), obj.get("body_markdown")
    if not isinstance(hook, str) or not isinstance(body, str):
        return None
    raw_dropped = obj.get("dropped")
    dropped = tuple(d for d in raw_dropped if isinstance(d, str)) if isinstance(raw_dropped, list) else ()
    return DescriptionDraft(
        hook=hook.strip(),
        body=body.strip(),
        method=SplitMethod.LLM,
        dropped=dropped,
    )
