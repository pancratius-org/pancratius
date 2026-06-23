"""Book-aware Russian→English translation for the corpus.

Public surface for the ``pancratius work translate`` verb: it drafts an ``en.md``
from a book's ``ru.md``, preserving Markdown structure and lineation by
construction. See :mod:`pancratius.translation.text.document` for the structure-preserving
core and :mod:`pancratius.translation.text.pipeline` for the run.
"""

from __future__ import annotations

from pancratius.translation.text.cache import TranslationCache
from pancratius.translation.text.client import OpenRouterClient, OpenRouterError
from pancratius.translation.text.config import DEFAULT_MODEL, StageModels, TranslateConfig
from pancratius.translation.text.pipeline import (
    CostEstimate,
    TranslateError,
    TranslationEstimateOutcome,
    TranslationOutcome,
    TranslationReport,
    TranslationWriteOutcome,
    find_untranslated,
    translate_book,
)
from pancratius.translation.text.profile import load_glossary, load_tag_labels
from pancratius.translation.text.prompts import TermEntry

__all__ = [
    "DEFAULT_MODEL",
    "CostEstimate",
    "OpenRouterClient",
    "OpenRouterError",
    "StageModels",
    "TermEntry",
    "TranslateConfig",
    "TranslateError",
    "TranslationCache",
    "TranslationEstimateOutcome",
    "TranslationOutcome",
    "TranslationReport",
    "TranslationWriteOutcome",
    "find_untranslated",
    "load_glossary",
    "load_tag_labels",
    "translate_book",
]
