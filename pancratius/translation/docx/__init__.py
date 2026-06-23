"""Translated DOCX transfer from aligned Markdown and a donor source DOCX."""

from pancratius.translation.docx.batch import discover_targets, translate_docx_batch
from pancratius.translation.docx.models import (
    BookDocxTranslationTarget,
    DocxTranslationBackend,
    DocxTranslationBatch,
    DocxTranslationDiscovery,
    DocxTranslationError,
    DocxTranslationReport,
    FootnoteAnchor,
    IgnoredWordSlot,
    MarkdownCoverImage,
    MarkdownFootnoteDefinition,
    MarkdownTransferDocument,
    MarkdownTransferUnit,
    MarkdownUnitPairing,
    SourceDocxAlignmentPlan,
    SourceDocxTextVariantReason,
    SourceTextAlignmentEvidence,
    TransferAlignment,
    TranslatedTextRun,
    WordTextSlot,
)
from pancratius.translation.docx.report import print_batch
from pancratius.translation.docx.transfer import render_markdown_docx, render_translated_docx

__all__ = [
    "BookDocxTranslationTarget",
    "DocxTranslationBackend",
    "DocxTranslationBatch",
    "DocxTranslationDiscovery",
    "DocxTranslationError",
    "DocxTranslationReport",
    "FootnoteAnchor",
    "IgnoredWordSlot",
    "MarkdownCoverImage",
    "MarkdownFootnoteDefinition",
    "MarkdownTransferDocument",
    "MarkdownTransferUnit",
    "MarkdownUnitPairing",
    "SourceDocxAlignmentPlan",
    "SourceDocxTextVariantReason",
    "SourceTextAlignmentEvidence",
    "TransferAlignment",
    "TranslatedTextRun",
    "WordTextSlot",
    "discover_targets",
    "print_batch",
    "render_markdown_docx",
    "render_translated_docx",
    "translate_docx_batch",
]
