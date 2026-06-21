"""Book-aware Russian→English translation for the corpus.

Public surface for the ``pancratius work translate`` verb: it drafts an ``en.md``
from a book's ``ru.md``, preserving Markdown structure and lineation by
construction. See :mod:`pancratius.translate.document` for the structure-preserving
core and :mod:`pancratius.translate.pipeline` for the run.
"""

from __future__ import annotations

from pancratius.translate.cache import TranslationCache
from pancratius.translate.client import OpenRouterClient, OpenRouterError
from pancratius.translate.config import DEFAULT_MODEL, StageModels, TranslateConfig
from pancratius.translate.pipeline import (
    CostEstimate,
    TranslateError,
    TranslationReport,
    find_untranslated,
    translate_book,
)
from pancratius.translate.profile import load_glossary, load_tag_labels
from pancratius.translate.prompts import TermEntry

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
    "TranslationReport",
    "find_untranslated",
    "load_glossary",
    "load_tag_labels",
    "translate_book",
]
