"""Domain types for image text translation.

The core abstraction is deliberately not "cover" and not "book": it translates
visible text in an image while keeping the image itself stable. Providers describe
what visible text is expected in this image and which source strings should get
curated translations when they are actually seen.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Any

from pancratius.locales import Locale

type JsonObject = dict[str, Any]

# The model used for generation (image editing)
GENERATION_MODEL = "google/gemini-3.1-flash-image"
# Cheap vision model for recon + QA
VISION_MODEL = "google/gemini-2.5-flash"

# Image output resolution for the generation model
GENERATION_RESOLUTION = "1K"

# OpenRouter
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

LANGUAGE_NAME: Mapping[Locale, str] = MappingProxyType({
    "ru": "Russian",
    "en": "English",
})

SCRIPT_HINT: Mapping[Locale, str] = MappingProxyType({
    "ru": "Russian/Cyrillic",
    "en": "English/Latin",
})


class TextRole(StrEnum):
    """Generic role of a text element visible in an image.

    Providers may constrain roles, but the engine gives no role special behavior
    beyond selector matching. A book title and project landing name are both just
    ``PRIMARY`` at this layer.
    """

    PRIMARY = "primary"
    SECONDARY = "secondary"
    CREDIT = "credit"
    TAGLINE = "tagline"
    LABEL = "label"
    ART_TEXT = "art_text"
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class ExactText:
    """Match a detected source string exactly."""

    source: str


@dataclass(frozen=True, slots=True)
class NormalizedText:
    """Match a detected source string after case/whitespace normalization."""

    source: str


@dataclass(frozen=True, slots=True)
class RoleSelector:
    """Match a detected text element by its generic image role."""

    role: TextRole


type SourceTextSelector = ExactText | NormalizedText
type TextSelector = SourceTextSelector | RoleSelector


@dataclass(frozen=True, slots=True)
class ExpectedText:
    """Provider assertion that a visible text element belongs in this image.

    Expected text is the only provider data the engine may synthesize when recon
    misses an element. Multiple selectors are alternatives for the same semantic
    element, for example "source title text" OR "primary visible text".
    """

    selectors: tuple[TextSelector, ...]
    target: str
    provenance: str = ""


@dataclass(frozen=True, slots=True)
class TextOverride:
    """Authoritative translation for a source string if that string is detected."""

    selector: SourceTextSelector
    target: str
    provenance: str = ""


type TextRule = ExpectedText | TextOverride


@dataclass(frozen=True, slots=True)
class DetectedText:
    """One text element read from the source image by recon."""

    role: TextRole
    source: str
    suggested_target: str
    embedded: bool


@dataclass(frozen=True, slots=True)
class ResolvedText:
    """Detected text after provider rules choose the final target string."""

    role: TextRole
    source: str
    target: str
    embedded: bool
    rule: TextRule | None = None

    @property
    def has_source(self) -> bool:
        return bool(self.source.strip())


@dataclass(frozen=True, slots=True)
class ImageReconResult:
    """Vision recon of the source image."""

    elements: tuple[DetectedText, ...]
    primary_text: str
    raw_json: str


class QaVerdict(StrEnum):
    PASS = "pass"
    FAIL = "fail"


class ImageTranslationStatus(StrEnum):
    """Terminal status of an image translation run."""

    OK = "ok"
    OK_WITH_CAVEAT = "ok_with_caveat"
    FAIL = "fail"


@dataclass(frozen=True, slots=True)
class QaDiscrepancy:
    """A single concrete defect found by QA."""

    kind: str
    description: str
    embedded: bool = False


@dataclass(frozen=True, slots=True)
class QaResult:
    """Vision QA of the source/target image pair."""

    verdict: QaVerdict
    discrepancies: tuple[QaDiscrepancy, ...]
    raw_json: str


@dataclass(frozen=True, slots=True)
class GenerationCost:
    """Cost components for one generation call."""

    cost_usd: float
    usage: JsonObject = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AttemptRecord:
    """One generation+QA cycle for a single image."""

    attempt: int
    qa: QaResult
    generation_cost: GenerationCost
    prompt_steering: str


@dataclass(frozen=True, slots=True)
class ImageTranslationJob:
    """A fully resolved image-translation job.

    Providers own source discovery, target paths, frontmatter reads, and all
    domain vocabulary. The translator consumes only this object.
    """

    key: str
    source_image: Path
    target_image: Path
    source_lang: Locale = "ru"
    target_lang: Locale = "en"
    expected_text: tuple[ExpectedText, ...] = ()
    overrides: tuple[TextOverride, ...] = ()
    context: str = "image"
    raw_image: Path | None = None
    steering_hint: str = ""
    metadata: Mapping[str, str] = field(default_factory=dict)
    allow_embedded_text_caveat: bool = False

    def raw_output(self) -> Path:
        """Raw model output path before de-cropping."""
        if self.raw_image is not None:
            return self.raw_image
        return self.target_image.with_name(f"{self.target_image.stem}.raw.png")


@dataclass(frozen=True, slots=True)
class ImageTranslationResult:
    """Final outcome for one image translation."""

    key: str
    status: ImageTranslationStatus
    final_path: Path | None
    raw_path: Path | None
    attempts: tuple[AttemptRecord, ...]
    primary_text: str | None
    error: str | None
    total_cost_usd: float
    metadata: Mapping[str, str] = field(default_factory=dict)
    embedded_leftovers: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.status in (ImageTranslationStatus.OK, ImageTranslationStatus.OK_WITH_CAVEAT)
