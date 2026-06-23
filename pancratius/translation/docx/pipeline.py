from __future__ import annotations

from pancratius.translation.docx.align import (
    align_source_units,
    ignored_slot_diagnostics,
    join_markdown_units_for_word_slot,
    merge_adjacent_runs,
    normalize_transfer_text,
    pair_markdown_units,
    split_inlines_on_newlines,
    texts_match_for_source_alignment,
)
from pancratius.translation.docx.batch import discover_targets, translate_docx_batch
from pancratius.translation.docx.donor_docx import (
    DocxPackageParts,
    copy_docx_parts,
    word_paragraph_text,
    word_text_slots,
)
from pancratius.translation.docx.markdown_units import (
    markdown_cover_image,
    parse_markdown_transfer,
)
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
from pancratius.translation.docx.ooxml_write import (
    HyperlinkRelationshipAllocator,
    dedupe_media_payloads,
    remove_ignored_word_slots,
    repair_unbound_relationship_prefixes,
    replace_embedded_cover_data_uri,
    replace_footnotes,
    replace_paragraph_text,
    sanitize_drawing_metadata,
    unit_has_hyperlink,
    write_docx_parts,
)
from pancratius.translation.docx.report import print_batch
from pancratius.translation.docx.transfer import render_markdown_docx, render_translated_docx

# Backwards-compatible private names for older internal tests and scripts.
_align_source_units = align_source_units
_copy_docx_parts = copy_docx_parts
_dedupe_media_payloads = dedupe_media_payloads
_ignored_slot_diagnostics = ignored_slot_diagnostics
_join_units = join_markdown_units_for_word_slot
_markdown_cover_image = markdown_cover_image
_merge_adjacent_runs = merge_adjacent_runs
_normalize_text = normalize_transfer_text
_pair_markdown_units = pair_markdown_units
_parse_markdown_transfer = parse_markdown_transfer
_repair_unbound_relationship_prefixes = repair_unbound_relationship_prefixes
_split_inlines_on_newlines = split_inlines_on_newlines
_texts_match_for_source_alignment = texts_match_for_source_alignment
_word_paragraph_text = word_paragraph_text
_word_slots = word_text_slots
_write_docx_parts = write_docx_parts

__all__ = [
    "BookDocxTranslationTarget",
    "DocxPackageParts",
    "DocxTranslationBackend",
    "DocxTranslationBatch",
    "DocxTranslationDiscovery",
    "DocxTranslationError",
    "DocxTranslationReport",
    "FootnoteAnchor",
    "HyperlinkRelationshipAllocator",
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
    "align_source_units",
    "copy_docx_parts",
    "dedupe_media_payloads",
    "discover_targets",
    "ignored_slot_diagnostics",
    "join_markdown_units_for_word_slot",
    "markdown_cover_image",
    "merge_adjacent_runs",
    "normalize_transfer_text",
    "pair_markdown_units",
    "parse_markdown_transfer",
    "print_batch",
    "remove_ignored_word_slots",
    "render_markdown_docx",
    "render_translated_docx",
    "repair_unbound_relationship_prefixes",
    "replace_embedded_cover_data_uri",
    "replace_footnotes",
    "replace_paragraph_text",
    "sanitize_drawing_metadata",
    "split_inlines_on_newlines",
    "texts_match_for_source_alignment",
    "translate_docx_batch",
    "unit_has_hyperlink",
    "word_paragraph_text",
    "word_text_slots",
    "write_docx_parts",
]
