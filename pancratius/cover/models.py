"""Domain types for the cover-translation pipeline.

Frozen, slotted dataclasses and StrEnums so every domain concept is
grep-able and the type-checker enforces correctness.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

# Wire types — open by nature (OpenRouter JSON envelopes)
type JsonObject = dict[str, Any]

# The model used for generation (image editing)
GENERATION_MODEL = "google/gemini-3.1-flash-image"
# Cheap vision model for recon + QA
VISION_MODEL = "google/gemini-2.5-flash"

# Image output resolution for the generation model
GENERATION_RESOLUTION = "1K"

# Source author string, invariant across all books
AUTHOR_RU = "Сергей Панкратиус"
AUTHOR_EN = "Sergei Pancratius"

# OpenRouter
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


class TitleSource(StrEnum):
    """Where the pinned English title came from (or that none was found)."""

    EN_MD = "en.md"
    SEED = "seed"
    QUEUE = "queue"
    MODEL = "model"  # no pin; model translates the displayed title itself


@dataclass(frozen=True, slots=True)
class TitlePin:
    """An authoritative English title wording and where it came from.

    The wording is the full catalogue title (e.g. "Mammon: Why You Are in His
    Power…"). It is NOT what goes on the cover verbatim — `ResolvedTitle` derives
    the displayed short form from it.
    """

    wording: str
    source: TitleSource


# A `TitlePin` source means "we found wording"; MODEL means "no pin, model
# translates the displayed title itself". This carries that whole disjunction.
type ResolvedPin = TitlePin | None


@dataclass(frozen=True, slots=True)
class ResolvedTitle:
    """The single decision of what title text to render on the cover.

    Resolution happens BEFORE the prompt: the prompt consumes `to_render` and
    nothing else. There is no in-prompt fight between a catalogue title and a
    displayed title.

    - `to_render`: the exact English string to put on the cover, or "" when the
      model should translate the displayed title itself (no usable pin).
    - `authoritative_wording`: the full pinned catalogue title `to_render` was
      derived from, kept for reporting; "" when there was no pin.
    - `source`: provenance of the decision.
    """

    to_render: str
    authoritative_wording: str
    source: TitleSource

    @property
    def is_pinned(self) -> bool:
        """True when a concrete string is pinned for the cover (not model-translated)."""
        return bool(self.to_render)


# The decision when no resolution ran (e.g. the cover blew up before lookup):
# no pin, model would translate the displayed title.
UNRESOLVED_TITLE = ResolvedTitle(to_render="", authoritative_wording="", source=TitleSource.MODEL)


class QaVerdict(StrEnum):
    PASS = "pass"
    FAIL = "fail"


class CoverStatus(StrEnum):
    """The terminal status of a cover-translation run.

    OK:            all overlay and art-baked text translated; QA passed.
    OK_WITH_CAVEAT: overlay text passed; one or more art-baked elements could
                   not be translated after the attempt cap, but the cover is
                   kept because those are hard to edit and the rest is correct.
    FAIL:          a non-art-baked (overlay) element failed, OR generation
                   could not complete; cover is unlinked.
    """

    OK = "ok"
    OK_WITH_CAVEAT = "ok_with_caveat"
    FAIL = "fail"


class ElementRole(StrEnum):
    """What a recon'd text element is on the cover.

    Drives element-English resolution: TITLE binds to the title pin, AUTHOR to the
    fixed author string, everything else to the recon model's own translation.
    """

    TITLE = "title"
    SUBTITLE = "subtitle"
    AUTHOR = "author"
    TAGLINE = "tagline"
    ART_TEXT = "art_text"  # baked into the artwork (emblem, coin, banner)
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class CoverElement:
    """One text element on the Russian cover, with the recon model's own English.

    Recon is load-bearing: it both finds the element and translates it, so the
    generation model is handed an explicit «russian → english» pair to render and
    can neither miss the element nor invent a wrong translation.

    ``art_baked`` marks text painted into the artwork (a coin emblem, a banner)
    rather than overlaid as a separate layer — those are harder to edit cleanly,
    so the pipeline attempts them but tolerates them surviving the attempt cap.
    """

    role: ElementRole
    russian: str  # verbatim Russian as shown on the cover
    english: str  # the recon model's English for this element
    art_baked: bool  # True when painted into the artwork (emblem/banner), not overlay


@dataclass(frozen=True, slots=True)
class ResolvedElement:
    """A cover element after English resolution: one authoritative English string.

    The English is decided by precedence (override > title-pin-for-title >
    fixed-author > recon translation; see ``resolve_elements``). This is what the
    generation prompt's replacement map renders and what steering quotes.
    """

    role: ElementRole
    russian: str
    english: str  # the authoritative English to render
    art_baked: bool


@dataclass(frozen=True, slots=True)
class ReconResult:
    """Vision recon of the Russian cover: what text is displayed, already translated."""

    elements: tuple[CoverElement, ...]
    displayed_title: str  # the cover's DISPLAYED title (often shorter than the full book title)
    raw_json: str  # kept for debugging


@dataclass(frozen=True, slots=True)
class QaDiscrepancy:
    """A single concrete defect found by QA.

    ``in_artwork`` is QA's own judgement of WHERE the offending text sits: True when
    it is painted into the artwork (a coin emblem, banner, decorative lettering),
    False when it is an overlay caption (title/subtitle/author/tagline). It is the
    structured signal the terminal-classifier uses to tell a tolerable art-baked
    leftover from an overlay defect — far more robust than correlating QA's
    free-text description against the recon elements (which breaks on case,
    inflection, or a partial quote).
    """

    kind: str  # "cyrillic_left" | "artwork_changed" | "text_dropped" | "author_wrong" | "other"
    description: str
    in_artwork: bool = False


@dataclass(frozen=True, slots=True)
class QaResult:
    """Vision QA of the RU→EN pair."""

    verdict: QaVerdict
    discrepancies: tuple[QaDiscrepancy, ...]  # empty on PASS
    raw_json: str


@dataclass(frozen=True, slots=True)
class GenerationCost:
    """Cost components for one generation call."""

    cost_usd: float
    usage: JsonObject = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AttemptRecord:
    """One generation+QA cycle for a single cover."""

    attempt: int  # 1-indexed
    qa: QaResult
    generation_cost: GenerationCost
    prompt_steering: str  # the steering addendum added (empty on attempt 1)


@dataclass(frozen=True, slots=True)
class CoverResult:
    """Final outcome for one cover.

    ``status`` is the authoritative terminal state (see CoverStatus). ``ok`` is
    a convenience property: True for OK and OK_WITH_CAVEAT, False for FAIL.

    ``art_baked_leftovers`` carries the description strings of unresolved
    art-baked discrepancies when status is OK_WITH_CAVEAT.
    """

    book_key: str
    status: CoverStatus
    final_path: Path | None  # None on FAIL
    raw_path: Path | None
    attempts: tuple[AttemptRecord, ...]
    title: ResolvedTitle  # the rendered-title decision (to_render / wording / source)
    displayed_title: str | None  # what recon read off the cover (Russian short form)
    error: str | None  # set when status is FAIL
    total_cost_usd: float
    art_baked_leftovers: tuple[str, ...] = ()  # unresolved art-baked descriptions (OK_WITH_CAVEAT)

    @property
    def ok(self) -> bool:
        """True when the cover was produced (OK or OK_WITH_CAVEAT)."""
        return self.status in (CoverStatus.OK, CoverStatus.OK_WITH_CAVEAT)
