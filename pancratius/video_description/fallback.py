"""The deterministic split, used when the model or QA gives up (or no API key).

It cannot judge like the model, so it stays conservative and — above all — safe:
strip the trailing promo footer, drop any junk lines, scrub inline junk from what
survives, drop a leading SEO opener, then take the first paragraph as the lede and
the rest as the body. A final guard proves the hook carries no junk, falling back
to the (scrubbed) title if it does. Everything it emits is the author's verbatim
text with junk removed — never invented, never a raw dump, never a leaked link or
card number. The result is recorded as ``SplitMethod.FALLBACK`` so it is easy to
find.
"""

from __future__ import annotations

import re

from pancratius.video_description.config import DescriptionConfig
from pancratius.video_description.models import (
    DescriptionDraft,
    RawDescription,
    SplitMethod,
    VideoContext,
)
from pancratius.video_description.patterns import (
    SEO_KEYWORD_LINE,
    footer_start,
    junk_categories,
    scrub,
)

_SENTENCE_END = re.compile(r"[.!?…»](?=\s|$)")
_MIN_BODY_CHARS = 120


def deterministic_split(
    raw: RawDescription,
    context: VideoContext,
    config: DescriptionConfig,
) -> DescriptionDraft:
    dropped: list[str] = []
    lines = raw.splitlines()
    cut = footer_start(lines)
    if cut < len(lines):
        dropped.append("promo footer")
    kept = [scrub(ln) for ln in lines[:cut] if not junk_categories(ln)]
    if any(not ln.strip() or junk_categories(ln) for ln in lines[:cut]):
        dropped.append("junk lines")
    kept = _drop_leading_seo(kept, dropped)

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", "\n".join(kept)) if p.strip()]
    hook = _safe_hook(paragraphs[0] if paragraphs else "", context, config)
    rest = paragraphs[1:]
    body = "" if context.is_short else "\n\n".join(rest)
    if sum(len(p) for p in rest) < _MIN_BODY_CHARS or junk_categories(body):
        body = ""
    return DescriptionDraft(hook=hook, body=body, method=SplitMethod.FALLBACK, dropped=tuple(dict.fromkeys(dropped)))


def _safe_hook(first_paragraph: str, context: VideoContext, config: DescriptionConfig) -> str:
    """A clean lede from the first clean paragraph, guaranteed junk-free. If the
    paragraph is empty or still trips a junk pattern, fall back to the scrubbed
    title; the writer turns a truly-empty result into a findable TODO marker."""
    for candidate in (first_paragraph, scrub(context.title)):
        hook = _clamp(candidate, config.hook_target_chars)
        if hook and not junk_categories(hook):
            return hook
    return ""


def _drop_leading_seo(lines: list[str], dropped: list[str]) -> list[str]:
    out = list(lines)
    removed = False
    while out and (not out[0].strip() or SEO_KEYWORD_LINE.search(out[0])):
        if out[0].strip():
            removed = True
        out.pop(0)
    if removed:
        dropped.append("SEO keyword line")
    return out


def _clamp(text: str, limit: int) -> str:
    """A complete lede no longer than ``limit`` — cut at the last sentence end that
    still keeps at least half the budget, else at the last word."""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    window = text[:limit]
    ends = [m.end() for m in _SENTENCE_END.finditer(window) if m.end() >= limit // 2]
    if ends:
        return window[: ends[-1]].strip()
    cut = window.rsplit(" ", 1)[0].strip()
    return f"{cut}…"
