"""Image text translation by vision recon, image edit, and QA.

This package is content-agnostic. Books, projects, videos, or one-off assets
provide source/target paths, expected visible text, and source-keyed overrides;
the translator only knows how to replace visible text while preserving artwork.
"""

from __future__ import annotations

from pancratius.translation.image.models import (
    AttemptRecord,
    DetectedText,
    ExactText,
    ExpectedText,
    GenerationCost,
    ImageReconResult,
    ImageTranslationJob,
    ImageTranslationResult,
    ImageTranslationStatus,
    NormalizedText,
    QaDiscrepancy,
    QaResult,
    QaVerdict,
    ResolvedText,
    RoleSelector,
    TextOverride,
    TextRole,
    TextRule,
)
from pancratius.translation.image.translator import (
    ImageTextTranslator,
    ImageTranslationConfig,
    resolve_texts,
    translate_image,
)

__all__ = [
    "AttemptRecord",
    "DetectedText",
    "ExactText",
    "ExpectedText",
    "GenerationCost",
    "ImageReconResult",
    "ImageTextTranslator",
    "ImageTranslationConfig",
    "ImageTranslationJob",
    "ImageTranslationResult",
    "ImageTranslationStatus",
    "NormalizedText",
    "QaDiscrepancy",
    "QaResult",
    "QaVerdict",
    "ResolvedText",
    "RoleSelector",
    "TextOverride",
    "TextRole",
    "TextRule",
    "resolve_texts",
    "translate_image",
]
