"""Domain types for turning a raw video description into the site's two fields.

A YouTube description is written for discovery: it bundles the real message with
an SEO keyword line, hashtags, and a fixed promo footer (book ads, a Telegram
channel, a donation block). The site renders two fields instead — a `hook` (the
lede above the embed, also the SEO/card copy) and a `body` (the reading below
it). This context owns the faithful split of one into the other.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

# The unprocessed description exactly as the platform returns it.
type RawDescription = str
# The lede: a short, complete Russian thought. Shown above the embed and used
# for SEO/OG/cards.
type Hook = str
# The reading, as clean Russian Markdown — or "" when the source carries no
# message beyond the hook (a short, or SEO + footer only).
type BodyMarkdown = str


class SplitMethod(StrEnum):
    """How a draft was produced. Recorded so an auto-merged sync stays auditable:
    a `fallback` entry is the one a human should look at first."""

    LLM = "llm"           # model output that passed the QA gate
    FALLBACK = "fallback"  # deterministic footer-strip, after the model/QA gave up


@dataclass(frozen=True, slots=True)
class VideoContext:
    """What the splitter knows besides the raw description.

    The title is passed so the hook does not merely restate it (the page already
    shows the title). Playlist titles and duration give topical/format grounding
    without leaking into the prose — a sub-minute video is almost always a
    hook-only short with no body to extract."""

    title: str
    playlists: tuple[str, ...] = ()
    duration_seconds: int | None = None

    @property
    def is_short(self) -> bool:
        return self.duration_seconds is not None and self.duration_seconds < 60


@dataclass(frozen=True, slots=True)
class DescriptionDraft:
    """The split result: a lede and a reading body, both faithful to the source."""

    hook: Hook
    body: BodyMarkdown
    method: SplitMethod
    # Short human-readable notes on what junk was removed, for the sync report.
    dropped: tuple[str, ...] = ()

    @property
    def has_body(self) -> bool:
        return bool(self.body.strip())
