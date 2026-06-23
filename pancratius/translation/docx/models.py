from __future__ import annotations

import base64
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pancratius.content_catalog import CatalogEntry
from pancratius.writeplan import Diagnostic
from pancratius.writer import WriteReport

TransferUnitKind = Literal[
    "heading",
    "paragraph",
    "lineated",
    "blank",
    "thematic",
    "image",
]
DocxTranslationBackend = Literal["transfer", "markdown-render"]


class DocxTranslationError(Exception):
    """The translated-DOCX transfer cannot proceed."""


@dataclass(frozen=True, slots=True)
class TranslatedTextRun:
    """A run of translated text with the inline emphasis Markdown can prove."""

    text: str
    strong: bool = False
    emphasis: bool = False
    link_target: str | None = None


@dataclass(frozen=True, slots=True)
class FootnoteAnchor:
    """A translated footnote reference in the text stream."""

    ordinal: int


type TranslatedInline = TranslatedTextRun | FootnoteAnchor
TextAlignmentVariantReason = Literal[
    "canonical",
    "docx_omitted_smiley",
    "markdown_literal_style_marker",
    "split_letter_typo",
    "spurious_connector_hyphen",
    "nonbreaking_hyphen_import",
    "source_citation_suffix",
    "colon_before_dash",
    "colon_before_punctuation",
    "terminal_label_colon",
]
SourceDocxTextVariantReason = Literal["canonical", "source_citation_suffix"]


@dataclass(frozen=True, slots=True)
class SourceTextAlignmentEvidence:
    """The named equivalence that matched imported Markdown to source DOCX text."""

    source_reason: TextAlignmentVariantReason
    docx_reason: SourceDocxTextVariantReason


@dataclass(frozen=True, slots=True)
class MarkdownTransferUnit:
    """One Markdown unit that should land in one Word paragraph slot."""

    kind: TransferUnitKind
    inlines: tuple[TranslatedInline, ...] = ()
    heading_level: int | None = None
    source_note: str = ""

    @property
    def plain_text(self) -> str:
        return "".join(run.text for run in self.inlines if isinstance(run, TranslatedTextRun))

    @property
    def footnote_anchors(self) -> tuple[int, ...]:
        return tuple(run.ordinal for run in self.inlines if isinstance(run, FootnoteAnchor))


@dataclass(frozen=True, slots=True)
class MarkdownTransferDocument:
    """The source-language or translated Markdown projected into transfer units."""

    units: tuple[MarkdownTransferUnit, ...]
    footnotes: tuple[tuple[MarkdownTransferUnit, ...], ...] = ()
    unsupported: tuple[Diagnostic, ...] = ()


@dataclass(frozen=True, slots=True)
class MarkdownFootnoteDefinition:
    """A labeled Markdown footnote body available to raw HTML text."""

    label: str
    markdown: str


@dataclass(frozen=True, slots=True)
class MarkdownCoverImage:
    """A cover image declared by translated Markdown frontmatter."""

    path: Path
    media_type: str

    @property
    def data_uri(self) -> str:
        encoded = base64.b64encode(self.path.read_bytes()).decode("ascii")
        return f"data:{self.media_type};base64,{encoded}"


@dataclass(frozen=True, slots=True)
class WordTextSlot:
    """A source DOCX paragraph selected by Markdown alignment."""

    ordinal: int
    paragraph: ET.Element
    text: str
    has_drawing: bool
    footnote_refs: tuple[ET.Element, ...]


@dataclass(frozen=True, slots=True)
class TransferAlignment:
    """A proven RU Markdown unit -> source DOCX paragraph mapping."""

    unit_indices: tuple[int, ...]
    slot: WordTextSlot
    source_text_evidence: SourceTextAlignmentEvidence | None = None


@dataclass(frozen=True, slots=True)
class IgnoredWordSlot:
    """A source DOCX paragraph intentionally removed because Markdown owns content."""

    slot: WordTextSlot
    reason: Literal["source_toc", "source_back_matter", "source_thematic_separator"]


@dataclass(frozen=True, slots=True)
class SourceDocxAlignmentPlan:
    """The complete source DOCX paragraph plan for translated text transfer."""

    alignments: tuple[TransferAlignment, ...]
    ignored_slots: tuple[IgnoredWordSlot, ...] = ()


@dataclass(frozen=True, slots=True)
class MarkdownUnitPairing:
    """Source Markdown unit index -> translated Markdown unit index.

    ``None`` means the source unit is a structural blank absent from the translation;
    the source DOCX blank paragraph is preserved as blank.
    """

    translated_indices_by_source: tuple[tuple[int, ...] | None, ...]


@dataclass(frozen=True, slots=True)
class BookDocxTranslationTarget:
    """One committed book bundle that can receive a translated DOCX artifact."""

    source_entry: CatalogEntry
    translated_entry: CatalogEntry
    source_docx: Path
    source_md: Path
    translated_md: Path
    translated_docx: Path

    @property
    def number(self) -> int:
        return self.source_entry.number


@dataclass(frozen=True, slots=True)
class DocxTranslationReport:
    """One work's translated-DOCX outcome."""

    target: BookDocxTranslationTarget
    write_report: WriteReport
    source_units: int
    translated_units: int
    aligned_units: int
    backend: DocxTranslationBackend = "transfer"
    output: Path | None = None


@dataclass(frozen=True, slots=True)
class DocxTranslationDiscovery:
    """Corpus denominator for a translated-DOCX batch."""

    source_books: int
    eligible: int
    missing: int
    existing: int
    ineligible: int


@dataclass(frozen=True, slots=True)
class DocxTranslationBatch:
    """A batch run across one or more work bundles."""

    reports: tuple[DocxTranslationReport, ...]
    discovery: DocxTranslationDiscovery

    @property
    def failed(self) -> bool:
        return any(
            report.write_report.refused
            or any(diagnostic.severity == "fatal" for diagnostic in report.write_report.diagnostics)
            for report in self.reports
        )
